"""Корпус чатов для разметки: блоки «чат × день» с сообщениями по делу; авторы → роли; ГОЗ/тендеры/прибыль исключены."""
import glob, html, json, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
R = json.load(open("crm/roles.json", encoding="utf-8"))
deps = R["deps"]
users = dict(R["users"])
EXCL = re.compile(r"гоз|тендер|прибыль|ebitda|бдр|культура|телевизор|модуль qm|дивизион №6|mes поддержка|обход", re.I)
KW = re.compile(r"1200\d{6}|декад|принят|загрузк|дефицит|срок|перенос|приоритет|себестоим|калькуляц|\bмз\b|\bмп\b|\bвп\b|цен[аыу]|маржин|отгруз|z[0-9]\b|брон|грунт|пруток|стеклопласт|оболочк|модул|окраск|волокн|линия|линии|мощност|пул|портфел|переделк|заказ", re.I)
files = []
for f in glob.glob("crm/chats/*.jsonl"):
    lines = open(f, encoding="utf-8").read().splitlines()
    meta = json.loads(lines[0])["meta"]
    if meta["type"] == "chat" and EXCL.search(meta["title"] or ""):
        continue
    files.append((meta, [json.loads(l) for l in lines[1:]]))
need = sorted({str(m["author"]) for _, ms in files for m in ms if str(m["author"]) not in users and m["author"]})
bx = Bitrix(load_yaml(найти_конфиг()).get("webhook", ""), rps=2)
for k in range(0, len(need), 50):
    for u in bx.call("user.get", {"FILTER": {"ID": need[k:k + 50]}}) or []:
        users[str(u["ID"])] = {"deps": [str(x) for x in (u.get("UF_DEPARTMENT") or [])]}
def chain(did):
    out = []
    while did and did in deps and len(out) < 4:
        out.append(deps[did]["name"]); did = deps[did]["parent"]
    return out
def role(uid):
    if str(uid) == "2289": return "РП (зам. операционного директора)"
    u = users.get(str(uid), {})
    ch = chain(u["deps"][0]) if u.get("deps") else []
    top = " / ".join(ch)
    for rx, name in (("Планово-диспетчерский", "ПДО"), ("сервиса и поддержки продаж", "Сервис и поддержка продаж"), ("снабжения", "Снабжение"),
                     ("складской", "Склад"), ("Отдел технологии", "Технология"), ("Дивизион|Отдел производства", "Производство"),
                     ("качеств", "Качество"), ("Управление продаж", "Продажи"), ("Холдинг|стратегии", "Руководство"), ("оборудован", "Обслуживание оборудования")):
        if re.search(rx, top): return name
    return ch[0] if ch else "?"
def clean(s):
    s = re.sub(r"\[USER=(\d+)\][^\[]*\[/USER\]", lambda m: "@" + role(m.group(1)), str(s or ""))
    s = re.sub(r"\[/?[A-Za-z]+(?:=[^\]]*)?\]", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()
blocks = []
for meta, ms in files:
    title = meta["title"] if meta["type"] == "chat" else f"личный: {meta.get('dep', '')[:40]}"
    byday = defaultdict(list)
    for m in ms:
        byday[m["date"][:10]].append(m)
    for day, xs in sorted(byday.items()):
        if not any(KW.search(x["text"] or "") for x in xs):
            continue
        # только сообщения по делу и по одному соседнему для контекста
        keep = set()
        for i, x in enumerate(xs):
            if KW.search(x["text"] or ""):
                keep.update({i - 1, i, i + 1})
        sel = [xs[i] for i in sorted(keep) if 0 <= i < len(xs) and (xs[i]["text"] or "").strip()]
        text = "\n".join(f"[{role(x['author'])}] {clean(x['text'])[:400]}" for x in sel)[:1800]
        blocks.append({"id": f"{meta['dialog']}|{day}", "chat": title, "type": meta["type"], "date": day, "orders": sorted(set(re.findall(r"1200\d{6}", text))), "text": text})
blocks.sort(key=lambda b: (b["chat"], b["date"]))
k = 6
for i in range(k):
    with open(f"crm/chat_part{i + 1}.jsonl", "w", encoding="utf-8") as f:
        for b in blocks[i * len(blocks) // k:(i + 1) * len(blocks) // k]:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
print("блоков", len(blocks), "символов", sum(len(b["text"]) for b in blocks), "чатов", len({b["chat"] for b in blocks}))
print(Counter(b["chat"] for b in blocks).most_common(12))
