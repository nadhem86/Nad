# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NAD_DB_PATH=/data/hub.sqlite
COPY requirements.txt requirements-dev.txt ./
# Derrière un proxy TLS d'entreprise : fournir son certificat CA sans l'inscrire dans l'image
#   docker build --secret id=pip_ca,src=/chemin/ca.crt .
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN useradd --create-home --uid 10001 hub && mkdir -p /data && chown hub /data
USER hub
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["sh", "-c", "python -m hub.cli ingest-demo --if-empty && python -m hub.cli serve --host 0.0.0.0 --port 8000"]
