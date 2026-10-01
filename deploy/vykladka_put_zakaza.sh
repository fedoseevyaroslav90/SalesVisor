#!/bin/bash
# Выкладка «Путь заказа» (этапы 1–2: отчёты ПДО, дефициты, «Пульс ПДО», «Окно мощности»; отбор «Скрыть этапы»)
# на ai-ag — одним запуском, шаг за шагом, с остановкой на первой ошибке.
# Запуск из PowerShell:
#   chcp 65001 | Out-Null; & "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe" /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/vykladka_put_zakaza.sh
# или из Git Bash:
#   bash /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/vykladka_put_zakaza.sh
# Повторный запуск безопасен: .env не дублируется, код выкладывается заново, уже загруженные файлы — в done/.
set -e
export PATH="/usr/bin:$PATH"
SRC="/c/Users/ia.fedoseev/Desktop/SalesVisor"
ARCH="$SRC/data/zagruzka_rc.tgz"          # история «Загрузки РЦ» 2026 (собрана из data/приёмка/*/Загрузка РЦ)
HOST="yfedoseev@172.16.50.30"
OPTS=(-i ~/.ssh/id_ed25519_ai -o IdentitiesOnly=yes -o ConnectionAttempts=5)

step() { echo; echo "=== $1"; }
# первая попытка через шлюз иногда падает на banner exchange — до трёх попыток, если ssh не соединился (код 255)
remote() {
  local i rc
  for i in 1 2 3; do
    ssh -J ai-gw -p 20022 "${OPTS[@]}" "$HOST" "$1" && return 0
    rc=$?
    [ "$rc" -ne 255 ] && return "$rc"
    echo "  связь не установилась, повтор через 5 с ($i из 3)…"
    sleep 5
  done
  return 255
}

step "1/5. Задача У2 (321346) для отчётов ПДО — в .env сервера"
remote "sudo grep -q '^PDO_TASK_IDS=' /opt/salesvisor/.env || printf '\nPDO_TASK_IDS=321346\nPDO_MATERIALS_DAYS=20\n' | sudo tee -a /opt/salesvisor/.env >/dev/null; sudo grep '^PDO_' /opt/salesvisor/.env"

step "2/5. Код на сервер: копия базы, сборка, пересоздание контейнеров (deploy/выкладка.sh)"
bash "$SRC/deploy/выкладка.sh"

step "3/5. Перечитать последний отчёт по отрезкам с новой логикой статусов"
remote "sudo docker exec salesvisor-sync python -m salesvisor reload"

step "4/5. История «Загрузки РЦ» 2026 — в папку выгрузок"
if [ -f "$ARCH" ]; then
  for i in 1 2 3; do
    scp -o ProxyJump=ai-gw -P 20022 "${OPTS[@]}" "$ARCH" "$HOST:/tmp/zagruzka_rc.tgz" && break
    [ "$i" -eq 3 ] && { echo "ОШИБКА: архив не отправился"; exit 1; }
    echo "  повтор отправки ($i из 3)…"; sleep 5
  done
  remote "mkdir -p /tmp/zrc && tar -xzf /tmp/zagruzka_rc.tgz -C /tmp/zrc && rm -f /tmp/zrc/*.docx && sudo install -o zavod-sftp -m 644 -t /data/SAP/salesvisor /tmp/zrc/* && sudo touch -d '-5 min' /data/SAP/salesvisor/* && rm -rf /tmp/zrc /tmp/zagruzka_rc.tgz && echo \"  файлов ждут загрузки: \$(sudo find /data/SAP/salesvisor -maxdepth 1 -type f | wc -l)\""
else
  echo "  архива $ARCH нет — шаг пропущен"
fi

step "5/5. Синхронизация: отчёты ПДО из задачи У2 и файлы из папки (5–15 минут, ждите)"
remote "sudo docker exec salesvisor-sync python -m salesvisor sync > /tmp/sv_sync.log 2>&1; echo '  загружено по видам:'; grep -oE \"'source': '[a-z_]+'\" /tmp/sv_sync.log | sort | uniq -c; echo '  предупреждения (последние):'; grep -E 'WARNING|ERROR' /tmp/sv_sync.log | cut -c1-220 | tail -15"

echo
echo "Готово. Откройте портал → «Контроль заказов»: вкладки «Пульс ПДО» и «Окно мощности», в отборе — «Скрыть этапы»."
echo "Если в предупреждениях есть «bitrix pdo: BitrixError» — пользователь вебхука сервера не видит задачу 321346."
