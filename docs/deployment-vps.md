# Triển khai trên VPS

Tài liệu này mô tả deployment hiện tại của IT-Job-Data-Lakehouse trên VPS. Các credential, cookie, private key và giá trị secret đã được loại bỏ.

## Kiến trúc mạng

```text
Internet
   │
   ▼
Nginx :80/:443
   │  HTTPS + reverse proxy
   ├── https://womtech.io.vn/          → 9Router Web UI
   └── https://womtech.io.vn/minio/ui/ → MinIO Console :9001
                                           │
                                           └── S3 API :9000
```

MinIO không mở trực tiếp port `9000` hoặc `9001` ra Internet. Docker publish hiện tại là:

```text
127.0.0.1:9000:9000
127.0.0.1:9001:9001
```

Nginx là public entrypoint duy nhất cho Console. TLS certificate của domain được quản lý bởi Certbot trên host.

## Thành phần

- MinIO container: `datalake-minio`.
- Image: `cgr.dev/chainguard/minio:latest`.
- Data mount: `data/minio_storage/` trên VPS.
- S3 API nội bộ: `http://127.0.0.1:9000`.
- Console nội bộ: `http://127.0.0.1:9001`.
- Domain public: `womtech.io.vn`.
- Console public: `https://womtech.io.vn/minio/ui/`.
- Nginx site config trên host: `/etc/nginx/sites-enabled/9router`.

Các path host như `/etc/nginx`, `/etc/letsencrypt` và Docker volume là deployment state của VPS, không nằm trong repository này.

## Khởi động hoặc cập nhật MinIO

Từ project root trên VPS:

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  config --quiet

docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up -d
```

Khi thay đổi biến môi trường hoặc Compose:

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up -d --force-recreate --no-deps minio
```

Lệnh trên recreate container nhưng giữ dữ liệu vì dữ liệu nằm ở bind mount `data/minio_storage/`. Không chạy `docker compose down -v` trên runtime thật.

## Kiểm tra MinIO

```bash
docker ps --filter name=datalake-minio
curl -f http://127.0.0.1:9000/minio/health/live
curl -I http://127.0.0.1:9001/
ss -ltnp | grep -E ':(9000|9001)\\b'
```

Kết quả mong đợi:

- S3 health trả `HTTP 200`.
- Console nội bộ trả `HTTP 200`.
- Listener chỉ hiện `127.0.0.1:9000` và `127.0.0.1:9001`.

## Cấu hình Console sau Nginx

Compose đặt:

```yaml
MINIO_BROWSER_REDIRECT_URL: https://womtech.io.vn/minio/ui
```

Nginx strip prefix `/minio/ui/` trước khi proxy tới `127.0.0.1:9001`, hỗ trợ WebSocket và sửa base path của SPA. MinIO tự xử lý login; không thêm Basic Auth Nginx riêng trên location Console vì lớp auth thứ hai có thể làm request API/WebSocket của Console không hoạt động đúng.

URL cũ `/browser/landing` được redirect sang `/minio/ui/` để tương thích.

Sau khi sửa Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Nếu host không cấp quyền sudo trực tiếp cho user hiện tại, thực hiện bằng tài khoản quản trị hệ thống. Không sửa Nginx từ container production nếu không hiểu namespace/process của host.

## Kiểm tra public endpoint

```bash
curl -k -I https://womtech.io.vn/minio/ui/
curl -k -I https://womtech.io.vn/browser/landing
curl -I http://womtech.io.vn/minio/ui/
curl --max-time 8 http://womtech.io.vn:9001/
```

Kết quả mong đợi:

- HTTPS Console trả `HTTP 200` hoặc chuyển tới trang login MinIO.
- HTTP chuyển hướng sang HTTPS.
- URL cũ trả `301` tới `/minio/ui/`.
- Truy cập trực tiếp `:9001` từ Internet bị từ chối.

## Bảo mật vận hành

- Không mở `9000`/`9001` bằng cách đổi bind thành `0.0.0.0`.
- Không commit `.env`, cookie, private key, database hoặc object payload.
- Dùng credential MinIO riêng cho crawler khi chuyển production; không dùng root account cho mọi thành phần.
- Giữ TLS certificate và private key ngoài repository.
- Khi thay đổi Nginx, luôn chạy `nginx -t` trước reload.
- Khi thay đổi MinIO credential, phải đối chiếu credential container và client read-only trước khi chạy crawler.
- Backup dữ liệu Bronze và SQLite trước các thay đổi storage/destructive operation.

## Khôi phục nhanh

Nếu Console không mở:

```bash
docker ps --filter name=datalake-minio
curl -f http://127.0.0.1:9000/minio/health/live
curl -I http://127.0.0.1:9001/
sudo nginx -t
sudo systemctl status nginx --no-pager
```

Phân biệt:

- MinIO local lỗi: kiểm tra container, mount và credential.
- HTTPS domain lỗi: kiểm tra Nginx, DNS và certificate.
- Giao diện trắng: kiểm tra `MINIO_BROWSER_REDIRECT_URL`, base path, static assets, API path và WebSocket.
