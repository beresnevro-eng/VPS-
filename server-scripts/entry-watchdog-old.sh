#!/bin/bash
set -uo pipefail
EXPECTED=103.75.126.216
LOG=/var/log/entry-watchdog.log
log(){ echo "$(date -Is) $*" | tee -a "$LOG"; }
ok=0
ip=$(curl -4 -sS --max-time 12 --socks5-hostname 10.99.0.2:11081 https://ifconfig.me 2>/dev/null || true)
if [[ "$ip" == "$EXPECTED" ]]; then
  log "OK wg-socks $ip"; ok=1
else
  log "WARN wg-socks '${ip:-none}'; restart wg-hop"
  systemctl restart wg-quick@wg-hop; sleep 3
  ip route replace 10.99.0.0/24 dev wg-hop 2>/dev/null || true
  ip route replace 10.99.0.2/32 dev wg-hop 2>/dev/null || true
fi
ip2=$(curl -4 -sS --max-time 12 --socks5-hostname 127.0.0.1:11081 https://ifconfig.me 2>/dev/null || true)
if [[ "$ip2" == "$EXPECTED" ]]; then
  log "OK sshR-socks $ip2"
  if [[ "$ok" -eq 0 ]]; then
    log "FALLBACK outbounds -> 127.0.0.1:11081"
    python3 - <<'PY'
import json
from pathlib import Path
p=Path("/usr/local/etc/xray/config.json"); cfg=json.loads(p.read_text())
for o in cfg.get("outbounds") or []:
  if o.get("tag")=="fornex":
    o["protocol"]="socks"; o["settings"]={"servers":[{"address":"127.0.0.1","port":11081}]}
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+"\n")
t=Path("/etc/hysteria/config.yaml").read_text().replace("addr: 10.99.0.2:11081","addr: 127.0.0.1:11081")
Path("/etc/hysteria/config.yaml").write_text(t)
PY
    systemctl restart xray hysteria-server
  fi
else
  log "FAIL both exit paths"
fi
if [[ "$ok" -eq 1 ]] && grep -q "addr: 127.0.0.1:11081" /etc/hysteria/config.yaml 2>/dev/null; then
  log "RECOVER outbounds -> 10.99.0.2:11081"
  python3 - <<'PY'
import json
from pathlib import Path
p=Path("/usr/local/etc/xray/config.json"); cfg=json.loads(p.read_text())
for o in cfg.get("outbounds") or []:
  if o.get("tag")=="fornex":
    o["protocol"]="socks"; o["settings"]={"servers":[{"address":"10.99.0.2","port":11081}]}
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+"\n")
t=Path("/etc/hysteria/config.yaml").read_text().replace("addr: 127.0.0.1:11081","addr: 10.99.0.2:11081")
Path("/etc/hysteria/config.yaml").write_text(t)
PY
  systemctl restart xray hysteria-server
fi
if ! ip route show table 100 | grep -q "^default via 10.99.0.2 dev wg-hop"; then
  log "restore awg table 100 via wg-hop"
  ip route replace default via 10.99.0.2 dev wg-hop table 100 || true
  ip route replace 10.66.77.0/24 dev awg0 table 100 || true
fi
for s in xray hysteria-server wg-quick@wg-hop awg-quick@awg0; do
  systemctl is-active --quiet "$s" || { log "restart $s"; systemctl restart "$s"; }
done
