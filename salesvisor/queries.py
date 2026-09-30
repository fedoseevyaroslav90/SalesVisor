"""Выборки для интерфейса: заказы, карточка заказа, лента изменений."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import desc, func, or_, select
from sqlalchemy.engine import Engine

from .db import bitrix_links, change_log, comments, dispatcher, positions, quality_msgs, segments, snapshots

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
    "quality": "Несоответствие по качеству",
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
    # Опоздание к обещанию клиенту: max(срок сейчас, сегодня) − первая дата клиента (конец первой декады).
    # Приоритет — МП × дни опоздания («Критичные заказы», 10–11.06.2026): чем дороже и дольше, тем выше
    first = pos.get("first_decade_end")
    first_d = date.fromisoformat(first) if isinstance(first, str) else first
    late = max(((max(due_d, today) if due_d else today) - first_d).days, 0) if first_d and not pos.get("closed") else 0
    return {**{k: _iso(v) for k, v in pos.items()}, "due_date": _iso(due_d), "overdue": overdue, "stale": stale,
            "stage": pos.get("stage") or "Нет в отчёте по отрезкам", "days_late": late,
            "priority": round((pos.get("mp_rub") or 0) * late)}


def vals(v: str | None, codes: bool = False) -> list[str]:
    """Значение фильтра «из нескольких»: «A|B» → [A, B]; для кодов — и «A,B»."""
    parts = (v or "").replace(",", "|").split("|") if codes else (v or "").split("|")
    return [x.strip() for x in parts if x.strip()]


def _in(col, v: str):
    """Условие «колонка в списке» для фильтра менеджера, отдела, линии (одно значение — как раньше)."""
    items = vals(v)
    return col == items[0] if len(items) == 1 else col.in_(items)


def _load_positions(engine: Engine, scope: str, manager: str, dept: str, today: date):
    """Позиции под область и фильтры менеджера/отдела + счётчики комментариев, связи Битрикс24 и сообщения о качестве."""
    stmt = select(positions)
    if scope in ("open", "stale"):
        stmt = stmt.where(positions.c.closed.is_(False))
    if vals(manager):
        stmt = stmt.where(_in(positions.c.manager, manager))
    if vals(dept):
        stmt = stmt.where(_in(positions.c.sales_dept, dept))
    with engine.connect() as conn:
        rows = [position_view(dict(r._mapping), today) for r in conn.execute(stmt)]
        n_comments = defaultdict(int)
        for (order_no,) in conn.execute(select(comments.c.order_no)):
            n_comments[order_no] += 1
        links = {r.order_no: r for r in conn.execute(select(bitrix_links))}
        n_quality = defaultdict(int)
        for order_no, pos in conn.execute(select(quality_msgs.c.order_no, quality_msgs.c.pos)):
            n_quality[order_no] += 1
            n_quality[(order_no, pos)] += 1
    if scope == "open":
        rows = [r for r in rows if not r["stale"]]
    elif scope == "stale":
        rows = [r for r in rows if r["stale"]]
    return rows, n_comments, links, n_quality


# Группы этапов для отбора: сам этап у позиции — строка вроде «В производстве 3/5»
STAGE_GROUPS = {"not_made": "Не произведено", "in_prod": "В производстве", "made": "Произведено", "stock": "На складе",
                "ready": "Готово к отгрузке", "transit": "В пути", "shipped": "Отгружено", "none": "Нет в отчёте по отрезкам"}
_STAGE_OF = {"не произведён": "not_made", "произведён": "made", "на складе": "stock", "готов к отгрузке": "ready",
             "в пути": "transit", "отгружен": "shipped", "отфактурирован": "shipped"}


def stage_group(stage: str | None) -> str:
    s = (stage or "").strip().lower()
    if s.startswith("в производстве"):
        return "in_prod"
    return _STAGE_OF.get(s, "none")


# ГОЗ и ВПК — по префиксу номенклатуры, как в аналитике продаж (ГОЗ, ГОЗ12…, ВПК, TEA)
_GOZ = re.compile(r"^\s*(ГОЗ|ВПК|TEA)", re.IGNORECASE)
# внутренние заказы площадки: ООО «Инкаб» (Дальний Восток и другие юрлица группы — не внутренние, решение пилота)
_INKAB = re.compile(r"^\s*ООО\s*[\"«]?\s*Инкаб\s*[\"»]?\s*$", re.IGNORECASE)


def _date(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


@dataclass
class Filters:
    """Отбор на уровне позиции (одинаковый для заказов, позиций и выгрузки в Excel). Пустое поле — не отбирать.
    Заказ попадает в список, если под отбор подошла хотя бы одна его позиция; считается он по подошедшим позициям."""
    q: str = ""                   # заказ, клиент, менеджер, изделие — подстрока
    order: str = ""               # номера заказов через «|» — позиции развёрнутых заказов
    customer: str = ""            # клиент — подстрока
    line: str = ""                # линия (рабочее место); «-» — без линии
    stage: str = ""               # группа этапа, см. STAGE_GROUPS; несколько — через запятую
    color: str = ""               # red | yellow | green | none; несколько — через запятую
    due_from: date | None = None  # срок сейчас (текущая декада светофора, иначе треб. дата) — с
    due_to: date | None = None    # — по
    first_from: date | None = None  # первая обещанная клиенту дата — с
    first_to: date | None = None    # — по
    shift_min: int | None = None  # смещение от первой даты, дней, не меньше
    reject: str = ""              # причина отклонения SAP: коды через запятую, «-» — пусто; «!» в начале — кроме них
    flags: frozenset = frozenset()  # overdue, quality, nolink, comments, noline, plan (есть в плане производства),
                                    # goz (ГОЗ/ВПК по изделию), noinkab (без внутренних ООО «Инкаб»)

    @classmethod
    def from_query(cls, **kw) -> "Filters":
        shift = str(kw.get("shift_min") or "").strip()
        return cls(q=(kw.get("q") or "").strip().lower(), order=(kw.get("order") or "").strip(),
                   customer=(kw.get("customer") or "").strip().lower(),
                   line=(kw.get("line") or "").strip(), stage=(kw.get("stage") or "").strip(),
                   color=(kw.get("color") or "").strip(),
                   due_from=_date(kw.get("due_from")), due_to=_date(kw.get("due_to")),
                   first_from=_date(kw.get("first_from")), first_to=_date(kw.get("first_to")),
                   shift_min=int(shift) if shift.lstrip("-").isdigit() else None,
                   reject=(kw.get("reject") or "").strip().upper(),
                   flags=frozenset(f for f in (kw.get("flags") or "").split(",") if f))

    def match(self, p: dict) -> bool:
        if self.order and p.get("order_no") not in vals(self.order):
            return False
        if self.q and not any(self.q in str(p.get(f) or "").lower() for f in ("order_no", "customer", "manager", "product")):
            return False
        if self.customer and self.customer not in str(p.get("customer") or "").lower():
            return False
        if self.line and (p.get("line") or "-") not in vals(self.line):
            return False
        if self.stage and stage_group(p.get("stage")) not in vals(self.stage, codes=True):
            return False
        if self.color and (p.get("color") or "none") not in self.color.split(","):
            return False
        due, first = _date(p.get("due_date")), _date(p.get("first_decade_end"))
        if self.due_from and not (due and due >= self.due_from) or self.due_to and not (due and due <= self.due_to):
            return False
        if self.first_from and not (first and first >= self.first_from) or self.first_to and not (first and first <= self.first_to):
            return False
        if self.shift_min is not None and (p.get("shift_days") or 0) < self.shift_min:
            return False
        if self.reject:
            codes = vals(self.reject.lstrip("!"), codes=True)
            if ((p.get("reject_code") or "-").upper() in codes) == self.reject.startswith("!"):
                return False
        f = self.flags
        return not (("overdue" in f and not p["overdue"]) or ("quality" in f and not p["quality"])
                    or ("nolink" in f and (p["bitrix_task"] or p["bitrix_deal"])) or ("comments" in f and not p["comments"])
                    or ("noline" in f and p.get("line")) or ("plan" in f and not p.get("line"))
                    or ("goz" in f and not _GOZ.match(p.get("product") or ""))
                    or ("noinkab" in f and _INKAB.match(p.get("customer") or "")))


# Поля позиции для ленты «Позиции» и выгрузки — только то, что показывает и по чему отбирает интерфейс
POSITION_FIELDS = ("order_no", "pos", "customer", "sales_dept", "manager", "product", "color", "first_decade",
                   "first_decade_end", "current_decade", "current_decade_end", "due_date", "shift_days", "required_date",
                   "plan_ship_date", "invoice_plan_date", "line", "plan_end_date", "plan_end_time", "stage",
                   "segments_total", "segments_ready", "overdue", "stale", "disp_decade", "disp_ready", "length_plan",
                   "unit", "amount_rub", "quality", "comments", "bitrix_task", "bitrix_deal", "reject_code", "reject_text",
                   "mp_rub", "mz_rub", "days_late", "priority")


def _filtered_positions(engine: Engine, scope: str, manager: str, dept: str, f: Filters, today: date) -> list[dict]:
    """Позиции под область, менеджера, отдел и отбор; у каждой — качество, комментарии и связь с Битрикс24."""
    rows, n_comments, links, n_quality = _load_positions(engine, scope, manager, dept, today)
    out = []
    for p in rows:
        link = links.get(p["order_no"])
        p["segments_ready"] = (p.get("segments_ready") or 0) + (p.get("segments_shipped") or 0)
        p["quality"] = n_quality.get((p["order_no"], p["pos"]), 0)
        p["comments"] = n_comments.get(p["order_no"], 0)
        p["bitrix_task"] = link.task_id if link is not None and link.task_id else p.get("bitrix_task")
        p["bitrix_deal"] = link.deal_id if link is not None else None
        if f.match(p):
            out.append(p)
    return out


def _sort_key(field: str):
    """Ключ сортировки по полю: пустые значения всегда в конце (для обоих направлений — см. sort_rows)."""
    if field == "pos":
        return lambda r: _pos_sort(r.get("pos"))
    if field == "color":
        return lambda r: COLOR_RANK.get(r.get("color"), 3)
    if field == "ready":
        return lambda r: (r.get("segments_ready") or 0) / r["segments_total"] if r.get("segments_total") else -1
    return lambda r: r.get(field)


def sort_rows(rows: list[dict], sort: str, default) -> list[dict]:
    """sort = «поле» или «-поле» (по убыванию); пустые значения — в конце при любом направлении."""
    field, reverse = (sort[1:], True) if sort.startswith("-") else (sort, False)
    if not field:
        return sorted(rows, key=default)
    key = _sort_key(field)
    filled = [r for r in rows if key(r) not in (None, "")]
    empty = [r for r in rows if key(r) in (None, "")]
    try:
        filled.sort(key=key, reverse=reverse)
    except TypeError:  # разнотипные значения — сравниваем как строки
        filled.sort(key=lambda r: str(key(r)), reverse=reverse)
    return filled + empty


def _position_default_order(r):
    return (0 if r["overdue"] else 1, COLOR_RANK.get(r["color"], 3), -(r["shift_days"] or 0),
            r["due_date"] or "9999", r["order_no"], _pos_sort(r["pos"]))


def list_positions(engine: Engine, *, scope: str = "open", manager: str = "", dept: str = "", filters: Filters | None = None,
                   sort: str = "", offset: int = 0, limit: int = 300, today: date | None = None) -> dict:
    """Лента позиций для отбора на уровне позиции: сервер отбирает, сортирует и отдаёт страницу (все открытые
    позиции — десятки тысяч строк, целиком в браузер не отдаём)."""
    today = today or date.today()
    rows = _filtered_positions(engine, scope, manager, dept, filters or Filters(), today)
    rows = sort_rows(rows, sort, _position_default_order)
    page = [{f: _iso(r.get(f)) for f in POSITION_FIELDS} for r in rows[offset:offset + limit]]
    return {"total": len(rows), "offset": offset, "rows": page}


def list_orders(engine: Engine, *, scope: str = "open", manager: str = "", dept: str = "", color: str = "",
                q: str = "", overdue_only: bool = False, filters: Filters | None = None,
                today: date | None = None) -> list[dict]:
    today = today or date.today()
    f = filters or Filters(q=q.strip().lower())
    rows = _filtered_positions(engine, scope, manager, dept, f, today)
    by_order: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_order[r["order_no"]].append(r)

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
            "segments_ready": sum(p["segments_ready"] for p in ps),
            "bitrix_task": next((p["bitrix_task"] for p in ps if p.get("bitrix_task")), None),
            "bitrix_deal": first.get("bitrix_deal"),
            "amount_rub": sum(p.get("amount_rub") or 0 for p in ps),
            # финансы заказа складываются по позициям; приоритет заказа — МП заказа × опоздание худшей позиции
            "mp_rub": sum(p.get("mp_rub") or 0 for p in ps) if any(p.get("mp_rub") is not None for p in ps) else None,
            "mz_rub": sum(p.get("mz_rub") or 0 for p in ps) if any(p.get("mz_rub") is not None for p in ps) else None,
            "days_late": max(p["days_late"] for p in ps),
            "ready_positions": sum(1 for p in ps if (p.get("segments_total") or 0) and p["segments_ready"] >= p["segments_total"]),
            "comments": first["comments"],
            "quality": sum(p["quality"] for p in ps),
            "lines": sorted({p["line"] for p in ps if p.get("line")}),
            "stages": sorted({p["stage"] for p in ps if p.get("stage")}),
        }
        if overdue_only and not order["overdue"] or color and not _tile_match(order, color, today):
            continue
        out.append(order)

    # Сверху просроченные, потом красные, жёлтые, внутри — по величине смещения
    for o in out:
        o["priority"] = round((o["mp_rub"] or 0) * o["days_late"])
    out.sort(key=lambda o: (0 if o["overdue"] else 1, COLOR_RANK.get(o["color"], 3), -(o["max_shift"] or 0), o["nearest_due"] or "9999"))
    return out


def _tile_match(o: dict, tile: str, today: date) -> bool:
    """Плитки списка заказов — как в интерфейсе: цвет, без светофора, без Битрикс24, несоответствия, срок ≤ 7 дней."""
    if tile == "none":
        return not o["color"]
    if tile == "nolink":
        return not (o["bitrix_task"] or o["bitrix_deal"])
    if tile == "quality":
        return o["quality"] > 0
    if tile == "soon":
        due = _date(o["nearest_due"])
        return bool(due and today <= due <= today + timedelta(days=7) and o["segments_ready"] < o["segments_total"])
    return o["color"] == tile


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
        quality = [_row(r) for r in conn.execute(select(quality_msgs).where(quality_msgs.c.order_no == order_no)
                                                   .order_by(desc(quality_msgs.c.plan_end_date)))]
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
    return {"order_no": order_no, "positions": ps, "changes": changes, "comments": notes, "bitrix": bitrix,
            "quality": quality}


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
    if vals(manager):
        stmt = stmt.where(_in(positions.c.manager, manager))
    with engine.connect() as conn:
        out = [_row(r) for r in conn.execute(stmt)]
    for c in out:
        c["field_name"] = FIELD_NAMES.get(c["field"], c["field"])
    return out


def meta(engine: Engine) -> dict:
    with engine.connect() as conn:
        managers = sorted({r[0] for r in conn.execute(select(positions.c.manager).where(positions.c.closed.is_(False))) if r[0]})
        depts = sorted({r[0] for r in conn.execute(select(positions.c.sales_dept).where(positions.c.closed.is_(False))) if r[0]})
        # клиенты открытых позиций — подсказки в поле «Клиент» отбора
        customers = sorted({r[0] for r in conn.execute(select(positions.c.customer).where(positions.c.closed.is_(False))) if r[0]})
        last = {r.source: _iso(r.loaded_at) for r in conn.execute(
            select(snapshots.c.source, snapshots.c.loaded_at).order_by(snapshots.c.loaded_at))}
        lines = sorted({r[0] for r in conn.execute(select(positions.c.line).where(positions.c.line.is_not(None))) if r[0]})
        quality_total = conn.execute(select(func.count()).select_from(quality_msgs)).scalar()
        rejects = {r[0]: r[1] for r in conn.execute(select(positions.c.reject_code, func.max(positions.c.reject_text))
                                                   .where(positions.c.closed.is_(False), positions.c.reject_code.is_not(None))
                                                   .group_by(positions.c.reject_code))}
    return {"managers": managers, "depts": depts, "customers": customers, "lines": lines,
            "rejects": [{"code": k, "text": v} for k, v in sorted(rejects.items())], "last_load": last, "today": date.today().isoformat(),
            "quality_total": quality_total}


def _produced(p: dict) -> bool:
    """Произведено: все отрезки со статусом «Произведен» или факт MES по ZPP context достиг плана."""
    total = p.get("segments_total") or 0
    if total and (p.get("segments_produced") or 0) >= total:
        return True
    plan, fact = p.get("plan_qty") or 0, p.get("plan_fact_qty") or 0
    return plan > 0 and fact >= plan * 0.99


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
    if vals(manager):
        stmt = stmt.where(_in(positions.c.manager, manager))
    if vals(dept):
        stmt = stmt.where(_in(positions.c.sales_dept, dept))
    if vals(line):
        stmt = stmt.where(_in(positions.c.line, line))
    with engine.connect() as conn:
        rows = [position_view(dict(r._mapping), today) for r in conn.execute(stmt)]
        qmap = defaultdict(list)
        for m in conn.execute(select(quality_msgs.c.order_no, quality_msgs.c.pos, quality_msgs.c.text)):
            qmap[(m.order_no, m.pos)].append(m.text or "без описания")
    out = []
    for r in rows:
        r["quality"] = qmap.get((r["order_no"], r["pos"]), [])
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
        risk = bool(r.get("plan_end_date") and ship_plan and r["plan_end_date"] > ship_plan and not ship_done and not make_done)
        out.append({**r, "ship_plan": ship_plan, "task_make": make, "task_ship": ship, "risk": risk,
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
        "quality": sum(1 for r in out if r["quality"]),
        "risk": sum(1 for r in out if r["risk"]),
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
    if vals(manager):
        stmt = stmt.where(_in(positions.c.manager, manager))
    if vals(dept):
        stmt = stmt.where(_in(positions.c.sales_dept, dept))
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
