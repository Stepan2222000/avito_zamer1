"""Application configuration loaded from a `.env` file via pydantic-settings.

В проекте придерживаемся принципа KISS: все параметры объявляем в одном месте
и читаем только из `.env`, чтобы запуск всегда выглядел как
``python -m src.runner`` без дополнительных флагов.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Union

from pydantic import Field, validator
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Файлы с данными находятся в соседней директории data/ на уровень выше
DATA_ROOT = (PROJECT_ROOT.parent / "data").resolve()
# Основной конфигурационный файл с переменными окружения
ENV_PATH = PROJECT_ROOT / ".env"


def _default(path_name: str) -> Path:
    """Return default paths under the shared data directory."""
    # Все путевые параметры собираем через эту функцию, чтобы не дублировать
    # вычисление абсолютных путей и избегать опечаток.
    return (DATA_ROOT / path_name).resolve()


class Settings(BaseSettings):
    """Strongly typed configuration container loaded from `.env`.

    Pydantic автоматически подставит значения из переменных окружения (файл
    `.env`). При отсутствии конкретного параметра используем разумный дефолт.
    """

    items_file: Path = Field(default_factory=lambda: _default("items.txt"))
    proxies_file: Path = Field(default_factory=lambda: _default("proxies.txt"))
    blocked_proxies_file: Path = Field(
        default_factory=lambda: _default("blocked_proxies.txt")
    )

    # DSN для подключения к PostgreSQL. Значение по умолчанию совпадает
    # с параметрами из спецификации, но может быть переопределено в `.env`.
    database_dsn: str = "postgresql://admin:root@81.30.105.134:5402/avito_zamer"
    database_schema: str = "new"
    db_pool_size: int = 5

    max_attempts: int = 5
    goto_timeout: float = 15.0
    worker_count: int = 4
    playwright_headless: bool = True
    playwright_display_base: int = 10

    log_level: str = "INFO"

    base_item_url: str = "https://www.avito.ru/item/{item_id}"

    class Config:
        env_file = ENV_PATH
        env_file_encoding = "utf-8"
        case_sensitive = False

    @validator("items_file", "proxies_file", "blocked_proxies_file", pre=True)
    def _resolve_path(cls, value: Union[str, Path]) -> Path:
        """Привести путь к абсолютному виду без создания директорий."""
        path = Path(value).expanduser() if not isinstance(value, Path) else value
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @validator("db_pool_size", "max_attempts", "worker_count", pre=True)
    def _ensure_positive(cls, value: Union[int, str]) -> int:
        """Конвертировать значение в int и проверить, что оно положительное."""
        ivalue = int(value)
        if ivalue < 1:
            raise ValueError("configuration values must be positive")
        return ivalue

    @validator("playwright_display_base", pre=True)
    def _ensure_non_negative(cls, value: Union[int, str]) -> int:
        """Конвертировать значение в int и проверить, что оно неотрицательное."""
        ivalue = int(value)
        if ivalue < 0:
            raise ValueError("playwright_display_base must be >= 0")
        return ivalue

    @validator("goto_timeout", pre=True)
    def _ensure_positive_float(cls, value: Union[str, float]) -> float:
        """Убедиться, что таймаут положителен."""
        fvalue = float(value)
        if fvalue <= 0:
            raise ValueError("timeouts must be positive")
        return fvalue

    @validator("log_level", pre=True)
    def _normalize_log_level(cls, value: Union[str, int]) -> str:
        """Привести уровень логирования к верхнему регистру и проверить допустимость."""
        if isinstance(value, int):
            # Допускаем передачу числового уровня (logging.INFO и т.д.)
            if value not in (10, 20, 30, 40, 50):
                raise ValueError("unsupported numeric log level")
            return logging.getLevelName(value)
        level = str(value).upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("log_level must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG")
        return level

    def build_item_url(self, item_id: Union[int, str]) -> str:
        """Построить каноническую ссылку на объявление по шаблону."""
        return self.base_item_url.format(item_id=item_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Получить (и закешировать) объект настроек.

    Cache гарантирует, что мы не перечитываем `.env` в каждом модуле и
    соблюдаем KISS по минимизации обращений к диску.
    """
    return Settings()


SETTINGS = get_settings()
