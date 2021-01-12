FROM python:3.7

ARG BUILD_VERSION=latest
LABEL io.hass.version="$BUILD_VERSION" io.hass.type="addon" io.hass.arch="armhf|armv7|aarch64|amd64|i386"

COPY . /app
WORKDIR /app

RUN dpkg --add-architecture i386
RUN apt-get update
RUN apt-get install -y --no-install-recommends jq
RUN python setup.py install

ENV PLATFORM=docker

ENV CONFIG_DIR=/opt/hisense
ENV OPTIONS_FILE=/data/options.json

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
