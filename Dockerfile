FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc \
  && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${APP_GID} app \
  && useradd -u ${APP_UID} -g ${APP_GID} -m app

COPY . /app

RUN pip install --upgrade pip \
  && pip install -e ".[dev]"
