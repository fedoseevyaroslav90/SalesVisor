"""Путь в производстве: снимки ПДО, дефициты, загрузка переделов, пульс, сборщик из Битрикса (этап 1, 01.10.2026)."""
import io
from datetime import date, datetime, timedelta

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import select

from salesvisor import pdo
from salesvisor.bitrix_pdo import collect
from salesvisor.brands import decode
from salesvisor.config import Settings
from salesvisor.db import bitrix_files, change_log, make_engine, positions
from salesvisor.ingest import load_file, load_segments, sniff_source
from salesvisor.queries import Filters, list_positions, order_card

from test_core import seg_row

PLAN_HDR = ["Требуемая дата поставки заказа клиента", "Элемент поступления", "Дата конца", "Заказ клиента", "Позиция заказа",
            "Порядковый номер", "Наименование материала ГП", "Кол-во по заказу клиента по отрезку", "Базовая ЕИ",
            "Принят / не принят", "Причина не принятия", "Материал / РЦ", "Дата, на которую перенести", "Номер ДСЕ",
            "Валовая прибыль, руб.", "Рабочее место"]
FACT_HDR = ["Заказ клиента", "Позиция заказа", "Элемент поступления", "Наименование материала поступления",
            "Требуемая дата поставки заказа клиента", "Кол-во поступления (план)", "Количество поступления (факт)",
            "Дата поступления (факт)", "Дата конца", "готов/не готов", "причина", "Классификатор", "ответственный"]


def xlsx(sheets: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def plan_row(order, pos, status, reason=None, bottleneck=None, move=None, wc="WOEL60-5_1100_001", req=date(2026, 10, 20)):
    return [req, "1", req, order, pos, "1", "ДПД-HF-8У (1х8) 7кН 12.6", 2.0, "КМ", status, reason, bottleneck, move, None, 100000,
            wc.replace("W", "", 1).split("_")[0]]


def seed(engine):
    segs = pd.DataFrame([seg_row("1200000301", "10", "1", "20 октября, 2026"), seg_row("1200000301", "20", "1", "20 октября, 2026")])
    segs["Материал"] = ["ДПД-HF-8У (1х8) 7кН 12.6", "ОК ОКД-2Д-П-1У 1.4кН 2.4х5.6"]
    load_segments(engine, segs, "seg")


def test_brands_decode():
    assert decode("ДПД-HF-8У (1х8) 7кН 12.6")["group"] == "грунт — стеклопластик"
    assert decode("ОПЫТ ДПС-П-16У 7кН 11.2")["group"] == "грунт — сталь"
    assert decode("МТС ДПТс-П-32У (4х8) 10кН 12.6")["group"] == "подвес"
    assert decode("ДПТс-П-16У 2.7кН 9.9")["group"] == "канализация/трубы"
    assert decode("ОК ОКД-2Д-П-1У 1.4кН 2.4х5.6")["group"] == "дроп"
    assert decode("ОКГТ-С-24G.652.D-12.0-46-68п")["group"] == "грозотрос"
    assert decode("PTEC-C-11х11-PP-6.35х0.89-316L-0.83T-150")["group"] == "спецкабель"
    assert decode("ЭРТХ ДОТс-П-64У (8х8) 4кН 10.9")["brand"] == "ДОТС"


def test_pdo_plan_fact_events_and_flags():
    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    name1 = "Отчет по принятым заказам в декаду 20.10.2026 ver.1.xlsx"
    data1 = xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "не принят", "полная загрузка", "OEL60-5", date(2026, 10, 31)),
                             plan_row("1200000301", "20", "принят")]})
    # отчёт ПДО не должен распознаться как план ZPP (у них общие колонки)
    assert sniff_source(data1, name1) == "pdo_plan"
    r = load_file(engine, "pdo_plan", data1, name1, published_at=datetime(2026, 10, 9, 10))
    assert (r["rejects"], r["events"]) == (1, 1)
    data2 = xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "принят"), plan_row("1200000301", "20", "принят")]})
    r = load_file(engine, "pdo_plan", data2, "Отчет по принятым заказам в декаду 20.10.2026 ver.2.xlsx",
                  published_at=datetime(2026, 10, 10, 9))
    assert r["events"] == 1
    fact = xlsx({"Sheet1": [FACT_HDR, ["1200000301", "10", "4300", "ДПД", date(2026, 10, 20), 2.0, 0, None, date(2026, 10, 20),
                                       "не готов", "поломка экструдера", "Поломка", "Производство"]]})
    assert sniff_source(fact, "Отчет по принятым заказам и факт в декаду 20.10.2026.xlsx") == "pdo_fact"
    load_file(engine, "pdo_fact", fact, "Отчет по принятым заказам и факт в декаду 20.10.2026.xlsx", published_at=datetime(2026, 10, 22))
    with engine.connect() as c:
        ev = [(r.field, r.old, r.new) for r in c.execute(select(change_log).where(change_log.c.field.in_(("pdo_plan", "pdo_fact")))
                                                         .order_by(change_log.c.id))]
        p10 = c.execute(select(positions).where(positions.c.order_no == "1200000301", positions.c.pos == "10")).first()
    assert ev[0] == ("pdo_plan", None, "не принят в 20.10: полная загрузка · OEL60-5 → 31.10")
    assert ev[1][0] == "pdo_plan" and ev[1][1] == "не принят в 20.10" and ev[1][2].startswith("принят в 20.10")
    assert ev[2] == ("pdo_fact", None, "не готов в 20.10: Поломка")
    # итоговое решение по декаде — принят (версия 2 заменила версию 1), поэтому декад с отказом 0
    assert (p10.pdo_last_status, p10.pdo_rejects, p10.pdo_first_date, p10.pdo_fact_status) == ("принят", 0, date(2026, 10, 20), "не готов")
    assert p10.product_group == "грунт — стеклопластик"
    card = order_card(engine, "1200000301", today=date(2026, 10, 23))
    path = {p["pos"]: p["path"] for p in card["positions"]}
    assert path["10"]["plan"][0]["status"] == "принят" and path["10"]["plan"][0]["version"] == 2
    assert path["10"]["fact"][0]["classifier"] == "Поломка"
    # отбор по группе продукции
    res = list_positions(engine, scope="all", filters=Filters.from_query(group="дроп"), today=date(2026, 10, 23))
    assert [r["pos"] for r in res["rows"]] == ["20"]


def mat_book(rows):
    hdr = ["Дата доступности/потребности", "Номер продукта", "Название продукта", "Название группы", "Кол-во пост./потребность",
           "Страховой запас", "Ожидаемые поступления", "Крайняя дата ожидаемого поступления", "Количество к крайней дате",
           "Базисная ЕИ", "Заказ клиента", "Позиция заказа клиента", "ОЗМ по заказу клиента", "Наименование ОЗМ заказа",
           "Запас_на_1010", "Запас_на_Таможенном_складе", "Запас_на_складе_ОХ", "Дефицит/Профицит", "Заказ_клиента-Позиция_заказа",
           "ПО", "Обеспеченность", "Треб.дата поставки", "Клиент", "Менеджер", "Не обеспечено", "Заказ-позиция", "КЛИЕНТ3", "ДАТА",
           "ДАТА ОЖИДАЕМОЙ ПОСТАВКИ", "Коментарий нормальный"]
    out = [hdr]
    for o, ps, mat, eta, state, comment in rows:
        r = [None] * len(hdr)
        r[1], r[2], r[3], r[4], r[7], r[10], r[11], r[20], r[21], r[29] = (mat, "Стеклопластик 1,0 мм", "Стеклопластик 1,0", -5,
                                                                              eta, o, ps, state, date(2026, 10, 20), comment)
        out.append(r)
    return xlsx({"Свод_дефицит (Z0+Z4)": [["x"]], "Дефицит": out})


def test_materials_deficits():
    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    d1 = mat_book([("1200000301", "10", "10001093", date(2026, 10, 5), "Не обеспечено", "ждём поставку")])
    assert sniff_source(d1, "Отчет по материалам на 01.10.26 (Z0+Z4).xlsx") == "materials"
    r = load_file(engine, "materials", d1, "Отчет по материалам на 01.10.26 (Z0+Z4).xlsx")
    assert (r["not_provided"], r["events"]) == (1, 0)          # первый отчёт — без событий
    d2 = mat_book([("1200000301", "10", "10001093", date(2026, 10, 18), "Не обеспечено", "ждём поставку"),
                   ("1200000301", "20", "10001093", None, "Не обеспечено", "ждём поставку")])
    r = load_file(engine, "materials", d2, "Отчет по материалам на 02.10.26 (Z0+Z4).xlsx")
    assert r["events"] == 2                                     # сдвиг поставки к сроку и новый дефицит без даты
    assert load_file(engine, "materials", d1, "Отчет по материалам на 01.10.26 (Z0+Z4).xlsx").get("skipped")
    card = order_card(engine, "1200000301", today=date(2026, 10, 3))
    x = {p["pos"]: p["path"]["deficits"] for p in card["positions"]}
    assert (x["10"][0]["eta"], x["10"][0]["eta_changes"], x["10"][0]["real"]) == ("2026-10-18", 1, True)
    res = list_positions(engine, scope="all", filters=Filters.from_query(flags="deficit_real"), today=date(2026, 10, 3))
    assert sorted(r["pos"] for r in res["rows"]) == ["10", "20"]
    d3 = mat_book([("1200000301", "20", "10001093", None, "Не обеспечено", "ждём поставку")])
    load_file(engine, "materials", d3, "Отчет по материалам на 03.10.26 (Z0+Z4).xlsx")
    with engine.connect() as c:
        p10 = c.execute(select(positions.c.deficit_active).where(positions.c.pos == "10")).scalar()
    assert p10 is None                                          # у поз. 10 дефицит закрыт


def load_book(load_pct):
    s = [["Период распределения:"], ["Версия: 010"],
         ["№ заявки", "Вид заказа", "ПО", "Контрагент", "Марка", "Продукт", "ВП", "Дата готовности", "3Д. ШЛАНГОВЫЕ %", None,
          "WOEL60-5_1100_001", None],
         ["Строительная длина"] + [None] * 7 + ["Длина, км", "Загрузка, %", "Длина, км", "Загрузка, %"],
         [None] * 8 + ["Мощность 100", None, "Мощность 50", None],
         ["Итого"] + [None] * 7 + [300, load_pct - 10, 150, load_pct]]
    return xlsx({"Свод": [["2026"]], "11.10 - 20.10": s})


def test_load_overload_and_pulse():
    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    name = "Отчет по принятым заказам в декаду 20.10.2026 ver.1.xlsx"
    load_file(engine, "pdo_plan", xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "не принят", "полная загрузка", "OEL60-5"),
                                                    plan_row("1200000301", "20", "не принят", wc="")]}), name, published_at=datetime(2026, 10, 12))
    r = load_file(engine, "load", load_book(130), "Сводный версия 010  от 05.10.2026.XLS")
    assert (r["records"], r["work_places"]) == (2, 1)
    res = list_positions(engine, scope="all", filters=Filters.from_query(flags="overload"), today=date(2026, 10, 12))
    assert [(r["pos"], r["wc_group"], r["wc_load"]) for r in res["rows"]] == [("10", "3Д. ШЛАНГОВЫЕ", 130.0)]
    res = list_positions(engine, scope="all", filters=Filters.from_query(flags="pdo_rejected"), today=date(2026, 10, 12))
    assert sorted(r["pos"] for r in res["rows"]) == ["10", "20"]
    p = pdo.pulse(engine, today=date(2026, 10, 24))
    d20 = next(d for d in p["decades"] if d["decade"] == "2026-10-20")
    assert (d20["lead_days"], d20["rejects"], d20["no_reason_pct"], d20["no_bottleneck_pct"]) == (-1, 2, 50, 50)
    texts = " | ".join(s["text"] for s in p["signals"])
    assert "Декада 20.10: первый отчёт вышел через 1 дн. после начала декады" in texts
    assert "Декада 20.10: итога «и факт» нет" in texts
    assert "Декада 31.10: предварительного отчёта" in texts
    assert "50 % отказов без причины" in texts
    assert p["load"]["rows"][0]["group"] == "3Д. ШЛАНГОВЫЕ"


class FakeBx:
    def __init__(self, files):
        self.files = files

    def call(self, method, params):
        if method == "task.commentitem.getlist":
            return [{"ID": str(i), "POST_DATE": posted.isoformat(), "ATTACHED_OBJECTS": {str(i): {"ATTACHMENT_ID": aid, "NAME": name}}}
                    for i, (aid, name, posted, _data) in enumerate(self.files)]
        if method == "disk.attachedObject.get":
            return {"DOWNLOAD_URL": "mem://" + params["id"]}
        raise AssertionError(method)

    def close(self):
        pass


def test_bitrix_collector(monkeypatch):
    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    now = datetime.now()
    plan = xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "не принят", "дефицит сырья")]})
    old_mat = mat_book([("1200000301", "10", "10001093", None, "Не обеспечено", "ждём поставку")])
    files = [("a1", "Отчет по принятым заказам в декаду 20.10.2026 ver.1.xlsx", now - timedelta(days=5), plan),
             ("a2", "Отчет по материалам на 01.06.26 (Z0+Z4).xlsx", now - timedelta(days=90), old_mat),
             ("a3", "image (5).png", now - timedelta(days=1), b"png")]
    monkeypatch.setenv("PDO_TASK_IDS", "321346")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://example.invalid/rest/1/x")
    settings = Settings()
    blobs = {"mem://" + aid: data for aid, _n, _p, data in files}
    res = collect(engine, settings, bx=FakeBx(files), download=lambda url: blobs[url])
    assert [r["source"] for r in res] == ["pdo_plan"]
    with engine.connect() as c:
        st = {r.attachment_id: r.status for r in c.execute(select(bitrix_files))}
    assert st == {"a1": "loaded", "a2": "skipped", "a3": "skipped"}
    assert collect(engine, settings, bx=FakeBx(files), download=lambda url: blobs[url]) == []   # повторно не качает


class FakeBxTasks(FakeBx):
    """Две задачи: в одной файлы, другую вебхук не видит."""

    def call(self, method, params):
        if method == "task.commentitem.getlist" and params["TASKID"] == "999":
            raise RuntimeError("ACCESS_DENIED")
        return super().call(method, params)

    def get_task(self, tid):
        if tid == "999":
            raise RuntimeError("ACCESS_DENIED")
        return {"id": tid, "title": "ВАЖНЫЕ НОВОСТИ У2 2026 год"}


def test_watch_list_add_remove_and_errors(monkeypatch):
    from salesvisor.bitrix_pdo import add_task, remove_task, task_id_of, watch_list, watched

    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    monkeypatch.setenv("PDO_TASK_IDS", "321346")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://example.invalid/rest/1/x")
    settings = Settings()
    assert task_id_of("https://team.incab.ru/company/personal/user/2289/tasks/task/view/280039/") == "280039"
    assert task_id_of("321346") == "321346" and task_id_of("задача") is None
    assert watched(engine, settings) == ["321346"]                       # первый запуск — из PDO_TASK_IDS
    plan = xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "не принят", "дефицит сырья")]})
    files = [("a1", "Отчет по принятым заказам в декаду 20.10.2026 ver.1.xlsx", datetime.now() - timedelta(days=2), plan)]
    bx = FakeBxTasks(files)
    r = add_task(engine, settings, "https://team.incab.ru/company/personal/user/2289/tasks/task/view/999/", "Федосеев", bx=bx)
    assert r == {"task_id": "999", "title": None, "error": "RuntimeError: ACCESS_DENIED"}
    res = collect(engine, settings, bx=bx, download=lambda url: plan)
    assert [x["bitrix_task"] for x in res] == ["321346"]                 # закрытая задача не мешает остальным
    w = {t["task_id"]: t for t in watch_list(engine, settings)["tasks"]}
    assert (w["321346"]["loaded"], w["321346"]["last_error"]) == (1, None)
    assert w["999"]["last_error"] == "RuntimeError: ACCESS_DENIED" and w["999"]["checked_at"]
    assert remove_task(engine, "999") and watched(engine, settings) == ["321346"]
    assert remove_task(engine, "321346") and watched(engine, settings) == []   # пусто, но PDO_TASK_IDS не возвращается
    assert add_task(engine, settings, "321346", "Федосеев", bx=bx)["title"] == "ВАЖНЫЕ НОВОСТИ У2 2026 год"
    assert watched(engine, settings) == ["321346"]


def test_sources_catalog_and_upload_date():
    from fastapi.testclient import TestClient

    from salesvisor import sources
    from salesvisor.web import create_app

    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    client = TestClient(create_app(engine, Settings(database_url="sqlite:///:memory:")))
    plan = xlsx({"Sheet1": [PLAN_HDR, plan_row("1200000301", "10", "не принят", "дефицит сырья")]})
    stamp = int(datetime(2026, 9, 28, 9, 0).timestamp() * 1000)   # дата файла в прошлом (будущую сервер обрежет до «сейчас»)
    r = client.post("/api/upload", files={"file": ("Отчет по принятым заказам в декаду 20.10.2026 ver.1.xlsx", plan)},
                    data={"modified": str(stamp)})
    assert r.status_code == 200 and r.json()["source"] == "pdo_plan"
    with engine.connect() as c:                                          # событие — на дату файла, не «сегодня»
        assert {x.at.date() for x in c.execute(select(change_log).where(change_log.c.field == "pdo_plan"))} == {date(2026, 9, 28)}
    cat = {s["key"]: s for s in sources.catalog(engine, today=date(2026, 10, 12))}
    assert cat["segments"]["status"] == "ok" or cat["segments"]["last"]   # отрезки загружены в seed
    assert cat["pdo_plan"]["status"] == "пропуски" and "нет за декады" in cat["pdo_plan"]["detail"]
    assert cat["load010"]["status"] == "нет" and cat["load010"]["required"]
    assert client.get("/api/sources").status_code == 200 and client.get("/api/bitrix/watch").json()["webhook"] is False
