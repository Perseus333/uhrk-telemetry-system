"""
imu_baro_reader.py
===================

Zenith telemetry IMU/barometer reader for the OzzMaker BerryGPS-IMU v4.

This version targets the sensor stack that matches the observed I2C scan:
- LSM6DSL accel/gyro at 0x6A
- BMP388/BMP3XX barometer at 0x77
- LIS3MDL magnetometer at 0x1C (not required for the telemetry packet, so it
  is not used here)

It intentionally avoids the unavailable ``adafruit_lsm6ds.lsm6dsl`` import and
reads the LSM6DSL directly over SMBus. The barometer still uses Adafruit's
BMP3XX driver.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from smbus2 import SMBus


@dataclass
class IMUData:
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    baro_alt_m: float
    imu_alt_m: float


class IMUBaroReader:
    """Read accel/gyro from the LSM6DSL and altitude from the BMP388/BMP3XX."""

    # I2C addresses from the user's bus scan
    LSM6DSL_ADDR = 0x6A

    # LSM6DSL registers
    WHO_AM_I = 0x0F
    CTRL1_XL = 0x10
    CTRL2_G = 0x11
    CTRL3_C = 0x12
    OUTX_L_G = 0x22
    OUTX_L_XL = 0x28

    # Sensitivity constants for the selected ranges below
    ACC_M_S2_PER_LSB = 0.061e-3 * 9.80665   # ±2 g => 0.061 mg/LSB
    GYRO_DPS_PER_LSB = 8.75e-3              # ±245 dps => 8.75 mdps/LSB

    def __init__(self, config) -> None:
        import board
        import busio
        import adafruit_bmp3xx

        self.config = config
        self._bus = SMBus(1)

        # Basic LSM6DSL bring-up:
        # CTRL3_C = BDU(1) | IF_INC(1)
        # CTRL1_XL = ODR_XL=104 Hz, FS_XL=±2 g
        # CTRL2_G  = ODR_G =104 Hz, FS_G =±245 dps
        self._bus.write_byte_data(self.LSM6DSL_ADDR, self.CTRL3_C, 0x44)
        self._bus.write_byte_data(self.LSM6DSL_ADDR, self.CTRL1_XL, 0x40)
        self._bus.write_byte_data(self.LSM6DSL_ADDR, self.CTRL2_G, 0x40)

        who = self._bus.read_byte_data(self.LSM6DSL_ADDR, self.WHO_AM_I)
        if who != 0x6A:
            raise RuntimeError(f"Unexpected LSM6DSL WHO_AM_I value: 0x{who:02X}")

        i2c = busio.I2C(board.SCL, board.SDA)
        self._baro = adafruit_bmp3xx.BMP3XX_I2C(i2c, address=0x77)
        self._baro.pressure_oversampling = 8
        self._baro.temperature_oversampling = 2

        self.gravity = self.config.GRAVITY
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._gyro_stationary_samples = 0
        self._vz = 0.0
        self._imu_alt = 0.0
        self._last_time = time.monotonic()
        self._last = IMUData(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        self._calibrate_stationary_biases()

    def _read_vec3(self, start_reg: int) -> tuple[int, int, int]:
        data = self._bus.read_i2c_block_data(self.LSM6DSL_ADDR, start_reg, 6)
        x = int.from_bytes(bytes(data[0:2]), byteorder="little", signed=True)
        y = int.from_bytes(bytes(data[2:4]), byteorder="little", signed=True)
        z = int.from_bytes(bytes(data[4:6]), byteorder="little", signed=True)
        return x, y, z

    def _read_acceleration(self) -> tuple[float, float, float]:
        rx, ry, rz = self._read_vec3(self.OUTX_L_XL)
        return (
            rx * self.ACC_M_S2_PER_LSB,
            ry * self.ACC_M_S2_PER_LSB,
            rz * self.ACC_M_S2_PER_LSB,
        )

    def _read_gyro(self) -> tuple[float, float, float]:
        rx, ry, rz = self._read_vec3(self.OUTX_L_G)
        return (
            rx * self.GYRO_DPS_PER_LSB,
            ry * self.GYRO_DPS_PER_LSB,
            rz * self.GYRO_DPS_PER_LSB,
        )

    def _calibrate_stationary_biases(self, samples: int = 150, delay: float = 0.01) -> None:
        acc_mag_sum = 0.0
        gx_sum = 0.0
        gy_sum = 0.0
        gz_sum = 0.0
        try:
            for _ in range(samples):
                ax, ay, az = self._read_acceleration()
                gx, gy, gz = self._read_gyro()
                acc_mag_sum += math.sqrt(ax * ax + ay * ay + az * az)
                gx_sum += gx
                gy_sum += gy
                gz_sum += gz
                time.sleep(delay)
            if samples > 0:
                self.gravity = acc_mag_sum / samples
                self._gyro_bias = (gx_sum / samples, gy_sum / samples, gz_sum / samples)
        except Exception:
            self.gravity = self.config.GRAVITY
            self._gyro_bias = (0.0, 0.0, 0.0)

    def _zero_small_gyro(self, value: float, deadband: float = 0.12) -> float:
        if abs(value) < deadband:
            return 0.0
        return value

    def _apply_stationary_gyro_autozero(
        self,
        ax: float,
        ay: float,
        az: float,
        raw_gx: float,
        raw_gy: float,
        raw_gz: float,
    ) -> tuple[float, float, float]:
        gx = raw_gx - self._gyro_bias[0]
        gy = raw_gy - self._gyro_bias[1]
        gz = raw_gz - self._gyro_bias[2]

        acc_mag = math.sqrt(ax * ax + ay * ay + az * az)
        gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
        stationary = abs(acc_mag - self.gravity) < 0.35 and gyro_mag < 35.0

        if stationary:
            self._gyro_stationary_samples += 1
            alpha = 0.03 if self._gyro_stationary_samples < 10 else 0.08
            self._gyro_bias = (
                self._gyro_bias[0] + gx * alpha,
                self._gyro_bias[1] + gy * alpha,
                self._gyro_bias[2] + gz * alpha,
            )
            gx = raw_gx - self._gyro_bias[0]
            gy = raw_gy - self._gyro_bias[1]
            gz = raw_gz - self._gyro_bias[2]
        else:
            self._gyro_stationary_samples = 0

        return (
            self._zero_small_gyro(gx),
            self._zero_small_gyro(gy),
            self._zero_small_gyro(gz),
        )

    def read(self) -> IMUData:
        try:
            now = time.monotonic()
            dt = now - self._last_time
            if dt <= 0:
                dt = 1e-3
            self._last_time = now

            ax, ay, az = self._read_acceleration()
            raw_gx, raw_gy, raw_gz = self._read_gyro()
            gx, gy, gz = self._apply_stationary_gyro_autozero(ax, ay, az, raw_gx, raw_gy, raw_gz)

            try:
                baro_alt = float(self._baro.altitude)
            except Exception:
                pressure_hpa = float(self._baro.pressure)
                pressure_pa = pressure_hpa * 100.0
                baro_alt = 44330.0 * (1.0 - (pressure_pa / 101325.0) ** 0.190295)

            acc_mag = math.sqrt(ax * ax + ay * ay + az * az)
            vert_acc = acc_mag - self.gravity
            gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
            if abs(vert_acc) < 0.20 and gyro_mag < 3.0:
                vert_acc = 0.0
                self._vz *= 0.5

            self._vz += vert_acc * dt
            self._imu_alt += self._vz * dt

            data = IMUData(
                ax=ax,
                ay=ay,
                az=az,
                gx=gx,
                gy=gy,
                gz=gz,
                baro_alt_m=baro_alt,
                imu_alt_m=self._imu_alt,
            )
            self._last = data
            return data
        except Exception:
            return self._last
