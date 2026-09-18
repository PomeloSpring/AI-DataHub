FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/semanticservice/requirements.txt /app/services/semanticservice/requirements.txt
RUN pip install --no-cache-dir -r services/semanticservice/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/semanticservice/ /app/services/semanticservice/
COPY services/datamind/ /app/services/datamind/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8012

EXPOSE 8012

CMD ["python", "-m", "uvicorn", "services.semanticservice.main:app", "--host", "0.0.0.0", "--port", "8012"]
