FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/datacatalog/requirements.txt /app/services/datacatalog/requirements.txt
RUN pip install --no-cache-dir -r services/datacatalog/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/datacatalog/ /app/services/datacatalog/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8005

EXPOSE 8005

CMD ["python", "-m", "uvicorn", "services.datacatalog.main:app", "--host", "0.0.0.0", "--port", "8005"]
