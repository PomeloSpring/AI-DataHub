FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/common/requirements.txt ./shared-requirements.txt
RUN pip install --no-cache-dir -r shared-requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY sync/ ./sync/

# Create directory for embedding model cache
RUN mkdir -p /root/.cache/huggingface

EXPOSE 8000

# web 入口：单进程绑定 services.conf 登记的监听口（当前单一 web :8000，
# 即 frontend/nginx.conf 上游 backend:8000 / docker-compose 映射 / vite 代理目标）。
# 端口不在此处再抄一份：改口径只改 backend/scripts/services.conf。
CMD ["python", "-m", "backend.app.serve", "--host", "0.0.0.0"]
