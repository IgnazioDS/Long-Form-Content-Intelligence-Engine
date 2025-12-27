FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc \
  && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --upgrade pip \
  && pip install -e ".[dev]"
