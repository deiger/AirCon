#!/bin/bash
set -e

PORT=$(jq -r '.port // 8888' $OPTIONS_FILE)
TYPE=$(jq -r '.type // "ac"' $OPTIONS_FILE)
LOG_LEVEL=$(jq -r '.log_level | ascii_upcase // "WARNING"' $OPTIONS_FILE)
MQTT_HOST=$(jq -r '.mqtt_host // ""' $OPTIONS_FILE)
MQTT_USER=$(jq -r 'if (.mqtt_user and .mqtt_pass) then (.mqtt_user + ":" + .mqtt_pass) else "" end' $OPTIONS_FILE)
APPS=$(jq -r '.app | length // 0' $OPTIONS_FILE)

mkdir $CONFIG_DIR
if [ -z "$(find $CONFIG_DIR -maxdepth 1 -type f -name "config_*.json")" ]; then
  rm -f config_*.json
  for i in $(seq 0 $(($APPS-1))); do
    CODE=$(jq -r '.app['$i'].code' $OPTIONS_FILE)
    USERNAME=$(jq -r '.app['$i'].username' $OPTIONS_FILE)
    PASSWORD=$(jq -r '.app['$i'].password' $OPTIONS_FILE)
    python -m aircon discovery $CODE $USERNAME $PASSWORD
  done
  mv config_*.json $CONFIG_DIR/
fi
configs=
for i in $(find $CONFIG_DIR -maxdepth 1 -type f -name "config_*.json" -exec basename {} \;)
  do configs="$configs --config $CONFIG_DIR/$i --type $TYPE"
done
python -m aircon --log_level $LOG_LEVEL run --port $PORT --mqtt_host "$MQTT_HOST" --mqtt_user "$MQTT_USER" $configs
