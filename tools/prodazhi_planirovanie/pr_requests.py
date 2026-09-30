"""«Комментарии к портфелю заказов» (просьбы У2 по декадам) → исход по ПДО и SAP. Выход: priemka/requests.pkl, requests.txt.
ФИО менеджеров в сводку не выводятся."""
import calendar
import os
import pickle
import re
from collections import Counter, defaultdict
from datetime import date, datetime

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = r"C:\Users\ia.fedoseev\Downloads\Комментарии к портфелю заказов.xlsx"
TODAY = date(2026, 9, 30)


def dec_end(d):
    if not d:
        return None
    return d.replace(day=10) if d.day <= 10 else d.replace(day=20) if d.day <= 20 else d.replace(day=calendar.monthrange(d.year, d.month)[1])


def as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", str(v or ""))
    if m:
        y = int(m.group(3))
        y = 2000 + y if y < 100 else y
        try:
            return date(y if y < 2100 else 2026, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def positions(spec, all_pos):
    """«10», «10-20», «10,20», «с 10 по 60», «10;100-120», «все» → список позиций (позиции заказа кратны 10)."""
    s = str(spec or "").lower().replace("\n", ",").replace(";", ",")
    if not s.strip() or "все" in s or s.strip() in ("-", "ч"):
        return sorted(all_pos, key=int) if all_pos else []
    out = set()
    s = re.sub(r"с\s*(\d+)\s*по\s*(\d+)", r"\1-\2", s)
    for part in s.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b >= a and b - a <= 20000:
                out.update(str(x) for x in range(a, b + 1, 10) if not all_pos or str(x) in all_pos)
            continue
        m = re.fullmatch(r"(\d+)(?:\.\d+)?", part)
        if m:
            out.add(m.group(1))
    return sorted(out, key=int)


def kind(text):
    t = (text or "").lower()
    if re.search(r"приоритет|срочн|прошу (взять|принять|изготовить|произвести)|необходимо изготовить до|до \d\d\.\d\d|отгрузить до|к \d\d\.\d\d|неустойк|штраф|сдаёт объект|сдает объект", t):
        return "приоритет/срок"
    if re.search(r"опытн|технолог|отработ|настро|испыт|тест|образ", t):
        return "опытный/отработка с технологом"
    if re.search(r"использ|ов |волокн|g\.?65|маркировк|паспорт|толеранс|вывести|конец|модул|цвет|оболочк", t):
        return "технологическое указание"
    if re.search(r"намот|барабан|катушк|погрузк|упаков|отмотк|зашив|инспекц", t):
        return "намотка/упаковка/отгрузка"
    return "без текста/прочее" if not t.strip() or t.strip() in ("-",) else "прочее"


cmp = {(r["order"], r["pos"]): r for r in pickle.load(open(os.path.join(HERE, "cmp.pkl"), "rb"))["rows"]}
order_pos = defaultdict(set)
for (o, p) in cmp:
    order_pos[o].add(p)
H = pickle.load(open(os.path.join(HERE, "hier.pkl"), "rb"))
rows = pickle.load(open(os.path.join(HERE, "rows.pkl"), "rb"))
rows.sort(key=lambda r: (r["snap"], 0 if r["kind"] == "план" else 1, r["ver"]))
plan_final, fact = {}, {}
for r in rows:
    # декада — та, в план которой попала строка (дата в имени снимка ПДО), а не требуемая дата
    D = dec_end(r["snap"])
    if r["kind"] == "план":
        plan_final[(D, r["order"], r["pos"])] = r
    else:
        fact[(D, r["order"], r["pos"])] = r

wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
reqs = []
for ws in wb.worksheets:
    m = re.fullmatch(r"(\d\d)\.(\d\d)", ws.title.strip())
    if not m:
        continue
    D = dec_end(date(2026, int(m.group(2)), int(m.group(1))))
    it = ws.iter_rows(values_only=True)
    hdr = [str(c).strip() if c is not None else "" for c in next(it)]
    ix = {h: i for i, h in reversed(list(enumerate(hdr))) if h}
    col = lambda r, name: r[ix[name]] if name in ix and ix[name] < len(r) else None
    txt_col = next((h for h in hdr if h.startswith("Комментарий")), None)
    for r in it:
        orders = re.findall(r"1200\d{6}", str(col(r, "Заказ клиента") or ""))
        if not orders:
            continue
        text = str(col(r, txt_col) or "").strip()
        for o in orders:
            ps = positions(col(r, "Позиция"), order_pos.get(o))
            reqs.append({"decade": D, "sheet": ws.title, "order": o, "positions": ps, "pos_spec": str(col(r, "Позиция") or ""),
                         "added": as_date(col(r, "Дата внесения комментария")), "zk_date": as_date(col(r, "Дата готовности из ЗК")),
                         "critical": as_date(col(r, "Критичная дата готовности")), "text": text, "kind": kind(text),
                         "manager": str(col(r, "Ответственный менеджер") or "").strip()})

# исход по каждой позиции просьбы
res = []
for q in reqs:
    for p in q["positions"] or []:
        s = cmp.get((q["order"], p))
        pl = plan_final.get((q["decade"], q["order"], p))
        fc = fact.get((q["decade"], q["order"], p))
        rel = s.get("release_date") if s else None
        done = bool(s and (s.get("closed") or s.get("released")))
        crit = q["critical"] or q["zk_date"]
        res.append({**{k: q[k] for k in ("decade", "order", "kind", "added", "zk_date", "critical")}, "pos": p,
                    "group": H["pos"].get((q["order"], p)) or "не определена",
                    "pdo": ("принят" if pl and not pl["status"].startswith("не") else "не принят" if pl else "нет в плане"),
                    "pdo_reason": pl["reason"] if pl else "", "fact": fc["status"] if fc else "",
                    "release": rel, "done": done,
                    "by_critical": (None if not crit or crit > TODAY else "в срок" if done and rel and rel <= crit else "позже" if done and rel else "не выпущено" if not done else None),
                    "by_decade": (None if q["decade"] > TODAY else "в срок" if done and rel and rel <= q["decade"] else "позже" if done and rel else "не выпущено" if not done else None)})

# база: все позиции в финальном плане тех же декад
base = defaultdict(Counter)
req_keys = {(r["decade"], r["order"], r["pos"]) for r in res}
for (D, o, p), pl in plan_final.items():
    if D < date(2026, 4, 1) or D > TODAY or (D, o, p) in req_keys:
        continue
    base[D]["принят" if not pl["status"].startswith("не") else "не принят"] += 1

L = [f"листов (декад): {len({q['decade'] for q in reqs})}; строк-просьб: {len(reqs)}; заказов: {len({q['order'] for q in reqs})}; позиций после раскрытия: {len(res)}"]
L.append("виды просьб (строки): " + ", ".join(f"{k} {v}" for k, v in Counter(q["kind"] for q in reqs).most_common()))
cr = [q for q in reqs if q["critical"] and q["zk_date"]]
L.append(f"критичная дата раньше даты ЗК: {sum(1 for q in cr if q['critical'] < q['zk_date'])} из {len(cr)}; "
         f"на сколько раньше (медиана, дн.): {sorted((q['zk_date'] - q['critical']).days for q in cr if q['critical'] < q['zk_date'])[len([1 for q in cr if q['critical'] < q['zk_date']]) // 2] if any(q['critical'] < q['zk_date'] for q in cr) else '—'}")
lead = sorted((q["decade"] - q["added"]).days for q in reqs if q["added"] and q["added"] <= q["decade"])
L.append(f"за сколько дней до конца декады вносят просьбу: медиана {lead[len(lead) // 2] if lead else '—'}")
L.append("группы продукции в просьбах (позиции): " + ", ".join(f"{k} {v}" for k, v in Counter(r["group"] for r in res).most_common(8)))
for k in sorted({r["kind"] for r in res}):
    xs = [r for r in res if r["kind"] == k]
    pdo = Counter(r["pdo"] for r in xs)
    bc = Counter(r["by_critical"] for r in xs if r["by_critical"])
    bd = Counter(r["by_decade"] for r in xs if r["by_decade"])
    L.append(f"\n{k}: позиций {len(xs)} (заказов {len({r['order'] for r in xs})})")
    L.append("  решение ПДО по декаде просьбы: " + ", ".join(f"{a} {b}" for a, b in pdo.most_common()))
    L.append("  выпуск к критичной дате: " + ", ".join(f"{a} {b}" for a, b in bc.most_common()) +
             (f" → в срок {100 * bc['в срок'] / sum(bc.values()):.0f} %" if bc else ""))
    L.append("  выпуск к концу декады просьбы: " + ", ".join(f"{a} {b}" for a, b in bd.most_common()) +
             (f" → в срок {100 * bd['в срок'] / sum(bd.values()):.0f} %" if bd else ""))
    L.append("  причины непринятия: " + ", ".join(f"{a} {b}" for a, b in Counter((r["pdo_reason"] or "не указана")[:40] for r in xs if r["pdo"] == "не принят").most_common(4)))
tb = sum(base.values(), Counter())
L.append(f"\nбаза (позиции в финальных планах тех же декад без просьб): принято {100 * tb['принят'] / max(1, sum(tb.values())):.0f} % из {sum(tb.values())}")
pr = [r for r in res if r["kind"] == "приоритет/срок" and r["pdo"] != "нет в плане"]
L.append(f"просьбы о приоритете/сроке, попавшие в план декады: принято {100 * sum(r['pdo'] == 'принят' for r in pr) / max(1, len(pr)):.0f} % из {len(pr)}")
L.append(f"просьб с позициями, которых нет в плане ПДО декады просьбы: {sum(1 for r in res if r['pdo'] == 'нет в плане')} из {len(res)}")
# по заказам: заказ «в срок», если все его позиции из просьбы выпущены к критичной дате
L.append("")
L.append("по заказам (все позиции просьбы выпущены к критичной дате):")
for k in sorted({r["kind"] for r in res}):
    per = defaultdict(list)
    for r in res:
        if r["kind"] == k and r["by_critical"]:
            per[(r["decade"], r["order"])].append(r["by_critical"] == "в срок")
    per_pdo = defaultdict(list)
    for r in res:
        if r["kind"] == k and r["pdo"] != "нет в плане":
            per_pdo[(r["decade"], r["order"])].append(r["pdo"] == "принят")
    L.append(f"  {k}: просьб-заказов {len(per)}, целиком в срок {sum(all(v) for v in per.values())}; "
             f"в плане декады целиком принято {sum(all(v) for v in per_pdo.values())} из {len(per_pdo)}")
pickle.dump({"reqs": reqs, "res": res}, open(os.path.join(HERE, "requests.pkl"), "wb"))
open(os.path.join(HERE, "requests.txt"), "w", encoding="utf-8").write("\n".join(L))
print("ok")
