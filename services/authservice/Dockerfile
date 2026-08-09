FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/authservice/requirements.txt /app/services/authservice/requirements.txt
RUN pip install --no-cache-dir -r services/authservice/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/authservice/ /app/services/authservice/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8006

EXPOSE 8006

CMD ["python", "-m", "uvicorn", "services.authservice.main:app", "--host", "0.0.0.0", "--port", "8006"]
