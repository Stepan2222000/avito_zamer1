"""Обёртки над `avito_library.reuse_utils.proxy_pool` с дополнительными методами.

Здесь мы используем общий пул прокси из библиотеки, но дополняем его
проектным логированием и вспомогательными операциями (`refresh_blocked`,
`mark_available`, фабричный метод `create` на основе `Settings`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from avito_library.reuse_utils import proxy_pool as base_pool

from .config import Settings
from .logging_utils import log_event as project_log_event


def _proxy_log_event(event: str, **payload: object) -> None:
    """Адаптер, приводящий формат логов к проектному."""
    item_id = payload.pop("item_id", None)
    proxy = payload.pop("proxy", None)
    extra = payload or None
    project_log_event(event, item_id=item_id, proxy=proxy, extra=extra)


# Перенаправляем логирование в адаптер.
base_pool.log_event = _proxy_log_event

ProxyEndpoint = base_pool.ProxyEndpoint


class ProxyPool(base_pool.ProxyPool):
    """Расширение общего пула с поддержкой настроек проекта."""

    def __init__(self, *, proxies_file: Path, blocked_file: Path) -> None:
        super().__init__(proxies_file=proxies_file, blocked_file=blocked_file)

    @classmethod
    async def create(cls, settings: Settings) -> "ProxyPool":
        """Создать пул на основе путей из настроек."""
        pool = cls(
            proxies_file=settings.proxies_file,
            blocked_file=settings.blocked_proxies_file,
        )
        await pool.reload()
        return pool

    async def reload(self) -> int:
        total = await super().reload()
        if total == 0:
            log_event(
                "proxy_pool_empty",
                extra={"proxies_file": str(self._proxies_file)},
            )
        return total

    async def refresh_blocked(self) -> None:
        """Актуализировать блокировки, перечитав файл."""
        blocked = await self._read_blocked()
        async with self._lock:
            self._blocked = blocked
            for proxy in self._proxies:
                proxy.is_blocked = proxy.address in blocked
            self._in_use -= blocked
            has_available = self._has_unblocked_locked()
        self._set_availability_event(has_available)

    async def mark_available(self, address: str) -> None:
        """Снять блокировку с прокси (например, после ручного восстановления)."""
        async with self._lock:
            self._blocked.discard(address)
            proxy = self._proxy_map.get(address)
            if proxy:
                proxy.is_blocked = False
            has_available = self._has_unblocked_locked()
        self._set_availability_event(has_available)

    async def all_proxies(self) -> Sequence[ProxyEndpoint]:
        """Вернуть копию списка всех прокси."""
        return await super().all_proxies()

    async def wait_for_unblocked(self) -> None:
        """Дождаться появления хотя бы одного доступного прокси."""
        await super().wait_for_unblocked()

    async def all_blocked(self) -> bool:
        """Проверить, что свободных прокси не осталось."""
        return await super().all_blocked()


__all__ = ["ProxyEndpoint", "ProxyPool"]
