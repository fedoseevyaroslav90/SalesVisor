"""Схема базы. Работает с PostgreSQL на сервере и с SQLite для локальной проверки."""
from __future__ import annotations

import os

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

metadata = MetaData()

# Каждая загруженная выгрузка
snapshots = Table(
    "snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20), nullable=False),  # svetofor | segments
    Column("origin", String(200)),                 # имя файла или metabase:card/522
    Column("rows", Integer),
    Column("loaded_at", DateTime, nullable=False, server_default=func.now()),
)

# Позиция заказа: сводка светофора и отчёта по отрезкам
positions = Table(
    "positions", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("pos", String(10), primary_key=True),
    Column("customer", String(300)),
    Column("sales_dept", String(100)),
    Column("manager", String(50)),        # логин SAP из поля «Создал»
    Column("product", String(300)),
    # Светофор
    Column("first_decade", String(10)),   # первая требуемая дата клиента (декада)
    Column("first_decade_end", Date),
    Column("current_decade", String(10)), # где позиция сейчас
    Column("current_decade_end", Date),
    Column("plan_ship_date", Date),       # ПланДатаОтгрузки
    Column("z4_date", Date),              # перевод в Z4 «Готов к производству»
    Column("shift_days", Integer),
    Column("color", String(10)),          # green | yellow | red
    Column("in_svetofor", Boolean, nullable=False, default=False),
    # Отчёт по отрезкам
    Column("required_date", Date),        # Треб. дата поставки
    Column("invoice_plan_date", Date),    # План дата отгрузки (фактуры)
    Column("to_production_date", Date),
    Column("reject_code", String(10)),
    Column("reject_text", String(100)),
    Column("segments_total", Integer, default=0),
    Column("segments_produced", Integer, default=0),
    Column("segments_stock", Integer, default=0),
    Column("segments_ready", Integer, default=0),
    Column("segments_in_transit", Integer, default=0),
    Column("segments_shipped", Integer, default=0),
    Column("segments_invoiced", Integer, default=0),
    Column("last_fact_ship_date", Date),
    Column("length_plan", Float),
    Column("unit", String(10)),
    Column("amount_rub", Float),
    Column("bitrix_raw", String(300)),
    Column("bitrix_task", String(20)),
    Column("stage", String(40)),
    Column("closed", Boolean, nullable=False, default=False),
    Column("first_seen_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now()),
)

# Отрезки последней выгрузки
segments = Table(
    "segments", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("pos", String(10), primary_key=True),
    Column("seg_no", String(10), primary_key=True),
    Column("length", Float),
    Column("unit", String(10)),
    Column("fact_length", Float),
    Column("produced", Boolean),
    Column("stock", Boolean),
    Column("ready", Boolean),
    Column("in_transit", Boolean),
    Column("shipped", Boolean),
    Column("invoiced", Boolean),
    Column("fact_ship_date", Date),
    Column("prod_order", String(30)),
    Column("warehouse", String(100)),
)

# Журнал изменений между выгрузками
change_log = Table(
    "change_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_no", String(20), nullable=False, index=True),
    Column("pos", String(10), nullable=False),
    Column("field", String(40), nullable=False),
    Column("old", String(100)),
    Column("new", String(100)),
    Column("snapshot_id", Integer, ForeignKey("snapshots.id")),
    Column("at", DateTime, nullable=False, server_default=func.now()),
    Column("bitrix_sent", Boolean, nullable=False, default=False),
)

# Ручная связь заказа SAP с Битрикс24. Приоритетнее номера задачи, найденного в отчёте SAP
bitrix_links = Table(
    "bitrix_links", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("task_id", String(20)),
    Column("deal_id", String(20)),
    Column("set_by", String(100)),
    Column("set_at", DateTime, nullable=False, server_default=func.now()),
)

comments = Table(
    "comments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_no", String(20), nullable=False, index=True),
    Column("pos", String(10)),
    Column("author", String(100)),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")) or ".", exist_ok=True)
    if url.endswith(":memory:"):
        # Одна общая база в памяти для тестов, в том числе из потоков веб-сервера
        engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, pool_pre_ping=True)
    metadata.create_all(engine)
    return engine
