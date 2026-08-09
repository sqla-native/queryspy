"""Shapes and inputs the library had never been pointed at.

Every other test file uses one small two-table schema and well-formed input.
That is not evidence of much: attribution walks a `PathRegistry`, entity naming
goes through `bind_mapper`, and baselines parse a file a human may have edited.
Each of those has shapes the happy path never produces.

Nothing here is exotic. Self-referential trees, inheritance, several engines in
one process and a hand-edited config file are all ordinary in real applications.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
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

from queryspy import Finding, record, render_findings
from queryspy._baseline import load as load_baseline
from queryspy._panel import render_panel
from queryspy.asgi import RequestReport


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = "country"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(2))


class Person(Base):
    __tablename__ = "person"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    homes: Mapped[list[Home]] = relationship()


class Home(Base):
    __tablename__ = "home"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"))
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id"))
    country: Mapped[Country] = relationship()


class Node(Base):
    """A self-referential tree - the shape that breaks naive path rendering."""

    __tablename__ = "node"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("node.id"), nullable=True)
    children: Mapped[list[Node]] = relationship(back_populates="parent")
    parent: Mapped[Node | None] = relationship(back_populates="children", remote_side=[id])


class Employee(Base):
    """Joined-table inheritance, so `bind_mapper` sees a polymorphic entity."""

    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(20))
    notes: Mapped[list[Note]] = relationship()
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_on": "kind",
        "polymorphic_identity": "employee",
    }


class Manager(Employee):
    __tablename__ = "manager"
    id: Mapped[int] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    budget: Mapped[int] = mapped_column()
    __mapper_args__: ClassVar[dict[str, Any]] = {"polymorphic_identity": "manager"}


class Note(Base):
    __tablename__ = "note"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id"))


@pytest.fixture
def db() -> Any:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        countries = [Country(code="pt"), Country(code="br")]
        session.add_all(countries)
        session.flush()
        for n in range(3):
            session.add(Person(name=f"p{n}", homes=[Home(country_id=countries[n % 2].id)]))
            session.add(Manager(name=f"m{n}", kind="manager", budget=n, notes=[Note()]))
        root = Node()
        session.add(root)
        session.flush()
        session.add_all([Node(parent_id=root.id) for _ in range(3)])
        session.commit()
    yield engine
    engine.dispose()


# --------------------------------------------------------- unusual mappings


def test_nested_relationship_path_is_rendered(db: Any) -> None:
    """Two levels deep: the path has four elements, not two."""
    with Session(db) as session, record() as spy:
        people = session.scalars(select(Person).options(selectinload(Person.homes))).all()
        for person in people:
            for home in person.homes:
                assert home.country is not None

    findings = spy.findings()
    assert [f.kind for f in findings] == ["lazy_load"]
    assert findings[0].label == "Person.homes.Home.country"
    assert findings[0].uselist is False


def test_self_referential_relationship(db: Any) -> None:
    with Session(db) as session, record() as spy:
        for node in session.scalars(select(Node)).all():
            list(node.children)

    findings = spy.findings()
    assert [f.kind for f in findings] == ["lazy_load"]
    assert findings[0].label == "Node.children"
    assert findings[0].uselist is True


def test_polymorphic_finding_names_the_declaring_mapper(db: Any) -> None:
    """Querying `Manager`, the finding says `Employee.notes`. That is correct.

    The relationship is declared on the base, so that is where the loader path
    points - and `selectinload(Employee.notes)` is the option that fixes it,
    which is what the hint has to be pasteable as. Naming `Manager` would read
    more naturally and suggest an attribute that is only an inherited alias.
    """
    with Session(db) as session, record() as spy:
        for manager in session.scalars(select(Manager)).all():
            list(manager.notes)

    findings = spy.findings()
    assert [f.kind for f in findings] == ["lazy_load"]
    assert findings[0].label == "Employee.notes"
    assert "selectinload(Employee.notes)" in render_findings(findings)


def test_joinedload_on_a_nested_path_stays_silent(db: Any) -> None:
    with Session(db) as session, record() as spy:
        people = (
            session.scalars(
                select(Person).options(selectinload(Person.homes).joinedload(Home.country))
            )
            .unique()
            .all()
        )
        for person in people:
            for home in person.homes:
                assert home.country is not None

    assert spy.findings() == []


# ------------------------------------------------------- more than one thing


def test_two_engines_in_one_window(db: Any) -> None:
    other = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(other)
    try:
        with record() as spy:
            with Session(db) as session:
                session.scalars(select(Person)).all()
            with Session(other) as session:
                session.scalars(select(Person)).all()

        assert spy.query_count == 2
        assert len(spy.orm_records) == 2
    finally:
        other.dispose()


def test_core_level_execution_is_counted_but_not_an_orm_record(db: Any) -> None:
    """A Core connection never touches the ORM hook, but it is still a query."""
    from sqlalchemy import text

    with record() as spy, db.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert spy.query_count == 1
    assert spy.orm_records == []
    assert spy.findings() == []


def test_an_empty_window(db: Any) -> None:
    with record() as spy:
        pass

    assert spy.query_count == 0
    assert spy.findings() == []
    assert spy.slowest is None
    assert spy.db_duration_ms == 0.0


# ------------------------------------------------------------------ tuning


def test_threshold_of_one_flags_a_single_query(db: Any) -> None:
    """Legal, and someone will try it. It must not crash or misgroup."""
    with Session(db) as session, record() as spy:
        session.scalars(select(Person)).all()

    assert [f.count for f in spy.findings(threshold=1)] == [1]


# ------------------------------------------------------- hand-edited input


def test_a_corrupt_baseline_file_fails_loudly(tmp_path: Path) -> None:
    """It is a committed file a human can break; the error must say so."""
    path = tmp_path / "baseline.json"
    path.write_text("{not json at all")

    with pytest.raises(json.JSONDecodeError):
        load_baseline(path)


def test_a_baseline_without_entries_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"tool": "queryspy", "version": "0.4.0"}')

    assert load_baseline(path) == set()


def test_a_baseline_entry_without_a_location_loads(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"entries": [{"kind": "lazy_load", "label": "A.b"}]}))

    entry = next(iter(load_baseline(path)))
    assert entry.file is None
    assert entry.function is None


# ------------------------------------------------------------------ output


def _finding(index: int) -> Finding:
    return Finding(
        kind="lazy_load",
        label=f"Model{index}.rel",
        count=index + 2,
        sql="SELECT " + "x" * 400,
        frame=None,
        entity=f"Model{index}",
        uselist=True,
    )


def test_the_panel_survives_many_findings() -> None:
    report = RequestReport(
        method="GET",
        path="/heavy",
        query_count=500,
        findings=[_finding(i) for i in range(50)],
        duration_ms=1.0,
    )
    page = render_panel([report], version="0.4.0")

    assert page.count("selectinload") == 50
    assert "500" in page


def test_rendering_truncates_long_sql() -> None:
    rendered = render_findings([_finding(0)])
    sql_line = next(line for line in rendered.splitlines() if line.strip().startswith("SELECT"))
    assert sql_line.strip().endswith("...")
