"""The end-to-end demonstration.

`test_list_projects_has_no_n_plus_one` is expected to FAIL until you apply the
fix queryspy prints. Everything else passes.
"""

from __future__ import annotations

import pytest
from app import Sessionmaker, list_projects, list_projects_fixed, seed

from queryspy import assert_max_queries, no_n_plus_one


@pytest.fixture(autouse=True)
async def database():
    await seed()


@pytest.mark.asyncio
async def test_list_projects_fixed_is_two_queries_regardless_of_size():
    async with Sessionmaker() as session:
        with assert_max_queries(2), no_n_plus_one():
            payload = await list_projects_fixed(session)
    assert len(payload) == 3


@pytest.mark.asyncio
@pytest.mark.xfail(reason="the deliberate N+1 this example exists to demonstrate", strict=True)
async def test_list_projects_has_no_n_plus_one():
    async with Sessionmaker() as session:
        with no_n_plus_one():
            await list_projects(session)
