"""Веб-приложение: API и страница для менеджеров и отдела сервиса."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import insert
from sqlalchemy.engine import Engine

from . import queries
from .config import Settings, get_settings
from .db import comments, make_engine
from .ingest import detect_source, load_file, read_table
from .metabase import MetabaseError
from .sync import run_sync

STATIC = Path(__file__).parent / "static"


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="", max_length=100)
    pos: str | None = None


def create_app(engine: Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    app = FastAPI(title="SalesVisor", docs_url="/api/docs")

    def user_of(request: Request, fallback: str) -> str:
        # Если портал ставит авторизацию перед приложением, берём имя из заголовка прокси
        return request.headers.get("x-remote-user") or fallback.strip() or "без имени"

    @app.get("/api/meta")
    def api_meta():
        return {**queries.meta(engine), "bitrix_task_url": settings.bitrix_task_url,
                "metabase_ready": bool(settings.metabase_url and (settings.metabase_api_key or settings.metabase_user))}

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
        except MetabaseError as e:
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
