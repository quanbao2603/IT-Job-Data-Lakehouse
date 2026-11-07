# Runtime State Snapshot

> Snapshot đã sanitize của môi trường VPS. Đây là bằng chứng tại thời điểm ghi tài liệu, không phải cấu hình live tự động cập nhật.
>
> Không chứa credential, API key, cookie, session token, private key hoặc object payload.

## Thời điểm snapshot

```text
2026-11-07 07:58 +0700
```

## Bronze inventory

```text
Bronze objects:  17
Manifest rows:   17
Landing objects: 0
```

Ý nghĩa:

- 17 object vật lý trong bucket Bronze đã được đối chiếu với 17 dòng trong `ingestion_objects`.
- Landing không còn object tạm sau các lượt kiểm tra.
- Con số object không đồng nghĩa với số record; record count được kiểm tra riêng trong Bronze audit/review.

## MinIO

```text
Container:       datalake-minio
S3 API:          127.0.0.1:9000
Console:         127.0.0.1:9001
Public Console:  https://womtech.io.vn/minio/ui/
Public S3/API:   không mở trực tiếp qua port 9000
Public :9001:    không mở trực tiếp
```

MinIO chạy sau Docker và bind vào localhost. Internet không kết nối thẳng tới `9000` hoặc `9001`.

## Reverse proxy và HTTPS

```text
Reverse proxy: Nginx
HTTPS domain:  https://womtech.io.vn
Console path:  /minio/ui/
Legacy path:   /browser/landing → redirect tới /minio/ui/
TLS:           Certbot/Let's Encrypt trên host
```

Nginx chuyển tiếp Console tới MinIO nội bộ, hỗ trợ static assets và WebSocket. MinIO tự xử lý login; Nginx không đặt thêm Basic Auth trên location Console.

## Runtime storage và state

Các path sau tồn tại trên VPS nhưng không được commit:

```text
data/state_store.db
data/minio_storage/
backups/
.env
lakehouse_env/
*.log
src/crawlers/cookies/*.txt
```

Lý do:

- `.env` chứa secret.
- Cookie chứa session data.
- `data/` và `backups/` là dữ liệu/runtime artifacts.
- `lakehouse_env/` là virtualenv lớn, có thể tái tạo từ `requirements.txt`.
- Log có thể chứa dữ liệu crawl hoặc thông tin vận hành.

## Kiểm chứng gần nhất

Các kiểm tra runtime đã ghi nhận:

```text
MinIO Console local: HTTP 200
MinIO S3 health:     HTTP 200
Nginx config test:   PASS
HTTPS Console path:  phản hồi qua Nginx
Old Console path:    redirect 301
Direct public :9001: bị từ chối
```

## Không suy diễn từ snapshot

Snapshot này không khẳng định:

- HA hoặc redundancy vật lý của MinIO single-node/single-disk.
- Off-host backup đang chạy tự động.
- Cookie nguồn còn hợp lệ.
- DNS/certificate sẽ luôn hợp lệ sau khi thay đổi nhà cung cấp.
- Runtime state sẽ tự đồng bộ ngược lên GitHub.

Sau mỗi thay đổi deployment hoặc ingestion lớn, cập nhật tài liệu này bằng số liệu đã kiểm tra và không ghi secret.
