"""App-frame attribution, including the greenlet fallback that async needs."""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from queryspy import record
from queryspy._frames import (
    _greenlet_frames,
    _is_library_frame,
    _roots,
    _walk,
    capture_app_frame,
)

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
    """Nothing attributable on the stack and no greenlet chain -> no frame.

    The depth cap is the lever rather than the root list, because what sits
    above pytest on the stack depends on how it was invoked: `python -m pytest`
    tops out in `<frozen runpy>` (filtered as a pseudo-file), while the `pytest`
    console script tops out in `bin/pytest`, which is under no sysconfig root
    and so reads as application code. Capping the walk makes the assertion hold
    either way.
    """
    monkeypatch.setattr("queryspy._frames._MAX_DEPTH", 0)
    assert capture_app_frame() is None


@pytest.mark.parametrize("filename", ["<frozen runpy>", "<string>", "<stdin>"])
def test_pseudo_filenames_are_never_app_code(filename: str) -> None:
    """These are not source lines anyone can open, whatever the root list says."""
    assert _is_library_frame(filename, ())


def test_real_paths_outside_the_roots_are_app_code() -> None:
    assert not _is_library_frame("/srv/app/services/users.py", ("/usr/lib/python3.12",))


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
