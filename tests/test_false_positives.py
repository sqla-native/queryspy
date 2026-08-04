"""The false-positive gate.

Constitution rule 3: a false positive is worse than a missed detection. A linter
that flags correctly-written code gets uninstalled the same day, so these carry
the same weight as the detection tests.

Every case here is code a reviewer would sign off on. None of it may produce a
finding.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload, subqueryload, undefer

from queryspy import record

from .conftest import Address, Team, User


def test_single_query_is_not_a_finding(session: Session) -> None:
    with record() as spy:
        session.scalars(select(User)).all()
    assert spy.findings() == []


def test_selectinload_is_the_fix_not_the_bug(session: Session) -> None:
    """The spike measured is_relationship_load=True here with lazy_loaded_from unset.

    Keying detection on is_relationship_load would flag this - the exact
    cry-wolf failure constitution rule 7 exists to prevent.
    """
    with record() as spy:
        for user in session.scalars(select(User).options(selectinload(User.addresses))).all():
            list(user.addresses)
    assert spy.findings() == []
    assert spy.query_count == 2


def test_subqueryload_is_not_flagged(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User).options(subqueryload(User.addresses))).all():
            list(user.addresses)
    assert spy.findings() == []


def test_joinedload_is_not_flagged(session: Session) -> None:
    with record() as spy:
        users = session.scalars(select(User).options(joinedload(User.addresses))).unique().all()
        for user in users:
            list(user.addresses)
    assert spy.findings() == []
    assert spy.query_count == 1


def test_undefer_is_not_flagged(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User).options(undefer(User.bio))).all():
            assert user.bio
    assert spy.findings() == []


def test_configured_eager_strategy_over_a_batch_is_not_flagged(session: Session) -> None:
    """lazy="selectin" on the relationship fires once per batch, not per parent."""
    with record() as spy:
        for team in session.scalars(select(Team)).all():
            list(team.members)
    assert spy.findings() == []
    assert spy.query_count == 2


def test_bulk_insert_is_not_flagged(session: Session) -> None:
    with record() as spy:
        session.add_all([Address(email=f"bulk{n}@example.com", user_id=1) for n in range(5)])
        session.flush()
    assert spy.findings() == []


def test_autoflush_insert_before_select_is_not_flagged(session: Session) -> None:
    """Flushes never reach do_orm_execute - measured 1 ORM execute, 2 statements."""
    with record() as spy:
        session.add(User(name="new", bio="b"))
        session.scalars(select(User)).all()
    assert spy.findings() == []
    assert spy.query_count == 2


def test_two_different_queries_are_not_a_repeat(session: Session) -> None:
    with record() as spy:
        session.scalars(select(User)).all()
        session.scalars(select(Address)).all()
    assert spy.findings() == []


@pytest.mark.asyncio
async def test_async_selectinload_is_not_flagged(async_session: object) -> None:
    with record() as spy:
        result = await async_session.scalars(  # type: ignore[attr-defined]
            select(User).options(selectinload(User.addresses))
        )
        for user in result.all():
            list(user.addresses)
    assert spy.findings() == []
