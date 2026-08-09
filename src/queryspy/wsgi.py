"""WSGI middleware: the same reporting, for Flask and anything else WSGI.

::

    from queryspy.wsgi import QuerySpyMiddleware

    app.wsgi_app = QuerySpyMiddleware(app.wsgi_app, budget=10)

No framework dependency and no new packages - it speaks the protocol directly.

The one thing that is genuinely harder than ASGI here: a WSGI application
returns an *iterable*, and for a streaming response the queries keep coming
after ``__call__`` has already returned. Finalising at that point would report a
fraction of the truth. So the window stays open and the returned iterable is
wrapped; the report is emitted when that iterable is exhausted or closed,
whichever happens first.

Concurrency is fine: a threaded WSGI server gives each request its own thread,
and a fresh thread gets a fresh context, so the recorder's context variable
isolates requests exactly as it does for ASGI tasks.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, MutableMapping
from types import TracebackType
from typing import Any

from ._detect import DEFAULT_THRESHOLD
from ._middleware import PANEL_CONTENT_TYPE, MiddlewareCore
from ._recorder import Recorder, start, stop
from ._request import RequestReport

__all__ = ["QuerySpyMiddleware", "RequestReport"]

Environ = MutableMapping[str, Any]
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]
StartResponse = Callable[..., Any]
WSGIApp = Callable[[Environ, StartResponse], Iterable[bytes]]


class _ClosingIterable:
    """Passes the response body through, then finalises exactly once.

    A WSGI server is required to call ``close()`` when it is done, but finishing
    on exhaustion as well costs nothing and means a server that skips it still
    produces a report. The guard makes the double call harmless.
    """

    def __init__(self, wrapped: Iterable[bytes], finish: Callable[[], None]) -> None:
        self._wrapped = wrapped
        self._finish = finish

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._wrapped
        finally:
            self._finish()

    def close(self) -> None:
        try:
            closer = getattr(self._wrapped, "close", None)
            if closer is not None:
                closer()
        finally:
            self._finish()


class QuerySpyMiddleware(MiddlewareCore):
    """Report the query behaviour of every request.

    ::

        app.wsgi_app = QuerySpyMiddleware(app.wsgi_app, panel=True)

    Same options as the ASGI middleware.
    """

    def __init__(
        self,
        app: WSGIApp,
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

    def __call__(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        if self.wants_panel(path):
            return self._serve_panel(start_response)

        method = str(environ.get("REQUEST_METHOD", "?"))
        started = time.perf_counter()
        recorder = Recorder(capture_stacks=self.capture_stacks)
        start(recorder)
        done = False

        def finish() -> None:
            nonlocal done
            if done:
                return
            done = True
            stop(recorder)
            self.report(method, path, recorder, started)

        responder = self._wrap_start_response(start_response, recorder)
        try:
            result = self.app(environ, responder)
        except BaseException:
            # The app blew up before returning a body; there is no iterable to
            # wait on, and this is exactly the request worth reporting.
            finish()
            raise
        return _ClosingIterable(result, finish)

    def _serve_panel(self, start_response: StartResponse) -> Iterable[bytes]:
        payload = self.panel_payload()
        start_response(
            "200 OK",
            [
                ("Content-Type", PANEL_CONTENT_TYPE),
                ("Content-Length", str(len(payload))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [payload]

    def _wrap_start_response(
        self, start_response: StartResponse, recorder: Recorder
    ) -> StartResponse:
        if not self.add_headers:
            return start_response

        def wrapper(
            status: str,
            headers: list[tuple[str, str]],
            exc_info: ExcInfo | None = None,
        ) -> Any:
            combined = list(headers) + self.counter_headers(recorder)
            # Only pass exc_info through when there is some: the third argument
            # is optional in the spec and not every implementation accepts None.
            if exc_info is not None:
                return start_response(status, combined, exc_info)
            return start_response(status, combined)

        return wrapper
