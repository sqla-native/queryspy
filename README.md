# queryspy

**N+1 and query-budget detection for SQLAlchemy 2.0 — sync and async.**

Django developers get `assertNumQueries`, `nplusone`, and the Debug Toolbar.
SQLAlchemy developers get told to write their own `before_cursor_execute`
listener. `queryspy` is the missing piece: point it at your existing test suite
and it tells you which line of your code fires an N+1, and what to do about it.

```
N+1 detected: 11 queries for User.addresses (lazy load)
  triggered from app/services/users.py:28 in list_users()
  SELECT address.id AS address_id, address.email AS address_email, address.user_id ...
  fix: .options(selectinload(User.addresses))
```

## Install

```bash
pip install queryspy
```

One runtime dependency: SQLAlchemy. The pytest plugin registers itself.

**[Full documentation →](https://sqla-native.github.io/queryspy/)**

## Quick start

Run your existing suite in strict mode and see what lights up:

```bash
pytest --queryspy-strict
```

Or assert deliberately, in the tests where it matters:

```python
from queryspy import assert_max_queries, no_n_plus_one


def test_list_users(session):
    with no_n_plus_one():
        list_users(session)


def test_list_users_is_two_queries(session):
    with assert_max_queries(2):
        list_users(session)
```

Mark the places where an N+1 is a deliberate trade-off:

```python
@pytest.mark.queryspy(allow_n_plus_one=True)
def test_admin_report(session): ...
```

## What it catches

Three detectors, applied in order of precision. Each claims the queries it
explains, so nothing is reported twice.

| Detector | Catches |
|---|---|
| `lazy_load` | A relationship lazily loaded once per parent row |
| `column_load` | A deferred column, or an attribute refreshed after commit, loaded per instance |
| `repeated_statement` | The same statement executed N times — a `session.get()` loop, parents fetched one at a time, anything the ORM hooks cannot see |

The third matters more than it looks. A loop of `await session.get(User, uid)`
is not an ORM lazy load at all — no relationship-load hook will ever fire for
it — but it is still N round trips where one would do.

## Async

Async is not symmetric with sync, and it is worth knowing why.

In async SQLAlchemy a plain lazy load raises `MissingGreenlet` rather than
silently N+1'ing, so the classic lazy-load bug is loud. What is *quiet* in async
code is:

```python
users = (await session.scalars(select(User))).all()
for user in users:
    await user.awaitable_attrs.addresses  # one query per user
```

`queryspy` catches that (it does set `lazy_loaded_from`), along with
`session.get()` loops and per-item repository calls. `AsyncSession` needs no
special setup — it wraps a sync `Session`, and the listeners are registered on
the class.

Async findings are attributed to your source line too. That takes a little work:
SQLAlchemy runs an async lazy load inside a spawned greenlet whose stack holds
no application frames at all, so `queryspy` walks up the greenlet chain to find
the caller. Without that, exactly the case you most want to diagnose would
report with no source line.

## In your running app

A per-request query panel for FastAPI, Starlette or Litestar. Pure ASGI3 — no
framework dependency, no new packages.

```python
from queryspy.asgi import QuerySpyMiddleware

app.add_middleware(QuerySpyMiddleware, budget=10)
```

```
WARNING queryspy: GET /projects - 12 queries in 41.3ms

N+1 detected: 11 queries for Project.tasks (lazy load)
  triggered from app/api/projects.py:31 in list_projects()
  fix: .options(selectinload(Project.tasks))
```

Or open the panel — the thing FastAPI has never had a Debug Toolbar equivalent
for:

```python
app.add_middleware(QuerySpyMiddleware, panel=True)  # then GET /__queryspy__
```

Recent requests, query counts, how much of the wall clock was actually spent in
the database, and every finding with its source line and fix. One self-contained
HTML page — no CDN, nothing fetched from anywhere. Off by default.

Concurrent requests are isolated from each other: recording is scoped to a
context variable, so interleaved requests never record each other's queries.
Failed requests still report — the report is most useful precisely when the
request blew up.

[ASGI guide →](https://sqla-native.github.io/queryspy/asgi/)

## In CI

```bash
pytest --queryspy-report=queryspy.sarif
```

SARIF uploads to GitHub code scanning, which puts each N+1 as an annotation on
the line of the pull request that causes it. There is an Action for the whole
sequence:

```yaml
permissions:
  security-events: write

- uses: sqla-native/queryspy@v0
  with:
    args: tests/
```

Requesting a report **collects without enforcing** — outcomes are unchanged
unless you also gate. Adopting the report first and the gate second is usually
the shorter path on a suite that has never been measured.

[CI guide →](https://sqla-native.github.io/queryspy/ci/)

## Adopting it on a codebase that already has N+1s

Turning the gate on and watching twenty tests go red is how a linter gets
switched back off. Record what is already there, then fail only on new ones:

```bash
pytest --queryspy-baseline=queryspy-baseline.json --queryspy-baseline-update
pytest --queryspy-baseline=queryspy-baseline.json --queryspy-strict
```

A finding is identified by `(kind, label, file, function)` — not by line number,
count, or which test found it — so the baseline survives unrelated edits instead
of expiring on every reformat. Entries that stop occurring get reported so you
can prune them.

[Baselines →](https://sqla-native.github.io/queryspy/baseline/)

## API

| | |
|---|---|
| `record()` | The recording window. `spy.query_count`, `spy.findings()`. |
| `assert_num_queries(n)` | Exactly `n` statements. |
| `assert_max_queries(n)` | At most `n` statements. |
| `no_n_plus_one()` | No findings. |

Query counts are **statements that reached the driver**, flushes included — the
same thing Django's `assertNumQueries` counts. Timing is available too
(`spy.db_duration_ms`, `spy.slowest`), and it is often the real story: twelve
queries where one takes 31 of the 39 milliseconds is a slow query wearing an
N+1's clothes.

Every failure subclasses `AssertionError`, so pytest renders it like a failed
`assert`. A failing test body always wins over a queryspy assertion; your own
exception is never masked.

### pytest options

| Option | Effect |
|---|---|
| `--queryspy-strict` | Fail any test that triggers an N+1 |
| `queryspy_budget = 10` | Maximum statements per test |
| `queryspy_fail_on = n_plus_one` | The ini equivalent of `--queryspy-strict` |
| `queryspy_capture_stacks = false` | Skip source attribution |
| `queryspy` fixture | A live recorder, for tests that want to inspect queries themselves |

## Why not nplusone?

[`nplusone`](https://github.com/jmcarp/nplusone) is still the answer everyone
gives, and it last shipped to PyPI in **May 2018**. It predates SQLAlchemy 2.0,
has no async support, and works by monkeypatching ORM internals — which is why
it stopped working rather than being ported.

SQLAlchemy 2.0 added `do_orm_execute` and an `ORMExecuteState` that exposes
lazy-load attribution as public API. `queryspy` is built entirely on that: no
patching, no private imports, a few hundred lines.

That API also makes correctness possible in a way it wasn't before. Measured on
SQLAlchemy 2.0.51:

| | `is_relationship_load` | `lazy_loaded_from` |
|---|---|---|
| true lazy load | True | **set** |
| `selectinload` | True | **None** |
| `subqueryload` | True | **None** |

A detector keyed on `is_relationship_load` would flag `selectinload` — the
*fix* — as the bug. `queryspy` keys on `lazy_loaded_from`, and the
false-positive suite is a release gate held to the same standard as the
detection suite.

## What it does not do

Not a profiler, not a query optimiser, not a production APM. It does not change
loader strategies or rewrite queries. It tells you where the problem is and what
to paste; the fix is yours.

It also does **not** detect eager loads that are never used, and that is a
deliberate omission rather than a gap. Measured: SQLAlchemy's `AttributeEvents`
exposes only mutation hooks with no read event, `InstanceState.unloaded` is
empty after an eager load, and reading an attribute does not alter `state.dict`.
The only routes left are patching `InstrumentedAttribute.__get__` — precisely
what killed `nplusone` — or taking over your entire model instrumentation. Not
worth the thing that makes this library trustworthy.

Django is out of scope too. This is `sqla-native`, and Django is the one
ecosystem that already has working tools.

## Requirements

Python 3.10+, SQLAlchemy 2.0+.

## License

MIT
