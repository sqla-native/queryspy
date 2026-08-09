"""ASGI middleware: a per-request query panel for FastAPI, Starlette, Litestar.

Pure ASGI3 with no framework dependency and no new packages - it speaks the
protocol directly, so it mounts the same way on any ASGI app.

Requests are isolated from each other by the recorder's context variable, so
this stays correct under concurrency. See `_recorder._active`.

Intended for development and staging. It records every request, and stack
capture is not free; `capture_stacks=False` cuts most of the cost if you want
counts only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ._detect import DEFAULT_THRESHOLD
from ._middleware import PANEL_CONTENT_TYPE, MiddlewareCore
from ._recorder import Recorder
from ._request import RequestReport
from .api import record

__all__ = ["QuerySpyMiddleware", "RequestReport"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class QuerySpyMiddleware(MiddlewareCore):
    """Report the query behaviour of every request.

    ::

        app.add_middleware(QuerySpyMiddleware, budget=10)

    ``budget`` warns when a request exceeds that many statements. ``on_report``
    receives every :class:`RequestReport` if you would rather route them
    somewhere other than the logger. ``panel=True`` serves the debug panel at
    ``panel_path``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        threshold: int = DEFAULT_THRESHOLD,
        budget: int | None = None,
        capture_stacks: bool = True,
        add_headers: bool = True,
        logger: logging.Logger | None = None,
        on_report: Callable[[RequestReport], None] | None = None,
        panel: bool = False,
        panel_path: str = "/__queryspy__",
        history: int = 50,
    ) -> None:
        super().__init__(
            threshold=threshold,
            budget=budget,
            capture_stacks=capture_stacks,
            add_headers=add_headers,
            logger=logger,
            on_report=on_report,
            panel=panel,
            panel_path=panel_path,
            history=history,
        )
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self.wants_panel(str(scope.get("path", ""))):
            await self._serve_panel(send)
            return

        started = time.perf_counter()
        with record(capture_stacks=self.capture_stacks) as recorder:
            wrapped = self._wrap_send(send, recorder) if self.add_headers else send
            try:
                await self.app(scope, receive, wrapped)
            finally:
                # Report inside the `finally`, not after the `with`: a request
                # that raised is exactly the one whose queries you want to see,
                # and the exception would otherwise skip the report entirely.
                # `report` never raises, so it can never mask that exception.
                self.report(
                    str(scope.get("method", "?")),
                    str(scope.get("path", "?")),
                    recorder,
                    started,
                )

    async def _serve_panel(self, send: Send) -> None:
        payload = self.panel_payload()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", PANEL_CONTENT_TYPE.encode()),
                    (b"content-length", str(len(payload)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    def _wrap_send(self, send: Send, recorder: Recorder) -> Send:
        async def wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    (name.encode(), value.encode())
                    for name, value in self.counter_headers(recorder)
                )
                message = {**message, "headers": headers}
            await send(message)

        return wrapper
