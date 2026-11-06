ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.22
FROM ${BUILD_FROM}

RUN apk add --no-cache tzdata sqlite

WORKDIR /opt/ballager
COPY app/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ ./
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
