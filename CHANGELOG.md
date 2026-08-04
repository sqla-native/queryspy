# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/sqla-native/queryspy/releases/tag/v0.1.0
