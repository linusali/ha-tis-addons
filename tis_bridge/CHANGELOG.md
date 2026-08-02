# Changelog

## 1.0.8
- Added a plausibility check to the water meter reading before publishing.
  Observed in testing: an occasional reading of exactly 0 got published,
  which Home Assistant's `total_increasing` statistics interpreted as a
  genuine meter reset, then counted the jump back to the real value as
  fresh consumption -- inflating a day's recorded usage by ~1700 m3 from
  just two bad readings. A drop is now only accepted if the previous
  reading was within 1% of the meter's actual physical maximum (9999 m3,
  from the dial's digit wheel count), i.e. a real rollover. Any other
  drop is logged and discarded, and the last good value is kept.

## 1.0.7
- Fixed the water meter entity showing "unknown" after a Home Assistant
  restart and staying that way until the add-on itself was restarted.
  Root cause: state was only published to MQTT when the decoded value
  actually differed from the last one, even though we actively poll every
  `poll_interval` seconds regardless. After an HA-only restart (the add-on
  keeps running with its own unchanged in-memory last-value), the
  controller kept replying with the same value every poll and nothing new
  was ever published, since it wasn't a change -- only restarting the
  add-on reset that in-memory state and forced a republish. Now publishes
  every successful poll response, so the entity self-heals within one
  poll cycle (worst case `poll_interval` seconds of "unknown") instead of
  potentially waiting up to a day for the value to next actually change.
  State topics are deliberately NOT retained: retaining would mean a
  restarted HA (or the water meter's own not-yet-arrived first poll)
  gets served a possibly-hours-old value presented as current, which for
  the motion sensors especially would be actively misleading (a stale
  retained "ON" from an old motion event, shown as current) rather than
  just briefly and honestly "unknown". Both entities self-heal organically
  instead: water meter within one poll cycle, motion sensors on their next
  real event.

## 1.0.6
- Exposed `poll_interval` and `controller_ip` as add-on Configuration
  options instead of being fixed script defaults.

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
