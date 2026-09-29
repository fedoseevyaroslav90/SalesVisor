"""Настройки берутся только из переменных окружения. Секреты в код и репозиторий не попадают."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///data/salesvisor.db"))

    # Metabase: адрес, вопросы и доступ (API-ключ или служебная учётка)
    metabase_url: str = field(default_factory=lambda: _env("METABASE_URL").rstrip("/"))
    metabase_api_key: str = field(default_factory=lambda: _env("METABASE_API_KEY"))
    metabase_user: str = field(default_factory=lambda: _env("METABASE_USER"))
    metabase_password: str = field(default_factory=lambda: _env("METABASE_PASSWORD"))
    card_segments: int = field(default_factory=lambda: int(_env("METABASE_CARD_SEGMENTS", "522")))
    card_svetofor: int = field(default_factory=lambda: int(_env("METABASE_CARD_SVETOFOR", "573")))
    verify_ssl: bool = field(default_factory=lambda: _env("METABASE_VERIFY_SSL", "1") != "0")

    # Битрикс24: адрес входящего вебхука вида https://portal/rest/<user>/<key>/
    bitrix_webhook_url: str = field(default_factory=lambda: _env("BITRIX_WEBHOOK_URL").rstrip("/"))
    # Шаблон ссылки на задачу для интерфейса, например https://portal/company/personal/user/0/tasks/task/view/{id}/
    bitrix_task_url: str = field(default_factory=lambda: _env("BITRIX_TASK_URL"))
    # Шаблон ссылки на сделку, например https://portal/crm/deal/details/{id}/
    bitrix_deal_url: str = field(default_factory=lambda: _env("BITRIX_DEAL_URL"))
    # Писать ли комментарии в задачи при переносе декады
    bitrix_post_comments: bool = field(default_factory=lambda: _env("BITRIX_POST_COMMENTS", "0") == "1")

    # Папка, куда завод кладёт выгрузки (SFTP контура: /data/SAP/salesvisor). Работает, когда Metabase недоступен
    import_dir: str = field(default_factory=lambda: _env("IMPORT_DIR"))

    # Вход через портал «Инкаб ИИ»: портал проксирует запросы и ставит общий секрет и имя сотрудника.
    # Если секрет задан, запросы без него отклоняются
    portal_token: str = field(default_factory=lambda: _env("PORTAL_TOKEN"))

    @property
    def metabase_ready(self) -> bool:
        return bool(self.metabase_url and (self.metabase_api_key or (self.metabase_user and self.metabase_password)))


def get_settings() -> Settings:
    return Settings()
