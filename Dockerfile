FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Install dependencies first (cached unless pyproject.toml changes)
COPY pyproject.toml .
RUN mkdir -p audiobiblio && \
    touch audiobiblio/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf audiobiblio

# 2) Copy actual code (this layer changes often but is fast)
COPY audiobiblio/ audiobiblio/
COPY alembic.ini .
COPY migrations/ migrations/

# 3) Reinstall just the package (deps already cached, only code changed)
RUN pip install --no-cache-dir --no-deps .

ENV AUDIOBIBLIO_CONFIG=/app/config.yaml

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "alembic upgrade head && audiobiblio serve"]
