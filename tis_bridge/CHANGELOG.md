# Changelog

## 1.0.5
- Added active polling for the water meter (every 60s by default) instead of
  relying entirely on the TIS app to trigger a reading -- the controller
  does not broadcast the water meter's health data unprompted, unlike the
  motion sensors and date/time heartbeat, so passive listening alone left
  the sensor stale whenever the app wasn't actively running.
- Switched from a hand-rolled CRC implementation to the official
  TISControlProtocol package's CRC functions (installed with --no-deps to
  avoid pulling in its homeassistant dependency, which isn't needed for
  the two functions actually used).

## 1.0.4
- Water meter unit confirmed against the physical meter dial (raw 14600 ==
  146.00 m3). Sensor now reports proper `m³` units with `device_class: water`
  (enables Home Assistant Energy dashboard water tracking) and a value
  template applying the /100 scale factor.

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
