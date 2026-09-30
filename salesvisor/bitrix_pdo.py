"""Сборщик отчётов ПДО из вложений задач Битрикса (решение РП 01.10.2026: «отчёты брать из Битрикс-задач»).

Задачи — PDO_TASK_IDS (например «ВАЖНЫЕ НОВОСТИ У2 2026 год»). По каждому новому вложению комментария:
«Отчёт по принятым заказам …» → снимок ПДО, «Отчёт по материалам … (Z0+Z4)» → дефициты (только свежие),
«Сводный версия NNN от …» → загрузка переделов. Дата публикации — дата комментария. Вебхук только читает."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from . import pdo
from .bitrix import Bitrix
from .config import Settings
from .db import bitrix_files
from .ingest import load_lock

log = logging.getLogger("salesvisor.bitrix_pdo")
ORDER = {"pdo_plan": 0, "pdo_fact": 1, "load": 2, "materials": 3}


def _posted(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except ValueError:
        return None


def _mark(engine: Engine, **kw) -> None:
    with engine.begin() as conn:
        conn.execute(insert(bitrix_files).values(seen_at=datetime.now(), **{k: (str(v)[:300] if isinstance(v, str) else v)
                                                                            for k, v in kw.items()}))


def _download(url: str) -> bytes:
    """В ссылке на скачивание — служебный токен: в сообщении об ошибке только код ответа, без адреса."""
    try:
        r = httpx.get(url, timeout=600, follow_redirects=True)
    except httpx.HTTPError as e:
        raise RuntimeError(f"скачивание: нет связи ({type(e).__name__})") from None
    if r.status_code != 200:
        raise RuntimeError(f"скачивание: HTTP {r.status_code}")
    return r.content


def collect(engine: Engine, settings: Settings, bx: Bitrix | None = None, download=None) -> list[dict]:
    """Просмотреть комментарии задач, скачать и загрузить новые вложения в хронологии. download(url) -> bytes."""
    own = bx is None
    bx = bx or Bitrix(settings)
    download = download or _download
    try:
        with engine.connect() as conn:
            seen = {r[0] for r in conn.execute(select(bitrix_files.c.attachment_id))}
        border = datetime.now() - timedelta(days=settings.pdo_materials_days)
        todo = []
        for tid in settings.pdo_task_ids:
            comments = bx.call("task.commentitem.getlist", {"TASKID": tid, "ORDER": {"ID": "asc"}}) or []
            for c in comments:
                posted = _posted(c.get("POST_DATE"))
                for o in (c.get("ATTACHED_OBJECTS") or {}).values():
                    aid, name = str(o.get("ATTACHMENT_ID") or ""), o.get("NAME") or ""
                    if not aid or aid in seen:
                        continue
                    seen.add(aid)
                    kind = pdo.kind_by_name(name)
                    if not kind:
                        _mark(engine, attachment_id=aid, task_id=tid, name=name, posted_at=posted, kind=None, status="skipped",
                              note="не отчёт ПДО")
                        continue
                    if kind == "materials" and (not posted or posted < border):
                        _mark(engine, attachment_id=aid, task_id=tid, name=name, posted_at=posted, kind=kind, status="skipped",
                              note=f"отчёт по материалам старше {settings.pdo_materials_days} дн.")
                        continue
                    todo.append((posted or datetime.min, ORDER[kind], kind, aid, name, tid))
        todo.sort()
        results = []
        for posted, _o, kind, aid, name, tid in todo:
            try:
                info = bx.call("disk.attachedObject.get", {"id": aid}) or {}
                url = info.get("DOWNLOAD_URL")
                if not url:
                    raise RuntimeError("нет ссылки на скачивание")
                data = download(url)
                with load_lock(engine, wait=True):
                    res = pdo.LOADERS[kind](engine, data, name, published_at=posted, attachment_id=aid)
                results.append({**res, "bitrix_task": tid})
                _mark(engine, attachment_id=aid, task_id=tid, name=name, posted_at=posted, kind=kind, status="loaded",
                      note=str(res.get("skipped") or "")[:200] or None)
            except Exception as e:  # один битый файл не останавливает остальные
                log.warning("bitrix pdo %s: %s: %s", name, type(e).__name__, str(e)[:200])
                _mark(engine, attachment_id=aid, task_id=tid, name=name, posted_at=posted, kind=kind, status="failed",
                      note=f"{type(e).__name__}: {str(e)[:150]}")
        return results
    finally:
        if own:
            bx.close()
