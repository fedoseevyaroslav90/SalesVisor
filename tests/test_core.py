from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from salesvisor import parsing as p
from salesvisor.config import Settings
from salesvisor.db import make_engine
from salesvisor.ingest import load_segments, load_svetofor, parse_segments
from salesvisor.queries import list_orders, order_card
from salesvisor.web import create_app


def test_parsing():
    assert p.parse_date("22 июля, 2026") == date(2026, 7, 22)
    assert p.parse_date("8 июня, 2026, 16:44") == date(2026, 6, 8)
    assert p.parse_date("31.12.2025") == date(2025, 12, 31)
    assert p.parse_date(None) is None
    assert p.parse_number("1 814,22") == 1814.22
    assert p.parse_flag("@08@") and not p.parse_flag("@EB@")
    assert p.parse_decade("2Д07", near=date(2026, 7, 22)) == ("2Д07", date(2026, 7, 20))
    assert p.parse_decade("3Д02", near=date(2026, 7, 1)) == ("3Д02", date(2026, 2, 28))
    # Декада декабря рядом с январской датой относится к прошлому году
    assert p.parse_decade("2Д12", near=date(2026, 1, 15)) == ("2Д12", date(2025, 12, 20))
    assert p.parse_bitrix_task("Задача № 313360 Губанов") == "313360"
    assert p.parse_bitrix_task("Громова") is None


def seg_row(order, pos, seg, req, produced="@EB@", shipped="@EB@", invoiced="@EB@", task=None, manager="IVANOVA"):
    return {
        "Заказ клиента": order, "Позиция заказа клиента": pos, "Номер отрезка по порядку в позиции": seg,
        "Треб. дата поставки": req, "Создал": manager, "Имя заказчика": "ООО Альфа", "Описание отдела сбыта": "Отдел РФ",
        "Статус Произведен": produced, "Статус На складе": "@EB@", "Статус Готов к отгрузке": "@EB@",
        "Статус Отгружен": shipped, "Статус Отфактурирован": invoiced, "Статус В пути": "@EB@",
        "Длина отдельного отрезка": "2", "ЕИ": "КМ", "Номер задачи в Битрикс": task,
    }


def svet_row(order, pos, first, cur, shift, color):
    return {"Заказ": order, "Позиция": pos, "ПланДатаОтгрузки": "20 октября, 2026", "Дата перевода Z4": "1 сентября, 2026",
            "Первая декада": first, "Текущая декада": cur, "Смещено дней": shift, "Цвет": color,
            "Заказчик": "ООО Альфа", "Отдел продаж": "Отдел РФ", "ГП": "Кабель ОК"}


def test_position_keys_with_thousand_separator():
    segs, pos = parse_segments(pd.DataFrame([seg_row("1200000001", "1 010", "1", "10 октября, 2026")]))
    assert pos[0]["pos"] == "1010"


def test_load_and_track_changes():
    engine = make_engine("sqlite:///:memory:")
    load_segments(engine, pd.DataFrame([
        seg_row("1200000001", "10", "1", "30 сентября, 2026", task="Задача № 313360"),
        seg_row("1200000001", "10", "2", "30 сентября, 2026"),
        seg_row("1200000002", "10", "1", "10 марта, 2026", produced="@08@", shipped="@08@", invoiced="@08@"),
    ]), "day1")
    load_svetofor(engine, pd.DataFrame([svet_row("1200000001", "10", "3Д09", "3Д09", "0", "green")]), "day1")

    # Через день: срок сдвинули, один отрезок произведён, светофор пожелтел
    r = load_segments(engine, pd.DataFrame([
        seg_row("1200000001", "10", "1", "10 октября, 2026", produced="@08@", task="Задача № 313360"),
        seg_row("1200000001", "10", "2", "10 октября, 2026"),
        seg_row("1200000002", "10", "1", "10 марта, 2026", produced="@08@", shipped="@08@", invoiced="@08@"),
    ]), "day2")
    assert r["changes"] == 2  # треб. дата и этап
    load_svetofor(engine, pd.DataFrame([svet_row("1200000001", "10", "3Д09", "2Д10", "20", "yellow")]), "day2")

    card = order_card(engine, "1200000001", today=date(2026, 10, 1))
    p0 = card["positions"][0]
    assert p0["first_decade"] == "3Д09" and p0["current_decade"] == "2Д10" and p0["color"] == "yellow"
    assert p0["stage"] == "В производстве 1/2" and p0["bitrix_task"] == "313360" and p0["manager"] == "IVANOVA"
    assert {c["field"] for c in card["changes"]} == {"required_date", "stage", "current_decade", "color"}

    orders = list_orders(engine, today=date(2026, 10, 1))
    assert [o["order_no"] for o in orders] == ["1200000001"]  # отгруженный заказ закрыт
    assert orders[0]["color"] == "yellow" and not orders[0]["overdue"]
    assert list_orders(engine, today=date(2026, 10, 25))[0]["overdue"] == 1


def test_api_comments_and_upload():
    engine = make_engine("sqlite:///:memory:")
    client = TestClient(create_app(engine, Settings(database_url="sqlite:///:memory:")))
    csv = pd.DataFrame([svet_row("1200000009", "10", "1Д10", "1Д10", "0", "green")]).to_csv(index=False).encode()
    r = client.post("/api/upload", files={"file": ("svetofor.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["source"] == "svetofor"
    assert client.post("/api/orders/1200000009/comments", json={"text": "Звонил клиенту", "author": "Я"}).status_code == 200
    card = client.get("/api/orders/1200000009").json()
    assert card["comments"][0]["text"] == "Звонил клиенту"
    assert client.post("/api/upload", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 400
    assert client.post("/api/sync").status_code == 400  # Metabase не настроен
