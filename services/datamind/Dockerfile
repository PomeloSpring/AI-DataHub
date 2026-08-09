FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/datamind/requirements.txt /app/services/datamind/requirements.txt
RUN pip install --no-cache-dir -r services/datamind/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/datamind/ /app/services/datamind/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8001

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "services.datamind.main:app", "--host", "0.0.0.0", "--port", "8001"]
