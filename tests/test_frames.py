"""App-frame attribution, including the greenlet fallback that async needs."""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from queryspy import record
from queryspy._frames import _greenlet_frames, _roots, _walk, capture_app_frame

from .conftest import User


def test_walk_returns_the_calling_frame() -> None:
    found = capture_app_frame()
    assert found is not None
    assert found.filename.endswith("test_frames.py")
    assert found.function == "test_walk_returns_the_calling_frame"


def test_walk_handles_a_missing_frame() -> None:
    assert _walk(None, _roots()) is None


def test_walk_gives_up_past_the_depth_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("queryspy._frames._MAX_DEPTH", 0)
    assert _walk(sys._getframe(), _roots()) is None


def test_no_app_frame_anywhere_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With this file treated as library code, nothing is attributable."""
    monkeypatch.setattr("queryspy._frames._ROOTS", (*_roots(), str(__file__)[: -len("x.py")]))
    assert capture_app_frame() is None


def test_greenlet_frames_is_empty_on_the_main_greenlet() -> None:
    assert _greenlet_frames() == []


def test_greenlet_frames_without_greenlet_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sync-only install has no greenlet; the fallback must degrade quietly."""
    monkeypatch.setitem(sys.modules, "greenlet", None)
    assert _greenlet_frames() == []


@pytest.mark.asyncio
async def test_async_lazy_load_is_attributed_through_the_greenlet(
    async_session: AsyncSession,
) -> None:
    """Without the greenlet hop this finding would carry no source line at all."""
    with record() as spy:
        users = (await async_session.scalars(select(User))).all()
        for user in users:
            await user.awaitable_attrs.addresses

    frame = spy.findings()[0].frame
    assert frame is not None
    assert frame.filename.endswith("test_frames.py")
    assert frame.function == "test_async_lazy_load_is_attributed_through_the_greenlet"
