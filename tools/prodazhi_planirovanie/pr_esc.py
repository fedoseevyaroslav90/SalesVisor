"""Эскалации: заказы из комментариев У2 против базы (та же группа продукции и месяц якоря)."""
import os, pickle
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
P = pickle.load(open(os.path.join(HERE, "process.pkl"), "rb"))
esc, base = P["esc_rows"], P["base"]
m = lambda d: d.strftime("%Y-%m") if d else None
rate = {k: (v["в срок"] / (v["в срок"] + v["не в срок"]), v["в срок"] + v["не в срок"]) for k, v in base.items() if v["в срок"] + v["не в срок"] >= 20}
L = []
by_kind = defaultdict(list)
for r in esc:
    if r["otd_anchor"] not in ("в срок", "не в срок") or not r["anchor"]:
        continue
    b = rate.get((r["group"], m(r["anchor"])))
    if not b:
        continue
    kinds = r["events"]
    k = ("запрос приоритета/вопрос о сроке" if ("приоритет" in kinds or "вопрос о сроке" in kinds or "обещание" in kinds)
         else "ограничение (материал/мощность)" if ("ограничение" in kinds) else
         "актуализация заказа" if "актуализ" in kinds else "прочее упоминание")
    by_kind[k].append((r, b[0]))
    by_kind["все"].append((r, b[0]))
for k, xs in by_kind.items():
    orders = {r["order"] for r, _ in xs}
    fact = sum(r["otd_anchor"] == "в срок" for r, _ in xs) / len(xs)
    exp = sum(b for _, b in xs) / len(xs)
    # по заказам: доля заказов, где все позиции в срок
    per_o = defaultdict(list)
    for r, _ in xs: per_o[r["order"]].append(r["otd_anchor"] == "в срок")
    L.append(f"{k}: заказов {len(orders)}, позиций {len(xs)}; в срок к якорю {fact:.0%} при ожидании по базе {exp:.0%}; заказов целиком в срок {sum(all(v) for v in per_o.values())}/{len(per_o)}")
# когда пишут: до или после декады якоря
when = Counter()
for r in esc:
    if r["anchor"]:
        dd = (r["first_comment"] - r["anchor"]).days
        when["до срока" if dd < 0 else "в течение 10 дн. после" if dd <= 10 else "позже 10 дн."] += 1
L.append("когда впервые пишут о заказе относительно декады якоря (позиции): " + ", ".join(f"{k} {v}" for k, v in when.most_common()))
L.append("группы продукции у заказов из комментариев (позиции): " + ", ".join(f"{k} {v}" for k, v in Counter(r["group"] for r in esc).most_common(8)))
open(os.path.join(HERE, "esc.txt"), "w", encoding="utf-8").write("\n".join(L))
