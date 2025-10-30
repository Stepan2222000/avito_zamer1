"""Обёртки над `avito_library.reuse_utils.task_queue` с локальным логированием.

Используем общую реализацию очереди задач, но добавляем совместимость с
прежним API (`item_id`, `url`) и поддерживаем метод `wait_until_resumed`,
которого нет в базовой версии.
"""
from __future__ import annotations

from typing import Any, Hashable, Iterable, Optional, Tuple

from avito_library.reuse_utils import task_queue as base_queue

from .logging_utils import log_event as project_log_event


def _queue_log_event(event: str, **payload: object) -> None:
    """Адаптация логгера библиотеки к формату проекта."""
    item_id = payload.pop("item_id", None)
    proxy = payload.pop("proxy", None)
    extra = payload or None
    project_log_event(event, item_id=item_id, proxy=proxy, extra=extra)


# Перенаправляем логирование очереди на адаптер.
base_queue.log_event = _queue_log_event

TaskState = base_queue.TaskState


class ProcessingTask(base_queue.ProcessingTask):
    """Совместимая обёртка, предоставляющая свойства `item_id` и `url`."""

    def __init__(
        self,
        task_key: Hashable,
        payload: Any,
        attempt: int = 1,
        state: TaskState = TaskState.PENDING,
        last_proxy: Optional[str] = None,
        enqueued_at=None,
        updated_at=None,
        last_result: Optional[str] = None,
    ) -> None:
        kwargs = dict(
            task_key=task_key,
            payload=payload,
            attempt=attempt,
            state=state,
            last_proxy=last_proxy,
            last_result=last_result,
        )
        if enqueued_at is not None:
            kwargs["enqueued_at"] = enqueued_at
        if updated_at is not None:
            kwargs["updated_at"] = updated_at
        super().__init__(**kwargs)

    # Свойства оставлены для обратной совместимости со старым кодом.
    @property
    def item_id(self) -> int:
        return int(self.task_key)

    @property
    def url(self) -> str:
        return str(self.payload)


# Убеждаемся, что базовая очередь будет создавать именно эту обёртку.
base_queue.ProcessingTask = ProcessingTask


class TaskQueue(base_queue.TaskQueue):
    """Расширение базовой очереди с методом ожидания возобновления."""

    async def put_many(self, items: Iterable[Tuple[int, str]]) -> int:
        return await super().put_many(items)

    async def get(self) -> Optional[ProcessingTask]:
        task = await super().get()
        if task is None:
            return None
        return task  # тип уже `ProcessingTask` благодаря переопределению

    async def retry(
        self,
        task_key: int,
        *,
        last_proxy: Optional[str] = None,
    ) -> bool:
        return await super().retry(task_key, last_proxy=last_proxy)

    async def wait_until_resumed(self) -> None:
        """Заблокироваться, пока очередь находится на паузе."""
        await self._pause_event.wait()  # атрибут объявлен в базовом классе


__all__ = ["ProcessingTask", "TaskQueue", "TaskState"]
