# Pipecat managed A/V agent image for Ringdown
FROM dailyco/pipecat-base:latest

WORKDIR /app

# Build dependencies for optional audio filters (e.g., Krisp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
  && rm -rf /var/lib/apt/lists/*

# Copy agent configuration alongside the source code.
COPY config.yaml /config.yaml
COPY requirements.txt /app/requirements.txt

ENV UV_PROJECT_ENVIRONMENT=.venv
RUN pip install uv
RUN uv pip install --upgrade -r requirements.txt

# Copy source tree (bot.py, config loader, etc.) into the working directory.
COPY . /app/

# The base image expects /app/bot.py as the entry point.
RUN mv /app/bot_phone_twilio.py /app/bot.py

# Optional: copy GCP credentials if present (ignored by git)
# COPY gcp_creds.json /app/gcp_creds.json
# or mount at runtime using Docker volume mounts / Cloud secrets.

# CMD inherited from base image (`python bot.py`)
