# GUIDELINES_QUERYSPY.md

## Core Philosophy — this library must be trustworthy enough to leave on

`queryspy` detects N+1 access patterns and enforces query budgets for
SQLAlchemy 2.0, sync and async. It is a **testing and diagnostics** tool, not a
query optimiser and not a profiler. It does not rewrite your queries, does not
change loader strategies, and does not run in production hot paths.

The library exists because `nplusone` — still the only thing anyone recommends
for this in Python — last shipped to PyPI in **May 2018**, predates SQLAlchemy
2.0 entirely, and worked by monkeypatching ORM internals. That last detail is
why it rotted, and it is the first rule below.

---

### 1. Architecture assumptions (never break these)

- **Public SQLAlchemy event API only. Never monkeypatch ORM internals.** This is
  the single rule that separates this library from the corpse of its
  predecessor. Detection is built on `do_orm_execute` /
  `before_cursor_execute`, both documented, both stable. If a feature seems to
  need reaching into `sqlalchemy.orm.*` privates, the feature is wrong, not the
  rule.

- **`lazy_loaded_from` is the ONLY valid lazy-load discriminator.** Measured on
  SQLAlchemy 2.0.51:

  | Scenario | `is_relationship_load` | `lazy_loaded_from` |
  |---|---|---|
  | true lazy load (sync and `awaitable_attrs`) | True | **set** |
  | `selectinload` | True | **None** |
  | `subqueryload` | True | **None** |

  `is_relationship_load` is True for the *fix* as well as the bug. Keying
  detection on it would flag correctly-written code. Any change to detection
  must re-run the spike matrix in `tests/test_false_positives.py` and
  `tests/test_detection.py`.

- **Column loads carry no `lazy_loaded_from`.** Deferred columns and
  post-commit attribute refreshes are detected on `is_column_load` alone, and
  attributed via `bind_mapper` plus the SQL template — `loader_strategy_path`
  is an empty `RootRegistry` for these.

- **The two measurement layers are never correlated.** `do_orm_execute`
  produces ORM records; `before_cursor_execute` produces the raw count. One ORM
  execute can yield several cursor executions (`selectinload` batching), and
  flushes reach the cursor hook without ever touching the ORM hook. They are
  reported as two clearly-labelled metrics. Stitching them together is exactly
  the fragility that killed `nplusone`.

- **Listeners are registered on the `Session` and `Engine` classes**, not on
  instances and not via `contextvars`. `AsyncSession` wraps a sync `Session`,
  so class-level registration covers async for free, and it sidesteps the open
  question of whether context variables propagate across SQLAlchemy's greenlet
  bridge. Every query during a test belongs to that test, so a per-window
  recorder stays correct even under `asyncio.gather`.

- **Listeners must be fully removable.** `stop()` removes them once the last
  window closes; `tests/test_lifecycle.py` asserts `event.contains` returns
  False afterwards, including when the recorded body raised.

- **Async attribution goes through the greenlet parent chain.** SQLAlchemy runs
  an async lazy load inside a spawned greenlet whose stack holds no application
  frames at all — only `strategies.py`, `session.py` and friends. Walking the
  live stack there returns nothing, so `await obj.awaitable_attrs.items` in a
  loop would report with no source line: the headline feature silently absent on
  the most common async pattern. `_frames.py` therefore falls back to
  `greenlet.getcurrent().parent.gr_frame` and walks from there.

  The `greenlet` import is **lazy and guarded**, and does not breach the
  single-dependency rule: `greenlet` is not declared anywhere in this project,
  it arrives with `sqlalchemy[asyncio]`, and it is only ever present in exactly
  the situation the fallback exists for. A sync-only install has no greenlet and
  degrades quietly to no fallback — which is covered by a test.

- Support line: **Python 3.10+, SQLAlchemy 2.0+**. `pytest` is an extra, never a
  dependency — `queryspy` must stay importable inside a production process.

### 2. A false positive is worse than a missed detection

A linter that flags correctly-written code gets uninstalled the same day. The
false-positive suite (`tests/test_false_positives.py`) therefore carries the
same weight as the detection suite, and both are release gates.

Every one of these must stay silent: a single query, `selectinload`,
`subqueryload`, `joinedload`, `undefer`, a relationship configured
`lazy="selectin"` loaded over a batch of parents, bulk inserts, and autoflush
INSERTs preceding a SELECT.

When in doubt, do not report. A missed N+1 costs the user some latency; a false
alarm costs the library its credibility.

### 3. Public API

- `record()` — the recording window; everything else is built on it.
- `assert_num_queries(n)` / `assert_max_queries(n)` — count **statements that
  reached the driver**, flushes included, which is what Django's
  `assertNumQueries` counts.
- `no_n_plus_one()` — fails on any finding.
- `Recorder.findings(threshold=...)` / `Recorder.query_count`.
- pytest: the `queryspy` fixture, `@pytest.mark.queryspy(...)`,
  `--queryspy-strict`, and the `queryspy_budget` / `queryspy_fail_on` /
  `queryspy_capture_stacks` ini options.

All assertion failures subclass `AssertionError` so pytest renders them like a
failed `assert`. A failing test body must never be masked by a queryspy
assertion — the body's own exception wins.

### 4. Implementation rules

- **Exactly one runtime dependency: `sqlalchemy>=2.0`.** Adding a second
  requires an explicit amendment to this document, argued on its own merits, in
  its own PR.
- The `fix:` line is not decoration. Every finding kind must suggest something a
  developer can paste. If a new kind cannot, it is not ready to ship.
- Stack capture is the largest per-query cost. It stays opt-out
  (`capture_stacks=False`, `queryspy_capture_stacks = false`).
- Keep the pytest wrapper inert when no policy asks for anything, so a suite
  that uses neither the marker nor the flag pays nothing.

### 5. Non-negotiable style

- **100% test coverage** (branch included) on `src/queryspy`, enforced by
  `coverage` with `fail_under = 100`.
- **Cognitive complexity ≤ 15 per function**, enforced by **complexipy**. It is
  a native binary with no dependency on a type checker, so the lint gate can
  never be held hostage by a parser's release cycle — the same reasoning that
  moved `@nest-native/jobs` from ESLint to Biome.
- `ruff check`, `ruff format --check`, and `mypy --strict` are all release gates.
- Prefer designing a branch out over contorting a test to reach it. Where a
  guard exists only for an environment no test can produce, restructure the code
  to be total instead (see `_library_roots` using a set intersection).

### 6. Strictness scope

The non-negotiables above — 100% coverage, complexity ≤ 15, the single runtime
dependency — govern the **core package** (`src/queryspy`). Non-core code
(`examples/`, `scripts/`, docs, dev tooling) runs lighter rules: dependency
updates there may merge on green CI without ceremony.

### 7. Security and supply chain (MANDATORY)

- Every PR includes a supply-chain pass.
- **Audit scope:** the release gate audits the *published* surface. Build the
  wheel, install it into a clean virtualenv, and `pip-audit` that closure — the
  production closure is `queryspy` plus SQLAlchemy, which is exactly what
  consumers install. Advisories confined to dev tooling or the examples are
  Dependabot's job and do not block a release.
- No secrets in code, tests, examples, logs, or docs.

### 8. Releasing

Bump `version` in `pyproject.toml` **and** `__version__` in
`src/queryspy/__init__.py` to the same value, then tag `vX.Y.Z` and push the
tag. `.github/workflows/release.yml` publishes to PyPI via **Trusted
Publishing** (OIDC — no token, no secret). Verify the version appears on PyPI
and that the tag points at the release commit.

---

## Local verification (mutation testing)

Mutation testing is an **occasional, targeted audit — not a per-PR gate, and
never in CI.** Run it deliberately after reworking a file's logic, to find out
whether its tests actually pin the behaviour.

- Scope it: `mutmut run --paths-to-mutate src/queryspy/_detect.py`. Never run it
  across the whole package — the runner re-executes the suite per mutant.
- **Verify a kill without re-running mutmut.** Hand-apply the surviving mutation
  to the source, run the plain suite, confirm your new test fails, then
  `git checkout --` to revert. This decouples the slow "find survivors" step
  from a fast "prove the kill" step.
- Treat each survivor by the doctrine: add a test that kills it; delete or
  simplify redundant code whose mutant is behaviourally equivalent (with a
  CHANGELOG note); or, for a genuine equivalent, leave a comment saying so.
- Machine constraint: never run two mutation sessions concurrently.
