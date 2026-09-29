"""Командная строка:
    python -m salesvisor serve [--port 8000]
    python -m salesvisor load ФАЙЛ [ФАЙЛ ...]       загрузить выгрузки вручную
    python -m salesvisor sync [--every СЕКУНД]      забрать выгрузки из Metabase или папки IMPORT_DIR (разово или по кругу)
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import get_settings
from .db import make_engine
from .ingest import detect_source, load_file, read_table


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="salesvisor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    l = sub.add_parser("load")
    l.add_argument("files", nargs="+")
    y = sub.add_parser("sync")
    y.add_argument("--every", type=int, default=0, help="повторять каждые N секунд")
    args = ap.parse_args()

    settings = get_settings()
    if args.cmd == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(settings=settings), host=args.host, port=args.port)
    elif args.cmd == "load":
        engine = make_engine(settings.database_url)
        for f in args.files:
            data = Path(f).read_bytes()
            source = detect_source(list(read_table(data, f).columns))
            if not source:
                raise SystemExit(f"{f}: не похоже ни на светофор, ни на отчёт по отрезкам")
            print(f, load_file(engine, source, data, Path(f).name))
    elif args.cmd == "sync":
        from .sync import run_sync
        engine = make_engine(settings.database_url)
        while True:
            try:
                run_sync(engine, settings)
            except Exception:  # следующая попытка по расписанию
                logging.exception("sync failed")
                if not args.every:
                    raise
            if not args.every:
                break
            time.sleep(args.every)


if __name__ == "__main__":
    main()
