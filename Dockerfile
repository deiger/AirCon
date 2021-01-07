FROM python:3.7

COPY . /app
WORKDIR /app

RUN apt-get update
RUN apt-get install python3-systemd

RUN python setup.py install

ENV APP=tornado-us
ENV TYPE=ac
ENV PORT=8888
ENV LOG_LEVEL=WARNING
ENV USERNAME=
ENV PASSWORD=
ENV MQTT_HOST=
ENV MQTT_USER=
VOLUME [ "/opt/hisense" ]

CMD \
if [ -z "$(cd /opt/hisense/ && find . -maxdepth 1 -type f -name "config_*.json")" ]; then \
rm -f config_*.json && python -m aircon discovery $APP $USERNAME $PASSWORD && mv config_*.json /opt/hisense/; \
fi; \   
configs= ; for i in $(find /opt/hisense/ -maxdepth 1 -type f -name "config_*.json" -exec basename {} \;); do configs="$configs --config /opt/hisense/$i --type $TYPE"; done ; \
python -m aircon --log_level $LOG_LEVEL run --port $PORT --mqtt_host $MQTT_HOST --mqtt_user $MQTT_USER $configs
