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

EXPOSE 8000-8007 8012

# 合并 web 入口：单进程绑定全部契约端口（vite/监控口径不变）；
# 8000 为 frontend/nginx.conf 上游（backend:8000）的汇聚端口。
CMD ["python", "-m", "backend.processes.serve", "--host", "0.0.0.0", "--ports", "8000,8001,8002,8003,8004,8005,8006,8007,8012"]
