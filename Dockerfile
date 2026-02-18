FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY audiobiblio/ audiobiblio/
COPY alembic.ini .
COPY migrations/ migrations/

RUN pip install --no-cache-dir .

# Default config path (mount your config.yaml here)
ENV AUDIOBIBLIO_CONFIG=/app/config.yaml

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "alembic upgrade head && audiobiblio serve"]
