# Fornex VPS — документация

**Сервер:** `103.75.126.216` · `287289.fornex.cloud`  
**Бот:** `@My_mini_server_bot`

## Команды

| Задача | Команда |
|--------|---------|
| Autopilot | `bash /root/vps-autopilot.sh` |
| Health-check | `bash /root/health-check.sh` |
| Бэкап | `bash /root/backup-server.sh` |
| Перезапуск бота | `systemctl restart xray-telegram-bot` |
| Подписка в боте | `/sub` |

## Git на сервере

```bash
cd /root/daily-telegram-security
git pull
bash /root/vps-autopilot.sh
systemctl restart xray-telegram-bot
```

Секреты не в репозитории — только `mirrors/config.env.example`.

## Правила

1. Бота перезапускать через **systemd**, не `nohup`.
2. `config.json` Xray — зеркало в `xray-config.json` (локально на сервере).
3. После смены SNI — обновить подписку (`/sub`).
