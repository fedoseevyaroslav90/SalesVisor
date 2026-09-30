"""Веб-приложение: API и страница для менеджеров и отдела сервиса."""
from __future__ import annotations

import hmac
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from openpyxl.utils.exceptions import InvalidFileException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from . import export, pdo, queries
from .config import Settings, get_settings
from .bitrix import Bitrix, live_info
from .db import bitrix_links, comments, make_engine, user_prefs
from .ingest import LoadBusy, load_file, load_lock, sniff_source
from .metabase import MetabaseError
from .sync import SyncError, run_sync

STATIC = Path(__file__).parent / "static"
# Предел загружаемой выгрузки: CSV отчёта по отрезкам — около 70 МБ, xlsx того же отчёта — около 30 МБ
MAX_UPLOAD_MB = 150
# Поля отбора в строке запроса /api/orders, /api/positions, /api/export.xlsx (см. queries.Filters)
FILTER_KEYS = ("q", "order", "customer", "line", "stage", "due_from", "due_to", "first_from", "first_to", "shift_min", "reject", "group",
               "flags")


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="", max_length=100)
    pos: str | None = Field(default=None, max_length=10)


class BitrixLinkIn(BaseModel):
    task_id: str = Field(default="", max_length=20)
    deal_id: str = Field(default="", max_length=20)
    author: str = Field(default="", max_length=100)


class SavedView(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(default="", max_length=2000)


class PrefsIn(BaseModel):
    """Преднастройки сотрудника: поле, которого нет в запросе, не меняется."""
    views: list[SavedView] | None = Field(default=None, max_length=30)   # «Мои отборы»
    last: str | None = Field(default=None, max_length=2000)             # последний отбор (строка адреса)
    sap_login: str | None = Field(default=None, max_length=50)          # свой логин SAP («Создал») — «Мои заказы»
    hidden_cols: dict[str, list[str]] | None = Field(default=None, max_length=4)  # скрытые столбцы таблиц: {"orders": [...]}


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
    # Документация API выключена: Swagger и ReDoc грузят скрипты с внешнего CDN, а за порталом страницы
    # сервиса открываются на origin портала, где в localStorage лежит ключ сотрудника
    app = FastAPI(title="SalesVisor", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def portal_guard(request: Request, call_next):
        # За порталом «Инкаб ИИ» приложение принимает только запросы, которые портал подписал общим секретом
        if settings.portal_token and request.url.path != "/api/health":
            got = request.headers.get("x-salesvisor-token", "")
            if not hmac.compare_digest(got.encode(), settings.portal_token.encode()):
                return JSONResponse({"detail": "Откройте SalesVisor через портал «Инкаб ИИ»"}, status_code=401)
        return await call_next(request)

    def user_of(request: Request, fallback: str = "") -> str:
        # ФИО и алиас сотрудника ставит портал (ФИО в percent-encoding). За порталом — только они:
        # имя из тела запроса или заголовка другого прокси подменило бы автора
        person = unquote(request.headers.get("x-salesvisor-person", "")).strip()
        alias = request.headers.get("x-salesvisor-user", "").strip()
        if settings.portal_token:
            return person or alias or "без имени"
        return person or alias or request.headers.get("x-remote-user") or fallback.strip() or "без имени"

    def can_upload(request: Request) -> bool:
        # Роль ставит портал: editor — загрузка выгрузок и «Обновить сейчас», viewer — остальное.
        # Без портала (PORTAL_TOKEN пуст, локальный запуск) можно всё
        return not settings.portal_token or request.headers.get("x-salesvisor-role", "") == "editor"

    def require_upload(request: Request) -> None:
        if not can_upload(request):
            raise HTTPException(403, "Загружать выгрузки может сотрудник с правом загрузки — его выдаёт руководитель программы ИИ")

    def order_no_ok(order_no: str) -> str:
        if not re.fullmatch(r"[\w.-]{1,20}", order_no):
            raise HTTPException(404, "Заказ не найден")
        return order_no

    @app.get("/api/health")
    def api_health():
        return {"ok": True}

    @app.get("/api/me")
    def api_me(request: Request):
        return {"user": user_of(request), "alias": request.headers.get("x-salesvisor-user", "")}

    def alias_of(request: Request) -> str:
        """Чьи преднастройки: алиас сотрудника портала; без портала (локальный запуск) — «local»."""
        alias = request.headers.get("x-salesvisor-user", "").strip()[:64]
        if not alias and settings.portal_token:
            raise HTTPException(400, "Портал не передал сотрудника — откройте раздел через портал «Инкаб ИИ»")
        return alias or "local"

    def prefs_of(alias: str) -> dict:
        with engine.connect() as conn:
            row = conn.execute(select(user_prefs.c.data).where(user_prefs.c.alias == alias)).first()
        try:
            return json.loads(row[0]) if row else {}
        except ValueError:
            return {}

    @app.get("/api/prefs")
    def api_prefs(request: Request):
        """Преднастройки того, кто открыл раздел (у каждого сотрудника портала — свои)."""
        alias = alias_of(request)
        p = prefs_of(alias)
        return {"alias": alias, "views": p.get("views", []), "last": p.get("last", ""), "sap_login": p.get("sap_login", ""),
                "hidden_cols": p.get("hidden_cols", {})}

    @app.put("/api/prefs")
    def api_prefs_put(body: PrefsIn, request: Request):
        alias = alias_of(request)
        p = prefs_of(alias)
        for k, v in body.model_dump(exclude_none=True).items():
            p[k] = v
        if "hidden_cols" in p:  # только короткие имена столбцов, не больше 30 на таблицу
            p["hidden_cols"] = {t[:20]: [c[:30] for c in cols[:30]] for t, cols in p["hidden_cols"].items()}
        data = json.dumps(p, ensure_ascii=False)
        person = user_of(request)[:200]
        with engine.begin() as conn:
            if not conn.execute(update(user_prefs).where(user_prefs.c.alias == alias)
                                .values(data=data, person=person, updated_at=func.now())).rowcount:
                conn.execute(insert(user_prefs).values(alias=alias, person=person, data=data))
        return {"ok": True}

    @app.get("/api/meta")
    def api_meta(request: Request):
        portal_user = user_of(request, "") if settings.portal_token else ""
        return {**queries.meta(engine), "portal_user": portal_user, "can_upload": can_upload(request),
                "bitrix_task_url": settings.bitrix_task_url,
                "bitrix_deal_url": settings.bitrix_deal_url, "bitrix_ready": bool(settings.bitrix_webhook_url),
                "metabase_ready": settings.metabase_ready, "import_dir": bool(settings.import_dir)}

    def filters_of(request: Request) -> queries.Filters:
        """Отбор из строки запроса: те же поля для заказов, позиций и выгрузки (цвет позиции — colors)."""
        p = request.query_params
        return queries.Filters.from_query(**{k: p.get(k, "") for k in FILTER_KEYS}, color=p.get("colors", ""))

    @app.get("/api/orders")
    def api_orders(request: Request, scope: str = "open", manager: str = "", dept: str = "", color: str = "",
                   overdue: bool = False):
        return queries.list_orders(engine, scope=scope, manager=manager, dept=dept, color=color, overdue_only=overdue,
                                   filters=filters_of(request))

    @app.get("/api/positions")
    def api_positions(request: Request, scope: str = "open", manager: str = "", dept: str = "", sort: str = "",
                      offset: int = 0, limit: int = 300):
        return queries.list_positions(engine, scope=scope, manager=manager, dept=dept, filters=filters_of(request),
                                      sort=sort, offset=max(0, offset), limit=max(1, min(limit, 1000)))

    @app.get("/api/stats")
    def api_stats(request: Request, scope: str = "all", manager: str = "", dept: str = ""):
        """Статистика и отчёт по срокам по текущему отбору (по умолчанию — вместе с отгруженными: для OTD)."""
        return queries.stats(engine, scope=scope, manager=manager, dept=dept, filters=filters_of(request))

    @app.get("/api/export.xlsx")
    def api_export(request: Request, view: str = "orders", scope: str = "open", manager: str = "", dept: str = "",
                   color: str = "", overdue: bool = False, sort: str = "", note: str = ""):
        """Текущий отбор в Excel: view=orders (заказы, с плиткой color/overdue) или positions."""
        f = filters_of(request)
        if view == "positions":
            rows = queries.list_positions(engine, scope=scope, manager=manager, dept=dept, filters=f, sort=sort,
                                          limit=10**6)["rows"]
        else:
            rows = queries.list_orders(engine, scope=scope, manager=manager, dept=dept, color=color, overdue_only=overdue,
                                       filters=f)
            if sort:
                rows = queries.sort_rows(rows, sort, lambda o: 0)
        data = export.build_xlsx(rows, "orders" if view != "positions" else "positions",
                                 (note or "SalesVisor")[:300] + f" · выгружено {len(rows)} строк")
        name = export.file_name("orders" if view != "positions" else "positions")
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=\"salesvisor.xlsx\"; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/orders/{order_no}")
    def api_order(order_no: str):
        card = queries.order_card(engine, order_no_ok(order_no))
        if not card:
            raise HTTPException(404, "Заказ не найден")
        return card

    @app.post("/api/orders/{order_no}/comments")
    def api_comment(order_no: str, body: CommentIn, request: Request):
        order_no_ok(order_no)
        with engine.begin() as conn:
            conn.execute(insert(comments).values(order_no=order_no, pos=body.pos, text=body.text.strip(),
                                                 author=user_of(request, body.author)))
        return {"ok": True}

    @app.put("/api/orders/{order_no}/bitrix")
    def api_bitrix_link(order_no: str, body: BitrixLinkIn, request: Request):
        order_no_ok(order_no)
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
        card = queries.order_card(engine, order_no_ok(order_no))
        if not card:
            raise HTTPException(404, "Заказ не найден")
        b = card["bitrix"]
        bx = Bitrix(settings)
        try:
            return {"configured": True, **live_info(bx, b["task_id"], b["deal_id"])}
        finally:
            bx.close()

    @app.get("/api/day")
    def api_day(date: str = "", manager: str = "", dept: str = "", line: str = "", backlog: bool = False, shift: bool = False):
        from datetime import date as _date
        try:
            day = _date.fromisoformat(date) if date else _date.today()
        except ValueError as e:
            raise HTTPException(400, "Дата в формате ГГГГ-ММ-ДД") from e
        return queries.day_plan(engine, day, manager=manager, dept=dept, line=line, with_backlog=backlog, shift_day=shift)

    @app.get("/api/dispatcher")
    def api_dispatcher(decade: str = "", manager: str = "", dept: str = ""):
        return queries.dispatcher_summary(engine, decade=decade, manager=manager, dept=dept)

    @app.get("/api/pdo/pulse")
    def api_pdo_pulse(decades: int = 12):
        """Пульс ПДО: своевременность и полнота решений по декадам, сигналы, загрузка переделов вперёд."""
        return pdo.pulse(engine, decades=max(4, min(decades, 40)))

    @app.get("/api/changes")
    def api_changes(days: int = 7, manager: str = ""):
        return queries.recent_changes(engine, days=max(1, min(days, 90)), manager=manager)

    def _load_upload(data: bytes, filename: str, source: str) -> dict:
        """Разбор и запись — в пуле потоков: pandas на десятках мегабайт не должен держать остальные запросы."""
        try:
            source = source or sniff_source(data, filename) or ""
            if not source:
                raise ValueError("Не похоже ни на одну из выгрузок: отрезки, светофор, план производства, диспетчерский")
            with load_lock(engine, wait=False):
                return load_file(engine, source, data, filename or source)
        except LoadBusy as e:
            raise HTTPException(409, str(e)) from e
        except (ValueError, KeyError, zipfile.BadZipFile, InvalidFileException) as e:
            raise HTTPException(400, f"Файл не разобран: {str(e)[:300]}") from e

    @app.post("/api/upload")
    async def api_upload(request: Request, file: UploadFile = File(...), source: str = Form("")):
        require_upload(request)
        if (file.size or 0) > MAX_UPLOAD_MB * 2**20:
            raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
        data = await file.read()
        return await run_in_threadpool(_load_upload, data, file.filename or "", source)

    @app.post("/api/sync")
    def api_sync(request: Request):
        require_upload(request)
        try:
            return run_sync(engine, settings, wait=False)
        except LoadBusy as e:
            raise HTTPException(409, str(e)) from e
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
