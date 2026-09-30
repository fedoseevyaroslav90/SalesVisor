#!/bin/bash
# Выкладка SalesVisor с ноутбука на ai-ag контура «Инкаб ИИ» (прыжок через ai-gw, ключ остаётся на ноутбуке).
# Запуск из Git Bash:
#   bash /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/выкладка.sh
# Копируется только нужное для сборки: salesvisor/, Dockerfile, docker-compose.yml, requirements.txt, deploy/.
# На сервере дерево /opt/salesvisor/src заменяется целиком (распаковка в src.new, затем подмена), дальше —
# deploy/деплой_ai-ag.sh: копия базы, сборка образа, пересоздание контейнеров, проверка здоровья, откат при сбое.
# После ПЕРВОЙ выкладки — deploy/портал_окружение.sh (общий секрет в окружение портала) и выкладка портала.
set -e
export PATH="/usr/bin:$PATH"
SRC="/c/Users/ia.fedoseev/Desktop/SalesVisor"
cd "$SRC"

for f in salesvisor Dockerfile docker-compose.yml requirements.txt deploy/деплой_ai-ag.sh deploy/docker-compose.ai-ag.yml; do
  [ -e "$f" ] || { echo "ОШИБКА: нет $f в $SRC"; exit 1; }
done
# скрипты с концами строк Windows на сервере не выполнятся — проверяем до отправки
if grep -l $'\r' deploy/*.sh deploy/*.yml docker-compose.yml Dockerfile 2>/dev/null; then
  echo "ОШИБКА: в перечисленных файлах концы строк Windows (CRLF) — git config core.autocrlf false и git checkout -- ."
  exit 1
fi
bash -n deploy/деплой_ai-ag.sh

# версия кода: в образе .git нет — ревизия едет файлом deploy/version.txt (в git не хранится)
REV=$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo "нет git")
[ -n "$(git -C "$SRC" status --porcelain 2>/dev/null)" ] && REV="$REV+незафиксированные правки"
printf '%s (%s)\n' "$REV" "$(date '+%Y-%m-%d %H:%M')" > deploy/version.txt
echo "версия кода: $(cat deploy/version.txt)"

tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='data' \
    -cf - salesvisor Dockerfile docker-compose.yml requirements.txt deploy | \
  ssh -J ai-gw -p 20022 -i ~/.ssh/id_ed25519_ai -o IdentitiesOnly=yes -o ConnectionAttempts=5 \
      yfedoseev@172.16.50.30 \
    'sudo rm -rf /opt/salesvisor/src.new && sudo mkdir -p /opt/salesvisor/src.new && sudo tar -C /opt/salesvisor/src.new -xf - && sudo rm -rf /opt/salesvisor/src.old && if [ -d /opt/salesvisor/src ]; then sudo mv /opt/salesvisor/src /opt/salesvisor/src.old; fi && sudo mv /opt/salesvisor/src.new /opt/salesvisor/src && sudo bash /opt/salesvisor/src/deploy/деплой_ai-ag.sh'
