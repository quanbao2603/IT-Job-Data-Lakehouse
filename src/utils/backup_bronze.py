import os
import sys
import shutil
import sqlite3
import hashlib
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

from minio import Minio
from src.storage.atomic_writer import load_env_file


def backup_lakehouse_metadata(
    backup_dir: str = "backups",
    include_objects: bool = False
) -> str:
    load_env_file()
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    staging_dir = os.path.join(backup_dir, f".tmp_lakehouse_backup_{timestamp}")
    final_backup_dir = os.path.join(backup_dir, f"lakehouse_backup_{timestamp}")
    os.makedirs(staging_dir, exist_ok=True)

    db_source = "data/state_store.db"
    if not os.path.exists(db_source):
        shutil.rmtree(staging_dir, ignore_errors=True)
        print(f"[!] Không tìm thấy tệp {db_source} để sao lưu.")
        return ""

    try:
        db_target = os.path.join(staging_dir, "state_store.db")
        shutil.copy2(db_source, db_target)

        hasher = hashlib.sha256()
        with open(db_target, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        db_sha256 = hasher.hexdigest()

        conn = sqlite3.connect(db_target)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ingestion_objects")
        total_objects = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ingestion_runs")
        total_runs = cursor.fetchone()[0]
        conn.close()

        backed_up_objects_count = 0
        backed_up_bytes = 0
        object_manifest_entries: List[Dict[str, Any]] = []

        if include_objects:
            endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
            access_key = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
            secret_key = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))
            secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
            bronze_bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze")

            if not access_key or not secret_key:
                raise ValueError("Không tìm thấy MinIO credential trong .env!")

            client = Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            objects_dir = os.path.join(staging_dir, "bronze_objects")
            os.makedirs(objects_dir, exist_ok=True)

            objects = list(client.list_objects(bronze_bucket, recursive=True))
            for obj in objects:
                if not obj.object_name.endswith(".jsonl.gz"):
                    continue

                obj_target = os.path.join(objects_dir, obj.object_name)
                os.makedirs(os.path.dirname(obj_target), exist_ok=True)

                client.fget_object(bronze_bucket, obj.object_name, obj_target)

                actual_size = os.path.getsize(obj_target)
                if actual_size != obj.size:
                    raise ValueError(f"Dung lượng không khớp cho {obj.object_name}!")

                obj_hash = hashlib.sha256()
                with open(obj_target, "rb") as f:
                    while chunk := f.read(65536):
                        obj_hash.update(chunk)
                computed_sha = obj_hash.hexdigest()

                backed_up_objects_count += 1
                backed_up_bytes += actual_size
                object_manifest_entries.append({
                    "object_key": obj.object_name,
                    "size_bytes": actual_size,
                    "sha256": computed_sha
                })

        info_file = os.path.join(staging_dir, "backup_info.txt")
        with open(info_file, "w", encoding="utf-8") as f:
            f.write(f"Timestamp UTC: {timestamp}\n")
            f.write(f"Database SHA-256: {db_sha256}\n")
            f.write(f"Total Objects in Manifest: {total_objects}\n")
            f.write(f"Total Ingestion Runs: {total_runs}\n")
            f.write(f"Bronze Objects Backed Up: {backed_up_objects_count}\n")
            f.write(f"Bronze Bytes Backed Up: {backed_up_bytes}\n")

        if object_manifest_entries:
            manifest_file = os.path.join(staging_dir, "objects_manifest.json")
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(object_manifest_entries, f, indent=2)

        db_size_bytes = os.path.getsize(db_target)
        os.rename(staging_dir, final_backup_dir)

        print("=" * 75)
        print("💾 SAO LƯU DỰ PHÒNG LAKEHOUSE HOÀN TẤT (FAIL-CLOSED VERIFIED):")
        print(f"   - Thư mục sao lưu     : {final_backup_dir}")
        print(f"   - Dung lượng SQLite   : {db_size_bytes:,} bytes")
        print(f"   - Mã băm SHA-256 DB   : {db_sha256}")
        print(f"   - Sổ cái Manifest DB  : {total_objects} tệp")
        if include_objects:
            print(f"   - Bronze Objects chép : {backed_up_objects_count} files ({backed_up_bytes:,} bytes)")
        print("=" * 75)

        return final_backup_dir

    except Exception as e:
        shutil.rmtree(staging_dir, ignore_errors=True)
        print(f"\n❌ LỖI TRONG QUÁ TRÌNH SAO LƯU (Đã thu hồi thư mục tạm): {e}")
        raise


def verify_backup_drill(backup_path: str) -> bool:
    if not os.path.isdir(backup_path):
        print(f"[!] Thư mục snapshot không tồn tại: {backup_path}")
        return False

    print("=" * 80)
    print("🛡️ THAO DIỄN KIỂM TRA PHỤC HỒI DỰ PHÒNG (BACKUP RESTORE DRILL)")
    print(f"   Snapshot kiểm tra: {backup_path}")
    print("=" * 80)

    db_path = os.path.join(backup_path, "state_store.db")
    if not os.path.exists(db_path):
        print("[❌ FAIL] Thiếu tệp state_store.db trong snapshot!")
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()[0]
        if res != "ok":
            print(f"[❌ FAIL] SQLite integrity check thất bại: {res}")
            conn.close()
            return False
        cursor.execute("SELECT COUNT(*) FROM ingestion_objects")
        total_objects = cursor.fetchone()[0]
        conn.close()
        print(f"   [✓] SQLite Integrity Check: PASS (PRAGMA integrity_check = ok, {total_objects} objects)")
    except Exception as e:
        print(f"[❌ FAIL] Lỗi đọc SQLite: {e}")
        return False

    manifest_path = os.path.join(backup_path, "objects_manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_entries = json.load(f)

        checked_count = 0
        for entry in manifest_entries:
            key = entry["object_key"]
            expected_size = entry["size_bytes"]
            expected_sha = entry["sha256"]

            file_path = os.path.join(backup_path, "bronze_objects", key)
            if not os.path.exists(file_path):
                print(f"[❌ FAIL] Thiếu tệp object: {key}")
                return False

            if os.path.getsize(file_path) != expected_size:
                print(f"[❌ FAIL] Dung lượng tệp không khớp: {key}")
                return False

            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            if hasher.hexdigest() != expected_sha:
                print(f"[❌ FAIL] Mã băm SHA-256 không khớp: {key}")
                return False

            checked_count += 1

        print(f"   [✓] Payload Check: {checked_count}/{len(manifest_entries)} objects khớp 100% SHA-256 & Size")

    print("\n" + "=" * 80)
    print("✅ KẾT QUẢ THAO DIỄN PHỤC HỒI (RESTORE DRILL): SẴN SÀNG KHÔI PHỤC (100% PASS)")
    print("=" * 80)
    return True


def restore_backup_to_bucket(backup_path: str, target_bucket: str = "bronze-restore-test") -> bool:
    load_env_file()
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
    secret_key = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    if not access_key or not secret_key:
        print("[!] Thiếu MinIO credential trong .env!")
        return False

    client = Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    if not client.bucket_exists(target_bucket):
        client.make_bucket(target_bucket)
        print(f"[*] Đã tạo bucket đích phục vụ khôi phục: s3://{target_bucket}")

    objects_dir = os.path.join(backup_path, "bronze_objects")
    manifest_path = os.path.join(backup_path, "objects_manifest.json")
    if not os.path.exists(manifest_path) or not os.path.isdir(objects_dir):
        print("[!] Không tìm thấy objects_manifest.json hoặc thư mục bronze_objects trong snapshot!")
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_entries = json.load(f)

    restored_count = 0
    for entry in manifest_entries:
        key = entry["object_key"]
        local_file = os.path.join(objects_dir, key)
        if not os.path.exists(local_file):
            print(f"[❌] Thiếu tệp cục bộ: {local_file}")
            return False

        client.fput_object(target_bucket, key, local_file, content_type="application/gzip")
        stat = client.stat_object(target_bucket, key)
        if stat.size != entry["size_bytes"]:
            print(f"[❌] Dung lượng sau khôi phục không khớp cho {key}")
            return False
        restored_count += 1

    print("=" * 80)
    print(f"🎉 THAO DIỄN KHÔI PHỤC TOÀN DIỆN THÀNH CÔNG (RESTORE DRILL SUCCESS):")
    print(f"   - Bucket đích khôi phục : s3://{target_bucket}")
    print(f"   - Số objects khôi phục  : {restored_count}/{len(manifest_entries)} tệp (100% Khớp)")
    print("=" * 80)
    return True


if __name__ == "__main__":
    if "--restore-to-bucket" in sys.argv:
        idx = sys.argv.index("--restore-to-bucket")
        target_b = sys.argv[idx + 1] if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-") else "bronze-restore-test"
        backups = sorted([d for d in os.listdir("backups") if d.startswith("lakehouse_backup_")])
        snap_path = os.path.join("backups", backups[-1]) if backups else ""
        restore_backup_to_bucket(snap_path, target_b)
    elif "--verify-drill" in sys.argv:
        idx = sys.argv.index("--verify-drill")
        path_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-") else ""
        if not path_arg:
            backups = sorted([d for d in os.listdir("backups") if d.startswith("lakehouse_backup_")])
            path_arg = os.path.join("backups", backups[-1]) if backups else ""
        verify_backup_drill(path_arg)
    else:
        with_objects = "--include-objects" in sys.argv
        backup_lakehouse_metadata(include_objects=with_objects)
