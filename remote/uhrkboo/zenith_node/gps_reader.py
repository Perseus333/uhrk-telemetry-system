"""
gps_reader.py
=================

This module provides a small helper class for reading NMEA sentences
from the BerryGPS‑IMU HAT attached to the Raspberry Pi Zero 2 W.
It uses PySerial to read from ``/dev/serial0`` and `pynmea2` to parse
sentences. The reading happens in a background thread so that calls
to :meth:`GPSReader.read` return the most recent fix without
blocking the main loop. If parsing fails or no fix is present the
last known good values are reused.

Fields exposed by :class:`GPSData` include decimal latitude,
decimal longitude, altitude in metres, a simple fix status code and
the number of satellites in view.

Use :class:`GPSReader.close` to stop the background thread when
shutting down.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import serial  # type: ignore
import pynmea2  # type: ignore


@dataclass
class GPSData:
    """Container for a single GPS fix.

    :param lat: Latitude in decimal degrees.
    :param lon: Longitude in decimal degrees.
    :param alt_m: Altitude above mean sea level in metres.
    :param status: Fix status code (0 = no fix, 1 = 2D fix, 2 = 3D fix).
    :param sats: Number of satellites used in the fix.
    """

    lat: float
    lon: float
    alt_m: float
    status: int
    sats: int


class GPSReader:
    """Continuously reads NMEA sentences from a serial port and exposes the most recent fix.

    The reader spawns a background thread on instantiation to avoid blocking
    the caller. It keeps the most recent :class:`GPSData` in a thread safe
    variable. On any parse error or communications fault the previous
    good fix is retained so that the main telemetry loop can continue
    transmitting.
    """

    def __init__(self, port: str = "/dev/serial0", baudrate: int = 9600, timeout: float = 1.0) -> None:
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._lock = threading.Lock()
        # Start with a sensible default: zeroed position and no fix
        self._latest: GPSData = GPSData(lat=0.0, lon=0.0, alt_m=0.0, status=0, sats=0)
        self._fix_dimension = 1
        self._stop = False
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _status_for_valid_position(self) -> int:
        return 2 if self._fix_dimension >= 3 else 1

    def _parse_sentence(self, line: str) -> Optional[GPSData]:
        """Parse a single NMEA sentence and return a :class:`GPSData` if it
        contains position information. Returns ``None`` if the sentence
        doesn't convey position or if parsing fails.
        """
        try:
            msg = pynmea2.parse(line)
        except pynmea2.ParseError:
            return None
        # We care about sentences carrying lat/lon (GGA, RMC) and altitude (GGA)
        if isinstance(msg, pynmea2.types.talker.GGA):  # type: ignore[attr-defined]
            lat = msg.latitude
            lon = msg.longitude
            alt = float(msg.altitude) if getattr(msg, "altitude", None) is not None else self._latest.alt_m
            sats_used = int(getattr(msg, "num_sats", 0) or 0)
            # gps_qual indicates fix quality (0 = invalid, 1 = GPS, 2 = DGPS, 6 = estimated)
            fix_quality = int(getattr(msg, "gps_qual", 0) or 0)
            sats = sats_used if fix_quality > 0 or sats_used > 0 else self._latest.sats
            if fix_quality == 0:
                status = 0
            else:
                status = self._status_for_valid_position()
            return GPSData(lat=lat, lon=lon, alt_m=alt, status=status, sats=sats)
        elif isinstance(msg, pynmea2.types.talker.RMC):  # type: ignore[attr-defined]
            # RMC lacks altitude so reuse the last known altitude
            if getattr(msg, "status", "V") != "A":
                # 'A' indicates data valid
                status = 0
            else:
                status = self._status_for_valid_position()
            lat = msg.latitude
            lon = msg.longitude
            # Altitude and satellites count remain unchanged
            alt = self._latest.alt_m
            sats = self._latest.sats
            return GPSData(lat=lat, lon=lon, alt_m=alt, status=status, sats=sats)
        elif getattr(msg, "sentence_type", "") == "GSA":
            try:
                self._fix_dimension = int(getattr(msg, "mode_fix_type", self._fix_dimension) or self._fix_dimension)
            except (TypeError, ValueError):
                pass
            if self._latest.status > 0:
                return GPSData(
                    lat=self._latest.lat,
                    lon=self._latest.lon,
                    alt_m=self._latest.alt_m,
                    status=self._status_for_valid_position(),
                    sats=self._latest.sats,
                )
            return None
        elif getattr(msg, "sentence_type", "") == "GSV":
            # GSV reports satellites in view even before they are used in a fix.
            try:
                sats = int(getattr(msg, "num_sv_in_view", self._latest.sats) or self._latest.sats)
            except (TypeError, ValueError):
                sats = self._latest.sats
            return GPSData(
                lat=self._latest.lat,
                lon=self._latest.lon,
                alt_m=self._latest.alt_m,
                status=self._latest.status,
                sats=sats,
            )
        else:
            return None

    def _read_loop(self) -> None:
        """Background thread that continuously reads from the serial port."""
        while not self._stop:
            try:
                line_bytes = self.ser.readline()
                if not line_bytes:
                    continue
                try:
                    line = line_bytes.decode("ascii", errors="ignore").strip()
                except UnicodeDecodeError:
                    continue
                if not line.startswith("$"):
                    continue
                data = self._parse_sentence(line)
                if data is not None:
                    with self._lock:
                        self._latest = data
            except Exception:
                # Ignore all errors; keep previous fix
                continue

    def read(self) -> GPSData:
        """Return the most recent GPS fix.

        This call never blocks and will always return a :class:`GPSData`
        even if no new fix has arrived since the previous call.
        """
        with self._lock:
            return self._latest

    def close(self) -> None:
        """Signal the background thread to stop and close the serial port."""
        self._stop = True
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
