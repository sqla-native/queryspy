"""The WSGI middleware against a real Flask app."""

from __future__ import annotations

import pytest

from flask_app.app import REPORTS, app, seed


@pytest.fixture(autouse=True)
def database():
    seed()
    REPORTS.clear()


@pytest.fixture
def client():
    return app.test_client()


def test_the_bug_is_detected(client):
    response = client.get("/projects")
    # Consume the body. A real WSGI server always does, and the report is
    # emitted when the response iterable is exhausted or closed - which is what
    # makes streaming responses count correctly. Werkzeug's test client is lazy,
    # so a test that only inspects headers would see no report yet.
    response.get_data()

    assert response.status_code == 200
    assert response.headers["x-queryspy-queries"] == "4"
    assert response.headers["x-queryspy-findings"] == "1"

    report = REPORTS[-1]
    assert [f.kind for f in report.findings] == ["lazy_load"]
    assert report.findings[0].label == "Project.tasks"
    # Attribution reaches into the Flask view, not into SQLAlchemy.
    assert report.findings[0].frame.function == "list_projects"


def test_the_fixed_endpoint_is_clean(client):
    response = client.get("/projects-fixed")
    response.get_data()

    assert response.headers["x-queryspy-queries"] == "2"
    assert "x-queryspy-findings" not in response.headers
    assert REPORTS[-1].clean


def test_panel_is_served(client):
    client.get("/projects").get_data()
    page = client.get("/__queryspy__").get_data(as_text=True)

    assert page.startswith("<!doctype html>")
    assert "GET /projects" in page
    assert "selectinload(Project.tasks)" in page


def test_ignore_suppresses_a_deliberate_loop(client):
    """The in-app escape hatch, exercised through a real request."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    import queryspy
    from flask_app.app import Project, engine

    with queryspy.record() as spy, queryspy.ignore(), Session(engine) as session:
        for project in session.scalars(select(Project)).all():
            list(project.tasks)

    assert spy.findings() == []
    assert spy.query_count == 4  # still counted
