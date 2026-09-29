"""Выборки для интерфейса: заказы, карточка заказа, лента изменений."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.engine import Engine

from .db import change_log, comments, positions, segments, snapshots

COLOR_RANK = {"red": 0, "yellow": 1, "green": 2, None: 3}
FIELD_NAMES = {
    "current_decade": "Текущая декада",
    "color": "Цвет светофора",
    "plan_ship_date": "План отгрузки",
    "required_date": "Треб. дата поставки",
    "stage": "Этап",
}


def _iso(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in r._mapping.items()}


def due_date(pos: dict):
    """Дата, к которой позицию нужно отгрузить сейчас: текущая декада светофора, иначе треб. дата."""
    return pos.get("current_decade_end") or pos.get("required_date")


STALE_DAYS = 180


def position_view(pos: dict, today: date) -> dict:
    due = due_date(pos)
    due_d = date.fromisoformat(due) if isinstance(due, str) else due
    has_segments = bool(pos.get("segments_total"))
    # Без строк в отчёте по отрезкам мы не знаем, отгружена ли позиция, поэтому просрочкой не считаем
    overdue = bool(has_segments and due_d and due_d < today and not pos.get("closed"))
    # «Хвосты»: срок прошёл больше полугода назад, а позиция так и висит открытой
    stale = bool(due_d and (today - due_d).days > STALE_DAYS and not pos.get("closed"))
    return {**{k: _iso(v) for k, v in pos.items()}, "due_date": _iso(due_d), "overdue": overdue, "stale": stale,
            "stage": pos.get("stage") or "Нет в отчёте по отрезкам"}


def list_orders(engine: Engine, *, scope: str = "open", manager: str = "", dept: str = "", color: str = "",
                q: str = "", overdue_only: bool = False, today: date | None = None) -> list[dict]:
    today = today or date.today()
    stmt = select(positions)
    if scope in ("open", "stale"):
        stmt = stmt.where(positions.c.closed.is_(False))
    if manager:
        stmt = stmt.where(positions.c.manager == manager)
    if dept:
        stmt = stmt.where(positions.c.sales_dept == dept)
    with engine.connect() as conn:
        rows = [position_view(dict(r._mapping), today) for r in conn.execute(stmt)]
        n_comments = defaultdict(int)
        for (order_no,) in conn.execute(select(comments.c.order_no)):
            n_comments[order_no] += 1

    if scope == "open":
        rows = [r for r in rows if not r["stale"]]
    elif scope == "stale":
        rows = [r for r in rows if r["stale"]]
    by_order: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_order[r["order_no"]].append(r)

    q = q.strip().lower()
    out = []
    for order_no, ps in by_order.items():
        first = next((p for p in ps if p.get("customer")), ps[0])
        colors = [p.get("color") for p in ps]
        worst = min(colors, key=lambda c: COLOR_RANK.get(c, 3))
        dues = [p["due_date"] for p in ps if p["due_date"]]
        firsts = [p["first_decade_end"] for p in ps if p.get("first_decade_end")]
        shifts = [p["shift_days"] for p in ps if p.get("shift_days") is not None]
        order = {
            "order_no": order_no,
            "customer": first.get("customer"),
            "sales_dept": first.get("sales_dept"),
            "manager": next((p["manager"] for p in ps if p.get("manager")), None),
            "positions": len(ps),
            "color": worst,
            "red": colors.count("red"), "yellow": colors.count("yellow"), "green": colors.count("green"),
            "overdue": sum(p["overdue"] for p in ps),
            "first_decade": _decade_of(ps, "first_decade", "first_decade_end", min),
            "current_decade": _decade_of(ps, "current_decade", "current_decade_end", max),
            "max_shift": max(shifts) if shifts else None,
            "nearest_due": min(dues) if dues else None,
            "earliest_first": min(firsts) if firsts else None,
            "segments_total": sum(p.get("segments_total") or 0 for p in ps),
            "segments_ready": sum((p.get("segments_ready") or 0) + (p.get("segments_shipped") or 0) for p in ps),
            "bitrix_task": next((p["bitrix_task"] for p in ps if p.get("bitrix_task")), None),
            "amount_rub": sum(p.get("amount_rub") or 0 for p in ps),
            "comments": n_comments.get(order_no, 0),
        }
        if color and (color != "none" and order["color"] != color or color == "none" and order["color"]):
            continue
        if overdue_only and not order["overdue"]:
            continue
        if q and not any(q in str(order.get(f) or "").lower() for f in ("order_no", "customer", "manager")) \
                and not any(q in str(p.get("product") or "").lower() for p in ps):
            continue
        out.append(order)

    # Сверху просроченные, потом красные, жёлтые, внутри — по величине смещения
    out.sort(key=lambda o: (0 if o["overdue"] else 1, COLOR_RANK.get(o["color"], 3), -(o["max_shift"] or 0), o["nearest_due"] or "9999"))
    return out


def _decade_of(ps, label_field, date_field, pick):
    items = [(p[date_field], p[label_field]) for p in ps if p.get(date_field)]
    if not items:
        return None
    return pick(items)[1]


def order_card(engine: Engine, order_no: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    with engine.connect() as conn:
        ps = [position_view(dict(r._mapping), today)
              for r in conn.execute(select(positions).where(positions.c.order_no == order_no))]
        if not ps:
            return None
        segs = defaultdict(list)
        for r in conn.execute(select(segments).where(segments.c.order_no == order_no)):
            segs[r.pos].append(_row(r))
        changes = [_row(r) for r in conn.execute(
            select(change_log).where(change_log.c.order_no == order_no).order_by(desc(change_log.c.at), desc(change_log.c.id)))]
        notes = [_row(r) for r in conn.execute(
            select(comments).where(comments.c.order_no == order_no).order_by(desc(comments.c.created_at)))]
    ps.sort(key=lambda p: _pos_sort(p["pos"]))
    for p in ps:
        p["segments"] = sorted(segs.get(p["pos"], []), key=lambda s: _pos_sort(s["seg_no"]))
    for c in changes:
        c["field_name"] = FIELD_NAMES.get(c["field"], c["field"])
    return {"order_no": order_no, "positions": ps, "changes": changes, "comments": notes}


def _pos_sort(v):
    try:
        return (0, int(v))
    except (TypeError, ValueError):
        return (1, str(v))


def recent_changes(engine: Engine, days: int = 7, manager: str = "", limit: int = 500) -> list[dict]:
    since = datetime.now() - timedelta(days=days)
    stmt = (select(change_log, positions.c.customer, positions.c.manager, positions.c.product)
            .join(positions, (positions.c.order_no == change_log.c.order_no) & (positions.c.pos == change_log.c.pos))
            .where(change_log.c.at >= since)
            .order_by(desc(change_log.c.at), desc(change_log.c.id)).limit(limit))
    if manager:
        stmt = stmt.where(positions.c.manager == manager)
    with engine.connect() as conn:
        out = [_row(r) for r in conn.execute(stmt)]
    for c in out:
        c["field_name"] = FIELD_NAMES.get(c["field"], c["field"])
    return out


def meta(engine: Engine) -> dict:
    with engine.connect() as conn:
        managers = sorted({r[0] for r in conn.execute(select(positions.c.manager).where(positions.c.closed.is_(False))) if r[0]})
        depts = sorted({r[0] for r in conn.execute(select(positions.c.sales_dept).where(positions.c.closed.is_(False))) if r[0]})
        last = {r.source: _iso(r.loaded_at) for r in conn.execute(
            select(snapshots.c.source, snapshots.c.loaded_at).order_by(snapshots.c.loaded_at))}
    return {"managers": managers, "depts": depts, "last_load": last, "today": date.today().isoformat()}
