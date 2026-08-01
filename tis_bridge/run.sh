#!/bin/sh
set -e

# Supervisor injects these automatically because config.yaml declares
# `services: [mqtt:want]` -- no manual credential entry needed, as long as
# the official Mosquitto broker add-on is installed.
MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"

ARGS="--mqtt-host $MQTT_HOST --mqtt-port $MQTT_PORT"

if [ -n "$MQTT_USERNAME" ]; then
  ARGS="$ARGS --mqtt-user $MQTT_USERNAME --mqtt-pass $MQTT_PASSWORD"
fi

exec python3 /app/tis-mqtt-bridge.py $ARGS
