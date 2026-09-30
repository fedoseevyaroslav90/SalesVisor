"""Корпус комментариев задач У2 2025–2026: роль вместо ФИО, чистый текст, вложения, заказы. → crm/corpus.jsonl и 4 части."""
import html, json, re
R = json.load(open("crm/roles.json", encoding="utf-8"))


def role(uid):
    u = R["users"].get(str(uid), {})
    ch = (u.get("dep_chain") or [[]])[0]
    top = " / ".join(ch[:3])
    if "Планово-диспетчерский" in top: return "ПДО"
    if "Отдел сервиса и поддержки продаж" in top: return "Сервис и поддержка продаж"
    if "Дивизион" in top or "Отдел производства" in top: return "Производство (" + ch[0] + ")"
    if "складской" in top.lower(): return "Склад"
    if "Управление продаж" in top: return "Продажи: " + ch[0]
    if "Холдинг" in top or "стратегии" in top: return "Руководство"
    return ch[0] if ch else "?"


def clean(s):
    s = str(s or "")
    s = re.sub(r"\[USER=(\d+)\][^\[]*\[/USER\]", lambda m: "@" + role(m.group(1)), s)
    s = re.sub(r"\[DISK FILE ID=[^\]]*\]", " [файл] ", s)
    s = re.sub(r"\[/?[A-Z]+(?:=[^\]]*)?\]", " ", s)
    s = html.unescape(s)
    # ФИО в тексте «Фамилия Имя написал(а):» — заменить
    s = re.sub(r"[А-ЯЁA-Z][а-яёa-z]+ [А-ЯЁA-Z][а-яёa-z]+ написал(а)?", "Коллега написал", s)
    return re.sub(r"\s+", " ", s).strip()


rows = []
for tid, year in ((280039, 2025), (321346, 2026)):
    for c in json.load(open(f"crm/task_{tid}.json", encoding="utf-8")).get("comments") or []:
        t = clean(c.get("POST_MESSAGE"))
        rows.append({"id": f"{year}-{c['ID']}", "date": c["POST_DATE"][:16].replace("T", " "), "role": role(c["AUTHOR_ID"]),
                     "files": [o.get("NAME") for o in (c.get("ATTACHED_OBJECTS") or {}).values()],
                     "orders": sorted(set(re.findall(r"\b1200\d{6}\b", t))), "text": t[:3000]})
with open("crm/corpus.jsonl", "w", encoding="utf-8") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
n = len(rows); k = 4
for i in range(k):
    with open(f"crm/corpus_part{i + 1}.jsonl", "w", encoding="utf-8") as f:
        for r in rows[i * n // k:(i + 1) * n // k]: f.write(json.dumps(r, ensure_ascii=False) + "\n")
tot = sum(len(r["text"]) for r in rows)
print("комм.", n, "символов текста", tot, "пустых (только файл)", sum(1 for r in rows if len(r["text"]) < 5))
