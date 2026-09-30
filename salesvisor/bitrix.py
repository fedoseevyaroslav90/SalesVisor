"""Битрикс24 через входящий вебхук: задачи, комментарии о переносах, уведомления."""
from __future__ import annotations

from datetime import date

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

from .config import Settings
from .db import bitrix_links, change_log, positions


class BitrixError(RuntimeError):
    pass


class Bitrix:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.bitrix_webhook_url:
            raise BitrixError("Не задан BITRIX_WEBHOOK_URL")
        self.http = client or httpx.Client(base_url=settings.bitrix_webhook_url + "/", timeout=60)

    def close(self) -> None:
        self.http.close()

    def call(self, method: str, params: dict) -> dict:
        try:
            r = self.http.post(f"{method}.json", json=params)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except (httpx.HTTPError, ValueError) as e:
            raise BitrixError(f"{method}: нет связи с Битрикс24 ({e.__class__.__name__})") from e
        if r.status_code != 200 or "error" in data:
            raise BitrixError(f"{method}: {data.get('error_description') or data.get('error') or r.status_code}")
        return data.get("result")

    def batch(self, commands: dict[str, str]) -> dict:
        """До 50 команд за один запрос: у Битрикс24 лимит около 2 запросов в секунду."""
        result = {}
        items = list(commands.items())
        for i in range(0, len(items), 50):
            res = self.call("batch", {"halt": 0, "cmd": dict(items[i:i + 50])})
            result.update(res.get("result") or {})
        return result

    def get_task(self, task_id: str) -> dict | None:
        res = self.call("tasks.task.get", {"taskId": task_id, "select": ["ID", "TITLE", "RESPONSIBLE_ID", "DEADLINE", "STATUS"]})
        return (res or {}).get("task")

    def get_deal(self, deal_id: str) -> dict | None:
        return self.call("crm.deal.get", {"id": deal_id})

    def add_task_comment(self, task_id: str, text: str) -> None:
        self.call("task.commentitem.add", {"TASKID": task_id, "FIELDS": {"POST_MESSAGE": text}})

    def notify(self, user_id: int, text: str) -> None:
        self.call("im.notify.system.add", {"USER_ID": user_id, "MESSAGE": text})


def _fmt_decade(v):
    return v or "—"


def post_decade_changes(engine: Engine, bx: Bitrix) -> int:
    """Пишет в задачу Битрикс24 комментарий о каждом переносе текущей декады, который ещё не отправлен."""
    # Ручная привязка из карточки заказа важнее номера задачи из отчёта SAP
    task = func.coalesce(bitrix_links.c.task_id, positions.c.bitrix_task).label("bitrix_task")
    stmt = (select(change_log.c.id, change_log.c.order_no, change_log.c.pos, change_log.c.old, change_log.c.new,
                   task, positions.c.first_decade, positions.c.product)
            .join(positions, (positions.c.order_no == change_log.c.order_no) & (positions.c.pos == change_log.c.pos))
            .outerjoin(bitrix_links, bitrix_links.c.order_no == change_log.c.order_no)
            .where(change_log.c.field == "current_decade", change_log.c.bitrix_sent.is_(False), task.is_not(None)))
    sent = 0
    with engine.begin() as conn:
        rows = list(conn.execute(stmt))
        by_task: dict[str, list] = {}
        for r in rows:
            by_task.setdefault(r.bitrix_task, []).append(r)
        for task, items in by_task.items():
            lines = [f"Заказ {items[0].order_no}: перенос сроков по данным SAP на {date.today():%d.%m.%Y}"]
            for r in items:
                lines.append(f"• поз. {r.pos} {r.product or ''}: {_fmt_decade(r.old)} → {_fmt_decade(r.new)} "
                             f"(первая требуемая: {_fmt_decade(r.first_decade)})")
            try:
                bx.add_task_comment(task, "\n".join(lines))
            except BitrixError:
                continue
            conn.execute(update(change_log).where(change_log.c.id.in_([r.id for r in items])).values(bitrix_sent=True))
            sent += len(items)
    return sent


TASK_STATUS = {"1": "Новая", "2": "Ждёт выполнения", "3": "Выполняется", "4": "Ждёт контроля",
               "5": "Завершена", "6": "Отложена", "7": "Отклонена"}


def live_info(bx: Bitrix, task_id: str | None, deal_id: str | None) -> dict:
    """Сведения из Битрикс24 для карточки заказа: задача и сделка."""
    out: dict = {}
    if task_id:
        try:
            t = bx.get_task(task_id) or {}
            resp = t.get("responsible") or {}
            out["task"] = {"id": task_id, "title": t.get("title"), "deadline": t.get("deadline"),
                           "status": TASK_STATUS.get(str(t.get("status")), t.get("status")),
                           "responsible": resp.get("name") or t.get("responsibleId")}
        except BitrixError as e:
            out["task_error"] = str(e)
    if deal_id:
        try:
            d = bx.get_deal(deal_id) or {}
            # сумму не отдаём: интерфейс её не показывает, а номер сделки в карточке может указать любой сотрудник
            out["deal"] = {"id": deal_id, "title": d.get("TITLE"), "stage": d.get("STAGE_ID"),
                           "assigned": d.get("ASSIGNED_BY_ID")}
        except BitrixError as e:
            out["deal_error"] = str(e)
    return out
