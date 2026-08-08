"""The detectors.

Three of them, applied in order of precision. Each claims the records it
explains so the same queries are never reported twice:

1. ``lazy_load``          - keyed on ``lazy_loaded_from`` (constitution rule 7)
2. ``column_load``        - keyed on ``is_column_load`` alone
3. ``repeated_statement`` - the strategy-agnostic backstop over what is left

The third exists because the first two structurally cannot see a loop of
``session.get()`` calls, or parents fetched one at a time: those are not ORM
lazy loads at all, just a loop of ordinary queries.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import-time only: _recorder imports detect() at runtime, and routing this
    # edge through TYPE_CHECKING keeps that from being a cycle.
    from ._frames import AppFrame
    from ._recorder import QueryRecord

__all__ = ["Finding", "detect"]

DEFAULT_THRESHOLD = 2
"""Two identical round trips is already the N+1 shape - one per parent row. A
higher default would let a two-item collection slip through unreported."""


@dataclass(frozen=True)
class Finding:
    """One detected problem, aggregated over the queries that evidence it."""

    kind: str
    label: str
    count: int
    sql: str
    frame: AppFrame | None
    entity: str | None = None
    uselist: bool | None = None
    origin: str | None = None
    """Where this finding came from, when something upstream knows - the pytest
    plugin fills in the test node id. Detection never sets it."""


def _build(kind: str, label: str, group: list[QueryRecord]) -> Finding:
    first = group[0]
    return Finding(
        kind=kind,
        label=label,
        count=len(group),
        sql=first.sql,
        frame=next((r.frame for r in group if r.frame is not None), None),
        entity=first.entity,
        uselist=first.uselist,
    )


def _collect(
    records: list[QueryRecord],
    candidates: list[int],
    kind: str,
    key: Callable[[QueryRecord], str | None],
    threshold: int,
    claimed: set[int],
) -> list[Finding]:
    """Group ``candidates`` (indices into ``records``) and emit findings.

    Every index in an emitted group is added to ``claimed`` so a later, less
    precise detector does not report the same queries again.
    """
    grouped: dict[str, list[int]] = defaultdict(list)
    for index in candidates:
        group_key = key(records[index])
        if group_key is not None:
            grouped[group_key].append(index)

    findings: list[Finding] = []
    for label, indices in grouped.items():
        if len(indices) < threshold:
            continue
        findings.append(_build(kind, label, [records[i] for i in indices]))
        claimed.update(indices)
    return findings


def _lazy_key(record: QueryRecord) -> str | None:
    if not record.is_lazy_load:
        return None
    return record.path or record.entity or record.sql


def _column_key(record: QueryRecord) -> str | None:
    if not record.is_column_load:
        return None
    return record.entity or record.sql


def detect(records: list[QueryRecord], *, threshold: int = DEFAULT_THRESHOLD) -> list[Finding]:
    """Return every problem visible in one recording window, worst first."""
    # Dropped before grouping, so an ignored block cannot even contribute to the
    # count that pushes an unignored group over the threshold.
    records = [record for record in records if not record.ignored]
    claimed: set[int] = set()
    everything = list(range(len(records)))

    findings = _collect(records, everything, "lazy_load", _lazy_key, threshold, claimed)
    findings += _collect(records, everything, "column_load", _column_key, threshold, claimed)

    remaining = [i for i in everything if i not in claimed]
    findings += _collect(
        records, remaining, "repeated_statement", lambda r: r.sql, threshold, claimed
    )

    findings.sort(key=lambda f: (-f.count, f.label))
    return findings
