"""The pytest plugin.

Two independent mechanisms:

* the ``queryspy`` fixture hands a live recorder to a test that wants to inspect
  queries itself;
* a ``pytest_runtest_call`` wrapper enforces budgets and the N+1 gate.

The wrapper only engages when a policy actually asks for something, so a suite
that uses neither pays nothing.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from ._detect import DEFAULT_THRESHOLD
from ._recorder import Recorder
from ._report import render_findings
from .api import NPlusOneError, QueryCountError, record

__all__ = ["queryspy"]

_MARKER = "queryspy"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("queryspy")
    group.addoption(
        "--queryspy-strict",
        action="store_true",
        default=False,
        help="Fail any test that triggers an N+1 access pattern.",
    )
    parser.addini(
        "queryspy_budget",
        help="Maximum statements any single test may issue. Unset means no budget.",
        default="",
    )
    parser.addini(
        "queryspy_fail_on",
        help="Set to 'n_plus_one' to fail tests that trigger an N+1. Default 'none'.",
        default="none",
    )
    parser.addini(
        "queryspy_capture_stacks",
        type="bool",
        help="Attribute each query to a line of your code. Default true.",
        default=True,
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{_MARKER}(max_queries=None, allow_n_plus_one=False, threshold=2): "
        "per-test query budget and N+1 policy.",
    )


@dataclass(frozen=True)
class _Policy:
    max_queries: int | None
    check_n_plus_one: bool
    threshold: int
    capture_stacks: bool

    @property
    def active(self) -> bool:
        return self.max_queries is not None or self.check_n_plus_one


def _ini_budget(config: pytest.Config) -> int | None:
    raw = str(config.getini("queryspy_budget")).strip()
    return int(raw) if raw else None


def _resolve_policy(item: pytest.Item) -> _Policy:
    config = item.config
    max_queries = _ini_budget(config)
    check = (
        bool(config.getoption("--queryspy-strict"))
        or str(config.getini("queryspy_fail_on")) == "n_plus_one"
    )
    threshold = DEFAULT_THRESHOLD

    marker = item.get_closest_marker(_MARKER)
    if marker is not None:
        max_queries = marker.kwargs.get("max_queries", max_queries)
        threshold = marker.kwargs.get("threshold", threshold)
        if marker.kwargs.get("allow_n_plus_one", False):
            check = False
    return _Policy(
        max_queries=max_queries,
        check_n_plus_one=check,
        threshold=threshold,
        capture_stacks=bool(config.getini("queryspy_capture_stacks")),
    )


def _enforce(recorder: Recorder, policy: _Policy) -> None:
    if policy.max_queries is not None and recorder.query_count > policy.max_queries:
        raise QueryCountError(
            f"expected at most {policy.max_queries} queries, got {recorder.query_count}"
        )
    if policy.check_n_plus_one:
        findings = recorder.findings(threshold=policy.threshold)
        if findings:
            raise NPlusOneError("\n\n" + render_findings(findings))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    policy = _resolve_policy(item)
    if not policy.active:
        return (yield)

    with record(capture_stacks=policy.capture_stacks) as recorder:
        result = yield
    _enforce(recorder, policy)
    return result


@pytest.fixture
def queryspy(request: pytest.FixtureRequest) -> Iterator[Recorder]:
    """A live recorder covering the test.

    ::

        def test_list_users(session, queryspy):
            list_users(session)
            assert queryspy.query_count == 2
            assert not queryspy.findings()
    """
    capture = bool(request.config.getini("queryspy_capture_stacks"))
    with record(capture_stacks=capture) as recorder:
        yield recorder
