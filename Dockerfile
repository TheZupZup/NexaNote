FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY nexanote ./nexanote

RUN mkdir -p /data
VOLUME ["/data"]

ENV NEXANOTE_HOST=0.0.0.0 \
    NEXANOTE_API_PORT=8766 \
    NEXANOTE_WEBDAV_PORT=8765 \
    NEXANOTE_DATA_DIR=/data \
    NEXANOTE_USERNAME=nexanote \
    NEXANOTE_PASSWORD=nexanote

EXPOSE 8765 8766

CMD ["sh", "-c", "python main.py --host \"$NEXANOTE_HOST\" --api-port \"$NEXANOTE_API_PORT\" --webdav-port \"$NEXANOTE_WEBDAV_PORT\" --data-dir \"$NEXANOTE_DATA_DIR\" --username \"$NEXANOTE_USERNAME\" --password \"$NEXANOTE_PASSWORD\""]
