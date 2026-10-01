"""Окно мощности и деньги на отрезке (этап 2 инструмента «Путь заказа», 01.10.2026).

Источник — снимки «Загрузки РЦ» ПДО (версия 010 — твёрдый план Z0+Z4): загрузка переделов по декадам (wc_load, вся
история снимков), позиции последнего «Сводного» с их долей мощности и ВП (wc_pos), итоги декад (dec_summary).

Почему прогноз: в твёрдом плане дальние декады почти пустые — заказы ещё не размещены. По 37 снимкам 2026 за 2 недели
до начала декады в нём видно ~46 % итоговой загрузки, за 3–4 недели — ~21 %. Прогноз «сейчас + обычное дозаполнение
этого передела за оставшиеся недели, не ниже снимка и не выше 90-го перцентиля его итоговой загрузки» проверен на истории (только по
прошлым данным): медиана ошибки за 2–4 недели 17–27 п.п. против 49–69 у чтения «как есть», перегрузов поймано втрое
больше (разбор 01.10.2026: «14 — Загрузка переделов», раздел «Окно мощности»). Дальше LEAD_MAX недель прогноз
ненадёжен — там показываем обычную загрузку передела к началу декады (медиана последних декад)."""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.engine import Engine

from .db import dec_summary, positions, wc_load, wc_pos
from .pdo import dec_end, dec_start

LEAD_MAX = 4          # недель до начала декады, до которых прогноз по истории дозаполнения проверен
MIN_SAMPLES = 4       # прогноз передела — если у него столько наблюдений на этом сроке
TYPICAL_DECADES = 12  # «обычно к началу декады» — по стольким последним декадам
MIN_TYPICAL = 3       # и не меньше чем по стольким
FINAL_GAP_DAYS = 7    # итог декады известен, если есть снимок не раньше чем за неделю до её начала
FREE_LOAD = 90        # «есть запас»: все ключевые переделы группы загружены меньше этого
KEY_SHARE = 0.25      # ключевой передел группы: через него идёт не меньше этой доли позиций группы
RISK_KINDS = ("сейчас", "прогноз")   # что считается перегрузом в отметке позиции («обычно» — только в окне)


def _short(grp: str | None) -> str:
    """«3Д. ШЛАНГОВЫЕ» → «шланговые»: в метках и карточке."""
    s = (grp or "").split(". ", 1)[-1]
    return s.lower()


def _history(conn) -> dict:
    """(передел, декада) → {дата снимка: загрузка, %} по всем снимкам версии 010."""
    h = defaultdict(dict)
    for r in conn.execute(select(wc_load.c.name, wc_load.c.decade_end, wc_load.c.snap_date, wc_load.c.load)
                          .where(wc_load.c.ver == "010", wc_load.c.level == "группа")):
        if r.load is not None:
            h[(r.name, r.decade_end)][r.snap_date] = r.load
    return h


def model(conn, today: date | None = None) -> dict:
    """Последний снимок и статистика дозаполнения: прирост загрузки к началу декады по переделу и сроку (недели),
    обычная итоговая загрузка и сколько последних декад передел был перегружен."""
    today = today or date.today()
    h = _history(conn)
    incs, finals = defaultdict(list), defaultdict(list)
    for (grp, dec), by in h.items():
        s0 = dec_start(dec)
        before = [s for s in by if s < s0]
        if not before or s0 > today:
            continue
        fs = max(before)
        if (s0 - fs).days > FINAL_GAP_DAYS:
            continue
        final = by[fs]
        finals[grp].append((dec, final))
        if final < 20:             # передел почти пустой — по нему прирост ничего не скажет
            continue
        for s, v in by.items():
            lead = (s0 - s).days // 7
            if s < fs and 1 <= lead <= LEAD_MAX:
                incs[(grp, lead)].append(final - v)
    inc = {k: st.median(v) for k, v in incs.items() if len(v) >= MIN_SAMPLES}
    typical = {}
    for grp, xs in finals.items():
        if len(xs) < MIN_TYPICAL:
            continue
        last = [v for _, v in sorted(xs)[-TYPICAL_DECADES:]]
        typical[grp] = {"median": round(st.median(last)), "p90": sorted(last)[int(0.9 * (len(last) - 1))],
                        "over": sum(v > 100 for v in last), "n": len(last)}
    snap = max((s for by in h.values() for s in by), default=None)
    now = {(grp, dec): by[snap] for (grp, dec), by in h.items() if snap in by}
    return {"snap": snap, "now": now, "inc": inc, "typical": typical}


def cell(m: dict, grp: str, dec: date) -> dict:
    """Загрузка передела в декаде: сейчас (последний снимок), прогноз к началу декады или обычная."""
    now = m["now"].get((grp, dec))
    snap = m["snap"]
    lead = (dec_start(dec) - snap).days // 7 if snap else 0
    t = m["typical"].get(grp)
    if lead <= 0 or not snap:
        val, kind = now, "сейчас"
    elif lead <= LEAD_MAX and (grp, lead) in m["inc"]:
        val = max((now or 0) + m["inc"][(grp, lead)], 0)
        if t:                      # потолок: не выше обычного максимума передела (90-й перцентиль итогов)
            val = min(val, max(now or 0, t["p90"]))
        val, kind = max(val, now or 0), "прогноз"   # и не ниже того, что уже стоит в плане
    elif t:
        val, kind = max(now or 0, t["median"]), "обычно"
    else:
        val, kind = now, "сейчас"
    return {"now": None if now is None else round(now), "value": None if val is None else round(val), "kind": kind,
            "lead_weeks": max(lead, 0)}


def _pos_snapshot(conn):
    return conn.execute(select(func.max(wc_pos.c.snap_date))).scalar()


def refresh_routes(conn, today: date | None = None) -> int:
    """Поля позиции по последнему «Сводному»: декада в твёрдом плане, ВП, переделы маршрута и самый загруженный из них."""
    m = model(conn, today)
    rows = defaultdict(lambda: {"decs": {}, "grps": defaultdict(float)})
    for r in conn.execute(select(wc_pos).where(wc_pos.c.place == "")):
        x = rows[(r.order_no, r.pos)]
        x["decs"][r.decade_end] = r.vp
        x["grps"][r.grp] += r.km or 0
    had = {(r.order_no, r.pos) for r in conn.execute(select(positions.c.order_no, positions.c.pos)
                                                     .where(positions.c.plan_decade.is_not(None)))}
    params = []
    for k, x in rows.items():
        dec = min(x["decs"])
        grps = sorted(x["grps"], key=lambda g: (g[:2], g))
        cells = {g: cell(m, g, dec) for g in grps}
        worst = max(grps, key=lambda g: cells[g]["value"] or 0) if grps else None
        params.append({"k_o": k[0], "k_p": k[1], "plan_decade": dec, "plan_vp": sum(v or 0 for v in x["decs"].values()),
                       "route": " → ".join(_short(g) for g in grps)[:200] or None,
                       "route_load": cells[worst]["value"] if worst else None, "route_bottleneck": worst,
                       "route_kind": cells[worst]["kind"] if worst else None})
    params += [{"k_o": o, "k_p": p_, "plan_decade": None, "plan_vp": None, "route": None, "route_load": None,
                "route_bottleneck": None, "route_kind": None} for o, p_ in had - set(rows)]
    if params:
        fields = ("plan_decade", "plan_vp", "route", "route_load", "route_bottleneck", "route_kind")
        conn.execute(update(positions).where(positions.c.order_no == bindparam("k_o"), positions.c.pos == bindparam("k_p"))
                     .values({f: bindparam(f) for f in fields}), params)
    return len(rows)


def _decades(today: date, n: int) -> list[date]:
    out, d = [], dec_end(today)
    while len(out) < n:
        out.append(d)
        d = dec_end(d + timedelta(days=1))
    return out


def window(engine: Engine, today: date | None = None, decades: int = 6, manager: str = "", dept: str = "",
           filters=None) -> dict:
    """Окно мощности: переделы на N декад вперёд (сейчас → прогноз), группы продукции по ключевым переделам с ближайшей
    декадой, где есть запас, деньги в работе и под риском по группам. Деньги — по менеджеру, отделу и отбору вкладки
    «Заказы» (например, без внутренних заказов ООО «Инкаб»); загрузка переделов — всегда по всему заводу."""
    from .queries import Filters, _filtered_positions   # queries → pdo → capacity: импорт здесь

    today = today or date.today()
    decs = _decades(today, decades)
    with engine.connect() as conn:
        m = model(conn, today)
        snap_pos = _pos_snapshot(conn)
        route_rows = list(conn.execute(select(wc_pos.c.order_no, wc_pos.c.pos, wc_pos.c.grp).where(wc_pos.c.place == "")))
        groups_of = {(r.order_no, r.pos): r.product_group
                     for r in conn.execute(select(positions.c.order_no, positions.c.pos, positions.c.product_group))}
        ls = conn.execute(select(func.max(dec_summary.c.snap_date)).where(dec_summary.c.ver == "010")).scalar()
        summ = defaultdict(dict)
        if ls:
            for r in conn.execute(select(dec_summary).where(dec_summary.c.ver == "010", dec_summary.c.snap_date == ls)):
                if r.decade_end in decs:
                    summ[r.decade_end.isoformat()][r.metric] = r.value
    grps = sorted({g for g, d in m["now"] if d in decs} | set(m["typical"]))
    peredely = []
    for g in grps:
        cells = {d.isoformat(): cell(m, g, d) for d in decs}
        t = m["typical"].get(g) or {}
        peak = max([c["value"] or 0 for c in cells.values()] + [t.get("median") or 0])
        if peak < 20:
            continue
        peredely.append({"grp": g, "short": _short(g), "typical": t.get("median"), "over": t.get("over"), "n": t.get("n"),
                         "cells": cells})
    peredely.sort(key=lambda r: -max(c["value"] or 0 for c in r["cells"].values()))
    pcell = {r["grp"]: r["cells"] for r in peredely}

    # маршруты групп продукции: через какие переделы идут их позиции в последнем «Сводном»
    pos_grps = defaultdict(set)
    for r in route_rows:
        pos_grps[(r.order_no, r.pos)].add(r.grp)
    by_group = defaultdict(lambda: defaultdict(int))
    n_group = defaultdict(int)
    for k, gs in pos_grps.items():
        pg = groups_of.get(k) or "не определена"
        n_group[pg] += 1
        for g in gs:
            by_group[pg][g] += 1

    # деньги: позиции в работе (не выпущены) и под риском — те же отметки, что в отборе
    money = defaultdict(lambda: {"positions": 0, "mp": 0.0, "risk": 0.0, "risk_positions": 0, "parts": defaultdict(float)})
    for p in _filtered_positions(engine, "open", manager, dept, filters or Filters(), today):
        if p.get("released") or p.get("closed"):
            continue
        x = money[p.get("product_group") or "не определена"]
        mp = p.get("mp_rub") or 0
        x["positions"] += 1
        x["mp"] += mp
        why = [k for k, on in (("overdue", p["overdue"]), ("pdo_rejected", p.get("pdo_rejected")),
                               ("deficit_real", bool(p.get("deficit_real"))), ("overload", p.get("overload"))) if on]
        if why:
            x["risk"] += mp
            x["risk_positions"] += 1
            for k in why:
                x["parts"][k] += mp

    groups = []
    for pg in sorted(set(money) | set(n_group), key=lambda g: -money[g]["mp"]):
        n = n_group.get(pg, 0)
        shares = sorted(((g, c / n) for g, c in by_group[pg].items()), key=lambda x: (-x[1], x[0])) if n else []
        key = [g for g, s in shares if s >= KEY_SHARE and g in pcell] or [g for g, _ in shares[:2] if g in pcell]
        cells, free = {}, None
        for d in decs:
            iso = d.isoformat()
            vals = [(pcell[g][iso]["value"] or 0, g, pcell[g][iso]["kind"]) for g in key]
            if vals:
                v, g, kind = max(vals)
                cells[iso] = {"value": v, "grp": g, "short": _short(g), "kind": kind}
                if free is None and d > decs[0] and v < FREE_LOAD:
                    free = iso
        mo = money[pg]
        groups.append({"group": pg, "positions": mo["positions"], "mp": round(mo["mp"]), "risk": round(mo["risk"]),
                       "risk_positions": mo["risk_positions"], "parts": {k: round(v) for k, v in mo["parts"].items()},
                       "routed": n, "key": [{"grp": g, "short": _short(g), "share": round(100 * s)} for g, s in shares
                                            if g in key],
                       "cells": cells, "free_decade": free})
    total = {"positions": sum(g["positions"] for g in groups), "mp": sum(g["mp"] for g in groups),
             "risk": sum(g["risk"] for g in groups), "risk_positions": sum(g["risk_positions"] for g in groups)}
    return {"today": today.isoformat(), "snapshot": m["snap"].isoformat() if m["snap"] else None,
            "positions_snapshot": snap_pos.isoformat() if snap_pos else None, "decades": [d.isoformat() for d in decs],
            "lead_max": LEAD_MAX, "free_load": FREE_LOAD, "summary": summ, "peredely": peredely, "groups": groups,
            "total": total}


def occupants(engine: Engine, grp: str, decade: date, limit: int = 500, today: date | None = None) -> dict:
    """Кто занимает передел в декаде по последнему «Сводному»: позиции с долей мощности, ВП и решением ПДО."""
    with engine.connect() as conn:
        m = model(conn, today)
        snap = _pos_snapshot(conn)
        rows = list(conn.execute(select(wc_pos).where(wc_pos.c.grp == grp, wc_pos.c.decade_end == decade)))
        keys = {(r.order_no, r.pos) for r in rows}
        info = {}
        for chunk in (sorted({k[0] for k in keys})[i:i + 500] for i in range(0, len(keys), 500)):
            for p in conn.execute(select(positions.c.order_no, positions.c.pos, positions.c.customer, positions.c.manager,
                                         positions.c.product, positions.c.product_group, positions.c.mp_rub,
                                         positions.c.first_decade_end, positions.c.pdo_last_status, positions.c.closed)
                                  .where(positions.c.order_no.in_(chunk))):
                info[(p.order_no, p.pos)] = dict(p._mapping)
    total = {r.order_no + "/" + r.pos: r for r in rows if r.place == ""}
    places = defaultdict(list)
    for r in rows:
        if r.place:
            places[r.order_no + "/" + r.pos].append(r)
    out = []
    for k, r in total.items():
        i = info.get((r.order_no, r.pos), {})
        top = max(places.get(k, []), key=lambda x: x.load or 0, default=None)
        out.append({"order_no": r.order_no, "pos": r.pos, "km": round(r.km or 0, 3), "load": round(r.load or 0, 2),
                    "vp": round(r.vp or 0), "vp_per_pct": round((r.vp or 0) / r.load) if r.load else None,
                    "place": top.place if top else None, "customer": i.get("customer"), "manager": i.get("manager"),
                    "product": i.get("product"), "product_group": i.get("product_group"), "mp_rub": i.get("mp_rub"),
                    "first_decade_end": i["first_decade_end"].isoformat() if i.get("first_decade_end") else None,
                    "pdo_last_status": i.get("pdo_last_status"), "known": bool(i)})
    out.sort(key=lambda x: -x["load"])
    c = cell(m, grp, decade)
    return {"grp": grp, "short": _short(grp), "decade": decade.isoformat(), "snapshot": snap.isoformat() if snap else None,
            "cell": c, "positions": len(out), "share": round(sum(x["load"] for x in out), 1), "rows": out[:limit]}


def card_rows(conn, order_no: str, today: date | None = None) -> dict:
    """Карточка заказа: через какие переделы идёт каждая позиция в декаде твёрдого плана и как они загружены."""
    rows = list(conn.execute(select(wc_pos).where(wc_pos.c.order_no == order_no)))
    if not rows:
        return {}
    m = model(conn, today)
    top = {}
    for r in rows:
        if r.place:
            k = (r.pos, r.decade_end, r.grp)
            if k not in top or (r.load or 0) > (top[k].load or 0):
                top[k] = r
    out = defaultdict(list)
    for r in sorted((r for r in rows if r.place == ""), key=lambda r: (r.decade_end, r.grp[:2], r.grp)):
        c = cell(m, r.grp, r.decade_end)
        t = top.get((r.pos, r.decade_end, r.grp))
        out[r.pos].append({"decade": r.decade_end.isoformat(), "grp": r.grp, "short": _short(r.grp),
                           "place": t.place if t else None, "km": round(r.km or 0, 3), "share": round(r.load or 0, 2),
                           "vp": round(r.vp or 0), "now": c["now"], "value": c["value"], "kind": c["kind"],
                           "snapshot": r.snap_date.isoformat() if r.snap_date else None})
    return dict(out)
