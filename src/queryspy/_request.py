"""What one request did to the database.

Its own module because both the ASGI and WSGI middleware produce it and the
panel consumes it; leaving it in either protocol module would make the other
import across for no reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._detect import Finding
from ._recorder import SlowStatement
from ._report import render_findings, render_timing

__all__ = ["RequestReport"]


@dataclass(frozen=True)
class RequestReport:
    """What one request did to the database."""

    method: str
    path: str
    query_count: int
    findings: list[Finding]
    duration_ms: float
    """Wall-clock time for the whole request."""
    db_duration_ms: float = 0.0
    """Time actually spent in the database driver."""
    slowest: SlowStatement | None = None

    @property
    def clean(self) -> bool:
        return not self.findings

    def render(self) -> str:
        headline = (
            f"{self.method} {self.path} - {self.query_count} "
            f"quer{'y' if self.query_count == 1 else 'ies'} in {self.duration_ms:.1f}ms "
            f"({render_timing(self.db_duration_ms, self.slowest)})"
        )
        if not self.findings:
            return headline
        return f"{headline}\n\n{render_findings(self.findings)}"
