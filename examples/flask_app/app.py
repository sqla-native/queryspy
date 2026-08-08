"""A Flask service with the same deliberate N+1, through WSGI.

The point of this example is that queryspy works on the sync stack too - the
half of nplusone's audience that the ASGI middleware does not reach.
"""

from __future__ import annotations

from flask import Flask, jsonify
from sqlalchemy import ForeignKey, String, create_engine, select
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


# `on_report` is here so the tests can assert on what the middleware saw; in a
# real app you would leave it out and read the log, or open the panel.
REPORTS: list[RequestReport] = []
app.wsgi_app = QuerySpyMiddleware(  # type: ignore[method-assign]
    app.wsgi_app,
    budget=5,
    on_report=REPORTS.append,
    panel=True,
)
