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

## Command line and ini

| Option | Effect |
| --- | --- |
| `--queryspy-strict` | Fail any test that triggers an N+1 |
| `--queryspy-report=PATH` | Write a session report (`.sarif` or `.json`) |
| `--queryspy-report-format` | `auto` (default), `json`, or `sarif` |
| `queryspy_fail_on = n_plus_one` | The ini equivalent of `--queryspy-strict` |
| `queryspy_budget = 10` | Maximum statements per test |
| `queryspy_capture_stacks = false` | Skip source attribution |

```toml
[tool.pytest.ini_options]
queryspy_fail_on = "n_plus_one"
queryspy_budget = 25
```

!!! tip "Cost"

    The wrapper stays inert unless a policy asks for something, so a suite using
    neither the marker nor the flag pays nothing. When it is active, stack
    capture is the main per-query cost — `queryspy_capture_stacks = false`
    keeps the counts and drops the source lines.

## Reports

```bash
pytest --queryspy-report=queryspy.sarif
```

Requesting a report **collects without enforcing**: outcomes are unchanged
unless you also pass `--queryspy-strict` or set a budget. The report is written
even when the run fails, because that is when it is most useful. Each finding
carries the test node id that produced it.

See [CI and code scanning](ci.md).
