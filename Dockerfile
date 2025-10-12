FROM python:3.12-slim

WORKDIR /app

# System deps for uvicorn's standard extras (httptools, ujson, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------
# Reproducible dependency installation using uv + lock file
# ------------------------------------------------------------------
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system -r pyproject.toml

# ------------------------------------------------------------------
# Copy application source and runtime configuration
# ------------------------------------------------------------------
COPY config.yaml ./
COPY log_love.py ./
COPY app ./app
ENV PYTHONUNBUFFERED=1

# HEALTHCHECK for Cloud Run readiness probe
HEALTHCHECK CMD curl -f http://localhost:8000/healthz/ || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"] 
