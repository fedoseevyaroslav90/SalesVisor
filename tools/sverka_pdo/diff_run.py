"""Сравнительный прогон SalesVisor: версия передачи (0a8063d) и текущая — на одних реальных выгрузках 30.09.2026.
Запуск: python diff_run.py <папка кода> <выход.json>"""
import json
import os
import sys
import time
from datetime import date

code, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, code)
from salesvisor import queries  # noqa: E402
from salesvisor.db import make_engine, positions  # noqa: E402
from salesvisor import ingest  # noqa: E402
from sqlalchemy import select  # noqa: E402

D = r"C:\Users\ia.fedoseev\Downloads"
SP = os.path.dirname(os.path.abspath(__file__))
FILES = [os.path.join(D, "запрос__отчет_по_отрезкам_xt_2026-09-30T12_59_44.300691+05_00.csv"),
         os.path.join(D, "светофор_v2__тест__2026-09-30T12_59_39.77714+05_00.csv"),
         os.path.join(D, "EXPORT.XLSX"),
         os.path.join(SP, "sv", "SalesVisor-передача", "образцы", "ШАБЛОН диспетчерского отчета 2026.xlsx")]
db = os.path.join(SP, f"diff_{os.path.basename(out)}.db")
if os.path.exists(db):
    os.remove(db)
engine = make_engine("sqlite:///" + db.replace("\\", "/"))
res = {"loads": []}
for f in FILES:
    data = open(f, "rb").read()
    name = os.path.basename(f)
    if hasattr(ingest, "sniff_source"):
        src = ingest.sniff_source(data, name)
    else:
        src = ingest.detect_source(list(ingest.read_table(data, name).columns))
    t = time.time()
    r = ingest.load_file(engine, src, data, name)
    r["sec"] = round(time.time() - t, 1)
    res["loads"].append(r)
today = date(2026, 9, 30)
with engine.connect() as conn:
    res["positions"] = {f"{r.order_no}/{r.pos}": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                                   for k, v in r._mapping.items() if k not in ("first_seen_at", "updated_at")}
                        for r in conn.execute(select(positions))}
day = queries.day_plan(engine, today, today=today)
res["day"] = day["summary"]
res["day_keys"] = sorted(f"{r['order_no']}/{r['pos']}:{'M' if r['task_make'] else ''}{'S' if r['task_ship'] else ''}" for r in day["positions"])
disp = queries.dispatcher_summary(engine)
res["disp"] = {d["decade"]: {k: d[k] for k in ("km", "km_ready", "positions", "positions_ready", "mismatch")}
               for mo in disp["months"] for d in mo["decades"]}
res["disp_mismatch"] = disp["mismatch"]
orders = queries.list_orders(engine, today=today) if "today" in queries.list_orders.__code__.co_varnames else queries.list_orders(engine)
res["orders"] = {"n": len(orders), "late": sum(1 for o in orders if o["overdue"]),
                 "red": sum(1 for o in orders if o["color"] == "red"), "yellow": sum(1 for o in orders if o["color"] == "yellow"),
                 "green": sum(1 for o in orders if o["color"] == "green"), "none": sum(1 for o in orders if not o["color"]),
                 "quality": sum(1 for o in orders if o["quality"])}
res["meta"] = {k: (len(v) if isinstance(v, list) else v) for k, v in queries.meta(engine).items() if k in ("managers", "depts", "lines", "quality_total")}
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, default=str)
print(out, "ok", [(x["source"], x.get("positions"), x.get("not_in_orders"), x["sec"]) for x in res["loads"]])
