#!/usr/bin/env bash
# AmnesiaWG 2.0 (lite) — для VPS где уже работает Xray.
# Не трогает UFW глобально, только открывает UDP-порт AWG.
set -euo pipefail

AWG_PORT="${AWG_PORT:-51830}"
AWG_SUBNET="${AWG_SUBNET:-10.66.66.0/24}"
AWG_SERVER_IP="${AWG_SERVER_IP:-10.66.66.1}"
AWG_DIR="/root/awg"
CLIENTS_DIR="/root/amnesiawg-clients"
SERVER_CONF="/etc/amnezia/amneziawg/awg0.conf"
LOG="/root/setup-amnesiawg.log"
PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$PUBLIC_IP" ]] && PUBLIC_IP="103.75.126.216"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
die() { log "ERROR: $*"; exit 1; }

fix_dns() {
  log "Проверка DNS..."
  if getent hosts ppa.launchpadcontent.net >/dev/null 2>&1; then
    log "DNS OK"
    return 0
  fi
  log "DNS не работает — чиню (1.1.1.1 / 8.8.8.8)..."
  if [[ -x /root/fix-dns-now.sh ]]; then
    bash /root/fix-dns-now.sh >>"$LOG" 2>&1 || true
  else
    cat > /etc/resolv.conf <<'EOF'
nameserver 1.1.1.1
nameserver 8.8.8.8
EOF
  fi
  getent hosts ppa.launchpadcontent.net >/dev/null 2>&1 \
    || die "DNS не поднялся. Выполни: bash /root/fix-dns-now.sh"
}

add_amnezia_ppa() {
  local list="/etc/apt/sources.list.d/amnezia-ppa.list"
  local key="/usr/share/keyrings/amnezia-ppa.gpg"
  local codename
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-noble}")"

  if [[ -f "$list" ]] && apt-cache show amneziawg &>/dev/null 2>&1; then
    log "PPA amnezia уже подключён"
    return 0
  fi

  log "Подключаю PPA amnezia вручную (без add-apt-repository)..."
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x57290828" \
    | gpg --dearmor -o "$key" \
    || die "не удалось скачать GPG-ключ PPA (проверь DNS)"

  echo "deb [signed-by=${key}] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu ${codename} main" > "$list"
  chmod 644 "$list"
  log "PPA: $list (suite=${codename})"
}

rand_range() {
  local min=$1 max=$2 range v
  range=$((max - min + 1))
  v=$(od -An -tu4 -N4 /dev/urandom 2>/dev/null | tr -d ' ')
  [[ -z "$v" || ! "$v" =~ ^[0-9]+$ ]] && v=$(( (RANDOM << 15) | RANDOM ))
  echo $(( (v % range) + min ))
}

generate_awg_h_ranges() {
  local attempt=0 arr=() sorted _v
  while (( attempt < 20 )); do
    arr=()
    local raw count=0
    raw=$(od -An -N32 -tu4 /dev/urandom 2>/dev/null | tr -s ' \n' '\n' | sed '/^$/d')
    if [[ -n "$raw" ]]; then
      while IFS= read -r _v; do
        [[ "$_v" =~ ^[0-9]+$ ]] || continue
        arr+=("$(( _v & 2147483647 ))")
        count=$((count + 1))
        (( count == 8 )) && break
      done <<< "$raw"
    fi
    if (( ${#arr[@]} != 8 )); then
      arr=()
      for _ in 1 2 3 4 5 6 7 8; do arr+=("$(rand_range 0 2147483647)"); done
    fi
    sorted=$(printf '%s\n' "${arr[@]}" | sort -n)
    arr=(); while IFS= read -r _v; do arr+=("$_v"); done <<< "$sorted"
    if (( ${arr[1]} - ${arr[0]} >= 1000 && ${arr[3]} - ${arr[2]} >= 1000 && ${arr[5]} - ${arr[4]} >= 1000 && ${arr[7]} - ${arr[6]} >= 1000 )); then
      printf '%s-%s\n' "${arr[0]}" "${arr[1]}"
      printf '%s-%s\n' "${arr[2]}" "${arr[3]}"
      printf '%s-%s\n' "${arr[4]}" "${arr[5]}"
      printf '%s-%s\n' "${arr[6]}" "${arr[7]}"
      return 0
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

generate_awg_params() {
  # mobile preset — лучше для телефонов в РФ
  AWG_Jc=3
  AWG_Jmin=$(rand_range 30 50)
  AWG_Jmax=$(( AWG_Jmin + $(rand_range 20 80) ))
  AWG_S1=$(rand_range 15 150)
  AWG_S2=$(rand_range 15 150)
  while [[ $((AWG_S1 + 56)) -eq $AWG_S2 ]]; do AWG_S2=$(rand_range 15 150); done
  AWG_S3=$(rand_range 8 55)
  AWG_S4=$(rand_range 4 27)
  mapfile -t _h < <(generate_awg_h_ranges) || die "H1-H4 generation failed"
  AWG_H1="${_h[0]}"; AWG_H2="${_h[1]}"; AWG_H3="${_h[2]}"; AWG_H4="${_h[3]}"
  AWG_I1=" "
  log "AWG params: Jc=$AWG_Jc Jmin=$AWG_Jmin Jmax=$AWG_Jmax S1=$AWG_S1 S2=$AWG_S2"
}

genkey() {
  if command -v awg >/dev/null 2>&1; then awg genkey
  elif command -v wg >/dev/null 2>&1; then wg genkey
  else die "нет awg/wg genkey"; fi
}

pubkey() {
  if command -v awg >/dev/null 2>&1; then awg pubkey
  else wg pubkey; fi
}

install_packages() {
  fix_dns
  log "Установка AmneziaWG из PPA amnezia/ppa..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || die "apt update не прошёл — сначала: bash /root/fix-dns-now.sh"
  apt-get install -y gnupg curl ca-certificates linux-headers-"$(uname -r)" \
    || apt-get install -y gnupg curl ca-certificates linux-headers-generic

  if ! apt-cache show amneziawg &>/dev/null 2>&1; then
    add_amnezia_ppa
    apt-get update -qq || die "apt update после PPA не прошёл"
  fi

  apt-get install -y amneziawg amneziawg-tools dkms \
    || apt-get install -y amneziawg dkms \
    || apt-get install -y amneziawg
  command -v awg >/dev/null 2>&1 || die "awg не установился — см. /var/log/dkms.log"
}

setup_sysctl() {
  cat > /etc/sysctl.d/99-amnesiawg-forwarding.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.forwarding=1
EOF
  sysctl -p /etc/sysctl.d/99-amnesiawg-forwarding.conf >/dev/null
}

setup_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q active; then
    ufw allow "${AWG_PORT}/udp" comment "AmneziaWG" || true
    ufw reload || true
    log "UFW: UDP ${AWG_PORT} открыт"
  else
    log "UFW неактивен — пропуск"
  fi
}

write_client() {
  local name="$1" ip="$2" priv="$3" server_pub="$4"
  local f="${CLIENTS_DIR}/${name}.conf"
  cat > "$f" <<EOF
[Interface]
PrivateKey = ${priv}
Address = ${ip}/32
DNS = 1.1.1.1, 8.8.8.8
MTU = 1280
Jc = ${AWG_Jc}
Jmin = ${AWG_Jmin}
Jmax = ${AWG_Jmax}
S1 = ${AWG_S1}
S2 = ${AWG_S2}
S3 = ${AWG_S3}
S4 = ${AWG_S4}
H1 = ${AWG_H1}
H2 = ${AWG_H2}
H3 = ${AWG_H3}
H4 = ${AWG_H4}
I1 = ${AWG_I1}

[Peer]
PublicKey = ${server_pub}
Endpoint = ${PUBLIC_IP}:${AWG_PORT}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF
  chmod 600 "$f"
  log "Клиент: $f"
}

main() {
  log "=== AmnesiaWG lite install (UDP ${AWG_PORT}) ==="
  [[ "$(id -u)" -eq 0 ]] || die "запускай от root"

  if [[ -f "$SERVER_CONF" ]] && awg show awg0 &>/dev/null; then
    log "AmnesiaWG уже работает. Клиенты в ${CLIENTS_DIR}/"
    awg show awg0 || true
    exit 0
  fi

  install_packages
  setup_sysctl
  generate_awg_params

  mkdir -p "$AWG_DIR" "$CLIENTS_DIR" /etc/amnezia/amneziawg
  chmod 700 "$AWG_DIR" /etc/amnezia/amneziawg

  if [[ ! -f "$AWG_DIR/server_private.key" ]]; then
    genkey | tee "$AWG_DIR/server_private.key" | pubkey > "$AWG_DIR/server_public.key"
    chmod 600 "$AWG_DIR/server_private.key" "$AWG_DIR/server_public.key"
  fi
  local server_priv server_pub nic
  server_priv=$(cat "$AWG_DIR/server_private.key")
  server_pub=$(cat "$AWG_DIR/server_public.key")
  nic=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  [[ -n "$nic" ]] || nic=eth0

  cat > "$SERVER_CONF" <<EOF
[Interface]
PrivateKey = ${server_priv}
Address = ${AWG_SERVER_IP}/24
ListenPort = ${AWG_PORT}
MTU = 1280
PostUp = iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o ${nic} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o ${nic} -j MASQUERADE
Jc = ${AWG_Jc}
Jmin = ${AWG_Jmin}
Jmax = ${AWG_Jmax}
S1 = ${AWG_S1}
S2 = ${AWG_S2}
S3 = ${AWG_S3}
S4 = ${AWG_S4}
H1 = ${AWG_H1}
H2 = ${AWG_H2}
H3 = ${AWG_H3}
H4 = ${AWG_H4}
I1 = ${AWG_I1}
EOF
  chmod 600 "$SERVER_CONF"

  # peers + client configs
  local ip_base=2
  for name in iphone router macbook; do
    local cpriv cpub peer_ip
    cpriv=$(genkey)
    cpub=$(echo "$cpriv" | pubkey)
    peer_ip="10.66.66.${ip_base}"
    ip_base=$((ip_base + 1))
    cat >> "$SERVER_CONF" <<EOF

[Peer]
# ${name}
PublicKey = ${cpub}
AllowedIPs = ${peer_ip}/32
EOF
    write_client "$name" "$peer_ip" "$cpriv" "$server_pub"
    echo "${name}:${peer_ip}" >> "$AWG_DIR/peers.txt"
  done

  # save params for regen
  cat > "$AWG_DIR/awg_params.env" <<EOF
AWG_Jc=${AWG_Jc}
AWG_Jmin=${AWG_Jmin}
AWG_Jmax=${AWG_Jmax}
AWG_S1=${AWG_S1}
AWG_S2=${AWG_S2}
AWG_S3=${AWG_S3}
AWG_S4=${AWG_S4}
AWG_H1=${AWG_H1}
AWG_H2=${AWG_H2}
AWG_H3=${AWG_H3}
AWG_H4=${AWG_H4}
AWG_PORT=${AWG_PORT}
PUBLIC_IP=${PUBLIC_IP}
EOF
  chmod 600 "$AWG_DIR/awg_params.env"

  setup_firewall

  systemctl enable awg-quick@awg0
  systemctl restart awg-quick@awg0
  sleep 2
  systemctl is-active awg-quick@awg0 || die "awg-quick@awg0 не запустился — см. journalctl -u awg-quick@awg0"

  cat > /root/amnesiawg-info.txt <<EOF
AmneziaWG 2.0 — ${PUBLIC_IP}
UDP порт: ${AWG_PORT}
Серверный конфиг: ${SERVER_CONF}
Клиенты: ${CLIENTS_DIR}/

Скачать конфиг iPhone:
  scp root@${PUBLIC_IP}:${CLIENTS_DIR}/iphone.conf .

Приложение: AmneziaVPN (импорт файла .conf) или AmneziaWG.

Проверка:
  awg show awg0
  ss -ulnp | grep ${AWG_PORT}
  systemctl status awg-quick@awg0
EOF

  log "=== Готово ==="
  awg show awg0 || true
  cat /root/amnesiawg-info.txt
}

main "$@"
