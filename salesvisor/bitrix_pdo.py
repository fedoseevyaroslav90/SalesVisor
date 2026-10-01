"""Сборщик отчётов ПДО из вложений задач Битрикса (решение РП 01.10.2026: «отчёты брать из Битрикс-задач»).

Задачи ведутся во вкладке «Загрузка» (таблица bitrix_watch); при первом запуске список берётся из PDO_TASK_IDS
(например «ВАЖНЫЕ НОВОСТИ У2 2026 год»). По каждому новому вложению комментария: «Отчёт по принятым заказам …» →
снимок ПДО, «Отчёт по материалам … (Z0+Z4)» → дефициты (только свежие), «Сводный версия NNN от …» / «Загрузка РЦ» →
загрузка переделов. Дата публикации — дата комментария. Вебхук только читает."""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from . import pdo
from .bitrix import Bitrix
from .config import Settings
from .db import bitrix_files, bitrix_watch
from .ingest import load_lock

log = logging.getLogger("salesvisor.bitrix_pdo")
ORDER = {"pdo_plan": 0, "pdo_fact": 1, "load": 2, "materials": 3}
RE_TASK = re.compile(r"(?:task/view/|tasks?/|^)(\d{3,10})(?:/|$|\?)")
_running = threading.Lock()   # фоновая проверка из вкладки «Загрузка» — не больше одной за раз


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


# ---------------------------------------------------------------- список задач

def task_id_of(raw: str) -> str | None:
    """Номер задачи из числа или ссылки «…/tasks/task/view/321346/»."""
    m = RE_TASK.search((raw or "").strip())
    return m.group(1) if m else None


def watched(engine: Engine, settings: Settings) -> list[str]:
    """Задачи, за файлами которых следим. Пустая таблица — первый запуск: берём PDO_TASK_IDS из настроек сервера."""
    with engine.begin() as conn:
        if not conn.execute(select(func.count()).select_from(bitrix_watch)).scalar() and settings.pdo_task_ids:
            conn.execute(insert(bitrix_watch), [{"task_id": t, "active": True, "added_by": "настройки сервера (PDO_TASK_IDS)",
                                                 "added_at": datetime.now()} for t in dict.fromkeys(settings.pdo_task_ids)])
        return [r[0] for r in conn.execute(select(bitrix_watch.c.task_id).where(bitrix_watch.c.active.is_(True))
                                           .order_by(bitrix_watch.c.added_at))]


def watch_list(engine: Engine, settings: Settings) -> dict:
    """Задачи со сводкой по их файлам: сколько вложений загружено, пропущено (не отчёты ПДО), с ошибкой; последний файл."""
    watched(engine, settings)
    with engine.connect() as conn:
        stats = defaultdict(lambda: {"loaded": 0, "skipped": 0, "failed": 0, "last_posted": None, "last_file": None})
        for r in conn.execute(select(bitrix_files.c.task_id, bitrix_files.c.status, bitrix_files.c.posted_at, bitrix_files.c.name)
                              .order_by(bitrix_files.c.posted_at)):
            s = stats[r.task_id]
            s[r.status if r.status in ("loaded", "skipped", "failed") else "failed"] += 1
            if r.status == "loaded" and r.posted_at:
                s["last_posted"], s["last_file"] = r.posted_at.isoformat(), r.name
        rows = [dict(r._mapping) for r in conn.execute(select(bitrix_watch).order_by(bitrix_watch.c.active.desc(),
                                                                                       bitrix_watch.c.added_at))]
    for r in rows:
        r.update(stats[r["task_id"]])
        for k in ("added_at", "removed_at", "checked_at"):
            r[k] = r[k].isoformat() if r[k] else None
    return {"webhook": bool(settings.bitrix_webhook_url), "running": _running.locked(), "tasks": rows}


def add_task(engine: Engine, settings: Settings, raw: str, who: str, bx: Bitrix | None = None) -> dict:
    """Добавить (или вернуть убранную) задачу. Название — из Битрикса; если вебхук задачу не видит, задача всё равно
    добавляется, а причина видна в списке — сборщик будет пытаться при каждой синхронизации."""
    tid = task_id_of(raw)
    if not tid:
        raise ValueError("Нужен номер задачи или ссылка на неё, например …/tasks/task/view/321346/")
    watched(engine, settings)
    title, error = None, None
    if settings.bitrix_webhook_url:
        own = bx is None
        bx = bx or Bitrix(settings)
        try:
            title = (bx.get_task(tid) or {}).get("title")
        except Exception as e:  # нет доступа к задаче, нет связи — сохраняем причину, задачу не теряем
            error = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            if own:
                bx.close()
    row = {"title": (title or "")[:300] or None, "active": True, "added_by": (who or "")[:100], "added_at": datetime.now(),
           "removed_at": None, "last_error": error}
    with engine.begin() as conn:
        if conn.execute(select(bitrix_watch.c.task_id).where(bitrix_watch.c.task_id == tid)).first():
            conn.execute(update(bitrix_watch).where(bitrix_watch.c.task_id == tid).values(**row))
        else:
            conn.execute(insert(bitrix_watch).values(task_id=tid, **row))
    return {"task_id": tid, "title": title, "error": error}


def remove_task(engine: Engine, task_id: str) -> bool:
    """Перестать следить за задачей. Загруженные из неё отчёты и история остаются."""
    with engine.begin() as conn:
        return conn.execute(update(bitrix_watch).where(bitrix_watch.c.task_id == task_id, bitrix_watch.c.active.is_(True))
                            .values(active=False, removed_at=datetime.now())).rowcount > 0


# ---------------------------------------------------------------- сборщик

def collect(engine: Engine, settings: Settings, bx: Bitrix | None = None, download=None, tasks: list[str] | None = None) -> list[dict]:
    """Просмотреть комментарии задач, скачать и загрузить новые вложения в хронологии. download(url) -> bytes.
    Ошибка одной задачи (нет доступа) не останавливает остальные — она записывается в список задач."""
    tasks = tasks if tasks is not None else watched(engine, settings)
    if not tasks:
        return []
    own = bx is None
    bx = bx or Bitrix(settings)
    download = download or _download
    try:
        with engine.connect() as conn:
            seen = {r[0] for r in conn.execute(select(bitrix_files.c.attachment_id))}
        border = datetime.now() - timedelta(days=settings.pdo_materials_days)
        todo = []
        for tid in tasks:
            try:
                comments = bx.call("task.commentitem.getlist", {"TASKID": tid, "ORDER": {"ID": "asc"}}) or []
                err = None
            except Exception as e:
                comments, err = [], f"{type(e).__name__}: {str(e)[:200]}"
                log.warning("bitrix pdo: задача %s: %s", tid, err)
            with engine.begin() as conn:
                conn.execute(update(bitrix_watch).where(bitrix_watch.c.task_id == tid)
                             .values(checked_at=datetime.now(), last_error=err))
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


def collect_background(engine: Engine, settings: Settings) -> bool:
    """«Проверить сейчас» из вкладки «Загрузка»: первая проверка задачи может качать сотни файлов — не держим запрос."""
    if not _running.acquire(blocking=False):
        return False

    def run():
        try:
            for r in collect(engine, settings):
                log.info("bitrix pdo: %s", r)
        except Exception as e:
            log.warning("bitrix pdo: %s: %s", type(e).__name__, str(e)[:300])
        finally:
            _running.release()

    threading.Thread(target=run, name="bitrix-pdo", daemon=True).start()
    return True
