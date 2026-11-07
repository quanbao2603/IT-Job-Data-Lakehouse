# Cấu trúc thư mục dự án IT-Job-Data-Lakehouse

> Snapshot cấu trúc tại thời điểm xuất file.
>
> Phạm vi: liệt kê thư mục và file, **không đọc hoặc chèn nội dung source code**.

## Tổng quan

```text
Project root: /home/womcloud/IT-Job-Data-Lakehouse

Tổng entry được liệt kê: 30
Tổng thư mục:              7
Tổng file:                 23
```

## Cây thư mục

```text
IT-Job-Data-Lakehouse/
├── active_env.txt
├── data/
│   └── state_store.db
├── docs/
│   ├── bronze-architecture-review.md
│   └── project-structure.md
├── infrastructure/
│   └── docker-compose.yml
├── itviec_full.log
├── requirements.txt
├── src/
│   ├── crawlers/
│   │   ├── base_crawler.py
│   │   ├── cookies/
│   │   │   ├── itviec_cookie.txt
│   │   │   ├── topcv_cookie.txt
│   │   │   ├── topdev_cookie.txt
│   │   │   └── vietnamworks_cookie.txt
│   │   ├── itviec_crawler.py
│   │   ├── topcv_crawler.py
│   │   ├── topdev_crawler.py
│   │   └── vietnamworks_crawler.py
│   └── storage/
│       ├── atomic_writer.py
│       ├── compaction_engine.py
│       ├── metadata_envelope.py
│       └── state_manager.py
├── topcv_full.log
├── topdev_full.log
└── vietnamworks_full.log
```

## Chi tiết theo nhóm

### 1. File ở thư mục gốc

| File | Vai trò khái quát |
|---|---|
| `active_env.txt` | Thông tin môi trường đang sử dụng |
| `requirements.txt` | Danh sách Python dependencies của project |
| `itviec_full.log` | Log lượt crawl full của ITviec |
| `topcv_full.log` | Log lượt crawl full của TopCV |
| `topdev_full.log` | Log lượt crawl full của TopDev |
| `vietnamworks_full.log` | Log lượt crawl full của VietnamWorks |

### 2. `data/`

```text
data/
└── state_store.db
```

| File | Vai trò khái quát |
|---|---|
| `data/state_store.db` | SQLite State Store: watermark, trạng thái run và audit metadata |

> Dữ liệu vật lý của MinIO nằm dưới `data/minio_storage/`. Khu vực này là runtime storage, không liệt kê từng file/object trong tài liệu cấu trúc source.

### 3. `docs/`

```text
docs/
├── bronze-architecture-review.md
└── project-structure.md
```

| File | Vai trò khái quát |
|---|---|
| `docs/bronze-architecture-review.md` | Review và đánh giá kiến trúc tầng Bronze |
| `docs/project-structure.md` | Tài liệu cấu trúc thư mục hiện tại của project này |

### 4. `infrastructure/`

```text
infrastructure/
└── docker-compose.yml
```

| File | Vai trò khái quát |
|---|---|
| `infrastructure/docker-compose.yml` | Định nghĩa service/container MinIO cho Data Lakehouse |

### 5. `src/crawlers/`

```text
src/crawlers/
├── base_crawler.py
├── cookies/
│   ├── itviec_cookie.txt
│   ├── topcv_cookie.txt
│   ├── topdev_cookie.txt
│   └── vietnamworks_cookie.txt
├── itviec_crawler.py
├── topcv_crawler.py
├── topdev_crawler.py
└── vietnamworks_crawler.py
```

| File/thư mục | Vai trò khái quát |
|---|---|
| `base_crawler.py` | Base class và lifecycle dùng chung cho crawler |
| `itviec_crawler.py` | Crawler nguồn ITviec |
| `topcv_crawler.py` | Crawler nguồn TopCV |
| `topdev_crawler.py` | Crawler nguồn TopDev |
| `vietnamworks_crawler.py` | Crawler nguồn VietnamWorks |
| `cookies/` | Cookie/session files dùng khi truy cập các nền tảng nguồn |
| `cookies/itviec_cookie.txt` | Cookie/session data của ITviec; không hiển thị nội dung |
| `cookies/topcv_cookie.txt` | Cookie/session data của TopCV; không hiển thị nội dung |
| `cookies/topdev_cookie.txt` | Cookie/session data của TopDev; không hiển thị nội dung |
| `cookies/vietnamworks_cookie.txt` | Cookie/session data của VietnamWorks; không hiển thị nội dung |

> Các file cookie có thể chứa session token. Tài liệu này chỉ liệt kê tên file, không xuất nội dung nhạy cảm.

### 6. `src/storage/`

```text
src/storage/
├── atomic_writer.py
├── compaction_engine.py
├── metadata_envelope.py
└── state_manager.py
```

| File | Vai trò khái quát |
|---|---|
| `atomic_writer.py` | Ghi dữ liệu qua landing, verify và commit sang Bronze |
| `compaction_engine.py` | Gom các file nhỏ và ghi audit compaction |
| `metadata_envelope.py` | Bọc payload với metadata, run ID và checksum |
| `state_manager.py` | Quản lý state, watermark, ingestion runs và audit log |

## Các khu vực runtime không đưa vào cây source

Các khu vực sau tồn tại hoặc được project sử dụng nhưng được loại khỏi cây chi tiết để tài liệu không bị phình to bởi file sinh tự động/dữ liệu runtime:

```text
.git/
lakehouse_env/
__pycache__/
data/minio_storage/
```

| Khu vực | Lý do không liệt kê chi tiết |
|---|---|
| `.git/` | Metadata và object database của Git |
| `lakehouse_env/` | Python virtual environment, chứa dependencies cài đặt |
| `__pycache__/` | Bytecode Python tự sinh |
| `data/minio_storage/` | Dữ liệu vật lý/runtime của MinIO, có thể chứa rất nhiều object |

## Phạm vi đã kiểm tra

```text
Đã liệt kê:
- thư mục source;
- crawler modules;
- storage modules;
- configuration/infrastructure;
- state database;
- documentation;
- crawler logs;
- cookie file names (không đọc nội dung).

Không liệt kê nội dung:
- source code;
- credentials;
- cookie/session values;
- object payload trong MinIO;
- dependency files bên trong virtualenv.
```
