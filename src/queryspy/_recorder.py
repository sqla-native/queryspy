"""Statement recording.

Two layers, deliberately kept apart (constitution rule 6):

* ``do_orm_execute`` gives ORM-level records with lazy-load attribution. This is
  where N+1 lives.
* ``before_cursor_execute`` / ``after_cursor_execute`` give the ground-truth
  statement count and the time actually spent in the driver, including flushes
  that never reach the ORM execute hook at all.

They are never correlated: a single ORM execute can produce several cursor
executions (``selectinload`` batching), and stitching the two together is
exactly the fragility that killed ``nplusone``. That is why timing is reported
per *window* and never per *finding* - the correlation needed to attribute
milliseconds to a finding is the thing we refuse to do.

Listeners are registered on the ``Session`` and ``Engine`` *classes*, not on
instances. ``AsyncSession`` wraps a sync ``Session``, so class-level listeners
cover async for free.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, event
from sqlalchemy.orm import ORMExecuteState, Session

from ._detect import DEFAULT_THRESHOLD, Finding, detect
from ._frames import AppFrame, capture_app_frame

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["QueryRecord", "Recorder", "SlowStatement"]

_START_KEY = "_queryspy_started"


@dataclass(frozen=True)
class SlowStatement:
    """The slowest single statement in a window."""

    sql: str
    duration_ms: float


@dataclass(frozen=True)
class QueryRecord:
    """One ORM-level statement execution."""

    statement: Any = field(repr=False)
    """The SQLAlchemy statement object, kept rather than rendered.

    Rendering costs a full compile, measured at roughly half of all recording
    overhead, and most records never need it: the lazy-load and column-load
    detectors key on the relationship path and the mapper. Only unclaimed
    records and the handful that become findings pay for it. See ``sql``.
    """

    is_lazy_load: bool
    """True only when ``ORMExecuteState.lazy_loaded_from`` was set.

    Constitution rule 7: this - never ``is_relationship_load`` - is the lazy-load
    discriminator. ``selectinload`` and ``subqueryload`` both report
    ``is_relationship_load=True`` while leaving ``lazy_loaded_from`` unset.
    """

    is_column_load: bool
    """A per-instance column round trip: a deferred column, or an attribute
    refreshed after expiry. ``lazy_loaded_from`` is never set for these."""

    entity: str | None
    """The mapped class this statement loads, when known."""

    path: str | None
    """The relationship path for a lazy load, e.g. ``User.addresses``."""

    uselist: bool | None
    """Whether the lazily-loaded relationship is a collection. Drives whether the
    suggested fix is ``selectinload`` or ``joinedload``."""

    frame: AppFrame | None

    ignored: bool = False
    """Executed inside a ``queryspy.ignore()`` block.

    Still counted - a query that ran, ran - but excluded from detection."""

    @cached_property
    def sql(self) -> str:
        """The statement as a template, compiled on first use and then cached.

        Bind parameters stay as named placeholders, so the same query shape with
        different values compares equal - and no parameter *values* are ever
        captured.
        """
        return " ".join(str(self.statement).split())


@dataclass
class Recorder:
    """Collects statements for one recording window."""

    capture_stacks: bool = True
    session_filter: Session | None = None
    orm_records: list[QueryRecord] = field(default_factory=list)
    cursor_count: int = 0
    db_duration_ms: float = 0.0
    slowest: SlowStatement | None = None

    @property
    def query_count(self) -> int:
        """Total statements that reached the database driver.

        This is the ground truth - it includes flush INSERTs and any Core-level
        statement, neither of which reaches the ORM execute hook.
        """
        return self.cursor_count

    def findings(self, *, threshold: int = DEFAULT_THRESHOLD) -> list[Finding]:
        """Every problem visible in what has been recorded so far, worst first."""
        return detect(self.orm_records, threshold=threshold)

    def _observe(self, statement: str, elapsed_ms: float) -> None:
        self.db_duration_ms += elapsed_ms
        if self.slowest is None or elapsed_ms > self.slowest.duration_ms:
            self.slowest = SlowStatement(sql=" ".join(statement.split()), duration_ms=elapsed_ms)

    def _accepts(self, session: Session) -> bool:
        return self.session_filter is None or self.session_filter is session


def _resolve_session(session: Session | AsyncSession | None) -> Session | None:
    """An ``AsyncSession`` wraps a sync ``Session``; events fire on the latter."""
    if session is None:
        return None
    sync_session = getattr(session, "sync_session", None)
    if sync_session is not None:
        return sync_session  # type: ignore[no-any-return]
    return session  # type: ignore[return-value]


_active: ContextVar[tuple[Recorder, ...]] = ContextVar("queryspy_active", default=())
"""Recorders collecting in the current context.

A context variable rather than a module global, because a module global is
correct for tests (one window at a time) and *wrong* for a concurrent server:
interleaved requests would each record every other request's queries.

`asyncio` copies the context when a task is created, so a window opened before
`asyncio.gather` still sees the queries its tasks issue, while a window opened
inside one request stays invisible to every other request. Measured: context
variables do propagate across SQLAlchemy's greenlet bridge, so this holds for
async lazy loads too, which run in a spawned greenlet.
"""

_ignore_depth: ContextVar[int] = ContextVar("queryspy_ignore_depth", default=0)
"""How many nested ``ignore()`` blocks are open in this context.

A depth rather than a flag so nesting composes, and a context variable for the
same reason the active set is one: it has to follow the work, including across
SQLAlchemy's greenlet bridge.
"""


def push_ignore() -> int:
    token = _ignore_depth.get()
    _ignore_depth.set(token + 1)
    return token


def pop_ignore(previous: int) -> None:
    _ignore_depth.set(previous)


# Listener *registration* is genuinely global, so it is refcounted separately
# from the per-context recorder set. The lock covers threaded use, where two
# threads may open their first window at the same moment.
_registration_lock = threading.Lock()
_registration_depth = 0


def _describe_entity(state: ORMExecuteState) -> str | None:
    mapper = state.bind_mapper
    if mapper is None:
        return None
    return str(mapper.class_.__name__)


def _describe_path(state: ORMExecuteState) -> tuple[str | None, bool | None]:
    """Render ``loader_strategy_path`` as ``User.addresses`` plus its cardinality."""
    path = state.loader_strategy_path
    if path is None:
        return None, None
    elements = getattr(path, "path", ())
    parts: list[str] = []
    uselist: bool | None = None
    for element in elements:
        mapped_class = getattr(element, "class_", None)
        if mapped_class is not None:
            parts.append(mapped_class.__name__)
            continue
        # Anything that is not a mapper is a mapped property; fall back to its
        # repr rather than branching on a key that real paths always carry.
        parts.append(str(getattr(element, "key", element)))
        uselist = getattr(element, "uselist", uselist)
    return (".".join(parts) if parts else None), uselist


def _on_orm_execute(state: ORMExecuteState) -> None:
    active = _active.get()
    if not active or not state.is_select:
        return
    listeners = [rec for rec in active if rec._accepts(state.session)]
    if not listeners:
        return

    path, uselist = _describe_path(state)
    frame = capture_app_frame() if any(rec.capture_stacks for rec in listeners) else None
    record = QueryRecord(
        statement=state.statement,
        is_lazy_load=state.lazy_loaded_from is not None,
        is_column_load=bool(state.is_column_load),
        entity=_describe_entity(state),
        path=path,
        uselist=uselist,
        frame=frame,
        ignored=_ignore_depth.get() > 0,
    )
    for recorder in listeners:
        recorder.orm_records.append(record)


def _on_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    active = _active.get()
    if not active:
        return
    for recorder in active:
        recorder.cursor_count += 1
    # Stack the start time on the connection: one connection runs one statement
    # at a time, so this nests correctly without any correlation between layers.
    conn.info.setdefault(_START_KEY, []).append(time.perf_counter())


def _on_cursor_execute_done(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    started = conn.info.get(_START_KEY)
    if not started:
        # The window opened after this statement began. Nothing to attribute.
        return
    elapsed_ms = (time.perf_counter() - started.pop()) * 1000
    for recorder in _active.get():
        recorder._observe(statement, elapsed_ms)


def _install() -> None:
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Engine, "before_cursor_execute", _on_cursor_execute)
    event.listen(Engine, "after_cursor_execute", _on_cursor_execute_done)


def _uninstall() -> None:
    event.remove(Session, "do_orm_execute", _on_orm_execute)
    event.remove(Engine, "before_cursor_execute", _on_cursor_execute)
    event.remove(Engine, "after_cursor_execute", _on_cursor_execute_done)


def start(recorder: Recorder) -> None:
    """Begin a recording window, installing listeners if this is the first."""
    global _registration_depth
    with _registration_lock:
        if _registration_depth == 0:
            _install()
        _registration_depth += 1
    _active.set((*_active.get(), recorder))


def stop(recorder: Recorder) -> None:
    """End a recording window, removing listeners once the last one closes.

    Constitution rule 4: listeners must be fully removable. The test suite
    asserts ``event.contains`` is False after teardown.
    """
    global _registration_depth
    _active.set(tuple(other for other in _active.get() if other is not recorder))
    with _registration_lock:
        _registration_depth -= 1
        if _registration_depth == 0:
            _uninstall()


def listeners_installed() -> bool:
    """Whether queryspy currently has listeners registered. Used by the tests."""
    return (
        event.contains(Session, "do_orm_execute", _on_orm_execute)
        or event.contains(Engine, "before_cursor_execute", _on_cursor_execute)
        or event.contains(Engine, "after_cursor_execute", _on_cursor_execute_done)
    )
