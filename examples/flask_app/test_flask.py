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


def test_streaming_response_is_counted_in_full(client):
    """The hardest correctness problem in the WSGI middleware, end to end.

    Every query happens while the body is being consumed. If the window closed
    when the view returned, this would report one query and no finding.
    """
    response = client.get("/projects-stream")
    body = response.get_data(as_text=True)

    assert body.count("\n") == 3
    report = REPORTS[-1]
    assert report.query_count == 4
    assert [f.kind for f in report.findings] == ["lazy_load"]
    # Headers were written before the body ran, so they show the count then.
    assert response.headers["x-queryspy-queries"] == "0"


def test_timing_is_reported(client):
    response = client.get("/slow")
    response.get_data()

    report = REPORTS[-1]
    assert report.db_duration_ms > 0
    assert report.db_duration_ms <= report.duration_ms
    assert report.slowest is not None
    assert report.clean  # two queries, no N+1 - the story here is time, not shape


def test_concurrent_wsgi_requests_are_isolated():
    """Threads, which is how a real WSGI server serves concurrently."""
    import threading

    from flask_app.app import app as flask_app

    seed()
    REPORTS.clear()
    barrier = threading.Barrier(2)

    def hit(path: str) -> None:
        local = flask_app.test_client()
        barrier.wait()
        local.get(path).get_data()

    threads = [
        threading.Thread(target=hit, args=("/projects-fixed",)),
        threading.Thread(target=hit, args=("/projects",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    by_path = {r.path: r.query_count for r in REPORTS}
    assert by_path["/projects-fixed"] == 2
    assert by_path["/projects"] == 4
