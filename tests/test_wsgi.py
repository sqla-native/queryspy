"""The WSGI middleware.

Driven through a hand-rolled harness rather than a framework test client, so the
"works with any WSGI app" claim is actually exercised. Streaming gets its own
tests: the returned iterable is where WSGI differs from ASGI in a way that
matters.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from queryspy._recorder import listeners_installed
from queryspy.wsgi import QuerySpyMiddleware, RequestReport

from .conftest import User


class Harness:
    """A minimal WSGI server: calls the app, drains the body, then closes."""

    def __init__(self) -> None:
        self.status: str | None = None
        self.headers: dict[str, str] = {}

    def start_response(self, status: str, headers: list[tuple[str, str]], *rest: Any) -> Any:
        self.status = status
        self.headers = dict(headers)
        return lambda chunk: None

    def request(
        self, app: QuerySpyMiddleware, *, path: str = "/users", method: str = "GET"
    ) -> bytes:
        environ = {"PATH_INFO": path, "REQUEST_METHOD": method}
        result = app(environ, self.start_response)
        try:
            body = b"".join(result)
        finally:
            closer = getattr(result, "close", None)
            if closer is not None:
                closer()
        return body


def make_app(engine: object, *, limit: int | None = None, streaming: bool = False) -> Any:
    """A WSGI app that talks to the database, then responds."""

    def app(environ: dict[str, Any], start_response: Any) -> Iterable[bytes]:
        def work() -> Iterator[bytes]:
            with Session(engine) as session:  # type: ignore[arg-type]
                statement = select(User)
                if limit is not None:
                    statement = statement.limit(limit)
                for user in session.scalars(statement).all():
                    list(user.addresses)
                    yield b"row"

        if streaming:
            # Queries happen while the body is being consumed, after __call__
            # has already returned.
            start_response("200 OK", [("Content-Type", "text/plain")])
            return work()
        # The ordinary shape: do the work, then start the response. Headers are
        # therefore written after the queries have run.
        body = b"".join(work())
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [body]

    return app


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_counts_queries_and_sets_headers(engine: object, harness: Harness) -> None:
    reports: list[RequestReport] = []
    app = QuerySpyMiddleware(make_app(engine), on_report=reports.append)

    harness.request(app)

    assert harness.headers["x-queryspy-queries"] == "4"
    assert harness.headers["x-queryspy-findings"] == "1"
    assert reports[0].query_count == 4
    assert [f.kind for f in reports[0].findings] == ["lazy_load"]


def test_streaming_response_is_counted_in_full(engine: object, harness: Harness) -> None:
    """The reason the window cannot close when __call__ returns.

    Every query here runs while the body is being consumed. Finalising early
    would report one query instead of four.
    """
    reports: list[RequestReport] = []
    app = QuerySpyMiddleware(make_app(engine, streaming=True), on_report=reports.append)

    body = harness.request(app)

    assert body == b"rowrowrow"
    assert reports[0].query_count == 4
    assert [f.kind for f in reports[0].findings] == ["lazy_load"]
    # Headers went out before the body ran, so they show the count at that point.
    assert harness.headers["x-queryspy-queries"] == "0"


def test_the_window_is_closed_afterwards(engine: object, harness: Harness) -> None:
    app = QuerySpyMiddleware(make_app(engine))
    harness.request(app)
    assert not listeners_installed()


def test_report_is_emitted_once_even_though_close_follows_exhaustion(
    engine: object, harness: Harness
) -> None:
    reports: list[RequestReport] = []
    app = QuerySpyMiddleware(make_app(engine), on_report=reports.append)

    harness.request(app)

    assert len(reports) == 1


def test_a_server_that_never_calls_close_still_reports(engine: object) -> None:
    reports: list[RequestReport] = []
    app = QuerySpyMiddleware(make_app(engine), on_report=reports.append)

    result = app({"PATH_INFO": "/x", "REQUEST_METHOD": "GET"}, lambda *a: None)
    b"".join(result)  # exhausted, never closed

    assert len(reports) == 1
    assert not listeners_installed()


def test_a_failing_app_still_reports(engine: object, harness: Harness) -> None:
    reports: list[RequestReport] = []

    def app(environ: dict[str, Any], start_response: Any) -> Iterable[bytes]:
        with Session(engine) as session:  # type: ignore[arg-type]
            session.scalars(select(User)).all()
        raise RuntimeError("boom")

    middleware = QuerySpyMiddleware(app, on_report=reports.append)

    with pytest.raises(RuntimeError, match="boom"):
        harness.request(middleware)

    assert reports[0].query_count == 1
    assert not listeners_installed()


def test_findings_are_logged(
    engine: object, harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    app = QuerySpyMiddleware(make_app(engine))
    with caplog.at_level(logging.WARNING, logger="queryspy"):
        harness.request(app, path="/users")

    assert "GET /users" in caplog.text
    assert "N+1 detected: 3 queries for User.addresses" in caplog.text


def test_budget_warning(engine: object, harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    app = QuerySpyMiddleware(make_app(engine, limit=1), budget=1)
    with caplog.at_level(logging.WARNING, logger="queryspy"):
        harness.request(app)

    assert "exceeds the budget of 1" in caplog.text


def test_headers_can_be_disabled(engine: object, harness: Harness) -> None:
    app = QuerySpyMiddleware(make_app(engine), add_headers=False)
    harness.request(app)
    assert "x-queryspy-queries" not in harness.headers


def test_exc_info_is_passed_through(engine: object) -> None:
    """The optional third argument must survive the wrapper."""
    seen: list[int] = []

    def start_response(status: str, headers: list[tuple[str, str]], *rest: Any) -> Any:
        seen.append(len(rest))
        return lambda chunk: None

    def app(environ: dict[str, Any], sr: Any) -> Iterable[bytes]:
        sr("500 Internal Server Error", [], (ValueError, ValueError("x"), None))
        return [b""]

    middleware = QuerySpyMiddleware(app)
    result = middleware({"PATH_INFO": "/x", "REQUEST_METHOD": "GET"}, start_response)
    b"".join(result)

    assert seen == [1]  # exc_info reached the underlying start_response


def test_panel(engine: object, harness: Harness) -> None:
    app = QuerySpyMiddleware(make_app(engine), panel=True)
    harness.request(app, path="/users")

    page = harness.request(app, path="/__queryspy__").decode()

    assert harness.headers["Content-Type"] == "text/html; charset=utf-8"
    assert harness.headers["Cache-Control"] == "no-store"
    assert "GET /users" in page
    assert "selectinload(User.addresses)" in page


def test_panel_is_off_by_default(engine: object, harness: Harness) -> None:
    app = QuerySpyMiddleware(make_app(engine))
    body = harness.request(app, path="/__queryspy__")
    assert b"queryspy" not in body


def test_concurrent_requests_are_isolated() -> None:
    """A fresh thread gets a fresh context, which is what isolates requests.

    Each thread builds its own engine: SQLite connections are thread-affine, and
    sharing one would test the driver rather than the recorder. If the context
    variable leaked, each report would show the sum of both requests.
    """
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from .conftest import Base, seed

    reports: list[RequestReport] = []
    barrier = threading.Barrier(2)

    def run(limit: int | None, path: str) -> None:
        own_engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(own_engine)
        with Session(own_engine) as session:
            seed(session)
        app = QuerySpyMiddleware(make_app(own_engine, limit=limit), on_report=reports.append)
        harness = Harness()
        barrier.wait()
        harness.request(app, path=path)
        own_engine.dispose()

    threads = [
        threading.Thread(target=run, args=(1, "/light")),
        threading.Thread(target=run, args=(None, "/heavy")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    by_path = {report.path: report for report in reports}
    assert by_path["/light"].query_count == 2
    assert by_path["/heavy"].query_count == 4
    assert not listeners_installed()
