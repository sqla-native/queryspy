# Design notes

Everything here was measured against a real session before it was written down.
Where a claim came from running code, the result is quoted.

## The rule that shapes everything

**Public SQLAlchemy event API only. Never monkeypatch ORM internals.**

This is not stylistic. `nplusone` — still the tool everyone recommends — worked
by patching ORM internals, and that is why it stopped working rather than being
ported to SQLAlchemy 2.0. queryspy uses `do_orm_execute` and
`before_cursor_execute`, both documented and stable.

## `lazy_loaded_from`, never `is_relationship_load`

Measured on SQLAlchemy 2.0.51:

| Scenario | `is_relationship_load` | `lazy_loaded_from` | Queries |
| --- | --- | --- | --- |
| lazy-load loop (sync) | `True` | **set** | 4 |
| `awaitable_attrs` loop (async) | `True` | **set** | 4 |
| `selectinload` | `True` | **None** | 2 |
| `subqueryload` | `True` | **None** | 2 |
| `joinedload` | — | — | 1 |

`is_relationship_load` is `True` for the **fix** as well as the bug. A detector
keyed on it would flag `selectinload` — correctly-written code — as a problem.
Only `lazy_loaded_from` separates them.

Any change to detection has to keep both the detection and false-positive suites
passing unchanged.

## Column loads carry no `lazy_loaded_from`

Deferred columns and post-commit refreshes set `is_column_load` but leave
`lazy_loaded_from` as `None`, and `loader_strategy_path` is an empty
`RootRegistry`. So the column detector keys on `is_column_load` alone, and
attribution comes from `bind_mapper` plus the SQL template rather than the path.

## Two measurement layers, never correlated

`do_orm_execute` gives ORM records with lazy-load attribution. `before_cursor_execute`
gives the ground-truth statement count. They are reported as two separate,
clearly-labelled metrics and never stitched together, because:

- one ORM execute can produce several cursor executions (`selectinload` batching)
- flushes reach the cursor hook without ever touching the ORM hook — measured at
  one ORM execute against two statements

Correlating them is exactly the fragility that killed the predecessor.

## Class-level listeners, context-scoped recorders

Listeners are registered on the `Session` and `Engine` **classes**. `AsyncSession`
wraps a sync `Session`, so that covers async for free and users pass nothing.

*Which* recorders receive a record is scoped to a context variable, not a module
global. A global is correct for tests — one window at a time — and wrong for a
concurrent server, where interleaved requests would each record every other
request's queries.

Measured: context variables **do** propagate across SQLAlchemy's greenlet bridge,
so an async lazy load lands in the window that caused it. Registration itself is
refcounted separately, under a lock, because it is genuinely global.

## Async attribution goes through the greenlet chain

SQLAlchemy runs an async lazy load inside a spawned greenlet whose stack holds
no application frames at all — only `strategies.py`, `session.py` and friends.
Walking the live stack there returns nothing, so the most common async N+1
(`await obj.awaitable_attrs.items` in a loop) would report with no source line.

Attribution therefore falls back to `greenlet.getcurrent().parent.gr_frame`. The
import is lazy and guarded: `greenlet` is not a declared dependency, it arrives
with `sqlalchemy[asyncio]`, and it is only ever present in the situation the
fallback exists for.

## Non-goal: unused eager-load detection

Detecting `selectinload` on a relationship that is never read would be genuinely
useful. It is not shipped, because it cannot be done through public API.
Measured:

- `AttributeEvents` exposes only mutation hooks — `append`, `remove`, `set`,
  `bulk_replace`, `init_collection` — and **no** read, get or access event
- `InstanceState.unloaded` is empty after an eager load, by definition
- reading an attribute does not alter `state.dict`

The only routes left are patching `InstrumentedAttribute.__get__` — precisely
what killed `nplusone` — or taking over the user's entire model instrumentation
through `sqlalchemy.ext.instrumentation`, which is far more invasive than the
patch it would replace.

So it is declined rather than smuggled in. If SQLAlchemy ever grows a read
event, this becomes a small feature.

## A false positive is worse than a missed detection

A tool that flags correct code gets uninstalled the same day. A missed N+1 costs
some latency; a false alarm costs the tool its credibility. The false-positive
suite is a release gate weighted equally with the detection suite, and when a
signal is ambiguous the library stays quiet.

## Quality gates

100% branch coverage · cognitive complexity ≤ 15 per function (complexipy) ·
`ruff` · `mypy --strict` · a supply-chain audit scoped to the production closure
rather than the development tree. Where a guard exists only for an environment
no test can produce, the code is restructured to be total instead of reaching
for a coverage pragma.
