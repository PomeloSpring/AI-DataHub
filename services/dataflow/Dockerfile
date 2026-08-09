FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/dataflow/requirements.txt /app/services/dataflow/requirements.txt
RUN pip install --no-cache-dir -r services/dataflow/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/dataflow/ /app/services/dataflow/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8003

EXPOSE 8003

CMD ["python", "-m", "uvicorn", "services.dataflow.main:app", "--host", "0.0.0.0", "--port", "8003"]
