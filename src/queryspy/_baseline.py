"""Baseline (ratchet) mode: fail on new findings, tolerate known ones.

Turning the gate on for the first time is the hard part of adopting any linter.
A suite that lights up in twenty places gets the gate switched back off, and
then nothing improves. A baseline records what is already there so the gate can
fail on *regressions* from day one, while the existing list is worked down.

**Identity deliberately excludes the line number and the count.** A finding is
the same finding after an unrelated edit shifts it down forty lines, or after a
fixture grows and turns eleven queries into fourteen. Keying on either would
make baselines expire constantly, which is the failure mode that makes people
abandon them.

**It also excludes which test found it.** Findings are attributed to the ORM
call site, so two tests exercising the same helper produce one entry, not two.
That is the intent: a baseline tracks code locations that have a problem, not
occurrences in a test suite. Adding a test that touches known-bad code is not a
regression, and should not fail the build; changing the code so a *new* place
has the problem is, and does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._detect import Finding

__all__ = ["BaselineEntry", "entry_for", "load", "save", "split", "stale"]


@dataclass(frozen=True)
class BaselineEntry:
    """A known finding, identified by what does not move."""

    kind: str
    label: str
    file: str | None
    function: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "file": self.file,
            "function": self.function,
        }

    def __str__(self) -> str:
        where = f" at {self.file}:{self.function}()" if self.file else ""
        return f"{self.kind} {self.label}{where}"


def _relative(path: str, root: str | None) -> str:
    if root is None:
        return path
    try:
        return os.path.relpath(path, root)
    except ValueError:  # pragma: no cover - only on Windows across drives
        return path


def entry_for(finding: Finding, *, root: str | None = None) -> BaselineEntry:
    """The baseline identity of a finding."""
    frame = finding.frame
    return BaselineEntry(
        kind=finding.kind,
        label=finding.label,
        file=None if frame is None else _relative(frame.filename, root),
        function=None if frame is None else frame.function,
    )


def load(path: Path) -> set[BaselineEntry]:
    """Read a baseline file. A missing file is an empty baseline, not an error."""
    if not path.exists():
        return set()
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        BaselineEntry(
            kind=item["kind"],
            label=item["label"],
            file=item.get("file"),
            function=item.get("function"),
        )
        for item in document.get("entries", [])
    }


def save(path: Path, findings: list[Finding], *, version: str, root: str | None = None) -> int:
    """Write a baseline from these findings. Returns how many were recorded."""
    entries = sorted(
        {entry_for(finding, root=root) for finding in findings},
        key=lambda e: (e.kind, e.label, e.file or "", e.function or ""),
    )
    document = {
        "tool": "queryspy",
        "version": version,
        "entries": [entry.as_dict() for entry in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def split(
    findings: list[Finding], baseline: set[BaselineEntry], *, root: str | None = None
) -> tuple[list[Finding], list[Finding]]:
    """Partition findings into (new, already known)."""
    new: list[Finding] = []
    known: list[Finding] = []
    for finding in findings:
        target = known if entry_for(finding, root=root) in baseline else new
        target.append(finding)
    return new, known


def stale(
    baseline: set[BaselineEntry], seen: list[Finding], *, root: str | None = None
) -> list[BaselineEntry]:
    """Baseline entries that no longer occur - fixed, or moved.

    Surfaced so the file gets pruned as things improve, rather than quietly
    accumulating entries that protect nothing.
    """
    occurred = {entry_for(finding, root=root) for finding in seen}
    return sorted(baseline - occurred, key=lambda e: (e.kind, e.label))
