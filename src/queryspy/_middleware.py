"""Everything the ASGI and WSGI middleware share.

Which is nearly all of it: the options, building a report from a recorder,
deciding what to log, the panel payload, and the counter headers. What differs
between the two is only protocol plumbing - how you get a method and a path, how
headers are written, and when a request is actually finished.

Keeping the shared half here means adding a third protocol later is a small
file, and means a fix to the reporting rules cannot land in one middleware and
miss the other.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress

from . import __version__
from ._detect import DEFAULT_THRESHOLD
from ._panel import render_panel
from ._recorder import Recorder
from ._request import RequestReport

__all__ = ["MiddlewareCore"]

_LOGGER = logging.getLogger("queryspy")

PANEL_CONTENT_TYPE = "text/html; charset=utf-8"


class MiddlewareCore:
    """Shared configuration and reporting for the protocol middlewares."""

    def __init__(
        self,
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
        self.threshold = threshold
        self.budget = budget
        self.capture_stacks = capture_stacks
        self.add_headers = add_headers
        self.logger = logger or _LOGGER
        self.on_report = on_report
        self.panel = panel
        self.panel_path = panel_path
        # Bounded on purpose: an unbounded buffer in a long-running process is a
        # leak, and old requests stop being interesting quickly.
        self.history: deque[RequestReport] = deque(maxlen=history)

    def wants_panel(self, path: str) -> bool:
        return self.panel and path == self.panel_path

    def panel_payload(self) -> bytes:
        return render_panel(list(self.history), version=__version__).encode()

    def build(self, method: str, path: str, recorder: Recorder, started: float) -> RequestReport:
        return RequestReport(
            method=method,
            path=path,
            query_count=recorder.query_count,
            findings=recorder.findings(threshold=self.threshold),
            duration_ms=(time.perf_counter() - started) * 1000,
            db_duration_ms=recorder.db_duration_ms,
            slowest=recorder.slowest,
        )

    def counter_headers(self, recorder: Recorder) -> list[tuple[str, str]]:
        """Counters as of *now*.

        For a streaming response the headers go out before the body runs, so
        these are the numbers at that moment. The log line is emitted once the
        request is genuinely finished and is always the final figure.
        """
        headers = [("x-queryspy-queries", str(recorder.query_count))]
        findings = recorder.findings(threshold=self.threshold)
        if findings:
            headers.append(("x-queryspy-findings", str(len(findings))))
        return headers

    def report(self, method: str, path: str, recorder: Recorder, started: float) -> None:
        """Build and emit a report, never raising.

        This runs from a ``finally`` around the application call, so anything
        that escapes here does two unacceptable things: it fails a request that
        was otherwise healthy, and - worse - it *replaces* the application's own
        exception with one from the diagnostics tool, destroying the traceback
        the developer actually needed.

        A user-supplied ``on_report``, a misconfigured logging handler, or a bug
        in detection can all raise. None of them are the request's problem.
        """
        try:
            self.emit(self.build(method, path, recorder, started))
        except Exception:
            self._reporting_failed(method, path)

    def _reporting_failed(self, method: str, path: str) -> None:
        # The logger itself may be what broke. There is nowhere left to say so,
        # and saying nothing is still better than breaking the request.
        with suppress(Exception):
            self.logger.exception("queryspy: failed to report on %s %s", method, path)

    def emit(self, report: RequestReport) -> None:
        if self.panel:
            self.history.append(report)
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
