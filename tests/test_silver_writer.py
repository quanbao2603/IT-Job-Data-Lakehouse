import json
import tempfile
import unittest
from pathlib import Path

from src.etl.silver_writer import SilverWriter


class TestSilverWriter(unittest.TestCase):
    def _observation(self, observation_id):
        return {
            "source_job_id": "job-1", "observation_id": observation_id, "platform": "itviec",
            "source_url": None, "canonical_url": None, "content_hash": None,
            "crawled_at": "2026-11-16T02:00:00Z", "observed_at": "2026-11-16T02:00:00Z",
            "ingested_at": "2026-11-16T02:00:00Z", "silver_updated_at": "2026-11-16T02:00:00Z",
            "is_active": True, "job_title": "Backend", "job_title_normalized": "backend",
            "job_level": None, "job_expertise": None, "job_domain": None, "work_mode": None,
            "working_time": None, "overtime_policy": None, "company_name": "Acme",
            "company_name_raw": "Acme", "company_name_normalized": "acme", "company_size": None,
            "company_industry": None, "company_type": None, "company_country": None, "city": None,
            "city_normalized": None, "full_address": None, "salary_raw": None,
            "salary_min_vnd": None, "salary_max_vnd": None, "is_negotiable": False,
            "salary_currency": None, "exp_years_min": None, "exp_years_max": None,
            "vacancies_count": 1, "posted_date": None, "expired_date": None,
            "skills_raw": None, "skills_standardized": None, "job_description_clean": None,
            "description_normalized": None, "requirements_clean": None, "benefits_clean": None,
            "platform_specific_attributes": "{}",
        }

    def test_bounded_parquet_write_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp, max_records_per_batch=2)
            rows = [{
                "source_job_id": str(i), "observation_id": f"obs-{i}", "platform": "itviec",
                "source_url": None, "canonical_url": None, "content_hash": None,
                "crawled_at": "2026-11-16T02:00:00Z", "observed_at": "2026-11-16T02:00:00Z",
                "ingested_at": "2026-11-16T02:00:00Z", "silver_updated_at": "2026-11-16T02:00:00Z",
                "is_active": True, "job_title": "Backend", "job_title_normalized": "backend",
                "job_level": None, "job_expertise": None, "job_domain": None, "work_mode": None,
                "working_time": None, "overtime_policy": None, "company_name": "Acme",
                "company_name_raw": "Acme", "company_name_normalized": "acme", "company_size": None,
                "company_industry": None, "company_type": None, "company_country": None, "city": None,
                "city_normalized": None, "full_address": None, "salary_raw": None,
                "salary_min_vnd": None, "salary_max_vnd": None, "is_negotiable": False,
                "salary_currency": None, "exp_years_min": None, "exp_years_max": None,
                "vacancies_count": 1, "posted_date": None, "expired_date": None,
                "skills_raw": None, "skills_standardized": None, "job_description_clean": None,
                "description_normalized": None, "requirements_clean": None, "benefits_clean": None,
                "platform_specific_attributes": "{}",
            } for i in range(3)]
            result = writer.write("observations", rows, "platform=itviec/year=2026/month=11/day=16")
            self.assertEqual(len(result), 2)
            self.assertTrue(all(Path(item["path"]).exists() for item in result))
            audit = writer.write_audit_log({"status": "SUCCESS"}, "test")
            self.assertTrue(Path(audit).exists())

    def test_idempotency_keys_deduplicate_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp)
            row = self._observation("obs-1")
            result = writer.write("observations", [row, dict(row)], "platform=itviec/year=2026/month=11/day=16")
            self.assertEqual(result[0]["records"], 1)

    def test_replay_order_produces_same_batch_objects(self):
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = SilverWriter(first_tmp, max_records_per_batch=2)
            second = SilverWriter(second_tmp, max_records_per_batch=2)
            rows = [self._observation(f"obs-{index}") for index in range(3)]
            first_paths = {item["object_key"] for item in first.write("observations", rows, "partition")}
            second_paths = {item["object_key"] for item in second.write("observations", list(reversed(rows)), "partition")}
            self.assertEqual(first_paths, second_paths)

    def test_run_namespace_prevents_cross_release_object_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = SilverWriter(tmp, object_namespace="run-a")
            second = SilverWriter(tmp, object_namespace="run-b")
            row = self._observation("obs-isolated")
            first_key = first.write("observations", [row], "partition")[0]["object_key"]
            second_key = second.write("observations", [row], "partition")[0]["object_key"]
            self.assertNotEqual(first_key, second_key)
            self.assertTrue(first_key.startswith("run-a/"))
            self.assertTrue(second_key.startswith("run-b/"))

    def test_idempotency_key_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp)
            with self.assertRaises(ValueError):
                writer.write("observations", [self._observation(None)], "partition")

    def test_manifest_transitions_to_complete_with_checksum_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp)
            rows = [self._observation("obs-manifest")]
            result = writer.write("observations", rows, "platform=itviec/year=2026/month=11/day=16")
            manifest = Path(result[0]["manifest_path"])
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "COMPLETE")
            self.assertEqual(payload["records"], 1)
            self.assertEqual(payload["sha256"], result[0]["sha256"])

    def test_failed_write_leaves_failed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SilverWriter(tmp)
            bad_row = self._observation("obs-fail")
            bad_row["vacancies_count"] = "not-an-int"
            with self.assertRaises((TypeError, ValueError)):
                writer.write("observations", [bad_row], "partition")
            manifests = list(Path(tmp).rglob("manifest-*.json"))
            self.assertTrue(manifests)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAILED")

    def test_minio_upload_is_explicit_and_checksum_verified(self):
        class FakeStat:
            size = 1

        class FakeMinio:
            def __init__(self):
                self.calls = []
                self.objects = {}

            def put_object(self, bucket, key, stream, length, content_type=None):
                self.calls.append((bucket, key, length, content_type))
                self.objects[(bucket, key)] = stream.read()

            def stat_object(self, bucket, key):
                class Stat:
                    size = len(self.objects[(bucket, key)])
                return Stat()

            def get_object(self, bucket, key):
                class Response:
                    def __init__(self, data):
                        self.data = data
                    def read(self, size=-1):
                        data, self.data = self.data[:size], self.data[size:]
                        return data
                    def close(self):
                        pass
                    def release_conn(self):
                        pass
                return Response(self.objects[(bucket, key)])

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeMinio()
            writer = SilverWriter(tmp, minio_client=client)
            result = writer.write("observations", [self._observation("obs-minio")], "platform=itviec/year=2026/month=11/day=16")
            self.assertEqual(result[0]["status"], "COMPLETE")
            self.assertEqual(client.calls[0][0], "silver")
            self.assertEqual(client.calls[0][1].split("/")[0], "observations")


if __name__ == "__main__":
    unittest.main()
