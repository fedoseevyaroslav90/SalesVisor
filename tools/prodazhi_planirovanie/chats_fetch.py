"""Сообщения рабочих диалогов РП с 01.01.2025 (только чтение) → crm/chats/<dialog>.jsonl.
Разрешено РП 01.10.2026 («разбираем чаты тоже»). Хранится только во временной папке сессии; ФИО заменяются ролями при разборе."""
import json, os, re, sys
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
bx = Bitrix(load_yaml(найти_конфиг()).get("webhook", ""), rps=2.5)
L = json.load(open("crm/chats_list.json", encoding="utf-8"))
KW = r"план|пдо|декад|производ|продаж|у2\b|заказ|загрузк|приорит|отгруз|снабж|дефицит|материал|склад|окгт|грунт|спецкаб|себестоим|\bмп\b|цех|дивизион|оперативк|штаб|сырь|волокн|брон|пул|портфел|sap|сап|срок|клиент|тендер|мтс|ростелеком|арктик|калькуляц|мпз|финанс|гоз|линии|координац"
DEP = r"Планово-диспетчерский|сервиса и поддержки продаж|Отдел производства|Дивизион|снабжения|складской|Управление продаж|Инкаб Холдинг|Отдел технологии"
sel = [o for o in L if (o["type"] == "chat" and re.search(KW, (o["title"] or "").lower())) or
       (o["type"] == "user" and re.search(DEP, o.get("dep") or ""))]
SINCE, CAP = "2025-01-01", 4000
done = 0
for o in sel:
    path = f"crm/chats/{o['dialog']}.jsonl"
    if os.path.exists(path):
        continue
    msgs, last = [], None
    while len(msgs) < CAP:
        p = {"DIALOG_ID": o["dialog"], "LIMIT": 50}
        if last:
            p["LAST_ID"] = last
        try:
            res = bx.call("im.dialog.messages.get", p) or {}
        except Exception as e:
            print("ошибка", o["type"], type(e).__name__, str(e)[:80]); break
        batch = res.get("messages") or []
        if not batch:
            break
        msgs += [{"id": m["id"], "date": m["date"], "author": m.get("author_id"), "text": m.get("text") or "",
                  "files": (m.get("params") or {}).get("FILE_ID") or []} for m in batch]
        last = min(m["id"] for m in batch)
        if min(m["date"] for m in batch)[:10] < SINCE:
            break
    msgs = [m for m in msgs if m["date"][:10] >= SINCE]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {k: o.get(k) for k in ("dialog", "type", "title", "dep", "position", "chat_id")}}, ensure_ascii=False) + "\n")
        for m in sorted(msgs, key=lambda m: m["id"]):
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    done += 1
    if done % 20 == 0:
        print("диалогов", done, "из", len(sel), flush=True)
print("готово: диалогов", done, "отобрано", len(sel))
