#!/usr/bin/env python3
"""
TIS Controller UDP -> MQTT bridge with Home Assistant MQTT auto-discovery.

Listens for the TIS controller's broadcast traffic on port 6000 and
republishes known entities to MQTT using HA's discovery convention
(homeassistant/<component>/<node_id>/<object_id>/config), so entities
appear in Home Assistant automatically without manual YAML.

Also actively polls the water meter (Subnet 1, Device 18) on a timer,
since its "health" data (opcode 0x2024/0x2025) is only ever sent in
response to a query -- unlike the motion sensors and date/time heartbeat,
the controller does not appear to broadcast it unprompted. Confirmed
working: spoof the app's own identity (source_device_id [0xBA,0xBA],
marker [0xCC,0xB1]) and bind the listening socket to 0.0.0.0 rather than
a specific interface IP -- a prior attempt bound to one specific IP and
never saw a reply, despite an otherwise byte-identical request.

Must run on a machine physically on the TIS LAN (same broadcast domain as
the controller) -- UDP broadcasts do not route across subnets.

Requires the TISControlProtocol package (pip install TISControlProtocol,
Python >=3.11) for CRC validation -- only BytesHelper.checkCRC and
crc.packCRC are used, neither of which pull in the homeassistant package,
so install with --no-deps to avoid that large unnecessary dependency.

Currently wired up:
    - Water meter (Subnet 1, Device 18) -- fully validated against 40+ real
      samples spanning a day, unit confirmed against the physical meter
      dial (raw/100 = m3), now actively polled every --poll-interval
      seconds instead of waiting on the TIS app.
    - Two motion sensors (Subnet 1, Device 80 and 81) -- confirmed via
      --discover: same opcode (0x012D), same payload shape, Device 80
      observed toggling 1->0 within 6s (PIR-pulse-like).

Usage:
    # normal operation
    python3 tis-mqtt-bridge.py --mqtt-host 192.168.x.x

    # discovery mode: log every new device/opcode combo seen, flag likely
    # binary-toggle candidates
    python3 tis-mqtt-bridge.py --discover
"""

import argparse
import json
import socket
import threading
import time

try:
    from TISControlProtocol.BytesHelper import checkCRC
    from TISControlProtocol.crc import packCRC
except ImportError:
    print("TISControlProtocol not installed. Run: pip install --no-deps TISControlProtocol")
    raise SystemExit(1)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


def extract_info(packet):
    if len(packet) < 27 or not checkCRC(list(packet)):
        return None
    return {
        "device_id": tuple(packet[17:19]),
        "device_type": tuple(packet[19:21]),
        "operation_code": tuple(packet[21:23]),
        "source_device_id": tuple(packet[23:25]),
        "additional_bytes": packet[25:-2],
    }


# ---------------------------------------------------------------------------
# Known entities
# ---------------------------------------------------------------------------

WATER_METER_DEVICE = (0x01, 0x12)   # subnet 1, device 18
WATER_METER_REQUEST_OPCODE = [0x20, 0x24]
WATER_METER_RESPONSE_OPCODE = (0x20, 0x25)
WATER_METER_REQUEST_PAYLOAD = [0x60, 0x26]

# Spoofed app identity -- confirmed byte-for-byte match against a genuine
# captured app request. The controller only replied once the listening
# socket was bound to 0.0.0.0 instead of one specific interface IP.
APP_SOURCE_DEVICE_ID = [0xBA, 0xBA]
APP_MARKER = [0xCC, 0xB1]


def parse_water_meter_reading(additional_bytes):
    # [echo:2][flag:1][(tag:2, value:4 BE) x N] -- first record is the live total
    body = additional_bytes[3:]
    if len(body) < 6:
        return None
    return (body[2] << 24) | (body[3] << 16) | (body[4] << 8) | body[5]


# The meter's physical digit wheels only go up to 9999 m3 before wrapping
# back to 0 -- a real, if extremely rare (would take decades at any normal
# household usage rate), event that must be allowed through. Everything
# else that looks like a drop is decode garbage: observed twice in testing,
# an occasional reading of exactly 0 got published and was misread by HA's
# total_increasing statistics as a meter reset, inflating a day's recorded
# consumption by ~1700 m3. ROLLOVER_FLOOR requires the previous reading to
# have actually been near the real maximum before a drop is trusted --
# not just "any drop after a plausible-looking value" -- since real
# garbage (like the observed 0) can appear at any point in the range.
METER_MAX_M3 = 9999
RAW_UNITS_PER_M3 = 100  # confirmed against the physical dial: raw 14600 == 146.00 m3
ROLLOVER_FLOOR = int(METER_MAX_M3 * RAW_UNITS_PER_M3 * 0.99)  # last reading must be within 1% of max


def is_plausible_water_reading(value, last_value):
    if last_value is None or value >= last_value:
        return True
    return last_value >= ROLLOVER_FLOOR  # genuine rollover: was near max, now near 0


def get_local_ip_for(target_ip):
    """Ask the OS routing table which local IP would be used to reach target_ip."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 1))
        return s.getsockname()[0]
    finally:
        s.close()


def build_water_meter_request(my_ip):
    ip_bytes = [int(p) for p in my_ip.split(".")]
    header = [ord(c) for c in "SMARTCLOUD"]
    body = (
        APP_SOURCE_DEVICE_ID
        + APP_MARKER
        + WATER_METER_REQUEST_OPCODE
        + list(WATER_METER_DEVICE)
        + WATER_METER_REQUEST_PAYLOAD
    )
    length = 11 + len(WATER_METER_REQUEST_PAYLOAD)
    packet = ip_bytes + header + [0xAA, 0xAA] + [length] + body
    packet = packCRC(packet)
    return bytes(packet)


def water_meter_poller(sock, controller_ip, port, interval, stop_event):
    my_ip = get_local_ip_for(controller_ip)
    request = build_water_meter_request(my_ip)
    print(f"Active water meter poller started: querying {controller_ip}:{port} "
          f"every {interval}s as {my_ip}")
    while not stop_event.is_set():
        try:
            sock.sendto(request, (controller_ip, port))
        except OSError as e:
            print(f"poll send failed: {e}")
        stop_event.wait(interval)


# Confirmed via --discover: both motion sensors broadcast on the same opcode
# with an identical payload shape (f8 03 00 00 03 00 00 XX), differing only
# in device_id and the trailing state byte. Device 80 was observed toggling
# 1 -> 0 within 6 seconds, consistent with a PIR motion pulse.
MOTION_OPCODE = (0x01, 0x2D)
MOTION_SENSORS = {
    (0x01, 0x50): "motion_1",  # subnet 1, device 80
    (0x01, 0x51): "motion_2",  # subnet 1, device 81
}


def parse_motion_state(additional_bytes):
    if not additional_bytes:
        return None
    return additional_bytes[-1] == 1


# ---------------------------------------------------------------------------
# MQTT / Home Assistant discovery
# ---------------------------------------------------------------------------

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "tis_bridge"


def publish_water_meter_discovery(client):
    topic = f"{DISCOVERY_PREFIX}/sensor/{NODE_ID}/water_meter/config"
    payload = {
        "name": "TIS Water Meter",
        "unique_id": "tis_water_meter_reading",
        "state_topic": f"{NODE_ID}/water_meter/state",
        "state_class": "total_increasing",
        "device_class": "water",
        "unit_of_measurement": "m³",
        # confirmed against the meter's physical dial: raw=14600 == 146.00 m3,
        # i.e. 1 raw unit = 0.01 m3
        "value_template": "{{ value | float / 100 }}",
        "suggested_display_precision": 2,
        "device": {
            "identifiers": ["tis_bridge"],
            "name": "TIS Controller Bridge",
            "manufacturer": "TIS Control",
        },
    }
    client.publish(topic, json.dumps(payload), retain=True)


def publish_motion_discovery(client, object_id, friendly_name):
    topic = f"{DISCOVERY_PREFIX}/binary_sensor/{NODE_ID}/{object_id}/config"
    payload = {
        "name": friendly_name,
        "unique_id": f"tis_{object_id}",
        "state_topic": f"{NODE_ID}/{object_id}/state",
        "device_class": "motion",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {
            "identifiers": ["tis_bridge"],
            "name": "TIS Controller Bridge",
            "manufacturer": "TIS Control",
        },
    }
    client.publish(topic, json.dumps(payload), retain=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=6000)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--controller-ip", default="192.168.6.200")
    ap.add_argument("--poll-interval", type=float, default=60.0,
                     help="Seconds between active water meter polls (default 60)")
    ap.add_argument("--no-poll", action="store_true", help="Disable active water meter polling")
    ap.add_argument("--mqtt-host")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-user")
    ap.add_argument("--mqtt-pass")
    ap.add_argument("--discover", action="store_true",
                     help="Log every new device/opcode combo instead of publishing to MQTT. "
                          "Use this to identify the motion sensors.")
    args = ap.parse_args()

    if not args.discover:
        if mqtt is None:
            print("paho-mqtt not installed. Run: pip install paho-mqtt")
            return
        if not args.mqtt_host:
            print("--mqtt-host is required unless --discover is set")
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if args.mqtt_user:
            client.username_pw_set(args.mqtt_user, args.mqtt_pass)
        connect_host = args.mqtt_host
        try:
            socket.getaddrinfo(connect_host, args.mqtt_port)
        except socket.gaierror:
            # Docker-internal hostnames like "core-mosquitto" don't resolve
            # when running with host_network: true (no longer on the
            # internal hassio bridge network). Under host networking,
            # "localhost" refers to the actual HAOS host itself, and
            # Mosquitto binds to the host's interfaces by default, so this
            # is a reliable fallback rather than a real connection attempt.
            print(f"'{connect_host}' did not resolve (expected under host_network mode) -- falling back to localhost")
            connect_host = "localhost"
        client.connect(connect_host, args.mqtt_port, 60)
        client.loop_start()
        publish_water_meter_discovery(client)
        for object_id in MOTION_SENSORS.values():
            friendly = object_id.replace("_", " ").title()
            publish_motion_discovery(client, object_id, f"TIS {friendly}")
        print(f"Connected to MQTT at {args.mqtt_host}:{args.mqtt_port}, discovery config published.")
    else:
        client = None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((args.bind, args.port))

    print(f"Listening on {args.bind}:{args.port} ({'discovery mode' if args.discover else 'bridge mode'})...\n")

    stop_event = threading.Event()
    poller = None
    if not args.no_poll:
        poller = threading.Thread(
            target=water_meter_poller,
            args=(sock, args.controller_ip, args.port, args.poll_interval, stop_event),
            daemon=True,
        )
        poller.start()

    last_water_value = None
    last_motion_state = {}
    seen_combos = set()

    try:
        while True:
            data, addr = sock.recvfrom(65535)
            info = extract_info(list(data))
            if info is None:
                continue

            # --- known: water meter ---
            if info["device_id"] == WATER_METER_DEVICE and info["operation_code"] == WATER_METER_RESPONSE_OPCODE:
                value = parse_water_meter_reading(info["additional_bytes"])
                if value is not None and not is_plausible_water_reading(value, last_water_value):
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}] water_meter REJECTED implausible reading: {value} "
                          f"(last was {last_water_value}, not near rollover) raw={bytes(info['additional_bytes']).hex()}")
                    value = None
                if value is not None:
                    changed = value != last_water_value
                    if changed:
                        ts = time.strftime("%Y-%m-%d %H:%M:%S")
                        print(f"[{ts}] water_meter = {value}")
                        last_water_value = value
                    if client:
                        # Publish every poll response, not just on change --
                        # we poll every --poll-interval seconds regardless,
                        # so there's no reason MQTT should only hear about it
                        # when the value happens to differ. This is what
                        # makes the entity self-heal within one poll cycle
                        # after an HA restart, rather than waiting for the
                        # value to next actually change (up to a day away).
                        # Deliberately not retained: HA would treat a
                        # replayed old value as a fresh state event on every
                        # restart. The organic self-heal above (worst case
                        # one poll_interval of "unknown") is preferable to
                        # that, same reasoning as the motion sensors below.
                        client.publish(f"{NODE_ID}/water_meter/state", value)
                continue

            # --- known: motion sensors ---
            if info["operation_code"] == MOTION_OPCODE and info["device_id"] in MOTION_SENSORS:
                object_id = MOTION_SENSORS[info["device_id"]]
                state = parse_motion_state(info["additional_bytes"])
                if state is not None and last_motion_state.get(object_id) != state:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}] {object_id} = {'ON (motion)' if state else 'OFF (clear)'}")
                    last_motion_state[object_id] = state
                    if client:
                        # Not retained, deliberately: a retained "ON" from
                        # an old motion event would be presented to HA as
                        # current on restart, which is actively misleading
                        # rather than just temporarily unknown. Motion
                        # state should only ever reflect a real, recent
                        # event -- it self-heals naturally on the next one.
                        client.publish(f"{NODE_ID}/{object_id}/state", "ON" if state else "OFF")
                continue

            # --- discovery mode: log anything new ---
            if args.discover:
                combo = (info["device_id"], info["operation_code"])
                ab = info["additional_bytes"]
                key = (combo, tuple(ab))
                if key in seen_combos:
                    continue
                seen_combos.add(key)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                toggle_hint = ""
                if ab and ab[-1] in (0, 1):
                    toggle_hint = "  <-- last byte is 0/1, possible binary state"
                print(f"[{ts}] NEW: device={info['device_id']} type={info['device_type']} "
                      f"op={info['operation_code']:} src={info['source_device_id']} "
                      f"bytes={bytes(ab).hex()}{toggle_hint}")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop_event.set()
        sock.close()
        if client:
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
