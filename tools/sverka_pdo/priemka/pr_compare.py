"""Сверка SalesVisor (светофор + отрезки 30.09) с якорем «первое появление в отчёте по принятым» ПДО.
Результат — priemka/cmp.pkl (позиции) и priemka/cmp.txt (сводка)."""
import calendar
import os
import pickle
import sys
from collections import Counter, defaultdict
from datetime import date

sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\SalesVisor")
from sqlalchemy import select  # noqa: E402

from salesvisor.db import make_engine, positions  # noqa: E402
from salesvisor.queries import position_view  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 9, 30)
rows = pickle.load(open(os.path.join(HERE, "rows.pkl"), "rb"))


def dec_end(d: date | None) -> date | None:
    if not d:
        return None
    if d.day <= 10:
        return d.replace(day=10)
    if d.day <= 20:
        return d.replace(day=20)
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def dec_idx(d: date) -> int:
    return d.year * 36 + (d.month - 1) * 3 + (0 if d.day <= 10 else 1 if d.day <= 20 else 2)


def dec_label(d: date | None) -> str:
    if not d:
        return ""
    return f"{1 if d.day <= 10 else 2 if d.day <= 20 else 3}Д{d.month:02d}"


# ---- снимки ПДО по позициям, в хронологии снимков (план раньше факта той же даты)
order_key = lambda r: (r["snap"], 0 if r["kind"] == "план" else 1, r["ver"])
rows.sort(key=order_key)
snaps = sorted({order_key(r) for r in rows})
pdo = defaultdict(lambda: {"snaps": [], "reqs": [], "rej": 0, "reasons": Counter(), "acc_end": None, "fact": []})
for r in rows:
    k = (r["order"], r["pos"])
    a = pdo[k]
    sk = order_key(r)
    if not a["snaps"] or a["snaps"][-1] != sk:
        a["snaps"].append(sk)
        a["reqs"].append(r["req"])       # первая строка позиции в снимке; ниже поправим на минимум в снимке
    elif r["req"] and (not a["reqs"][-1] or r["req"] < a["reqs"][-1]):
        a["reqs"][-1] = r["req"]         # в пределах одного снимка — ранняя дата из отрезков (как в пилоте)
    if r["kind"] == "план":
        if r["status"].startswith("не"):
            a["rej"] += 1
            if r["reason"] and r["reason"] != "0":
                a["reasons"][r["reason"].strip().lower()] += 1
        elif r["status"].startswith("принят") and r["end"]:
            a["acc_end"] = r["end"]
    else:
        a["fact"].append((r["snap"], r["status"], r["reason"], r["classifier"]))
    a.setdefault("customer", r["customer"] or None)
    if not a.get("customer") and r["customer"]:
        a["customer"] = r["customer"]
    a.setdefault("product", r["product"])

for k, a in pdo.items():
    a["anchor"] = a["reqs"][0]
    a["first_snap"] = a["snaps"][0]
    a["req_moves"] = sum(1 for x, y in zip(a["reqs"], a["reqs"][1:]) if x and y and dec_idx(y) != dec_idx(x))
    a["last_req"] = next((x for x in reversed(a["reqs"]) if x), None)

# ---- SalesVisor на 30.09
engine = make_engine("sqlite:///" + os.path.join(os.path.dirname(HERE), "diff_rel.json.db").replace("\\", "/"))
with engine.connect() as conn:
    sv = {(r.order_no, r.pos): position_view(dict(r._mapping), TODAY) for r in conn.execute(select(positions))}


def d(v):
    return date.fromisoformat(v) if isinstance(v, str) and v else v


out_rows = []
for k, p in sv.items():
    a = pdo.get(k)
    first_sv = d(p.get("first_decade_end"))
    cur_sv = d(p.get("due_date"))
    rel_d = d(p.get("release_date")) or (d(p.get("last_fact_ship_date")) if p.get("closed") else None)
    done = bool(p.get("closed") or p.get("released"))
    anchor = dec_end(a["anchor"]) if a else None
    row = {
        "order": k[0], "pos": k[1], "customer": p.get("customer"), "dept": p.get("sales_dept"), "manager": p.get("manager"),
        "product": p.get("product"), "stage": p.get("stage"), "closed": bool(p.get("closed")), "released": bool(p.get("released")),
        "release_date": rel_d, "mp_rub": p.get("mp_rub"),
        "sv_first": first_sv, "sv_first_label": p.get("first_decade"), "sv_current": cur_sv, "sv_current_label": p.get("current_decade"),
        "sap_req": d(p.get("required_date")), "sv_overdue": bool(p.get("overdue")), "sv_days_late": p.get("days_late") or 0,
        "in_pdo": bool(a),
        "anchor": anchor, "anchor_label": dec_label(anchor),
        "first_snap": f"{a['first_snap'][0]:%d.%m} {'план' if a['first_snap'][1] == 0 else 'факт'}{(' v' + str(a['first_snap'][2])) if a['first_snap'][2] else ''}" if a else "",
        "pdo_snaps": len(a["snaps"]) if a else 0, "pdo_rej": a["rej"] if a else 0,
        "pdo_reason": a["reasons"].most_common(1)[0][0] if a and a["reasons"] else "",
        "pdo_moves": a["req_moves"] if a else 0,
        "pdo_acc_end": a["acc_end"] if a else None,
        "pdo_fact": "; ".join(f"{s:%d.%m} {st}" + (f" ({c or rs})" if st.startswith("не") and (c or rs) else "") for s, st, rs, c in a["fact"][-3:]) if a else "",
    }
    # разница якоря и первой декады светофора, в декадах (минус — якорь раньше)
    row["diff_dec"] = dec_idx(anchor) - dec_idx(first_sv) if anchor and first_sv else None
    # долг на 30.09 по якорю: декада якоря прошла, позиция не выпущена и не закрыта
    row["anchor_overdue"] = bool(anchor and anchor < TODAY and not done and p.get("segments_total"))
    end = rel_d if done and rel_d else TODAY
    row["anchor_days_late"] = max((min(end, TODAY) - anchor).days, 0) if anchor else None
    # выпуск к сроку (для позиций, у которых срок уже прошёл)
    row["otd_anchor"] = (None if not anchor or anchor >= TODAY else
                         "в срок" if done and rel_d and rel_d <= anchor else "не в срок" if done and rel_d or not done else "дата неизвестна")
    row["otd_sv"] = (None if not first_sv or first_sv >= TODAY else
                     "в срок" if done and rel_d and rel_d <= first_sv else "не в срок" if done and rel_d or not done else "дата неизвестна")
    out_rows.append(row)

pickle.dump({"rows": out_rows, "pdo": {k: {kk: vv for kk, vv in v.items() if kk != "reasons"} for k, v in pdo.items()},
             "snaps": snaps}, open(os.path.join(HERE, "cmp.pkl"), "wb"))

# ---- сводка
L = []
pct = lambda a, b: f"{100 * a / b:.1f} %" if b else "—"
in_period = [r for r in out_rows if r["sv_first"] and date(2026, 1, 20) <= r["sv_first"] <= date(2026, 9, 30)]
L.append(f"снимков ПДО: {len(snaps)} ({snaps[0][0]:%d.%m} … {snaps[-1][0]:%d.%m}); позиций в снимках: {len(pdo)}")
L.append(f"позиций SalesVisor: {len(out_rows)}; с первой декадой светофора 20.01–30.09: {len(in_period)}; из них есть в снимках ПДО: {sum(r['in_pdo'] for r in in_period)} ({pct(sum(r['in_pdo'] for r in in_period), len(in_period))})")
both = [r for r in in_period if r["in_pdo"] and r["diff_dec"] is not None]
c = Counter(max(-3, min(3, r["diff_dec"])) for r in both)
L.append("якорь ПДО − первая декада светофора (декад): " + ", ".join(f"{k:+d}{'+' if abs(k) == 3 else ''}: {c[k]}" for k in sorted(c)))
L.append(f"  совпадает: {c[0]} ({pct(c[0], len(both))}); якорь раньше: {sum(v for k, v in c.items() if k < 0)}; якорь позже: {sum(v for k, v in c.items() if k > 0)}")
openp = [r for r in out_rows if not r["closed"]]
L.append(f"\nоткрытые позиции (не закрыты) на 30.09: {len(openp)}")
so, ao = {(r['order'], r['pos']) for r in openp if r['sv_overdue']}, {(r['order'], r['pos']) for r in openp if r['anchor_overdue']}
L.append(f"  долг по SalesVisor (текущая декада прошла, не выпущено): {len(so)}")
L.append(f"  долг по якорю ПДО (декада якоря прошла, не выпущено): {len(ao)}  — из них нет в долге SalesVisor: {len(ao - so)}; в долге SalesVisor, но не по якорю: {len(so - ao)}")
L.append(f"  не выпущено и нет в снимках ПДО: {sum(1 for r in openp if not r['in_pdo'] and not r['released'])}")
oo, oa = {o for o, _ in so}, {o for o, _ in ao}
L.append(f"  заказов с долгом: SalesVisor {len(oo)}, по якорю {len(oa)}; только по якорю {len(oa - oo)}, только SalesVisor {len(oo - oa)}")
for name, fld, key_d in (("якорь ПДО", "otd_anchor", "anchor"), ("первая декада светофора", "otd_sv", "sv_first")):
    xs = [r for r in out_rows if r[fld] and r[key_d] and date(2026, 1, 20) <= r[key_d]]
    cc = Counter(r[fld] for r in xs)
    L.append(f"\nвыпуск к сроку — {name} (срок 20.01–20.09): {len(xs)} позиций: " + ", ".join(f"{k} {v}" for k, v in cc.most_common())
             + f"; OTD {pct(cc['в срок'], cc['в срок'] + cc['не в срок'])}")
same = [r for r in in_period if r["in_pdo"] and r["otd_anchor"] and r["otd_sv"]]
L.append(f"на одном множестве (есть и якорь, и светофор, оба срока прошли): {len(same)}: OTD якорь "
         f"{pct(sum(r['otd_anchor'] == 'в срок' for r in same), sum(r['otd_anchor'] in ('в срок', 'не в срок') for r in same))}, "
         f"OTD светофор {pct(sum(r['otd_sv'] == 'в срок' for r in same), sum(r['otd_sv'] in ('в срок', 'не в срок') for r in same))}")
for o in ("1200022802", "1200023400"):
    L.append(f"\nзаказ {o}:")
    for r in sorted((r for r in out_rows if r["order"] == o), key=lambda r: int(r["pos"]) if r["pos"].isdigit() else 0):
        L.append(f"  {r['pos']}: светофор {r['sv_first_label']}→{r['sv_current_label']}; якорь {r['anchor_label']} ({r['first_snap']}); "
                 f"снимков {r['pdo_snaps']}, не принят {r['pdo_rej']}; факт: {r['pdo_fact']}; этап {r['stage']}, выпуск {r['release_date']}; "
                 f"долг SV {r['sv_overdue']} / по якорю {r['anchor_overdue']}")
open(os.path.join(HERE, "cmp.txt"), "w", encoding="utf-8").write("\n".join(L))
