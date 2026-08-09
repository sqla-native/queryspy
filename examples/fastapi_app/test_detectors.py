"""All three detectors, end to end through real HTTP.

The other example files exercise `lazy_load` only. These two shapes are the ones
that distinguish queryspy from what came before, so they get the same real-stack
treatment rather than living in unit tests alone.
"""

from __future__ import annotations

import httpx

from fastapi_app.app import REPORTS, app, seed


async def _get(path: str):
    await seed()
    REPORTS.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get(path)
    return REPORTS[-1]


async def test_repeated_statement_catches_a_fetch_by_id_loop():
    """The shape no relationship-load hook can see."""
    report = await _get("/projects-by-id")

    assert [f.kind for f in report.findings] == ["repeated_statement"]
    finding = report.findings[0]
    assert finding.count == 3
    assert finding.frame.function == "list_projects_by_id"
    assert "in_(ids)" in _hint(finding)


async def test_column_load_catches_a_deferred_column():
    report = await _get("/tasks-verbose")

    kinds = [f.kind for f in report.findings]
    assert "column_load" in kinds
    column = next(f for f in report.findings if f.kind == "column_load")
    assert column.entity == "Task"
    assert "undefer" in _hint(column)


async def test_the_three_detectors_do_not_collide():
    """Each shape produces its own kind, not a pile of repeated_statement."""
    lazy = await _get("/projects")
    repeated = await _get("/projects-by-id")

    assert [f.kind for f in lazy.findings] == ["lazy_load"]
    assert [f.kind for f in repeated.findings] == ["repeated_statement"]


def _hint(finding) -> str:
    from queryspy._hints import hint_for

    return hint_for(finding)
