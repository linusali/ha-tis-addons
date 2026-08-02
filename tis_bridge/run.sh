#!/bin/sh
set -e

CONFIG_PATH=/data/options.json

MQTT_HOST=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('mqtt_host','core-mosquitto'))")
MQTT_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('mqtt_port',1883))")
MQTT_USERNAME=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('mqtt_username',''))")
MQTT_PASSWORD=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('mqtt_password',''))")
CONTROLLER_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('controller_ip','192.168.6.200'))")
POLL_INTERVAL=$(python3 -c "import json; print(json.load(open('$CONFIG_PATH')).get('poll_interval',60))")

echo "--- config ---"
echo "mqtt_host=${MQTT_HOST}"
echo "mqtt_port=${MQTT_PORT}"
echo "mqtt_username is $( [ -n "$MQTT_USERNAME" ] && echo 'SET' || echo 'EMPTY -- set this in the add-on Configuration tab' )"
echo "controller_ip=${CONTROLLER_IP}"
echo "poll_interval=${POLL_INTERVAL}"
echo "--------------"

ARGS="--mqtt-host $MQTT_HOST --mqtt-port $MQTT_PORT --controller-ip $CONTROLLER_IP --poll-interval $POLL_INTERVAL"

if [ -n "$MQTT_USERNAME" ]; then
  ARGS="$ARGS --mqtt-user $MQTT_USERNAME --mqtt-pass $MQTT_PASSWORD"
fi

exec python3 /app/tis-mqtt-bridge.py $ARGS
