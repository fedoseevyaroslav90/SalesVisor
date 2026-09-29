"""Синхронизация: забрать выгрузки из Metabase, загрузить в базу, отправить переносы в Битрикс24."""
from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from .bitrix import Bitrix, post_decade_changes
from .config import Settings
from .ingest import load_segments, load_svetofor, read_table
from .metabase import Metabase

log = logging.getLogger("salesvisor.sync")


def run_sync(engine: Engine, settings: Settings) -> list[dict]:
    mb = Metabase(settings)
    results = []
    # Сначала отрезки (менеджер, этапы), потом светофор (декады и цвет)
    data = mb.card_csv(settings.card_segments)
    results.append(load_segments(engine, read_table(data, "segments.csv"), f"metabase:card/{settings.card_segments}"))
    data = mb.card_csv(settings.card_svetofor)
    results.append(load_svetofor(engine, read_table(data, "svetofor.csv"), f"metabase:card/{settings.card_svetofor}"))
    if settings.bitrix_post_comments and settings.bitrix_webhook_url:
        sent = post_decade_changes(engine, Bitrix(settings))
        results.append({"source": "bitrix", "comments_for_changes": sent})
    for r in results:
        log.info("sync: %s", r)
    return results
