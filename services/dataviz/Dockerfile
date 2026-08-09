FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY services/shared/common/requirements.txt /app/services/shared/common/requirements.txt
RUN pip install --no-cache-dir -r services/shared/common/requirements.txt

COPY services/dataviz/requirements.txt /app/services/dataviz/requirements.txt
RUN pip install --no-cache-dir -r services/dataviz/requirements.txt

COPY services/shared/ /app/services/shared/
COPY services/dataviz/ /app/services/dataviz/
COPY backend/ /app/backend/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8004

EXPOSE 8004

CMD ["python", "-m", "uvicorn", "services.dataviz.main:app", "--host", "0.0.0.0", "--port", "8004"]
