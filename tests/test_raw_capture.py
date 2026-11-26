"""Raw response capture: byte-exact, gzipped, keyed by url, and never fatal."""
import gzip
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.storage.raw_capture import RawCapture


class FakeClient:
    def __init__(self, fail: bool = False):
        self.objects = {}
        self.fail = fail

    def put_object(self, bucket, key, stream, length, content_type=None, **kwargs):
        if self.fail:
            raise RuntimeError("minio down")
        self.objects[key] = stream.read()


class TestRawCapture(unittest.TestCase):
    def test_body_round_trips_byte_exact(self):
        client = FakeClient()
        capture = RawCapture(client=client, enabled=True)
        html = "<html><body>Lương 20 triệu</body></html>"
        key = capture.capture(source="topcv", url="https://www.topcv.vn/viec-lam/x/1.html", body=html)
        self.assertIsNotNone(key)
        self.assertEqual(gzip.decompress(client.objects[key]).decode("utf-8"), html)

    def test_key_layout_is_partitioned_by_day_and_url_hash(self):
        capture = RawCapture(client=FakeClient(), enabled=True)
        key = capture.key_for(
            source="itviec",
            url="https://itviec.com/it-jobs/x-123",
            now=datetime(2026, 11, 26, tzinfo=timezone.utc),
        )
        self.assertTrue(key.startswith("raw/itviec/ingest_year=2026/ingest_month=11/ingest_day=26/detail-"))
        self.assertTrue(key.endswith(".html.gz"))
        same = capture.key_for(source="itviec", url="https://itviec.com/it-jobs/x-123",
                               now=datetime(2026, 11, 26, tzinfo=timezone.utc))
        self.assertEqual(key, same, "cùng URL phải cùng khoá để lần chạy lại ghi đè, không nhân bản")

    def test_json_endpoints_keep_their_extension(self):
        capture = RawCapture(client=FakeClient(), enabled=True)
        self.assertTrue(capture.key_for(source="vieclam24h", url="https://x/api/search", kind="api").endswith(".json.gz"))

    def test_disabled_capture_writes_nothing(self):
        client = FakeClient()
        capture = RawCapture(client=client, enabled=False)
        self.assertIsNone(capture.capture(source="topcv", url="https://x/1", body="<html></html>"))
        self.assertEqual(client.objects, {})

    def test_failure_in_storage_never_raises(self):
        capture = RawCapture(client=FakeClient(fail=True), enabled=True)
        self.assertIsNone(capture.capture(source="topcv", url="https://x/1", body="<html></html>"))

    def test_oversized_body_is_skipped(self):
        client = FakeClient()
        capture = RawCapture(client=client, enabled=True)
        capture.max_bytes = 10
        self.assertIsNone(capture.capture(source="topcv", url="https://x/1", body="x" * 500))
        self.assertEqual(client.objects, {})

    def test_env_toggle_is_honoured(self):
        with patch.dict(os.environ, {"LAKEHOUSE_KEEP_RAW": "0"}):
            self.assertFalse(RawCapture(client=FakeClient()).enabled)
        with patch.dict(os.environ, {"LAKEHOUSE_KEEP_RAW": "1"}):
            self.assertTrue(RawCapture(client=FakeClient()).enabled)


if __name__ == "__main__":
    unittest.main()
