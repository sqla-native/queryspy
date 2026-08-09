# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-08-09

### Fixed

- **Reporting could break the request it was observing.** The middleware reports
  from a `finally` around the application call, and nothing guarded it. A
  user-supplied `on_report` callback that raised — or a misconfigured logging
  handler — would fail a request that was otherwise healthy, and, worse,
  *replace* the application's own exception with the diagnostics one, destroying
  the traceback the developer needed.

  Reporting can no longer raise. Failures are logged with their own traceback
  rather than swallowed silently; if the logger is what broke, there is nowhere
  left to report it and the request still wins. Applies to both the ASGI and
  WSGI middleware.

  The pytest plugin always had this property — "a failing test body always wins"
  — and it simply had never been extended to the middleware.

### Added

- Dependabot for dev tooling and GitHub Actions. Nothing here reaches consumers
  (the production closure is SQLAlchemy alone), but an unmaintained toolchain
  rots quietly.

## [Unreleased]

No library code changed, so there is nothing to release. What changed is the
evidence that the library works.

### Testing

- **Real databases.** `tests/integration` runs the three detectors, source
  attribution, timing and the false-positive gate against **PostgreSQL and
  MySQL**, sync and async (`psycopg`, `asyncpg`, `pymysql`, `aiomysql`) — on top
  of the SQLite suite everything used to rest on. SQLite is in-process, uses `?`
  parameters and has its own driver, so "it works on SQLite" was never evidence
  that it worked anywhere else. The specs skip when the URLs are unset, so a
  clone still runs green without Docker; CI runs them with service containers
  and **fails if they skip**.
- **Unusual mappings and hand-edited input** (`tests/test_robustness.py`):
  nested relationship paths, self-referential trees, joined-table inheritance,
  two engines in one window, Core-level execution, an empty window,
  `threshold=1`, corrupt and partial baseline files, and a panel rendering fifty
  findings.
- **All three detectors now have end-to-end examples.** Previously only
  `lazy_load` did — including `repeated_statement`, which is the shape no
  relationship-load hook can see and therefore the strongest argument for the
  library.
- **Streaming WSGI responses, timing and thread concurrency** are exercised
  against the real Flask app, not only in unit tests.
- **The examples dogfood the gate they document.** Their deliberate N+1s live in
  a committed baseline and CI runs them under `--queryspy-strict`, so detection
  regressing in either direction — a new finding, or a known one disappearing —
  fails the build.
- A committed `compose.yaml` and `scripts/test-integration.sh` for running the
  gated suite locally.

### Documented behaviour that was previously only implicit

- On joined-table inheritance a finding names the mapper that **declares** the
  relationship (`Employee.notes`), not the one queried (`Manager`). That is
  correct — `selectinload(Employee.notes)` is the option that fixes it — and it
  is now asserted, including that the suggested fix actually resolves the
  finding.
- `QueryRecord.sql` is compiled with the **default** dialect, never the
  backend's, so a finding's identity and therefore a baseline entry stay stable
  across SQLite, Postgres and MySQL. Backend placeholder styles (`%(id_1)s`,
  `%s`, `?`) must never leak into it.

## [0.4.0] - 2026-08-08

Closes the two gaps against `nplusone` that were worth closing. The others —
unused eager-load detection, Django and Peewee support — remain declined, for
the reasons in the design notes.

### Added

- **WSGI middleware** (`queryspy.wsgi.QuerySpyMiddleware`) for Flask and any
  other WSGI application. Same options and same panel as the ASGI middleware.
  This is the half of `nplusone`'s audience the ASGI middleware could not reach.

  A WSGI app returns an *iterable*, and for a streaming response the queries
  keep coming after `__call__` has returned — so the recording window stays open
  and the returned iterable is wrapped. The report is emitted when it is
  exhausted or closed, whichever comes first, which means streaming responses
  are counted in full rather than reporting the one query issued before the body
  started.

- **`queryspy.ignore()`** — suppress findings for a block that is deliberately
  doing what queryspy would otherwise flag. Queries inside it are **still
  counted**: a query that ran, ran, and a budget that quietly under-reported
  would be worse than no budget. Nests, works outside a recording window, and
  follows the work across SQLAlchemy's greenlet bridge. Blunt by design — for
  tolerating specific known findings use a baseline, for tolerating more round
  trips raise the threshold.

- A Flask example under `examples/flask_app/`, asserted in CI like the others.

### Changed

- `RequestReport` moved to `queryspy._request` and the shared middleware
  behaviour to `queryspy._middleware`, so the two protocol modules hold only
  their own plumbing. `from queryspy.asgi import RequestReport` is unchanged.

## [0.3.0] - 2026-08-05

### Added

- **Debug panel** — the ASGI middleware can serve a self-contained HTML page at
  `/__queryspy__` (`panel=True`): recent requests, query counts, database time,
  and every finding with its source line and fix. No CDN, no external
  stylesheet, nothing fetched from anywhere. Off by default, with bounded
  history. It renders query *shapes* only — bind parameters are never captured
  anywhere in queryspy, so values cannot appear.
- **Query timing** — `Recorder.db_duration_ms` and `Recorder.slowest` measure
  time actually spent in the driver. Surfaced in query-count failures, in the
  middleware's log line and `RequestReport`, and in the panel. Reported per
  *window*, never per finding: attributing milliseconds to a finding would mean
  correlating the ORM and cursor layers, which the recorder refuses to do.
  Deliberately **not** a gate — wall-clock assertions are flaky in CI.
- **Baselines** — `--queryspy-baseline=PATH` tolerates known findings so the
  gate fails only on new ones, and `--queryspy-baseline-update` records them.
  Identity is `(kind, label, file, function)`, excluding line number, count and
  which test found it, so a baseline survives unrelated edits. Entries that stop
  occurring are reported rather than silently dropped.
- `scripts/benchmark.py`, committed so the performance claims in the docs stay
  checkable.

### Changed

- **Statement rendering is deferred**, cutting recording overhead from roughly
  172% to ~40% of baseline — queries go from 2.7x slower to about 1.4x.
  `str(state.statement)` is a full compile and used to run on every recorded
  query; `QueryRecord.sql` is now a `cached_property`, so only unclaimed records
  and the handful that become findings pay for it.

### Fixed

- **The docs named the wrong bottleneck.** The README, pytest guide and
  constitution all claimed stack capture was the largest per-query cost.
  Measured, it was around 9%; statement rendering was roughly half. Corrected,
  and the benchmark is now committed so the claim can be re-checked.
- Stale-baseline output said "1 baseline entry no longer occur".

## [0.2.0] - 2026-08-04

### Added

- **ASGI middleware** (`queryspy.asgi.QuerySpyMiddleware`) — a per-request query
  panel for FastAPI, Starlette and Litestar. Pure ASGI3, no framework
  dependency, no new packages. Reports counts and findings per request, with
  optional `x-queryspy-queries` / `x-queryspy-findings` headers and an
  `on_report` hook for routing to metrics.
- **Machine-readable reports** — `--queryspy-report=PATH` writes the whole
  session as JSON or SARIF (`to_json` / `to_sarif` are public). SARIF uploads to
  GitHub code scanning, putting each N+1 on the line of the pull request that
  causes it. Requesting a report collects without enforcing, and is written even
  when the run fails.
- **GitHub Action** (`action.yml`) — runs pytest with the report flag, uploads
  the SARIF, and preserves the pytest exit code. It deliberately does not
  install Python or your dependencies.
- **Documentation site** at <https://sqla-native.github.io/queryspy/>.
- `Finding.origin`, carrying the test node id into reports.

### Changed

- **Recording is now scoped to a context variable rather than a module global.**
  A global is correct for tests, where one window is open at a time, and wrong
  for a concurrent server: interleaved requests each recorded every other
  request's queries. Context copies at task creation mean a window still covers
  the tasks it spawns, while a window opened inside one request stays invisible
  to the others. Verified that context variables propagate across SQLAlchemy's
  greenlet bridge, so async lazy loads land in the window that caused them.
  Listener registration is refcounted separately, under a lock, because it is
  genuinely global.

### Not shipped

- **Unused eager-load detection**, deliberately. Measured: `AttributeEvents`
  exposes only mutation hooks with no read/get/access event,
  `InstanceState.unloaded` is empty after an eager load, and reading an
  attribute does not alter `state.dict`. The only routes are patching
  `InstrumentedAttribute.__get__` — precisely what killed `nplusone` — or taking
  over the user's entire model instrumentation. Declined rather than smuggled
  in; see the design notes.
- **Django ORM support.** Out of scope for `sqla-native`, and Django is the one
  ecosystem that already has working tools.

## [0.1.0] - 2026-08-04

Initial release.

### Added

- Recording window (`record()`) built entirely on SQLAlchemy 2.0's public
  `do_orm_execute` and `before_cursor_execute` events — no monkeypatching.
- Three detectors: `lazy_load` (keyed on `lazy_loaded_from`), `column_load`
  (deferred columns and post-commit refreshes), and `repeated_statement` (the
  strategy-agnostic backstop that catches `session.get()` loops).
- Source attribution: each finding names the line of your own code that
  triggered it, with an actionable `fix:` suggestion. Async lazy loads run
  inside a spawned greenlet with no application frames on its stack, so
  attribution walks the greenlet parent chain to recover the caller.
- Assertions: `assert_num_queries`, `assert_max_queries`, `no_n_plus_one`.
- pytest plugin: the `queryspy` fixture, `@pytest.mark.queryspy(...)`,
  `--queryspy-strict`, and the `queryspy_budget` / `queryspy_fail_on` /
  `queryspy_capture_stacks` ini options.
- Async support with no extra setup — `AsyncSession` wraps a sync `Session`,
  and listeners are registered on the class.

[0.4.1]: https://github.com/sqla-native/queryspy/releases/tag/v0.4.1
[0.4.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.4.0
[0.3.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.3.0
[0.2.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.2.0
[0.1.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.1.0
