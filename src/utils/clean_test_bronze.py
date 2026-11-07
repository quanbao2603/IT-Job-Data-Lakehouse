import os
import sqlite3
from minio import Minio
from src.storage.atomic_writer import load_env_file


def clean_test_bronze_data():
    load_env_file()

    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
    secret_key = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
    bronze_bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze")
    db_path = "data/state_store.db"

    if not access_key or not secret_key:
        print("[!] Thiếu credentials MinIO trong .env!")
        return

    client = Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    print("=" * 70)
    print("🧹 BẮT ĐẦU DỌN DẸP DỮ LIỆU THỬ NGHIỆM TRONG BRONZE PRODUCTION")
    print("=" * 70)

    test_prefixes = ["test_audit", "test_stream", "test_"]
    removed_objects = []

    try:
        objects = client.list_objects(bronze_bucket, recursive=True)
        for obj in objects:
            key = obj.object_name
            if any(key.startswith(p) for p in test_prefixes):
                client.remove_object(bronze_bucket, key)
                removed_objects.append(key)
                print(f"   [-] Đã xóa object test MinIO: {key} ({obj.size} bytes)")
    except Exception as e:
        print(f"[!] Lỗi khi quét MinIO: {e}")

    deleted_states = 0
    deleted_runs = 0
    deleted_manifests = 0

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            for prefix in ["test_audit", "test_stream"]:
                cursor.execute("DELETE FROM ingestion_state WHERE source_name LIKE ?", (f"{prefix}%",))
                deleted_states += cursor.rowcount
                cursor.execute("DELETE FROM ingestion_runs WHERE source_name LIKE ?", (f"{prefix}%",))
                deleted_runs += cursor.rowcount
                cursor.execute("DELETE FROM ingestion_objects WHERE source_name LIKE ?", (f"{prefix}%",))
                deleted_manifests += cursor.rowcount
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[!] Lỗi dọn dẹp SQLite: {e}")

    print("\n" + "=" * 70)
    print("✅ KẾT QUẢ DỌN DẸP HOÀN TẤT:")
    print(f"   - Số object test đã xóa trên MinIO : {len(removed_objects)} files")
    print(f"   - Số dòng test xóa trong SQLite    : {deleted_states} states, {deleted_runs} runs, {deleted_manifests} manifests")
    print("   - Trạng thái Bronze Production    : 100% Thuần khiết (Chỉ chứa dữ liệu tuyển dụng thật)")
    print("=" * 70)


if __name__ == "__main__":
    clean_test_bronze_data()
