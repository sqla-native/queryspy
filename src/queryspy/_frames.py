"""App-frame attribution.

A query count is nearly worthless on its own; "which line of *your* code caused
this" is the whole value. We walk the live stack and return the first frame that
is neither SQLAlchemy's, nor ours, nor the standard library's.

Async needs a second step. SQLAlchemy runs an async lazy load inside a spawned
greenlet whose stack contains no application frames at all - only
``strategies.py``, ``session.py`` and friends. The caller's frames live on the
*parent* greenlet, so when the ordinary walk comes up empty we hop up the
greenlet chain and walk again. Without this, the single most common async N+1
(``await obj.awaitable_attrs.items`` in a loop) reports with no source line.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from types import FrameType

__all__ = ["AppFrame", "capture_app_frame"]

# Walking the whole stack on every query is the single largest cost in the hot
# path, so cap it. Real application stacks put the offending line well within
# this many frames of the SQLAlchemy call.
_MAX_DEPTH = 40

_INSTALL_PATH_KEYS = frozenset({"stdlib", "platstdlib", "purelib", "platlib"})

_ROOTS: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AppFrame:
    """A single source location in the user's own code."""

    filename: str
    lineno: int
    function: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno} in {self.function}()"


def _library_roots() -> tuple[str, ...]:
    """Directories whose frames are never the user's code."""
    import sqlalchemy

    import queryspy

    roots = [
        os.path.dirname(os.path.abspath(sqlalchemy.__file__)),
        os.path.dirname(os.path.abspath(queryspy.__file__)),
    ]
    # Intersecting the key sets rather than testing each key keeps this total:
    # an unusual sysconfig scheme drops out silently instead of taking a branch
    # that no test on a normal interpreter could ever cover.
    paths = sysconfig.get_paths()
    roots.extend(os.path.abspath(paths[key]) for key in _INSTALL_PATH_KEYS & paths.keys())
    return tuple(dict.fromkeys(roots))


def _roots() -> tuple[str, ...]:
    global _ROOTS
    if _ROOTS is None:
        _ROOTS = _library_roots()
    return _ROOTS


def _is_library_frame(filename: str, roots: tuple[str, ...]) -> bool:
    # "<frozen runpy>", "<string>", "<stdin>" and friends are pseudo-files, never
    # a source line anyone can go and open.
    if filename.startswith("<"):
        return True
    absolute = os.path.abspath(filename)
    return any(absolute.startswith(root) for root in roots)


def _walk(frame: FrameType | None, roots: tuple[str, ...]) -> AppFrame | None:
    """Return the first non-library frame at or above ``frame``."""
    depth = 0
    while frame is not None and depth < _MAX_DEPTH:
        filename = frame.f_code.co_filename
        if not _is_library_frame(filename, roots):
            return AppFrame(
                filename=filename,
                lineno=frame.f_lineno,
                function=frame.f_code.co_name,
            )
        frame = frame.f_back
        depth += 1
    return None


def _greenlet_frames() -> list[FrameType | None]:
    """Frames of the greenlets that spawned this one, nearest first.

    ``greenlet`` is imported lazily and optionally: it is not a dependency of
    this package, it ships with ``sqlalchemy[asyncio]``, and it is only ever
    present in exactly the situation this fallback exists for. Parent chains are
    acyclic and rooted at the main greenlet, so the walk always terminates.
    """
    try:
        import greenlet
    except ImportError:
        return []

    frames: list[FrameType | None] = []
    current = getattr(greenlet.getcurrent(), "parent", None)
    while current is not None:
        frames.append(getattr(current, "gr_frame", None))
        current = getattr(current, "parent", None)
    return frames


def capture_app_frame() -> AppFrame | None:
    """Return the nearest stack frame belonging to the caller's own code.

    The live stack comes first; the greenlet chain is the async fallback.
    Returns ``None`` when no such frame exists anywhere - for example when a
    query is issued directly from library code.
    """
    roots = _roots()
    for frame in (sys._getframe(1), *_greenlet_frames()):
        found = _walk(frame, roots)
        if found is not None:
            return found
    return None
