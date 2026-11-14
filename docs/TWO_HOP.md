# Two-hop: RU entry → Fornex exit

Схема для телефона, когда прямой Reality на Fornex из РФ мёртв, а RU→Fornex TCP/ICMP тоже режется.

```
телефон ──VLESS+Reality TCP:443 ──┐
         ──Hysteria2 UDP:8444 ────┼──► RuVDS entry (195.133.146.140)
         ──AmneziaWG UDP:51830 ───┘
                                  │
                    ┌─────────────┴─────────────┐
                    │ geoip:ru / банки / маркеты │
                    ▼                           ▼
             eth0 напрямую              SOCKS hop → Fornex
             (RU IP RuVDS)              (103.75.126.216)
```

- **VLESS Reality + XHTTP** — выгрузка на `:443`, загрузка на `:2083` того же entry (разные сокеты, одна сессия). Отпечаток uTLS `randomized`, путь `/api/v1/events`, режим `packet-up`. Padding `100-1000`, интервал постов `20-80` мс, срок соединения `90-240` с. Заголовки gRPC/SSE выключены. Старый TCP Reality с Vision — `:2053` (`RuVDS-tcp`). IPv6 на entry выключен: глобального адреса нет, иначе фон уходит мимо туннеля. В Amnezia `AllowedIPs` включает `::/0`, чтобы телефон не выпускал IPv6 напрямую. После смены обновить подписку и заново импортировать `/awg`.
- **Hysteria2** — LTE МТС Москва и другие сети, где Reality рвётся после handshake.
- **AmneziaWG** — LTE МТС Москва; foreign → Fornex (`table 100`), **RU → eth0** (`fwmark` + `ipset ru_direct`).

Reverse WG нужен потому что **entry не достучится до Fornex**, а Fornex→entry UDP 51820 проходит.

## Split: российский трафик напрямую

На entry скрипт `/root/entry-ru-direct.sh` (зеркало в `server-scripts/entry-ru-direct.sh`):

| Протокол | RU (банки, маркетплейсы, geoip:ru) | Остальное |
|----------|-------------------------------------|-----------|
| Xray | outbound `direct` (geosite/domain/geoip) | SOCKS Fornex |
| Hysteria2 | ACL `direct(...)` | `fornex(all)` |
| AmneziaWG | ipset `ru_direct` → table 101 → eth0 | table 100 → wg-hop |

Переприменить: `bash /root/entry-ru-direct.sh apply` · проверка: `... verify`.

## Адреса

| Узел | Роль | IP |
|------|------|-----|
| RuVDS entry | Reality/HY2/AWG + WG listen | `195.133.146.140` · WG `10.99.0.1` · host `ruvds-u6jt0` |
| RuVDS-old | standby (снят с hop) | `194.226.54.124` |
| Fornex | WG dial + SOCKS exit | `103.75.126.216` · WG `10.99.0.2` |

## Сервисы

| Где | Что |
|-----|-----|
| entry | `xray` TCP `:443` Reality → **split** direct/RU + SOCKS hop |
| entry | `hysteria-server` UDP `:8444` → **split** ACL |
| entry | `awg-quick@awg0` UDP `:51830` → table 100 hop + table 101 RU-direct |
| entry | `wg-quick@wg-hop` ListenPort `51820`, `Table=off` |
| entry | `ru-direct-refresh.timer` — еженедельное обновление RU CIDR |
| Fornex | `wg-quick@wg-hop` Endpoint `195.133.146.140:51820` |
| Fornex | `hop-socks` / `microsocks` на `10.99.0.2:11081` |
| Fornex | `hop-forward-nat` — MASQUERADE `10.99.0.0/24` и `10.66.77.0/24` |

## Скорость и Instagram

Активный RU entry: **RuVDS `195.133.146.140`** (старый `194.226.54.124` отключён от hop).

Замеры с нового entry (2026-11-07):

| Путь | Скорость |
|------|----------|
| Entry → Cloudflare напрямую | ~7–12 MB/s |
| Entry → Yandex mirror | ~8–10 MB/s |
| SOCKS hop → Cloudflare 5 MB | ~2.0 MB/s (~16 Mbps) |
| SOCKS hop → Instagram | HTTP 200 |
| Exit IP через hop | `103.75.126.216` |

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
Иностранный трафик выходит с `103.75.126.216`; российский — с `195.133.146.140`.

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


## Надёжный dual-entry (с 2026-11-13)

Проблема: с **нового** RuVDS (`195.133.146.140`) ответы до Fornex часто не доходят (асимметрия). WG Fornex↔**RuVDS-old** (`194.226.54.124`) стабилен.

```
Телефон ──► RuVDS (new) ──SSH forward──► RuVDS-old:11081 ──┐
         ──► RuVDS-old (VLESS/HY2/AWG) ─────────────────────┤
                                                            ▼
                                              WG hop / SSH -R ◄── Fornex dials
                                                            ▼
                                                    microsocks → интернет
                                                      103.75.126.216
```

Принципы:
1. Fornex **сам** держит туннель (WG dial + `autossh -R`) — RU не инициирует выход на Fornex.
2. Watchdog каждую минуту: Fornex `/root/hop-watchdog.sh`, на каждом entry `/root/entry-watchdog.sh`.
3. В боте `/sub` и `/links` — профили **обоих** entry (`/root/hop-vpn-links.txt`).
4. Amnezia: `/awg wife` (new) · `/awg old wife` (old).

Сервисы Fornex: `wg-quick@wg-hop`, `hop-socks`, `hop-socks-local`, `hop-bridge-ssh`, `hop-watchdog.timer`.

## Куда уходит зарубежный трафик с нового RuVDS

У нового сервера свой канал около 50–100 Мбит/с, но ответы до IP Fornex не возвращаются вообще (даже ping). Старый RuVDS до Fornex доходит, и у него порт около 1 Мбит/с и ниже. Поэтому весь зарубежный трафик, завернутый в старый сервер, ползёт.

На новом entry зарубежное разбирает локальный Xray `xray-exit` (`127.0.0.1:11085`, конфиг `server-scripts/exit-split.json`):

| Куда | Выход |
|------|--------|
| Россия (geoip/geosite, банки, маркеты) | напрямую, IP RuVDS |
| YouTube, Instagram, Google, Meta, X | напрямую, TLS ClientHello режется на куски (`finalmask` fragment), чтобы фильтр на магистрали не увидел имя сайта в одном пакете |
| Telegram (`t.me`, IP `149.154.160.0/20`, `91.108.0.0/16`) | по-прежнему через старый RuVDS → Fornex. С нового сервера TCP до этих IP не устанавливается |
| Остальной зарубежный | напрямую, IP `195.133.146.140` |

Агента хостера в процессах нет. Режет сеть перед сервером: по IP назначения и по имени сайта в первом пакете TLS. Пинг до Fornex и до дата-центров Telegram не возвращается, маскировка протокола между VPS это не чинит.

Amnezia на новом entry: table 100 → `hop-tun` → SOCKS `127.0.0.1:11085` (без `--interface`, набор на localhost). DNS в профилях `/awg`: `77.88.8.8`. `8.8.8.8` с этого сервера не резолвит YouTube и Instagram.

На старом entry маршрут table 100 — `via 10.99.0.2 dev wg-hop`. Его возвращают PostUp `wg-hop` и `entry-watchdog.sh`. Профили RuVDS-old остаются на узком порту старого сервера.

