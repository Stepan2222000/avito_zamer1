"""Асинхронный воркер Playwright для обработки объявлений Avito.

Пока реализуем только инфраструктурные части из фазы US1:
- инициализация браузера/страницы под конкретным прокси;
- каркас класса `Worker`, удерживающий ссылки на очередь, пул БД и прокси;
- базовая навигация по карточке объявления с обязательным вызовом
  `detect_page_state` сразу после `page.goto`.

Обработка конкретных состояний, парсинг карточек и обработка ошибок будут
добавлены в следующих фазах (см. TODO).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import asyncpg
from avito_library import (
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    CardData,
    CardParsingError,
    DetectionError,
    detect_page_state as library_detect_page_state,
    parse_card,
    resolve_captcha_flow,
)
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    async_playwright,
)

from .config import Settings
from .db import ListingRecord, upsert_listing
from .logging_utils import log_event
from .proxy_pool import ProxyEndpoint, ProxyPool
from .queue import ProcessingTask, TaskQueue

DETECTION_ERROR_STATE = "detection_error"


@dataclass(slots=True)
class Worker:
    """Асинхронный воркер, обрабатывающий задачи из очереди одним браузером.

    Attributes:
        worker_id: Порядковый номер воркера (для логов и диагностики).
        settings: Глобальные настройки приложения.
        queue: Очередь задач `ProcessingTask`.
        db_pool: Пул подключений к PostgreSQL.
        proxy_pool: Кольцевой пул прокси.
    """

    worker_id: int
    settings: Settings
    queue: TaskQueue
    db_pool: asyncpg.Pool
    proxy_pool: ProxyPool

    _playwright: Optional[Playwright] = None
    _browser: Optional[Browser] = None
    _context: Optional[BrowserContext] = None
    _page: Optional[Page] = None
    _proxy: Optional[ProxyEndpoint] = None
    _base_profile_url: Optional[str] = None

    async def start(self) -> None:
        """Запустить воркер (инициализирует Playwright и страницу).

        TODO: добавить основной цикл обработки задач и интеграцию с очередью
        после реализации парсинга и обработки состояний.
        """
        log_event("worker_start", extra={"worker_id": self.worker_id})

        while True:
            await self._ensure_page()
            task = await self.queue.get()
            if task is None:
                log_event("worker_idle", extra={"worker_id": self.worker_id})
                break

            try:
                await self._process_task(task)
            except Exception as exc:
                log_event(
                    "worker_error",
                    item_id=task.item_id,
                    proxy=self._proxy.address if self._proxy else None,
                    extra={
                        "worker_id": self.worker_id,
                        "error": exc.__class__.__name__,
                    },
                    level=None,
                )
                await self._retry_with_limit(
                    task,
                    failure_reason="worker_error",
                    extra={"exception": exc.__class__.__name__},
                    rotate_proxy=True,
                )
            await asyncio.sleep(0)

    async def shutdown(self) -> None:
        """Корректно закрыть браузер и вернуть прокси в пул."""
        log_event("worker_shutdown", extra={"worker_id": self.worker_id})
        await self._cleanup_playwright()
        if self._proxy:
            await self.proxy_pool.release(self._proxy.address)
            self._proxy = None

    async def navigate(self, task: ProcessingTask) -> str:
        """Перейти на страницу объявления и вернуть результат `detect_page_state`.

        Args:
            task: Задача с URL объявления.

        Returns:
            Строковый код состояния страницы, возвращаемый `detect_page_state`.
        """
        if self._page is None:
            await self._ensure_page()

        if self._page is None:
            raise RuntimeError("Playwright page is not initialized")

        response: Response | None = await self._page.goto(
            task.url,
            wait_until="domcontentloaded",
            timeout=int(self.settings.goto_timeout * 1000),
        )
        priority: Sequence[str] = (
            PROXY_BLOCK_403_DETECTOR_ID,
            PROXY_AUTH_DETECTOR_ID,
            PROXY_BLOCK_429_DETECTOR_ID,
            CAPTCHA_DETECTOR_ID,
            REMOVED_DETECTOR_ID,
            SELLER_PROFILE_DETECTOR_ID,
            CATALOG_DETECTOR_ID,
            CARD_FOUND_DETECTOR_ID,
            CONTINUE_BUTTON_DETECTOR_ID,
        )
        try:
            state = await library_detect_page_state(
                self._page,
                priority=priority,
                last_response=response,
            )
        except DetectionError:
            return DETECTION_ERROR_STATE

        log_event(
            "worker_detect_state",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id, "state": state},
        )
        return state

    async def _process_task(self, task: ProcessingTask) -> None:
        """Обработать единственную задачу, учитывая результат детектора."""
        log_event(
            "task_start",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id, "attempt": task.attempt},
        )

        state = await self.navigate(task)
        await self._handle_state(task, state)

    async def _handle_card_found(self, task: ProcessingTask) -> None:
        """Сохранить карточку объявления и отметить задачу успешной."""
        if self._page is None:
            raise RuntimeError("Page is not initialized for card processing")
        html = await self._page.content()
        try:
            card = parse_card(
                html,
                fields=(
                    "title",
                    "description",
                    "characteristics",
                    "price",
                    "seller",
                    "item_id",
                    "published_at",
                    "location",
                    "views_total",
                ),
            )
        except CardParsingError as exc:
            log_event(
                "task_parse_error",
                item_id=task.item_id,
                proxy=self._proxy.address if self._proxy else None,
                extra={"worker_id": self.worker_id, "error": str(exc)},
            )
            await self._retry_with_limit(
                task,
                failure_reason="parse_card_error",
                extra={"error": str(exc)},
            )
            return

        if card.item_id and card.item_id != task.item_id:
            log_event(
                "task_item_mismatch",
                item_id=task.item_id,
                proxy=self._proxy.address if self._proxy else None,
                extra={
                    "worker_id": self.worker_id,
                    "card_item_id": card.item_id,
                },
            )

        record = self._build_listing_record(task.item_id, card, status="success", failure_reason=None)
        await upsert_listing(self.db_pool, self.settings.database_schema, record)
        task.last_result = None
        await self.queue.mark_done(task.item_id)
        log_event(
            "task_success",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id},
        )

    async def _handle_removed(self, task: ProcessingTask) -> None:
        """Зафиксировать отсутствие карточки и отметить задачу."""
        record = ListingRecord(
            item_id=task.item_id,
            status="unavailable",
            failure_reason=None,
        )
        await upsert_listing(self.db_pool, self.settings.database_schema, record)
        task.last_result = None
        await self.queue.mark_done(task.item_id)
        log_event(
            "task_missing",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id},
        )

    async def _handle_state(self, task: ProcessingTask, state: str) -> None:
        """Распределить обработку по результату детектора."""
        if state == CARD_FOUND_DETECTOR_ID:
            await self._handle_card_found(task)
            return
        if state == REMOVED_DETECTOR_ID:
            await self._handle_removed(task)
            return
        if state in {CAPTCHA_DETECTOR_ID, CONTINUE_BUTTON_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID}:
            await self._handle_captcha(task, state)
            return
        if state in {PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID}:
            await self._handle_proxy_block(task, state)
            return
        if state in {SELLER_PROFILE_DETECTOR_ID, CATALOG_DETECTOR_ID}:
            await self._handle_unexpected(task, state, event="unexpected_state")
            return
        if state == DETECTION_ERROR_STATE:
            await self._handle_unexpected(task, state, event="detection_error")
            return

        log_event(
            "state_unhandled",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id, "state": state},
        )
        # TODO: обработать остальные состояния (403/407, каталоги, seller_profile) в следующих задачах.
        await self._retry_with_limit(
            task,
            failure_reason="unhandled_state",
            extra={"state": state},
        )

    def _build_listing_record(
        self,
        item_id: int,
        card: CardData,
        *,
        status: str,
        failure_reason: Optional[str],
    ) -> ListingRecord:
        """Преобразовать CardData в ListingRecord для UPSERT."""
        location = card.location or {}
        seller = card.seller or {}
        price = self._normalize_price(card.price)

        return ListingRecord(
            item_id=item_id,
            status=status,
            failure_reason=failure_reason,
            title=card.title,
            description=card.description,
            characteristics=card.characteristics,
            price=price,
            seller_name=seller.get("name"),
            seller_profile_url=self._canonicalize_profile_url(seller.get("profile_url")),
            published_at=card.published_at,
            location_address=location.get("address"),
            location_metro=location.get("metro"),
            location_region=location.get("region"),
            views_total=self._to_int(card.views_total),
        )

    async def _retry_with_limit(
        self,
        task: ProcessingTask,
        *,
        failure_reason: str,
        extra: Optional[dict[str, object]] = None,
        rotate_proxy: bool = False,
    ) -> None:
        """Вернуть задачу обратно или зафиксировать превышение лимита попыток."""
        last_proxy = self._proxy.address if self._proxy else None
        allowed = await self.queue.retry(task.item_id, last_proxy=last_proxy)
        if allowed:
            context = {"worker_id": self.worker_id, "reason": failure_reason}
            if extra:
                context.update(extra)
            log_event(
                "task_retry",
                item_id=task.item_id,
                proxy=last_proxy,
                extra=context,
            )
            task.last_result = failure_reason
            if rotate_proxy:
                await self._rotate_proxy()
            return

        record = ListingRecord(
            item_id=task.item_id,
            status="error",
            failure_reason="attempt_limit",
        )
        await upsert_listing(self.db_pool, self.settings.database_schema, record)
        await self.queue.abandon(task.item_id)
        log_event(
            "task_failed",
            item_id=task.item_id,
            proxy=last_proxy,
            extra={"worker_id": self.worker_id, "reason": "attempt_limit"},
        )
        task.last_result = "attempt_limit"
        if rotate_proxy:
            await self._rotate_proxy()

    async def _handle_captcha(self, task: ProcessingTask, state: str) -> None:
        """Попытаться решить капчу или справиться с 429, затем продолжить обработку."""
        if self._page is None:
            raise RuntimeError("Page is not initialized for captcha handling")

        _, solved = await resolve_captcha_flow(self._page)
        if solved:
            # Повторно детектируем страницу и продолжаем обработку.
            new_state = await library_detect_page_state(self._page)
            log_event(
                "captcha_resolved",
                item_id=task.item_id,
                proxy=self._proxy.address if self._proxy else None,
                extra={"worker_id": self.worker_id, "state": new_state},
            )
            await self._handle_state(task, new_state)
            return

        log_event(
            "captcha_failed",
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id, "state": state},
        )
        await self._retry_with_limit(
            task,
            failure_reason="captcha_failed",
            extra={"state": state},
            rotate_proxy=True,
        )

    async def _handle_proxy_block(self, task: ProcessingTask, state: str) -> None:
        """Обработать блокировку прокси по кодам 403 или 407."""
        if self._proxy is None:
            await self._retry_with_limit(
                task,
                failure_reason="proxy_block_unknown",
                extra={"state": state},
                rotate_proxy=True,
            )
            return

        proxy_address = self._proxy.address
        reason = "http_403" if state == PROXY_BLOCK_403_DETECTOR_ID else "http_407"

        await self.proxy_pool.mark_blocked(proxy_address, reason=reason)
        log_event(
            "proxy_blocked",
            item_id=task.item_id,
            proxy=proxy_address,
            extra={"worker_id": self.worker_id, "reason": reason},
        )

        await self._retry_with_limit(
            task,
            failure_reason="proxy_blocked",
            extra={"state": state, "reason": reason},
            rotate_proxy=True,
        )

    async def _handle_unexpected(
        self,
        task: ProcessingTask,
        state: str,
        *,
        event: str,
    ) -> None:
        """Обработать неожиданные состояния (каталог, профиль, DetectionError)."""
        current_proxy = self._proxy.address if self._proxy else None
        rotate = task.last_result == event and task.last_proxy == current_proxy
        log_event(
            event,
            item_id=task.item_id,
            proxy=self._proxy.address if self._proxy else None,
            extra={"worker_id": self.worker_id, "state": state, "rotate": rotate},
        )
        await self._retry_with_limit(
            task,
            failure_reason=event,
            extra={"state": state},
            rotate_proxy=rotate,
        )

    async def _rotate_proxy(self) -> None:
        """Сменить прокси после неуспешной попытки решения капчи."""
        await self._cleanup_playwright()
        if self._proxy:
            await self.proxy_pool.release(self._proxy.address)
            self._proxy = None

    def _normalize_price(self, raw_price: Optional[Any]) -> Optional[Decimal]:
        """Преобразовать цену в Decimal(12,2)."""
        if raw_price is None:
            return None
        try:
            price = Decimal(str(raw_price))
            return price.quantize(Decimal("1.00"))
        except Exception:
            return None

    def _canonicalize_profile_url(self, url: Optional[str]) -> Optional[str]:
        """Собрать абсолютный URL профиля продавца."""
        if not url:
            return None
        parsed = urlsplit(url)
        if parsed.scheme and parsed.netloc:
            return url
        if self._base_profile_url is None:
            sample = self.settings.build_item_url(0)
            parts = urlsplit(sample)
            self._base_profile_url = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        return urljoin(self._base_profile_url, url)

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """Безопасно привести значение к int."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _ensure_page(self) -> None:
        """Создать браузер и страницу, если они ещё не инициализированы."""
        if self._page is not None:
            return

        while True:
            self._proxy = await self.proxy_pool.acquire()
            if self._proxy is None:
                all_blocked = await self.proxy_pool.all_blocked()
                if all_blocked:
                    log_event(
                        "worker_no_proxy",
                        extra={"worker_id": self.worker_id, "reason": "all_blocked"},
                    )
                    paused = await self.queue.pause(reason="no_proxy_available")
                    if not paused:
                        await self.queue.wait_until_resumed()
                    await self.proxy_pool.wait_for_unblocked()
                    await self.queue.resume(reason="proxy_available")
                    continue

                await asyncio.sleep(0.1)
                continue

            self._playwright = await async_playwright().start()
            display_value = f":{self.settings.playwright_display_base + self.worker_id - 1}"
            launch_env = os.environ.copy()
            launch_env["DISPLAY"] = display_value

            try:
                browser = await self._playwright.chromium.launch(
                    proxy=self._proxy.as_playwright_arguments(),
                    headless=self.settings.playwright_headless,
                    env=launch_env,
                )
                context = await browser.new_context()
                page = await context.new_page()
            except Exception:
                await self._cleanup_playwright()
                if self._proxy:
                    await self.proxy_pool.release(self._proxy.address)
                    self._proxy = None
                await asyncio.sleep(1)
                continue

            self._browser = browser
            self._context = context
            self._page = page

            await self.queue.resume(reason="proxy_available")
            log_event(
                "worker_page_ready",
                extra={
                    "worker_id": self.worker_id,
                    "proxy": self._proxy.address,
                    "display": display_value,
                },
            )
            break

    async def _cleanup_playwright(self) -> None:
        """Остановить Playwright-примитивы без выбрасывания исключений."""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
