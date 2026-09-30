"""Синхронизация: забрать выгрузки из Metabase или из папки SFTP, загрузить в базу, отправить переносы в Битрикс24."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from .bitrix import Bitrix, post_decade_changes
from .config import Settings
from .ingest import SOURCE_ORDER, detect_source, load_file, load_plan, load_dispatcher, load_segments, load_svetofor, read_table
from .metabase import Metabase

log = logging.getLogger("salesvisor.sync")


class SyncError(Exception):
    pass


def import_folder(engine: Engine, folder: str) -> list[dict]:
    """Загрузить новые выгрузки из папки и переложить их в done/ (или failed/, если файл не разобран)."""
    root = Path(folder)
    files = sorted(f for f in root.iterdir() if f.is_file() and f.suffix.lower() in (".csv", ".xlsx", ".xls")
                   and not f.name.startswith("."))
    parsed = []
    for f in files:
        data = f.read_bytes()
        try:
            source = detect_source(list(read_table(data, f.name).columns))
        except Exception:  # битый или недокачанный файл
            source = None
        parsed.append((f, data, source))
    # Сначала отрезки (менеджер, этапы), потом светофор; внутри — по времени изменения файла
    parsed.sort(key=lambda x: (SOURCE_ORDER.get(x[2], 9), x[0].stat().st_mtime))
    results = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for f, data, source in parsed:
        target = "failed"
        if source:
            try:
                results.append(load_file(engine, source, data, f.name))
                target = "done"
            except ValueError as e:
                log.warning("import %s: %s", f.name, e)
        else:
            log.warning("import %s: не похоже ни на светофор, ни на отчёт по отрезкам", f.name)
        (root / target).mkdir(exist_ok=True)
        shutil.move(str(f), root / target / f"{stamp}_{f.name}")
    return results


def run_sync(engine: Engine, settings: Settings) -> list[dict]:
    if settings.metabase_ready:
        results = _from_metabase(engine, settings)
    elif settings.import_dir:
        results = import_folder(engine, settings.import_dir)
    else:
        raise SyncError("Не настроены ни Metabase, ни папка выгрузок (IMPORT_DIR)")
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
