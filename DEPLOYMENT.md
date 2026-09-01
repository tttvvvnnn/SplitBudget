# CI/CD: автодеплой на сервер через GitHub Actions

Как это работает: на каждый пуш/PR гоняются автотесты (`.github/workflows/ci.yml`) — это не
имеет отношения к деплою, просто проверка, что код рабочий. А сам релиз запускается отдельно и
осознанно: вы ставите git-тег версии (`v1.2.0`) → `.github/workflows/release.yml` ещё раз
прогоняет тесты → собирает Docker-образ → пушит его в GitHub Container Registry (ghcr.io) →
создаёт релиз на GitHub → по SSH заходит на сервер → скачивает этот образ и перезапускает
контейнер (`docker-compose.prod.yml`). Сервер сам ничего не собирает — только скачивает готовый
образ, поэтому деплой быстрый и серверу не нужен git.

Почему по тегам, а не на каждый пуш в `main`: так `main` может спокойно копить незавершённые
изменения без риска случайно выкатить их на прод, а версия, которая крутится на сервере, всегда
однозначно соответствует конкретному тегу и записи в `CHANGELOG.md` — это и есть контроль версий
в том смысле, в котором он обычно устроен у настоящих приложений. Как именно оформлять релиз
(что писать в CHANGELOG, как называть тег) — в `RELEASING.md`.

## Шаг 1. Создать репозиторий на GitHub

В проекте уже сделан `git init` и первый коммит. Останется только создать пустой репозиторий на
GitHub и запушить:

```bash
cd family-expenses-bot
# создайте пустой репозиторий на github.com (без README/лицензии), затем:
git remote add origin git@github.com:<ваш-логин>/family-expenses-bot.git
git branch -M main
git push -u origin main
```

Если репозиторий приватный — ничего дополнительно настраивать не нужно, `GITHUB_TOKEN`
для пуша образа в GHCR из Actions работает и для приватных репо.

## Шаг 2. Один раз подготовить сервер

Всё выполняется один раз, дальше сервер только принимает деплои.

```bash
# на сервере, от пользователя с доступом к docker
sudo mkdir -p /opt/family-expenses-bot
sudo chown $USER:$USER /opt/family-expenses-bot
cd /opt/family-expenses-bot
```

Скопируйте на сервер два файла из репозитория (через `scp`, `rsync` или просто создайте руками):
`docker-compose.prod.yml` и `.env` (на основе `.env.example`, с реальным `BOT_TOKEN` и `WEBAPP_URL`).
В `docker-compose.prod.yml` замените `OWNER` на ваш GitHub-логин в нижнем регистре:

```bash
scp docker-compose.prod.yml .env user@your-server:/opt/family-expenses-bot/
```

```
image: ghcr.io/OWNER/family-expenses-bot:latest
```

Создайте папки для данных (если ещё нет) и один раз авторизуйтесь в GHCR — Docker запомнит
логин, и дальше `docker compose pull` будет работать без пароля:

```bash
mkdir -p data/db data/photos
```

Пакет, который соберёт Actions, по умолчанию **приватный**. Чтобы сервер мог его скачивать,
авторизуйтесь один раз токеном с правом `read:packages`:

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token. Права: только `read:packages` (Repository permissions → Packages: Read),
   доступ ограничьте этим одним репозиторием.
2. На сервере:
   ```bash
   echo "<токен>" | docker login ghcr.io -u <ваш-github-логин> --password-stdin
   ```
   Токен можно после этого нигде больше не хранить — Docker сохранит его в
   `~/.docker/config.json`. CI ничего про этот токен не знает и не использует его.

Если не хочется возиться с токеном — проще всего после первого пуша сделать пакет публичным:
GitHub → ваш профиль → Packages → `family-expenses-bot` → Package settings → Change visibility →
Public. Тогда `docker login` на сервере не нужен вовсе.

Первый запуск (дальше это будет делать CI):

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Не забудьте настроить реверс-прокси с HTTPS на этот контейнер — см. основной `README.md`,
раздел «Шаг 3. HTTPS и реверс-прокси» (ничего не меняется, порт тот же — 8000).

## Шаг 3. Deploy-ключ для GitHub Actions

Отдельная SSH-пара только для деплоя (не используйте свой личный ключ):

```bash
ssh-keygen -t ed25519 -f deploy_key -N "" -C "github-actions-deploy"
```

Публичный ключ добавьте на сервер тому пользователю, от которого будет заходить CI (лучше
отдельный непривилегированный пользователь в группе `docker`, а не root):

```bash
# на сервере
sudo useradd -m -s /bin/bash deploy
sudo usermod -aG docker deploy
sudo mkdir -p /home/deploy/.ssh
echo "<содержимое deploy_key.pub>" | sudo tee -a /home/deploy/.ssh/authorized_keys
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh && sudo chmod 600 /home/deploy/.ssh/authorized_keys
sudo chown -R deploy:deploy /opt/family-expenses-bot
```

Приватный ключ (`deploy_key`, без `.pub`) — в секреты GitHub, следующим шагом. Локальную копию
после этого удалите.

## Шаг 4. Секреты в GitHub Actions

В репозитории: Settings → Secrets and variables → Actions → New repository secret. Добавьте:

| Секрет | Значение |
|---|---|
| `DEPLOY_HOST` | IP или домен сервера |
| `DEPLOY_USER` | `deploy` (или другой пользователь из шага 3) |
| `DEPLOY_SSH_KEY` | содержимое приватного ключа `deploy_key` целиком |
| `DEPLOY_PORT` | обычно `22` |
| `DEPLOY_PATH` | `/opt/family-expenses-bot` |

`GITHUB_TOKEN` для пуша образа в ghcr.io передавать не нужно — GitHub создаёт и подставляет его
автоматически в каждом workflow-запуске.

## Шаг 5. Деплой

Дальше деплой происходит по тегам версий, не по каждому пушу — сам процесс выпуска релиза
(обновление CHANGELOG, `git tag`, `git push --tags`) описан в `RELEASING.md`. Коротко:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Это запускает `.github/workflows/release.yml`: тесты → образ `ghcr.io/<логин>/family-expenses-bot`
с тегами `:1.0.0`, `:1.0`, `:latest` → релиз на GitHub → деплой по SSH
(`docker compose -f docker-compose.prod.yml pull && up -d`).

Прогресс — во вкладке **Actions** репозитория.

## Откат на предыдущую версию

Раз каждый релиз затегирован версией, откат — это просто пересборка `latest` на предыдущий тег:

```bash
# на сервере
docker pull ghcr.io/<логин>/family-expenses-bot:0.9.0
docker tag ghcr.io/<логин>/family-expenses-bot:0.9.0 ghcr.io/<логин>/family-expenses-bot:latest
docker compose -f docker-compose.prod.yml up -d
```

Список версий — в GitHub Releases репозитория или `git tag -l`.
