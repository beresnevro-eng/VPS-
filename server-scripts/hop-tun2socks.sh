#!/bin/bash
set -euo pipefail
GW=$(ip -4 route show default dev eth0 | awk '/default/ {print $3; exit}')
ip rule add from 10.66.77.0/24 table 100 priority 32765 2>/dev/null || true
ip route replace 194.226.54.124/32 via "$GW" table 100
ip route replace 10.66.77.0/24 dev awg0 table 100
ip route replace 127.0.0.0/8 dev lo table 100
exec /usr/local/bin/tun2socks \
  --device=hop-tun \
  --proxy=socks5://127.0.0.1:11081 \
  --interface=eth0 \
  --loglevel=warning \
  --mtu=1280 \
  --tun-post-up="/sbin/ip link set hop-tun up; /sbin/ip addr replace 10.88.0.1/30 dev hop-tun; /sbin/ip route replace default dev hop-tun table 100"
