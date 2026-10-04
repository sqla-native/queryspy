# pytest

The plugin registers itself. There is nothing to add to `conftest.py`.

## Assertions

```python
from queryspy import (
    assert_max_queries,
    assert_num_queries,
    no_n_plus_one,
    record,
)
```

| | |
| --- | --- |
| `assert_num_queries(n)` | Exactly `n` statements |
| `assert_max_queries(n)` | At most `n` statements |
| `no_n_plus_one()` | No findings |
| `record()` | Just record; inspect `query_count` and `findings()` yourself |

Counts are **statements that reached the driver**, flushes included — the same
thing Django's `assertNumQueries` counts. That is deliberately not the same as
the number of ORM-level executions: one `selectinload` is one ORM execute but
two statements, and a flush is a statement that never reaches the ORM hook at
all.

Every failure subclasses `AssertionError`, so pytest renders it like a failed
`assert`. A failing test body always wins — your own exception is never masked
by a queryspy assertion.

## The fixture

```python
def test_inspect(session, queryspy):
    list_users(session)
    assert queryspy.query_count == 2
    assert not queryspy.findings()
```

## The marker

```python
@pytest.mark.queryspy(max_queries=5, allow_n_plus_one=True, threshold=3)
def test_admin_report(session): ...
```

| Argument | Effect |
| --- | --- |
| `max_queries` | Per-test budget; overrides `queryspy_budget` |
| `allow_n_plus_one` | Exempt this test from the N+1 gate |
| `threshold` | Repeats required before something is a finding |
| `fail_on` | Which finding kinds fail this test; same values as `queryspy_fail_on`, as a string or a list. `allow_n_plus_one=True` still wins |

## Command line and ini

| Option | Effect |
| --- | --- |
| `--queryspy-strict` | Fail any test that triggers an N+1 |
| `--queryspy-report=PATH` | Write a session report (`.sarif` or `.json`) |
| `--queryspy-report-format` | `auto` (default), `json`, or `sarif` |
| `queryspy_fail_on = n_plus_one` | The ini equivalent of `--queryspy-strict` |
| `queryspy_fail_on = lazy_load,column_load` | Fail only on the listed finding kinds (see below) |
| `queryspy_budget = 10` | Maximum statements per test |
| `queryspy_capture_stacks = false` | Skip source attribution |
| `--queryspy-baseline=PATH` | Tolerate findings recorded in PATH ([baselines](baseline.md)) |
| `--queryspy-baseline-update` | Rewrite the baseline from this run instead of enforcing it |

```toml
[tool.pytest.ini_options]
queryspy_fail_on = "n_plus_one"
queryspy_budget = 25
```

### Gating on some kinds only

`queryspy_fail_on` also takes a comma-separated list of
[finding kinds](detectors.md): `lazy_load`, `column_load` and
`repeated_statement`. Only those fail a test; the others are still recorded,
so they show up in a report and can be baselined.

```toml
[tool.pytest.ini_options]
# Gate on the two precise detectors now; adopt the repeated-statement backstop
# later, through a baseline.
queryspy_fail_on = "lazy_load,column_load"
```

That is the adoption path the detectors' precision suggests: `lazy_load` and
`column_load` are rarely wrong, while `repeated_statement` also counts lookups
that a shared test session makes look identical. `--queryspy-strict` always
gates on every kind, and an unknown name is a usage error, so a typo cannot
quietly turn the gate off.

!!! tip "Cost"

    The wrapper stays inert unless a policy asks for something, so a suite using
    neither the marker nor the flag pays nothing.

    When it is active, recording adds roughly 40% to query time — measured by
    `scripts/benchmark.py`, which is committed so the number stays checkable.
    About a quarter of that is stack capture; `queryspy_capture_stacks = false`
    keeps the counts and drops the source lines.

    Rendering a statement to SQL is deliberately deferred until a record is
    actually reported. Doing it eagerly, as queryspy did before 0.3, was roughly
    half of all recording overhead on its own.

## Reports

```bash
pytest --queryspy-report=queryspy.sarif
```

Requesting a report **collects without enforcing**: outcomes are unchanged
unless you also pass `--queryspy-strict` or set a budget. The report is written
even when the run fails, because that is when it is most useful. Each finding
carries the test node id that produced it.

See [CI and code scanning](ci.md).
