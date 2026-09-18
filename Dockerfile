# FormPilot production image
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    HEADLESS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    fonts-dejavu-core \
    ca-certificates \
    curl \
    unzip \
    libnss3 \
    libxss1 \
    libasound2 \
    libgbm1 \
    libgtk-3-0 \
    libxshmfence1 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Normalize repositories where the package folders were flattened during upload.
# This is harmless when bot/ and core/ already exist, and prevents a confusing
# ModuleNotFoundError during Gunicorn boot.
RUN set -eux; \
    mkdir -p bot core; \
    if [ -f handlers.py ] && [ ! -f bot/handlers.py ]; then mv handlers.py bot/handlers.py; fi; \
    if [ -f llm_parser.py ] && [ ! -f bot/llm_parser.py ]; then mv llm_parser.py bot/llm_parser.py; fi; \
    if [ -f submitter.py ] && [ ! -f bot/submitter.py ]; then mv submitter.py bot/submitter.py; fi; \
    if [ -f browser.py ] && [ ! -f core/browser.py ]; then mv browser.py core/browser.py; fi; \
    if [ -f logger.py ] && [ ! -f core/logger.py ]; then mv logger.py core/logger.py; fi; \
    if [ -f proxy.py ] && [ ! -f core/proxy.py ]; then mv proxy.py core/proxy.py; fi; \
    if [ ! -f bot/__init__.py ]; then printf '%s\n' '"""Telegram bot package."""' > bot/__init__.py; fi; \
    if [ ! -f core/__init__.py ]; then printf '%s\n' '"""FormPilot core utilities."""' > core/__init__.py; fi; \
    test -f app.py; \
    test -f bot/handlers.py; \
    test -f bot/llm_parser.py; \
    test -f bot/submitter.py; \
    test -f core/browser.py; \
    test -f core/logger.py; \
    test -f core/proxy.py

EXPOSE 10000

CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers 1 \
    --threads 4 \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile -
