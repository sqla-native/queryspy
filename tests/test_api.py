"""The assertion context managers."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from queryspy import (
    NPlusOneError,
    QueryCountError,
    QuerySpyError,
    assert_max_queries,
    assert_num_queries,
    no_n_plus_one,
)

from .conftest import User


def test_assert_num_queries_passes_on_the_exact_count(session: Session) -> None:
    with assert_num_queries(1):
        session.scalars(select(User)).all()


def test_assert_num_queries_fails_when_over(session: Session) -> None:
    with (
        pytest.raises(QueryCountError, match="expected exactly 1 query, got 4"),
        assert_num_queries(1),
    ):
        for user in session.scalars(select(User)).all():
            list(user.addresses)


def test_assert_num_queries_fails_when_under(session: Session) -> None:
    with (
        pytest.raises(QueryCountError, match="expected exactly 2 queries, got 1"),
        assert_num_queries(2),
    ):
        session.scalars(select(User)).all()


def test_assert_num_queries_failure_includes_the_findings(session: Session) -> None:
    with pytest.raises(QueryCountError, match="selectinload"), assert_num_queries(1):
        for user in session.scalars(select(User)).all():
            list(user.addresses)


def test_assert_max_queries_passes_at_the_limit(session: Session) -> None:
    with assert_max_queries(2):
        session.scalars(select(User).options(selectinload(User.addresses))).all()


def test_assert_max_queries_fails_over_the_limit(session: Session) -> None:
    with (
        pytest.raises(QueryCountError, match="expected at most 2 queries, got 4"),
        assert_max_queries(2),
    ):
        for user in session.scalars(select(User)).all():
            list(user.addresses)


def test_no_n_plus_one_passes_on_eager_loading(session: Session) -> None:
    with no_n_plus_one():
        for user in session.scalars(select(User).options(selectinload(User.addresses))).all():
            list(user.addresses)


def test_no_n_plus_one_fails_on_a_lazy_loop(session: Session) -> None:
    with (
        pytest.raises(NPlusOneError, match=r"N\+1 detected: 3 queries for User.addresses"),
        no_n_plus_one(),
    ):
        for user in session.scalars(select(User)).all():
            list(user.addresses)


def test_no_n_plus_one_honours_the_threshold(session: Session) -> None:
    with no_n_plus_one(threshold=4):
        for user in session.scalars(select(User)).all():
            list(user.addresses)


def test_body_exception_is_not_masked_by_the_assertion(session: Session) -> None:
    """A failing body must surface its own error, not a query-count complaint."""
    with pytest.raises(ValueError, match="original"), assert_num_queries(99):
        session.scalars(select(User)).all()
        raise ValueError("original")


def test_errors_are_assertion_errors() -> None:
    assert issubclass(QuerySpyError, AssertionError)
    assert issubclass(QueryCountError, QuerySpyError)
    assert issubclass(NPlusOneError, QuerySpyError)
