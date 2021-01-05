FROM python:3.7

COPY . /app
WORKDIR /app

RUN python setup.py install

ENV APP=tornado-us
ENV TYPE=ac
ENV PORT=8888
ENV USERNAME=
ENV PASSWORD=
ENV MQTT_HOST=
ENV MQTT_USER=


CMD rm -Rf config_*.json && \
python -m aircon discovery $APP $USERNAME $PASSWORD && \   
configs= && for i in $(find . -maxdepth 1 -type f -name "config_*.json" -exec basename {} \;); do configs="$configs --config $i --type $TYPE"; done && \
python -m aircon run --port $PORT --mqtt_host $MQTT_HOST --mqtt_user $MQTT_USER $configs