"""The ASGI middleware.

Driven through a hand-rolled harness rather than a test client, which keeps the
suite honest about the claim that this needs no framework: if it only worked
under Starlette, these tests would not pass.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from queryspy.asgi import QuerySpyMiddleware, RequestReport

from .conftest import User

Scope = dict[str, Any]
Message = dict[str, Any]


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def call(
    middleware: QuerySpyMiddleware, *, method: str = "GET", path: str = "/users"
) -> list[Message]:
    """Drive one request through the middleware and collect what it sent."""
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": method, "path": path, "headers": []}
    await middleware(scope, _receive, send)
    return sent


def make_app(engine: object, *, eager: bool = False, limit: int | None = None) -> Any:
    """An ASGI app that talks to the database, then responds."""

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        async with AsyncSession(engine) as session:  # type: ignore[arg-type]
            statement = select(User)
            if limit is not None:
                statement = statement.limit(limit)
            users = (await session.scalars(statement)).all()
            if not eager:
                for user in users:
                    await user.awaitable_attrs.addresses
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def _headers(sent: list[Message]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])


@pytest.mark.asyncio
async def test_counts_queries_and_sets_headers(async_engine: object) -> None:
    reports: list[RequestReport] = []
    middleware = QuerySpyMiddleware(make_app(async_engine), on_report=reports.append)

    sent = await call(middleware)

    assert _headers(sent)[b"x-queryspy-queries"] == b"4"
    assert _headers(sent)[b"x-queryspy-findings"] == b"1"
    assert reports[0].query_count == 4
    assert [f.kind for f in reports[0].findings] == ["lazy_load"]
    assert not reports[0].clean


@pytest.mark.asyncio
async def test_a_clean_request_reports_no_findings(async_engine: object) -> None:
    reports: list[RequestReport] = []
    middleware = QuerySpyMiddleware(make_app(async_engine, limit=1), on_report=reports.append)

    sent = await call(middleware)

    assert reports[0].clean
    assert b"x-queryspy-findings" not in _headers(sent)


@pytest.mark.asyncio
async def test_findings_are_logged_with_the_report(
    async_engine: object, caplog: pytest.LogCaptureFixture
) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine))
    with caplog.at_level(logging.WARNING, logger="queryspy"):
        await call(middleware, path="/users")

    assert "GET /users" in caplog.text
    assert "N+1 detected: 3 queries for User.addresses" in caplog.text
    assert "selectinload(User.addresses)" in caplog.text


@pytest.mark.asyncio
async def test_budget_warns_without_findings(
    async_engine: object, caplog: pytest.LogCaptureFixture
) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine, limit=1), budget=1)
    with caplog.at_level(logging.WARNING, logger="queryspy"):
        await call(middleware)

    assert "exceeds the budget of 1" in caplog.text


@pytest.mark.asyncio
async def test_clean_request_under_budget_is_debug_only(
    async_engine: object, caplog: pytest.LogCaptureFixture
) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine, limit=1), budget=10)
    with caplog.at_level(logging.WARNING, logger="queryspy"):
        await call(middleware)
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_headers_can_be_disabled(async_engine: object) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine), add_headers=False)
    sent = await call(middleware)
    assert _headers(sent) == {}


@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    middleware = QuerySpyMiddleware(app)
    await middleware({"type": "lifespan"}, _receive, lambda _m: asyncio.sleep(0))

    assert seen == ["lifespan"]


@pytest.mark.asyncio
async def test_a_failing_request_still_reports(async_engine: object) -> None:
    """The report is most useful precisely when the request blew up."""
    reports: list[RequestReport] = []

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        async with AsyncSession(async_engine) as session:  # type: ignore[arg-type]
            await session.scalars(select(User))
        raise RuntimeError("boom")

    middleware = QuerySpyMiddleware(app, on_report=reports.append)

    with pytest.raises(RuntimeError, match="boom"):
        await call(middleware)

    assert reports[0].query_count == 1


@pytest.mark.asyncio
async def test_concurrent_requests_are_isolated(async_engine: object) -> None:
    """The reason the recorder is context-scoped rather than global."""
    reports: list[RequestReport] = []
    light = QuerySpyMiddleware(make_app(async_engine, limit=1), on_report=reports.append)
    heavy = QuerySpyMiddleware(make_app(async_engine), on_report=reports.append)

    await asyncio.gather(
        call(light, path="/light"),
        call(heavy, path="/heavy"),
        call(light, path="/light"),
    )

    by_path = {r.path: r for r in reports}
    assert by_path["/light"].query_count == 2
    assert by_path["/heavy"].query_count == 4
    assert by_path["/light"].clean
    assert not by_path["/heavy"].clean


def test_request_report_rendering() -> None:
    clean = RequestReport(
        method="GET", path="/x", query_count=1, findings=[], duration_ms=12.34, db_duration_ms=4.0
    )
    assert clean.render() == "GET /x - 1 query in 12.3ms (4.0ms in the database)"
    assert clean.clean

    plural = RequestReport(method="GET", path="/x", query_count=2, findings=[], duration_ms=1.0)
    assert "2 queries" in plural.render()


@pytest.mark.asyncio
async def test_timing_is_measured(async_engine: object) -> None:
    reports: list[RequestReport] = []
    middleware = QuerySpyMiddleware(make_app(async_engine), on_report=reports.append)
    await call(middleware)

    report = reports[0]
    assert report.db_duration_ms > 0
    # Driver time is a subset of wall-clock time for the request.
    assert report.db_duration_ms <= report.duration_ms
    assert report.slowest is not None
    assert report.slowest.sql.startswith("SELECT")


@pytest.mark.asyncio
async def test_panel_is_off_by_default(async_engine: object) -> None:
    """It renders SQL shapes, so it must be opted into."""
    middleware = QuerySpyMiddleware(make_app(async_engine))
    sent = await call(middleware, path="/__queryspy__")
    # Falls through to the app, which responds with its own body.
    assert sent[-1]["body"] == b"ok"


@pytest.mark.asyncio
async def test_panel_serves_recorded_requests(async_engine: object) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine), panel=True)
    await call(middleware, path="/projects")

    sent = await call(middleware, path="/__queryspy__")
    headers = _headers(sent)
    assert headers[b"content-type"] == b"text/html; charset=utf-8"
    assert headers[b"cache-control"] == b"no-store"

    page = sent[-1]["body"].decode()
    assert "<!doctype html>" in page
    assert "GET /projects" in page
    assert "lazy_load" in page
    assert "selectinload(User.addresses)" in page
    # Self-contained: nothing is fetched from anywhere.
    assert "http://" not in page
    assert "https://" not in page


@pytest.mark.asyncio
async def test_panel_requests_are_not_themselves_recorded(async_engine: object) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine), panel=True)
    await call(middleware, path="/projects")
    await call(middleware, path="/__queryspy__")

    page = (await call(middleware, path="/__queryspy__"))[-1]["body"].decode()
    assert page.count("__queryspy__") == 0
    assert "1 request" in page


@pytest.mark.asyncio
async def test_panel_history_is_bounded(async_engine: object) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine, limit=1), panel=True, history=2)
    for _ in range(5):
        await call(middleware)

    assert len(middleware.history) == 2
    page = (await call(middleware, path="/__queryspy__"))[-1]["body"].decode()
    assert "2 requests" in page


@pytest.mark.asyncio
async def test_panel_path_is_configurable(async_engine: object) -> None:
    middleware = QuerySpyMiddleware(make_app(async_engine), panel=True, panel_path="/_qs")
    page = (await call(middleware, path="/_qs"))[-1]["body"].decode()
    assert "queryspy" in page
