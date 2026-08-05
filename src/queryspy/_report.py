"""Rendering findings for humans.

Output is tuned for a pytest failure message: the headline first, then where in
*your* code it came from, then the SQL, then the fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._detect import Finding
from ._hints import hint_for

if TYPE_CHECKING:
    from ._recorder import SlowStatement

__all__ = ["render_finding", "render_findings", "render_timing"]

_MAX_SQL = 120

_HEADLINES = {
    "lazy_load": "N+1 detected: {count} queries for {label} (lazy load)",
    "column_load": "N+1 detected: {count} column loads for {label}",
    "repeated_statement": "{count} identical queries",
}


def _truncate(sql: str) -> str:
    if len(sql) <= _MAX_SQL:
        return sql
    return sql[: _MAX_SQL - 3] + "..."


def render_finding(finding: Finding) -> str:
    """Render one finding as an indented, multi-line block."""
    lines = [_HEADLINES[finding.kind].format(count=finding.count, label=finding.label)]
    if finding.frame is not None:
        lines.append(f"  triggered from {finding.frame}")
    lines.append(f"  {_truncate(finding.sql)}")
    lines.append(f"  fix: {hint_for(finding)}")
    return "\n".join(lines)


def render_findings(findings: list[Finding]) -> str:
    """Render every finding, worst first."""
    return "\n\n".join(render_finding(finding) for finding in findings)


def render_timing(db_duration_ms: float, slowest: SlowStatement | None) -> str:
    """One line of timing, naming the slowest statement when it dominates.

    Timing is reported per window, never per finding: attributing milliseconds
    to a finding would mean correlating the ORM and cursor layers, which is the
    one thing the recorder refuses to do.
    """
    line = f"{db_duration_ms:.1f}ms in the database"
    if slowest is None:
        return line
    share = slowest.duration_ms / db_duration_ms if db_duration_ms else 0.0
    if share < 0.5:
        return line
    return (
        f"{line}, {slowest.duration_ms:.1f}ms of it in one statement:\n  {_truncate(slowest.sql)}"
    )
