"""
Configuration for the Zenith Pi Zero 2 W telemetry node.

This module defines a simple dataclass holding all of the configurable
parameters for the transmitter. The values here can be adjusted
before running on the Raspberry Pi to change the device identifier,
radio frequency, transmission cadence, scaling factors and flight
event detection thresholds.
"""

from dataclasses import dataclass
import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    """Container for all node‑side configuration values."""

    # Device ID mapping to stage names (0 = Booster, 1 = Sustainer, 2 = Payload)
    DEVICE_ID: int = _env_int("UHRK_DEVICE_ID", 0)

    # LoRa radio frequency in MHz. Use 868.0 MHz for the EU/UK 868 band.
    RADIO_FREQ_MHZ: float = _env_float("UHRK_RADIO_FREQ_MHZ", 868.1)

    # LoRa modem settings. These must match the SX1303 packet forwarder.
    SIGNAL_BANDWIDTH: int = _env_int("UHRK_SIGNAL_BANDWIDTH", 125000)
    CODING_RATE: int = _env_int("UHRK_CODING_RATE", 5)
    SPREADING_FACTOR: int = _env_int("UHRK_SPREADING_FACTOR", 10)
    PREAMBLE_LENGTH: int = _env_int("UHRK_PREAMBLE_LENGTH", 8)
    SYNC_WORD: int = _env_int("UHRK_SYNC_WORD", 0x34)

    # LoRa transmit power (range 5 .. 23). Higher values give longer range
    # but draw more current. For bench testing you can leave this high; for
    # flight you may reduce it to conserve battery.
    TX_POWER: int = _env_int("UHRK_TX_POWER", 20)

    # Cadence in seconds between successive telemetry packets. One packet
    # every second keeps the ground station fed with fresh data without
    # flooding the channel. Increase this value to reduce airtime.
    CADENCE_SECONDS: float = _env_float("UHRK_CADENCE_SECONDS", 1.0)
    LAUNCH_READY_CADENCE_SECONDS: float = _env_float("UHRK_LAUNCH_READY_CADENCE_SECONDS", 0.45)
    LAUNCH_READY_DEVICE_SLOT_SECONDS: float = _env_float("UHRK_LAUNCH_READY_DEVICE_SLOT_SECONDS", 0.12)

    # Scaling factors used when converting floating point values to
    # integers for the compact binary packet. These must remain
    # consistent on both the transmitter and receiver.
    LAT_LON_SCALE: float = 1e7  # decimal degrees → scaled integer
    ALT_SCALE: float = 100.0    # metres → centimetres
    ACC_SCALE: float = 10.0     # m/s² → tenths of m/s²
    GYRO_SCALE: float = 10.0    # deg/s → tenths of deg/s

    # Thresholds for simple event detection. These values are
    # intentionally conservative; you may tune them after flight testing.
    BURN_ACTIVE_ACC_THRESHOLD: float = _env_float("UHRK_BURN_ACTIVE_ACC_THRESHOLD", 15.0)
    BURN_OUT_ACC_THRESHOLD: float = _env_float("UHRK_BURN_OUT_ACC_THRESHOLD", 3.0)
    LAUNCH_CONFIRM_SAMPLES: int = _env_int("UHRK_LAUNCH_CONFIRM_SAMPLES", 3)
    MIN_LAUNCH_ALTITUDE_DELTA: float = _env_float("UHRK_MIN_LAUNCH_ALTITUDE_DELTA", 8.0)
    STAGE_SEP_ALTITUDE: float = _env_float("UHRK_STAGE_SEP_ALTITUDE", 3000.0)
    DROGUE_DROP_ALT: float = _env_float("UHRK_DROGUE_DROP_ALT", 50.0)
    MAIN_DEPLOY_ALTITUDE: float = _env_float("UHRK_MAIN_DEPLOY_ALTITUDE", 500.0)
    LANDED_ALTITUDE: float = _env_float("UHRK_LANDED_ALTITUDE", 5.0)
    LANDED_VSPEED_THRESHOLD: float = _env_float("UHRK_LANDED_VSPEED_THRESHOLD", 1.0)

    # Standard gravity used for calibrating the accelerometer.
    GRAVITY: float = _env_float("UHRK_GRAVITY", 9.80665)
