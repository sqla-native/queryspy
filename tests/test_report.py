"""Report rendering and fix hints.

The `fix:` line is the differentiator between a useful report and a number, so
it gets pinned per finding kind.
"""

from __future__ import annotations

from queryspy import AppFrame, Finding, render_finding, render_findings

FRAME = AppFrame(filename="app/services/users.py", lineno=42, function="list_users")


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "lazy_load",
        "label": "User.addresses",
        "count": 11,
        "sql": "SELECT address.id FROM address WHERE address.user_id = :pk_1",
        "frame": FRAME,
        "entity": "Address",
        "uselist": True,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_collection_lazy_load_suggests_selectinload() -> None:
    rendered = render_finding(_finding())
    assert rendered.splitlines() == [
        "N+1 detected: 11 queries for User.addresses (lazy load)",
        "  triggered from app/services/users.py:42 in list_users()",
        "  SELECT address.id FROM address WHERE address.user_id = :pk_1",
        "  fix: .options(selectinload(User.addresses))",
    ]


def test_many_to_one_lazy_load_suggests_joinedload() -> None:
    rendered = render_finding(_finding(label="Address.user", uselist=False))
    assert "joinedload(Address.user)" in rendered


def test_column_load_names_both_causes() -> None:
    rendered = render_finding(_finding(kind="column_load", label="User", entity="User"))
    assert "N+1 detected: 11 column loads for User" in rendered
    assert "undefer" in rendered
    assert "expire_on_commit=False" in rendered


def test_repeated_statement_suggests_a_single_statement() -> None:
    rendered = render_finding(_finding(kind="repeated_statement", label="SELECT ...", count=3))
    assert rendered.startswith("3 identical queries")
    assert "in_(ids)" in rendered


def test_repeated_statement_without_an_entity_still_renders() -> None:
    rendered = render_finding(_finding(kind="repeated_statement", entity=None))
    assert "fetch the rows in one statement" in rendered


def test_missing_frame_omits_the_attribution_line() -> None:
    rendered = render_finding(_finding(frame=None))
    assert "triggered from" not in rendered
    assert len(rendered.splitlines()) == 3


def test_long_sql_is_truncated() -> None:
    rendered = render_finding(_finding(sql="SELECT " + "x" * 300))
    sql_line = rendered.splitlines()[2].strip()
    assert sql_line.endswith("...")
    assert len(sql_line) == 120


def test_short_sql_is_not_truncated() -> None:
    rendered = render_finding(_finding(sql="SELECT 1"))
    assert rendered.splitlines()[2].strip() == "SELECT 1"


def test_findings_render_separated_by_a_blank_line() -> None:
    rendered = render_findings([_finding(), _finding(kind="repeated_statement")])
    assert "\n\n" in rendered


def test_app_frame_str() -> None:
    assert str(FRAME) == "app/services/users.py:42 in list_users()"
