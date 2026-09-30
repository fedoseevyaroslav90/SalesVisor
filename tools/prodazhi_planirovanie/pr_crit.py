"""Критичная дата из реестра просьб против первого снимка ПДО (якорь), первой декады светофора и даты ЗК."""
import calendar, os, pickle
from collections import Counter
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
R = pickle.load(open(os.path.join(HERE, "requests.pkl"), "rb"))
cmp = {(r["order"], r["pos"]): r for r in pickle.load(open(os.path.join(HERE, "cmp.pkl"), "rb"))["rows"]}
def dec_end(d):
    if not d: return None
    return d.replace(day=10) if d.day <= 10 else d.replace(day=20) if d.day <= 20 else d.replace(day=calendar.monthrange(d.year, d.month)[1])
def di(d): return d.year * 36 + (d.month - 1) * 3 + (0 if d.day <= 10 else 1 if d.day <= 20 else 2)
out = {"all": Counter(), "crit<zk": Counter(), "crit=zk": Counter()}
zk_vs_anchor = Counter(); ex = []
n_rows = 0
for q in R["reqs"]:
    if not q["critical"] or not q["zk_date"] or not q["positions"] or (q["added"] and (q["added"] - q["critical"]).days > 30) or q["critical"].year < 2026:
        continue
    anchors = [cmp[(q["order"], p)]["anchor"] for p in q["positions"] if (q["order"], p) in cmp and cmp[(q["order"], p)].get("anchor")]
    svf = [cmp[(q["order"], p)]["sv_first"] for p in q["positions"] if (q["order"], p) in cmp and cmp[(q["order"], p)].get("sv_first")]
    first = min(anchors + svf) if anchors or svf else None
    if not first:
        continue
    n_rows += 1
    c, z = dec_end(q["critical"]), dec_end(q["zk_date"])
    k = "crit<zk" if di(c) < di(z) else "crit=zk" if di(c) == di(z) else "crit>zk"
    rel = "крит. = первая дата" if di(c) == di(first) else "крит. раньше первой даты" if di(c) < di(first) else "крит. позже первой даты"
    for key in ("all", k) if k in out else ("all",):
        out[key][rel] += 1
    zk_vs_anchor["ЗК = первая дата" if di(z) == di(first) else "ЗК позже первой (сдвинута)" if di(z) > di(first) else "ЗК раньше первой"] += 1
    if k == "crit<zk" and len(ex) < 8:
        ex.append((q["order"], q["decade"], q["critical"], q["zk_date"], first, rel))
L = [f"просьб с датами и известной первой датой (якорь ПДО или первая декада светофора): {n_rows}"]
L.append("дата ЗК на момент просьбы против первой даты: " + ", ".join(f"{k} {v}" for k, v in zk_vs_anchor.most_common()))
for k, c in out.items():
    L.append(f"{k}: " + ", ".join(f"{a} {b}" for a, b in c.most_common()))
L.append("примеры (заказ, декада просьбы, критичная, ЗК, первая дата, вывод):")
L += [f"  {e[0]} {e[1]:%d.%m} крит {e[2]:%d.%m} ЗК {e[3]:%d.%m} первая {e[4]:%d.%m} — {e[5]}" for e in ex]
open(os.path.join(HERE, "crit.txt"), "w", encoding="utf-8").write("\n".join(L))
