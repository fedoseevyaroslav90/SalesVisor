"""Процессная сверка: комментарии задач У2 (разметка) ↔ решения ПДО по декадам ↔ исход в SAP.
Вход: crm/corpus.jsonl, crm/cls_part*.jsonl, priemka/rows.pkl, priemka/hier.pkl, priemka/cmp.pkl.
Выход: priemka/process.pkl и priemka/process.txt."""
import calendar
import json
import os
import pickle
import re
from collections import Counter, defaultdict
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(HERE)
TODAY = date(2026, 9, 30)


def dec_end(d):
    if not d:
        return None
    if d.day <= 10:
        return d.replace(day=10)
    if d.day <= 20:
        return d.replace(day=20)
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def month(d):
    return d.strftime("%Y-%m") if d else None


# ---------------- комментарии
corpus = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(SP, "crm", "corpus.jsonl"), encoding="utf-8")}
cls = {}
for i in range(1, 5):
    for l in open(os.path.join(SP, "crm", f"cls_part{i}.jsonl"), encoding="utf-8"):
        x = json.loads(l)
        cls[x["id"]] = x
C = []
for cid, c in corpus.items():
    x = cls.get(cid, {})
    C.append({**x, "id": cid, "date": datetime.strptime(c["date"][:10], "%Y-%m-%d").date(), "role": c["role"],
              "orders": sorted(set(x.get("orders") or []) | set(c["orders"])), "text": c["text"]})
C.sort(key=lambda c: c["date"])
ROUTINE = {"отчёт о дефиците материалов"}

# ---------------- группы продукции
H = pickle.load(open(os.path.join(HERE, "hier.pkl"), "rb"))


def group(order, pos, ozm):
    return H["pos"].get((order, pos)) or H["mat"].get(ozm) or "не определена"


# ---------------- решения ПДО: последняя версия плана по каждому отрезку в декаде
rows = pickle.load(open(os.path.join(HERE, "rows.pkl"), "rb"))
okey = lambda r: (r["snap"], 0 if r["kind"] == "план" else 1, r["ver"])
rows.sort(key=okey)


def reason_group(s):
    s = (s or "").lower()
    if not s.strip() or s.strip() in ("0", "-"):
        return "не указана"
    rules = [("загрузк|мощност|полнвя", "мощность (полная загрузка)"), ("кле", "технология: клей на броне"),
             ("ст-к|стеклопрут|пруток|стеклонит|нит", "материал: стеклопруток/нити"), ("тар", "материал: тара/барабаны"),
             ("опыт|конструкц|техпроцесс|отработ", "опыт/новая конструкция"),
             ("приоритет|перепланир|портфел", "приоритеты/перепланирование"),
             ("полом|станин|скорост|оборуд|ремонт", "оборудование"), ("перенос|согласов", "согласованный перенос"),
             ("дефицит|отсутств|приход|нет |брак|пров|сырь|материал|тпу|тпж|ов\\b", "материал (прочее)")]
    for rx, name in rules:
        if re.search(rx, s):
            return name
    return "прочее"


final = {}   # (декада, заказ, поз, отрезок) → последняя строка плана
for r in rows:
    if r["kind"] != "план":
        continue
    d = dec_end(r["req"]) or r["snap"]
    final[(d, r["order"], r["pos"], r["seg"] or "1")] = r
fact = {}
for r in rows:
    if r["kind"] == "факт":
        fact[(dec_end(r["req"]) or r["snap"], r["order"], r["pos"])] = r


def km(r):
    u = (r.get("unit") or "").upper()
    return (r.get("qty_plan") or 0) if u in ("КМ", "KM", "") else 0


dec_stats = defaultdict(lambda: defaultdict(float))
grp_stats = defaultdict(lambda: defaultdict(float))
reasons_by_grp = defaultdict(Counter)
wc_by_grp = defaultdict(Counter)
bn_by_grp = defaultdict(Counter)
reasons_by_month = defaultdict(Counter)
pos_hist = defaultdict(list)
for (d, o, p, s), r in final.items():
    g = group(o, p, r.get("ozm"))
    acc = not r["status"].startswith("не") and r["status"] != ""
    k = km(r)
    vp = r.get("vp") or 0
    for st, key in ((dec_stats, d), (grp_stats, g)):
        st[key]["n"] += 1
        st[key]["km"] += k
        st[key]["acc_n"] += acc
        st[key]["acc_km"] += k if acc else 0
        st[key]["vp"] += vp
        st[key]["rej_vp"] += 0 if acc else vp
    pos_hist[(o, p)].append((d, acc, r["reason"], r.get("bottleneck"), r.get("wc")))
    if not acc:
        rg = reason_group(r["reason"])
        reasons_by_grp[g][rg] += 1
        reasons_by_month[month(d)][rg] += 1
        if r.get("wc"):
            wc_by_grp[g][re.sub(r"[-_]?\d.*$", "", r["wc"])] += 1
        if r.get("bottleneck") and r["bottleneck"] not in ("0", "-"):
            bn_by_grp[g][r["bottleneck"][:40]] += 1
fact_grp = defaultdict(Counter)
fact_cls = defaultdict(Counter)
for (d, o, p), r in fact.items():
    g = group(o, p, r.get("ozm"))
    ok = r["status"].startswith("готов")
    fact_grp[g]["ok" if ok else "bad"] += 1
    if not ok:
        fact_cls[g][(r.get("classifier") or r.get("reason") or "не указана")[:60]] += 1

# ---------------- связь во времени: темы комментариев ↔ причины ПДО по месяцам
TOPIC = {"материал": {"ограничение/нехватка материала"}, "мощность": {"ограничение по мощности/переделу"},
         "приоритет": {"запрос приоритета/ускорения от продаж", "перепланирование/смена приоритетов"},
         "актуализация": {"запрос актуализировать заказ (дата, Z-статус, данные)"}}
cm = defaultdict(Counter)
for c in C:
    evs = {c.get("event")} | set(c.get("also") or [])
    for t, es in TOPIC.items():
        if evs & es:
            cm[month(c["date"])][t] += 1
months = sorted(set(cm) | set(reasons_by_month))


def spearman(a, b):
    def rank(x):
        s = sorted(range(len(x)), key=lambda i: x[i])
        r = [0] * len(x)
        for k, i in enumerate(s):
            r[i] = k
        return r
    if len(a) < 4:
        return None
    ra, rb = rank(a), rank(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return round(1 - 6 * d2 / (n * (n * n - 1)), 2)


ms = [m for m in months if m and "2025-01" <= m <= "2026-09"]
mat_c = [cm[m]["материал"] for m in ms]
mat_d = [reasons_by_month[m]["материал: стеклопруток/нити"] + reasons_by_month[m]["материал (прочее)"] + reasons_by_month[m]["материал: тара/барабаны"] for m in ms]
cap_c = [cm[m]["мощность"] for m in ms]
cap_d = [reasons_by_month[m]["мощность (полная загрузка)"] for m in ms]

# ---------------- заказы из комментариев: что было до и после
cmp = {(r["order"], r["pos"]): r for r in pickle.load(open(os.path.join(HERE, "cmp.pkl"), "rb"))["rows"]}
by_order_pos = defaultdict(list)
for (o, p), v in cmp.items():
    by_order_pos[o].append(v)
esc = []
for c in C:
    if not c.get("orders"):
        continue
    for o in c["orders"]:
        esc.append((o, c))
esc_orders = {}
for o, c in esc:
    esc_orders.setdefault(o, []).append(c)
esc_rows = []
for o, cs in esc_orders.items():
    ps = by_order_pos.get(o, [])
    first_c = min(c["date"] for c in cs)
    kinds = Counter(c.get("event") for c in cs)
    for p in ps:
        hist = sorted(pos_hist.get((o, p["pos"]), []))
        rej_before = sum(1 for d, acc, *_ in hist if not acc and d <= first_c)
        rej_after = sum(1 for d, acc, *_ in hist if not acc and d > first_c)
        esc_rows.append({"order": o, "pos": p["pos"], "group": group(o, p["pos"], None), "first_comment": first_c,
                         "events": "; ".join(f"{k}×{v}" for k, v in kinds.most_common(3)),
                         "rej_before": rej_before, "rej_after": rej_after, "anchor": p.get("anchor"),
                         "release": p.get("release_date"), "otd_anchor": p.get("otd_anchor"), "otd_sv": p.get("otd_sv"),
                         "closed": p.get("closed"), "stage": p.get("stage"), "customer": p.get("customer"), "dept": p.get("dept")})
# база сравнения: позиции той же группы и того же месяца якоря, которых нет в комментариях
esc_keys = {(r["order"], r["pos"]) for r in esc_rows}
base = defaultdict(Counter)
for k, p in cmp.items():
    if k in esc_keys or not p.get("anchor") or not p.get("otd_anchor"):
        continue
    base[(group(k[0], k[1], None), month(p["anchor"]))][p["otd_anchor"]] += 1

pickle.dump({"C": C, "dec_stats": {k: dict(v) for k, v in dec_stats.items()}, "grp_stats": {k: dict(v) for k, v in grp_stats.items()},
             "reasons_by_grp": reasons_by_grp, "wc_by_grp": wc_by_grp, "bn_by_grp": bn_by_grp, "reasons_by_month": reasons_by_month,
             "fact_grp": fact_grp, "fact_cls": fact_cls, "cm": cm, "ms": ms, "esc_rows": esc_rows, "base": base},
            open(os.path.join(HERE, "process.pkl"), "wb"))

# ---------------- сводка
L = []
pct = lambda a, b: f"{100 * a / b:.0f} %" if b else "—"
ev = Counter(c.get("event") for c in C)
L.append(f"комментариев {len(C)} ({C[0]['date']} … {C[-1]['date']}); регулярных отчётов о дефиците {ev['отчёт о дефиците материалов']}")
L.append("события (без регулярных отчётов): " + ", ".join(f"{k} {v}" for k, v in ev.most_common() if k not in ROUTINE))
L.append("кто что пишет (роль → событие, топ):")
re_ = Counter((c["role"].split(":")[0].split(" (")[0], c.get("event")) for c in C if c.get("event") not in ROUTINE)
for (r, e), n in re_.most_common(14):
    L.append(f"  {n:4} {r} → {e}")
L.append("причины в комментариях: " + ", ".join(f"{k} {v}" for k, v in Counter(c.get("cause") for c in C if c.get("cause") not in (None, "нет", "неясно")).most_common()))
L.append("материалы: " + ", ".join(f"{k} {v}" for k, v in Counter(m.strip().lower() for c in C for m in re.split(r"[,;]", c.get("material") or "") if m.strip()).most_common(15)))
L.append("переделы: " + ", ".join(f"{k} {v}" for k, v in Counter(m.strip().lower() for c in C for m in re.split(r"[,;]", c.get("process_step") or "") if m.strip()).most_common(15)))
L.append(f"о деньгах: {sum(1 for c in C if c.get('cost'))}; о сроках: {sum(1 for c in C if c.get('deadline'))}")
L.append("\nрешения ПДО по декадам (последняя версия плана по отрезку):")
for d in sorted(dec_stats):
    s = dec_stats[d]
    if d < date(2025, 1, 1) or d > date(2026, 10, 31):
        continue
    L.append(f"  {d:%d.%m.%y}: отрезков {int(s['n'])}, км {s['km']:.0f}, принято {pct(s['acc_km'], s['km'])} км; ВП не принятого {s['rej_vp'] / 1e6:.1f} млн")
L.append("\nпо группам продукции (все декады 2025–2026):")
for g, s in sorted(grp_stats.items(), key=lambda x: -x[1]["km"]):
    if s["n"] < 200:
        continue
    fg = fact_grp.get(g, {})
    L.append(f"  {g}: отрезков {int(s['n'])}, км {s['km']:.0f}; принято {pct(s['acc_km'], s['km'])} км; ВП не принятого {s['rej_vp'] / 1e6:.0f} млн; "
             f"факт декады выполнен {pct(fg.get('ok', 0), fg.get('ok', 0) + fg.get('bad', 0))}")
    L.append(f"     причины непринятия: " + ", ".join(f"{k} {v}" for k, v in reasons_by_grp[g].most_common(4)))
    L.append(f"     узкое место (ПДО): " + ", ".join(f"{k} {v}" for k, v in bn_by_grp[g].most_common(4)))
    L.append(f"     невыполнение факта: " + ", ".join(f"{k} {v}" for k, v in fact_cls[g].most_common(3)))
L.append(f"\nсвязь во времени (по месяцам {ms[0]}…{ms[-1]}, корреляция Спирмена):")
L.append(f"  комментарии о нехватке материала ↔ непринятие ПДО из-за материала: {spearman(mat_c, mat_d)}")
L.append(f"  комментарии об ограничении мощности ↔ непринятие «полная загрузка»: {spearman(cap_c, cap_d)}")
for m in ms:
    L.append(f"   {m}: комм. материал {cm[m]['материал']:3}, мощность {cm[m]['мощность']:3}, приоритет {cm[m]['приоритет']:3}, актуализ. {cm[m]['актуализация']:3} | "
             f"ПДО не принято: загрузка {reasons_by_month[m]['мощность (полная загрузка)']:5}, материал {reasons_by_month[m]['материал: стеклопруток/нити'] + reasons_by_month[m]['материал (прочее)'] + reasons_by_month[m]['материал: тара/барабаны']:4}, клей {reasons_by_month[m]['технология: клей на броне']:4}")
L.append(f"\nзаказы из комментариев: {len(esc_orders)} заказов, {len(esc_rows)} позиций")
ok = Counter(r["otd_anchor"] for r in esc_rows)
L.append("  выпуск к якорю ПДО: " + ", ".join(f"{k} {v}" for k, v in ok.most_common()))
bb = Counter()
for r in esc_rows:
    if r["anchor"]:
        bb.update(base.get((r["group"], month(r["anchor"])), {}))
L.append("  база (те же группа и месяц якоря, без комментариев): " + ", ".join(f"{k} {v}" for k, v in bb.most_common()) +
         f"; в срок {pct(bb['в срок'], bb['в срок'] + bb['не в срок'])}")
L.append(f"  у эскалированных в срок: {pct(ok['в срок'], ok['в срок'] + ok['не в срок'])}; непринятий ПДО до первого комментария {sum(r['rej_before'] for r in esc_rows)}, после {sum(r['rej_after'] for r in esc_rows)}")
open(os.path.join(HERE, "process.txt"), "w", encoding="utf-8").write("\n".join(L))
print("ok")
