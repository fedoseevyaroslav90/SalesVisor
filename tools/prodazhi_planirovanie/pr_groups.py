"""Решения ПДО и итог декад по группам РП (по марке кабеля): грунт со стеклопластиковой бронёй отдельно."""
import calendar, os, pickle, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brand import decode
HERE = os.path.dirname(os.path.abspath(__file__))
rows = pickle.load(open(os.path.join(HERE, "rows.pkl"), "rb"))
def dec_end(d):
    if not d: return None
    return d.replace(day=10) if d.day <= 10 else d.replace(day=20) if d.day <= 20 else d.replace(day=calendar.monthrange(d.year, d.month)[1])
def grp(name):
    d = decode(name)
    g = d["group"]
    if g == "грунт":
        g = "грунт: броня стеклопластик" if d["glass_armour"] else "грунт: броня сталь"
    return g, d["brand"]
rows.sort(key=lambda r: (r["snap"], r["ver"]))
final, fact = {}, {}
for r in rows:
    D = dec_end(r["snap"])
    (final if r["kind"] == "план" else fact)[(D, r["order"], r["pos"], r["seg"] or "1")] = r
S = defaultdict(lambda: defaultdict(float)); RS = defaultdict(Counter); BN = defaultdict(Counter); WC = defaultdict(Counter); BR = defaultdict(Counter)
for k, r in final.items():
    g, b = grp(r["product"])
    acc = r["status"] != "" and not r["status"].startswith("не")
    km = (r.get("qty_plan") or 0) if (r.get("unit") or "").upper() in ("КМ", "KM", "") else 0
    s = S[g]; s["n"] += 1; s["km"] += km; s["acc_km"] += km if acc else 0; s["vp"] += r.get("vp") or 0; s["rej_vp"] += 0 if acc else (r.get("vp") or 0)
    BR[g][b] += 1
    if not acc:
        RS[g][re.sub(r"\s+", " ", (r["reason"] or "не указана").lower())[:45]] += 1
        if r.get("bottleneck") and r["bottleneck"] not in ("0", "-"): BN[g][r["bottleneck"][:30]] += 1
    if r.get("wc"): WC[g][re.sub(r"[-_ ]?\d[\d-]*$", "", r["wc"])] += 1
F = defaultdict(Counter); FC = defaultdict(Counter)
for k, r in fact.items():
    g, _ = grp(r["product"])
    ok = r["status"].startswith("готов"); F[g]["ok" if ok else "bad"] += 1
    if not ok: FC[g][(r.get("classifier") or r.get("reason") or "не указана")[:50]] += 1
L = []
pct = lambda a, b: f"{100 * a / b:.0f} %" if b else "—"
for g, s in sorted(S.items(), key=lambda x: -x[1]["km"]):
    f = F[g]
    L.append(f"{g}: отрезков {int(s['n'])}, км {s['km']:.0f}, принято {pct(s['acc_km'], s['km'])} км, ВП плана {s['vp'] / 1e6:.0f} млн, ВП непринятого {s['rej_vp'] / 1e6:.0f} млн, итог декады выполнен {pct(f['ok'], f['ok'] + f['bad'])}")
    L.append("   марки: " + ", ".join(f"{k} {v}" for k, v in BR[g].most_common(6)))
    L.append("   рабочие места (линии): " + ", ".join(f"{k} {v}" for k, v in WC[g].most_common(6)))
    L.append("   причины непринятия: " + ", ".join(f"{k} {v}" for k, v in RS[g].most_common(4)))
    L.append("   узкое место: " + ", ".join(f"{k} {v}" for k, v in BN[g].most_common(4)))
    L.append("   невыполнение декады: " + ", ".join(f"{k} {v}" for k, v in FC[g].most_common(3)))
pickle.dump({"S": {k: dict(v) for k, v in S.items()}, "RS": RS, "BN": BN, "WC": WC, "BR": BR, "F": F, "FC": FC}, open(os.path.join(HERE, "groups.pkl"), "wb"))
open(os.path.join(HERE, "groups.txt"), "w", encoding="utf-8").write("\n".join(L))
