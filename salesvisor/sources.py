"""Что загружать в SalesVisor и в каком состоянии данные сейчас (вкладка «Загрузка» → «Что загружать»).

Перечень файлов, без которых экраны и статистика врут: откуда каждый берётся, как часто нужен, для чего, когда был
последний и где пропуски (декады без отчёта ПДО, недели без «Сводного»). Статус — по датам в базе, без догадок."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from .db import pdo_files, snapshots, wc_load, wc_pos
from .pdo import dec_end, dec_start

# (ключ, файл, откуда, как часто, для чего, обязательный, срок «свежий» / «ещё терпимо», дн.)
SOURCES = [
    ("segments", "Отчёт по отрезкам — Metabase, вопрос 522 «Отчёт по отрезкам xt» (CSV)",
     "Metabase или папка выгрузок (SFTP), вручную — «Загрузить файл»", "каждый рабочий день",
     "Основа всего: позиции, этапы отрезков, сроки, менеджер, МП и МЗ. Без него нет заказов и статусов.", True, 3, 7),
    ("svetofor", "Светофор — Metabase, вопрос 573 «Светофор v2» (CSV)",
     "Metabase или папка выгрузок", "каждый рабочий день",
     "Первая и текущая декада, смещение, цвет. Без него нет опоздания к первой дате и OTD в «Статистике».", True, 3, 7),
    ("plan", "План производства ZPP context (EXPORT.XLSX)", "папка выгрузок или «Загрузить файл»", "каждый рабочий день",
     "Линия и окончание производства, «План на день».", True, 3, 7),
    ("dispatcher", "Диспетчерский отчёт по декаде", "папка выгрузок или «Загрузить файл»", "каждый рабочий день",
     "«Диспетчерский», признак декады и готовность в строке позиции.", False, 3, 10),
    ("pdo_plan", "Отчёт по принятым заказам в декаду ДД.ММ.ГГГГ ver.N (ПДО)",
     "задачи Битрикса из списка ниже — сами; иначе папка или «Загрузить файл»", "к каждой декаде, все версии",
     "Решения ПДО «принят / не принят», причина и узкое место, первая дата по первому снимку, «Пульс ПДО».", True, None, None),
    ("pdo_fact", "Отчёт по принятым заказам и факт в декаду ДД.ММ.ГГГГ (ПДО)",
     "задачи Битрикса — сами; иначе папка", "после каждой декады",
     "Итог декады «готов / не готов» и классификатор причины.", True, None, None),
    ("materials", "Отчёт по материалам на ДД.ММ.ГГ (Z0+Z4), лист «Дефицит»",
     "задачи Битрикса — сами; иначе папка", "каждый рабочий день (берутся последние 20 дней)",
     "«Ждём материал», дефициты и сдвиги поставок в карточке.", True, 3, 7),
    ("load010", "Загрузка РЦ \\ «Сводный версия 010 от ДД.ММ.ГГГГ» (ПДО)",
     "папка выгрузок или «Загрузить файл» (в задачах У2 его нет)", "раз в неделю",
     "Загрузка переделов, маршруты позиций и ВП, перегруз передела. Для прогноза «Окна мощности» нужна история "
     "не меньше 8 недель (лучше с начала года).", True, 9, 16),
    ("summary", "Короткая сводка ПДО «Загрузка РЦ ДД.ММ»", "папка выгрузок или «Загрузить файл»", "раз в неделю",
     "Свежая загрузка переделов на 4 декады (с окраской) между «Сводными».", False, 9, 16),
    ("load100", "Загрузка РЦ \\ «Сводный версия 100 от ДД.ММ.ГГГГ» (с прогнозными Z6/Z7)", "папка выгрузок",
     "раз в неделю, если ПДО делает", "Пока только хранится: история для будущего прогноза с учётом воронки.", False, 9, 30),
]
HISTORY_DECADES = 12      # сколько последних декад проверять на пропуски отчётов ПДО
FACT_GRACE_DAYS = 3       # итог «и факт» ждём через столько дней после конца декады
FORECAST_WEEKS = 8        # прогнозу окна мощности нужно не меньше стольких недельных снимков


def _status(last: date | None, fresh: int, ok: int, today: date) -> str:
    if not last:
        return "нет"
    age = (today - last).days
    return "ok" if age <= fresh else "устарел" if age <= ok else "давно"


def _decades_back(today: date, n: int) -> list[date]:
    out, d = [], dec_end(today)
    while len(out) < n:
        out.append(d)
        d = dec_end(dec_start(d) - timedelta(days=1))
    return out


def catalog(engine: Engine, today: date | None = None) -> list[dict]:
    today = today or date.today()
    with engine.connect() as conn:
        snaps = {}
        for r in conn.execute(select(snapshots.c.source, func.max(snapshots.c.loaded_at), func.count())
                              .where(snapshots.c.loaded_at >= datetime.combine(today - timedelta(days=30), datetime.min.time()))
                              .group_by(snapshots.c.source)):
            snaps[r[0]] = (r[1], r[2])
        ever = {r[0]: r[1] for r in conn.execute(select(snapshots.c.source, func.max(snapshots.c.loaded_at)).group_by(snapshots.c.source))}
        files = [dict(r._mapping) for r in conn.execute(select(pdo_files.c.kind, pdo_files.c.origin, pdo_files.c.decade_end,
                                                               pdo_files.c.report_date, pdo_files.c.version,
                                                               pdo_files.c.published_at))]
        load_snaps = {}
        for ver, snap in conn.execute(select(wc_load.c.ver, wc_load.c.snap_date).distinct()):
            load_snaps.setdefault(ver, set()).add(snap)
        pos_snap = conn.execute(select(func.max(wc_pos.c.snap_date))).scalar()

    def svodny(f):
        return "сводный" in (f["origin"] or "").lower()

    out = []
    for key, name, where, every, need, required, fresh, ok in SOURCES:
        row = {"key": key, "name": name, "where": where, "every": every, "need": need, "required": required,
               "last": None, "status": "нет", "detail": ""}
        if key in ("segments", "svetofor", "plan", "dispatcher"):
            last_at = ever.get(key)
            row["last"] = last_at.date().isoformat() if last_at else None
            row["status"] = _status(last_at.date() if last_at else None, fresh, ok, today)
            row["detail"] = f"загрузок за 30 дней: {snaps.get(key, (None, 0))[1]}"
        elif key in ("pdo_plan", "pdo_fact"):
            got = {f["decade_end"] for f in files if f["kind"] == key and f["decade_end"]}
            last_pub = max((f["published_at"] for f in files if f["kind"] == key and f["published_at"]), default=None)
            row["last"] = last_pub.date().isoformat() if last_pub else None
            decs = _decades_back(today, HISTORY_DECADES)
            if key == "pdo_plan":
                nxt = dec_end(dec_end(today) + timedelta(days=1))
                want = ([nxt] if (dec_start(nxt) - today).days <= 3 else []) + decs
            else:
                want = [d for d in decs if (today - d).days >= FACT_GRACE_DAYS]
            missing = [d for d in want if d not in got]
            first = min(got) if got else None
            row["status"] = ("нет" if not got else "ok" if not missing else
                             "давно" if key == "pdo_plan" and dec_end(today) in missing else "пропуски")
            parts = []
            if missing:
                parts.append("нет за декады: " + ", ".join(f"{d:%d.%m}" for d in sorted(missing)))
            if first:
                parts.append(f"история с декады {first:%d.%m.%Y}")
                if first > date(today.year, 1, 20):
                    parts.append("для первой даты по первому снимку нужна история с начала года")
            row["detail"] = "; ".join(parts)
        elif key == "materials":
            dates = sorted({f["report_date"] for f in files if f["kind"] == "materials" and f["report_date"]})
            row["last"] = dates[-1].isoformat() if dates else None
            row["status"] = _status(dates[-1] if dates else None, fresh, ok, today)
            row["detail"] = f"отчётов за 20 дней: {sum(1 for d in dates if (today - d).days <= 20)}"
        elif key in ("load010", "summary", "load100"):
            if key == "load100":
                snaps_k = sorted(load_snaps.get("100", set()))
            else:
                snaps_k = sorted({f["report_date"] for f in files if f["kind"] == "load" and f["report_date"]
                                  and (f["version"] or 0) == 10 and svodny(f) == (key == "load010")})
            last = snaps_k[-1] if snaps_k else None
            row["last"] = last.isoformat() if last else None
            row["status"] = _status(last, fresh, ok, today)
            recent = sum(1 for d in snaps_k if (today - d).days <= 7 * 12)
            parts = [f"снимков за 12 недель: {recent}"]
            if snaps_k:
                parts.append(f"история с {snaps_k[0]:%d.%m.%Y}")
            if key == "load010":
                if recent < FORECAST_WEEKS:
                    parts.append(f"для прогноза нужно не меньше {FORECAST_WEEKS}")
                if pos_snap:
                    parts.append(f"позиции и маршруты — из «Сводного» от {pos_snap:%d.%m.%Y}")
            row["detail"] = "; ".join(parts)
            if key == "load010" and row["status"] == "ok" and recent < FORECAST_WEEKS:
                row["status"] = "пропуски"
        if not required and row["status"] == "нет":
            row["status"] = "не загружался"       # необязательный источник — без тревоги
        out.append(row)
    return out
