"""Иерархия продукции по позиции и по ОЗМ из «портфель Отчёт по отрезкам» (ПДО, 20.05.2026) → priemka/hier.pkl"""
import glob, os, pickle, re
from collections import Counter
import openpyxl
f = sorted(glob.glob(r"C:\Users\ia.fedoseev\Desktop\SalesVisor\data\приёмка\05 Май\*портфель*20.05.2026*"))[0]
wb = openpyxl.load_workbook(open(f, "rb"), read_only=True, data_only=True)
ws = wb.worksheets[0]
it = ws.iter_rows(values_only=True)
hdr = [str(c).strip() if c is not None else "" for c in next(it)]
ix = lambda pred: next(i for i, h in enumerate(hdr) if pred(h))
i_o = ix(lambda h: h == "Заказ клиента"); i_p = ix(lambda h: h.startswith("Позиция заказа"))
i_m = ix(lambda h: h == "Материал"); i_h = ix(lambda h: "иерарх" in h.lower())
i_mt = next((i for i, h in enumerate(hdr) if h.startswith("Материал") and i != i_m), None)
i_brand = ix(lambda h: h.startswith("Марка кабеля"))
pos, mat, brand = {}, {}, {}
for r in it:
    o = str(r[i_o] or "").split(".")[0].strip(); p = str(r[i_p] or "").split(".")[0].strip()
    h = str(r[i_h] or "").strip(); m = str(r[i_m] or "").split(".")[0].strip()
    if not o or not h: continue
    pos[(o, p)] = h
    if m: mat.setdefault(m, Counter())[h] += 1
    b = str(r[i_brand] or "").strip()
    if b: brand.setdefault(b, Counter())[h] += 1
mat = {m: c.most_common(1)[0][0] for m, c in mat.items()}
brand = {b: c.most_common(1)[0][0] for b, c in brand.items()}
pickle.dump({"pos": pos, "mat": mat, "brand": brand, "hdr": hdr}, open("priemka/hier.pkl", "wb"))
c = Counter(pos.values())
open("priemka/hier.txt", "w", encoding="utf-8").write(f"колонка: {hdr[i_h]}; позиций {len(pos)}, ОЗМ {len(mat)}, марок {len(brand)}\n" + "\n".join(f"{v:7} {k}" for k, v in c.most_common()))
