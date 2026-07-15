# OpenMontage - Triển khai Web + Docker

Tài liệu này hướng dẫn chạy OpenMontage dưới dạng dịch vụ web đa người dùng qua Docker.

## 1. Cấu trúc mới

| File | Mục đích |
|------|----------|
| `Dockerfile` | Image Python backend + Node.js 22 + FFmpeg + HyperFrames CLI |
| `docker-compose.yml` | Stack container với volume bền vững |
| `.dockerignore` | Giảm kích thước image, tránh leak secret |
| `api/web_server.py` | FastAPI phục vụ UI + REST API |
| `api/static/index.html` | Dashboard dark mode, glassmorphism |
| `api/static/app.js` | Logic frontend |

## 2. API endpoints

| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/` | Dashboard |
| GET | `/health` | Healthcheck |
| GET | `/preflight` | Khả năng hệ thống |
| GET | `/pipelines` | Danh sách pipeline |
| POST | `/projects` | Tạo dự án + plan |
| GET | `/projects` | Danh sách dự án |
| GET | `/projects/{id}` | Chi tiết dự án |
| POST | `/projects/{id}/plan` | Tạo production plan |
| POST | `/projects/{id}/approve` | Phê duyệt stage |
| POST | `/projects/{id}/run/idea` | Chạy demo stage ý tưởng |
| GET | `/projects/{id}/assets/{path}` | Xem/tải asset |

## 3. Chạy local (không cần Docker)

```bash
# Cài dependencies
pip install -r requirements.txt -r api/requirements-mcp.txt
pip install fastapi uvicorn python-multipart

# Khởi động server
python -m uvicorn api.web_server:app --host 0.0.0.0 --port 8000
```

Truy cập: http://localhost:8000

## 4. Chạy bằng Docker Compose

> Yêu cầu: Docker Desktop đang chạy.

Build & chạy lần đầu:

```bash
docker-compose up --build -d
```

Nếu muốn build sạch (bỏ cache cũ) sau khi thay đổi dependency hoặc Dockerfile:

```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

Truy cập: http://localhost:8000

Dừng:

```bash
docker-compose down
```

## 5. Kiểm tra nhanh sau khi chạy

```bash
# Health
curl http://localhost:8000/health

# Runtime availability (ffmpeg, remotion, hyperframes)
curl http://localhost:8000/preflight | python -m json.tool

# HyperFrames CLI bên trong container
docker exec openmontage-web hyperframes --version
```

## 6. Deploy cho nhiều người dùng

Để expose ra ngoài:

1. Mở cổng firewall `8000` (hoặc cổng reverse proxy).
2. Dùng reverse proxy (Nginx / Traefik / Caddy) với SSL.
3. Thêm `VIRTUAL_HOST`/`LETSENCRYPT_HOST` nếu dùng nginx-proxy.
4. Mount volume `./projects`, `./output`, `./pipelines`, `./logs` để dữ liệu không mất khi container restart.

Ví dụ Nginx:

```nginx
server {
    listen 443 ssl;
    server_name openmontage.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
```

## 7. Lưu ý bảo mật

- Không đưa `.env` vào image (đã ignore).
- Truyền API key qua `env_file` hoặc secret manager.
- Hiện tại UI là single-tenant, chưa có auth. Để multi-user thực sự cần thêm login/DB trong phiên bản tiếp theo.
- Nếu deploy public, nên đặt OpenMontage sau reverse proxy có SSL và giới hạn IP/vpn truy cập cho đến khi có hệ thống xác thực.

## 8. Lỗi thường gặp

**Build Docker báo lỗi daemon:** Bật Docker Desktop và đợi engine sẵn sàng, sau đó chạy lại `docker-compose up --build`.

**HyperFrames trong preflight báo `false` mặc dù đã cài:** Kiểm tra quyền npm cache (`/app/.npm`) hoặc chạy `docker-compose build --no-cache` để tái cài global package.

**Preflight trả về 500 "No module named 'numpy'":** Đảm bảo `numpy>=1.24` có trong `requirements.txt` và rebuild image.
