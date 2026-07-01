# Подключение Git (GitHub)

Репозиторий: https://github.com/beresnevro-eng/VPS-

## 1. Deploy key на GitHub

На сервере уже создан ключ:

```bash
cat /root/.ssh/id_ed25519_github.pub
```

В GitHub: **репозиторий VPS- → Settings → Deploy keys → Add deploy key**

- Title: `fornex-vps`
- Key: вставить содержимое `.pub`
- Allow write access: **включить** (для `git push`)

## 2. Первый push (на сервере)

```bash
cd /root/daily-telegram-security
git push -u origin main
```

Если репозиторий на GitHub пустой — push пройдёт сразу.  
Если там уже есть README — один раз:

```bash
git pull origin main --rebase
git push -u origin main
```

## 3. Обновление после правок

```bash
cd /root/daily-telegram-security
git add -A
git status   # убедиться, что нет config.env / xray-config.json
git commit -m "описание изменений"
git push
bash /root/vps-autopilot.sh
systemctl restart xray-telegram-bot
```

## Что не попадает в git

См. `.gitignore`: `config.env`, `xray-config.json`, `clients_db.json`, пароли Hysteria.

## Remote

```
origin  git@github.com:beresnevro-eng/VPS-.git
```

SSH config: `/root/.ssh/config` → ключ `id_ed25519_github`
