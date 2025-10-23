"""Асинхронная очередь задач для обработки объявлений Avito.

Очередь хранит уникальные задачи по `item_id`, обеспечивает эксклюзивную
выдачу задач воркерам, учитывает число попыток и предотвращает превышение
`MAX_ATTEMPTS`. В реализации сознательно избегаем дополнительных слоёв в
соответствии с принципом KISS.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, Iterable, Optional

from .logging_utils import log_event


class TaskState(str, Enum):
    """Допустимые состояния задачи в очереди."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETURNED = "returned"


@dataclass(slots=True)
class ProcessingTask:
    """Модель задачи на обработку объявления.

    Attributes:
        item_id: Идентификатор объявления Avito.
        url: Канонический URL карточки.
        attempt: Текущий номер попытки (начинается с 1).
        state: Текущее состояние задачи в очереди.
        last_proxy: Последний прокси, на котором задача выполнялась.
        enqueued_at: Время первичной постановки задачи.
        updated_at: Время последнего изменения состояния.
    """

    item_id: int
    url: str
    attempt: int = 1
    state: TaskState = TaskState.PENDING
    last_proxy: Optional[str] = None
    enqueued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    last_result: Optional[str] = None

    def bump_attempt(self) -> None:
        """Увеличить счётчик попыток и зафиксировать время обновления."""
        self.attempt += 1
        self.touch()

    def set_state(self, state: TaskState) -> None:
        """Обновить состояние и `updated_at`."""
        self.state = state
        self.touch()

    def set_last_proxy(self, proxy: Optional[str]) -> None:
        """Сохранить прокси, использованный в последней попытке."""
        self.last_proxy = proxy
        self.touch()

    def touch(self) -> None:
        """Обновить временную метку `updated_at`."""
        self.updated_at = datetime.now(timezone.utc)


class TaskQueue:
    """Памятная очередь задач с защитой от гонок."""

    def __init__(self, *, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._paused = False
        self._pause_reason: Optional[str] = None
        self._pending_order: Deque[int] = deque()
        self._tasks: Dict[int, ProcessingTask] = {}

    async def put_many(self, items: Iterable[tuple[int, str]]) -> int:
        """Поставить в очередь несколько задач подряд.

        Args:
            items: Итерация пар `(item_id, url)`.

        Returns:
            Количество добавленных задач (дубликаты не засчитываются).
        """
        inserted = 0
        async with self._lock:
            for item_id, url in items:
                if item_id in self._tasks:
                    continue
                task = ProcessingTask(item_id=item_id, url=url)
                self._tasks[item_id] = task
                self._pending_order.append(item_id)
                inserted += 1
        return inserted

    async def get(self) -> Optional[ProcessingTask]:
        """Получить следующую задачу для обработки.

        Returns:
            Экземпляр `ProcessingTask`, уже отмеченный как `in_progress`.
            Если очередь пуста, возвращает `None`.
        """
        while True:
            await self._pause_event.wait()
            async with self._lock:
                if self._paused:
                    continue
                while self._pending_order:
                    item_id = self._pending_order.popleft()
                    task = self._tasks.get(item_id)
                    if task is None:
                        continue
                    if task.state not in (TaskState.PENDING, TaskState.RETURNED):
                        # Задача уже выдаётся другому воркеру; пропускаем.
                        continue
                    task.set_state(TaskState.IN_PROGRESS)
                    return task
                return None

    async def mark_done(self, item_id: int) -> None:
        """Удалить задачу из очереди после успешной обработки."""
        async with self._lock:
            self._tasks.pop(item_id, None)

    async def retry(
        self,
        item_id: int,
        *,
        last_proxy: Optional[str] = None,
    ) -> bool:
        """Вернуть задачу в очередь с увеличением попытки.

        Args:
            item_id: Идентификатор объявления.
            last_proxy: Прокси, использованный перед возвратом.

        Returns:
            `True`, если задача возвращена в очередь; `False`, если достигнут
            предел попыток и задача удалена.
        """
        async with self._lock:
            task = self._tasks.get(item_id)
            if task is None:
                return False
            task.set_last_proxy(last_proxy)
            task.bump_attempt()
            if task.attempt > self._max_attempts:
                # Превышен предел — больше не планируем повтор.
                self._tasks.pop(item_id, None)
                return False
            task.set_state(TaskState.RETURNED)
            self._pending_order.append(item_id)
            return True

    async def abandon(self, item_id: int) -> None:
        """Удалить задачу без повторной постановки (например, при критической ошибке)."""
        async with self._lock:
            self._tasks.pop(item_id, None)

    async def __len__(self) -> int:
        """Получить общее количество задач в очереди (включая in_progress)."""
        async with self._lock:
            return len(self._tasks)

    async def pending_count(self) -> int:
        """Количество задач, ожидающих выдачи (`pending` или `returned`)."""
        async with self._lock:
            return sum(
                1
                for task in self._tasks.values()
                if task.state in (TaskState.PENDING, TaskState.RETURNED)
            )

    async def pause(self, *, reason: str) -> bool:
        """Приостановить выдачу задач."""
        async with self._lock:
            if self._paused:
                return False
            self._paused = True
            self._pause_reason = reason
            self._pause_event.clear()
            pending = sum(
                1
                for task in self._tasks.values()
                if task.state in (TaskState.PENDING, TaskState.RETURNED)
            )
        log_event("queue_paused", extra={"reason": reason, "pending": pending})
        return True

    async def resume(self, *, reason: str) -> bool:
        """Возобновить выдачу задач."""
        async with self._lock:
            if not self._paused:
                return False
            self._paused = False
            self._pause_reason = None
            self._pause_event.set()
        log_event("queue_resumed", extra={"reason": reason})
        return True

    async def wait_until_resumed(self) -> None:
        """Заблокироваться, пока очередь на паузе."""
        await self._pause_event.wait()


__all__ = [
    "ProcessingTask",
    "TaskQueue",
    "TaskState",
]
