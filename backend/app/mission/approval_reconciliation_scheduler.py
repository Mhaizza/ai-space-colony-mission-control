"""Independent lifecycle task for approval-domain expiration reconciliation
(Slice 5A Checkpoint E).

Deliberately does not use, extend, subclass, or import
`app.mission.polling.PollingScheduler` -- per the Checkpoint A architecture
proposal's explicit requirement that approval reconciliation run on its own
scheduled task, started independently of `_start_github_adapter`'s
`GITHUB_PAT`-gated startup. `PollingScheduler` is instantiated only inside
that conditional path; approval governance has no dependency on the GitHub
adapter being enabled, so tying reconciliation to it would silently stop
expiring/blocking/recreating approval requests in the explicitly-supported
adapter-disabled configuration.

The small amount of asyncio-interval-loop boilerplate this duplicates from
`PollingScheduler` is intentional: the two schedulers must remain
independently startable, stoppable, and reasoned-about, with zero coupling
between the GitHub adapter's lifecycle and the approval domain's.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class ApprovalReconciliationScheduler:
    """In-process interval ticker for approval expiration reconciliation."""

    def __init__(
        self,
        *,
        interval_seconds: int,
        tick: Callable[[], Awaitable[None]],
    ) -> None:
        if interval_seconds < 15 or interval_seconds > 300:
            raise ValueError("reconciliation interval must be between 15 and 300 seconds")
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
        self._task = asyncio.create_task(self._run(), name="mission-approval-reconciliation")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("mission.approval_reconciliation.started interval_seconds=%s", self._interval)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("mission.approval_reconciliation.tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
        logger.info("mission.approval_reconciliation.stopped")
