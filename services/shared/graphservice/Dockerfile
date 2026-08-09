FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/shared/graphservice/requirements.txt /app/services/shared/graphservice/requirements.txt
RUN pip install --no-cache-dir -r services/shared/graphservice/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/shared/graphservice/ /app/services/shared/graphservice/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8011

EXPOSE 8011

CMD ["python", "-m", "uvicorn", "services.shared.graphservice.main:app", "--host", "0.0.0.0", "--port", "8011"]
