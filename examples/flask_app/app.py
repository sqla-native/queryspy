"""A Flask service with the same deliberate N+1, through WSGI.

The point of this example is that queryspy works on the sync stack too - the
half of nplusone's audience that the ASGI middleware does not reach.
"""

from __future__ import annotations

import time

from flask import Flask, jsonify, stream_with_context
from sqlalchemy import ForeignKey, String, create_engine, select, text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)
from sqlalchemy.pool import StaticPool

from queryspy.wsgi import QuerySpyMiddleware, RequestReport


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    tasks: Mapped[list[Task]] = relationship()


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))


engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

app = Flask(__name__)


def seed() -> None:
    """Reset to a known three-project state. Idempotent, so counts are stable."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Project(name=f"project-{n}", tasks=[Task(title=f"task-{n}-{i}") for i in range(3)])
                for n in range(3)
            ]
        )
        session.commit()


@app.get("/projects")
def list_projects() -> object:
    """One query for the projects, then one more per project. The bug."""
    with Session(engine) as session:
        projects = session.scalars(select(Project)).all()
        return jsonify([{"name": p.name, "tasks": [t.title for t in p.tasks]} for p in projects])


@app.get("/projects-fixed")
def list_projects_fixed() -> object:
    """Two queries, no matter how many projects there are."""
    with Session(engine) as session:
        projects = session.scalars(select(Project).options(selectinload(Project.tasks))).all()
        return jsonify([{"name": p.name, "tasks": [t.title for t in p.tasks]} for p in projects])


@app.get("/projects-stream")
def stream_projects():
    """A streaming response: the queries run *after* the view returns.

    This is the case that makes WSGI harder than ASGI. Finalising when the app
    returns would count one query; the window stays open until the iterable is
    exhausted, so all four are counted.
    """

    @stream_with_context
    def generate():
        with Session(engine) as session:
            for project in session.scalars(select(Project)).all():
                yield f"{project.name}:{len(project.tasks)}\n"

    return app.response_class(generate(), mimetype="text/plain")


@app.get("/slow")
def slow():
    """Few queries, one of which dominates.

    The headline is not always an N+1. Two queries where one takes most of the
    time is a slow query, and the timing figures are what say so.
    """
    with Session(engine) as session:
        session.scalars(select(Project)).all()
        session.execute(text("SELECT 1")).all()
        time.sleep(0)  # keep the shape obvious without making the suite slow
        return jsonify({"ok": True})


# `on_report` is here so the tests can assert on what the middleware saw; in a
# real app you would leave it out and read the log, or open the panel.
REPORTS: list[RequestReport] = []
app.wsgi_app = QuerySpyMiddleware(  # type: ignore[method-assign]
    app.wsgi_app,
    budget=5,
    on_report=REPORTS.append,
    panel=True,
)
