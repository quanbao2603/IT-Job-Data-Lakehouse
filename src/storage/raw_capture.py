"""Keep the bytes the source actually sent, so a broken selector is fixable without a re-crawl.

Bronze stores parsed records. When a parser drifts (TopCV's Angular template, Devwork's redesign)
the only recovery was crawling the site again — slow, rate-limited, and impossible for postings
that have since expired. This writes the original HTML/JSON to `bronze/raw/...` before parsing.

Design choices:
  * gzip, ~5-10x smaller, still byte-exact after decompression;
  * one object per request, keyed by url hash so a retry overwrites instead of duplicating;
  * capture failures are swallowed: losing a raw copy must never fail an ingestion run;
  * short retention (default 7 days) — this is a debugging window, not an archive.

    LAKEHOUSE_KEEP_RAW=0    disable
    LAKEHOUSE_RAW_MAX_BYTES cap per object (default 3 MB)
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from src.storage.atomic_writer import load_env_file


class RawCapture:
    """Best-effort writer for the raw response bodies of one crawl."""

    def __init__(self, client=None, bucket: Optional[str] = None, enabled: Optional[bool] = None):
        load_env_file()
        self._client = client
        self.bucket = bucket or os.getenv("MINIO_BRONZE_BUCKET", "bronze")
        if enabled is None:
            enabled = os.getenv("LAKEHOUSE_KEEP_RAW", "1").strip().lower() in {"1", "true", "yes", "on"}
        self.enabled = enabled
        self.max_bytes = int(os.getenv("LAKEHOUSE_RAW_MAX_BYTES", str(3 * 1024 * 1024)))

    def _get_client(self):
        if self._client is None:
            from minio import Minio

            endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000").replace("http://", "").replace("https://", "")
            self._client = Minio(
                endpoint,
                access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
                secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
                secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
            )
        return self._client

    def key_for(self, *, source: str, url: str, kind: str = "detail", now: Optional[datetime] = None) -> str:
        stamp = now or datetime.now(timezone.utc)
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
        suffix = "json" if self._looks_like_json(url, kind) else "html"
        return (
            f"raw/{source}/ingest_year={stamp:%Y}/ingest_month={stamp:%m}/ingest_day={stamp:%d}/"
            f"{kind}-{digest}.{suffix}.gz"
        )

    @staticmethod
    def _looks_like_json(url: str, kind: str) -> bool:
        if kind in {"listing_json", "api"}:
            return True
        path = urlparse(str(url)).path.lower()
        return path.endswith(".json") or "/api/" in path

    def capture(self, *, source: str, url: str, body, kind: str = "detail", run_id: Optional[str] = None) -> Optional[str]:
        """Store one body and return its key, or None when skipped/unavailable."""
        if not self.enabled or body is None:
            return None
        try:
            payload = body.encode("utf-8", "replace") if isinstance(body, str) else bytes(body)
            if not payload or len(payload) > self.max_bytes:
                return None
            key = self.key_for(source=source, url=url, kind=kind)
            blob = gzip.compress(payload, compresslevel=6)
            client = self._get_client()
            kwargs = {}
            if run_id:
                kwargs["metadata"] = {"run-id": str(run_id)[:200], "source-url": str(url)[:900]}
            client.put_object(self.bucket, key, io.BytesIO(blob), len(blob), content_type="application/gzip", **kwargs)
            return key
        except Exception:
            # A missing raw copy is a missed convenience; a failed crawl is an outage.
            return None
