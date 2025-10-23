# Data Model — Асинхронный обработчик объявлений Avito

## Таблица `new.listings`
- **item_id** (BIGINT, PK) — ID объявления Avito (валидация: положительное число, совпадает с входным ID)
- **title** (TEXT, nullable)
- **description** (TEXT, nullable)
- **characteristics** (JSONB, nullable)
- **price** (NUMERIC(12,2), nullable)
- **seller_name** (TEXT, nullable)
- **seller_profile_url** (TEXT, nullable)
- **published_at** (TEXT, nullable)
- **location_address/location_metro/location_region** (TEXT, nullable)
- **views_total** (INTEGER, nullable, default NULL)
- **processed_at** (TIMESTAMPTZ, NOT NULL) — момент успешной обработки или классифицированного отсутствия
- **status** (TEXT, NOT NULL, enum: `success`, `unavailable`, `error`) — текущее состояние карточки
- **failure_reason** (TEXT, nullable) — заполняется при блокировках/ошибках
- **created_at / updated_at** (TIMESTAMPTZ, NOT NULL, default now()) — `updated_at` обновляется триггером

**Триггер**: `listings_touch_updated_at` — до обновления проставляет `updated_at = now()`.

## Очередь задач (в памяти)
- **ProcessingTask**
  - `item_id: int`
  - `url: str`
  - `attempt: int` (≥1)
  - `last_proxy: Optional[str]`
  - `enqueued_at: datetime`
  - `state: Literal["pending","in_progress","returned"]`

Правила:
- одна активная задача на `item_id`;
- при ре-кьюинге `attempt += 1`, `state` переходит в `returned`;
- если `attempt` превышает порог (по умолчанию 5) → фиксируется `status=error` и `failure_reason=attempt_limit`.

## Кольцевой пул прокси
- **ProxyEndpoint**
  - `address: str` (формат `host:port`)
  - `auth: Optional[str]`
  - `is_blocked: bool`
  - `last_used_at: datetime`
  - `failures: int`

- **BlockedProxyRecord** (строка файла `blocked_proxies.txt`)
  - `address` | `timestamp` | `reason`

Правила:
- при блокировке (403/407) `is_blocked = True`, запись добавляется в файл;
- при восстановлении оператор вручную убирает строку из файла, система перечитывает список при следующем цикле синхронизации (каждые N минут или по сигналу).

## Состояния воркера
- `idle` → `fetch_task` → `navigate` → `detect` → (`parse_success` | `mark_unavailable` | `solve_captcha` | `switch_proxy` | `retry`) → `complete`
- Переход в `switch_proxy` сопровождается закрытием страницы и выбором следующего прокси.
