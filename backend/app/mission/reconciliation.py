"""Partition-scoped reconciliation for read-only projection tombstoning.

A *completeness partition* is the pair ``(source_type, partition_key)``. During a
sync the adapter records every source id it observes for a partition and whether
the partition's reads (all pages + all required child reads) completed
successfully. Only fully-completed partitions may be reconciled: records that
belong to that exact partition but were not observed are tombstoned. Partial,
malformed, interrupted, rate-limited, or failed partition reads never tombstone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class _TombstoneableRow(Protocol):
    """Minimal row shape the reconciler needs to decide tombstoning."""

    source_id: str
    tombstoned: bool


@dataclass(slots=True)
class PartitionState:
    """Observed ids and completeness for one ``(source_type, partition_key)``."""

    source_type: str
    partition_key: str
    complete: bool = True
    observed_ids: set[str] = field(default_factory=set)


class PartitionReconciler:
    """Tracks observed ids/completeness per partition and selects tombstones.

    Reconciliation is isolated per ``(source_type, partition_key)``: marking one
    partition partial never affects another, and two partitions that share a
    ``partition_key`` but differ in ``source_type`` are reconciled independently.
    """

    def __init__(self) -> None:
        self._partitions: dict[tuple[str, str], PartitionState] = {}

    def touch(self, source_type: str, partition_key: str) -> PartitionState:
        """Register a partition read attempt (so empty partitions reconcile)."""
        key = (source_type, partition_key)
        state = self._partitions.get(key)
        if state is None:
            state = PartitionState(source_type=source_type, partition_key=partition_key)
            self._partitions[key] = state
        return state

    def observe(self, source_type: str, partition_key: str, source_id: str) -> None:
        """Record that ``source_id`` was observed in this partition."""
        self.touch(source_type, partition_key).observed_ids.add(source_id)

    def mark_partial(self, source_type: str, partition_key: str) -> None:
        """Mark a partition read as incomplete; it will not be reconciled."""
        self.touch(source_type, partition_key).complete = False

    def reconcilable_partitions(self) -> list[PartitionState]:
        """Return only partitions whose reads completed fully and successfully."""
        return [state for state in self._partitions.values() if state.complete]


def select_tombstones[RowT: _TombstoneableRow](
    partition: PartitionState,
    rows: list[RowT],
) -> list[RowT]:
    """Return live rows in a completed partition that were not observed.

    Idempotent: already-tombstoned rows are skipped, and observed rows are never
    selected, so revived records (re-observed after a prior tombstone) stay live.
    """
    return [
        row for row in rows if not row.tombstoned and row.source_id not in partition.observed_ids
    ]
