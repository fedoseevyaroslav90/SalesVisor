"""Синхронизация: забрать выгрузки из Metabase или из папки SFTP, загрузить в базу, отправить переносы в Битрикс24."""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from .bitrix import Bitrix, post_decade_changes
from .config import Settings
from .ingest import SOURCE_ORDER, load_file, load_lock, load_plan, load_dispatcher, load_segments, load_svetofor, read_table, sniff_source
from .metabase import Metabase
from . import pdo

log = logging.getLogger("salesvisor.sync")


class SyncError(Exception):
    pass


# Файл моложе этого ещё может докачиваться по SFTP: обрезанный CSV с верным заголовком загрузился бы
# как полный отчёт и стёр бы отрезки, которых в нём не оказалось
SETTLE_SECONDS = 120
# Разобранные выгрузки лежат в done/ и failed/ столько дней, потом удаляются (отчёт по отрезкам — ~65 МБ за раз)
KEEP_DAYS = 30


def import_folder(engine: Engine, folder: str) -> list[dict]:
    """Загрузить новые выгрузки из папки и переложить их в done/ (или failed/, если файл не разобран)."""
    root = Path(folder)
    now = time.time()
    files = sorted(f for f in root.iterdir() if f.is_file() and f.suffix.lower() in (".csv", ".xlsx", ".xls")
                   and not f.name.startswith(".") and now - f.stat().st_mtime >= SETTLE_SECONDS)
    parsed = []
    for f in files:
        data = f.read_bytes()
        try:
            source = sniff_source(data, f.name)
        except Exception:  # битый или недокачанный файл
            source = None
        parsed.append((f, data, source))
    # Сначала отрезки (менеджер, этапы), потом светофор; внутри — по времени изменения файла.
    # Файлы ПДО — по декаде / дате снимка и версии: история должна лечь в хронологии
    parsed.sort(key=lambda x: (SOURCE_ORDER.get(x[2], 9),
                               pdo.sort_key(x[2], x[0].name) if x[2] in pdo.KINDS else (),
                               x[0].stat().st_mtime))
    results = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for f, data, source in parsed:
        target = "failed"
        if source:
            try:
                results.append(load_file(engine, source, data, f.name, published_at=datetime.fromtimestamp(f.stat().st_mtime)))
                target = "done"
            except Exception as e:  # любой сбой — в failed/, иначе файл вставал бы первым каждый час
                log.warning("import %s: %s: %s", f.name, type(e).__name__, str(e)[:300])
        else:
            log.warning("import %s: не похоже ни на одну из выгрузок (отрезки, светофор, план, диспетчерский)", f.name)
        (root / target).mkdir(exist_ok=True)
        shutil.move(str(f), root / target / f"{stamp}_{f.name}")
    _prune(root, now)
    return results


def _prune(root: Path, now: float) -> None:
    """Удалить из done/ и failed/ файлы старше KEEP_DAYS — иначе папка SFTP растёт без предела."""
    for sub in ("done", "failed"):
        d = root / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and now - f.stat().st_mtime > KEEP_DAYS * 86400:
                try:
                    f.unlink()
                    log.info("import: удалён старый файл %s/%s", sub, f.name)
                except OSError as e:
                    log.warning("import: не удалось удалить %s/%s: %s", sub, f.name, e)


def run_sync(engine: Engine, settings: Settings, wait: bool = True) -> list[dict]:
    """wait=False — из веба: если идёт другая загрузка, сразу LoadBusy (веб отвечает 409)."""
    if not settings.metabase_ready and not settings.import_dir:
        raise SyncError("Не настроены ни Metabase, ни папка выгрузок (IMPORT_DIR)")
    with load_lock(engine, wait=wait):
        if settings.metabase_ready:
            results = _from_metabase(engine, settings)
        else:
            results = import_folder(engine, settings.import_dir)
    if settings.bitrix_webhook_url:
        # отчёты ПДО и по материалам из вложений задач Битрикса (решение РП 01.10.2026); список задач — во вкладке
        # «Загрузка», при первом запуске — PDO_TASK_IDS
        from .bitrix_pdo import collect
        try:
            results += collect(engine, settings)
        except Exception as e:  # сбой Битрикса не должен ронять синхронизацию отрезков
            log.warning("bitrix pdo: %s: %s", type(e).__name__, str(e)[:300])
            results.append({"source": "bitrix_pdo", "error": str(e)[:300]})
    if settings.bitrix_post_comments and settings.bitrix_webhook_url:
        sent = post_decade_changes(engine, Bitrix(settings))
        results.append({"source": "bitrix", "comments_for_changes": sent})
    for r in results:
        log.info("sync: %s", r)
    return results


def _from_metabase(engine: Engine, settings: Settings) -> list[dict]:
    mb = Metabase(settings)
    results = []
    # Сначала отрезки (менеджер, этапы), потом светофор (декады и цвет)
    data = mb.card_csv(settings.card_segments)
    results.append(load_segments(engine, read_table(data, "segments.csv"), f"metabase:card/{settings.card_segments}"))
    data = mb.card_csv(settings.card_svetofor)
    results.append(load_svetofor(engine, read_table(data, "svetofor.csv"), f"metabase:card/{settings.card_svetofor}"))
    # План производства и диспетчерский — если для них заведены вопросы в Metabase
    for card, loader, name in ((settings.card_plan, load_plan, "plan.csv"), (settings.card_dispatcher, load_dispatcher, "dispatcher.csv")):
        if card:
            results.append(loader(engine, read_table(mb.card_csv(card), name), f"metabase:card/{card}"))
    return results
