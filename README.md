# VPS-

Управление Fornex VPS: Xray (VLESS+Reality), Hysteria2, Telegram-бот `@My_mini_server_bot`.

**Сервер:** `103.75.126.216` · `287289.fornex.cloud`

## Быстрый старт на сервере

```bash
cd /root/daily-telegram-security
cp mirrors/config.env.example config.env   # заполнить токены
cp mirrors/config.env config.env           # или mirrors/config.env
chmod 600 config.env mirrors/config.env
systemctl restart xray-telegram-bot
bash /root/health-check.sh
```

## Структура

| Путь | Назначение |
|------|------------|
| `bot_poller.py`, `vpn_manager.py` | Telegram-бот |
| `xray-config.json` | Зеркало Xray (не в git — только `xray-config.json.example`) |
| `mirrors/` | Зеркала для autopilot |
| `server-scripts/` | Скрипты обслуживания (копии из `/root/`) |
| `docs/` | Документация |
| `INVENTORY.json` | Инвентарь сервисов |

## Git

Секреты (`config.env`, UUID, private keys) **не коммитятся** — см. `.gitignore`.

После `git pull` на сервере:

```bash
bash /root/vps-autopilot.sh
systemctl restart xray-telegram-bot
```

## Документация

См. [docs/README.md](docs/README.md) и [docs/GIT.md](docs/GIT.md)
