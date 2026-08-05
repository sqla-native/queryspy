"""Statement recording.

Two layers, deliberately kept apart (constitution rule 6):

* ``do_orm_execute`` gives ORM-level records with lazy-load attribution. This is
  where N+1 lives.
* ``before_cursor_execute`` gives the ground-truth statement count, including
  flushes that never reach the ORM execute hook at all.

They are never correlated: a single ORM execute can produce several cursor
executions (``selectinload`` batching), and stitching the two together is
exactly the fragility that killed ``nplusone``.

Listeners are registered on the ``Session`` and ``Engine`` *classes*, not on
instances. ``AsyncSession`` wraps a sync ``Session``, so class-level listeners
cover async for free, and it sidesteps the question of whether context variables
propagate across SQLAlchemy's greenlet bridge.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, event
from sqlalchemy.orm import ORMExecuteState, Session

from ._detect import DEFAULT_THRESHOLD, Finding, detect
from ._frames import AppFrame, capture_app_frame

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["QueryRecord", "Recorder"]


@dataclass(frozen=True)
class QueryRecord:
    """One ORM-level statement execution."""

    sql: str
    """The statement rendered as a template - bind parameters stay as named
    placeholders, so the same query shape with different values compares equal."""

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


@dataclass
class Recorder:
    """Collects statements for one recording window."""

    capture_stacks: bool = True
    session_filter: Session | None = None
    orm_records: list[QueryRecord] = field(default_factory=list)
    cursor_count: int = 0

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
        sql=" ".join(str(state.statement).split()),
        is_lazy_load=state.lazy_loaded_from is not None,
        is_column_load=bool(state.is_column_load),
        entity=_describe_entity(state),
        path=path,
        uselist=uselist,
        frame=frame,
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
    for recorder in _active.get():
        recorder.cursor_count += 1


def _install() -> None:
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Engine, "before_cursor_execute", _on_cursor_execute)


def _uninstall() -> None:
    event.remove(Session, "do_orm_execute", _on_orm_execute)
    event.remove(Engine, "before_cursor_execute", _on_cursor_execute)


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
    return event.contains(Session, "do_orm_execute", _on_orm_execute) or event.contains(
        Engine, "before_cursor_execute", _on_cursor_execute
    )
