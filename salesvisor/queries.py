"""Выборки для интерфейса: заказы, карточка заказа, лента изменений."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import desc, or_, select
from sqlalchemy.engine import Engine

from .db import bitrix_links, change_log, comments, dispatcher, positions, segments, snapshots

COLOR_RANK = {"red": 0, "yellow": 1, "green": 2, None: 3}
FIELD_NAMES = {
    "current_decade": "Текущая декада",
    "color": "Цвет светофора",
    "plan_ship_date": "План отгрузки",
    "required_date": "Треб. дата поставки",
    "stage": "Этап",
    "line": "Линия",
    "plan_end_date": "План. окончание производства",
    "disp_decade": "Декада в диспетчерском",
    "disp_ready": "Готовность в диспетчерском",
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
        links = {r.order_no: r for r in conn.execute(select(bitrix_links))}

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
            "bitrix_task": _effective_task(links.get(order_no), ps),
            "bitrix_deal": links[order_no].deal_id if order_no in links else None,
            "amount_rub": sum(p.get("amount_rub") or 0 for p in ps),
            "comments": n_comments.get(order_no, 0),
        }
        if color and color != "nolink" and (color != "none" and order["color"] != color or color == "none" and order["color"]):
            continue
        if color == "nolink":
            if order["bitrix_task"] or order["bitrix_deal"]:
                continue
        elif overdue_only and not order["overdue"]:
            continue
        if q and not any(q in str(order.get(f) or "").lower() for f in ("order_no", "customer", "manager")) \
                and not any(q in str(p.get("product") or "").lower() for p in ps):
            continue
        out.append(order)

    # Сверху просроченные, потом красные, жёлтые, внутри — по величине смещения
    out.sort(key=lambda o: (0 if o["overdue"] else 1, COLOR_RANK.get(o["color"], 3), -(o["max_shift"] or 0), o["nearest_due"] or "9999"))
    return out


def _effective_task(link, ps):
    if link is not None and link.task_id:
        return link.task_id
    return next((p["bitrix_task"] for p in ps if p.get("bitrix_task")), None)


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
        link = conn.execute(select(bitrix_links).where(bitrix_links.c.order_no == order_no)).first()
    ps.sort(key=lambda p: _pos_sort(p["pos"]))
    for p in ps:
        p["segments"] = sorted(segs.get(p["pos"], []), key=lambda s: _pos_sort(s["seg_no"]))
    for c in changes:
        c["field_name"] = FIELD_NAMES.get(c["field"], c["field"])
    sap_task = next((p["bitrix_task"] for p in ps if p.get("bitrix_task")), None)
    bitrix = {
        "sap_task": sap_task,
        "sap_raw": next((p["bitrix_raw"] for p in ps if p.get("bitrix_raw")), None),
        "task_id": _effective_task(link, ps),
        "deal_id": link.deal_id if link else None,
        "manual": _row(link) if link else None,
    }
    return {"order_no": order_no, "positions": ps, "changes": changes, "comments": notes, "bitrix": bitrix}


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
        lines = sorted({r[0] for r in conn.execute(select(positions.c.line).where(positions.c.line.is_not(None))) if r[0]})
    return {"managers": managers, "depts": depts, "lines": lines, "last_load": last, "today": date.today().isoformat()}


def _produced(p: dict) -> bool:
    total = p.get("segments_total") or 0
    return bool(total) and (p.get("segments_produced") or 0) >= total


def _shipped(p: dict) -> bool:
    total = p.get("segments_total") or 0
    return bool(p.get("closed")) or bool(total) and (p.get("segments_shipped") or 0) >= total


def day_plan(engine: Engine, day: date, *, manager: str = "", dept: str = "", line: str = "",
             with_backlog: bool = False, today: date | None = None) -> dict:
    """Что по плану должно случиться в этот день: окончание производства (план производства, линия)
    и отгрузка (план отгрузки светофора, иначе план дата отгрузки фактуры из отчёта по отрезкам).
    С with_backlog добавляются невыполненные позиции с плановой датой раньше дня."""
    today = today or date.today()
    ship_col = positions.c.plan_ship_date
    inv_col = positions.c.invoice_plan_date
    if with_backlog:
        cond = or_(positions.c.plan_end_date <= day, ship_col <= day, (ship_col.is_(None)) & (inv_col <= day))
    else:
        cond = or_(positions.c.plan_end_date == day, ship_col == day, (ship_col.is_(None)) & (inv_col == day))
    stmt = select(positions).where(cond)
    if manager:
        stmt = stmt.where(positions.c.manager == manager)
    if dept:
        stmt = stmt.where(positions.c.sales_dept == dept)
    if line:
        stmt = stmt.where(positions.c.line == line)
    with engine.connect() as conn:
        rows = [position_view(dict(r._mapping), today) for r in conn.execute(stmt)]
    out = []
    for r in rows:
        ship_plan = r.get("plan_ship_date") or r.get("invoice_plan_date")
        make = bool(r.get("plan_end_date")) and (r["plan_end_date"] <= day.isoformat() if with_backlog
                                                 else r["plan_end_date"] == day.isoformat())
        ship = bool(ship_plan) and (ship_plan <= day.isoformat() if with_backlog else ship_plan == day.isoformat())
        make_done, ship_done = _produced(r), _shipped(r)
        if with_backlog:
            # Из прошлых дней берём только невыполненное; сам день показываем целиком
            make = make and (r["plan_end_date"] == day.isoformat() or not make_done)
            ship = ship and (ship_plan == day.isoformat() or not ship_done)
        if not (make or ship):
            continue
        out.append({**r, "ship_plan": ship_plan, "task_make": make, "task_ship": ship,
                    "make_done": make_done, "ship_done": ship_done,
                    "late": (make and not make_done and r["plan_end_date"] < day.isoformat())
                            or (ship and not ship_done and ship_plan < day.isoformat())})
    out.sort(key=lambda r: (r.get("line") or "яяя", r.get("plan_end_date") or "", r.get("plan_end_time") or "",
                            r["order_no"], _pos_sort(r["pos"])))
    make = [r for r in out if r["task_make"]]
    ship = [r for r in out if r["task_ship"]]
    summary = {
        "positions": len(out),
        "make": len(make), "make_done": sum(r["make_done"] for r in make),
        "ship": len(ship), "ship_done": sum(r["ship_done"] for r in ship),
        "late": sum(r["late"] for r in out),
        "no_line": sum(1 for r in out if not r.get("line")),
    }
    by_line: dict[str, dict] = {}
    for r in make:
        b = by_line.setdefault(r.get("line") or "", {"line": r.get("line"), "make": 0, "make_done": 0, "km": 0.0})
        b["make"] += 1
        b["make_done"] += r["make_done"]
        if (r.get("unit") or "").upper() in ("КМ", "KM"):
            b["km"] += r.get("length_plan") or 0
    return {"date": day.isoformat(), "summary": summary, "lines": sorted(by_line.values(), key=lambda b: b["line"] or "яяя"),
            "positions": out}


DISP_MEASURES = ["km", "pcs", "ov_km", "mz", "vp"]


def _month_of(label: str) -> str:
    parts = (label or "").split()
    return parts[1] if len(parts) > 1 else ""


def dispatcher_summary(engine: Engine, *, decade: str = "", manager: str = "", dept: str = "") -> dict:
    """План и факт по декадам, как в листе «отчет (итог)» диспетчерского отчёта, плюс позиции выбранной декады."""
    stmt = (select(dispatcher, positions.c.customer.label("pos_customer"), positions.c.manager, positions.c.line,
                   positions.c.plan_end_date, positions.c.stage, positions.c.sales_dept,
                   positions.c.segments_total, positions.c.segments_produced)
            .select_from(dispatcher.outerjoin(positions, (positions.c.order_no == dispatcher.c.order_no)
                                              & (positions.c.pos == dispatcher.c.pos))))
    if manager:
        stmt = stmt.where(positions.c.manager == manager)
    if dept:
        stmt = stmt.where(positions.c.sales_dept == dept)
    with engine.connect() as conn:
        rows = [_row(r) for r in conn.execute(stmt)]
    for r in rows:
        r["mismatch"] = _disp_mismatch(r)
        r["customer"] = r.pop("pos_customer") or r["customer"]
    counted = [r for r in rows if r["counted"] and r["decade_no"] is not None]
    by_dec: dict[int, dict] = {}
    for r in counted:
        d = by_dec.setdefault(r["decade_no"], {"decade": r["decade"], "decade_no": r["decade_no"], "month": _month_of(r["decade"]),
                                               "positions": 0, "positions_ready": 0, "mismatch": 0,
                                               **{m: 0.0 for m in DISP_MEASURES}, **{f"{m}_ready": 0.0 for m in DISP_MEASURES}})
        d["positions"] += 1
        d["positions_ready"] += int(r["segs_ready"] == r["segs"])
        d["mismatch"] += int(bool(r["mismatch"]) and r["mismatch"] != NOT_IN_SEGMENTS)
        for m in DISP_MEASURES:
            d[m] += r[m] or 0
            d[f"{m}_ready"] += r[f"{m}_ready"] or 0
    decades = [by_dec[k] for k in sorted(by_dec)]
    months: list[dict] = []
    for d in decades:
        if not months or months[-1]["month"] != d["month"]:
            months.append({"month": d["month"], "decades": [], **{m: 0.0 for m in DISP_MEASURES},
                           **{f"{m}_ready": 0.0 for m in DISP_MEASURES}})
        mo = months[-1]
        mo["decades"].append(d)
        for m in DISP_MEASURES:
            mo[m] += d[m]
            mo[f"{m}_ready"] += d[f"{m}_ready"]
    total = {m: sum(d[m] for d in decades) for m in DISP_MEASURES}
    total.update({f"{m}_ready": sum(d[f"{m}_ready"] for d in decades) for m in DISP_MEASURES})
    detail = []
    if decade:
        detail = [r for r in rows if r["decade"] == decade]
        # Сверху расхождения, потом не готовые из плана декады, затем остальное
        detail.sort(key=lambda r: (not r["mismatch"] or r["mismatch"] == NOT_IN_SEGMENTS, not r["counted"],
                                   r["segs_ready"] == r["segs"], -(r["mz"] or 0)))
    return {"months": months, "total": total, "decade": decade, "positions": detail[:2000],
            "not_counted": sum(1 for r in rows if not r["counted"]),
            "no_decade": sum(1 for r in rows if r["counted"] and r["decade_no"] is None),
            "loaded": bool(rows), "mismatch": sum(1 for r in counted if r["mismatch"] and r["mismatch"] != NOT_IN_SEGMENTS)}


NOT_IN_SEGMENTS = "нет в отчёте по отрезкам"


def _disp_mismatch(r: dict) -> str | None:
    """Сверка с отчётом по отрезкам: партия назначена, а отрезки не произведены, и наоборот."""
    total = r.get("segments_total")
    if not total:
        return NOT_IN_SEGMENTS if r.get("pos_customer") is None else None
    produced = (r.get("segments_produced") or 0) >= total
    ready = r["segs_ready"] == r["segs"]
    if ready and not produced:
        return "в диспетчерском готов, по отрезкам не произведён"
    if produced and not ready:
        return "по отрезкам произведён, в диспетчерском не готов"
    return None
