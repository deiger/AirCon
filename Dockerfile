FROM python:3.7
LABEL io.hass.version="0.3.2" io.hass.type="addon" io.hass.arch="armhf|armv7|aarch64|amd64|i386"

COPY . /app
WORKDIR /app

RUN python setup.py install

ENV PLATFORM=docker

ENV APP=tornado-us
ENV TYPE=ac
ENV PORT=8888
ENV LOG_LEVEL=WARNING
ENV USERNAME=
ENV PASSWORD=
ENV MQTT_HOST=
ENV MQTT_USER=
ENV CONFIG_DIR=/opt/hisense

CMD \
mkdir $CONFIG_DIR; \
if [ -z "$(cd $CONFIG_DIR && find . -maxdepth 1 -type f -name "config_*.json")" ]; then \
rm -f config_*.json && python -m aircon discovery $APP $USERNAME $PASSWORD && mv config_*.json $CONFIG_DIR/; \
fi; \   
configs= ; for i in $(find $CONFIG_DIR -maxdepth 1 -type f -name "config_*.json" -exec basename {} \;); do configs="$configs --config $CONFIG_DIR/$i --type $TYPE"; done ; \
python -m aircon --log_level $LOG_LEVEL run --port $PORT --mqtt_host "$MQTT_HOST" --mqtt_user "$MQTT_USER" $configs
