"""Suggested fixes.

The `fix:` line is what separates a useful report from a number. A developer who
sees "11 queries" still has to go and work out what to do; a developer who sees
``.options(selectinload(User.addresses))`` can paste it.
"""

from __future__ import annotations

from ._detect import Finding

__all__ = ["hint_for"]


def _relationship_hint(finding: Finding) -> str:
    target = finding.label
    if finding.uselist is False:
        return (
            f".options(joinedload({target})) - or selectinload() if you would rather "
            f"keep it a separate query"
        )
    return f".options(selectinload({target}))"


def _column_hint(finding: Finding) -> str:
    entity = finding.entity or "the model"
    # A deferred column and an attribute refreshed after commit produce the same
    # signature at the event level, so name both causes rather than guess.
    return (
        f"if {entity} defers this column, load it with .options(undefer(...)); "
        f"if these are refreshes after a commit, use "
        f"sessionmaker(expire_on_commit=False)"
    )


def _repeated_hint(finding: Finding) -> str:
    entity = finding.entity or "the rows"
    return (
        f"fetch {entity} in one statement - e.g. select(...).where(...id.in_(ids)) - "
        f"instead of one query per item"
    )


_HINTS = {
    "lazy_load": _relationship_hint,
    "column_load": _column_hint,
    "repeated_statement": _repeated_hint,
}


def hint_for(finding: Finding) -> str:
    """Return an actionable fix for a finding."""
    return _HINTS[finding.kind](finding)
