"""Reference implementation of the TaskQueue pattern reused from zamer_avito.

This module mirrors the core logic for unique task scheduling with attempt
tracking and pause/resume semantics. Kept standalone so it can be adapted for
new projects (e.g., catalog parsing) without pulling the entire codebase.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, Iterable, Optional


def log_event(event: str, **_: object) -> None:
    """Placeholder logger used in the extracted snippet."""
    # Swap this with the real logging implementation in the target project.


class TaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETURNED = "returned"


@dataclass(slots=True)
class ProcessingTask:
    item_id: int
    url: str
    attempt: int = 1
    state: TaskState = TaskState.PENDING
    last_proxy: Optional[str] = None
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_result: Optional[str] = None

    def bump_attempt(self) -> None:
        self.attempt += 1
        self.touch()

    def set_state(self, state: TaskState) -> None:
        self.state = state
        self.touch()

    def set_last_proxy(self, proxy: Optional[str]) -> None:
        self.last_proxy = proxy
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


class TaskQueue:
    """Concurrency-safe FIFO queue with attempt tracking (round-robin friendly)."""

    def __init__(self, *, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._paused = False
        self._pending_order: Deque[int] = deque()
        self._tasks: Dict[int, ProcessingTask] = {}

    async def put_many(self, items: Iterable[tuple[int, str]]) -> int:
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
                        continue
                    task.set_state(TaskState.IN_PROGRESS)
                    return task
                return None

    async def mark_done(self, item_id: int) -> None:
        async with self._lock:
            self._tasks.pop(item_id, None)

    async def retry(self, item_id: int, *, last_proxy: Optional[str] = None) -> bool:
        async with self._lock:
            task = self._tasks.get(item_id)
            if task is None:
                return False
            task.set_last_proxy(last_proxy)
            task.bump_attempt()
            if task.attempt > self._max_attempts:
                self._tasks.pop(item_id, None)
                return False
            task.set_state(TaskState.RETURNED)
            self._pending_order.append(item_id)
            return True

    async def abandon(self, item_id: int) -> None:
        async with self._lock:
            self._tasks.pop(item_id, None)

    async def pending_count(self) -> int:
        async with self._lock:
            return sum(
                1
                for task in self._tasks.values()
                if task.state in (TaskState.PENDING, TaskState.RETURNED)
            )

    async def pause(self, *, reason: str) -> bool:
        async with self._lock:
            if self._paused:
                return False
            self._paused = True
            self._pause_event.clear()
        log_event("queue_paused", reason=reason)
        return True

    async def resume(self, *, reason: str) -> bool:
        async with self._lock:
            if not self._paused:
                return False
            self._paused = False
            self._pause_event.set()
        log_event("queue_resumed", reason=reason)
        return True

