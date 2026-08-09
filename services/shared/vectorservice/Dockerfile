FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/shared/vectorservice/requirements.txt /app/services/shared/vectorservice/requirements.txt
RUN pip install --no-cache-dir -r services/shared/vectorservice/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/shared/vectorservice/ /app/services/shared/vectorservice/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8010

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "services.shared.vectorservice.main:app", "--host", "0.0.0.0", "--port", "8010"]
