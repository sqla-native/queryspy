"""Recorder lifecycle: listener installation, nesting, concurrency, filtering."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from queryspy import record
from queryspy._recorder import listeners_installed

from .conftest import User


def test_listeners_are_installed_only_while_recording(session: Session) -> None:
    """Constitution rule 4: listeners must be fully removable."""
    assert not listeners_installed()
    with record():
        assert listeners_installed()
    assert not listeners_installed()


def test_listeners_are_removed_even_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError), record():
        raise RuntimeError("boom")
    assert not listeners_installed()


def test_nested_windows_both_record(session: Session) -> None:
    with record() as outer:
        session.scalars(select(User)).all()
        with record() as inner:
            session.scalars(select(User)).all()
        assert listeners_installed()

    assert inner.query_count == 1
    assert outer.query_count == 2
    assert not listeners_installed()


def test_capture_stacks_off_leaves_no_frame(session: Session) -> None:
    with record(capture_stacks=False) as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    assert spy.findings()[0].frame is None


def test_session_filter_narrows_orm_records(engine: object) -> None:
    with (
        Session(engine) as watched,  # type: ignore[arg-type]
        Session(engine) as ignored,
        record(session=watched) as spy,
    ):
        watched.scalars(select(User)).all()
        ignored.scalars(select(User)).all()

    assert len(spy.orm_records) == 1
    # The raw count stays process-wide for the window, as documented.
    assert spy.query_count == 2


@pytest.mark.asyncio
async def test_async_session_filter_resolves_the_sync_session(async_engine: object) -> None:
    async with AsyncSession(async_engine) as watched:  # type: ignore[arg-type]
        with record(session=watched) as spy:
            await watched.scalars(select(User))

    assert len(spy.orm_records) == 1


@pytest.mark.asyncio
async def test_concurrent_tasks_land_in_one_window(async_session: AsyncSession) -> None:
    async def fetch() -> None:
        await async_session.scalars(select(User))

    with record() as spy:
        await asyncio.gather(fetch(), fetch(), fetch())

    assert spy.query_count == 3
    assert spy.findings()[0].kind == "repeated_statement"


def test_recorder_findings_default_threshold(session: Session) -> None:
    with record() as spy:
        session.scalars(select(User)).all()
    assert spy.findings() == []


def test_non_select_orm_statements_are_ignored(session: Session) -> None:
    """Bulk UPDATE/DELETE reach do_orm_execute but are not loads."""
    from sqlalchemy import update

    with record() as spy:
        session.execute(update(User).values(name="renamed"))
        session.rollback()

    assert spy.orm_records == []
    assert spy.query_count >= 1


def test_statements_without_a_mapped_entity_are_recorded_without_one(session: Session) -> None:
    from sqlalchemy import literal_column

    with record() as spy:
        session.execute(select(literal_column("1")))

    assert len(spy.orm_records) == 1
    assert spy.orm_records[0].entity is None


@pytest.mark.asyncio
async def test_concurrent_windows_do_not_see_each_others_queries(async_engine: object) -> None:
    """The isolation a concurrent server needs.

    A module-global recorder list would give each of these every other request's
    queries. Context variables scope a window to the task that opened it - and
    they propagate across SQLAlchemy's greenlet bridge, so async lazy loads land
    in the right window too.
    """

    async def request(n: int) -> tuple[int, list[str]]:
        async with AsyncSession(async_engine) as session:  # type: ignore[arg-type]
            with record() as spy:
                users = (await session.scalars(select(User).limit(n))).all()
                for user in users:
                    await user.awaitable_attrs.addresses
            return spy.query_count, [f.kind for f in spy.findings()]

    light, heavy = await asyncio.gather(request(1), request(3))

    assert light == (2, [])  # one parent query, one lazy load: no N+1
    assert heavy == (4, ["lazy_load"])  # one parent query, three lazy loads


@pytest.mark.asyncio
async def test_a_window_still_covers_tasks_it_spawns(async_session: AsyncSession) -> None:
    """Context is copied at task creation, so gather() inside a window is covered."""

    async def fetch() -> None:
        await async_session.scalars(select(User))

    with record() as spy:
        await asyncio.gather(fetch(), fetch())

    assert spy.query_count == 2


def test_listeners_are_refcounted_across_threads() -> None:
    """Registration is global even though the recorder set is per-context."""
    import threading as _threading

    barrier = _threading.Barrier(2)
    seen: list[bool] = []

    def worker() -> None:
        with record():
            barrier.wait()
            seen.append(listeners_installed())

    threads = [_threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == [True, True]
    assert not listeners_installed()


def test_queries_outside_any_window_are_ignored(session: Session) -> None:
    """Listeners stay installed globally; recording is per-context.

    A fresh `contextvars.Context` has no active recorders, so a query issued
    inside one while another context holds a window open must land nowhere -
    neither counted nor timed. This is the isolation an ASGI server depends on,
    exercised without the thread-affinity constraints of a SQLite connection.
    """
    import contextvars

    def outside() -> None:
        session.scalars(select(User)).all()

    with record() as spy:
        assert listeners_installed()
        contextvars.Context().run(outside)

    assert spy.query_count == 0
    assert spy.db_duration_ms == 0.0
    assert spy.slowest is None


def test_timing_is_recorded(session: Session) -> None:
    with record() as spy:
        session.scalars(select(User)).all()

    assert spy.db_duration_ms > 0
    assert spy.slowest is not None
    assert spy.slowest.sql.startswith("SELECT")
    assert spy.slowest.duration_ms > 0


def test_slowest_tracks_the_largest(session: Session) -> None:
    with record() as spy:
        for _ in range(3):
            session.scalars(select(User)).all()

    assert spy.slowest is not None
    assert spy.slowest.duration_ms <= spy.db_duration_ms


def test_sql_is_computed_lazily_and_cached(session: Session) -> None:
    """The perf fix: rendering a statement is ~half of all recording overhead."""
    with record() as spy:
        session.scalars(select(User)).all()

    record_ = spy.orm_records[0]
    assert "sql" not in record_.__dict__  # not rendered while recording
    first = record_.sql
    assert "sql" in record_.__dict__  # rendered on demand
    assert record_.sql is first  # and cached
