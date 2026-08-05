"""The debug panel.

A single self-contained HTML page: no CDN, no stylesheet, no script tag pointing
anywhere. Everything is inlined, because a debug panel that phones out is a
debug panel you cannot run offline or behind a firewall.

On what it shows: bind parameters are never captured anywhere in queryspy - a
recorded statement keeps its ``:param_1`` placeholders - so the panel renders
query *shapes*, never values. That is the property that makes it safe to look at
in a staging environment. It is still off by default, because SQL shape alone
tells a reader a lot about your schema.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from ._hints import hint_for

if TYPE_CHECKING:
    from ._detect import Finding
    from .asgi import RequestReport

__all__ = ["render_panel"]

_STYLE = """
:root { color-scheme: light dark; --fg:#16202b; --bg:#fff; --muted:#5b6b7f;
  --line:#dde3ea; --raised:#f6f8fa; --bad:#b3261e; --ok:#1f7d70; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6ecf3; --bg:#0f1620;
  --muted:#9aa9bb; --line:#263243; --raised:#161f2b; --bad:#f2837b; --ok:#66d3c1; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--fg); line-height:1.5;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif }
header { padding:1.25rem 1.5rem; border-bottom:1px solid var(--line);
  display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap }
h1 { margin:0; font-size:1.1rem; letter-spacing:-.01em }
.muted { color:var(--muted) }
main { padding:1.5rem; max-width:70rem; margin:0 auto }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums }
th,td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top }
th { font-size:.75rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted) }
td.num,th.num { text-align:right }
tr.bad td:first-child { border-left:3px solid var(--bad); padding-left:.45rem }
details { margin:.4rem 0 0 }
summary { cursor:pointer; color:var(--muted) }
.finding { margin:.6rem 0 0; padding:.6rem .75rem; background:var(--raised);
  border:1px solid var(--line); border-radius:.4rem }
.finding b { color:var(--bad) }
.where { color:var(--muted); font-size:.85rem }
code,pre { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.82rem }
pre { margin:.4rem 0; padding:.5rem; background:var(--bg); border:1px solid var(--line);
  border-radius:.3rem; overflow-x:auto }
.fix { color:var(--ok) }
.empty { padding:3rem 1rem; text-align:center; color:var(--muted) }
footer { padding:1rem 1.5rem; border-top:1px solid var(--line); color:var(--muted);
  font-size:.8rem }
"""


def _finding_html(finding: Finding) -> str:
    where = ""
    if finding.frame is not None:
        where = f'<div class="where">{escape(str(finding.frame))}</div>'
    return (
        '<div class="finding">'
        f"<b>{escape(finding.kind)}</b> &middot; {escape(finding.label)} "
        f"&times;{finding.count}"
        f"{where}"
        f"<pre>{escape(finding.sql)}</pre>"
        f'<div class="fix">fix: {escape(hint_for(finding))}</div>'
        "</div>"
    )


def _row(index: int, report: RequestReport) -> str:
    detail = ""
    if report.findings:
        body = "".join(_finding_html(finding) for finding in report.findings)
        detail = (
            f"<details><summary>{len(report.findings)} finding"
            f"{'' if len(report.findings) == 1 else 's'}</summary>{body}</details>"
        )
    slowest = ""
    if report.slowest is not None:
        slowest = (
            f"<details><summary>slowest {report.slowest.duration_ms:.1f}ms</summary>"
            f"<pre>{escape(report.slowest.sql)}</pre></details>"
        )
    classes = ' class="bad"' if report.findings else ""
    return (
        f"<tr{classes}>"
        f"<td class='num muted'>{index}</td>"
        f"<td><code>{escape(report.method)} {escape(report.path)}</code>{detail}{slowest}</td>"
        f"<td class='num'>{report.query_count}</td>"
        f"<td class='num'>{report.db_duration_ms:.1f}</td>"
        f"<td class='num'>{report.duration_ms:.1f}</td>"
        f"</tr>"
    )


def render_panel(reports: list[RequestReport], *, version: str) -> str:
    """Render the whole panel. ``reports`` is newest-last."""
    ordered = list(reversed(reports))
    if ordered:
        rows = "".join(_row(len(ordered) - i, r) for i, r in enumerate(ordered))
        body = (
            "<table><thead><tr>"
            "<th class='num'>#</th><th>Request</th>"
            "<th class='num'>Queries</th><th class='num'>DB ms</th><th class='num'>Total ms</th>"
            "</tr></thead><tbody>" + rows + "</tbody></table>"
        )
    else:
        body = '<p class="empty">No requests recorded yet.</p>'

    flagged = sum(1 for r in reports if r.findings)
    queries = sum(r.query_count for r in reports)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex'>"
        "<title>queryspy</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<header><h1>queryspy</h1>"
        f"<span class='muted'>{len(reports)} request"
        f"{'' if len(reports) == 1 else 's'} &middot; {queries} quer"
        f"{'y' if queries == 1 else 'ies'} &middot; {flagged} flagged</span>"
        "</header>"
        f"<main>{body}</main>"
        "<footer>Bind parameters are never captured &mdash; these are query "
        f"shapes, not values. queryspy {escape(version)}</footer>"
        "</body></html>"
    )
