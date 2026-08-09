FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/aiplatform/requirements.txt /app/services/aiplatform/requirements.txt
RUN pip install --no-cache-dir -r services/aiplatform/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/aiplatform/ /app/services/aiplatform/
COPY backend/ /app/backend/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8007

EXPOSE 8007

CMD ["python", "-m", "uvicorn", "services.aiplatform.main:app", "--host", "0.0.0.0", "--port", "8007"]
