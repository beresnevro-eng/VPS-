#!/usr/bin/env bash
# Быстрый фикс DNS на VPS (когда apt/curl не резолвят домены)
set -euo pipefail

echo "[*] Чиню DNS..."

# systemd-resolved
if [[ -d /run/systemd ]]; then
  mkdir -p /etc/systemd/resolved.conf.d
  cat > /etc/systemd/resolved.conf.d/99-dns.conf <<'EOF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9
Domains=~.
EOF
  systemctl restart systemd-resolved 2>/dev/null || true
fi

# Прямой resolv.conf (если не symlink или после resolved)
if [[ -L /etc/resolv.conf ]]; then
  ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf 2>/dev/null || true
fi

cat > /etc/resolv.conf <<'EOF'
nameserver 1.1.1.1
nameserver 8.8.8.8
options edns0
EOF

echo "[*] Проверка..."
if getent hosts mirror.fornex.org >/dev/null 2>&1; then
  echo "[+] DNS OK: mirror.fornex.org -> $(getent hosts mirror.fornex.org | awk '{print $1}')"
elif getent hosts google.com >/dev/null 2>&1; then
  echo "[+] DNS OK: google.com"
else
  echo "[!] DNS всё ещё не работает. Проверь сеть VPS в панели Fornex."
  exit 1
fi

echo "[+] Готово. Запусти: bash /root/setup-amnesiawg-lite.sh"
