"""JSON and SARIF output."""

from __future__ import annotations

import json

from queryspy import AppFrame, Finding, to_dict, to_json, to_sarif

FRAME = AppFrame(filename="/repo/app/services/users.py", lineno=42, function="list_users")

LAZY = Finding(
    kind="lazy_load",
    label="User.addresses",
    count=11,
    sql="SELECT address.id FROM address WHERE address.user_id = :pk_1",
    frame=FRAME,
    entity="Address",
    uselist=True,
)
UNLOCATED = Finding(
    kind="repeated_statement",
    label="SELECT 1",
    count=3,
    sql="SELECT 1",
    frame=None,
)


def test_to_dict_carries_the_hint_and_location() -> None:
    payload = to_dict(LAZY, root="/repo")
    assert payload["kind"] == "lazy_load"
    assert payload["count"] == 11
    assert payload["hint"] == ".options(selectinload(User.addresses))"
    assert payload["location"] == {
        "file": "app/services/users.py",
        "line": 42,
        "function": "list_users",
    }


def test_to_dict_omits_location_when_unattributed() -> None:
    assert "location" not in to_dict(UNLOCATED)


def test_to_dict_includes_origin_only_when_set() -> None:
    assert "origin" not in to_dict(LAZY)
    tagged = to_dict(Finding(**{**LAZY.__dict__, "origin": "tests/test_x.py::test_y"}))
    assert tagged["origin"] == "tests/test_x.py::test_y"


def test_absolute_path_kept_when_no_root_given() -> None:
    assert to_dict(LAZY)["location"]["file"] == "/repo/app/services/users.py"


def test_json_document_shape() -> None:
    document = json.loads(to_json([LAZY, UNLOCATED], version="0.2.0", root="/repo"))
    assert document["tool"] == "queryspy"
    assert document["version"] == "0.2.0"
    assert len(document["findings"]) == 2


def test_sarif_is_valid_2_1_0() -> None:
    document = json.loads(to_sarif([LAZY], version="0.2.0", root="/repo"))
    assert document["version"] == "2.1.0"
    assert document["$schema"].endswith("sarif-2.1.0.json")

    driver = document["runs"][0]["tool"]["driver"]
    assert driver["name"] == "queryspy"
    assert driver["version"] == "0.2.0"

    result = document["runs"][0]["results"][0]
    assert result["ruleId"] == "lazy_load"
    assert result["level"] == "warning"
    location = result["locations"][0]["physicalLocation"]
    # Repo-relative, which is what code scanning needs to place the annotation.
    assert location["artifactLocation"]["uri"] == "app/services/users.py"
    assert location["region"]["startLine"] == 42


def test_sarif_describes_only_the_rules_that_fired() -> None:
    document = json.loads(to_sarif([LAZY], version="0.2.0"))
    rules = [rule["id"] for rule in document["runs"][0]["tool"]["driver"]["rules"]]
    assert rules == ["lazy_load"]

    both = json.loads(to_sarif([LAZY, UNLOCATED], version="0.2.0"))
    assert {r["id"] for r in both["runs"][0]["tool"]["driver"]["rules"]} == {
        "lazy_load",
        "repeated_statement",
    }


def test_sarif_result_without_a_frame_has_no_location() -> None:
    document = json.loads(to_sarif([UNLOCATED], version="0.2.0"))
    assert "locations" not in document["runs"][0]["results"][0]


def test_sarif_message_includes_count_label_and_fix() -> None:
    document = json.loads(to_sarif([LAZY], version="0.2.0"))
    message = document["runs"][0]["results"][0]["message"]["text"]
    assert "11x User.addresses (lazy_load)" in message
    assert "selectinload" in message


def test_sarif_message_prefixes_the_origin() -> None:
    tagged = Finding(**{**LAZY.__dict__, "origin": "tests/test_x.py::test_y"})
    document = json.loads(to_sarif([tagged], version="0.2.0"))
    assert document["runs"][0]["results"][0]["message"]["text"].startswith(
        "[tests/test_x.py::test_y] "
    )


def test_empty_run_is_still_valid() -> None:
    document = json.loads(to_sarif([], version="0.2.0"))
    assert document["runs"][0]["results"] == []
    assert document["runs"][0]["tool"]["driver"]["rules"] == []
