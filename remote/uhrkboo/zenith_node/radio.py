"""
radio.py
========

Abstraction over the Adafruit RFM95W LoRa radio. This module
initialises the radio using Blinka, configures the frequency and
transmit power from the configuration and exposes a simple
:meth:`LoRaRadio.send` method. Any exceptions during transmission are
silently swallowed to ensure the main telemetry loop never stops.
"""

from __future__ import annotations

from typing import Any

import board  # type: ignore
import busio  # type: ignore
import digitalio  # type: ignore
import adafruit_rfm9x  # type: ignore

from .config import Config


class LoRaRadio:
    """Wrapper around the RFM95W radio for sending telemetry packets."""

    def __init__(self, config: Config) -> None:
        self.config = config
        # Set up the SPI bus. This uses the Pi's hardware SPI pins.
        self._spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        # Chip select on CE1 (GPIO7) and reset on GPIO25; these pins were
        # validated in the wiring bring‑up.
        self._cs = digitalio.DigitalInOut(board.CE1)
        self._reset = digitalio.DigitalInOut(board.D25)
        # Initialise the radio at the configured frequency. The RFM9x
        # constructor will raise an exception if SPI or wiring is not
        # correctly set up, which should surface during bench testing.
        self._rfm9x = adafruit_rfm9x.RFM9x(
            self._spi,
            self._cs,
            self._reset,
            config.RADIO_FREQ_MHZ,
            preamble_length=config.PREAMBLE_LENGTH,
            crc=True,
        )
        # Configure transmit power. Some versions of the library expose
        # ``tx_power`` while others require setting ``tx_power`` via a
        # property. We guard against AttributeError just in case.
        try:
            self._rfm9x.tx_power = config.TX_POWER
        except Exception:
            pass
        try:
            self._rfm9x.node = config.DEVICE_ID
            self._rfm9x.destination = 0xFF
        except Exception:
            pass
        for attr, value in (
            ("signal_bandwidth", config.SIGNAL_BANDWIDTH),
            ("coding_rate", config.CODING_RATE),
            ("spreading_factor", config.SPREADING_FACTOR),
            ("preamble_length", config.PREAMBLE_LENGTH),
        ):
            try:
                setattr(self._rfm9x, attr, value)
            except Exception as exc:
                print(f"Failed to set LoRa {attr}={value}: {exc}", flush=True)
        # Enable CRC to improve packet integrity on the link
        try:
            self._rfm9x.enable_crc = True
        except Exception:
            pass
        # CircuitPython RFM9x does not expose sync_word in this version.
        # RegSyncWord 0x39 must be 0x34 to match the public SX1303 gateway.
        try:
            self._rfm9x._write_u8(0x39, config.SYNC_WORD)
        except Exception as exc:
            print(f"Failed to set LoRa sync word: {exc}", flush=True)

    def send(self, data: bytes) -> None:
        """Transmit a bytes object over LoRa.

        Any exceptions raised by the underlying library are swallowed.
        This method never returns a value and is safe to call in a
        high‑frequency loop.
        """
        try:
            self._rfm9x.send(data)
        except Exception as exc:
            print(f"LoRa send failed: {exc}", flush=True)
            # Do not propagate exceptions; the main loop must continue
            pass

    def receive(self, timeout_s: float) -> bytes | None:
        """Listen briefly for a LoRa command packet."""
        try:
            packet = self._rfm9x.receive(
                timeout=timeout_s,
                keep_listening=False,
                with_header=True,
            )
        except Exception as exc:
            print(f"LoRa receive failed: {exc}", flush=True)
            return None
        return bytes(packet) if packet is not None else None
