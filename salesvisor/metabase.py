"""Забор выгрузок из Metabase по API: вопрос 522 (отчёт по отрезкам) и 573 (светофор)."""
from __future__ import annotations

import httpx

from .config import Settings


class MetabaseError(RuntimeError):
    pass


class Metabase:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.metabase_url:
            raise MetabaseError("Не задан METABASE_URL")
        if not settings.metabase_api_key and not (settings.metabase_user and settings.metabase_password):
            raise MetabaseError("Не задан METABASE_API_KEY или METABASE_USER/METABASE_PASSWORD")
        self.s = settings
        self.http = client or httpx.Client(base_url=settings.metabase_url, verify=settings.verify_ssl, timeout=600)
        self._session: str | None = None

    def _headers(self) -> dict:
        if self.s.metabase_api_key:
            return {"x-api-key": self.s.metabase_api_key}
        if not self._session:
            r = self.http.post("/api/session", json={"username": self.s.metabase_user, "password": self.s.metabase_password})
            if r.status_code != 200:
                raise MetabaseError(f"Metabase не пустил служебную учётку: HTTP {r.status_code}")
            self._session = r.json()["id"]
        return {"X-Metabase-Session": self._session}

    def card_csv(self, card_id: int) -> bytes:
        """Полный результат вопроса в CSV, без ограничения в 2000 строк, как у выгрузки из интерфейса."""
        r = self.http.post(f"/api/card/{card_id}/query/csv", headers=self._headers(), data={"format_rows": "false"})
        if r.status_code not in (200, 202):
            raise MetabaseError(f"Вопрос {card_id}: HTTP {r.status_code} {r.text[:200]}")
        return r.content
