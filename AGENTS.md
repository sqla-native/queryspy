# Agent Guidance

`queryspy` detects N+1 access patterns and enforces query budgets for
SQLAlchemy 2.0, sync and async.

**[GUIDELINES_QUERYSPY.md](GUIDELINES_QUERYSPY.md) is binding.** Read it before
changing anything under `src/queryspy`. The rules there are not style
preferences; several encode measured SQLAlchemy behaviour that is easy to get
wrong and expensive to get wrong.

## The three that bite hardest

1. **Never monkeypatch SQLAlchemy internals.** Public event API only. The
   library this one replaces died of exactly that.
2. **`lazy_loaded_from` — never `is_relationship_load` — is the lazy-load
   discriminator.** `is_relationship_load` is True for `selectinload` and
   `subqueryload` too, so keying on it flags the *fix* as the bug.
3. **A false positive is worse than a missed detection.**
   `tests/test_false_positives.py` is a release gate, equal in weight to the
   detection suite. When in doubt, do not report.

## Before opening a PR

```bash
./scripts/verify.sh          # everything hermetic. ~1 minute.
./scripts/verify.sh --all    # adds the real-database suite. Needs Docker.
```

[TESTING.md](TESTING.md) explains the nine layers, what each one proves, and
which to run for what you changed. Report the results in the PR body.

Coverage must be 100% including branches. Do not lower `fail_under`, do not add
`# pragma: no cover` to reach it, and do not weaken a gate to make a change fit
— restructure the code instead.

Mutation testing (`mutmut`) is an occasional local audit, never a per-PR step
and never in CI — see TESTING.md, including the results of the first audit and
why a test is not a kill until you have watched it fail.

## Real databases

The default suite runs on SQLite. That is a reasonable default and a bad place
to stop: Postgres and MySQL differ in parameter style, in the DBAPI, and - for
async - in how the driver interacts with greenlets.

```bash
docker compose up -d --wait
./scripts/test-integration.sh
docker compose down -v
```

Those specs skip when the URLs are unset, so a clone runs green without Docker.
CI runs them with service containers and **fails if they skip** - a skipped
suite reporting green looks like coverage that is not there.

Run them before any PR that touches `_recorder.py`, `_detect.py` or `_frames.py`.

## Changing detection

Any change to `_detect.py` or `_recorder.py` must keep both suites passing
unchanged, and must not add a finding kind without a corresponding actionable
`fix:` hint in `_hints.py`.

If you need to check how SQLAlchemy actually behaves, write a throwaway script
and measure it. Do not reason from memory about which flags are set — the
distinction between `selectinload` and a real lazy load is invisible in the
docs and only shows up when you run it.

## Environment

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e . --group dev
```
