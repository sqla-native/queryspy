# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.3.0
[0.2.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.2.0
[0.1.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.1.0
