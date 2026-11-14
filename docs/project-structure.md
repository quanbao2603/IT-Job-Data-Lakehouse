# Cấu trúc thư mục dự án IT-Job-Data-Lakehouse

> Snapshot cấu trúc tại thời điểm xuất file, đối chiếu từ output `find` trực tiếp trên VPS ngày 14/11/2026.
>
> Phạm vi: liệt kê thư mục và file, **không đọc hoặc chèn nội dung source code**, không chứa credential/cookie/session value.

## Tổng quan

```text
Project root (VPS): /home/womcloud/IT-Job-Data-Lakehouse
Remote:             https://github.com/quanbao2603/IT-Job-Data-Lakehouse.git (main)
Python runtime:     lakehouse_env/bin/python — Python 3.10.12
```

## Cây thư mục

```text
IT-Job-Data-Lakehouse/
├── .env.example
├── .gitignore
├── README.md
├── active_env.txt
├── requirements.txt
├── data/                                   (runtime, Git-ignored)
│   ├── state_store.db
│   └── minio_storage/                      (dữ liệu vật lý MinIO)
├── docs/
│   ├── bronze-architecture-review.md
│   ├── bronze-design-review-stages-1-5.md
│   ├── deployment-vps.md
│   ├── project-structure.md
│   ├── runtime-state.md
│   └── context/
│       ├── AI_HANDOFF.md
│       ├── ARCHITECTURE.md
│       ├── CODING_CONVENTIONS.md
│       ├── CURRENT_STATUS.md
│       ├── DATA_AND_API_CONTRACTS.md
│       ├── DECISIONS.md
│       ├── DOMAIN_OR_MODULE_CONVENTIONS.md
│       └── PROJECT_CONTEXT.md
├── infrastructure/
│   └── docker-compose.yml
├── logs/                                   (runtime, Git-ignored)
│   ├── full_crawl_summary.log
│   ├── full_itviec.log
│   ├── full_topcv.log
│   ├── full_topcv_retry.log
│   ├── full_topdev.log
│   ├── full_topdev_retry.log
│   ├── full_topdev_retry2.log
│   ├── full_vietnamworks.log
│   └── post_crawl_audit.log
├── backups/                                (runtime, Git-ignored)
│   ├── lakehouse_backup_*/                 (SQLite + bronze objects + manifest)
│   └── metadata_backup_*/                  (SQLite only)
├── src/
│   ├── crawlers/
│   │   ├── base_crawler.py
│   │   ├── itviec_crawler.py
│   │   ├── topcv_crawler.py
│   │   ├── topdev_crawler.py
│   │   ├── vietnamworks_crawler.py
│   │   └── cookies/                        (runtime, Git-ignored)
│   │       ├── itviec_cookie.txt
│   │       ├── topcv_cookie.txt
│   │       ├── topdev_cookie.txt
│   │       └── vietnamworks_cookie.txt
│   ├── storage/
│   │   ├── atomic_writer.py
│   │   ├── compaction_engine.py
│   │   ├── metadata_envelope.py
│   │   └── state_manager.py
│   └── utils/
│       ├── backfill_manifest.py
│       ├── backup_bronze.py
│       ├── bronze_audit_reporter.py
│       └── clean_test_bronze.py
└── tests/
    └── test_bronze_layer.py
```

## Chi tiết theo nhóm

### 1. Source code (`src/`)

| Module | Vai trò |
|---|---|
| `src/crawlers/base_crawler.py` | Base class và lifecycle dùng chung cho crawler (lock → fetch → envelope → write → commit) |
| `src/crawlers/{itviec,topcv,topdev,vietnamworks}_crawler.py` | Crawler 4 sàn tuyển dụng |
| `src/storage/state_manager.py` | State Store SQLite: watermark, ingestion runs, manifest objects, compaction audit, concurrency lock |
| `src/storage/atomic_writer.py` | Ghi dữ liệu qua landing, verify và commit sang Bronze |
| `src/storage/compaction_engine.py` | Gom file nhỏ và ghi audit compaction |
| `src/storage/metadata_envelope.py` | Bọc payload với metadata, run ID và checksum |
| `src/utils/bronze_audit_reporter.py` | Báo cáo kiểm toán Bronze (tùy chọn `--deep` verify checksum) |
| `src/utils/backfill_manifest.py` | Đối soát và bổ sung manifest cho object chưa có sổ cái |
| `src/utils/backup_bronze.py` | Backup/restore SQLite + bronze objects, kèm restore drill |
| `src/utils/clean_test_bronze.py` | Dọn dữ liệu thử nghiệm khỏi Bronze và State Store |

### 2. Test (`tests/`)

`test_bronze_layer.py` — 17 test methods cho State Manager, AtomicWriter, MetadataEnvelope, BaseCrawler lifecycle. Lưu ý: bản project cũ có 20 test; 3 test chưa được port lại (`test_fetch_phase_heartbeat_progress`, `test_partial_unique_index_schema_exists`, `test_stress_concurrency_race`).

### 3. Hạ tầng (`infrastructure/`)

`docker-compose.yml` khai báo container MinIO với port bind `127.0.0.1:9000/9001`, credential nạp từ `.env` ngoài Git.

### 4. Tài liệu (`docs/`)

`docs/context/` là bộ tài liệu handoff cho AI/đội phát triển (đọc `AI_HANDOFF.md` trước). Các file review và deployment mô tả thiết kế Bronze, thẩm định kiến trúc, và vận hành VPS.

## Khu vực runtime (Git-ignored, không phải source)

```text
.env                  — credential MinIO, không bao giờ commit
data/                 — state_store.db + data/minio_storage/ (dữ liệu vật lý MinIO)
backups/              — snapshot backup SQLite + bronze objects
logs/                 — log crawl (kèm các *_full.log ở root từ giai đoạn trước)
src/crawlers/cookies/ — session cookie các sàn, chứa token nhạy cảm
lakehouse_env/        — Python virtual environment (3.10.12)
backup/               — (chỉ tồn tại trên máy dev local) bản copy project cũ, không có trên VPS
__pycache__/          — bytecode Python tự sinh
```

## Quy trình đồng bộ bắt buộc

1. Mọi thay đổi code được thực hiện và commit trên repo Git (GitHub `main`).
2. VPS cập nhật bằng `git pull` — VPS không bao giờ là nơi sửa code trực tiếp.
3. Trước khi chạy crawler trên VPS: `git status --porcelain` phải trả về rỗng.
4. Sau mỗi thay đổi lớn về storage/deployment, cập nhật `docs/runtime-state.md` và snapshot cấu trúc này.

## Bằng chứng kiểm tra

Snapshot này được dựng từ output read-only trực tiếp trên VPS ngày 14/11/2026 (`git remote -v`, `git log`, `git status --porcelain` sạch, `find` toàn cây trừ virtualenv/.git/minio storage, `python --version` = 3.10.12) tại commit `6ad7ba9`.
