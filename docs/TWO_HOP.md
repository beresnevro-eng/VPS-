# Two-hop: RU entry → Fornex exit

Схема для телефона, когда прямой Reality на Fornex из РФ мёртв, а RU→Fornex TCP/ICMP тоже режется.

```
телефон ──VLESS+Reality──► RU entry (194.226.54.124:443)
                              │
                              │ SOCKS через reverse WireGuard
                              ▼
                           Fornex (10.99.0.2:11080) ──freedom──► интернет
                              ▲
                              │ WG dial (Fornex → entry:51820)
                           Fornex wg-hop 10.99.0.2
```

Reverse WG нужен потому что **entry не достучится до Fornex**, а Fornex→entry UDP 51820 проходит.

## Адреса

| Узел | Роль | IP |
|------|------|-----|
| RU entry | Reality inbound + WG listen | `194.226.54.124` · WG `10.99.0.1` |
| Fornex | WG dial + SOCKS exit + старый exit | `103.75.126.216` · WG `10.99.0.2` |

## Сервисы

| Где | Что |
|-----|-----|
| entry | `xray` на `:443` Reality (SNI microsoft) |
| entry | `wg-quick@wg-hop` ListenPort `51820` |
| Fornex | `wg-quick@wg-hop` Endpoint `194.226.54.124:51820` |
| Fornex | Xray inbound `hop-socks` → `10.99.0.2:11080` |
| entry | Xray outbound `fornex` → SOCKS `10.99.0.2:11080` |

## Ссылка для телефона

На entry: `/root/phone-link.txt` (клиент `phone@entry.local`).

Импорт в Streisand / Hiddify / v2rayNG — один профиль **RU-entry**. Выходной IP должен быть `103.75.126.216`.

## Проверка

```bash
# Fornex
wg show wg-hop
ping -c2 10.99.0.1
ss -tlnp | grep 11080
bash /root/hop-status.sh

# Entry
wg show wg-hop
ping -c2 10.99.0.2
curl -sS --max-time 10 --socks5-hostname 10.99.0.2:11080 https://ifconfig.me
systemctl is-active xray wg-quick@wg-hop
```

Ожидаемый exit IP через SOCKS: `103.75.126.216`.

## Перезапуск

```bash
# Entry
systemctl restart wg-quick@wg-hop xray

# Fornex
systemctl restart wg-quick@wg-hop xray
```

UFW на entry: `22/tcp`, `443/tcp`, `51820/udp`.

## Важно

- Секреты WG и Reality keys только на серверах (`/etc/wireguard/`, xray config) — не в git.
- Клиент `hop@xray.local` на Fornex `:443` оставлен, но entry его больше не использует (путь через SOCKS).
- После смены ключей WG — обновить peer public key на обеих сторонах и `systemctl restart wg-quick@wg-hop`.
