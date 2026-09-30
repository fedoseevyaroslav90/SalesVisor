"""Анализ истории дефицита материалов (2026) по позициям заказов и связь с исходом, ПДО и комментариями."""
import os
import pickle
import re
from collections import Counter, defaultdict
from datetime import date
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))
by_day = pickle.load(open(os.path.join(HERE, "deficit.pkl"), "rb"))
cmp = {(r["order"], r["pos"]): r for r in pickle.load(open(os.path.join(HERE, "cmp.pkl"), "rb"))["rows"]}
H = pickle.load(open(os.path.join(HERE, "hier.pkl"), "rb"))
P = pickle.load(open(os.path.join(HERE, "process.pkl"), "rb"))
days = sorted(by_day)

pos = defaultdict(lambda: {"days": set(), "mats": Counter(), "groups": Counter(), "eta": [], "req": None, "dept": "", "product": "",
                           "comments": Counter()})
mat_pos = defaultdict(set)
mat_days = Counter()
mat_names = {}
for day in days:
    name, rows = by_day[day]
    seen = set()
    for o, p, mat, mname, mgroup, need, eta, state, req, po, comment, dept, product in rows:
        if state != "Не обеспечено":
            continue
        k = (o, p)
        a = pos[k]
        a["days"].add(day)
        a["mats"][mat] += 1
        a["groups"][mgroup] += 1
        a["req"] = req or a["req"]
        a["dept"] = dept or a["dept"]
        a["product"] = product or a["product"]
        if comment:
            a["comments"][comment.strip().lower()[:40]] += 1
        if (k, mat) not in seen:
            seen.add((k, mat))
            a["eta"].append((day, mat, eta))
            mat_pos[mat].add(k)
            mat_days[mat] += 1
            mat_names[mat] = (mname, mgroup)

L = [f"дней в выборке: {len(days)} ({days[0]} … {days[-1]}); позиций хоть раз «не обеспечено»: {len(pos)}"]
# сдвиги ожидаемой даты поставки по материалу для позиции
shifts = []
for k, a in pos.items():
    per = defaultdict(list)
    for day, mat, eta in a["eta"]:
        if eta:
            per[mat].append((day, eta))
    n_ch = 0
    tot = 0
    for mat, xs in per.items():
        xs.sort()
        etas = [e for _, e in xs]
        ch = sum(1 for x, y in zip(etas, etas[1:]) if y != x)
        n_ch += ch
        if len(etas) > 1:
            tot += (etas[-1] - etas[0]).days
    a["eta_changes"], a["eta_shift"] = n_ch, tot
    shifts.append((n_ch, tot))
L.append(f"дней в дефиците на позицию: медиана {median(len(a['days']) for a in pos.values()):.0f}, "
         f"90-й процентиль {sorted(len(a['days']) for a in pos.values())[int(0.9 * len(pos))]}")
L.append(f"позиций, у которых ожидаемая дата поставки менялась: {sum(1 for c, _ in shifts if c)}; из них сдвиг позже на сумму >14 дн.: {sum(1 for c, t in shifts if c and t > 14)}")
grp = Counter()
for a in pos.values():
    for g in a["groups"]:
        grp[g] += 1
L.append("группы материалов (позиций в дефиците): " + ", ".join(f"{k} {v}" for k, v in grp.most_common(12)))
L.append("материалы (позиций; «позиция-дни» по числу отчётов):")
for mat, n in sorted(((m, len(s)) for m, s in mat_pos.items()), key=lambda x: -x[1])[:15]:
    L.append(f"  {n:5} поз.  {mat_days[mat]:6} поз.-отч.  {mat_names[mat][0]}  [{mat_names[mat][1]}]")
L.append("комментарии снабжения в отчёте (топ): " + ", ".join(f"{k} {v}" for k, v in sum((a['comments'] for a in pos.values()), Counter()).most_common(8)))

# группы продукции у позиций в дефиците
def group(o, p):
    return H["pos"].get((o, p)) or "не определена"
L.append("группы продукции позиций в дефиците: " + ", ".join(f"{k} {v}" for k, v in Counter(group(*k) for k in pos).most_common(10)))

# исход: выпуск к требуемой дате (req из отчёта) у позиций в дефиците против остальных того же месяца требуемой даты
def outcome(k, req):
    s = cmp.get(k)
    if not s or not req or req >= date(2026, 9, 30):
        return None
    rel = s.get("release_date")
    done = s.get("closed") or s.get("released")
    if done and rel:
        return "в срок" if rel <= req else "позже"
    return "не выпущено" if not done else None
res_def = Counter()
for k, a in pos.items():
    o = outcome(k, a["req"])
    if o:
        res_def[o] += 1
L.append("исход позиций в дефиците (выпуск к треб. дате из отчёта): " + ", ".join(f"{k} {v}" for k, v in res_def.most_common()))
# база: позиции SAP с треб. датой в 2026 (01–09), не попадавшие в дефицит
res_base = Counter()
for k, s in cmp.items():
    if k in pos:
        continue
    req = s.get("sap_req")
    if req and date(2026, 1, 15) <= req < date(2026, 9, 30) and s.get("in_pdo"):
        o = outcome(k, req)
        if o:
            res_base[o] += 1
L.append("база: позиции из снимков ПДО с треб. датой 15.01–29.09.2026 без дефицита: " + ", ".join(f"{k} {v}" for k, v in res_base.most_common()))

# связь с ПДО: позиции, не принятые с причиной «материал», — были ли в отчёте о дефиците в ту же неделю
dec_hist = defaultdict(list)
import calendar
rows = pickle.load(open(os.path.join(HERE, "rows.pkl"), "rb"))
mat_rej = []
for r in rows:
    if r["kind"] == "план" and r["status"].startswith("не") and r["snap"] >= days[0]:
        s = (r["reason"] or "").lower()
        if re.search(r"дефицит|отсутств|приход|нет |ст-к|пруток|нит|тар|пров|сырь|материал|тпу|тпж", s):
            mat_rej.append(r)
hit = sum(1 for r in mat_rej if (r["order"], r["pos"]) in pos)
L.append(f"непринятых ПДО по материалу (2026): {len(mat_rej)} строк, из них позиция есть в отчётах о дефиците: {hit} ({100 * hit / max(1, len(mat_rej)):.0f} %)")
# связь с комментариями: материалы, названные в комментариях, и их доля в дефиците
C = P["C"]
com_mats = Counter(m.strip().lower() for c in C if c["date"] >= days[0] for m in re.split(r"[,;]", c.get("material") or "") if m.strip())
L.append("материалы в комментариях 2026: " + ", ".join(f"{k} {v}" for k, v in com_mats.most_common(10)))
pickle.dump({"pos": {k: {kk: (sorted(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()} for k, v in pos.items()},
             "mat_pos": {m: sorted(s) for m, s in mat_pos.items()}, "mat_names": mat_names, "days": days},
            open(os.path.join(HERE, "deficit_an.pkl"), "wb"))
open(os.path.join(HERE, "deficit_an.txt"), "w", encoding="utf-8").write("\n".join(L))
print("ok")
