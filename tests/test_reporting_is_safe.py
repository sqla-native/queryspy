"""Reporting must never break the request it is observing.

The middleware reports from a `finally` around the application call. Anything
that escapes there does two unacceptable things: it fails a request that was
otherwise fine, and it *replaces* the application's own exception with one from
the diagnostics tool - destroying the traceback the developer actually needed.

The pytest plugin has always had this property ("a failing test body always
wins"). These pin it for the middleware too.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from queryspy._recorder import listeners_installed
from queryspy.asgi import QuerySpyMiddleware as ASGIMiddleware
from queryspy.wsgi import QuerySpyMiddleware as WSGIMiddleware

from .conftest import User


def boom(report: object) -> None:
    raise RuntimeError("callback exploded")


class BrokenLogger(logging.Logger):
    def warning(self, *args: Any, **kwargs: Any) -> None:
        raise OSError("logging handler is misconfigured")

    def debug(self, *args: Any, **kwargs: Any) -> None:
        raise OSError("logging handler is misconfigured")

    def exception(self, *args: Any, **kwargs: Any) -> None:
        raise OSError("logging handler is misconfigured")


# ------------------------------------------------------------------- ASGI


async def _drive(middleware: Any, path: str = "/x") -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await middleware({"type": "http", "method": "GET", "path": path, "headers": []}, receive, send)


def _asgi_app(engine: object, *, fail: bool = False) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        with Session(engine) as session:  # type: ignore[arg-type]
            session.scalars(select(User)).all()
        if fail:
            raise ValueError("the real application error")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


@pytest.mark.asyncio
async def test_asgi_a_broken_callback_does_not_break_a_healthy_request(engine: object) -> None:
    await _drive(ASGIMiddleware(_asgi_app(engine), on_report=boom))
    assert not listeners_installed()


@pytest.mark.asyncio
async def test_asgi_a_broken_callback_does_not_mask_the_app_exception(engine: object) -> None:
    """The one that actually costs a developer their afternoon."""
    middleware = ASGIMiddleware(_asgi_app(engine, fail=True), on_report=boom)

    with pytest.raises(ValueError, match="the real application error"):
        await _drive(middleware)


@pytest.mark.asyncio
async def test_asgi_the_reporting_failure_is_logged(
    engine: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Swallowed, but never silently - a broken callback must be discoverable."""
    with caplog.at_level(logging.ERROR, logger="queryspy"):
        await _drive(ASGIMiddleware(_asgi_app(engine), on_report=boom), path="/orders")

    assert "failed to report on GET /orders" in caplog.text
    assert "callback exploded" in caplog.text


@pytest.mark.asyncio
async def test_asgi_a_broken_logger_breaks_nothing(engine: object) -> None:
    """Last resort: if the logger is what failed, there is nowhere left to say so."""
    middleware = ASGIMiddleware(_asgi_app(engine), on_report=boom, logger=BrokenLogger("broken"))
    await _drive(middleware)
    assert not listeners_installed()


# ------------------------------------------------------------------- WSGI


def _wsgi_app(engine: object, *, fail: bool = False) -> Any:
    def app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        with Session(engine) as session:  # type: ignore[arg-type]
            session.scalars(select(User)).all()
        if fail:
            raise ValueError("the real application error")
        start_response("200 OK", [])
        return [b"ok"]

    return app


def _run(middleware: Any) -> None:
    result = middleware({"PATH_INFO": "/x", "REQUEST_METHOD": "GET"}, lambda *a: None)
    try:
        b"".join(result)
    finally:
        closer = getattr(result, "close", None)
        if closer is not None:
            closer()


def test_wsgi_a_broken_callback_does_not_break_a_healthy_request(engine: object) -> None:
    _run(WSGIMiddleware(_wsgi_app(engine), on_report=boom))
    assert not listeners_installed()


def test_wsgi_a_broken_callback_does_not_mask_the_app_exception(engine: object) -> None:
    middleware = WSGIMiddleware(_wsgi_app(engine, fail=True), on_report=boom)

    with pytest.raises(ValueError, match="the real application error"):
        _run(middleware)

    assert not listeners_installed()


def test_wsgi_a_broken_logger_breaks_nothing(engine: object) -> None:
    _run(WSGIMiddleware(_wsgi_app(engine), on_report=boom, logger=BrokenLogger("broken")))
    assert not listeners_installed()
