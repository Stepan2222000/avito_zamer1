# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Системные зависимости для Playwright + Git (для установки библиотеки из GitHub)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        wget \
        gnupg \
        ca-certificates \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libdrm2 \
        libxkbcommon0 \
        libgtk-3-0 \
        libnss3 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libxshmfence1 \
        libasound2 \
        fonts-liberation \
        xauth \
        xvfb \
        procps \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей (включая avito-library из GitHub)
COPY requirements.txt ./requirements.txt

RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && playwright install-deps chromium \
    && playwright install chromium

# Копируем проект
COPY . .

RUN chmod +x docker/entrypoint.sh

WORKDIR /app

ENV PLAYWRIGHT_HEADLESS=false

ENTRYPOINT ["/app/docker/entrypoint.sh"]
