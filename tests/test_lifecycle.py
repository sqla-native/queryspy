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
