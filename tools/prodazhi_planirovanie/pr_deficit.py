"""Ежедневные «Отчёт по материалам (Z0+Z4)» 2026 → история дефицита по позициям заказа. Результат — priemka/deficit.pkl.
Строка листа «Дефицит»: материал × позиция заказа клиента (потребность, ожидаемое поступление и дата, обеспеченность)."""
import glob
import os
import pickle
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = r"C:\Users\ia.fedoseev\Desktop\SalesVisor\data\у2\2026"
RE_D = re.compile(r"на (\d\d)\.(\d\d)\.(\d\d)")

WANT = {"Номер продукта": "mat", "Название продукта": "mat_name", "Название группы": "mat_group",
        "Кол-во пост./потребность": "need", "Ожидаемые поступления": "incoming",
        "Крайняя дата ожидаемого поступления": "eta", "Заказ клиента": "order", "Позиция заказа клиента": "pos",
        "Наименование ОЗМ заказа": "product", "ПО": "po", "Обеспеченность": "state", "Треб.дата поставки": "req",
        "Коментарий нормальный": "comment", "Отдел продаж": "dept", "Дефицит/Профицит": "balance"}


def d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def one(path):
    m = RE_D.search(os.path.basename(path))
    day = date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    out = []
    try:
        wb = openpyxl.load_workbook(open(path, "rb"), read_only=True, data_only=True)
    except Exception as e:
        return day, os.path.basename(path), [], f"ошибка чтения: {type(e).__name__}"
    ws = next((w for w in wb.worksheets if w.title.strip().lower() == "дефицит"), None)
    if ws is None:
        return day, os.path.basename(path), [], "нет листа «Дефицит»"
    it = ws.iter_rows(values_only=True)
    hdr = [str(c).strip() if c is not None else "" for c in next(it)]
    ix = {}
    for i, h in enumerate(hdr):
        for k, v in WANT.items():
            if v not in ix and (h.lower() == k.lower() if len(k) <= 3 else h.lower().startswith(k.lower()[:22])):
                ix[v] = i
    for r in it:
        g = lambda k: r[ix[k]] if k in ix and ix[k] < len(r) else None
        o = str(g("order") or "").split(".")[0].strip()
        if not o.startswith("12"):
            continue
        out.append((o, str(g("pos") or "").split(".")[0].strip(), str(g("mat") or "").split(".")[0], str(g("mat_name") or "")[:60],
                    str(g("mat_group") or "")[:40], g("need"), d(g("eta")), str(g("state") or "").strip(), d(g("req")),
                    str(g("po") or "").strip(), str(g("comment") or "")[:60], str(g("dept") or "")[:30], str(g("product") or "")[:60]))
    wb.close()
    return day, os.path.basename(path), out, f"{len(out)} строк"


if __name__ == "__main__":
    files = sorted(f for f in glob.glob(os.path.join(SRC, "*Отчет по материалам*(Z0+Z4)*.xlsx")))
    res = []
    with ProcessPoolExecutor(max_workers=4) as ex:
        for day, name, out, msg in ex.map(one, files):
            res.append((day, name, out))
            print(day, msg, name[:60], flush=True)
    # один файл на дату (если за день два — берём больший)
    by_day = {}
    for day, name, out in res:
        if day and (day not in by_day or len(out) > len(by_day[day][1])):
            by_day[day] = (name, out)
    pickle.dump(by_day, open(os.path.join(HERE, "deficit.pkl"), "wb"))
    print("дней", len(by_day), "строк", sum(len(v[1]) for v in by_day.values()))
