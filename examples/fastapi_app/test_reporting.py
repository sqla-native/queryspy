"""Machine-readable output, produced from a real request.

Validates that what CI uploads to code scanning actually describes the bug in
this app - correct rule, correct file, correct line.
"""

from __future__ import annotations

import json

import httpx

from fastapi_app.app import REPORTS, app, seed
from queryspy import to_json, to_sarif


async def _findings():
    await seed()
    REPORTS.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/projects")
    return REPORTS[-1].findings


async def test_sarif_points_code_scanning_at_the_offending_line(tmp_path):
    findings = await _findings()
    document = json.loads(to_sarif(findings, version="0.2.0", root=str(tmp_path.parent)))

    assert document["version"] == "2.1.0"
    result = document["runs"][0]["results"][0]
    assert result["ruleId"] == "lazy_load"
    assert result["level"] == "warning"

    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"].endswith("app.py")
    assert location["region"]["startLine"] > 0

    # The rule block only describes rules that actually fired.
    assert [r["id"] for r in document["runs"][0]["tool"]["driver"]["rules"]] == ["lazy_load"]


async def test_json_report_carries_the_fix():
    findings = await _findings()
    document = json.loads(to_json(findings, version="0.2.0"))

    assert document["tool"] == "queryspy"
    finding = document["findings"][0]
    assert finding["kind"] == "lazy_load"
    assert finding["label"] == "Project.tasks"
    assert finding["hint"] == ".options(selectinload(Project.tasks))"
    assert finding["location"]["function"] == "list_projects"
