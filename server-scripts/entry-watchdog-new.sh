#!/bin/bash
set -uo pipefail
EXPECTED=103.75.126.216
LOG=/var/log/entry-watchdog.log
log(){ echo "$(date -Is) $*" >> "$LOG"; }
ip=$(curl -4 -sS --max-time 12 --socks5-hostname 10.77.0.1:11083 https://ifconfig.me 2>/dev/null || true)
if [[ "$ip" == "$EXPECTED" ]]; then
  log "OK wg-ru socks $ip"
else
  log "WARN socks ${ip:-none}; restart wg-ru and proxies"
  systemctl restart wg-quick@wg-ru
  sleep 2
  systemctl restart xray hysteria-server || true
  systemctl restart hop-tun2socks || true
fi
if ! ip route show table 100 | grep -q "^default dev hop-tun"; then
  log "restore awg table 100 via hop-tun"
  systemctl restart hop-tun2socks || true
fi
pid=$(systemctl show -p MainPID --value hop-tun2socks 2>/dev/null || echo 0)
if [[ "$pid" != "0" && -r "/proc/$pid/cmdline" ]] && ! grep -qzF -- "127.0.0.1:11085" "/proc/$pid/cmdline"; then
  log "tun2socks not on exit-split; restart"
  systemctl restart hop-tun2socks || true
fi
for s in xray hysteria-server wg-quick@wg-ru awg-quick@awg0 hop-tun2socks; do
  systemctl is-active --quiet "$s" || systemctl restart "$s" || true
done
