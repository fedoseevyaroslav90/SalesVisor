#!/bin/bash
# Развёртывание SalesVisor на узле ai-ag контура «Инкаб ИИ» (30.09.2026). Запуск от root:
#   sudo bash /opt/salesvisor/src/deploy/деплой_ai-ag.sh            — сборка и запуск (обновление)
#   sudo bash /opt/salesvisor/src/deploy/деплой_ai-ag.sh --rollback — откат на прежний образ salesvisor:prev
# Исходники кладёт deploy/выкладка.sh с ноутбука в /opt/salesvisor/src. Окружение — /opt/salesvisor/.env
# (600, создаётся один раз со случайными паролем базы и общим секретом портала), база — /opt/salesvisor/pgdata,
# копии базы — /opt/salesvisor/backups (перед каждой выкладкой), выгрузки SFTP — /data/SAP/salesvisor.
# Три контейнера docker compose (проект salesvisor): salesvisor-db (PostgreSQL 16), salesvisor-web
# (порт 8102 на 127.0.0.1 и 172.16.50.30), salesvisor-sync (раз в час забирает выгрузки из папки SFTP).
set -e

BASE=/opt/salesvisor
SRC=$BASE/src
ENV_FILE=$BASE/.env
IMG=salesvisor:local
PREV=salesvisor:prev
IMPORT=/data/SAP/salesvisor
HEALTH=http://127.0.0.1:8102/api/health
COMPOSE="docker compose -p salesvisor --project-directory $SRC -f $SRC/docker-compose.yml -f $SRC/deploy/docker-compose.ai-ag.yml"

wait_health() {
  local i=0 code=000
  while [ "$i" -lt 20 ]; do
    sleep 3
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$HEALTH" || true)
    [ "$code" = 200 ] && break
    i=$((i+1))
  done
  echo "здоровье ($HEALTH): HTTP $code"
  [ "$code" = 200 ]
}

# up ОБРАЗ — поднять все три контейнера из образа (веб и синхронизация — один образ salesvisor:local)
up() {
  if [ "$1" != "$IMG" ]; then docker tag "$1" "$IMG"; fi
  $COMPOSE up -d --no-build --remove-orphans
}

command -v docker >/dev/null 2>&1 || { echo "ОШИБКА: docker не найден"; exit 1; }
[ -f "$SRC/docker-compose.yml" ] || { echo "ОШИБКА: нет исходников в $SRC (сначала deploy/выкладка.sh с ноутбука)"; exit 1; }
# compose читает .env из каталога проекта; сам файл живёт вне src (src заменяется целиком при выкладке)
ln -sfn "$ENV_FILE" "$SRC/.env"

if [ "${1:-}" = "--rollback" ]; then
  [ -f "$ENV_FILE" ] || { echo "ОШИБКА: нет $ENV_FILE — откатывать нечего"; exit 1; }
  docker image inspect "$PREV" >/dev/null 2>&1 || { echo "ОШИБКА: образа $PREV нет — откатывать не на что"; exit 1; }
  echo "откат: поднимаю веб и синхронизацию из $PREV ..."
  up "$PREV"
  if ! wait_health; then docker logs --tail 30 salesvisor-web 2>&1 || true; exit 1; fi
  echo "ОТКАТ ВЫПОЛНЕН: работает $PREV (он же теперь $IMG). Схема базы назад не откатывается —"
  echo "копии базы до выкладок: ls $BASE/backups (восстановление: gunzip -c <файл> | docker exec -i salesvisor-db psql -U salesvisor salesvisor)"
  exit 0
fi

[ -s "$SRC/deploy/version.txt" ] && echo "версия кода: $(cat "$SRC/deploy/version.txt")"

mkdir -p "$BASE/pgdata" "$BASE/backups"
chmod 700 "$BASE/backups"
# папка выгрузок: пишет служебный пользователь SFTP завода, забирает контейнер синхронизации (done/, failed/)
if id zavod-sftp >/dev/null 2>&1; then
  install -d -o zavod-sftp -g zavod-sftp -m 775 "$IMPORT"
else
  mkdir -p "$IMPORT"
  echo "ПРЕДУПРЕЖДЕНИЕ: нет пользователя zavod-sftp — $IMPORT создан от root"
fi

# .env создаётся один раз; дальше правится руками (sudo nano $ENV_FILE) и применяется повторным запуском.
# Общий секрет портала в экран не выводится — его переносит deploy/портал_окружение.sh по ssh-каналу.
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<EOF
# SalesVisor на ai-ag — создан деплой-скриптом $(date '+%Y-%m-%d %H:%M'). Права 600, в git не хранится.
POSTGRES_PASSWORD=$(openssl rand -hex 24)
WEB_BIND=172.16.50.30
WEB_PORT=8102
SYNC_EVERY=3600
TZ=Asia/Yekaterinburg
# общий секрет с порталом «Инкаб ИИ» (на ai-gw — SALESVISOR_TOKEN в /opt/litellm/.env)
PORTAL_TOKEN=$(openssl rand -hex 24)
IMPORT_HOST_DIR=$IMPORT
# Metabase с Timeweb не виден — выгрузки приходят в папку SFTP; ключ оставить пустым
METABASE_URL=https://metabase.incab.ru
METABASE_API_KEY=
# Битрикс24: только СЛУЖЕБНЫЙ входящий вебхук (не личный)
BITRIX_WEBHOOK_URL=
BITRIX_TASK_URL=https://team.incab.ru/company/personal/user/0/tasks/task/view/{id}/
BITRIX_DEAL_URL=https://team.incab.ru/crm/deal/details/{id}/
BITRIX_POST_COMMENTS=0
EOF
  chmod 600 "$ENV_FILE"
  umask 022
  echo "создан $ENV_FILE (права 600): пароль базы и общий секрет портала — случайные, на экран не выводятся"
fi

# копия базы перед выкладкой (если база уже работает); хранятся 10 последних
if docker ps --format '{{.Names}}' | grep -qx salesvisor-db; then
  f="$BASE/backups/salesvisor_$(date +%Y%m%d-%H%M%S)_pre-deploy.sql.gz"
  docker exec salesvisor-db pg_dump -U salesvisor salesvisor | gzip > "$f"
  echo "копия базы: $f ($(du -h "$f" | cut -f1))"
  ls -1t "$BASE"/backups/*_pre-deploy.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

# прежний образ — под тег :prev (одно поколение отката без пересборки)
if docker image inspect "$IMG" >/dev/null 2>&1; then
  docker tag "$IMG" "$PREV"
  echo "прежний образ сохранён как $PREV"
fi

# базовые образы: python:3.12-slim-bookworm уже есть на узле (Тендер-агент); postgres:16 — с Docker Hub,
# который из контура временами отвечает 429 — три попытки с паузой
for img in python:3.12-slim-bookworm postgres:16; do
  docker image inspect "$img" >/dev/null 2>&1 && continue
  ok=0
  for attempt in 1 2 3; do
    if docker pull -q "$img" >/dev/null 2>&1; then ok=1; break; fi
    echo "docker pull $img: попытка $attempt не удалась — пауза 20 с"; sleep 20
  done
  [ "$ok" = 1 ] || { echo "ОШИБКА: образ $img не скачался (Docker Hub недоступен) — повторить позже"; exit 1; }
done

echo "собираю образ $IMG ..."
docker build --build-arg BASE_IMAGE=python:3.12-slim-bookworm -t "$IMG" "$SRC" > "$BASE/build.log" 2>&1 || {
  echo "ОШИБКА сборки, хвост журнала $BASE/build.log:"; tail -20 "$BASE/build.log"; exit 1; }
tail -1 "$BASE/build.log"

$COMPOSE config -q
echo "запускаю контейнеры ..."
up "$IMG"
if ! wait_health; then
  echo "ОШИБКА: веб не поднялся, хвост журнала:"
  docker logs --tail 30 salesvisor-web 2>&1 || true
  if docker image inspect "$PREV" >/dev/null 2>&1; then
    echo "возвращаю прежний образ $PREV ..."
    up "$PREV"
    wait_health && echo "работает прежняя версия ($PREV)"
  fi
  exit 1
fi
$COMPOSE ps --format 'table {{.Name}}\t{{.Status}}'
echo "ВЫКЛАДКА ВЫПОЛНЕНА: salesvisor-web/-sync из $IMG, база salesvisor-db; прежний образ — $PREV"
echo "откат: sudo bash $SRC/deploy/деплой_ai-ag.sh --rollback"
