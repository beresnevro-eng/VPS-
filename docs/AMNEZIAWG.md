# AmneziaWG на Fornex VPS

**Ветка:** `feature/amneziawg` (отладка параллельно с `main`)  
**Протокол:** UDP `51830` · не трогает Xray `:443` и Hysteria `:8444`

---

## Зачем отдельная ветка

| `main` | `feature/amneziawg` |
|--------|---------------------|
| Xray + HY2 в проде | эксперимент с AWG |
| стабильные коммиты | установка, тесты, откат |
| merge только после проверки на LTE | |

```bash
cd /root/daily-telegram-security
git checkout feature/amneziawg   # тест AWG
git checkout main                # обратно в прод
```

---

## Установка (на сервере)

**Перед установкой:** диск &lt;85%, DNS работает (`getent hosts github.com`).

```bash
bash /root/fix-dns-now.sh          # если DNS сбоит
bash /root/setup-amnesiawg-lite.sh # или server-scripts/setup-amnesiawg-lite.sh
```

Скрипт:
- ставит `amneziawg` из PPA (DKMS + kernel module)
- открывает только **UDP 51830** в UFW
- создаёт клиентов: `iphone`, `router`, `macbook`
- не меняет конфиг Xray

Проверка:

```bash
systemctl status awg-quick@awg0
awg show awg0
ss -ulnp | grep 51830
```

---

## Бот

После установки и `systemctl restart xray-telegram-bot`:

- `/awg` — меню
- `/awg iphone` — файл `.conf` для iPhone
- `/awg status` — статус сервиса
- `/vpn` — строка `AmneziaWG: active`

Клиент: **AmneziaVPN** или **AmneziaWG** (импорт `.conf`).

---

## Безопасный запуск (после инцидента с UFW)

**НЕ использовать** `fix-awg-routing.sh` (удалён — ломал `before.rules`).

```bash
bash /root/awg-safe-start.sh   # запуск + проверка Xray :443
bash /root/awg-safe-stop.sh     # откат AWG без трогания Xray
```

Скрипт:
- бэкапит `before.rules` (на всякий случай)
- **не редактирует** файлы UFW
- только `ufw allow 51830/udp` (без reload)
- NAT/forward через **PostUp** в `awg0.conf` (цепочка `ufw-before-forward`)
- перед и после — проверка **Xray :443**

---

## Handshake есть, интернета нет

Симптом на iPhone: **Отправлено** много, **Получено** ~92 B.

**Частая причина:** в клиенте `AllowedIPs = 0.0.0.0/0, ::/0` — iPhone гонит IPv6 в туннель, на сервере нет IPv6-NAT на `awg0`.  
**Исправление:** только `AllowedIPs = 0.0.0.0/0`, перезапуск AmneziaVPN (disconnect → connect). Конфиги в `/root/amnesiawg-clients/` и `/awg` в боте уже без `::/0`.

Сначала: `bash /root/awg-safe-start.sh` (безопасный PostUp).

---

## Откат (если AWG мешает)

```bash
systemctl stop awg-quick@awg0
systemctl disable awg-quick@awg0
# опционально:
# apt-get remove -y amneziawg amneziawg-tools
```

Xray и Hysteria продолжат работать.

---

## Merge в `main`

Когда на **LTE в проблемном регионе** AWG стабильнее VLESS:

1. `git checkout main && git merge feature/amneziawg`
2. `git push origin main`
3. Обновить `INVENTORY.json`, health-check
4. Создать тег `v1.1.0-awg` (опционально)

---

## Ресурсы VPS

- RAM ~1 GB — DKMS может быть тесно при сборке модуля
- Диск — следить за `df -h` (цель &lt;80% до установки)
- Лог установки: `/root/setup-amnesiawg.log`
