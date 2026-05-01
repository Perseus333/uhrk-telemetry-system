#!/usr/bin/env bash
set -u

sudo systemctl stop uhrk-node.service
sleep 1

echo "DEVICE"
ls -l /dev/serial0 /dev/ttyS0 2>/dev/null || true
stty -F /dev/serial0 9600 raw -echo 2>/dev/null || true

echo "RAW_NMEA_START"
timeout 10 cat /dev/serial0 | head -20
echo "RAW_NMEA_END"

echo "GPS_READER_START"
cd /home/uhrkboo
/home/uhrkboo/env/bin/python - <<'PY'
import time
from zenith_node.gps_reader import GPSReader

reader = GPSReader()
try:
    for i in range(12):
        time.sleep(1)
        data = reader.read()
        print(
            i,
            f"lat={data.lat}",
            f"lon={data.lon}",
            f"alt={data.alt_m}",
            f"status={data.status}",
            f"sats={data.sats}",
            flush=True,
        )
finally:
    reader.close()
PY
echo "GPS_READER_END"

sudo systemctl start uhrk-node.service
sleep 5
systemctl is-active uhrk-node.service
