# UHRK Pi Deployment Guide

This guide explains how to clone the repository onto a Raspberry Pi and install
either a telemetry node or the ground station services.

The installers make the software repeatable, but they do not replace hardware
bring-up. SPI, I2C, serial GPS wiring, LoRa HAT wiring, and the SX1303 packet
forwarder still need to match the hardware.

## Repository Clone

On the Pi:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/WilliamB75/uhrk-telemetry-system.git
cd uhrk-telemetry-system
```

If the repository is private, use the team's normal GitHub access method.

## Before Installing

Enable the hardware interfaces needed by the role.

Telemetry node:

- SPI for the RFM9x LoRa radio
- I2C for BerryGPS-IMU sensors
- Serial hardware for GPS on `/dev/serial0`

Ground station:

- SPI for the SX1303 gateway HAT
- Serial hardware for GC GPS, usually `/dev/ttyAMA0`
- Network/hotspot setup for dashboard access

On Raspberry Pi OS, `sudo raspi-config` is the safest way to enable these:

- Interface Options -> SPI -> Enable
- Interface Options -> I2C -> Enable
- Interface Options -> Serial Port -> Disable login shell, enable serial hardware

Reboot after changing hardware interface settings.

## Install A Telemetry Node

From the cloned repository:

```bash
sudo ./install/install_node.sh --device-id 0 --frequency 868.1 --user uhrkboo
```

Device IDs:

- `0`: Booster
- `1`: Sustainer
- `2`: Payload Bay

Suggested first installs:

```bash
sudo ./install/install_node.sh --device-id 0 --frequency 868.1 --user uhrkboo
sudo ./install/install_node.sh --device-id 1 --frequency 868.1 --user uhrksus
sudo ./install/install_node.sh --device-id 2 --frequency 868.1 --user uhrkpay
```

If multi-frequency operation is enabled later, use different frequencies for
each node and make sure the SX1303 gateway is configured to listen on all of
them.

The node installer:

- Creates the service user if needed
- Installs Python packages in `/opt/uhrk/node-venv`
- Copies `remote/uhrkboo/zenith_node/` to `/opt/uhrk/zenith_node`
- Writes `/etc/uhrk/node.env`
- Installs `/etc/systemd/system/uhrk-node.service`
- Adds limited passwordless sudo for shutdown and clock setting
- Enables and starts `uhrk-node.service`

Check the node:

```bash
systemctl status uhrk-node
journalctl -u uhrk-node -f
```

Node flight logs are written to:

```text
/home/<node-user>/flight_logs/
```

The default generated node environment file is:

```text
/etc/uhrk/node.env
```

You can edit it, then restart:

```bash
sudo nano /etc/uhrk/node.env
sudo systemctl restart uhrk-node
```

Useful node settings:

- `UHRK_DEVICE_ID`
- `UHRK_RADIO_FREQ_MHZ`
- `UHRK_SPREADING_FACTOR`
- `UHRK_SIGNAL_BANDWIDTH`
- `UHRK_CADENCE_SECONDS`
- `UHRK_LAUNCH_READY_CADENCE_SECONDS`
- `UHRK_TX_POWER`
- event thresholds such as `UHRK_BURN_ACTIVE_ACC_THRESHOLD`

An example file is stored at:

```text
install/node.env.example
```

## Install The Ground Station

The GC software has two parts:

- UHRK backend and web dashboard
- SX1303/SX1302 packet forwarder

The installer can set up the UHRK backend/dashboard by itself. If the SX1303 HAL
has already been built, pass its root path with `--gateway-dir` and the
installer will also install the packet-forwarder service.

Example:

```bash
sudo ./install/install_gc.sh \
  --user uhrkgc \
  --gateway-dir /home/uhrkgc/gateway/sx1302_hal_rpi5-master \
  --gps-port /dev/ttyAMA0
```

If the gateway HAL is not ready yet:

```bash
sudo ./install/install_gc.sh --user uhrkgc --gps-port /dev/ttyAMA0
```

Then install/build the SX1303 packet forwarder separately and rerun with
`--gateway-dir`.

The GC installer:

- Creates the service user if needed
- Copies `remote/uhrkgc/uhrk_site/` to `/opt/uhrk/uhrk_site`
- Writes `/etc/uhrk/gc.env`
- Installs `uhrk-backend.service`
- Installs `uhrk-web.service`
- Optionally installs `lora-pkt-fwd.service`
- Adds limited passwordless sudo for shutdown and clock setting
- Enables and starts the installed services

Check the GC:

```bash
systemctl status uhrk-backend
systemctl status uhrk-web
systemctl status lora-pkt-fwd
journalctl -u uhrk-backend -f
```

Dashboard:

```text
http://<ground-station-ip>:8000/
```

GC logs are written to:

```text
/opt/uhrk/uhrk_site/flight_logs/
```

The generated GC environment file is:

```text
/etc/uhrk/gc.env
```

Edit and restart:

```bash
sudo nano /etc/uhrk/gc.env
sudo systemctl restart uhrk-backend
```

An example file is stored at:

```text
install/gc.env.example
```

## Updating An Existing Pi

On a Pi that was installed from this repository:

```bash
cd uhrk-telemetry-system
git pull
sudo ./install/install_node.sh --device-id 0 --frequency 868.1 --user uhrkboo
```

or for the GC:

```bash
cd uhrk-telemetry-system
git pull
sudo ./install/install_gc.sh --user uhrkgc --gateway-dir /path/to/sx1302_hal
```

The installers overwrite the installed source snapshot and service files but
preserve runtime logs. They regenerate `/etc/uhrk/*.env`, so copy out any custom
settings first or pass them as environment variables when running the installer.

## Current Install Limits

The repository is now much closer to clone-and-install, but a few areas still
depend on the exact Pi image and hardware:

- The SX1303/SX1302 HAL build is not vendored into this repo.
- Network/hotspot setup is not automated.
- `raspi-config` interface setup is documented rather than forced.
- Multi-frequency gateway channel configuration is not automated yet.
- Existing hand-built Pis may still use `/home/uhrkgc` and `/home/uhrkboo`
  paths until reinstalled with these scripts.

## Quick Health Checks

Node:

```bash
systemctl is-active uhrk-node
tail -f /home/<node-user>/flight_logs/node_*.jsonl
```

GC:

```bash
systemctl is-active uhrk-backend
systemctl is-active uhrk-web
curl http://localhost:8000/telemetry_latest.json
```

Packet forwarder:

```bash
systemctl is-active lora-pkt-fwd
journalctl -u lora-pkt-fwd -n 100
```
