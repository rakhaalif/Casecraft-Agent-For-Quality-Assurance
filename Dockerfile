# syntax=docker/dockerfile:1.7

# Lightweight Python base
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps (minimal). Pillow wheels usually work, but keep runtime libs handy
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better build caching
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy application source
COPY . .

# Non-root user for better security
RUN useradd -m appuser \
    && chown -R appuser:appuser /app
USER appuser

# No port exposed (bot uses long polling)

# Environment variables expected at runtime (set via --env or --env-file)
#   TELEGRAM_BOT_TOKEN=<token>
#   GOOGLE_API_KEY=<key>
# Optional:
#   GEMINI_MODEL=gemini-1.5-flash

# Default command: start the bot with retry wrapper
CMD ["python", "telegram_bot.py"]
