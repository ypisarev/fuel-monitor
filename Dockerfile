FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY fuel_monitor.py /app/fuel_monitor.py

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata fonts-dejavu-core \
    && python -m pip install --no-cache-dir Pillow \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data

CMD ["python", "/app/fuel_monitor.py", "--daemon"]