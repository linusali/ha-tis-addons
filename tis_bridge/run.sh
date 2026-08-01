#!/bin/sh
set -e

# Supervisor injects these automatically because config.yaml declares
# `services: [mqtt:want]` -- no manual credential entry needed, as long as
# the official Mosquitto broker add-on is installed.
MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"

echo "--- env diagnostic ---"
echo "MQTT_HOST=${MQTT_HOST}"
echo "MQTT_PORT=${MQTT_PORT}"
echo "MQTT_USERNAME is $( [ -n "$MQTT_USERNAME" ] && echo 'SET' || echo 'EMPTY/UNSET' )"
echo "MQTT_PASSWORD is $( [ -n "$MQTT_PASSWORD" ] && echo 'SET' || echo 'EMPTY/UNSET' )"
echo "Full env dump (names only, no values):"
env | cut -d= -f1 | sort
echo "----------------------"

ARGS="--mqtt-host $MQTT_HOST --mqtt-port $MQTT_PORT"

if [ -n "$MQTT_USERNAME" ]; then
  ARGS="$ARGS --mqtt-user $MQTT_USERNAME --mqtt-pass $MQTT_PASSWORD"
fi

exec python3 /app/tis-mqtt-bridge.py $ARGS
