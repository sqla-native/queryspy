"""A minimal FastAPI service with one deliberate N+1.

`list_projects` is the bug; `list_projects_fixed` is the same endpoint written
correctly. The test suite next door catches the first and passes the second.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload
from sqlalchemy.pool import StaticPool


class Base(AsyncAttrs, DeclarativeBase):
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


engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
Sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with Sessionmaker() as session:
        yield session


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Sessionmaker() as session:
        session.add_all(
            [
                Project(name=f"project-{n}", tasks=[Task(title=f"task-{n}-{i}") for i in range(3)])
                for n in range(3)
            ]
        )
        await session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await seed()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/projects")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict[str, object]]:
    """One query for the projects, then one more per project. The bug."""
    projects = (await session.scalars(select(Project))).all()
    return [
        {
            "name": project.name,
            "tasks": [task.title for task in await project.awaitable_attrs.tasks],
        }
        for project in projects
    ]


@app.get("/projects-fixed")
async def list_projects_fixed(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """Two queries, no matter how many projects there are."""
    projects = (await session.scalars(select(Project).options(selectinload(Project.tasks)))).all()
    return [
        {"name": project.name, "tasks": [task.title for task in project.tasks]}
        for project in projects
    ]
