# queryspy

**N+1 and query-budget detection for SQLAlchemy 2.0 — sync and async.**

Django developers get `assertNumQueries`, `nplusone`, and the Debug Toolbar.
SQLAlchemy developers get told to write their own `before_cursor_execute`
listener. queryspy is the missing piece: point it at the test suite you already
have and it names the line of your code that fires an N+1, with a fix you can
paste.

```
N+1 detected: 11 queries for User.addresses (lazy load)
  triggered from app/services/users.py:28 in list_users()
  SELECT address.id AS address_id, address.email AS address_email, ...
  fix: .options(selectinload(User.addresses))
```

## Install

```bash
pip install queryspy
```

One runtime dependency: SQLAlchemy. The pytest plugin registers itself.

## Three ways to use it

<div class="grid cards" markdown>

-   __In your test suite__

    Fail any test that triggers an N+1.

    ```bash
    pytest --queryspy-strict
    ```

    [:octicons-arrow-right-24: pytest guide](pytest.md)

-   __In your running app__

    A per-request query panel for FastAPI, Starlette or Litestar.

    ```python
    app.add_middleware(QuerySpyMiddleware)
    ```

    [:octicons-arrow-right-24: ASGI guide](asgi.md)

-   __In CI__

    Annotate pull requests through GitHub code scanning.

    ```yaml
    - uses: sqla-native/queryspy@v0
    ```

    [:octicons-arrow-right-24: CI guide](ci.md)

</div>

## Why this exists

[`nplusone`](https://github.com/jmcarp/nplusone) is still the answer everyone
gives, and it last shipped to PyPI in **May 2018**. It predates SQLAlchemy 2.0,
has no async support, and worked by monkeypatching ORM internals — which is why
it stopped working rather than being ported. Everything adjacent is stale too:
`sqltap` (2019), `sqlalchemy-easy-profile` (2023).

Meanwhile FastAPI plus async SQLAlchemy became the default modern Python
backend, with effectively no tooling of its own.

SQLAlchemy 2.0 added the `do_orm_execute` event and an `ORMExecuteState` that
exposes lazy-load attribution as public API. queryspy is built entirely on
that — no patching, no private imports.

!!! info "Not a profiler"

    queryspy is a testing and diagnostics tool. It does not change loader
    strategies, rewrite queries, or optimise anything. It tells you where the
    problem is and what to paste; the fix is yours.

## Requirements

Python 3.10+, SQLAlchemy 2.0+.

Tested against **SQLite, PostgreSQL and MySQL**, sync and async (`psycopg`,
`asyncpg`, `pymysql`, `aiomysql`, `aiosqlite`), on Python 3.10 through 3.14.

The detectors, source attribution and timing are all exercised against real
database servers in CI, not only SQLite — and that job fails if the specs skip,
because a skipped suite reporting green looks like coverage that is not there.
