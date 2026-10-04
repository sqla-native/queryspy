"""The pytest plugin.

Two independent mechanisms:

* the ``queryspy`` fixture hands a live recorder to a test that wants to inspect
  queries itself;
* a ``pytest_runtest_call`` wrapper enforces budgets and the N+1 gate.

The wrapper only engages when a policy actually asks for something, so a suite
that uses neither pays nothing.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from . import __version__
from ._baseline import BaselineEntry
from ._baseline import load as load_baseline
from ._baseline import save as save_baseline
from ._baseline import split as split_baseline
from ._baseline import stale as stale_entries
from ._detect import DEFAULT_THRESHOLD, KINDS, Finding
from ._recorder import Recorder
from ._report import render_findings
from ._serialize import to_json, to_sarif
from .api import NPlusOneError, QueryCountError, record

__all__ = ["queryspy"]

_MARKER = "queryspy"
_FINDINGS: pytest.StashKey[list[Finding]] = pytest.StashKey()
_BASELINE: pytest.StashKey[set[BaselineEntry]] = pytest.StashKey()
_FAIL_ON: pytest.StashKey[frozenset[str]] = pytest.StashKey()
_ALL_KINDS = frozenset(KINDS)


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
        help=(
            "Which findings fail a test: 'none' (default), 'n_plus_one' (every kind), "
            "or a comma-separated list of kinds: " + ", ".join(KINDS) + "."
        ),
        default="none",
    )
    parser.addini(
        "queryspy_capture_stacks",
        type="bool",
        help="Attribute each query to a line of your code. Default true.",
        default=True,
    )
    group.addoption(
        "--queryspy-report",
        metavar="PATH",
        default=None,
        help="Write findings from the whole session to PATH (.sarif or .json).",
    )
    group.addoption(
        "--queryspy-report-format",
        choices=("auto", "json", "sarif"),
        default="auto",
        help="Report format. 'auto' picks SARIF for a .sarif path, else JSON.",
    )
    group.addoption(
        "--queryspy-baseline",
        metavar="PATH",
        default=None,
        help="Tolerate findings recorded in PATH; fail only on new ones.",
    )
    group.addoption(
        "--queryspy-baseline-update",
        action="store_true",
        default=False,
        help="Rewrite the baseline from this run instead of enforcing it.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{_MARKER}(max_queries=None, allow_n_plus_one=False, threshold=2, fail_on=None): "
        "per-test query budget and N+1 policy.",
    )
    try:
        config.stash[_FAIL_ON] = _parse_fail_on(config.getini("queryspy_fail_on"))
    except ValueError as error:
        raise pytest.UsageError(f"queryspy_fail_on: {error}") from None
    path = _baseline_path(config)
    if path is not None:
        config.stash[_BASELINE] = load_baseline(path)


def _baseline_path(config: pytest.Config) -> Path | None:
    raw = config.getoption("--queryspy-baseline")
    return None if raw is None else Path(str(raw))


def _parse_fail_on(value: str | Iterable[str]) -> frozenset[str]:
    """The finding kinds a ``fail_on`` value gates on.

    ``none`` gates on nothing, ``n_plus_one`` on every kind, and anything else
    is a comma-separated string or an iterable of kind names. An unknown name
    raises ``ValueError``, so a typo cannot quietly turn the gate off.
    """
    names = value.split(",") if isinstance(value, str) else list(value)
    kinds: set[str] = set()
    for raw in names:
        name = raw.strip()
        if name in ("", "none"):
            continue
        if name == "n_plus_one":
            kinds |= _ALL_KINDS
        elif name in _ALL_KINDS:
            kinds.add(name)
        else:
            expected = ", ".join(("none", "n_plus_one", *KINDS))
            raise ValueError(f"unknown value {name!r}; expected one of {expected}")
    return frozenset(kinds)


@dataclass(frozen=True)
class _Policy:
    max_queries: int | None
    fail_on: frozenset[str]
    """The finding kinds that fail the test; empty when none do."""
    threshold: int
    capture_stacks: bool
    collect: bool
    """Record even when nothing is enforced, because a report or a baseline
    rewrite needs the full picture."""

    @property
    def active(self) -> bool:
        return self.max_queries is not None or bool(self.fail_on) or self.collect


def _ini_budget(config: pytest.Config) -> int | None:
    raw = str(config.getini("queryspy_budget")).strip()
    return int(raw) if raw else None


def _session_fail_on(config: pytest.Config) -> frozenset[str]:
    if config.getoption("--queryspy-strict"):
        return _ALL_KINDS
    return config.stash[_FAIL_ON]


def _marker_fail_on(marker: pytest.Mark, fail_on: frozenset[str]) -> frozenset[str]:
    if marker.kwargs.get("allow_n_plus_one", False):
        return frozenset()
    if "fail_on" in marker.kwargs:
        return _parse_fail_on(marker.kwargs["fail_on"])
    return fail_on


def _resolve_policy(item: pytest.Item) -> _Policy:
    config = item.config
    max_queries = _ini_budget(config)
    updating = bool(config.getoption("--queryspy-baseline-update"))
    fail_on = _session_fail_on(config)
    threshold = DEFAULT_THRESHOLD

    marker = item.get_closest_marker(_MARKER)
    if marker is not None:
        max_queries = marker.kwargs.get("max_queries", max_queries)
        threshold = marker.kwargs.get("threshold", threshold)
        fail_on = _marker_fail_on(marker, fail_on)
    return _Policy(
        max_queries=max_queries,
        # Rewriting the baseline is a recording run, not an enforcing one.
        fail_on=frozenset() if updating else fail_on,
        threshold=threshold,
        capture_stacks=bool(config.getini("queryspy_capture_stacks")),
        collect=config.getoption("--queryspy-report") is not None or updating,
    )


def _enforce(
    recorder: Recorder,
    policy: _Policy,
    baseline: set[BaselineEntry] | None,
    root: str,
) -> None:
    if policy.max_queries is not None and recorder.query_count > policy.max_queries:
        raise QueryCountError(
            f"expected at most {policy.max_queries} queries, got {recorder.query_count}"
        )
    if not policy.fail_on:
        return
    findings = [
        finding
        for finding in recorder.findings(threshold=policy.threshold)
        if finding.kind in policy.fail_on
    ]
    if baseline is not None:
        findings, _known = split_baseline(findings, baseline, root=root)
    if findings:
        raise NPlusOneError("\n\n" + render_findings(findings))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    policy = _resolve_policy(item)
    if not policy.active:
        return (yield)

    with record(capture_stacks=policy.capture_stacks) as recorder:
        result = yield

    root = str(item.config.rootpath)
    found = recorder.findings(threshold=policy.threshold)
    if policy.collect or _BASELINE in item.config.stash:
        _collected(item.config).extend(replace(finding, origin=item.nodeid) for finding in found)
    _enforce(recorder, policy, item.config.stash.get(_BASELINE, None), root)
    return result


def _collected(config: pytest.Config) -> list[Finding]:
    return config.stash.setdefault(_FINDINGS, [])


def _report_format(path: str, choice: str) -> str:
    if choice != "auto":
        return choice
    return "sarif" if path.endswith(".sarif") else "json"


def _write_report(session: pytest.Session, findings: list[Finding], root: str) -> None:
    destination = session.config.getoption("--queryspy-report")
    if destination is None:
        return
    fmt = _report_format(destination, str(session.config.getoption("--queryspy-report-format")))
    render = to_sarif if fmt == "sarif" else to_json
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(findings, version=__version__, root=root), encoding="utf-8")


def _handle_baseline(session: pytest.Session, findings: list[Finding], root: str) -> None:
    path = _baseline_path(session.config)
    if path is None:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")

    if session.config.getoption("--queryspy-baseline-update"):
        count = save_baseline(path, findings, version=__version__, root=root)
        _note(reporter, f"queryspy: wrote {_entries(count)} to {path}")
        return

    gone = stale_entries(session.config.stash[_BASELINE], findings, root=root)
    if gone:
        verb = "occurs" if len(gone) == 1 else "occur"
        _note(reporter, f"queryspy: {_entries(len(gone))} no longer {verb}:")
        for entry in gone:
            _note(reporter, f"  - {entry}")
        _note(reporter, "  run with --queryspy-baseline-update to prune them")


def _entries(count: int) -> str:
    return f"{count} baseline entr{'y' if count == 1 else 'ies'}"


def _note(reporter: Any, message: str) -> None:
    if reporter is not None:  # pragma: no branch - always present in a real run
        reporter.write_line(message)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Write the report and settle the baseline, if either was asked for.

    Runs regardless of test outcomes: both are most useful when the run failed.
    """
    findings = _collected(session.config)
    root = str(session.config.rootpath)
    _write_report(session, findings, root)
    _handle_baseline(session, findings, root)


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
