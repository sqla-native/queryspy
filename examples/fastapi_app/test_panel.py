"""The debug panel, served by the real app.

Validates that what you would actually open in a browser reflects the requests
that happened, and that it leaks nothing it should not.
"""

from __future__ import annotations

import httpx
from app import app, seed


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_panel_reflects_real_traffic():
    await seed()
    async with await _client() as client:
        await client.get("/projects")
        await client.get("/projects-fixed")
        page = (await client.get("/__queryspy__")).text

    assert page.startswith("<!doctype html>")
    assert "GET /projects" in page
    assert "GET /projects-fixed" in page
    assert "lazy_load" in page
    assert "selectinload(Project.tasks)" in page
    # It names the line in app.py, the same attribution the logs give.
    assert "app.py:" in page


async def test_panel_never_renders_parameter_values():
    """queryspy only ever holds statement templates, so values cannot leak here."""
    await seed()
    async with await _client() as client:
        await client.get("/projects")
        page = (await client.get("/__queryspy__")).text

    # Placeholders survive; the seeded values never appear.
    assert ":param_1" in page or "?" in page
    assert "project-0" not in page
    assert "task-0-0" not in page


async def test_panel_is_self_contained():
    """No CDN, no external stylesheet - it has to work offline."""
    await seed()
    async with await _client() as client:
        page = (await client.get("/__queryspy__")).text

    assert "<style>" in page
    assert "http://" not in page
    assert "https://" not in page
