"""Снимки «Отчёт по принятым заказам …» ПДО → строки по отрезкам с порядком снимков. Результат — priemka/rows.pkl."""
import glob
import os
import pickle
import re
from datetime import date, datetime

import openpyxl

# папка ПДО, которую положил РП, и вложения задач «ВАЖНЫЕ НОВОСТИ У2» 2025–2026 (имя: ГГГГ-ММ-ДД_idвложения_имя)
ROOTS = [r"C:\Users\ia.fedoseev\Desktop\SalesVisor\data\приёмка", r"C:\Users\ia.fedoseev\Desktop\SalesVisor\data\у2"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.pkl")

RE_DEC = re.compile(r"в декаду\s+(\d\d)\.(\d\d)\.(\d{4})(?:\s*ver\.?\s*(\d+))?", re.I)
RE_POOL = re.compile(r"(\d)\s*-?\s*[а-я]{0,3}\s*пул", re.I)
RE_JUN = re.compile(r"в ИЮНЬ\s+ver\.?\s*(\d+)(?:\s+(\d\d)\.(\d\d))?", re.I)
RE_PREFIX = re.compile(r"^\d{4}-\d\d-\d\d_\d+_")


def snap_of(name: str):
    """(вид, дата снимка, версия) по имени файла; None — не отчёт по принятым."""
    low = name.lower()
    if "отчет по принятым" not in low and "отчёт по принятым" not in low:
        return None
    kind = "факт" if "и факт" in low else "план"
    m = RE_DEC.search(name)
    if m:
        d, mth, y, v = m.groups()
        if not v:   # 2025: «…_1-ый пул» вместо ver
            pm = RE_POOL.search(name)
            v = pm.group(1) if pm else 0
        return kind, date(int(y), int(mth), int(d)), int(v or 0)
    m = RE_JUN.search(name)
    if m:
        v, d, mth = m.groups()
        return kind, date(2026, int(mth), int(d)) if d else date(2026, 6, 1), int(v)
    return None


def key(v):
    s = str(v if v is not None else "").strip()
    return s.split(".")[0].strip()


def as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", ".").rstrip("-"))
    except (TypeError, ValueError):
        return None


files = {}
for f in [g for root in ROOTS for g in sorted(glob.glob(os.path.join(root, "**", "*.*"), recursive=True))]:
    name = RE_PREFIX.sub("", os.path.basename(f))
    if not name.lower().endswith(".xlsx"):
        continue
    s = snap_of(name)
    if not s:
        continue
    dup = name.startswith("!") or "копия" in name.lower() or re.search(r"\(\d\)", name) is not None
    # один снимок — один файл: «!»-пометку и «— копия» берём, только если другого файла с тем же снимком нет
    if s in files and dup:
        continue
    if s in files and not files[s][1]:
        continue
    files[s] = (f, dup)

rows = []
log = []
for (kind, d, v), (f, _) in sorted(files.items(), key=lambda x: (x[0][1], 0 if x[0][0] == "план" else 1, x[0][2])):
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    # лист с приёмкой по отрезкам: в 2026 — первый («Sheet1»), в 2025 — «исх данные»; полный «Отчет по отрезкам» внутри
    # файлов 2025 — снимок SAP, не приёмка, его пропускаем
    hdr = it = None
    for ws in wb.worksheets:
        if "отрезк" in ws.title.lower():
            continue
        it = ws.iter_rows(values_only=True)
        for i, r in enumerate(it):
            cells = [str(c).strip() if c is not None else "" for c in r]
            if "Заказ клиента" in cells and any(re.fullmatch(r"(принят[оа]?/непринят[оа]?|готов/неготов)", c.lower().replace(" ", "")) for c in cells):
                hdr = {h: i for i, h in reversed(list(enumerate(cells))) if h}
                break
            if i > 10:
                break
        if hdr:
            break
    if not hdr:
        log.append(f"нет заголовка: {os.path.basename(f)}")
        continue
    nh = {re.sub(r"[\s.]+", "", h.lower()): i for h, i in hdr.items()}
    g = lambda r, h: (r[nh[k]] if (k := re.sub(r"[\s.]+", "", h.lower())) in nh and nh[k] < len(r) else None)
    n = 0
    for r in it:
        o, p = key(g(r, "Заказ клиента")), key(g(r, "Позиция заказа"))
        if not o.isdigit() or not p:
            continue
        n += 1
        rows.append({
            "kind": kind, "snap": d, "ver": v, "file": os.path.basename(f),
            "order": o, "pos": p, "seg": key(g(r, "Порядковый номер")),
            "req": as_date(g(r, "Требуемая дата поставки заказа клиента")),
            "end": as_date(g(r, "Дата конца")),
            "status": str(g(r, "Принят / не принят") or g(r, "Принято/не принято") or g(r, "готов/не готов") or "").strip().lower(),
            "reason": str(g(r, "Причина не принятия") or g(r, "причина") or "").strip(),
            "move_to": as_date(g(r, "Дата, на которую перенести")),
            "fact_date": as_date(g(r, "Дата поступления (факт)")),
            "qty_plan": num(g(r, "Кол-во по заказу клиента по отрезку") or g(r, "Кол-во поступления (план)")),
            "qty_fact": num(g(r, "Количество поступления (факт)") or g(r, "Кол-во поступления (факт) MES")),
            "unit": str(g(r, "Базовая ЕИ") or "").strip(),
            "customer": str(g(r, "Имя заказчика") or "").strip(),
            "product": str(g(r, "Наименование материала ГП") or g(r, "Наименование материала поступления") or "").strip(),
            "wc": str(g(r, "Рабочее место") or "").strip(),
            "bottleneck": str(g(r, "Материал / РЦ") or "").strip(),
            "ozm": key(g(r, "ОЗМ ГП") or g(r, "ОЗМ элемента поступления")),
            "classifier": str(g(r, "Классификатор") or "").strip(),
            "mz": num(g(r, "МЗ (из калькуляции) Плановые на отрезок")),
            "vp": num(g(r, "Валовая прибыль, руб.") or g(r, "Валовая прибыль, руб")),
            "dept": str(g(r, "Отдел продаж") or "").strip(),
        })
    wb.close()
    log.append(f"{kind:4} {d} v{v}: {n} строк — {os.path.basename(f)}")

pickle.dump(rows, open(OUT, "wb"))
open(os.path.join(os.path.dirname(OUT), "parse_log.txt"), "w", encoding="utf-8").write("\n".join(log) + f"\nвсего строк {len(rows)}\n")
print(len(rows))
