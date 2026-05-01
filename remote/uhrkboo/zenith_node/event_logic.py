"""
event_logic.py
================

This module contains a simple finite‑state event detector for the
rocket telemetry node. It derives a 16‑bit bitmask representing
various flight events from the current sensor measurements and the
history of the flight. The event flags map directly to the contract
described in the Zenith handoff document.

The flags are defined as follows:

==================  =====================================================
Bit position        Meaning
0                   ``burnActive`` – motor currently burning
1                   ``burnout`` – burnout detected
2                   ``stageSep`` – stage separation detected
3                   ``drogue`` – drogue parachute deployed
4                   ``main`` – main parachute deployed
5                   ``landed`` – vehicle has landed
==================  =====================================================

Additional bits are reserved for future use and are always zero.

The detection logic is intentionally conservative and may need
tuning on real flight data. It relies primarily on barometric
altitude and vertical acceleration; if these sensors are noisy
the thresholds in :mod:`config` should be adjusted accordingly.
"""

from __future__ import annotations

from typing import Optional

from .config import Config
from .gps_reader import GPSData
from .imu_baro_reader import IMUData


class EventDetector:
    """Derive basic flight events from sensor data and simple thresholds."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config if config is not None else Config()
        # State variables for the detector
        self._burn_active = False
        self._burnout = False
        self._stage_sep = False
        self._drogue = False
        self._main = False
        self._landed = False

        self._max_alt = float("-inf")
        self._prev_alt: Optional[float] = None

    def _current_flight_flag(self) -> int:
        """Return exactly one active flight event flag."""
        if self._landed:
            return 1 << 5
        if self._main:
            return 1 << 4
        if self._drogue:
            return 1 << 3
        if self._stage_sep:
            return 1 << 2
        if self._burnout:
            return 1 << 1
        if self._burn_active:
            return 1 << 0
        return 0

    def update(self, gps: GPSData, imu: IMUData) -> int:
        """Update the detector with new sensor data and return the bitmask.

        :param gps: Most recent GPS data (unused for event logic except as
            fallback altitude).
        :param imu: Most recent IMU/barometric data.
        :returns: A 16‑bit integer bitmask of event flags as defined above.
        """
        # Choose the barometric altitude as the primary measure; fall back to GPS
        alt = imu.baro_alt_m if imu.baro_alt_m is not None else gps.alt_m
        # Compute approximate vertical acceleration magnitude relative to gravity
        # Only the z‑axis is considered here. A positive value implies upward
        # acceleration during boost.
        vert_acc = imu.az - self.config.GRAVITY
        acc_mag = abs(vert_acc)

        # Update maximum altitude reached
        if alt > self._max_alt:
            self._max_alt = alt

        # Detect burn active – large upward acceleration
        if not self._burn_active and acc_mag > self.config.BURN_ACTIVE_ACC_THRESHOLD:
            self._burn_active = True

        # Detect burnout – end of acceleration after burnActive was true
        if self._burn_active and not self._burnout and acc_mag < self.config.BURN_OUT_ACC_THRESHOLD:
            self._burnout = True

        # Detect stage separation – altitude threshold after burnout
        if self._burnout and not self._stage_sep and alt > self.config.STAGE_SEP_ALTITUDE:
            self._stage_sep = True

        # Detect drogue deployment – significant drop from apogee
        if not self._drogue and (self._max_alt - alt) > self.config.DROGUE_DROP_ALT:
            self._drogue = True

        # Detect main deployment – altitude below threshold
        main_armed = self._drogue or self._max_alt > (self.config.MAIN_DEPLOY_ALTITUDE + self.config.DROGUE_DROP_ALT)
        if not self._main and main_armed and alt < self.config.MAIN_DEPLOY_ALTITUDE:
            self._main = True

        # Detect landing – altitude near ground with low vertical speed
        if self._prev_alt is not None:
            vertical_speed = alt - self._prev_alt
            landing_armed = self._main or self._drogue
            if (not self._landed and landing_armed and alt < self.config.LANDED_ALTITUDE
                    and abs(vertical_speed) < self.config.LANDED_VSPEED_THRESHOLD):
                self._landed = True
        self._prev_alt = alt

        return self._current_flight_flag()
