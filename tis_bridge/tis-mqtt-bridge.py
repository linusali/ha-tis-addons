#!/usr/bin/env python3
"""
TIS Controller UDP -> MQTT bridge with Home Assistant MQTT auto-discovery.

Passively listens for the TIS controller's broadcast traffic on port 6000
and republishes known entities to MQTT using HA's discovery convention
(homeassistant/<component>/<node_id>/<object_id>/config), so entities
appear in Home Assistant automatically without manual YAML.

Must run on a machine physically on the TIS LAN (same broadcast domain as
the controller) -- UDP broadcasts do not route across subnets.

Currently wired up:
    - Water meter (Subnet 1, Device 18) -- fully validated against 40+ real
      samples spanning a day, including a caught daily-update event.

Not yet wired up:
    - The two motion sensors. Device (1, 80) / opcode 0x012D is a *lead*
      from an earlier capture (its payload's last byte toggled 1/0 across
      two captures 5s apart -- consistent with a binary state), but this
      is UNCONFIRMED and intentionally not exposed as an entity yet. Run
      with --discover to find and confirm both motion sensors instead of
      trusting a guess.

Usage:
    # normal operation
    python3 tis-mqtt-bridge.py --mqtt-host 192.168.x.x

    # discovery mode: log every new device/opcode combo seen, flag likely
    # binary-toggle candidates -- run this while triggering the motion
    # sensors to identify them
    python3 tis-mqtt-bridge.py --discover
"""

import argparse
import json
import socket
import time
from ctypes import c_ushort, c_ubyte

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

CRC_TAB = [
0x0000,0x1021,0x2042,0x3063,0x4084,0x50A5,0x60C6,0x70E7,0x8108,0x9129,0xA14A,0xB16B,0xC18C,0xD1AD,0xE1CE,0xF1EF,
0x1231,0x0210,0x3273,0x2252,0x52B5,0x4294,0x72F7,0x62D6,0x9339,0x8318,0xB37B,0xA35A,0xD3BD,0xC39C,0xF3FF,0xE3DE,
0x2462,0x3443,0x0420,0x1401,0x64E6,0x74C7,0x44A4,0x5485,0xA56A,0xB54B,0x8528,0x9509,0xE5EE,0xF5CF,0xC5AC,0xD58D,
0x3653,0x2672,0x1611,0x0630,0x76D7,0x66F6,0x5695,0x46B4,0xB75B,0xA77A,0x9719,0x8738,0xF7DF,0xE7FE,0xD79D,0xC7BC,
0x48C4,0x58E5,0x6886,0x78A7,0x0840,0x1861,0x2802,0x3823,0xC9CC,0xD9ED,0xE98E,0xF9AF,0x8948,0x9969,0xA90A,0xB92B,
0x5AF5,0x4AD4,0x7AB7,0x6A96,0x1A71,0x0A50,0x3A33,0x2A12,0xDBFD,0xCBDC,0xFBBF,0xEB9E,0x9B79,0x8B58,0xBB3B,0xAB1A,
0x6CA6,0x7C87,0x4CE4,0x5CC5,0x2C22,0x3C03,0x0C60,0x1C41,0xEDAE,0xFD8F,0xCDEC,0xDDCD,0xAD2A,0xBD0B,0x8D68,0x9D49,
0x7E97,0x6EB6,0x5ED5,0x4EF4,0x3E13,0x2E32,0x1E51,0x0E70,0xFF9F,0xEFBE,0xDFDD,0xCFFC,0xBF1B,0xAF3A,0x9F59,0x8F78,
0x9188,0x81A9,0xB1CA,0xA1EB,0xD10C,0xC12D,0xF14E,0xE16F,0x1080,0x00A1,0x30C2,0x20E3,0x5004,0x4025,0x7046,0x6067,
0x83B9,0x9398,0xA3FB,0xB3DA,0xC33D,0xD31C,0xE37F,0xF35E,0x02B1,0x1290,0x22F3,0x32D2,0x4235,0x5214,0x6277,0x7256,
0xB5EA,0xA5CB,0x95A8,0x8589,0xF56E,0xE54F,0xD52C,0xC50D,0x34E2,0x24C3,0x14A0,0x0481,0x7466,0x6447,0x5424,0x4405,
0xA7DB,0xB7FA,0x8799,0x97B8,0xE75F,0xF77E,0xC71D,0xD73C,0x26D3,0x36F2,0x0691,0x16B0,0x6657,0x7676,0x4615,0x5634,
0xD94C,0xC96D,0xF90E,0xE92F,0x99C8,0x89E9,0xB98A,0xA9AB,0x5844,0x4865,0x7806,0x6827,0x18C0,0x08E1,0x3882,0x28A3,
0xCB7D,0xDB5C,0xEB3F,0xFB1E,0x8BF9,0x9BD8,0xABBB,0xBB9A,0x4A75,0x5A54,0x6A37,0x7A16,0x0AF1,0x1AD0,0x2AB3,0x3A92,
0xFD2E,0xED0F,0xDD6C,0xCD4D,0xBDAA,0xAD8B,0x9DE8,0x8DC9,0x7C26,0x6C07,0x5C64,0x4C45,0x3CA2,0x2C83,0x1CE0,0x0CC1,
0xEF1F,0xFF3E,0xCF5D,0xDF7C,0xAF9B,0xBFBA,0x8FD9,0x9FF8,0x6E17,0x7E36,0x4E55,0x5E74,0x2E93,0x3EB2,0x0ED1,0x1EF0,
]


def packCRC(ptr):
    crc = c_ushort(0)
    for i in range(len(ptr) - 16):
        data = c_ubyte(crc.value >> 8)
        crc.value <<= 8
        reg = c_ubyte(data.value ^ ptr[i + 16])
        crc = c_ushort(crc.value ^ CRC_TAB[reg.value])
    h, l = divmod(crc.value, 0x100)
    ptr.append(h)
    ptr.append(l)
    return ptr


def checkCRC(ptr):
    ptr = list(ptr)
    l = ptr.pop()
    h = ptr.pop()
    t = packCRC(ptr)
    return t[-1] == l and t[-2] == h


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
WATER_METER_OPCODE = (0x20, 0x25)   # response to the app's 0x2024 "health" poll


def parse_water_meter_reading(additional_bytes):
    # [echo:2][flag:1][(tag:2, value:4 BE) x N] -- first record is the live total
    body = additional_bytes[3:]
    if len(body) < 6:
        return None
    return (body[2] << 24) | (body[3] << 16) | (body[4] << 8) | body[5]


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
    sock.bind((args.bind, args.port))

    print(f"Listening on {args.bind}:{args.port} ({'discovery mode' if args.discover else 'bridge mode'})...\n")

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
            if info["device_id"] == WATER_METER_DEVICE and info["operation_code"] == WATER_METER_OPCODE:
                value = parse_water_meter_reading(info["additional_bytes"])
                if value is not None and value != last_water_value:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}] water_meter = {value}")
                    last_water_value = value
                    if client:
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
        sock.close()
        if client:
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
