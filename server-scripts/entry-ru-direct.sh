#!/bin/bash
# RuVDS entry: российский трафик напрямую с eth0, остальное — через Fornex hop.
# Применять на entry (195.133.146.140), не на Fornex.
set -euo pipefail

XRAY_CFG="${XRAY_CFG:-/usr/local/etc/xray/config.json}"
HY2_CFG="${HY2_CFG:-/etc/hysteria/config.yaml}"
AWG_CFG="${AWG_CFG:-/etc/amnezia/amneziawg/awg0.conf}"
RU_CIDR_URL="${RU_CIDR_URL:-https://www.ipdeny.com/ipblocks/data/countries/ru.zone}"
RU_CIDR_URL_FALLBACK="${RU_CIDR_URL_FALLBACK:-https://raw.githubusercontent.com/ipverse/rir-ip/master/country/ru/ipv4-aggregated.txt}"
RU_CIDR_FILE="${RU_CIDR_FILE:-/var/lib/ru-direct/ru.cidrs}"
IPSET_NAME="${IPSET_NAME:-ru_direct}"
MARK="${MARK:-0x64}"
TABLE_DIRECT="${TABLE_DIRECT:-101}"
ETH_IF="${ETH_IF:-eth0}"
AWG_IF="${AWG_IF:-awg0}"
AWG_NET="${AWG_NET:-10.66.77.0/24}"

need_root() {
  [[ $(id -u) -eq 0 ]] || { echo "нужен root"; exit 1; }
}

backup() {
  local f=$1
  [[ -f "$f" ]] || return 0
  cp -a "$f" "${f}.bak.ru-direct.$(date +%s)"
}

eth_gw() {
  ip -4 route show default dev "$ETH_IF" | awk '/default/ {print $3; exit}'
}

install_deps() {
  if ! command -v ipset >/dev/null 2>&1; then
    # не трогаем linux-headers/dkms — только ipset
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ipset 2>/dev/null \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o Dpkg::Options::="--force-confdef" \
           -o Dpkg::Options::="--force-confold" --no-install-recommends ipset
  fi
  command -v ipset >/dev/null 2>&1 || { echo "FAIL: ipset не установлен"; exit 1; }
  mkdir -p "$(dirname "$RU_CIDR_FILE")"
}

parse_cidrs() {
  local src=$1
  awk '
    /^#/ {next}
    /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/[0-9]+$/ {print; next}
    /^route / {print $2; next}
    /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ {print $1"/32"; next}
  ' "$src" | sort -u
}

fetch_ru_cidrs() {
  echo ">>> скачиваю RU CIDR"
  local tmp
  tmp=$(mktemp)
  if curl -fsSL --max-time 60 "$RU_CIDR_URL" -o "$tmp"; then
    parse_cidrs "$tmp" > "$RU_CIDR_FILE"
  else
    echo "WARN: primary CIDR URL fail, fallback"
    curl -fsSL --max-time 60 "$RU_CIDR_URL_FALLBACK" -o "$tmp"
    parse_cidrs "$tmp" > "$RU_CIDR_FILE"
  fi
  rm -f "$tmp"
  local n
  n=$(wc -l < "$RU_CIDR_FILE")
  echo "OK  RU CIDR: $n префиксов → $RU_CIDR_FILE"
  [[ "$n" -gt 100 ]] || { echo "FAIL слишком мало CIDR ($n)"; exit 1; }
}

load_ipset() {
  echo ">>> ipset $IPSET_NAME"
  ipset create "$IPSET_NAME" hash:net family inet maxelem 200000 -exist
  ipset flush "$IPSET_NAME"
  # batch load
  {
    echo "create $IPSET_NAME hash:net family inet maxelem 200000 -exist"
    while read -r cidr; do
      [[ -n "$cidr" ]] || continue
      echo "add $IPSET_NAME $cidr -exist"
    done < "$RU_CIDR_FILE"
  } | ipset restore -!
  echo "OK  ipset entries: $(ipset list "$IPSET_NAME" | awk '/Number of entries/ {print $4}')"
}

setup_awg_direct() {
  local gw
  gw=$(eth_gw)
  [[ -n "$gw" ]] || { echo "FAIL нет gateway на $ETH_IF"; exit 1; }
  echo ">>> AWG policy: RU → $ETH_IF via $gw (table $TABLE_DIRECT, mark $MARK)"

  # mark RU destinations from AWG clients
  iptables -t mangle -C PREROUTING -i "$AWG_IF" -m set --match-set "$IPSET_NAME" dst -j MARK --set-mark "$MARK" 2>/dev/null \
    || iptables -t mangle -A PREROUTING -i "$AWG_IF" -m set --match-set "$IPSET_NAME" dst -j MARK --set-mark "$MARK"

  # direct table
  ip route replace default via "$gw" dev "$ETH_IF" table "$TABLE_DIRECT"
  ip route replace "$AWG_NET" dev "$AWG_IF" table "$TABLE_DIRECT" 2>/dev/null || true

  # fwmark rule (priority выше table 100 = 32765)
  ip rule del fwmark "$MARK" table "$TABLE_DIRECT" 2>/dev/null || true
  ip rule add fwmark "$MARK" table "$TABLE_DIRECT" priority 100

  # forward + NAT на eth0 для прямого RU
  iptables -C FORWARD -i "$AWG_IF" -o "$ETH_IF" -j ACCEPT 2>/dev/null \
    || iptables -I FORWARD 1 -i "$AWG_IF" -o "$ETH_IF" -j ACCEPT
  iptables -C FORWARD -i "$ETH_IF" -o "$AWG_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
    || iptables -I FORWARD 1 -i "$ETH_IF" -o "$AWG_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  iptables -t nat -C POSTROUTING -s "$AWG_NET" -o "$ETH_IF" -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -s "$AWG_NET" -o "$ETH_IF" -j MASQUERADE

  echo "OK  AWG RU-direct rules"
}

patch_xray() {
  echo ">>> Xray routing geoip:ru + geosite RU"
  backup "$XRAY_CFG"
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("/usr/local/etc/xray/config.json")
cfg = json.loads(p.read_text())
# ensure outbounds
tags = {o.get("tag") for o in cfg.get("outbounds") or []}
if "direct" not in tags:
    cfg.setdefault("outbounds", []).append({"tag": "direct", "protocol": "freedom", "settings": {}})
if "fornex" not in tags:
    raise SystemExit("нет outbound fornex в xray config")

ru_domains = [
    "geosite:category-ru",
    "geosite:yandex",
    "geosite:vk",
    "geosite:mailru",
]

# банки / маркетплейсы / госуслуги (домены важны: CDN ≠ geoip:ru)
extra = [
    "domain:sberbank.ru", "domain:sber.ru", "domain:sberbank.com",
    "domain:tbank.ru", "domain:tinkoff.ru", "domain:tinkoff.com",
    "domain:vtb.ru", "domain:alfabank.ru", "domain:raiffeisen.ru",
    "domain:gazprombank.ru", "domain:open.ru", "domain:psbank.ru",
    "domain:ozon.ru", "domain:ozon.com", "domain:wildberries.ru", "domain:wb.ru",
    "domain:avito.ru", "domain:lamoda.ru", "domain:megamarket.ru",
    "domain:dns-shop.ru", "domain:mvideo.ru", "domain:citilink.ru",
    "domain:gosuslugi.ru", "domain:nalog.ru", "domain:mos.ru", "domain:cbr.ru",
    "domain:hh.ru", "domain:2gis.ru", "domain:rzd.ru", "domain:pochta.ru",
    "domain:vk.com", "domain:vk.ru", "domain:ok.ru", "domain:max.ru",
    "domain:yandex.ru", "domain:yandex.com", "domain:ya.ru", "domain:dzen.ru",
    "domain:mail.ru", "domain:mediascope.net",
]

cfg["routing"] = {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
        {"type": "field", "domain": ru_domains + extra, "outboundTag": "direct"},
        {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "fornex"},
    ],
}
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
print("OK  xray routing updated")
PY
  /usr/local/bin/xray -test -config "$XRAY_CFG"
  systemctl restart xray
  systemctl is-active xray
}

patch_hysteria() {
  echo ">>> Hysteria2 ACL geoip:ru + домены"
  backup "$HY2_CFG"
  python3 - <<'PY'
from pathlib import Path
p = Path("/etc/hysteria/config.yaml")
text = p.read_text()
# заменяем блок acl целиком — проще и надёжнее
acl = '''
acl:
  inline:
    - direct(geoip:private)
    - direct(geosite:category-ru)
    - direct(geosite:yandex)
    - direct(geosite:vk)
    - direct(geosite:mailru)
    - direct(suffix:sberbank.ru)
    - direct(suffix:sber.ru)
    - direct(suffix:tbank.ru)
    - direct(suffix:tinkoff.ru)
    - direct(suffix:vtb.ru)
    - direct(suffix:alfabank.ru)
    - direct(suffix:ozon.ru)
    - direct(suffix:wildberries.ru)
    - direct(suffix:wb.ru)
    - direct(suffix:avito.ru)
    - direct(suffix:lamoda.ru)
    - direct(suffix:megamarket.ru)
    - direct(suffix:gosuslugi.ru)
    - direct(suffix:nalog.ru)
    - direct(suffix:mos.ru)
    - direct(suffix:hh.ru)
    - direct(suffix:vk.com)
    - direct(suffix:ok.ru)
    - direct(suffix:yandex.ru)
    - direct(suffix:ya.ru)
    - direct(suffix:mail.ru)
    - direct(geoip:ru)
    - fornex(all)
'''
import re
if re.search(r'^acl:\s*$', text, re.M) or re.search(r'^acl:', text, re.M):
    text = re.sub(r'\nacl:[\s\S]*?(?=\n[a-zA-Z]|\Z)', '\n' + acl.strip() + '\n', text, count=1)
else:
    text = text.rstrip() + '\n\n' + acl.strip() + '\n'
p.write_text(text)
print("OK  hysteria acl updated")
print(p.read_text()[-800:])
PY
  systemctl restart hysteria-server
  sleep 1
  systemctl is-active hysteria-server
}

install_systemd_refresh() {
  # периодическое обновление RU CIDR + ipset
  cat > /etc/systemd/system/ru-direct-refresh.service <<'UNIT'
[Unit]
Description=Refresh RU direct ipset for AWG split
After=network-online.target

[Service]
Type=oneshot
ExecStart=/root/entry-ru-direct.sh refresh-ipset
UNIT
  cat > /etc/systemd/system/ru-direct-refresh.timer <<'UNIT'
[Unit]
Description=Weekly RU CIDR refresh

[Timer]
OnCalendar=Sun *-*-* 04:20:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  # copy self to /root if needed
  if [[ "${BASH_SOURCE[0]}" != /root/entry-ru-direct.sh ]]; then
    cp -a "${BASH_SOURCE[0]}" /root/entry-ru-direct.sh
    chmod +x /root/entry-ru-direct.sh
  fi
  systemctl daemon-reload
  systemctl enable --now ru-direct-refresh.timer
  echo "OK  timer ru-direct-refresh"
}

persist_awg_postup() {
  # чтобы после рестарта awg правила mark/NAT eth0 поднимались
  [[ -f "$AWG_CFG" ]] || { echo "WARN нет $AWG_CFG"; return 0; }
  backup "$AWG_CFG"
  if grep -q 'entry-ru-direct.sh apply-awg' "$AWG_CFG"; then
    echo "OK  AWG PostUp уже вызывает ru-direct"
    return 0
  fi
  # добавим в конец PostUp вызов apply-awg (мягко)
  python3 - <<'PY'
from pathlib import Path
p = Path("/etc/amnezia/amneziawg/awg0.conf")
if not p.exists():
    p = Path("/etc/wireguard/awg0.conf")
text = p.read_text()
hook = " ; /root/entry-ru-direct.sh apply-awg || true"
lines = text.splitlines()
out = []
for line in lines:
    if line.startswith("PostUp =") and "entry-ru-direct.sh apply-awg" not in line:
        line = line + hook
    out.append(line)
p.write_text("\n".join(out) + "\n")
print("OK  patched AWG PostUp")
PY
}

verify() {
  echo ">>> verify"
  echo -n "direct exit: "; curl -4 -sS --max-time 8 https://ifconfig.me; echo
  echo -n "hop exit: "; curl -4 -sS --max-time 10 --socks5-hostname 10.99.0.2:11081 https://ifconfig.me; echo
  # yandex IP should be RU and reachable direct
  python3 - <<'PY'
import socket, subprocess
for host in ("ya.ru", "sberbank.ru", "ozon.ru"):
    try:
        ip = socket.gethostbyname(host)
    except Exception as e:
        print(host, "dns fail", e); continue
    r = subprocess.run(["ipset", "test", "ru_direct", ip], capture_output=True, text=True)
    print(f"{host} -> {ip} in_ru_ipset={'yes' if r.returncode==0 else 'no'}")
PY
  ip rule show | grep -E "fwmark|lookup 100|lookup 101" || true
  systemctl is-active xray hysteria-server awg-quick@awg0 wg-quick@wg-hop
}

cmd=${1:-apply}

need_root
case "$cmd" in
  apply)
    install_deps
    fetch_ru_cidrs
    load_ipset
    setup_awg_direct
    patch_xray
    patch_hysteria
    persist_awg_postup
    install_systemd_refresh
    verify
    echo "RESULT: RU-direct applied"
    ;;
  refresh-ipset)
    install_deps
    fetch_ru_cidrs
    load_ipset
    setup_awg_direct
    ;;
  apply-awg)
    # вызывается из AWG PostUp — ipset уже должен быть
    if ! ipset list "$IPSET_NAME" >/dev/null 2>&1; then
      install_deps
      [[ -f "$RU_CIDR_FILE" ]] || fetch_ru_cidrs
      load_ipset
    fi
    setup_awg_direct
    ;;
  verify)
    verify
    ;;
  *)
    echo "usage: $0 {apply|refresh-ipset|apply-awg|verify}"
    exit 2
    ;;
esac
