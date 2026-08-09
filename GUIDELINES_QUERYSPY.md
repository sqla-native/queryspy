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
  instances. `AsyncSession` wraps a sync `Session`, so class-level registration
  covers async for free and users pass nothing.

- **Which recorders receive a record is scoped to a context variable, never a
  module global.** A global is correct for tests, where one window is open at a
  time, and wrong for a concurrent server: interleaved requests would each
  record every other request's queries. `asyncio` copies the context at task
  creation, so a window still covers the tasks it spawns, while a window opened
  inside one request stays invisible to every other request.

  Measured: context variables **do** propagate across SQLAlchemy's greenlet
  bridge, so an async lazy load lands in the window that caused it. Listener
  *registration* is refcounted separately, under a lock, because registration is
  genuinely global while the recorder set is not.

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

### 3. Declined by measurement, not by omission

**Unused eager-load detection is a non-goal.** Detecting `selectinload` on a
relationship that is never read would be genuinely useful, and it cannot be done
through public API. Measured on SQLAlchemy 2.0.51:

- `AttributeEvents` exposes only mutation hooks — `append`, `remove`, `set`,
  `bulk_replace`, `init_collection` — and **no** read, get or access event
- `InstanceState.unloaded` is empty after an eager load, by definition
- reading an attribute does not alter `state.dict`

The only remaining routes are patching `InstrumentedAttribute.__get__`, which is
exactly what killed the predecessor, or taking over the user's entire model
instrumentation through `sqlalchemy.ext.instrumentation` — more invasive than
the patch it would replace, and it changes the semantics of the user's own
application. Declined rather than smuggled in. If SQLAlchemy ever grows a read
event, this becomes a small feature; re-run the measurement before assuming it
has not.

**Django ORM support is a non-goal.** This org is `sqla-native`, and Django is
the one ecosystem that already has working tools.

### 4. Public API

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

### 5. Implementation rules

- **Exactly one runtime dependency: `sqlalchemy>=2.0`.** Adding a second
  requires an explicit amendment to this document, argued on its own merits, in
  its own PR.
- The `fix:` line is not decoration. Every finding kind must suggest something a
  developer can paste. If a new kind cannot, it is not ready to ship.
- **Know where the cost actually is, by measuring it.** `scripts/benchmark.py`
  is committed for exactly this. Until 0.3 these guidelines asserted that stack
  capture was the largest per-query cost; measurement showed otherwise —
  rendering the statement was, at roughly half of all recording overhead, and
  recording made queries 2.7x slower. Rendering is now deferred until a record
  is reported (`QueryRecord.sql` is a `cached_property`), which took overhead to
  ~40% of baseline. Stack capture is about a quarter of what remains and stays
  opt-out (`capture_stacks=False`, `queryspy_capture_stacks = false`). Re-run
  the benchmark before making a performance claim in the docs.
- Keep the pytest wrapper inert when no policy asks for anything, so a suite
  that uses neither the marker nor the flag pays nothing.

### 6. Evidence, not assumption

A green suite on one backend, one schema shape and well-formed input is not
evidence that the library works. Every layer this project touches is close
enough to SQLAlchemy's internals that "it should be fine" has already been wrong
several times.

Concretely, all of these are release gates:

- **Real databases.** `tests/integration` runs the detectors, attribution and
  timing against Postgres and MySQL, sync and async, on top of the SQLite suite.
  CI fails if they skip.
- **Unusual mappings.** `tests/test_robustness.py` covers nested relationship
  paths, self-referential trees, joined-table inheritance, several engines in
  one window, Core-level execution and hand-edited baseline files.
- **The examples dogfood the gate.** Their deliberate N+1s live in a committed
  baseline and CI runs them under `--queryspy-strict`, so detection regressing
  in *either* direction - a new finding, or a known one disappearing - fails.
- **Mutation testing, occasionally.** 100% coverage says a line ran, not that an
  assertion would notice it changing. The first audit scored 77% and found seven
  real gaps in the detection core, including that "findings are worst first" was
  documented in four places and asserted in none. See `TESTING.md`.

When adding a feature, ask what shape of schema, backend or input would break it
and add that case. When a claim goes in the docs, add the thing that proves it.

And when you write a test to close a gap, **watch it fail first**. One of the
seven above passed against its own mutant on the first attempt, because the
scenario produced a single finding and sorting one element cannot go wrong. A
test is not a kill until you have seen it red.

### 7. Non-negotiable style

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

### 8. Strictness scope

The non-negotiables above — 100% coverage, complexity ≤ 15, the single runtime
dependency — govern the **core package** (`src/queryspy`). Non-core code
(`examples/`, `scripts/`, `docs/`, dev tooling) runs lighter rules: dependency
updates there may merge on green CI without ceremony.

### 9. Security and supply chain (MANDATORY)

- Every PR includes a supply-chain pass.
- **Audit scope:** the release gate audits the *published* surface. Build the
  wheel, install it into a clean virtualenv, and `pip-audit` that closure — the
  production closure is `queryspy` plus SQLAlchemy, which is exactly what
  consumers install. Advisories confined to dev tooling or the examples are
  Dependabot's job and do not block a release.
- No secrets in code, tests, examples, logs, or docs.

### 10. Releasing

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
