"""Machine-readable findings: JSON for tooling, SARIF for GitHub code scanning.

SARIF is normally a static-analysis format and these findings are produced at
runtime, but the shape fits exactly: a rule id, a message, and a source
location. Uploading it to GitHub code scanning puts each N+1 as an annotation on
the line of the pull request that causes it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ._detect import Finding
from ._hints import hint_for

__all__ = ["to_dict", "to_json", "to_sarif"]

_SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_INFO_URI = "https://github.com/sqla-native/queryspy"

_RULES: dict[str, tuple[str, str]] = {
    "lazy_load": (
        "Relationship lazily loaded per row",
        "A relationship was loaded once per parent row. Load it eagerly instead.",
    ),
    "column_load": (
        "Column loaded per instance",
        "A deferred column, or an attribute refreshed after commit, was loaded once per instance.",
    ),
    "repeated_statement": (
        "Identical statement repeated",
        "The same statement ran several times with different parameters. Fetch "
        "the rows in one statement instead.",
    ),
}


def _relative(path: str, root: str | None) -> str:
    """Repo-relative path, which is what code scanning needs to place an annotation."""
    if root is None:
        return path
    try:
        return os.path.relpath(path, root)
    except ValueError:  # pragma: no cover - only on Windows across drives
        return path


def to_dict(finding: Finding, *, root: str | None = None) -> dict[str, Any]:
    """One finding as a plain, JSON-safe mapping."""
    payload: dict[str, Any] = {
        "kind": finding.kind,
        "label": finding.label,
        "count": finding.count,
        "sql": finding.sql,
        "entity": finding.entity,
        "hint": hint_for(finding),
    }
    if finding.origin is not None:
        payload["origin"] = finding.origin
    if finding.frame is not None:
        payload["location"] = {
            "file": _relative(finding.frame.filename, root),
            "line": finding.frame.lineno,
            "function": finding.frame.function,
        }
    return payload


def to_json(
    findings: list[Finding], *, version: str, root: str | None = None, indent: int = 2
) -> str:
    """The whole result set as JSON."""
    document = {
        "tool": "queryspy",
        "version": version,
        "findings": [to_dict(finding, root=root) for finding in findings],
    }
    return json.dumps(document, indent=indent, sort_keys=False) + "\n"


def _sarif_result(finding: Finding, root: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": finding.kind,
        "level": "warning",
        "message": {"text": _message(finding)},
    }
    if finding.frame is not None:
        result["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": _relative(finding.frame.filename, root)},
                    "region": {"startLine": finding.frame.lineno},
                }
            }
        ]
    return result


def _message(finding: Finding) -> str:
    prefix = f"[{finding.origin}] " if finding.origin else ""
    return f"{prefix}{finding.count}x {finding.label} ({finding.kind}). {hint_for(finding)}"


def _sarif_rules(findings: list[Finding]) -> list[dict[str, Any]]:
    """Only describe rules that actually fired, so the tool block stays honest."""
    seen = [kind for kind in _RULES if any(f.kind == kind for f in findings)]
    return [
        {
            "id": kind,
            "name": kind,
            "shortDescription": {"text": _RULES[kind][0]},
            "fullDescription": {"text": _RULES[kind][1]},
            "helpUri": f"{_INFO_URI}#what-it-catches",
            "defaultConfiguration": {"level": "warning"},
        }
        for kind in seen
    ]


def to_sarif(
    findings: list[Finding], *, version: str, root: str | None = None, indent: int = 2
) -> str:
    """SARIF 2.1.0, ready to upload to GitHub code scanning."""
    document = {
        "$schema": _SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "queryspy",
                        "version": version,
                        "informationUri": _INFO_URI,
                        "rules": _sarif_rules(findings),
                    }
                },
                "results": [_sarif_result(finding, root) for finding in findings],
            }
        ],
    }
    return json.dumps(document, indent=indent) + "\n"
