#!/usr/bin/env bash
set -u

sudo systemctl stop lora-pkt-fwd.service
sleep 1

for dev in /dev/ttyAMA0 /dev/ttyAMA10 /dev/serial0; do
  for baud in 9600 38400 115200; do
    echo "TEST ${dev} ${baud}"
    stty -F "${dev}" "${baud}" raw -echo 2>/dev/null || true
    timeout 4 cat "${dev}" | head -5
  done
done

sudo systemctl start lora-pkt-fwd.service
sleep 5
systemctl is-active lora-pkt-fwd.service uhrk-backend.service uhrk-web.service
