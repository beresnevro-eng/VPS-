# Two-hop: RU entry → Fornex exit

Схема для телефона, когда прямой Reality на Fornex из РФ мёртв, а RU→Fornex TCP/ICMP тоже режется.

```
телефон ──VLESS+Reality TCP:443 ──┐
         ──Hysteria2 UDP:8444 ────┼──► RU entry (194.226.54.124)
         ──AmneziaWG UDP:51830 ───┘
                                  │
                                  │ SOCKS / policy-route через reverse WireGuard
                                  ▼
                               Fornex (10.99.0.2:11081 microsocks) ──► интернет
                                  ▲
                                  │ WG dial (Fornex → entry:51820)
                               Fornex wg-hop 10.99.0.2
```

- **VLESS Reality** — Wi‑Fi / регионы, где TCP Reality проходит (напр. МТС Челябинск).
- **Hysteria2** — LTE МТС Москва и другие сети, где Reality рвётся после handshake.
- **AmneziaWG** — LTE МТС Москва; трафик clients `10.66.77.0/24` уходит в Fornex (не в RU eth0).

Reverse WG нужен потому что **entry не достучится до Fornex**, а Fornex→entry UDP 51820 проходит.

## Адреса

| Узел | Роль | IP |
|------|------|-----|
| RU entry | Reality/HY2/AWG inbound + WG listen | `194.226.54.124` · WG `10.99.0.1` |
| Fornex | WG dial + SOCKS exit + старый exit | `103.75.126.216` · WG `10.99.0.2` |

## Сервисы

| Где | Что |
|-----|-----|
| entry | `xray` на TCP `:443` Reality (SNI microsoft) → SOCKS `10.99.0.2:11081` |
| entry | `hysteria-server` на UDP `:8444` (salamander) → SOCKS `10.99.0.2:11081` |
| entry | `awg-quick@awg0` UDP `:51830` → policy route table 100 via `wg-hop` |
| entry | `wg-quick@wg-hop` ListenPort `51820`, `Table=off`, `AllowedIPs=0.0.0.0/0` |
| Fornex | `wg-quick@wg-hop` Endpoint `194.226.54.124:51820` |
| Fornex | `hop-socks` / `microsocks` на `10.99.0.2:11081` |
| Fornex | `hop-forward-nat` — MASQUERADE `10.99.0.0/24` и `10.66.77.0/24` |

## Скорость и Instagram

Замеры с RU entry (Fritato AS219061, Москва), стабильно:

| Путь | Скорость |
|------|----------|
| Entry → Cloudflare напрямую | ~0.9 Mbps |
| Entry → Fornex по WG (iperf -R) | ~0.9 Mbps |
| SOCKS через hop → Instagram HTML | HTTP 200, ~0.7–0.9 Mbps |
| Fornex напрямую → Cloudflare | ~100–200 Mbps |

Потолок **~1 Mbps на всём hop** — не конфиг VPN и не «тариф 1 Mbps» в маркетинге Fornex. Узкое место: **канал самого RU entry** (provider soft-cap / peering). Для ленты/чатов хватает; для Reels/Stories часто не хватает — буфер не успевает. «У жены иногда простирается» = короткий ролик / краткий всплеск / другой профиль.

Видео смотреть стабильно через hop нельзя, пока entry не даст десятки Mbps. Варианты: другой RU VPS с реальной скоростью или прямой Fornex-профиль, когда оператор пускает UDP/TCP до `103.75.126.216`.

## Ссылки для телефона

На entry:

| Файл | Профиль | Когда |
|------|---------|--------|
| `/root/phone-link.txt` | VLESS **RU-entry** | Wi‑Fi / Челябинск |
| `/root/phone-hy2-link.txt` | Hysteria2 **RU-entry-HY2** | LTE МТС Москва и т.п. |
| `/root/amnesiawg-clients/*.conf` | AmneziaWG | LTE МТС Москва (AmneziaVPN) |

На Fornex бот читает рабочие ключи из `/root/hop-vpn-links.txt` (подписка `/sub`).
Amnezia: `/awg iphone` · `/awg wife`.

Импорт в Streisand / Hiddify — подписка URL (VLESS+HY2). Amnezia — отдельным приложением.
VLESS / HY2 / AWG выход: `103.75.126.216`.

## Проверка

```bash
# Fornex
wg show wg-hop
ping -c2 10.99.0.1
ss -tlnp | grep 11081
bash /root/hop-status.sh

# Entry
wg show wg-hop
ping -c2 10.99.0.2
curl -sS --max-time 10 --socks5-hostname 10.99.0.2:11081 https://ifconfig.me
curl -sS --max-time 12 --interface 10.66.77.1 https://ifconfig.me   # должен быть Fornex
systemctl is-active xray hysteria-server wg-quick@wg-hop awg-quick@awg0
ss -ulnp | grep -E '8444|51830'
```

Ожидаемый exit IP: `103.75.126.216`.

## Перезапуск

```bash
# Entry
systemctl restart wg-quick@wg-hop awg-quick@awg0 xray hysteria-server

# Fornex
systemctl restart wg-quick@wg-hop hop-socks hop-forward-nat
```

UFW на entry: `22/tcp`, `443/tcp`, `8444/udp`, `51820/udp`, `51830/udp`.

## Важно

- Секреты WG и Reality keys только на серверах (`/etc/wireguard/`, xray config) — не в git.
- На entry у `wg-hop` обязательно `Table = off` + ручной `PostUp` маршрут `10.99.0.0/24`, иначе `AllowedIPs=0.0.0.0/0` утащит default route и убьёт SSH.
- После смены ключей WG — обновить peer public key на обеих сторонах и `systemctl restart wg-quick@wg-hop`.
