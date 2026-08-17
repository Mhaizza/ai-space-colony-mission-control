# ruff: noqa: INP001
"""Slice 5A Checkpoint E: ApprovalReconciliationScheduler tests.

Unit-level start/stop/tick-isolation behavior of the scheduler class
itself, plus two structural checks proving it has zero coupling to
`PollingScheduler`/the GitHub adapter's gated startup:

1. Neither scheduler module imports the other.
2. `app/main.py`'s `lifespan()` instantiates/starts the approval scheduler
   *outside* the `if settings.github_adapter_enabled:` block that gates
   `PollingScheduler`.

No existing test in this repo exercises `app.main.lifespan()` end-to-end
(it requires a live-ish `init_db()`/GitHub-probe environment with no
established mocking convention here), so a full lifespan integration test
was not attempted -- the source-level placement check below is the
practical, honest substitute for proving "starts regardless of
github_adapter_enabled" without inventing new, unproven test
infrastructure under this checkpoint.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from app.mission.approval_reconciliation_scheduler import ApprovalReconciliationScheduler


class TestStartStop:
    def test_not_running_before_start(self) -> None:
        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_noop_tick)
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_running_after_start_and_stopped_after_stop(self) -> None:
        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_noop_tick)
        scheduler.start()
        try:
            assert scheduler.running is True
        finally:
            await scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_noop_tick)
        scheduler.start()
        first_task = scheduler._task  # noqa: SLF001 - white-box, this module's own test
        scheduler.start()
        try:
            assert scheduler._task is first_task  # noqa: SLF001
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_a_no_op(self) -> None:
        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_noop_tick)
        await scheduler.stop()  # must not raise
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_tick_is_invoked(self) -> None:
        calls = 0

        async def _tick() -> None:
            nonlocal calls
            calls += 1

        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_tick)
        scheduler.start()
        try:
            for _ in range(50):
                if calls >= 1:
                    break
                await asyncio.sleep(0.01)
        finally:
            await scheduler.stop()
        assert calls >= 1

    @pytest.mark.asyncio
    async def test_tick_exception_does_not_kill_the_loop(self) -> None:
        calls = 0

        async def _tick() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("simulated tick failure")

        scheduler = ApprovalReconciliationScheduler(interval_seconds=15, tick=_tick)
        scheduler.start()
        try:
            for _ in range(50):
                if calls >= 1:
                    break
                await asyncio.sleep(0.01)
            assert calls >= 1
            assert scheduler.running is True  # loop survived the raised exception
        finally:
            await scheduler.stop()

    def test_interval_out_of_bounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="between 15 and 300"):
            ApprovalReconciliationScheduler(interval_seconds=14, tick=_noop_tick)
        with pytest.raises(ValueError, match="between 15 and 300"):
            ApprovalReconciliationScheduler(interval_seconds=301, tick=_noop_tick)


async def _noop_tick() -> None:
    return None


class TestNoCouplingWithPollingScheduler:
    """Checks actual import statements (not prose) -- the module docstrings
    on both files legitimately *mention* the other by name to explain why
    they're deliberately independent; what must never exist is an import
    edge between them."""

    def _imported_module_names(self, module_path: Path) -> set[str]:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_neither_scheduler_module_imports_the_other(self) -> None:
        import app.mission.approval_reconciliation_scheduler as approval_scheduler_module
        import app.mission.polling as polling_module

        approval_path = Path(inspect.getfile(approval_scheduler_module))
        polling_path = Path(inspect.getfile(polling_module))

        approval_imports = self._imported_module_names(approval_path)
        polling_imports = self._imported_module_names(polling_path)

        assert "app.mission.polling" not in approval_imports
        assert "app.mission.approval_reconciliation_scheduler" not in polling_imports


class TestSchedulerStartsIndependentlyOfGithubAdapter:
    """Source-level structural check: the approval scheduler's
    instantiation/start call in app/main.py's lifespan() must not be nested
    inside the `if settings.github_adapter_enabled:` block that gates
    PollingScheduler/_start_github_adapter."""

    def test_approval_scheduler_wiring_is_outside_github_adapter_conditional(self) -> None:
        main_path = Path(inspect.getfile(__import__("app.main", fromlist=["lifespan"])))
        tree = ast.parse(main_path.read_text(encoding="utf-8"))

        lifespan_func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
        )

        def _contains_approval_scheduler_call(node: ast.AST) -> bool:
            return any(
                isinstance(n, ast.Name) and n.id == "ApprovalReconciliationScheduler"
                for n in ast.walk(node)
            )

        # The github_adapter_enabled conditional's body must NOT reference
        # ApprovalReconciliationScheduler at all.
        github_conditional = next(
            node
            for node in ast.walk(lifespan_func)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and node.test.attr == "github_adapter_enabled"
        )
        assert not _contains_approval_scheduler_call(github_conditional)

        # But lifespan() as a whole must reference it (i.e. it is wired up
        # somewhere -- just not inside that conditional).
        assert _contains_approval_scheduler_call(lifespan_func)
