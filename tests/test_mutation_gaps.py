"""Behaviour that a mutation run proved nothing was checking.

Every test here corresponds to a mutant that survived the audit — a change to
the source that the whole suite, at 100% branch coverage, did not notice.
Coverage says a line ran; these say the line matters.

The mutant each one kills is named, so a future reader can tell whether the test
is still earning its place.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from queryspy import (
    AppFrame,
    Finding,
    QueryCountError,
    QueryRecord,
    assert_num_queries,
    detect,
    record,
)
from queryspy._detect import _column_key, _lazy_key
from queryspy._hints import hint_for

from .conftest import Address, User


def _record(**overrides: object) -> QueryRecord:
    defaults: dict[str, object] = {
        "statement": "SELECT 1",
        "is_lazy_load": False,
        "is_column_load": False,
        "entity": "User",
        "path": None,
        "uselist": None,
        "frame": None,
    }
    defaults.update(overrides)
    return QueryRecord(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------- ordering


def test_findings_are_returned_worst_first() -> None:
    """Kills `detect__mutmut_59`: `-f.count` -> `+f.count`.

    "Worst first" is stated in the README, the API reference and three
    docstrings, and nothing asserted it.

    Built from explicit records rather than a session: the first attempt used
    two real relationships and produced only one finding, because the second
    lazy load hit the identity map. Sorting a one-element list cannot fail, so
    the test passed against the mutant and proved nothing.
    """
    small = [_record(is_lazy_load=True, path="User.orders") for _ in range(2)]
    large = [_record(is_lazy_load=True, path="User.addresses") for _ in range(5)]

    # Smaller group first in input order, so only the sort can fix the output.
    findings = detect(small + large)

    assert [(f.label, f.count) for f in findings] == [
        ("User.addresses", 5),
        ("User.orders", 2),
    ]


def test_worst_first_holds_for_real_sessions_too(session: Session) -> None:
    """The same property against a real unit of work, with two live groups."""
    with record() as spy:
        # Addresses 1 and 3 belong to different users, so this is two distinct
        # lazy loads rather than one plus an identity-map hit.
        for address in session.scalars(select(Address).where(Address.id.in_([1, 3]))).all():
            assert address.user is not None
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    counts = [f.count for f in spy.findings()]
    assert len(counts) == 2, spy.findings()
    assert counts == sorted(counts, reverse=True)


def test_a_small_group_does_not_hide_the_ones_after_it(session: Session) -> None:
    """Kills `_collect__mutmut_9`: `continue` -> `break`.

    A group below the threshold must be skipped, not stop the scan. With
    `break`, one harmless single query issued early would suppress every finding
    that followed it.
    """
    with record() as spy:
        session.get(User, 1)  # one query of its own shape: below threshold
        for user_id in (1, 2, 3):  # a different shape, three times
            session.get(Address, user_id)

    findings = spy.findings()
    assert [f.kind for f in findings] == ["repeated_statement"]
    assert findings[0].count == 3


# ----------------------------------------------------------------- timing


def test_duration_is_reported_in_milliseconds(session: Session) -> None:
    """Kills `_on_cursor_execute_done__mutmut_5`: `* 1000` -> `/ 1000`.

    Every timing test only asserted `> 0`, which holds whatever the unit is. A
    conversion off by a factor of a million would have shipped unnoticed.
    """
    started = time.perf_counter()
    with record() as spy:
        for _ in range(5):
            session.scalars(select(User)).all()
    wall_ms = (time.perf_counter() - started) * 1000

    # Driver time is a real fraction of wall-clock time, in the same unit.
    assert spy.db_duration_ms > 0.001
    assert spy.db_duration_ms <= wall_ms
    assert spy.db_duration_ms > wall_ms / 1000


# -------------------------------------------------------- grouping keys


def test_lazy_key_falls_back_through_path_then_entity_then_sql() -> None:
    """Kills `_lazy_key__mutmut_2`: the `or` chain becoming `and`.

    In practice a lazy load has a path, so the fallbacks never ran in anger.
    They still have to be right: `and` would return the SQL whenever an entity
    was present, collapsing unrelated relationships into one group.
    """
    assert _lazy_key(_record(is_lazy_load=True, path="User.addresses")) == "User.addresses"
    assert _lazy_key(_record(is_lazy_load=True, path=None, entity="User")) == "User"
    assert _lazy_key(_record(is_lazy_load=True, path=None, entity=None)) == "SELECT 1"
    assert _lazy_key(_record(is_lazy_load=False, path="User.addresses")) is None


def test_column_key_falls_back_from_entity_to_sql() -> None:
    """Kills `_column_key__mutmut_2`: `or` becoming `and`."""
    assert _column_key(_record(is_column_load=True, entity="User")) == "User"
    assert _column_key(_record(is_column_load=True, entity=None)) == "SELECT 1"
    assert _column_key(_record(is_column_load=False, entity="User")) is None


def test_two_relationships_with_no_path_stay_separate() -> None:
    """The consequence of the above, at the level someone would notice."""
    records = [
        _record(is_lazy_load=True, path=None, entity="User", statement="SELECT a"),
        _record(is_lazy_load=True, path=None, entity="User", statement="SELECT a"),
        _record(is_lazy_load=True, path=None, entity="Order", statement="SELECT b"),
        _record(is_lazy_load=True, path=None, entity="Order", statement="SELECT b"),
    ]
    assert sorted(f.label for f in detect(records)) == ["Order", "User"]


# ------------------------------------------------------------------ hints


def test_column_hint_names_the_entity() -> None:
    """Kills `_column_hint__mutmut_1`: `finding.entity or "the model"` -> `None`.

    All four mutants of this function survived: the hint text was rendered in
    tests but never read closely enough to notice it naming the wrong thing.
    """
    finding = Finding(
        kind="column_load", label="User", count=3, sql="SELECT", frame=None, entity="User"
    )
    hint = hint_for(finding)
    assert "User defers this column" in hint
    assert "undefer" in hint
    assert "expire_on_commit=False" in hint


def test_column_hint_without_an_entity_still_reads() -> None:
    finding = Finding(kind="column_load", label="?", count=3, sql="SELECT", frame=None, entity=None)
    assert "the model defers this column" in hint_for(finding)


def test_repeated_statement_hint_names_the_entity() -> None:
    finding = Finding(
        kind="repeated_statement", label="SELECT", count=3, sql="SELECT", frame=None, entity="User"
    )
    assert "fetch User in one statement" in hint_for(finding)


# ------------------------------------------------- count-failure messages


def test_a_count_failure_names_the_dominant_statement(session: Session) -> None:
    """Kills `_detail__mutmut_3`: `recorder.slowest` -> `None`.

    When one statement is most of the time, the failure message says which. That
    line was rendered but never asserted.
    """
    with pytest.raises(QueryCountError) as caught, assert_num_queries(99):
        session.scalars(select(User)).all()

    message = str(caught.value)
    assert "in the database" in message
    assert "in one statement" in message
    assert "SELECT" in message


def test_a_count_failure_still_reports_timing_with_no_findings(session: Session) -> None:
    with pytest.raises(QueryCountError, match="in the database"), assert_num_queries(99):
        session.scalars(select(User)).all()


# --------------------------------------------------------------- frames


def test_app_frame_equality_is_by_value() -> None:
    """Findings are deduplicated and compared; the frame has to behave."""
    a = AppFrame(filename="x.py", lineno=1, function="f")
    b = AppFrame(filename="x.py", lineno=1, function="f")
    assert a == b
    assert a != AppFrame(filename="x.py", lineno=2, function="f")
