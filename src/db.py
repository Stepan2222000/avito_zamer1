"""Работа с PostgreSQL: создание пула и инициализация схемы `new.listings`.

Все DDL берём из спецификации, без лишних абстракций (KISS). Модуль
предоставляет три функции:

- `create_pool` — создаёт `asyncpg.Pool` с параметрами из конфигурации.
- `init_schema` — гарантирует наличие схемы, таблицы и триггера.
- `close_pool` — аккуратно закрывает пул при завершении раннера.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Final, Optional, Sequence
import json

import asyncpg

from .config import Settings

LISTINGS_TABLE_NAME: Final[str] = "listings"
UPDATED_AT_FUNCTION: Final[str] = "touch_listings_updated_at"
UPDATED_AT_TRIGGER: Final[str] = "listings_touch_updated_at"

SCHEMA_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LISTING_ALLOWED_STATUSES: Final[set[str]] = {"success", "unavailable", "error"}
LISTING_COLUMNS: Final[tuple[str, ...]] = (
    "item_id",
    "title",
    "description",
    "characteristics",
    "price",
    "seller_name",
    "seller_profile_url",
    "published_at",
    "location_address",
    "location_metro",
    "location_region",
    "views_total",
    "processed_at",
    "status",
    "failure_reason",
)


@dataclass(slots=True)
class ListingRecord:
    """Данные карточки объявления для UPSERT в `new.listings`.

    Attributes:
        item_id: Идентификатор объявления Avito (PRIMARY KEY).
        status: Текущий статус карточки (`success`, `unavailable`, `error`).
        processed_at: Метка времени обработки.
        failure_reason: Причина неуспеха (если есть).
        Остальные поля соответствуют столбцам таблицы `new.listings`.
    """

    item_id: int
    status: str
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    failure_reason: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    characteristics: Optional[Any] = None
    price: Optional[Decimal] = None
    seller_name: Optional[str] = None
    seller_profile_url: Optional[str] = None
    published_at: Optional[str] = None
    location_address: Optional[str] = None
    location_metro: Optional[str] = None
    location_region: Optional[str] = None
    views_total: Optional[int] = None

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ValueError("item_id must be a positive integer")
        if self.status not in LISTING_ALLOWED_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(sorted(LISTING_ALLOWED_STATUSES))}",
            )
        if not isinstance(self.processed_at, datetime):
            raise TypeError("processed_at must be a datetime instance")
        if self.processed_at.tzinfo is None:
            # Сохраняем TZ-aware время, приводя к UTC по умолчанию.
            self.processed_at = self.processed_at.replace(tzinfo=timezone.utc)
        if self.views_total is not None and self.views_total < 0:
            raise ValueError("views_total cannot be negative")
        if isinstance(self.characteristics, (dict, list)):
            self.characteristics = json.dumps(self.characteristics, ensure_ascii=False)

    def as_sequence(self) -> Sequence[Any]:
        """Вернуть значения колонок в порядке `LISTING_COLUMNS`."""
        return (
            self.item_id,
            self.title,
            self.description,
            self.characteristics,
            self.price,
            self.seller_name,
            self.seller_profile_url,
            self.published_at,
            self.location_address,
            self.location_metro,
            self.location_region,
            self.views_total,
            self.processed_at,
            self.status,
            self.failure_reason,
        )


def _validate_schema_name(schema: str) -> str:
    """Проверить имя схемы и вернуть его.

    Args:
        schema: Имя схемы из конфигурации.

    Returns:
        Строку с именем схемы, если оно прошло проверку.

    Raises:
        ValueError: Если имя схемы пустое либо содержит недопустимые символы.
    """
    if not schema:
        raise ValueError("database schema name must be non-empty")
    if not SCHEMA_NAME_PATTERN.match(schema):
        raise ValueError(
            "database schema name must match ^[A-Za-z_][A-Za-z0-9_]*$ to avoid SQL injection",
        )
    return schema


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Создать пул соединений `asyncpg` согласно параметрам конфигурации.

    Args:
        settings: Экземпляр настроек приложения.

    Returns:
        Инициализированный `asyncpg.Pool`, готовый к использованию воркерами.

    Raises:
        asyncpg.PostgresError: Если не удалось подключиться к БД.
    """
    return await asyncpg.create_pool(
        dsn=settings.database_dsn,
        min_size=1,
        max_size=settings.db_pool_size,
    )


async def init_schema(pool: asyncpg.Pool, schema: str) -> None:
    """Создать схему, таблицу и триггер, если они отсутствуют.

    Args:
        pool: Пул соединений `asyncpg`.
        schema: Имя схемы (например, `new`).

    Raises:
        asyncpg.PostgresError: При ошибках выполнения DDL.
        ValueError: Если имя схемы не прошло валидацию.
    """
    schema = _validate_schema_name(schema)
    table_identifier = f"{schema}.{LISTINGS_TABLE_NAME}"

    create_schema_sql = f"CREATE SCHEMA IF NOT EXISTS {schema};"

    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_identifier} (
        item_id BIGINT PRIMARY KEY,
        title TEXT NULL,
        description TEXT NULL,
        characteristics JSONB NULL,
        price NUMERIC(12, 2) NULL,
        seller_name TEXT NULL,
        seller_profile_url TEXT NULL,
        published_at TEXT NULL,
        location_address TEXT NULL,
        location_metro TEXT NULL,
        location_region TEXT NULL,
        views_total INTEGER NULL,
        processed_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('success', 'unavailable', 'error')),
        failure_reason TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """

    create_function_sql = f"""
    CREATE OR REPLACE FUNCTION {schema}.{UPDATED_AT_FUNCTION}() RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """

    drop_trigger_sql = f"""
    DROP TRIGGER IF EXISTS {UPDATED_AT_TRIGGER}
    ON {table_identifier};
    """

    create_trigger_sql = f"""
    CREATE TRIGGER {UPDATED_AT_TRIGGER}
    BEFORE UPDATE ON {table_identifier}
    FOR EACH ROW EXECUTE FUNCTION {schema}.{UPDATED_AT_FUNCTION}();
    """

    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(create_schema_sql)
            await connection.execute(create_table_sql)
            await connection.execute(create_function_sql)
            await connection.execute(drop_trigger_sql)
            await connection.execute(create_trigger_sql)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Закрыть пул соединений, ожидая завершения активных операций."""
    await pool.close()


async def upsert_listing(
    pool: asyncpg.Pool,
    schema: str,
    record: ListingRecord,
) -> None:
    """Выполнить UPSERT записи объявления.

    Args:
        pool: Пул соединений asyncpg.
        schema: Имя схемы (например, `new`).
        record: Данные карточки в виде `ListingRecord`.
    """
    schema = _validate_schema_name(schema)
    table_identifier = f"{schema}.{LISTINGS_TABLE_NAME}"

    placeholders = ", ".join(f"${idx}" for idx in range(1, len(LISTING_COLUMNS) + 1))
    columns = ", ".join(LISTING_COLUMNS)
    update_columns = ", ".join(f"{col}=EXCLUDED.{col}" for col in LISTING_COLUMNS[1:])

    sql = f"""
    INSERT INTO {table_identifier} ({columns})
    VALUES ({placeholders})
    ON CONFLICT (item_id) DO UPDATE SET
        {update_columns};
    """

    async with pool.acquire() as connection:
        await connection.execute(sql, *record.as_sequence())


__all__ = [
    "LISTINGS_TABLE_NAME",
    "LISTING_ALLOWED_STATUSES",
    "ListingRecord",
    "create_pool",
    "init_schema",
    "upsert_listing",
    "close_pool",
]
