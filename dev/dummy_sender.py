#!/usr/bin/env python3
"""
UHRK dummy telemetry sender  —  local development only.

Simulates three rocket stages (Booster, Sustainer, Payload Bay) transmitting
telemetry through the Semtech UDP packet-forwarder protocol.  The ground-station
backend (uhrk_backend.py) cannot tell the difference between this and a real
SX1303 gateway, so the full dashboard works without any hardware.

Usage
-----
    python dev/dummy_sender.py                        # send to 127.0.0.1:1700
    python dev/dummy_sender.py --host backend --port 1700   # inside Docker
    python dev/dummy_sender.py --loop-s 90           # shorter flight cycle

A full simulated flight cycle is 140 s by default (pad → launch → apogee →
landing), then repeats.  All three stages fly in parallel with a small time
offset between them so the dashboard shows realistic multi-node traffic.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import socket
import struct
import time

# ── Semtech UDP packet-forwarder protocol constants ───────────────────────────
PROTO_VER  = 2
PUSH_DATA  = 0x00
PULL_DATA  = 0x02

# ── UHRK 39-byte telemetry packet (must match packet.py + uhrk_backend.py) ───
PACKET_FMT = ">B H i i i i i B B h h h h h h h h h h H"
PACKET_LEN = struct.calcsize(PACKET_FMT)   # 47

# Adafruit RFM9x prepends a 4-byte RadioHead-compatible header
RH_HEADER = bytes([0xFF, 0x00, 0x00, 0x00])

# ── Event flag bits (matches uhrk_backend.py EVENT_FLAG_NAMES) ───────────────
F_BURN    = 1 << 0   # Burn active
F_BURNOUT = 1 << 1   # Burnout
F_SEP     = 1 << 2   # Stage separation
F_DROGUE  = 1 << 3   # Drogue deployed
F_MAIN    = 1 << 4   # Main deployed
F_LANDED  = 1 << 5   # Landed
F_IDLE    = 1 << 6   # On Pad Idle
F_READY   = 1 << 7   # On Pad Launch Ready

STAGE_NAMES = {0: "Booster", 1: "Sustainer", 2: "Payload Bay"}

# Launch site center from map.js
BASE_LAT = 55.43539
BASE_LON = -5.68593

# Quaterion scale
Q_SCALE = 10000.0

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def noise(scale: float = 1.0) -> float:
    return random.gauss(0.0, scale)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def ci32(v: float) -> int:
    return max(-0x80000000, min(0x7FFFFFFF, int(round(v))))


def ci16(v: float) -> int:
    return max(-32768, min(32767, int(round(v))))

def meters_to_lat(m):
    return m / 111320.0

def meters_to_lon(m, lat):
    return m / (111320.0 * math.cos(math.radians(lat)))

def axis_angle_to_quat(axis: tuple, angle: float) -> tuple:
    """Return (w, x, y, z) for a rotation by angle (rad) around unit axis."""
    x, y, z = axis
    half = angle * 0.5
    s = math.sin(half)
    return (math.cos(half), x * s, y * s, z * s)

def quat_mult(q1: tuple, q2: tuple) -> tuple:
    """Multiply two quaternions (w1,x1,y1,z1) * (w2,x2,y2,z2)."""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return (
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Flight profile — one instance per stage
# ─────────────────────────────────────────────────────────────────────────────

class FlightProfile:
    """Piecewise physics model of a single rocket stage."""

    def __init__(self, stage_id: int, cycle_s: float = 140.0) -> None:
        self.stage_id = stage_id
        self.seq      = 0
        self.cycle_s  = cycle_s
        self.vx = 0.0
        self.vy = 0.0

        # Orientation dynamics
        self.spin_freq   = 0.4         # rev/s  → yaw spin
        self.cone_freq   = 0.7         # wobble cycles per second
        self.max_tilt_deg = 10.0       # max pitch/tilt away from vertical
        self.max_tilt_rad = math.radians(self.max_tilt_deg)

        s = stage_id  # offset so stages don't all fly in perfect lock-step

        # --- Timeline (seconds into a cycle) ----------------------------------
        self.t_idle_end    = 10 + s * 2
        self.t_ready       = 13 + s * 2
        self.t_ignition    = 16 + s * 2
        self.t_beco        = 22 + s * 2        # burnout
        self.t_apogee      = 38 + s * 2
        self.t_drogue_end  = 62 + s * 2        # main deploys here
        self.t_landed      = 90 + s * 2

        # --- Altitude keyframes (m AMSL) --------------------------------------
        self.pad_alt     = 85.0  + s * 3.0
        self.beco_alt    = 640.0 + s * 50.0    # altitude at engine cutoff
        self.apogee_alt  = 2000.0 + s * 150.0
        self.main_alt    = 480.0                # altitude when main deploys
        self.thrust_acc  = 30.0                 # net upward acceleration during burn m/s²

    def _altitude(self, phase: float) -> float:
        """Return modelled altitude for time `phase` within a cycle."""
        p = phase
        if p <= self.t_ignition:
            return self.pad_alt

        if p <= self.t_beco:
            frac = (p - self.t_ignition) / (self.t_beco - self.t_ignition)
            return lerp(self.pad_alt, self.beco_alt, frac * frac)

        if p <= self.t_apogee:
            frac = (p - self.t_beco) / (self.t_apogee - self.t_beco)
            return lerp(self.beco_alt, self.apogee_alt, smoothstep(frac))

        if p <= self.t_drogue_end:
            frac = (p - self.t_apogee) / (self.t_drogue_end - self.t_apogee)
            return lerp(self.apogee_alt, self.main_alt, smoothstep(frac))

        if p <= self.t_landed:
            frac = (p - self.t_drogue_end) / (self.t_landed - self.t_drogue_end)
            return lerp(self.main_alt, self.pad_alt, smoothstep(frac))

        return self.pad_alt

    def _event_flags(self, phase: float) -> int:
        p = phase
        if p < self.t_idle_end:    return F_IDLE
        if p < self.t_ready:       return F_IDLE
        if p < self.t_ignition:    return F_READY
        if p < self.t_beco:        return F_BURN
        if p < self.t_apogee - 3:  return F_BURNOUT
        if p < self.t_drogue_end:  return F_DROGUE
        if p < self.t_landed:      return F_MAIN
        return F_LANDED

    def _vert_accel(self, phase: float) -> float:
        p = phase
        if p < self.t_ignition:                   return 0.0
        if p < self.t_beco:                        return self.thrust_acc + noise(2.0)
        if p < self.t_apogee:
            frac = (p - self.t_beco) / (self.t_apogee - self.t_beco)
            return lerp(-12.0, 0.0, frac) + noise(0.5)
        if p < self.t_drogue_end:                  return -2.8 + noise(0.3)
        if p < self.t_landed:                      return -1.2 + noise(0.2)
        return 0.0
    
    def _quaternion(self, phase: float) -> tuple:
        """Return orientation quaternion (w, x, y, z) for the given phase time (seconds)."""
        # Spin around the rocket's vertical axis (body z)
        yaw_angle = 2.0 * math.pi * self.spin_freq * phase
        q_spin = axis_angle_to_quat((0.0, 0.0, 1.0), yaw_angle)

        # Coning (wobble) – small tilt whose direction rotates
        cone_phase = 2.0 * math.pi * self.cone_freq * phase
        # Tilt magnitude oscillates slowly between 0 and max_tilt_rad for a natural feel
        tilt = self.max_tilt_rad * (0.5 + 0.5 * math.sin(0.3 * phase + self.stage_id)) * noise(0.5)
        cone_axis = (math.cos(cone_phase), math.sin(cone_phase), 0.0)   # horizontal axis rotating
        q_cone = axis_angle_to_quat(cone_axis, tilt)

        # Overall attitude: cone first, then spin around the (now tilted) body z
        q = quat_mult(q_cone, q_spin)
        return q

    def sample(self, elapsed_s: float) -> dict:
        """Return a full telemetry dict for the given elapsed time."""
        phase = elapsed_s % self.cycle_s
        alt   = self._altitude(phase)
        vacc  = self._vert_accel(phase)
        flags = self._event_flags(phase)

        baro_alt = alt + noise(0.3)
        imu_alt  = alt + noise(1.2)
        gps_alt  = alt + noise(3.5)

        # 1. Always report a perfect 3D fix so the dashboard accepts it immediately
        gps_status   = 2
        sats_used    = 12
        sats_in_view = 14

        # 2. Initialize a starting position once per stage
        if not hasattr(self, 'current_lat'):
            self.current_lat = BASE_LAT + noise(0.001) # Start slightly offset
            self.current_lon = BASE_LON + noise(0.001)

        # 3. Simple random walk: drift a tiny amount every tick to draw a trail
        self.current_lat += noise(0.00002) + 0.00005
        self.current_lon += noise(0.00002) + 0.00005

        lat = self.current_lat
        lon = self.current_lon

        # Accelerometer  (az ≈ 9.8 g when stationary, larger during burn)
        az = 9.80665 + vacc + noise(0.06)
        ax = noise(0.08 if flags != F_BURN else 0.6)
        ay = noise(0.08 if flags != F_BURN else 0.6)

        # Gyro noise — larger during burn
        g_scale = 6.0 if flags == F_BURN else 0.5
        gx, gy, gz = noise(g_scale), noise(g_scale), noise(g_scale * 0.4)

        qw, qx, qy, qz = self._quaternion(phase)

        return {
            "device_id":    self.stage_id,
            "seq":          self.seq,
            "lat":          lat,
            "lon":          lon,
            "gps_alt":      gps_alt,
            "baro_alt":     baro_alt,
            "imu_alt":      imu_alt,
            "gps_status":   gps_status,
            "sats_used":    sats_used,
            "sats_in_view": sats_in_view,
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
            "qw": qw,
            "qx": qx,
            "qy": qy,
            "qz": qz,
            "event_flags":  flags,
            "phase":        phase,     # for the log line only
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Packet packing
# ─────────────────────────────────────────────────────────────────────────────

def encode_sats(used: int, view: int) -> int:
    return ((view & 0x0F) << 4) | (used & 0x0F)


def pack_payload(d: dict) -> bytes:
    """Pack a telemetry dict into the 39-byte UHRK binary payload."""
    LAT_LON = 1e7
    ALT     = 100.0
    ACC     = 10.0
    GYRO    = 10.0
    Q       = Q_SCALE
    return struct.pack(
        PACKET_FMT,
        d["device_id"] & 0xFF,
        d["seq"]       & 0xFFFF,
        ci32(d["lat"]     * LAT_LON),
        ci32(d["lon"]     * LAT_LON),
        ci32(d["gps_alt"] * ALT),
        ci32(d["baro_alt"]* ALT),
        ci32(d["imu_alt"] * ALT),
        d["gps_status"] & 0xFF,
        encode_sats(d["sats_used"], d["sats_in_view"]),
        ci16(d["ax"] * ACC), ci16(d["ay"] * ACC), ci16(d["az"] * ACC),
        ci16(d["gx"] * GYRO), ci16(d["gy"] * GYRO), ci16(d["gz"] * GYRO),
        ci16(d["qw"] * Q),
        ci16(d["qx"] * Q),
        ci16(d["qy"] * Q),
        ci16(d["qz"] * Q),
        d["event_flags"] & 0xFFFF,
    )


def make_rxpk(payload_bytes: bytes, rssi: float, snr: float) -> dict:
    full = RH_HEADER + payload_bytes
    return {
        "tmst": int(time.monotonic() * 1e6) & 0xFFFFFFFF,
        "freq": 868.1,
        "chan": 0,
        "rfch": 0,
        "stat": 1,
        "modu": "LORA",
        "datr": "SF10BW125",
        "codr": "4/5",
        "rssi": int(rssi),
        "lsnr": round(snr, 1),
        "size": len(full),
        "data": base64.b64encode(full).decode("ascii"),
    }


def make_push_data(gw_id: bytes, rxpk_list: list) -> bytes:
    token = os.urandom(2)
    body  = json.dumps({"rxpk": rxpk_list}, separators=(",", ":")).encode()
    return bytes([PROTO_VER]) + token + bytes([PUSH_DATA]) + gw_id + body


def make_pull_data(gw_id: bytes) -> bytes:
    token = os.urandom(2)
    return bytes([PROTO_VER]) + token + bytes([PULL_DATA]) + gw_id


# ─────────────────────────────────────────────────────────────────────────────
#  Event name helper
# ─────────────────────────────────────────────────────────────────────────────

_EVENT_LABELS = {
    F_BURN:    "Burn active",
    F_BURNOUT: "Burnout",
    F_SEP:     "Stage sep",
    F_DROGUE:  "Drogue",
    F_MAIN:    "Main",
    F_LANDED:  "Landed",
    F_IDLE:    "Pad idle",
    F_READY:   "Launch ready",
}

def event_label(flags: int) -> str:
    return _EVENT_LABELS.get(flags, f"0x{flags:04X}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="UHRK dummy telemetry sender")
    parser.add_argument("--host",     default="127.0.0.1",
                        help="Backend UDP host (default: 127.0.0.1)")
    parser.add_argument("--port",     type=int, default=1700,
                        help="Backend UDP port (default: 1700)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between packets per stage (default: 1.0)")
    parser.add_argument("--loop-s",   type=float, default=140.0,
                        help="Flight cycle duration in seconds (default: 140)")
    args = parser.parse_args()

    # Gateway ID that matches remote/uhrkgc/test_conf.json
    gw_id    = bytes.fromhex("00016C001F161870")
    profiles = [FlightProfile(i, cycle_s=args.loop_s) for i in range(3)]
    sock     = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr     = (args.host, args.port)

    start       = time.monotonic()
    last_pull   = 0.0
    last_pkt    = [0.0, 0.0, 0.0]

    print(f"UHRK dummy sender → udp://{args.host}:{args.port}")
    print(f"Simulating 3 stages over a {args.loop_s:.0f}s flight cycle (Ctrl-C to stop)\n")
    print(f"{'t(s)':>6}  {'Stage':12}  {'Baro(m)':>8}  {'GPS':>5}  {'Sats':>4}  "
          f"{'RSSI':>5}  {'Event'}")
    print("-" * 72)
    

    try:
        while True:
            now = time.monotonic()
            t   = now - start

            # PULL_DATA keepalive — tells the backend we can accept downlinks
            if now - last_pull >= 5.0:
                sock.sendto(make_pull_data(gw_id), addr)
                last_pull = now

            for sid, profile in enumerate(profiles):
                if now - last_pkt[sid] < args.interval:
                    continue

                tel     = profile.sample(t)
                payload = pack_payload(tel)
                rssi    = -45.0 + noise(3.0)
                snr     = 9.5   + noise(0.4)
                rxpk    = make_rxpk(payload, rssi, snr)
                push    = make_push_data(gw_id, [rxpk])

                sock.sendto(push, addr)
                profile.seq = (profile.seq + 1) & 0xFFFF
                last_pkt[sid] = now

                print(
                    f"{t:6.1f}  {STAGE_NAMES[sid]:12}  "
                    f"{tel['baro_alt']:8.1f}  "
                    f"{'3D' if tel['gps_status'] == 2 else 'no':>5}  "
                    f"{tel['sats_used']:>2}|{tel['sats_in_view']:<2}  "
                    f"{rssi:5.0f}  "
                    f"{event_label(tel['event_flags'])}",
                    flush=True,
                )

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nDummy sender stopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()