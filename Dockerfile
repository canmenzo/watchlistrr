FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_PATH=/config/watchlistrr.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY watchlistrr ./watchlistrr

# The image runs as root by default so it can create /config on first start.
# Set `user: "1000:1000"` in docker-compose.yml (and chown the volume) to drop
# privileges - see the README.
RUN mkdir -p /config && chmod 777 /config
VOLUME ["/config"]

ENTRYPOINT ["python", "-m", "watchlistrr"]
