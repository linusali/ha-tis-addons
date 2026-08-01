# Changelog

## 1.0.3
- Dropped reliance on Supervisor's `services: mqtt:want` auto-injection --
  it did not provide credentials in testing (confirmed via env dump: no
  MQTT_* variables present at all). MQTT host/port/username/password are
  now configured manually via the add-on's Configuration tab instead.

## 1.0.1
- Fixed paho-mqtt v2 callback API deprecation warning.
- Removed invalid empty `url` field from config.yaml that could fail Supervisor's validation.
- Added automatic `localhost` fallback for MQTT connection when the Supervisor-injected
  hostname (e.g. `core-mosquitto`) doesn't resolve under `host_network: true` mode.

## 1.0.0
- Initial release: water meter sensor + two motion sensor binary_sensors,
  published to MQTT with Home Assistant auto-discovery.
