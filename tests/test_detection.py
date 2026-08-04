"""The detection suite - every access pattern queryspy must catch."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from queryspy import record

from .conftest import Team, User


def _kinds(spy: object) -> list[str]:
    return [f.kind for f in spy.findings()]  # type: ignore[attr-defined]


def test_sync_lazy_load_loop(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "lazy_load"
    assert findings[0].label == "User.addresses"
    assert findings[0].count == 3
    assert findings[0].uselist is True


def test_lazy_load_finding_attributes_to_this_file(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    frame = spy.findings()[0].frame
    assert frame is not None
    assert frame.filename.endswith("test_detection.py")
    assert frame.function == "test_lazy_load_finding_attributes_to_this_file"


def test_many_to_one_lazy_load_reports_uselist_false(session: Session) -> None:
    from .conftest import Address

    with record() as spy:
        for address in session.scalars(select(Address)).all():
            assert address.user is not None

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "lazy_load"
    assert findings[0].label == "Address.user"
    assert findings[0].uselist is False


def test_deferred_column_loop(session: Session) -> None:
    """Measured: is_column_load=True with lazy_loaded_from unset."""
    with record() as spy:
        for user in session.scalars(select(User)).all():
            assert user.bio

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "column_load"
    assert findings[0].entity == "User"
    assert findings[0].count == 3


def test_expired_attribute_refresh_loop(session: Session) -> None:
    """Post-commit refreshes share the column-load signature and are real work."""
    users = session.scalars(select(User)).all()
    session.commit()

    with record() as spy:
        for user in users:
            assert user.name

    assert "column_load" in _kinds(spy)


def test_session_get_loop(session: Session) -> None:
    """Not an ORM lazy load at all - only the repeated-statement detector sees it."""
    with record() as spy:
        for user_id in (1, 2, 3):
            session.get(User, user_id)

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "repeated_statement"
    assert findings[0].count == 3


def test_parents_fetched_one_by_one_with_configured_eager_loading(session: Session) -> None:
    """Two repeated templates, neither carrying lazy_loaded_from."""
    with record() as spy:
        for team_id in (1, 2, 3):
            team = session.get(Team, team_id)
            assert team is not None
            list(team.members)

    kinds = _kinds(spy)
    assert kinds == ["repeated_statement", "repeated_statement"]


def test_a_lazy_load_is_reported_once_not_twice(session: Session) -> None:
    """The specific detector claims its records so the backstop cannot re-report them."""
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    assert _kinds(spy) == ["lazy_load"]


def test_threshold_is_respected(session: Session) -> None:
    with record() as spy:
        for user_id in (1, 2, 3):
            session.get(User, user_id)

    assert spy.findings(threshold=4) == []
    assert len(spy.findings(threshold=3)) == 1


@pytest.mark.asyncio
async def test_async_awaitable_attrs_loop(async_session: object) -> None:
    """The most common async N+1 - and it does set lazy_loaded_from."""
    with record() as spy:
        users = (await async_session.scalars(select(User))).all()  # type: ignore[attr-defined]
        for user in users:
            await user.awaitable_attrs.addresses

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "lazy_load"
    assert findings[0].label == "User.addresses"


@pytest.mark.asyncio
async def test_async_session_get_loop(async_session: object) -> None:
    with record() as spy:
        for user_id in (1, 2, 3):
            await async_session.get(User, user_id)  # type: ignore[attr-defined]

    findings = spy.findings()
    assert len(findings) == 1
    assert findings[0].kind == "repeated_statement"
