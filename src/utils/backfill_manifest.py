import os
import io
import gzip
import hashlib
import sqlite3
from datetime import datetime
from typing import Optional

from minio import Minio
from src.storage.atomic_writer import load_env_file


def backfill_ingestion_manifest(db_path: str = "data/state_store.db") -> None:
    """
    Tiện ích đồng bộ Sổ cái Manifest (Object Manifest Backfill).
    Quét toàn bộ các tệp .jsonl.gz đang có trong bucket MinIO Bronze và
    ghi nhận đầy đủ vào bảng ingestion_objects trong SQLite.
    """
    load_env_file()

    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
    secret_key = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
    bronze_bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze")

    if not access_key or not secret_key:
        print("[!] Không tìm thấy cấu hình xác thực MinIO trong .env!")
        return

    client = Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    print("=" * 80)
    print("🔄 BẮT ĐẦU ĐỒNG BỘ SỔ CÁI MANIFEST TẦNG BRONZE (MANIFEST BACKFILL)")
    print(f"   Bucket: s3://{bronze_bucket}/ | Thời điểm: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_objects (
            object_key          TEXT PRIMARY KEY,
            run_id              TEXT NOT NULL,
            source_name         TEXT NOT NULL,
            file_size_bytes     INTEGER NOT NULL,
            records_count       INTEGER NOT NULL,
            checksum_sha256     TEXT,
            committed_at        TEXT NOT NULL,
            is_compacted        INTEGER DEFAULT 0,
            compacted_into_key  TEXT
        )
    """)
    conn.commit()

    cursor.execute("SELECT object_key FROM ingestion_objects")
    existing_keys = {row[0] for row in cursor.fetchall()}

    try:
        objects = list(client.list_objects(bronze_bucket, recursive=True))
    except Exception as e:
        print(f"[!] Không thể kết nối hoặc quét bucket MinIO {bronze_bucket}: {e}")
        conn.close()
        return

    gz_objects = [obj for obj in objects if obj.object_name.endswith(".jsonl.gz")]
    print(f"[*] Tìm thấy {len(gz_objects)} tệp .jsonl.gz trong bucket '{bronze_bucket}'.")

    newly_added = 0
    already_synced = 0
    total_records_synced = 0

    for obj in gz_objects:
        key = obj.object_name

        if key in existing_keys:
            already_synced += 1
            continue

        parts = key.split("/")
        source_name = parts[0].lower() if parts else "unknown"
        filename = parts[-1]
        run_id_guess = filename.replace(".jsonl.gz", "")

        hasher = hashlib.sha256()
        records_count = 0
        resp = client.get_object(bronze_bucket, key)

        try:
            file_bytes = resp.read()
            hasher.update(file_bytes)
            file_sha256 = hasher.hexdigest()

            with gzip.GzipFile(fileobj=io.BytesIO(file_bytes), mode="rb") as gz:
                with io.TextIOWrapper(gz, encoding="utf-8") as stream:
                    for line in stream:
                        if line.strip():
                            records_count += 1
        finally:
            resp.close()
            resp.release_conn()

        committed_at = obj.last_modified.isoformat() if obj.last_modified else datetime.utcnow().isoformat() + "Z"

        cursor.execute("""
            INSERT INTO ingestion_objects (
                object_key, run_id, source_name, file_size_bytes,
                records_count, checksum_sha256, committed_at, is_compacted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (key, run_id_guess, source_name, obj.size, records_count, file_sha256, committed_at))
        conn.commit()

        newly_added += 1
        total_records_synced += records_count
        print(f"   [+] Đã đăng ký: {key} ({records_count} records, {obj.size} bytes, SHA: {file_sha256[:12]}...)")

    conn.close()

    print("\n" + "=" * 80)
    print("✅ KẾT QUẢ ĐỒNG BỘ MANIFEST HOÀN TẤT:")
    print(f"   - Số tệp mới đăng ký vào Manifest: {newly_added} tệp")
    print(f"   - Số tệp đã có từ trước          : {already_synced} tệp")
    print(f"   - Tổng số tệp Bronze theo dõi    : {newly_added + already_synced} tệp")
    print(f"   - Tổng số bản ghi ghi nhận mới   : {total_records_synced} tin")
    print("=" * 80)


if __name__ == "__main__":
    backfill_ingestion_manifest()
