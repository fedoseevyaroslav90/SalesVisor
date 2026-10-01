"""Окно мощности (этап 2 «Путь заказа»): разбор «Сводного» с позициями и короткой сводки ПДО, прогноз загрузки
к началу декады, маршруты позиций, группы продукции, деньги под риском, «кто занимает передел»."""
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import insert

from salesvisor import capacity, pdo
from salesvisor.brands import decode
from salesvisor.db import make_engine, wc_load
from salesvisor.ingest import load_file, load_segments, sniff_source
from salesvisor.queries import Filters, list_positions, order_card
from test_core import seg_row
from test_pdo import xlsx

TODAY = date(2026, 10, 1)
HDR3 = ["№ заявки", None, "Вид заказа", "ПО", "Контрагент", "Марка", None, "Продукт", "Валовая прибыль", "Дата готовности",
        "3Д. ШЛАНГОВЫЕ %", None, "WOEL60-5_1100_001", None, "1Д. МОДУЛЬН-БУФЕР %", None, "WOEL41-2_1100_001", None]


def dec_sheet(shl, mb, rows):
    """Лист декады: итог по шланговым и модульно-буферным (км, %), ниже позиции парами строк."""
    out = [["Период распределения"], ["Версия: 010"], HDR3,
           ["Строительная длина"] + [None] * 9 + ["Длина, км", "Загрузка, %"] * 4,
           [None] * 10 + ["Мощность 100", None, "Мощность 100", None, "Мощность 200", None, "Мощность 200", None],
           ["Итого"] + [None] * 9 + [shl[0], shl[1], shl[0], shl[1], mb[0], mb[1], mb[0], mb[1]]]
    for o, pos, vp, s, m in rows:
        cells = [o, pos.zfill(6), "1SO1", None, "ООО Альфа", "ДПД", None, "40000001", vp, None]
        for x in (s, s, m, m):
            cells += list(x) if x else [0, 0]
        out.append(cells)
        out.append(["27", None, None, None, "ООО Альфа", "ДПД", None, "40000001", vp, 8] + [None] * 8)
    return out


def svodny():
    return xlsx({
        "Свод": [[None, "2026"], [], [None, None, "01.10 - 10.10", "11.10 - 20.10"],
                 ["Общий объем по декаде, км", 0, 120, 60], ["Валовая прибыль, руб", 0, 3_000_000, 2_000_000],
                 ["3Д. ШЛАНГОВЫЕ %", 0, 95, 40]],
        "11.09 - 20.09": dec_sheet((10, 30), (5, 10), [("1200000599", "10", 1000, (1, 5), None)]),   # прошлая — без позиций
        "01.10 - 10.10": dec_sheet((90, 95), (60, 60), [("1200000501", "10", 500_000, (2, 0.5), (2, 0.2))]),
        "11.10 - 20.10": dec_sheet((30, 40), (40, 50), [("1200000501", "20", 700_000, (3, 0.7), (3, 0.3)),
                                                         ("1200000502", "10", 100_000, None, (4, 0.4))]),
    })


def seed(engine):
    segs = pd.DataFrame([seg_row("1200000501", "10", "1", "10 октября, 2026"), seg_row("1200000501", "20", "1", "20 октября, 2026"),
                         seg_row("1200000502", "10", "1", "20 октября, 2026")])
    segs["Материал"] = ["ДПД-HF-8У (1х8) 7кН 12.6", "ДПД-HF-8У (1х8) 7кН 12.6", "ОК ОКД-2Д-П-1У 1.4кН 2.4х5.6"]
    segs["МП (расчетно) план на отрезок"] = ["100 000", "200 000", "5 000"]
    load_segments(engine, segs, "seg")
    # история снимков: шланговые за неделю до декады заполнены на 30 %, к началу — на 110 % (+80 п.п.)
    hist = []
    for dec in (date(2026, 9, 10), date(2026, 8, 31), date(2026, 8, 20), date(2026, 8, 10), date(2026, 7, 31), date(2026, 7, 20)):
        s0 = pdo.dec_start(dec)
        hist += [{"ver": "010", "snap_date": s0 - timedelta(days=9), "decade_end": dec, "level": "группа", "name": "3Д. ШЛАНГОВЫЕ",
                  "grp": "3Д. ШЛАНГОВЫЕ", "km": None, "load": 30.0, "cap": None},
                 {"ver": "010", "snap_date": s0 - timedelta(days=2), "decade_end": dec, "level": "группа", "name": "3Д. ШЛАНГОВЫЕ",
                  "grp": "3Д. ШЛАНГОВЫЕ", "km": None, "load": 110.0, "cap": None}]
    with engine.begin() as conn:
        conn.execute(insert(wc_load), hist)


def test_brands_underwater_and_partner_prefixes():
    assert decode("ГОЗ7 ГП БСПз ОКПС-16У2% 8х1.6/1.5/1.1 19")["group"] == "подводный (полуфабрикат)"
    assert decode("МГФН ДПТ-П-64У (2х8)(4х16) 7кН")["brand"] == "ДПТ"
    assert decode("ОВ SM G.654.E Е2 125 ОВС")["group"] == "волокно"
    assert decode("Барабан БШ-1-3 ССД")["group"] == "прочее"
    assert decode("ДПД-HF-8У (1х8) 7кН 12.6")["group"] == "грунт — стеклопластик"


def test_summary_report_parse():
    rows = [["ЗАГРУЗКА РЦ"], [], ["Z0+Z4 на 21.09.2026"], [], [None, "21.09 - 30.09", "01.10 - 10.10"],
            ["Общий объем по декаде, км", 1120.8, 4667.7], ["из них с ПО Z4", 0, 1926.5], ["из них с ПО", 1120.8, 2741.2],
            ["Валовая прибыль, руб", 76e6, 155e6], ["3Д. ШЛАНГОВЫЕ %", 114.9, 128.4], ["2Д. БРОНЯ %"],
            ["CTRL", 100, 94], ["LBK1", 56, 24]]
    data = xlsx({"Лист1": rows})
    assert sniff_source(data, "21.09.xlsx") == "load"                 # без даты-подсказки в имени — по содержимому
    engine = make_engine("sqlite:///:memory:")
    r = load_file(engine, "load", data, "Загрузка РЦ 28.09.xlsx", published_at=datetime(2026, 9, 28, 9))
    assert (r["format"], r["snapshot"]) == ("summary", "2026-09-28")  # дата из имени, не из устаревшего заголовка
    with engine.connect() as conn:
        m = capacity.model(conn, TODAY)
    assert m["now"][("3Д. ШЛАНГОВЫЕ", date(2026, 10, 10))] == 128.4
    assert m["now"][("2Д. БРОНЯ", date(2026, 10, 10))] == 94            # у брони нет своей строки — по самой загруженной линии
    assert r["summary"] == 8


def test_forecast_routes_window_and_occupants():
    engine = make_engine("sqlite:///:memory:")
    seed(engine)
    r = load_file(engine, "load", svodny(), "Сводный версия 010  от 28.09.2026.XLS", published_at=datetime(2026, 9, 28))
    assert (r["format"], r["position_rows"]) == ("full", 10)              # 4 + 4 + 2; прошлая декада 20.09 — без позиций
    with engine.connect() as conn:
        m = capacity.model(conn, TODAY)
        assert m["inc"][("3Д. ШЛАНГОВЫЕ", 1)] == 80
        c10 = capacity.cell(m, "3Д. ШЛАНГОВЫЕ", date(2026, 10, 10))   # декада начинается через 3 дня — как в снимке
        c20 = capacity.cell(m, "3Д. ШЛАНГОВЫЕ", date(2026, 10, 20))   # за неделю: 40 + 80, но не выше обычных 110
        c31 = capacity.cell(m, "3Д. ШЛАНГОВЫЕ", date(2026, 10, 31))   # прироста на 3 недели нет — обычная загрузка
    assert (c10["value"], c10["kind"]) == (95, "сейчас")
    assert (c20["now"], c20["value"], c20["kind"]) == (40, 110, "прогноз")
    assert (c31["value"], c31["kind"]) == (110, "обычно")

    # маршрут и отметка перегруза: поз. 20 в декаде 20.10 идёт через шланговые, по прогнозу 110 %
    res = list_positions(engine, scope="all", filters=Filters.from_query(flags="overload"), today=TODAY)
    assert [(x["order_no"], x["pos"], x["route_bottleneck"], x["route_load"], x["route_kind"]) for x in res["rows"]] == [
        ("1200000501", "20", "3Д. ШЛАНГОВЫЕ", 110.0, "прогноз")]
    # через месяц декада 20.10 прошла — это уже не перегруз, а срок
    later = list_positions(engine, scope="all", filters=Filters.from_query(flags="overload"), today=date(2026, 11, 2))
    assert later["total"] == 0

    w = capacity.window(engine, today=TODAY)
    g = {x["group"]: x for x in w["groups"]}
    glass = g["грунт — стеклопластик"]
    assert [k["short"] for k in glass["key"]] == ["модульн-буфер", "шланговые"]   # при равной доле — по дивизиону
    assert glass["cells"]["2026-10-20"]["value"] == 110 and glass["free_decade"] is None
    assert (glass["positions"], glass["mp"], glass["risk"]) == (2, 300_000, 200_000)   # под риском — поз. 20 (перегруз)
    assert glass["parts"] == {"overload": 200_000}
    assert g["дроп"]["free_decade"] == "2026-10-20"                     # модульно-буферные 50 % — запас есть
    assert w["summary"]["2026-10-10"]["vp_rub"] == 3_000_000

    o = capacity.occupants(engine, "3Д. ШЛАНГОВЫЕ", date(2026, 10, 20))
    assert [(x["order_no"], x["pos"], x["load"], x["vp"], x["place"]) for x in o["rows"]] == [
        ("1200000501", "20", 0.7, 700_000, "OEL60-5")]
    card = order_card(engine, "1200000501", today=TODAY)
    caps = {p["pos"]: p["path"]["capacity"] for p in card["positions"]}
    assert [(c["decade"], c["short"], c["value"], c["kind"]) for c in caps["20"]] == [
        ("2026-10-20", "модульн-буфер", 50, "сейчас"), ("2026-10-20", "шланговые", 110, "прогноз")]
