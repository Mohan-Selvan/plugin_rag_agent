FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY config ./config
COPY scripts ./scripts

RUN chmod +x scripts/*.sh \
    && mkdir -p /app/storage /app/data \
    && chown -R app:app /app

USER app
EXPOSE 8000

ENTRYPOINT ["/app/scripts/bootstrap.sh"]
CMD ["serve"]
