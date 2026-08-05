"""Panel rendering."""

from __future__ import annotations

from queryspy import AppFrame, Finding
from queryspy._panel import render_panel
from queryspy._recorder import SlowStatement
from queryspy.asgi import RequestReport

FINDING = Finding(
    kind="lazy_load",
    label="User.addresses",
    count=11,
    sql="SELECT address.id FROM address WHERE address.user_id = :pk_1",
    frame=AppFrame(filename="app/users.py", lineno=42, function="list_users"),
    entity="Address",
    uselist=True,
)


def report(**overrides: object) -> RequestReport:
    defaults: dict[str, object] = {
        "method": "GET",
        "path": "/users",
        "query_count": 12,
        "findings": [FINDING],
        "duration_ms": 41.3,
        "db_duration_ms": 22.5,
        "slowest": SlowStatement(sql="SELECT 1", duration_ms=9.0),
    }
    defaults.update(overrides)
    return RequestReport(**defaults)  # type: ignore[arg-type]


def test_empty_panel() -> None:
    page = render_panel([], version="0.3.0")
    assert "No requests recorded yet." in page
    assert "0 requests" in page


def test_panel_shows_counts_and_findings() -> None:
    page = render_panel([report()], version="0.3.0")
    assert "1 request &middot; 12 queries &middot; 1 flagged" in page
    assert "GET /users" in page
    assert "lazy_load" in page
    assert "app/users.py:42 in list_users()" in page
    assert "selectinload(User.addresses)" in page
    assert "slowest 9.0ms" in page


def test_newest_request_is_first() -> None:
    page = render_panel([report(path="/old"), report(path="/new")], version="0.3.0")
    assert page.index("/new") < page.index("/old")


def test_clean_request_is_not_flagged() -> None:
    page = render_panel([report(findings=[])], version="0.3.0")
    assert "0 flagged" in page
    assert 'class="bad"' not in page
    assert "finding" not in page.split("<footer>")[0].split("<main>")[1]


def test_singular_wording() -> None:
    page = render_panel([report(query_count=1, findings=[FINDING])], version="0.3.0")
    assert "1 request &middot; 1 query" in page
    assert "1 finding<" in page


def test_report_without_a_slowest_statement() -> None:
    page = render_panel([report(slowest=None)], version="0.3.0")
    assert "slowest" not in page


def test_finding_without_a_frame_omits_the_location() -> None:
    page = render_panel(
        [report(findings=[Finding(**{**FINDING.__dict__, "frame": None})])], version="0.3.0"
    )
    assert 'class="where"' not in page


def test_html_is_escaped() -> None:
    """A path is attacker-influenced; it must never reach the page as markup."""
    page = render_panel([report(path="/<script>alert(1)</script>")], version="0.3.0")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_panel_is_self_contained() -> None:
    page = render_panel([report()], version="0.3.0")
    assert "<style>" in page
    assert "src=" not in page
    assert "href=" not in page


def test_footer_states_the_privacy_property() -> None:
    page = render_panel([], version="0.3.0")
    assert "Bind parameters are never captured" in page
    assert "0.3.0" in page
