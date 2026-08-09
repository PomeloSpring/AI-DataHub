FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/datagov/requirements.txt /app/services/datagov/requirements.txt
RUN pip install --no-cache-dir -r services/datagov/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/datagov/ /app/services/datagov/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8002

EXPOSE 8002

CMD ["python", "-m", "uvicorn", "services.datagov.main:app", "--host", "0.0.0.0", "--port", "8002"]
