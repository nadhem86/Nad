FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OKP_DATA_DIR=/data OKP_DB_PATH=/data/okp.sqlite OKP_SOURCES_DIR=/data/sources \
    OKP_MODELS_DIR=/app/models OKP_PRINCIPALS_FILE=/app/config/principals.demo.yaml

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY okp ./okp
COPY models ./models
COPY config ./config
COPY generated ./generated

RUN useradd --create-home --uid 10001 okp && mkdir -p /data && chown okp /data
USER okp
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "-m", "okp", "serve", "--host", "0.0.0.0", "--port", "8000"]
