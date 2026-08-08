"""The ASGI middleware, driven through real HTTP against the real app.

These are validation, not illustration: if the middleware stopped counting, or
stopped isolating concurrent requests, these fail.
"""

from __future__ import annotations

import asyncio

import httpx

from fastapi_app.app import REPORTS, app, seed


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_middleware_counts_and_flags_the_bad_endpoint():
    await seed()
    REPORTS.clear()
    async with await _client() as client:
        response = await client.get("/projects")

    assert response.status_code == 200
    assert response.headers["x-queryspy-queries"] == "4"
    assert response.headers["x-queryspy-findings"] == "1"

    report = REPORTS[-1]
    assert report.path == "/projects"
    assert [f.kind for f in report.findings] == ["lazy_load"]
    assert report.findings[0].label == "Project.tasks"
    # The whole point: it names the line in app.py, not somewhere in SQLAlchemy.
    assert report.findings[0].frame.filename.endswith("app.py")


async def test_middleware_stays_quiet_on_the_fixed_endpoint():
    await seed()
    REPORTS.clear()
    async with await _client() as client:
        response = await client.get("/projects-fixed")

    assert response.headers["x-queryspy-queries"] == "2"
    assert "x-queryspy-findings" not in response.headers
    assert REPORTS[-1].clean


async def test_concurrent_requests_do_not_contaminate_each_other():
    """Two endpoints with different query counts, hit at the same time.

    A module-global recorder would give both requests the union of their
    queries. Each report has to show only its own.
    """
    await seed()
    REPORTS.clear()
    async with await _client() as client:
        await asyncio.gather(
            *(client.get("/projects-fixed") for _ in range(3)),
            *(client.get("/projects") for _ in range(3)),
        )

    by_path: dict[str, set[int]] = {}
    for report in REPORTS:
        by_path.setdefault(report.path, set()).add(report.query_count)

    assert by_path["/projects-fixed"] == {2}
    assert by_path["/projects"] == {4}
