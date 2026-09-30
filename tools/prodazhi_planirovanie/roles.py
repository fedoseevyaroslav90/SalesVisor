"""Роли авторов комментариев: пользователь → подразделение (Битрикс, только чтение). Результат — crm/roles.json (локально, с ФИО)."""
import json, re, sys
from collections import Counter
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
bx = Bitrix(load_yaml(найти_конфиг()).get("webhook", ""), rps=2)
ids = set()
for tid in (280039, 321346):
    for c in json.load(open(f"crm/task_{tid}.json", encoding="utf-8")).get("comments") or []:
        ids.add(str(c.get("AUTHOR_ID")))
        ids.update(re.findall(r"\[USER=(\d+)\]", str(c.get("POST_MESSAGE") or "")))
deps = {}
for d in bx.call_list("department.get", {}) if hasattr(bx, "call_list") else bx.call("department.get", {}):
    deps[str(d["ID"])] = {"name": d.get("NAME"), "parent": str(d.get("PARENT") or "")}
users = {}
ids = sorted(i for i in ids if i and i != "None")
for i in range(0, len(ids), 50):
    part = ids[i:i + 50]
    res = bx.call("user.get", {"FILTER": {"ID": part}, "ADMIN_MODE": False}) or []
    for u in res:
        users[str(u["ID"])] = {"name": f"{u.get('LAST_NAME', '')} {u.get('NAME', '')}".strip(), "position": u.get("WORK_POSITION"),
                               "deps": [str(x) for x in (u.get("UF_DEPARTMENT") or [])], "active": u.get("ACTIVE")}
def chain(did):
    out = []
    while did and did in deps and len(out) < 8:
        out.append(deps[did]["name"]); did = deps[did]["parent"]
    return out
for uid, u in users.items():
    u["dep_chain"] = [chain(d) for d in u["deps"]]
json.dump({"users": users, "deps": deps}, open("crm/roles.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("пользователей", len(users), "из", len(ids), "подразделений", len(deps))
