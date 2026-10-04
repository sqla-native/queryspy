"""Baseline identity and file handling."""

from __future__ import annotations

import json
from pathlib import Path

from queryspy import AppFrame, Finding
from queryspy._baseline import (
    BaselineEntry,
    canonical_label,
    entry_for,
    load,
    save,
    split,
    stale,
)


def finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "lazy_load",
        "label": "User.addresses",
        "count": 11,
        "sql": "SELECT ...",
        "frame": AppFrame(filename="/repo/app/users.py", lineno=42, function="list_users"),
        "entity": "Address",
        "uselist": True,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_identity_ignores_the_line_number() -> None:
    """An unrelated edit above must not expire the entry."""
    moved = finding(
        frame=AppFrame(filename="/repo/app/users.py", lineno=999, function="list_users")
    )
    assert entry_for(finding(), root="/repo") == entry_for(moved, root="/repo")


def test_identity_ignores_the_count() -> None:
    """A bigger fixture must not expire the entry."""
    assert entry_for(finding(count=3), root="/repo") == entry_for(finding(count=800), root="/repo")


def test_identity_distinguishes_kind_label_and_place() -> None:
    base = entry_for(finding(), root="/repo")
    assert base != entry_for(finding(kind="column_load"), root="/repo")
    assert base != entry_for(finding(label="User.orders"), root="/repo")
    assert base != entry_for(
        finding(frame=AppFrame(filename="/repo/app/other.py", lineno=42, function="list_users")),
        root="/repo",
    )


def test_identity_without_a_frame() -> None:
    entry = entry_for(finding(frame=None))
    assert entry.file is None
    assert entry.function is None
    assert str(entry) == "lazy_load User.addresses"


def test_entry_str_names_the_place() -> None:
    assert (
        str(entry_for(finding(), root="/repo"))
        == "lazy_load User.addresses at app/users.py:list_users()"
    )


def test_missing_file_is_an_empty_baseline_not_an_error(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == set()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    count = save(path, [finding(), finding(kind="column_load")], version="0.3.0", root="/repo")

    assert count == 2
    document = json.loads(path.read_text())
    assert document["tool"] == "queryspy"
    assert document["version"] == "0.3.0"
    assert load(path) == {
        entry_for(finding(), root="/repo"),
        entry_for(finding(kind="column_load"), root="/repo"),
    }


def test_save_deduplicates(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    # The same finding from two tests is one entry.
    assert save(path, [finding(), finding(count=5)], version="0.3.0", root="/repo") == 1


def test_save_creates_missing_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "baseline.json"
    save(path, [finding()], version="0.3.0", root="/repo")
    assert path.exists()


def test_split_separates_new_from_known() -> None:
    known = finding()
    fresh = finding(label="User.orders")
    baseline = {entry_for(known, root="/repo")}

    new, already = split([known, fresh], baseline, root="/repo")

    assert [f.label for f in new] == ["User.orders"]
    assert [f.label for f in already] == ["User.addresses"]


def test_stale_reports_entries_that_no_longer_occur() -> None:
    baseline = {
        entry_for(finding(), root="/repo"),
        entry_for(finding(label="User.orders"), root="/repo"),
    }
    gone = stale(baseline, [finding()], root="/repo")

    assert [entry.label for entry in gone] == ["User.orders"]


def test_nothing_is_stale_when_everything_still_occurs() -> None:
    baseline = {entry_for(finding(), root="/repo")}
    assert stale(baseline, [finding()], root="/repo") == []


def test_entry_as_dict_is_json_safe() -> None:
    payload = BaselineEntry(kind="lazy_load", label="X.y", file="a.py", function="f").as_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_absolute_path_kept_when_no_root_given() -> None:
    assert entry_for(finding()).file == "/repo/app/users.py"


# SQLAlchemy 2.0 renders an ORM SELECT with an alias per column and 2.1 does not.
_SQLA_20 = (
    "SELECT project.id AS project_id, project.name AS project_name"
    " FROM project WHERE project.id = :pk_1"
)
_SQLA_21 = "SELECT project.id, project.name FROM project WHERE project.id = :pk_1"


def test_identity_is_the_same_on_sqlalchemy_2_0_and_2_1() -> None:
    on_20 = entry_for(finding(kind="repeated_statement", label=_SQLA_20), root="/repo")
    on_21 = entry_for(finding(kind="repeated_statement", label=_SQLA_21), root="/repo")
    assert on_20 == on_21
    assert on_20.label == _SQLA_21


def test_a_baseline_written_on_2_0_gates_a_run_on_2_1(tmp_path: Path) -> None:
    """A file recorded with 2.0's aliases must neither go stale nor let the finding through."""
    path = tmp_path / "baseline.json"
    save(path, [finding(kind="repeated_statement", label=_SQLA_20)], version="0", root="/repo")
    # Hand-written 2.0 labels on disk, as files saved before canonicalization have them.
    document = json.loads(path.read_text(encoding="utf-8"))
    document["entries"][0]["label"] = _SQLA_20
    path.write_text(json.dumps(document), encoding="utf-8")

    baseline = load(path)
    on_21 = [finding(kind="repeated_statement", label=_SQLA_21)]
    new, known = split(on_21, baseline, root="/repo")
    assert (new, known) == ([], on_21)
    assert stale(baseline, on_21, root="/repo") == []


def test_canonical_label_drops_every_dialect_s_quoted_alias() -> None:
    assert canonical_label('SELECT "Order".id AS "Order_id" FROM "Order"') == (
        'SELECT "Order".id FROM "Order"'
    )
    assert canonical_label("SELECT `t`.`a` AS `t_a` FROM t") == "SELECT `t`.`a` FROM t"
    assert canonical_label("SELECT [t].[a] AS [t_a] FROM [t]") == "SELECT [t].[a] FROM [t]"
    assert canonical_label("SELECT count(*) AS count_1 FROM (SELECT x FROM y) AS anon_1") == (
        "SELECT count(*) FROM (SELECT x FROM y)"
    )


def test_canonical_label_leaves_a_label_without_aliases_alone() -> None:
    assert canonical_label("User.addresses") == "User.addresses"
    assert canonical_label(_SQLA_21) == _SQLA_21
