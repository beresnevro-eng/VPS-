# VPS Autopilot

Один скрипт вместо ручных починок: `/root/vps-autopilot.sh`

## Расписание (cron)

| Когда | Что |
|-------|-----|
| **каждые 15 мин** | Autopilot — чинит DNS, конфиги, процессы |
| **03:00** | Бэкап → `/root/backups/` |
| **08:00** | Health-check → Telegram при проблемах |

## Что чинит автоматически

- DNS (если не резолвит)
- `config.json` Xray (из зеркала или бэкапа)
- `config.env`, `hysteria/config.yaml`, код бота
- Процессы: xray, hysteria, telegram-бот
- Дубликаты бота
- Диск >80%: journal, apt cache, большие логи

## Зеркала (не удалять)

```
/root/daily-telegram-security/xray-config.json
/root/daily-telegram-security/mirrors/config.env
/root/daily-telegram-security/mirrors/hysteria-config.yaml
```

## Команды

```bash
bash /root/vps-autopilot.sh      # ручной запуск
bash /root/health-check.sh       # полная проверка
tail -30 /var/log/vps-autopilot.log
```

## Алерты в Telegram

Только когда autopilot **что-то починил**, не чаще 1 раза в 6 часов (одинаковая проблема).
