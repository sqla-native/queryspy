"""ASGI middleware: a per-request query panel for the terminal.

FastAPI, Starlette and Litestar have never had a Django-Debug-Toolbar
equivalent for SQL. This is the smallest useful version of one: every request
gets a query count and a findings report, attributed to your source lines.

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
from dataclasses import dataclass
from typing import Any

from ._detect import DEFAULT_THRESHOLD, Finding
from ._report import render_findings
from .api import record

__all__ = ["QuerySpyMiddleware", "RequestReport"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_LOGGER = logging.getLogger("queryspy")


@dataclass(frozen=True)
class RequestReport:
    """What one request did to the database."""

    method: str
    path: str
    query_count: int
    findings: list[Finding]
    duration_ms: float

    @property
    def clean(self) -> bool:
        return not self.findings

    def render(self) -> str:
        headline = (
            f"{self.method} {self.path} - {self.query_count} "
            f"quer{'y' if self.query_count == 1 else 'ies'} in {self.duration_ms:.1f}ms"
        )
        if not self.findings:
            return headline
        return f"{headline}\n\n{render_findings(self.findings)}"


class QuerySpyMiddleware:
    """Report the query behaviour of every request.

    ::

        app.add_middleware(QuerySpyMiddleware, budget=10)

    ``budget`` warns when a request exceeds that many statements. ``on_report``
    receives every :class:`RequestReport` if you would rather route them
    somewhere other than the logger.
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
    ) -> None:
        self.app = app
        self.threshold = threshold
        self.budget = budget
        self.capture_stacks = capture_stacks
        self.add_headers = add_headers
        self.logger = logger or _LOGGER
        self.on_report = on_report

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        with record(capture_stacks=self.capture_stacks) as recorder:
            wrapped = self._wrap_send(send, recorder) if self.add_headers else send
            try:
                await self.app(scope, receive, wrapped)
            finally:
                # Emit inside the `finally`, not after the `with`: a request that
                # raised is exactly the one whose queries you want to see, and
                # the exception would otherwise skip the report entirely.
                self._emit(self._build(scope, recorder, started))

    def _build(self, scope: Scope, recorder: Any, started: float) -> RequestReport:
        return RequestReport(
            method=str(scope.get("method", "?")),
            path=str(scope.get("path", "?")),
            query_count=recorder.query_count,
            findings=recorder.findings(threshold=self.threshold),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def _wrap_send(self, send: Send, recorder: Any) -> Send:
        """Attach counters to the response.

        Headers must go out with ``http.response.start``, which for a streaming
        response happens *before* the body runs - so the header reflects the
        count at that moment. The log line is emitted after the request
        completes and is always the final figure.
        """

        async def wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-queryspy-queries", str(recorder.query_count).encode()))
                findings = recorder.findings(threshold=self.threshold)
                if findings:
                    headers.append((b"x-queryspy-findings", str(len(findings)).encode()))
                message = {**message, "headers": headers}
            await send(message)

        return wrapper

    def _emit(self, report: RequestReport) -> None:
        if self.on_report is not None:
            self.on_report(report)

        over_budget = self.budget is not None and report.query_count > self.budget
        if report.findings:
            self.logger.warning("%s", report.render())
        elif over_budget:
            self.logger.warning(
                "%s %s - %d queries exceeds the budget of %d",
                report.method,
                report.path,
                report.query_count,
                self.budget,
            )
        else:
            self.logger.debug("%s", report.render())
