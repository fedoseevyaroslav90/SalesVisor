#!/bin/bash
# Задать вебхук Битрикс24 для SalesVisor на ai-ag (BITRIX_WEBHOOK_URL в /opt/salesvisor/.env) и проверить доступ к задачам.
# Адрес спрашивается скрытым вводом и уходит на сервер через stdin — не попадает ни в командную строку, ни в журналы,
# ни в файлы на ноутбуке. SalesVisor вебхуком только читает (BITRIX_POST_COMMENTS=0).
# Запуск из PowerShell:
#   chcp 65001 | Out-Null; & "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe" /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/vebhuk_bitrix.sh
set -e
export PATH="/usr/bin:$PATH"
HOST="yfedoseev@172.16.50.30"

echo "Вставьте адрес входящего вебхука Битрикс24 (вида https://team.incab.ru/rest/<номер>/<ключ>/) и нажмите Enter."
echo "Ввод не отображается на экране."
IFS= read -rs W
echo
W="$(printf '%s' "$W" | tr -d '\r\n ')"
# лишнее после ключа (например, «profile.json» из примера Битрикса) отрезаем
if [[ "$W" =~ ^(https://[^/]+/rest/[0-9]+/[A-Za-z0-9]+)(/.*)?$ ]]; then
  W="${BASH_REMATCH[1]}/"
else
  echo "ОШИБКА: это не похоже на адрес входящего вебхука (https://…/rest/<номер>/<ключ>/). Ничего не изменено."
  exit 1
fi

REMOTE='
set -e
read -r W
ENV=/opt/salesvisor/.env
if sudo grep -q "^BITRIX_WEBHOOK_URL=." $ENV; then echo "  был задан — заменяю"; else echo "  не был задан — записываю"; fi
sudo sed -i "/^BITRIX_WEBHOOK_URL=/d" $ENV
printf "BITRIX_WEBHOOK_URL=%s\n" "$W" | sudo tee -a $ENV >/dev/null
unset W
sudo grep -q "^BITRIX_POST_COMMENTS=" $ENV || echo "BITRIX_POST_COMMENTS=0" | sudo tee -a $ENV >/dev/null
echo "  перезапуск веба и синхронизации…"
cd /opt/salesvisor/src
sudo docker compose -p salesvisor --project-directory /opt/salesvisor/src -f docker-compose.yml \
  -f deploy/docker-compose.ai-ag.yml up -d --force-recreate web sync </dev/null >/dev/null 2>&1
sleep 8
echo "  проверка доступа:"
sudo docker exec -i salesvisor-web python -
'

CHECK='
from salesvisor.config import get_settings
from salesvisor.bitrix import Bitrix
s = get_settings()
ids = list(s.pdo_task_ids)
try:                                   # список задач из вкладки «Загрузка», если код уже новый
    from sqlalchemy import select
    from salesvisor.db import bitrix_watch, make_engine
    with make_engine(s.database_url).connect() as c:
        ids = [r[0] for r in c.execute(select(bitrix_watch.c.task_id).where(bitrix_watch.c.active.is_(True)))] or ids
except Exception:
    pass
ids = ids or ["321346"]
b = Bitrix(s)
try:
    me = b.call("user.current", {}) or {}
    print("  вебхук работает от имени:", me.get("LAST_NAME") or "", me.get("NAME") or "", "(id", str(me.get("ID")) + ")")
except Exception as e:
    print("  вебхук отвечает; владельца не узнать (нет права «Пользователи»):", str(e)[:120])
for t in ids:
    try:
        title = (b.get_task(t) or {}).get("title")
        n = len(b.call("task.commentitem.getlist", {"TASKID": t}) or [])
        print(f"  задача {t}: «{title}» — доступ есть, комментариев {n}")
    except Exception as e:
        print(f"  задача {t}: НЕТ ДОСТУПА — {str(e)[:200]}")
b.close()
'

echo
echo "=== Запись вебхука на сервер"
for i in 1 2 3; do
  { printf '%s\n' "$W"; printf '%s\n' "$CHECK"; } | \
    ssh -J ai-gw -p 20022 -i ~/.ssh/id_ed25519_ai -o IdentitiesOnly=yes -o ConnectionAttempts=5 "$HOST" "$REMOTE" && break
  rc=$?
  [ "$rc" -ne 255 ] && { unset W; exit "$rc"; }
  [ "$i" -eq 3 ] && { unset W; echo "ОШИБКА: нет связи с сервером"; exit 255; }
  echo "  связь не установилась, повтор через 5 с ($i из 3)…"; sleep 5
done
unset W
echo
echo "Готово. Если у задачи «НЕТ ДОСТУПА» — добавьте владельца вебхука наблюдателем в эту задачу и запустите скрипт снова."
echo "Отчёты из задач загрузятся при ближайшей синхронизации (раз в час) или по «Проверить сейчас» во вкладке «Загрузка»."
