# AJ CINE AI — Developed by AJ APPLICATIONS · Developer: AJWEYA
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
# build-essential is only needed to compile TgCrypto
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY ajcine ./ajcine

# run as an unprivileged user; /app/data holds backups (and legacy poster files)
RUN useradd --system --uid 10001 --home /app ajcine && mkdir -p /app/data && chown -R ajcine /app
USER ajcine
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)"

CMD ["python", "-m", "ajcine"]
