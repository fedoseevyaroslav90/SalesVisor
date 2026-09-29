"""Веб-приложение: API и страница для менеджеров и отдела сервиса."""
from __future__ import annotations

import hmac
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sqlalchemy import delete, insert
from sqlalchemy.engine import Engine

from . import queries
from .config import Settings, get_settings
from .bitrix import Bitrix, live_info
from .db import bitrix_links, comments, make_engine
from .ingest import detect_source, load_file, read_table
from .metabase import MetabaseError
from .sync import SyncError, run_sync

STATIC = Path(__file__).parent / "static"


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="", max_length=100)
    pos: str | None = None


class BitrixLinkIn(BaseModel):
    task_id: str = Field(default="", max_length=20)
    deal_id: str = Field(default="", max_length=20)
    author: str = Field(default="", max_length=100)


def _id_or_none(v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not re.fullmatch(r"\d{1,12}", v):
        raise HTTPException(400, "Номер задачи или сделки должен состоять из цифр")
    return v


def create_app(engine: Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    app = FastAPI(title="SalesVisor", docs_url="/api/docs")

    @app.middleware("http")
    async def portal_guard(request: Request, call_next):
        # За порталом «Инкаб ИИ» приложение принимает только запросы, которые портал подписал общим секретом
        if settings.portal_token and request.url.path != "/api/health":
            got = request.headers.get("x-salesvisor-token", "")
            if not hmac.compare_digest(got.encode(), settings.portal_token.encode()):
                return JSONResponse({"detail": "Откройте SalesVisor через портал «Инкаб ИИ»"}, status_code=401)
        return await call_next(request)

    def user_of(request: Request, fallback: str = "") -> str:
        # ФИО и алиас сотрудника ставит портал (ФИО в percent-encoding), иначе заголовок другого прокси
        person = unquote(request.headers.get("x-salesvisor-person", "")).strip()
        alias = request.headers.get("x-salesvisor-user", "").strip()
        return person or alias or request.headers.get("x-remote-user") or fallback.strip() or "без имени"

    @app.get("/api/health")
    def api_health():
        return {"ok": True}

    @app.get("/api/me")
    def api_me(request: Request):
        return {"user": user_of(request), "alias": request.headers.get("x-salesvisor-user", "")}

    @app.get("/api/meta")
    def api_meta(request: Request):
        portal_user = user_of(request, "") if settings.portal_token else ""
        return {**queries.meta(engine), "portal_user": portal_user, "bitrix_task_url": settings.bitrix_task_url,
                "bitrix_deal_url": settings.bitrix_deal_url, "bitrix_ready": bool(settings.bitrix_webhook_url),
                "metabase_ready": settings.metabase_ready, "import_dir": bool(settings.import_dir)}

    @app.get("/api/orders")
    def api_orders(scope: str = "open", manager: str = "", dept: str = "", color: str = "", q: str = "", overdue: bool = False):
        return queries.list_orders(engine, scope=scope, manager=manager, dept=dept, color=color, q=q, overdue_only=overdue)

    @app.get("/api/orders/{order_no}")
    def api_order(order_no: str):
        card = queries.order_card(engine, order_no)
        if not card:
            raise HTTPException(404, "Заказ не найден")
        return card

    @app.post("/api/orders/{order_no}/comments")
    def api_comment(order_no: str, body: CommentIn, request: Request):
        with engine.begin() as conn:
            conn.execute(insert(comments).values(order_no=order_no, pos=body.pos, text=body.text.strip(),
                                                 author=user_of(request, body.author)))
        return {"ok": True}

    @app.put("/api/orders/{order_no}/bitrix")
    def api_bitrix_link(order_no: str, body: BitrixLinkIn, request: Request):
        task, deal = _id_or_none(body.task_id), _id_or_none(body.deal_id)
        with engine.begin() as conn:
            conn.execute(delete(bitrix_links).where(bitrix_links.c.order_no == order_no))
            if task or deal:
                conn.execute(insert(bitrix_links).values(order_no=order_no, task_id=task, deal_id=deal,
                                                         set_by=user_of(request, body.author)))
        return {"ok": True}

    @app.get("/api/orders/{order_no}/bitrix/live")
    def api_bitrix_live(order_no: str):
        if not settings.bitrix_webhook_url:
            return {"configured": False}
        card = queries.order_card(engine, order_no)
        if not card:
            raise HTTPException(404, "Заказ не найден")
        b = card["bitrix"]
        return {"configured": True, **live_info(Bitrix(settings), b["task_id"], b["deal_id"])}

    @app.get("/api/changes")
    def api_changes(days: int = 7, manager: str = ""):
        return queries.recent_changes(engine, days=max(1, min(days, 90)), manager=manager)

    @app.post("/api/upload")
    async def api_upload(file: UploadFile = File(...), source: str = Form("")):
        data = await file.read()
        try:
            if not source:
                source = detect_source(list(read_table(data, file.filename or "").columns)) or ""
                if not source:
                    raise ValueError("Не похоже ни на светофор, ни на отчёт по отрезкам")
            return load_file(engine, source, data, file.filename or source)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/sync")
    def api_sync():
        try:
            return run_sync(engine, settings)
        except (MetabaseError, SyncError) as e:
            raise HTTPException(400, str(e)) from e

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


# Для uvicorn salesvisor.web:app
def __getattr__(name):
    if name == "app":
        return create_app()
    raise AttributeError(name)
