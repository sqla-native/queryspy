"""The pytest plugin, exercised through pytester.

These run a real pytest session, so they cover the plugin the way a user meets
it: an ini file, a command-line flag, and a marker.
"""

from __future__ import annotations

import pytest

BOILERPLATE = """
import pytest
from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload,
)
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    addresses: Mapped[list["Address"]] = relationship()


class Address(Base):
    __tablename__ = "address"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(50))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for n in range(3):
            s.add(User(name=f"u{n}", addresses=[Address(email=f"{n}@x.com")]))
        s.commit()
        s.expunge_all()
    with Session(engine) as s:
        yield s
    engine.dispose()


def n_plus_one(session):
    for user in session.scalars(select(User)).all():
        list(user.addresses)


def eager(session):
    for user in session.scalars(select(User).options(selectinload(User.addresses))).all():
        list(user.addresses)


def one_by_one(session):
    for user_id in (1, 2, 3):
        session.get(User, user_id)
"""

_IMPORTS = "from conftest import eager, n_plus_one, one_by_one\n"


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(BOILERPLATE)
    return pytester


def write(project: pytest.Pytester, body: str) -> None:
    """Write the inner test module.

    Conftest names are not injected into test modules, so the helpers have to be
    imported explicitly; pytester puts its rootdir on sys.path, which makes
    ``from conftest import ...`` resolve.
    """
    project.makepyfile(_IMPORTS + body)


def run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner pytest session.

    ``-p no:asyncio`` matters: the inner run has its own rootdir, so it never
    sees this project's asyncio settings, and the outer session's
    ``filterwarnings = error`` would turn pytest-asyncio's configure-time
    deprecation warning into a hard crash. The generated tests are all sync.
    """
    return project.runpytest("-p", "no:asyncio", *args)


def test_strict_flag_fails_an_n_plus_one(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    result = run(project, "--queryspy-strict")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*N+1 detected: 3 queries for User.addresses*"])
    result.stdout.fnmatch_lines(["*fix: .options(selectinload(User.addresses))*"])


def test_strict_flag_passes_eager_loading(project: pytest.Pytester) -> None:
    write(project, "def test_clean(session):\n    eager(session)\n")
    run(project, "--queryspy-strict").assert_outcomes(passed=1)


def test_without_a_policy_the_wrapper_does_nothing(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project).assert_outcomes(passed=1)


def test_marker_can_allow_a_known_n_plus_one(project: pytest.Pytester) -> None:
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(allow_n_plus_one=True)\n"
        "def test_known(session):\n    n_plus_one(session)\n",
    )
    run(project, "--queryspy-strict").assert_outcomes(passed=1)


def test_marker_threshold_raises_the_bar(project: pytest.Pytester) -> None:
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(threshold=4)\n"
        "def test_under_threshold(session):\n    n_plus_one(session)\n",
    )
    run(project, "--queryspy-strict").assert_outcomes(passed=1)


def test_ini_fail_on_n_plus_one(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = n_plus_one\n")
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    result = run(project)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*N+1 detected*"])


# One lazy-load offender and one repeated-statement offender, so a per-kind gate
# can be seen failing one and passing the other.
_BOTH_KINDS = (
    "def test_lazy(session):\n    n_plus_one(session)\n\n"
    "def test_repeated(session):\n    one_by_one(session)\n"
)


def test_ini_fail_on_gates_only_the_listed_kinds(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = lazy_load, column_load\n")
    write(project, _BOTH_KINDS)
    result = run(project)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["FAILED *::test_lazy*"])


def test_ini_fail_on_can_gate_the_repeated_statement_backstop_alone(
    project: pytest.Pytester,
) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = repeated_statement\n")
    write(project, _BOTH_KINDS)
    result = run(project)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["FAILED *::test_repeated*"])


def test_ini_fail_on_n_plus_one_gates_every_kind(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = n_plus_one\n")
    write(project, _BOTH_KINDS)
    run(project).assert_outcomes(failed=2)


def test_ini_fail_on_ignores_none_and_empty_entries(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = none,, lazy_load\n")
    write(project, _BOTH_KINDS)
    run(project).assert_outcomes(failed=1, passed=1)


def test_ini_fail_on_rejects_an_unknown_kind(project: pytest.Pytester) -> None:
    """A typo must not quietly turn the gate off."""
    project.makeini("[pytest]\nqueryspy_fail_on = lazy_loads\n")
    write(project, _BOTH_KINDS)
    result = run(project)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryspy_fail_on: unknown value 'lazy_loads'*"])


def test_strict_flag_gates_every_kind_whatever_the_ini(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = lazy_load\n")
    write(project, _BOTH_KINDS)
    run(project, "--queryspy-strict").assert_outcomes(failed=2)


def test_marker_fail_on_overrides_the_ini(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_fail_on = lazy_load\n")
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(fail_on='repeated_statement')\n"
        "def test_repeated_gated(session):\n    one_by_one(session)\n\n"
        "@pytest.mark.queryspy(fail_on=['column_load'])\n"
        "def test_lazy_not_gated(session):\n    n_plus_one(session)\n\n"
        "@pytest.mark.queryspy(fail_on='none')\n"
        "def test_lazy_opted_out(session):\n    n_plus_one(session)\n",
    )
    result = run(project)
    result.assert_outcomes(failed=1, passed=2)
    result.stdout.fnmatch_lines(["FAILED *::test_repeated_gated*"])


def test_allow_n_plus_one_wins_over_a_marker_fail_on(project: pytest.Pytester) -> None:
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(fail_on='lazy_load', allow_n_plus_one=True)\n"
        "def test_known(session):\n    n_plus_one(session)\n",
    )
    run(project, "--queryspy-strict").assert_outcomes(passed=1)


def test_marker_fail_on_rejects_an_unknown_kind(project: pytest.Pytester) -> None:
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(fail_on='lazy-load')\n"
        "def test_typo(session):\n    n_plus_one(session)\n",
    )
    result = run(project)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*unknown value 'lazy-load'*"])


def test_ini_budget_fails_an_over_budget_test(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_budget = 2\n")
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    result = run(project)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected at most 2 queries, got 4*"])


def test_marker_budget_overrides_the_ini(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_budget = 2\n")
    write(
        project,
        "import pytest\n\n"
        "@pytest.mark.queryspy(max_queries=10)\n"
        "def test_generous(session):\n    n_plus_one(session)\n",
    )
    run(project).assert_outcomes(passed=1)


def test_fixture_exposes_the_recorder(project: pytest.Pytester) -> None:
    write(
        project,
        "def test_inspect(session, queryspy):\n"
        "    n_plus_one(session)\n"
        "    assert queryspy.query_count == 4\n"
        '    assert [f.kind for f in queryspy.findings()] == ["lazy_load"]\n',
    )
    run(project).assert_outcomes(passed=1)


def test_capture_stacks_can_be_disabled_from_the_ini(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryspy_capture_stacks = false\n")
    write(
        project,
        "def test_inspect(session, queryspy):\n"
        "    n_plus_one(session)\n"
        "    assert queryspy.findings()[0].frame is None\n",
    )
    run(project).assert_outcomes(passed=1)


def test_a_failing_test_body_is_not_masked(project: pytest.Pytester) -> None:
    write(
        project,
        'def test_broken(session):\n    n_plus_one(session)\n    raise ValueError("original")\n',
    )
    result = run(project, "--queryspy-strict")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ValueError: original*"])


def test_marker_is_registered(project: pytest.Pytester) -> None:
    result = run(project, "--markers")
    result.stdout.fnmatch_lines(["*queryspy(max_queries=None*"])


def test_report_written_as_json(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-report=out/findings.json")

    import json

    document = json.loads((project.path / "out" / "findings.json").read_text())
    assert document["tool"] == "queryspy"
    assert [f["kind"] for f in document["findings"]] == ["lazy_load"]
    assert document["findings"][0]["origin"].endswith("::test_offender")
    assert document["findings"][0]["location"]["file"].endswith(".py")


def test_report_format_inferred_from_the_sarif_extension(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-report=findings.sarif")

    import json

    document = json.loads((project.path / "findings.sarif").read_text())
    assert document["version"] == "2.1.0"
    assert document["runs"][0]["results"][0]["ruleId"] == "lazy_load"


def test_report_format_can_be_forced(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-report=findings.txt", "--queryspy-report-format=sarif")

    import json

    assert json.loads((project.path / "findings.txt").read_text())["version"] == "2.1.0"


def test_requesting_a_report_does_not_fail_the_run(project: pytest.Pytester) -> None:
    """Collecting is not enforcing - a report alone must leave outcomes alone."""
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-report=findings.json").assert_outcomes(passed=1)


def test_report_is_written_even_when_tests_fail(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-report=findings.json", "--queryspy-strict").assert_outcomes(failed=1)
    assert (project.path / "findings.json").exists()


def test_clean_run_writes_an_empty_report(project: pytest.Pytester) -> None:
    write(project, "def test_clean(session):\n    eager(session)\n")
    run(project, "--queryspy-report=findings.json")

    import json

    assert json.loads((project.path / "findings.json").read_text())["findings"] == []


def test_no_report_written_without_the_flag(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project)
    assert not (project.path / "findings.json").exists()


def test_baseline_update_records_and_does_not_fail(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    result = run(
        project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update", "--queryspy-strict"
    )

    result.assert_outcomes(passed=1)  # recording, not enforcing
    result.stdout.fnmatch_lines(["*wrote 1 baseline entry to qs.json*"])

    import json

    document = json.loads((project.path / "qs.json").read_text())
    assert document["entries"][0]["kind"] == "lazy_load"
    assert document["entries"][0]["label"] == "User.addresses"
    # Deliberately absent: line number and count.
    assert set(document["entries"][0]) == {"kind", "label", "file", "function"}


def test_baseline_suppresses_the_known_finding(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update")

    # Same offender, now baselined: the gate passes.
    run(project, "--queryspy-baseline=qs.json", "--queryspy-strict").assert_outcomes(passed=1)


def test_baseline_still_fails_on_a_new_finding(project: pytest.Pytester) -> None:
    write(project, "def test_known(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update")

    # A second offender at a genuinely different code location. Note that
    # simply calling n_plus_one() from another test would NOT count as new:
    # findings are attributed to the ORM call site, so the baseline tracks code
    # locations rather than test occurrences.
    write(
        project,
        "from sqlalchemy import select\n"
        "from conftest import User\n\n"
        "def test_known(session):\n    n_plus_one(session)\n\n\n"
        "def test_new(session):\n"
        "    for user in session.scalars(select(User)).all():\n"
        "        list(user.addresses)\n",
    )
    result = run(project, "--queryspy-baseline=qs.json", "--queryspy-strict")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*N+1 detected*"])


def test_baseline_survives_a_line_shift(project: pytest.Pytester) -> None:
    """The point of excluding line numbers from the identity."""
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update")

    write(
        project,
        "# a new comment\n" * 40 + "def test_offender(session):\n    n_plus_one(session)\n",
    )
    run(project, "--queryspy-baseline=qs.json", "--queryspy-strict").assert_outcomes(passed=1)


def test_stale_baseline_entries_are_reported(project: pytest.Pytester) -> None:
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update")

    # Fixed it. The baseline entry now protects nothing.
    write(project, "def test_offender(session):\n    eager(session)\n")
    result = run(project, "--queryspy-baseline=qs.json", "--queryspy-strict")

    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*1 baseline entry no longer occurs*"])
    result.stdout.fnmatch_lines(["*--queryspy-baseline-update to prune them*"])


def test_missing_baseline_file_is_not_an_error(project: pytest.Pytester) -> None:
    write(project, "def test_clean(session):\n    eager(session)\n")
    run(project, "--queryspy-baseline=absent.json", "--queryspy-strict").assert_outcomes(passed=1)


def test_baseline_does_not_suppress_budget_failures(project: pytest.Pytester) -> None:
    """A baseline is about findings, not about how many queries you may run."""
    write(project, "def test_offender(session):\n    n_plus_one(session)\n")
    run(project, "--queryspy-baseline=qs.json", "--queryspy-baseline-update")

    project.makeini("[pytest]\nqueryspy_budget = 2\n")
    run(project, "--queryspy-baseline=qs.json").assert_outcomes(failed=1)
