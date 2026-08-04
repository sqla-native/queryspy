"""Rendering findings for humans.

Output is tuned for a pytest failure message: the headline first, then where in
*your* code it came from, then the SQL, then the fix.
"""

from __future__ import annotations

from ._detect import Finding
from ._hints import hint_for

__all__ = ["render_finding", "render_findings"]

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
