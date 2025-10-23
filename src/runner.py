"""Entry point for the asynchronous Avito listings processor."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, NoReturn, Optional, Tuple

import asyncpg

from .config import Settings, get_settings
from .db import close_pool, create_pool, init_schema
from .logging_utils import configure_logging, log_event
from .proxy_pool import ProxyPool
from .queue import TaskQueue
from .worker import Worker


@dataclass(slots=True)
class AppContext:
    """Держим ссылки на ключевые компоненты раннера."""

    settings: Settings
    queue: TaskQueue
    db_pool: asyncpg.Pool
    proxy_pool: ProxyPool


async def run() -> None:
    """Инициализировать базовые компоненты без запуска воркеров."""
    settings = get_settings()
    configure_logging(settings)

    task_queue = TaskQueue(max_attempts=settings.max_attempts)
    db_pool = await create_pool(settings)
    proxy_pool: Optional[ProxyPool] = None
    context: Optional[AppContext] = None

    try:
        await init_schema(db_pool, settings.database_schema)
        proxy_pool = await ProxyPool.create(settings)

        context = AppContext(
            settings=settings,
            queue=task_queue,
            db_pool=db_pool,
            proxy_pool=proxy_pool,
        )

        enqueue_stats = await _load_tasks(context)

        log_event(
            "runner_bootstrap",
            extra={
                "workers": context.settings.worker_count,
                "pending_tasks": await context.queue.pending_count(),
                "proxies_available": len(await context.proxy_pool.all_proxies()),
                "tasks_total": enqueue_stats["total"],
                "tasks_invalid": enqueue_stats["invalid"],
                "tasks_duplicates": enqueue_stats["duplicates"],
            },
        )

        await _run_workers(context)

    finally:
        remaining = await context.queue.pending_count() if context else 0
        proxies_remaining = (
            len(await proxy_pool.all_proxies()) if proxy_pool else 0
        )
        await close_pool(db_pool)
        log_event(
            "runner_finished",
            extra={
                "reason": "bootstrap_complete",
                "pending_tasks": remaining,
                "proxies_available": proxies_remaining,
            },
        )


async def _load_tasks(context: AppContext) -> Dict[str, int]:
    """Прочитать `items_file`, валидировать ID и добавить задачи в очередь."""
    path = context.settings.items_file
    if not path.exists():
        log_event("items_file_missing", extra={"path": str(path)})
        raise FileNotFoundError(f"items file not found: {path}")

    raw_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    seen: set[int] = set()
    items: list[Tuple[int, str]] = []
    invalid = 0
    duplicates = 0

    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            item_id = int(line)
        except ValueError:
            invalid += 1
            log_event(
                "items_invalid",
                extra={"line": line_number, "value": line},
            )
            continue

        if item_id <= 0:
            invalid += 1
            log_event(
                "items_invalid",
                item_id=item_id,
                extra={"line": line_number, "reason": "non_positive"},
            )
            continue

        if item_id in seen:
            duplicates += 1
            log_event("items_duplicate", item_id=item_id, extra={"line": line_number})
            continue

        seen.add(item_id)
        items.append((item_id, context.settings.build_item_url(item_id)))

    inserted = await context.queue.put_many(items)
    if inserted == 0:
        log_event("items_no_valid_entries", extra={"path": str(path)})
        raise ValueError("items.txt does not contain valid unique IDs")

    return {
        "total": inserted,
        "invalid": invalid,
        "duplicates": duplicates,
    }


async def _run_workers(context: AppContext) -> None:
    """Запустить воркеров и перезапускать упавшие экземпляры."""

    active_workers: Dict[int, Worker] = {
        worker_id: Worker(
            worker_id=worker_id,
            settings=context.settings,
            queue=context.queue,
            db_pool=context.db_pool,
            proxy_pool=context.proxy_pool,
        )
        for worker_id in range(1, context.settings.worker_count + 1)
    }

    tasks: Dict[asyncio.Task[None], int] = {
        asyncio.create_task(worker.start()): worker_id
        for worker_id, worker in active_workers.items()
    }

    try:
        while tasks:
            done, _pending = await asyncio.wait(
                tasks.keys(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for finished_task in done:
                worker_id = tasks.pop(finished_task)
                worker = active_workers[worker_id]
                try:
                    finished_task.result()
                except Exception as exc:
                    log_event(
                        "worker_crash",
                        extra={
                            "worker_id": worker_id,
                            "error": exc.__class__.__name__,
                        },
                    )
                    await worker.shutdown()
                    replacement = Worker(
                        worker_id=worker_id,
                        settings=context.settings,
                        queue=context.queue,
                        db_pool=context.db_pool,
                        proxy_pool=context.proxy_pool,
                    )
                    active_workers[worker_id] = replacement
                    tasks[asyncio.create_task(replacement.start())] = worker_id
                else:
                    await worker.shutdown()
                    active_workers.pop(worker_id, None)

    finally:
        for task, worker_id in list(tasks.items()):
            task.cancel()
            try:
                await task
            except Exception:
                pass
            await active_workers[worker_id].shutdown()


def main() -> NoReturn:
    """Синхронная точка входа, оборачивающая асинхронный запуск."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log_event("runner_finished", extra={"reason": "keyboard_interrupt"})


if __name__ == "__main__":
    main()
