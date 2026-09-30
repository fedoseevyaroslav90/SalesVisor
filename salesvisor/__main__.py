"""Командная строка:
    python -m salesvisor serve [--port 8000]
    python -m salesvisor load ФАЙЛ [ФАЙЛ ...]       загрузить выгрузки вручную (SAP, отчёты ПДО)
    python -m salesvisor sync [--every СЕКУНД]      забрать выгрузки из Metabase или папки IMPORT_DIR (разово или по кругу)
    python -m salesvisor reload [ВИД ...]           перечитать последнюю выгрузку каждого вида из IMPORT_DIR/done
                                                    (после смены логики разбора; по умолчанию — отчёт по отрезкам)
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from . import pdo
from .config import get_settings
from .db import make_engine
from .ingest import load_file, load_lock, sniff_source


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx на INFO пишет полный адрес запроса — а в адресе вебхука Битрикс24 ключ
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="salesvisor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    l = sub.add_parser("load")
    l.add_argument("files", nargs="+")
    y = sub.add_parser("sync")
    y.add_argument("--every", type=int, default=0, help="повторять каждые N секунд")
    r = sub.add_parser("reload")
    r.add_argument("kinds", nargs="*", default=["segments"], help="segments, svetofor, plan, dispatcher")
    args = ap.parse_args()

    settings = get_settings()
    if args.cmd == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(settings=settings), host=args.host, port=args.port)
    elif args.cmd == "load":
        engine = make_engine(settings.database_url)
        todo = []
        for f in args.files:
            data = Path(f).read_bytes()
            source = sniff_source(data, f)
            if not source:
                raise SystemExit(f"{f}: не похоже ни на одну из выгрузок (отрезки, светофор, план, диспетчерский, отчёты ПДО)")
            todo.append((f, data, source))
        # отчёты ПДО — в хронологии декад и версий, дата публикации — время изменения файла
        todo.sort(key=lambda x: pdo.sort_key(x[2], Path(x[0]).name) if x[2] in pdo.KINDS else ())
        for f, data, source in todo:
            published = datetime.fromtimestamp(Path(f).stat().st_mtime) if source in pdo.KINDS else None
            with load_lock(engine):
                print(f, load_file(engine, source, data, Path(f).name, published_at=published))
    elif args.cmd == "reload":
        if not settings.import_dir:
            raise SystemExit("IMPORT_DIR не задан")
        engine = make_engine(settings.database_url)
        done = sorted((f for f in (Path(settings.import_dir) / "done").iterdir() if f.is_file()),
                      key=lambda f: f.stat().st_mtime, reverse=True)
        want = set(args.kinds)
        for f in done:  # от свежих к старым, по одному файлу каждого вида
            if not want:
                break
            data = f.read_bytes()
            source = sniff_source(data, f.name)
            if source in want:
                want.discard(source)
                with load_lock(engine):
                    print(f.name, load_file(engine, source, data, f.name))
        if want:
            print("не найдено в done/:", ", ".join(sorted(want)))
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
