"""Компактные утилиты логирования с фиксированным форматом сообщений.

Логи всегда выводятся одной строкой вида:
```
event=task_start item_id=123 proxy=1.2.3.4:8080 attempt=1
```
Формат и уровень управляются конфигурацией (см. `config.py`).
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from .config import Settings

LOGGER_NAME = "avito_async_runner"
_DEFAULT_LEVEL = logging.INFO


def configure_logging(settings: Settings) -> logging.Logger:
    """Настроить базовый логгер, используя уровень из конфигурации.

    Args:
        settings: Объект настроек приложения.

    Returns:
        Экземпляр логгера, который следует использовать по всему проекту.
    """
    global _DEFAULT_LEVEL

    level = getattr(logging, settings.log_level, logging.INFO)
    _DEFAULT_LEVEL = level

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format="%(message)s")
    else:
        root_logger.setLevel(level)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    return logger


def make_log_line(
    event: str,
    *,
    item_id: Optional[int | str] = None,
    proxy: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> str:
    """Собрать строку лога по правилам спецификации.

    Args:
        event: Название события (обязательное поле).
        item_id: Идентификатор объявления, если уместно.
        proxy: Прокси, участвующий в событии.
        extra: Дополнительные ключ-значение, добавляемые в конец строки.

    Returns:
        Готовая строка лога.
    """
    parts: list[str] = [f"event={event}"]
    if item_id is not None:
        parts.append(f"item_id={item_id}")
    if proxy:
        parts.append(f"proxy={proxy}")
    if extra:
        for key, value in extra.items():
            parts.append(f"{key}={_stringify(value)}")
    return " ".join(parts)


def log_event(
    event: str,
    *,
    item_id: Optional[int | str] = None,
    proxy: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
    level: Optional[int] = None,
) -> None:
    """Вывести событие в лог в соответствии с KISS-форматом.

    Args:
        event: Название события.
        item_id: Идентификатор объявления.
        proxy: Прокси адрес.
        extra: Дополнительные поля (порядок передачи сохраняется).
        level: Переопределение уровня логирования (по умолчанию глобальный).
    """
    logger = logging.getLogger(LOGGER_NAME)
    line = make_log_line(event, item_id=item_id, proxy=proxy, extra=extra)
    logger.log(level or _DEFAULT_LEVEL, line)


def _stringify(value: Any) -> str:
    """Привести значение к строке, избегая пробелов по краям."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = ["LOGGER_NAME", "configure_logging", "log_event", "make_log_line"]
