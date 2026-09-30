import os, pickle, re, calendar
from collections import Counter
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
A = pickle.load(open(os.path.join(HERE, "deficit_an.pkl"), "rb"))
cmp = {(r["order"], r["pos"]): r for r in pickle.load(open(os.path.join(HERE, "cmp.pkl"), "rb"))["rows"]}
H = pickle.load(open(os.path.join(HERE, "hier.pkl"), "rb"))
T = date(2026, 9, 30)
def first_date(s):
    xs = [d for d in (s.get("anchor"), s.get("sv_first")) if d]
    return min(xs) if xs else None
def res(s, fd):
    if not fd or fd >= T: return None
    rel = s.get("release_date"); done = s.get("closed") or s.get("released")
    if done and rel: return "в срок" if rel <= fd else "позже"
    return "не выпущено" if not done else None
# вид дефицита по комментариям снабжения
def kind(c):
    ks = set(c)
    real = any("ждём" in k or "ждем" in k for k in ks)
    fake = any(k.startswith("ох") or "склад" in k for k in ks)
    return "ждём поставку" if real and not fake else "материал на ОХ/складе" if fake and not real else "смешано" if real and fake else "в обработке/прочее"
L = []
kc = Counter(); out = {}
for k, a in A["pos"].items():
    kd = kind(a["comments"])
    kc[kd] += 1
    s = cmp.get(k)
    if not s: continue
    r = res(s, first_date(s))
    if r: out.setdefault(kd, Counter())[r] += 1
L.append("вид дефицита по комментарию снабжения (позиции): " + ", ".join(f"{k} {v}" for k, v in kc.most_common()))
tot = Counter()
for kd, c in out.items():
    tot.update(c)
    n = sum(c.values()); L.append(f"  {kd}: к первой дате в срок {100 * c['в срок'] / n:.0f} % ({n} поз.; позже {c['позже']}, не выпущено {c['не выпущено']})")
n = sum(tot.values()); L.append(f"все позиции в дефиците: к первой дате в срок {100 * tot['в срок'] / n:.0f} % из {n}")
base = Counter()
for k, s in cmp.items():
    if k in A["pos"]: continue
    fd = first_date(s)
    if fd and date(2026, 1, 20) <= fd < T:
        r = res(s, fd)
        if r: base[r] += 1
n = sum(base.values()); L.append(f"база 2026 без дефицита: к первой дате в срок {100 * base['в срок'] / n:.0f} % из {n}")
# по группам продукции: в дефиците против без
for g in ("Дроп-кабель", "Самонесущие кабели", "Кабели в грунт", "Кабели в канализацию", "Грозотрос", "Специальные кабели", "Внутриобъектовый кабель"):
    a, b = Counter(), Counter()
    for k, s in cmp.items():
        if (H["pos"].get(k) or "") != g: continue
        fd = first_date(s)
        if not fd or fd < date(2026, 1, 20): continue
        r = res(s, fd)
        if r: (a if k in A["pos"] else b)[r] += 1
    f = lambda c: f"{100 * c['в срок'] / sum(c.values()):.0f} % из {sum(c.values())}" if sum(c.values()) else "—"
    L.append(f"  {g}: в дефиците {f(a)}; без дефицита {f(b)}")
# стеклопластик — броня
glass = {m for m, (n, g) in A["mat_names"].items() if "стеклопласт" in (g or "").lower() or "стеклопласт" in (n or "").lower()}
gp = {k for m in glass for k in A["mat_pos"].get(m, [])}
L.append(f"позиций с дефицитом стеклопластика: {len(gp)}; группы: " + ", ".join(f"{k} {v}" for k, v in Counter(H['pos'].get(tuple(k)) or 'не определена' for k in gp).most_common(5)))
open(os.path.join(HERE, "def2.txt"), "w", encoding="utf-8").write("\n".join(L))
