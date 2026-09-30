"""Схема базы. Работает с PostgreSQL на сервере и с SQLite для локальной проверки."""
from __future__ import annotations

import os

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, func, inspect, text,
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
    Column("mp_rub", Float),              # МП (расчётно) позиции — сумма по отрезкам, как в пилоте VOLS-Zakazy
    Column("mz_rub", Float),              # МЗ (расчётно) позиции — сумма по отрезкам
    Column("bitrix_raw", String(300)),
    Column("bitrix_task", String(20)),
    Column("stage", String(40)),
    Column("closed", Boolean, nullable=False, default=False),
    # План производства SAP: линия (рабочее место) и плановое окончание последней операции
    Column("line", String(40)),
    Column("plan_lines", String(200)),    # все линии позиции через запятую
    Column("plan_end_date", Date),
    Column("plan_end_time", String(5)),
    Column("dse", String(40)),
    Column("plan_msg", String(200)),
    Column("plan_qty", Float),            # Кол-во поступления (план) по ZPP context
    Column("plan_fact_qty", Float),       # Кол-во поступления (факт) MES
    # Диспетчерский отчёт
    Column("disp_decade", String(40)),    # признак декады, например «27. ОКТЯБРЬ 1декада»
    Column("disp_counted", Boolean),      # ПО = «считать»: позиция входит в план декады
    Column("disp_ready", String(40)),     # готов / не готов / готово 3 из 5 (назначена партия)
    Column("disp_batch", String(40)),
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

# Диспетчерский отчёт: план и факт позиции в единицах отчёта. Заменяется при каждой загрузке
dispatcher = Table(
    "dispatcher", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("pos", String(10), primary_key=True),
    Column("decade", String(40)),
    Column("decade_no", Integer),         # порядковый номер декады в году из признака («27.» → 27)
    Column("customer", String(300)),
    Column("product", String(300)),
    Column("counted", Boolean),
    Column("km", Float), Column("pcs", Float), Column("ov_km", Float), Column("mz", Float), Column("vp", Float),
    Column("km_ready", Float), Column("pcs_ready", Float), Column("ov_km_ready", Float),
    Column("mz_ready", Float), Column("vp_ready", Float),
    Column("segs", Integer), Column("segs_ready", Integer),
    Column("batch", String(40)),
    # применена ли строка к позиции; не true — позиции ещё не было, строка ждёт отрезков или светофора
    Column("applied", Boolean),
)

# Порядок загрузки не важен (30.09.2026): план и диспетчерский дополняют только известные позиции, поэтому их
# последние строки хранятся целиком, а те, чьей позиции ещё нет (applied не true), применяются после загрузки,
# в которой позиция появилась. План производства ZPP context — строки последнего прогона ППМ по позициям
plan_rows = Table(
    "plan_rows", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("pos", String(10), primary_key=True),
    Column("line", String(40)),
    Column("plan_lines", String(200)),
    Column("plan_end_date", Date),
    Column("plan_end_time", String(5)),
    Column("dse", String(40)),
    Column("plan_msg", String(200)),
    Column("plan_qty", Float),
    Column("plan_fact_qty", Float),
    Column("applied", Boolean),
)

# Плановое окончание из листа 1S0D диспетчерского — последней книги с этим листом
disp_end_dates = Table(
    "disp_end_dates", metadata,
    Column("order_no", String(20), primary_key=True),
    Column("pos", String(10), primary_key=True),
    Column("plan_end_date", Date),
    Column("plan_end_time", String(5)),
    Column("applied", Boolean),
)

# Сообщения о качестве (выявленные несоответствия) из ZPP context. Копятся: прогон ППМ их не стирает
quality_msgs = Table(
    "quality_msgs", metadata,
    Column("msg_no", String(20), primary_key=True),
    Column("order_no", String(20), nullable=False, index=True),
    Column("pos", String(10)),
    Column("line", String(40)),
    Column("text", String(300)),
    Column("product", String(300)),
    Column("plan_end_date", Date),
    Column("first_seen_at", DateTime, nullable=False, server_default=func.now()),
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

# Преднастройки сотрудника (30.09.2026): сохранённые отборы, последний отбор, свой логин SAP («Создал»).
# Ключ — алиас сотрудника портала из X-SalesVisor-User; без портала (локальный запуск) — «local»
user_prefs = Table(
    "user_prefs", metadata,
    Column("alias", String(64), primary_key=True),
    Column("person", String(200)),
    Column("data", Text, nullable=False),
    Column("updated_at", DateTime, nullable=False, server_default=func.now()),
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")) or ".", exist_ok=True)
    if url.endswith(":memory:"):
        # Одна общая база в памяти для тестов, в том числе из потоков веб-сервера
        engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "postgresql":
        # Веб и синхронизация стартуют одновременно: схему обновляет один процесс, второй ждёт
        # (иначе второй падает на «column/relation already exists» и поднимается только перезапуском)
        with engine.connect() as lock:
            lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
            lock.commit()
            try:
                metadata.create_all(engine)
                _add_missing_columns(engine)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})
                lock.commit()
    else:
        metadata.create_all(engine)
        _add_missing_columns(engine)
    return engine


_SCHEMA_LOCK_KEY = 5_417_320_931


def _add_missing_columns(engine: Engine) -> None:
    """Базы, созданные прежней версией, получают новые колонки без потери данных."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for c in table.columns:
                if c.name not in have:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {c.name} {c.type.compile(engine.dialect)}'))
