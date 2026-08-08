"""N+1 and query-budget detection for SQLAlchemy 2.0, sync and async.

::

    from queryspy import assert_max_queries, no_n_plus_one

    def test_list_users(session):
        with no_n_plus_one():
            list_users(session)
"""

from __future__ import annotations

from ._detect import DEFAULT_THRESHOLD, Finding, detect
from ._frames import AppFrame
from ._recorder import QueryRecord, Recorder, SlowStatement
from ._report import render_finding, render_findings
from ._serialize import to_dict, to_json, to_sarif
from .api import (
    NPlusOneError,
    QueryCountError,
    QuerySpyError,
    assert_max_queries,
    assert_num_queries,
    ignore,
    no_n_plus_one,
    record,
)

__all__ = [
    "DEFAULT_THRESHOLD",
    "AppFrame",
    "Finding",
    "NPlusOneError",
    "QueryCountError",
    "QueryRecord",
    "QuerySpyError",
    "Recorder",
    "SlowStatement",
    "assert_max_queries",
    "assert_num_queries",
    "detect",
    "ignore",
    "no_n_plus_one",
    "record",
    "render_finding",
    "render_findings",
    "to_dict",
    "to_json",
    "to_sarif",
]

__version__ = "0.4.0"
