import json, re
from collections import Counter, defaultdict
L = []
allc = []
for tid in (280039, 321346):
    d = json.load(open(f"crm/task_{tid}.json", encoding="utf-8"))
    for c in d.get("comments") or []:
        allc.append((tid, c))
txt = lambda c: re.sub(r"\[/?(?:B|I|U|S|COLOR[^\]]*|SIZE[^\]]*|QUOTE|CODE|LIST|\*|P|TABLE|TR|TD|DISK FILE ID=[^\]]*)\]", " ", str(c.get("POST_MESSAGE") or ""))
orders, mats, tasks, deals, users = Counter(), Counter(), Counter(), Counter(), Counter()
per_order = defaultdict(list)
topics = Counter()
TOP = {"материалы/дефицит": r"дефицит|материал|сырь|волокн|поставк[аи] материал|снабж",
       "приём в декаду/загрузка": r"прием|приём|принят|декад|загрузк|мощност|пул",
       "отгрузка/ограничения": r"отгруз|ограничен|склад|запас клиента",
       "сроки/переносы": r"срок|перенос|сдвиг|раньше|позже|успе[ет]",
       "долги/просрочка": r"долг|просроч|не выполн",
       "себестоимость/цена/МП": r"себестоим|калькуляц|цен[аыу]|маржин|\bмп\b|стоимост|рентаб",
       "качество/несоответствия": r"несоответ|брак|качеств|отк\b|испытан",
       "договор/аванс/оплата": r"договор|аванс|оплат|предоплат",
       "ОПЫТ/новая конструкция": r"опыт|конструкц|новинк|образц",
       "Z-статусы": r"\bz[0-9]\b|z0|z4|z6|z7"}
for tid, c in allc:
    t = txt(c)
    for o in re.findall(r"\b1200\d{6}\b", t):
        orders[o] += 1; per_order[o].append(c["POST_DATE"][:10])
    for m in re.findall(r"\b4000\d{4}\b", t): mats[m] += 1
    for x in re.findall(r"tasks/task/view/(\d+)", t): tasks[x] += 1
    for x in re.findall(r"crm/deal/details/(\d+)", t): deals[x] += 1
    for x in re.findall(r"\[USER=(\d+)\]", str(c.get("POST_MESSAGE") or "")): users[x] += 1
    lt = t.lower()
    for k, rx in TOP.items():
        if re.search(rx, lt): topics[k] += 1
L.append(f"комментариев {len(allc)}")
L.append(f"номеров заказов 1200xxxxxx: {sum(orders.values())} упоминаний, {len(orders)} разных; в скольких комментариях: {sum(1 for _, c in allc if re.search(r'1200\d{6}', txt(c)))}")
L.append(f"ОЗМ 4000xxxx: {sum(mats.values())} упоминаний, {len(mats)} разных")
L.append(f"ссылки на задачи: {len(tasks)} разных; на сделки CRM: {len(deals)}; упоминания сотрудников: {len(users)} разных")
L.append("темы (комментариев): " + ", ".join(f"{k} {v}" for k, v in topics.most_common()))
L.append("заказы, которые обсуждают чаще всего: " + ", ".join(f"{o}×{n}" for o, n in orders.most_common(15)))
# сравнить с SalesVisor: есть ли эти заказы в отчёте по отрезкам 30.09
import os, pickle
cmp = pickle.load(open("priemka/cmp.pkl", "rb"))["rows"]
sv_orders = {r["order"] for r in cmp}
L.append(f"из обсуждаемых заказов есть в отчёте по отрезкам 30.09: {sum(1 for o in orders if o in sv_orders)} из {len(orders)}")
# длина и примеры тематических фраз — без ФИО: печатаем 12 коротких фрагментов с номерами заказов
ex = [re.sub(r"\s+", " ", txt(c))[:220] for _, c in allc if re.search(r"1200\d{6}", txt(c))][:12]
L.append("\nпримеры фрагментов с заказами:")
L += ["  - " + re.sub(r"\[USER=\d+\][^\[]*\[/USER\]", "@сотрудник", e) for e in ex]
open("crm/links.txt", "w", encoding="utf-8").write("\n".join(L))
json.dump({"orders": orders, "tasks": tasks, "deals": deals}, open("crm/links.json", "w", encoding="utf-8"))
