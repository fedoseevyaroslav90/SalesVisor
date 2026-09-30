"""Список диалогов РП в Битрикс-чате (только чтение) и подразделения собеседников. → crm/chats_list.json (локально)."""
import json, sys
from collections import Counter
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
bx = Bitrix(load_yaml(найти_конфиг()).get("webhook", ""), rps=2)
items, offset = [], 0
while True:
    res = bx.call("im.recent.list", {"SKIP_OPENLINES": "Y", "LIMIT": 200, "OFFSET": offset}) or {}
    batch = res.get("items") or []
    items += batch
    if not res.get("hasMore") or not batch:
        break
    offset += len(batch)
R = json.load(open("crm/roles.json", encoding="utf-8"))
deps = R["deps"]
def chain(did):
    out = []
    while did and did in deps and len(out) < 6:
        out.append(deps[did]["name"]); did = deps[did]["parent"]
    return out
uids = sorted({str(i["id"]) for i in items if i.get("type") == "user"})
users = dict(R["users"])
need = [u for u in uids if u not in users]
for k in range(0, len(need), 50):
    for u in bx.call("user.get", {"FILTER": {"ID": need[k:k + 50]}}) or []:
        users[str(u["ID"])] = {"deps": [str(x) for x in (u.get("UF_DEPARTMENT") or [])], "position": u.get("WORK_POSITION")}
out = []
for i in items:
    t = i.get("type")
    rec = {"dialog": str(i["id"]), "type": t, "title": i.get("title"), "last": (i.get("message") or {}).get("date"),
           "chat_id": (i.get("chat") or {}).get("id")}
    if t == "user":
        u = users.get(str(i["id"]), {})
        rec["dep"] = " / ".join((chain(u["deps"][0]) if u.get("deps") else [])[:3])
        rec["position"] = u.get("position")
    out.append(rec)
json.dump(out, open("crm/chats_list.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("диалогов", len(out), Counter(o["type"] for o in out))
print("с сотрудниками по подразделениям (топ):")
for k, v in Counter(o.get("dep", "")[:80] for o in out if o["type"] == "user").most_common(25): print(f"  {v:3} {k}")
print("групповые чаты: всего", sum(1 for o in out if o["type"] == "chat"))
