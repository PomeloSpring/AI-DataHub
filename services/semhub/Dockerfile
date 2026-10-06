FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/semhub/requirements.txt /app/services/semhub/requirements.txt
RUN pip install --no-cache-dir -r services/semhub/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/semhub/ /app/services/semhub/
COPY services/datamind/ /app/services/datamind/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8012

EXPOSE 8012

CMD ["python", "-m", "uvicorn", "services.semhub.main:app", "--host", "0.0.0.0", "--port", "8012"]
