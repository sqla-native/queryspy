# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.2.0
[0.1.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.1.0
