# Git и версионность

**Репозиторий:** https://github.com/beresnevro-eng/VPS-  
**Путь на сервере:** `/root/daily-telegram-security`

---

## Роли

| Что | Где | В git? |
|-----|-----|--------|
| Код бота, доки, примеры | `daily-telegram-security/` | ✅ |
| Живые секреты | `config.env`, `mirrors/config.env` | ❌ |
| Живой Xray | `/usr/local/etc/xray/config.json` + зеркало `xray-config.json` | ❌ (только `.example`) |
| Скрипты на сервере | `/root/vps-autopilot.sh` и др. | копии в `server-scripts/` |

**Источник правды для кода** — git. После правок в `/root/*.sh` копируйте в `server-scripts/` перед коммитом.

---

## Как ведём версионность

Сейчас — **простая линейная история** на ветке `main`:

1. Работаем на сервере (или pull → правки → push)
2. Один коммит = одна законченная задача (фича, фикс, доки)
3. Сообщение коммита по типу:

| Тип | Когда |
|-----|--------|
| `feat:` | новая команда бота, порт, протокол |
| `fix:` | исправление бага |
| `docs:` | README, GIT, runbook |
| `chore:` | .gitignore, cron, мелочи без логики |
| `refactor:` | перестройка без смены поведения |

Пример:
```bash
git commit -m "fix: корректный pinSHA256 для ссылок Hysteria2"
```

Теги (`v1.0.0`) пока не обязательны — при необходимости пометим релиз на GitHub Releases.

---

## Типовой цикл (фича или доработка)

```bash
cd /root/daily-telegram-security
git pull origin main                    # 1. подтянуть чужие изменения

# 2. правки в коде / docs / server-scripts

git status                              # 3. проверить — нет секретов
git add -A
git commit -m "feat: описание изменения"
git push origin main

# 4. применить на сервере
bash /root/vps-autopilot.sh
systemctl restart xray-telegram-bot     # если менялся бот
systemctl restart xray                    # если менялся xray-config зеркало
```

---

## Кто коммитит

- **Вы** — после крупных изменений вручную или по просьбе в чате
- **AI в Cursor** — после завершения задачи предлагает коммит или коммитит, если вы попросили «сделай и закоммить»

Правило для агента: `.cursor/rules/git-workflow.mdc`

---

## Deploy key (уже настроено)

```bash
cat /root/.ssh/id_ed25519_github.pub
```

GitHub → Settings → Deploy keys → `fornex-vps` (write access).

Remote:
```
origin  git@github.com:beresnevro-eng/VPS-.git
```

---

## Что никогда не коммитить

См. `.gitignore`:

- `config.env`, `clients_db.json`, `vpn-profiles.txt`
- `xray-config.json`, `mirrors/hysteria-config.yaml` (с паролями)
- логи, бэкапы, `__pycache__`

---

## Конфликт с GitHub (README и т.п.)

```bash
git pull origin main --rebase --allow-unrelated-histories
# разрешить конфликты → git add → git rebase --continue
git push origin main
```
