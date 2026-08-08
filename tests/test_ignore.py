"""The ignore() escape hatch."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from queryspy import ignore, no_n_plus_one, record

from .conftest import User


def n_plus_one(session: Session) -> None:
    for user in session.scalars(select(User)).all():
        list(user.addresses)


def test_findings_are_suppressed(session: Session) -> None:
    with record() as spy, ignore():
        n_plus_one(session)

    assert spy.findings() == []


def test_queries_are_still_counted(session: Session) -> None:
    """A query that ran, ran. Only detection is suppressed."""
    with record() as spy, ignore():
        n_plus_one(session)

    assert spy.query_count == 4


def test_only_the_block_is_ignored(session: Session) -> None:
    with record() as spy:
        with ignore():
            n_plus_one(session)
        n_plus_one(session)

    findings = spy.findings()
    assert [f.kind for f in findings] == ["lazy_load"]
    # Three, not six: the ignored half cannot contribute to the count either.
    assert findings[0].count == 3


def test_ignored_records_cannot_push_a_group_over_the_threshold(session: Session) -> None:
    with record() as spy:
        with ignore():
            for user_id in (1, 2):
                session.get(User, user_id)
        session.get(User, 3)

    assert spy.findings() == []


def test_nesting(session: Session) -> None:
    with record() as spy:
        with ignore():
            with ignore():
                n_plus_one(session)
            # Still inside the outer block.
            n_plus_one(session)
        n_plus_one(session)

    assert spy.findings()[0].count == 3


def test_it_unblocks_the_gate(session: Session) -> None:
    with no_n_plus_one(), ignore():
        n_plus_one(session)


def test_outside_a_window_is_a_no_op(session: Session) -> None:
    with ignore():
        session.scalars(select(User)).all()

    with record() as spy:
        n_plus_one(session)
    assert spy.findings() != []


def test_records_are_marked(session: Session) -> None:
    with record() as spy, ignore():
        session.scalars(select(User)).all()

    assert all(record_.ignored for record_ in spy.orm_records)


@pytest.mark.asyncio
async def test_it_crosses_the_greenlet_bridge(async_session: object) -> None:
    """Async lazy loads run in a spawned greenlet; the flag has to follow."""
    with record() as spy, ignore():
        users = (await async_session.scalars(select(User))).all()  # type: ignore[attr-defined]
        for user in users:
            await user.awaitable_attrs.addresses

    assert spy.findings() == []
    assert spy.query_count == 4
