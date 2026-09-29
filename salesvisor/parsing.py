"""Разбор значений из выгрузок Metabase: русские даты, декады, числа, флаги SAP."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_RU_DATE = re.compile(r"^(\d{1,2})\s+([а-яё]+),?\s+(\d{4})(?:,\s*(\d{1,2}):(\d{2}))?$", re.I)
_DOT_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DECADE = re.compile(r"^([123])\s*Д\s*(\d{1,2})$", re.I)
_TASK = re.compile(r"(?<!\d)(\d{5,7})(?!\d)")


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN из pandas
        return True
    return isinstance(v, str) and v.strip() == ""


def text(v) -> str | None:
    if is_blank(v):
        return None
    return str(v).strip()


def parse_date(v) -> date | None:
    """«22 июля, 2026», «8 июня, 2026, 16:44», «22.07.2026», ISO, datetime."""
    if is_blank(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime().date()
    s = str(v).strip()
    m = _RU_DATE.match(s)
    if m and m.group(2).lower() in MONTHS:
        return _safe_date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    m = _DOT_DATE.match(s)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = _ISO_DATE.match(s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_number(v) -> float | None:
    """«1 814,22» → 1814.22; числа из Excel возвращаются как есть."""
    if is_blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_flag(v) -> bool:
    """Иконки статусов SAP в отчёте: @08@ — да (зелёный), @EB@ и прочие — нет."""
    return text(v) == "@08@"


def parse_decade(label, near: date | None = None) -> tuple[str | None, date | None]:
    """«2Д07» → («2Д07», последний день декады). Года в метке нет, поэтому берём год,
    при котором декада ближе всего к опорной дате near (обычно дата плана или перевода в Z4)."""
    s = text(label)
    if not s:
        return None, None
    m = _DECADE.match(s)
    if not m:
        return s, None
    k, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return s, None
    base = near or date.today()
    best = None
    for year in (base.year - 1, base.year, base.year + 1):
        d = decade_end(year, month, k)
        if best is None or abs((d - base).days) < abs((best - base).days):
            best = d
    return f"{k}Д{month:02d}", best


def decade_end(year: int, month: int, k: int) -> date:
    if k == 1:
        return date(year, month, 10)
    if k == 2:
        return date(year, month, 20)
    return date(year, month, calendar.monthrange(year, month)[1])


def decade_label(d: date | None) -> str | None:
    if d is None:
        return None
    k = 1 if d.day <= 10 else 2 if d.day <= 20 else 3
    return f"{k}Д{d.month:02d}"


def parse_bitrix_task(v) -> str | None:
    """Из свободного текста «Задача № 313360 Губанов» достаёт номер задачи."""
    s = text(v)
    if not s:
        return None
    m = _TASK.search(s)
    return m.group(1) if m else None


def parse_int(v) -> int | None:
    n = parse_number(v)
    return int(n) if n is not None else None
