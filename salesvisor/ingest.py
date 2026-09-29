"""Загрузка выгрузок светофора и отчёта по отрезкам в базу с журналом изменений."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
from sqlalchemy import bindparam, delete, insert, select, update
from sqlalchemy.engine import Engine

from . import parsing as p
from .db import change_log, positions, segments, snapshots

SVETOFOR_REQUIRED = ["Заказ", "Позиция", "Первая декада", "Текущая декада"]
SEGMENTS_REQUIRED = ["Заказ клиента", "Позиция заказа клиента", "Номер отрезка по порядку в позиции", "Треб. дата поставки"]

# Какие поля позиции отслеживаем между выгрузками
TRACKED_SVETOFOR = ["current_decade", "color", "plan_ship_date"]
TRACKED_SEGMENTS = ["required_date", "stage"]

STAGES = [
    # (поле-счётчик, название этапа, когда все отрезки дошли до него)
    ("segments_invoiced", "Отфактурирован"),
    ("segments_shipped", "Отгружен"),
    ("segments_in_transit", "В пути"),
    ("segments_ready", "Готов к отгрузке"),
    ("segments_stock", "На складе"),
    ("segments_produced", "Произведён"),
]


def read_table(data: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=object)
    else:
        text = _decode(data)
        df = pd.read_csv(io.StringIO(text), dtype=str, sep=None, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def _key(v) -> str | None:
    """Номер заказа или позиции: «10», 10, 10.0 → «10»."""
    s = p.text(v)
    if s is None:
        return None
    s = s.replace("\u00a0", "").replace(" ", "")  # в CSV из Metabase «1 010»
    try:
        f = float(s.replace(",", "."))
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def _check_columns(df: pd.DataFrame, required: list[str], what: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"В выгрузке «{what}» нет колонок: {', '.join(missing)}")


def _fmt(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


# ---------------------------------------------------------------- светофор

def parse_svetofor(df: pd.DataFrame) -> list[dict]:
    _check_columns(df, SVETOFOR_REQUIRED, "Светофор")
    rows = []
    for r in df.to_dict("records"):
        order_no, pos = _key(r.get("Заказ")), _key(r.get("Позиция"))
        if not order_no or not pos:
            continue
        plan = p.parse_date(r.get("ПланДатаОтгрузки"))
        z4 = p.parse_date(r.get("Дата перевода Z4"))
        first, first_end = p.parse_decade(r.get("Первая декада"), near=z4 or plan)
        cur, cur_end = p.parse_decade(r.get("Текущая декада"), near=plan)
        rows.append({
            "order_no": order_no, "pos": pos,
            "customer": p.text(r.get("Заказчик")),
            "sales_dept": p.text(r.get("Отдел продаж")),
            "product": p.text(r.get("ГП")),
            "plan_ship_date": plan, "z4_date": z4,
            "first_decade": first, "first_decade_end": first_end,
            "current_decade": cur, "current_decade_end": cur_end,
            "shift_days": p.parse_int(r.get("Смещено дней")),
            "color": (p.text(r.get("Цвет")) or "").lower() or None,
            "in_svetofor": True,
        })
    return rows


# ---------------------------------------------------------------- отрезки

def parse_segments(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Возвращает (отрезки, сводку по позициям)."""
    _check_columns(df, SEGMENTS_REQUIRED, "Отчёт по отрезкам")
    product_col = "Материал.1" if "Материал.1" in df.columns else "Материал"
    amount_col = next((c for c in df.columns if c.startswith("СУММА ЗАКАЗА, РУБ. (СУММА ВАЛ")), None)

    segs: list[dict] = []
    agg: dict[tuple[str, str], dict] = {}
    for r in df.to_dict("records"):
        order_no, pos = _key(r.get("Заказ клиента")), _key(r.get("Позиция заказа клиента"))
        if not order_no or not pos:
            continue
        seg = {
            "order_no": order_no, "pos": pos,
            "seg_no": _key(r.get("Номер отрезка по порядку в позиции")) or "1",
            "length": p.parse_number(r.get("Длина отдельного отрезка")),
            "unit": p.text(r.get("ЕИ")),
            "fact_length": p.parse_number(r.get("Фактическая длина")),
            "produced": p.parse_flag(r.get("Статус Произведен")),
            "stock": p.parse_flag(r.get("Статус На складе")),
            "ready": p.parse_flag(r.get("Статус Готов к отгрузке")),
            "in_transit": p.parse_flag(r.get("Статус В пути")),
            "shipped": p.parse_flag(r.get("Статус Отгружен")),
            "invoiced": p.parse_flag(r.get("Статус Отфактурирован")),
            "fact_ship_date": p.parse_date(r.get("Факт. дата поставки")),
            "prod_order": _key(r.get("Заказ на производство")),
            "warehouse": p.text(r.get("Наименование склада отгрузки")),
        }
        segs.append(seg)

        a = agg.get((order_no, pos))
        if a is None:
            raw_task = p.text(r.get("Номер задачи в Битрикс"))
            a = agg[(order_no, pos)] = {
                "order_no": order_no, "pos": pos,
                "customer": p.text(r.get("Имя заказчика")),
                "sales_dept": p.text(r.get("Описание отдела сбыта")),
                "manager": p.text(r.get("Создал")),
                "product": p.text(r.get(product_col)),
                "required_date": p.parse_date(r.get("Треб. дата поставки")),
                "invoice_plan_date": p.parse_date(r.get("План дата отгрузки (фактуры)")),
                "to_production_date": p.parse_date(r.get("Дата передачи в производство")),
                "reject_code": p.text(r.get("Причина отклонения")),
                "reject_text": p.text(r.get("Описание Причины отклонения")),
                "unit": seg["unit"],
                "bitrix_raw": raw_task,
                "bitrix_task": p.parse_bitrix_task(raw_task),
                "segments_total": 0, "segments_produced": 0, "segments_stock": 0, "segments_ready": 0,
                "segments_in_transit": 0, "segments_shipped": 0, "segments_invoiced": 0,
                "length_plan": 0.0, "amount_rub": 0.0, "last_fact_ship_date": None,
            }
        a["segments_total"] += 1
        for flag in ("produced", "stock", "ready", "in_transit", "shipped", "invoiced"):
            a[f"segments_{flag}"] += int(seg[flag])
        a["length_plan"] += seg["length"] or 0
        if amount_col:
            a["amount_rub"] += p.parse_number(r.get(amount_col)) or 0
        if seg["fact_ship_date"] and (not a["last_fact_ship_date"] or seg["fact_ship_date"] > a["last_fact_ship_date"]):
            a["last_fact_ship_date"] = seg["fact_ship_date"]

    for a in agg.values():
        a["stage"] = stage_of(a)
        total = a["segments_total"]
        a["closed"] = total > 0 and (a["segments_shipped"] == total or a["segments_invoiced"] == total)
    return segs, list(agg.values())


def stage_of(a: dict) -> str:
    total = a.get("segments_total") or 0
    if not total:
        return "Нет отрезков"
    for field, name in STAGES:
        if (a.get(field) or 0) >= total:
            return name
    produced = a.get("segments_produced") or 0
    if produced:
        return f"В производстве {produced}/{total}"
    return "Не произведён"


# ---------------------------------------------------------------- запись в базу

def _load_existing(conn, keys: set[tuple[str, str]], fields: list[str]) -> dict:
    cols = [positions.c.order_no, positions.c.pos] + [positions.c[f] for f in fields]
    out = {}
    for row in conn.execute(select(*cols)):
        k = (row[0], row[1])
        if k in keys:
            out[k] = dict(zip(fields, row[2:]))
    return out


def _upsert(conn, rows: list[dict], tracked: list[str], snapshot_id: int) -> dict:
    keys = {(r["order_no"], r["pos"]) for r in rows}
    existing = _load_existing(conn, keys, tracked)
    now = datetime.now()
    to_insert, to_update, changes = [], [], []
    for r in rows:
        k = (r["order_no"], r["pos"])
        r = {**r, "updated_at": now}
        if k not in existing:
            to_insert.append({**r, "first_seen_at": now})
            continue
        old = existing[k]
        for f in tracked:
            if f in r and r[f] is not None and old.get(f) != r[f] and old.get(f) is not None:
                changes.append({"order_no": k[0], "pos": k[1], "field": f, "old": _fmt(old[f]),
                                "new": _fmt(r[f]), "snapshot_id": snapshot_id, "at": now})
        to_update.append(r)

    if to_insert:
        # В одной пачке у всех строк должен быть одинаковый набор полей
        cols = sorted({c for r in to_insert for c in r})
        conn.execute(insert(positions), [{c: r.get(c) for c in cols} for r in to_insert])
    if to_update:
        fields = sorted({c for r in to_update for c in r} - {"order_no", "pos"})
        stmt = (update(positions)
                .where(positions.c.order_no == bindparam("k_order"), positions.c.pos == bindparam("k_pos"))
                .values({f: bindparam(f) for f in fields}))
        conn.execute(stmt, [{"k_order": r["order_no"], "k_pos": r["pos"], **{f: r.get(f) for f in fields}} for r in to_update])
    if changes:
        conn.execute(insert(change_log), changes)
    return {"new": len(to_insert), "updated": len(to_update), "changes": len(changes)}


def _snapshot(conn, source: str, origin: str, rows: int) -> int:
    res = conn.execute(insert(snapshots).values(source=source, origin=origin, rows=rows, loaded_at=datetime.now()))
    return res.inserted_primary_key[0]


def load_svetofor(engine: Engine, df: pd.DataFrame, origin: str) -> dict:
    rows = parse_svetofor(df)
    with engine.begin() as conn:
        sid = _snapshot(conn, "svetofor", origin, len(df))
        # Позиции, которых нет в свежем светофоре, помечаем, но не удаляем
        conn.execute(update(positions).values(in_svetofor=False))
        stats = _upsert(conn, rows, TRACKED_SVETOFOR, sid)
        _fill_order_managers(conn)
    return {"source": "svetofor", "rows": len(df), "positions": len(rows), **stats}


def load_segments(engine: Engine, df: pd.DataFrame, origin: str) -> dict:
    segs, pos_rows = parse_segments(df)
    with engine.begin() as conn:
        sid = _snapshot(conn, "segments", origin, len(df))
        conn.execute(delete(segments))
        for i in range(0, len(segs), 5000):
            conn.execute(insert(segments), segs[i:i + 5000])
        stats = _upsert(conn, pos_rows, TRACKED_SEGMENTS, sid)
        _fill_order_managers(conn)
    return {"source": "segments", "rows": len(df), "positions": len(pos_rows), **stats}


def _fill_order_managers(conn) -> None:
    """В светофоре нет менеджера: берём его с других позиций того же заказа."""
    by_order: dict[str, str] = {}
    missing = defaultdict(list)
    for order_no, pos, manager in conn.execute(select(positions.c.order_no, positions.c.pos, positions.c.manager)):
        if manager:
            by_order.setdefault(order_no, manager)
        else:
            missing[order_no].append(pos)
    params = [{"k_order": o, "k_pos": ps, "manager": by_order[o]} for o, lst in missing.items() if o in by_order for ps in lst]
    if params:
        stmt = (update(positions)
                .where(positions.c.order_no == bindparam("k_order"), positions.c.pos == bindparam("k_pos"))
                .values(manager=bindparam("manager")))
        conn.execute(stmt, params)


def load_file(engine: Engine, source: str, data: bytes, filename: str) -> dict:
    df = read_table(data, filename)
    if source == "svetofor":
        return load_svetofor(engine, df, filename)
    if source == "segments":
        return load_segments(engine, df, filename)
    raise ValueError("Неизвестный источник")


def detect_source(df_columns: list[str]) -> str | None:
    cols = set(df_columns)
    if set(SEGMENTS_REQUIRED) <= cols:
        return "segments"
    if set(SVETOFOR_REQUIRED) <= cols:
        return "svetofor"
    return None
