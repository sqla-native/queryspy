"""The public API: four context managers and two exceptions.

All the assertions raise subclasses of ``AssertionError`` so pytest renders them
the same way it renders a failed ``assert``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ._detect import DEFAULT_THRESHOLD
from ._recorder import Recorder, _resolve_session, pop_ignore, push_ignore, start, stop
from ._report import render_findings, render_timing

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

__all__ = [
    "NPlusOneError",
    "QueryCountError",
    "QuerySpyError",
    "assert_max_queries",
    "assert_num_queries",
    "ignore",
    "no_n_plus_one",
    "record",
]


class QuerySpyError(AssertionError):
    """Base class for every queryspy assertion failure."""


class QueryCountError(QuerySpyError):
    """A query-count budget was exceeded or missed."""


class NPlusOneError(QuerySpyError):
    """An N+1 access pattern was detected."""


@contextmanager
def record(
    *,
    session: Session | AsyncSession | None = None,
    capture_stacks: bool = True,
) -> Iterator[Recorder]:
    """Record every statement issued inside the block.

    ``session`` narrows ORM-level records to one session; the raw query count
    stays process-wide for the duration of the block. ``capture_stacks=False``
    skips app-frame attribution, which is the main per-query cost.
    """
    recorder = Recorder(
        capture_stacks=capture_stacks,
        session_filter=_resolve_session(session),
    )
    start(recorder)
    try:
        yield recorder
    finally:
        stop(recorder)


@contextmanager
def ignore() -> Iterator[None]:
    """Suppress findings for statements issued inside the block.

    The escape hatch for code that is deliberately doing something queryspy
    would otherwise flag - a small admin loop, a migration script, a path where
    the round trips are known and accepted::

        with queryspy.ignore():
            for account in accounts:
                audit(account.owner)

    Queries inside the block are **still counted**. A query that ran, ran, and a
    budget that quietly under-reported would be worse than no budget. Only
    detection is suppressed.

    Blunt by design: there is no per-kind or per-relationship filtering. If you
    want to tolerate specific known findings across a codebase, that is what a
    baseline is for; if you want to tolerate a larger number of round trips, that
    is what ``threshold`` is for.

    Nests, and works whether or not a window is open.
    """
    previous = push_ignore()
    try:
        yield
    finally:
        pop_ignore(previous)


@contextmanager
def assert_num_queries(
    expected: int,
    *,
    session: Session | AsyncSession | None = None,
) -> Iterator[Recorder]:
    """Assert the block issues exactly ``expected`` statements.

    Counts every statement that reached the driver, flushes included - the same
    thing Django's ``assertNumQueries`` counts.
    """
    with record(session=session) as recorder:
        yield recorder
    if recorder.query_count != expected:
        raise QueryCountError(
            f"expected exactly {expected} quer{_y(expected)}, got {recorder.query_count}"
            + _detail(recorder)
        )


@contextmanager
def assert_max_queries(
    limit: int,
    *,
    session: Session | AsyncSession | None = None,
) -> Iterator[Recorder]:
    """Assert the block issues no more than ``limit`` statements."""
    with record(session=session) as recorder:
        yield recorder
    if recorder.query_count > limit:
        raise QueryCountError(
            f"expected at most {limit} quer{_y(limit)}, got {recorder.query_count}"
            + _detail(recorder)
        )


@contextmanager
def no_n_plus_one(
    *,
    threshold: int = DEFAULT_THRESHOLD,
    session: Session | AsyncSession | None = None,
) -> Iterator[Recorder]:
    """Assert the block triggers no N+1 access pattern."""
    with record(session=session) as recorder:
        yield recorder
    findings = recorder.findings(threshold=threshold)
    if findings:
        raise NPlusOneError("\n\n" + render_findings(findings))


def _y(count: int) -> str:
    return "y" if count == 1 else "ies"


def _detail(recorder: Recorder) -> str:
    """Everything useful we already know, appended to a count failure."""
    parts = [render_timing(recorder.db_duration_ms, recorder.slowest)]
    findings = recorder.findings()
    if findings:
        parts.append(render_findings(findings))
    return "\n\n" + "\n\n".join(parts)
