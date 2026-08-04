from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey, String, create_engine
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

pytest_plugins = ["pytester"]


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    bio: Mapped[str] = mapped_column(String(200), deferred=True)
    addresses: Mapped[list[Address]] = relationship(back_populates="user")


class Address(Base):
    __tablename__ = "address"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(50))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    user: Mapped[User] = relationship(back_populates="addresses")


class Team(Base):
    """A model whose relationship is eagerly loaded by configuration.

    The spike measured that this does *not* N+1 over a batch of parents, so it
    belongs in the false-positive suite, not the detection suite.
    """

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    members: Mapped[list[Member]] = relationship(lazy="selectin")


class Member(Base):
    __tablename__ = "member"

    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(String(50))
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))


def seed(session: Session) -> None:
    for n in range(3):
        session.add(
            User(
                name=f"u{n}",
                bio=f"bio{n}",
                addresses=[Address(email=f"{n}-{i}@example.com") for i in range(2)],
            )
        )
        session.add(Team(name=f"t{n}", members=[Member(handle=f"m{n}")]))
    session.commit()
    session.expunge_all()


@pytest.fixture
def engine() -> Iterator[object]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: object) -> Iterator[Session]:
    with Session(engine) as session:  # type: ignore[arg-type]
        yield session


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[object]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        await session.run_sync(seed)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine: object) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(async_engine) as session:  # type: ignore[arg-type]
        yield session
