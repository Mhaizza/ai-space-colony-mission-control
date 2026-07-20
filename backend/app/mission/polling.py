"""Background polling loop for the read-only GitHub adapter."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class PollingScheduler:
    """Outbound-only polling scheduler (no inbound webhooks)."""

    def __init__(
        self,
        *,
        interval_seconds: int,
        tick: Callable[[], Awaitable[None]],
    ) -> None:
        if interval_seconds < 15 or interval_seconds > 300:
            raise ValueError("poll interval must be between 15 and 300 seconds")
        self._interval = interval_seconds
        self._tick = tick
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="mission-github-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("mission.poller.started interval_seconds=%s", self._interval)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("mission.poller.tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
        logger.info("mission.poller.stopped")
