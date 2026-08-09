FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY services/shared/common/requirements.txt ./shared-requirements.txt
RUN pip install --no-cache-dir -r shared-requirements.txt

# Copy application code
COPY services/ ./services/
COPY sync/ ./sync/

# Create directory for embedding model cache
RUN mkdir -p /root/.cache/huggingface

EXPOSE 8001-8011

CMD ["python", "-m", "uvicorn", "services.datamind.main:app", "--host", "0.0.0.0", "--port", "8001"]
