"""queryspy against real database servers.

Everything else in this suite runs on SQLite, which is a reasonable default and
a bad place to stop. SQLite is in-process, uses ``?`` parameters, and has its
own driver; Postgres and MySQL differ in parameter style, in the DBAPI, and -
for async - in the driver's greenlet interaction. Detection, attribution and
timing all sit close enough to those layers that "it works on SQLite" is not
evidence that it works anywhere else.

These skip unless the matching environment variable is set, so a clone runs
green with no Docker. To run them::

    docker compose up -d --wait
    ./scripts/test-integration.sh
    docker compose down -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import ForeignKey, String, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)

from queryspy import record


class Base(AsyncAttrs, DeclarativeBase):
    """Its own registry, so these tables never collide with the SQLite suite."""


class User(Base):
    __tablename__ = "queryspy_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    bio: Mapped[str] = mapped_column(String(200), deferred=True)
    addresses: Mapped[list[Address]] = relationship()


class Address(Base):
    __tablename__ = "queryspy_address"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(50))
    user_id: Mapped[int] = mapped_column(ForeignKey("queryspy_user.id"))


BACKENDS = [
    pytest.param(
        ("postgresql", "QUERYSPY_POSTGRES_URL", "QUERYSPY_POSTGRES_ASYNC_URL"),
        id="postgresql",
    ),
    pytest.param(
        ("mysql", "QUERYSPY_MYSQL_URL", "QUERYSPY_MYSQL_ASYNC_URL"),
        id="mysql",
    ),
]


def _url(variable: str) -> str:
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f"{variable} is not set")
    return value


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> tuple[str, str, str]:
    name, sync_var, async_var = request.param
    return name, _url(sync_var), async_var


@pytest.fixture
def engine(backend: tuple[str, str, str]) -> Iterator[Engine]:
    engine = create_engine(backend[1])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                User(name=f"u{n}", bio=f"bio{n}", addresses=[Address(email=f"{n}@x.com")])
                for n in range(3)
            ]
        )
        session.commit()
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


# ----------------------------------------------------------------- detection


def test_lazy_load_is_detected(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    findings = spy.findings()
    assert [(f.kind, f.label, f.count) for f in findings] == [("lazy_load", "User.addresses", 3)]
    assert spy.query_count == 4


def test_selectinload_stays_silent(session: Session) -> None:
    """The false-positive gate, on a real server.

    `is_relationship_load` is True here exactly as it is on SQLite; if the
    discriminator ever regressed to it, this is where a real backend would show
    the same cry-wolf failure.
    """
    with record() as spy:
        for user in session.scalars(select(User).options(selectinload(User.addresses))).all():
            list(user.addresses)

    assert spy.findings() == []
    assert spy.query_count == 2


def test_deferred_column_is_detected(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User)).all():
            assert user.bio

    assert [(f.kind, f.entity) for f in spy.findings()] == [("column_load", "User")]


def test_repeated_statement_is_detected(session: Session) -> None:
    with record() as spy:
        for user_id in (1, 2, 3):
            session.get(User, user_id)

    findings = spy.findings()
    assert [f.kind for f in findings] == ["repeated_statement"]
    assert findings[0].count == 3


def test_raw_text_is_counted_but_not_flagged(session: Session) -> None:
    with record() as spy:
        session.execute(text("SELECT 1"))

    assert spy.query_count == 1
    assert spy.findings() == []


# --------------------------------------------------------------- attribution


def test_attribution_reaches_this_file(session: Session) -> None:
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    frame = spy.findings()[0].frame
    assert frame is not None
    assert frame.filename.endswith("test_real_databases.py")
    assert frame.function == "test_attribution_reaches_this_file"


# -------------------------------------------------------------------- timing


def test_timing_is_measured(session: Session) -> None:
    """Real network round trips, unlike in-process SQLite."""
    with record() as spy:
        for user in session.scalars(select(User)).all():
            list(user.addresses)

    assert spy.db_duration_ms > 0
    assert spy.slowest is not None
    assert spy.slowest.duration_ms > 0


# ------------------------------------------------------------- grouping keys


def test_the_grouping_key_is_dialect_independent(session: Session) -> None:
    """What queryspy stores is the *default* compilation, not the backend's.

    `before_cursor_execute` sees backend-specific SQL - `%(id_1)s` on psycopg,
    `%s` on MySQL, `?` on SQLite - but the record keeps `str(statement)`, which
    always renders named `:param` placeholders. That is what keeps a finding's
    identity, and therefore a baseline entry, stable across backends.
    """
    with record() as spy:
        session.get(User, 1)

    stored = spy.orm_records[0].sql
    assert ":pk_1" in stored or ":param_1" in stored
    # Backend placeholder styles must not leak into the grouping key.
    assert "%(" not in stored
    assert "%s" not in stored
    assert "?" not in stored


# --------------------------------------------------------------------- async


@pytest.mark.asyncio
async def test_async_lazy_load_and_attribution(backend: tuple[str, str, str]) -> None:
    """A different async driver from aiosqlite, with its own greenlet path."""
    _name, sync_url, async_var = backend
    async_url = _url(async_var)

    sync_engine = create_engine(sync_url)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    with Session(sync_engine) as session:
        session.add_all(
            [User(name=f"u{n}", bio="b", addresses=[Address(email="a@x.com")]) for n in range(3)]
        )
        session.commit()

    engine = create_async_engine(async_url)
    try:
        async with AsyncSession(engine) as session:
            with record() as spy:
                users = (await session.scalars(select(User))).all()
                for user in users:
                    await user.awaitable_attrs.addresses

        findings = spy.findings()
        assert [(f.kind, f.label, f.count) for f in findings] == [
            ("lazy_load", "User.addresses", 3)
        ]
        # The greenlet-parent hop has to work on this driver too.
        frame = findings[0].frame
        assert frame is not None
        assert frame.function == "test_async_lazy_load_and_attribution"
    finally:
        await engine.dispose()
        Base.metadata.drop_all(sync_engine)
        sync_engine.dispose()
