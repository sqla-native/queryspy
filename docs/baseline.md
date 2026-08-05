# Baselines

Turning the gate on for the first time is the hard part of adopting any linter.
A suite that lights up in twenty places gets the gate switched back off, and then
nothing improves.

A baseline records what is already there, so the gate fails on **regressions**
from day one while the existing list gets worked down.

## Record what you have

```bash
pytest --queryspy-baseline=queryspy-baseline.json --queryspy-baseline-update
```

```
queryspy: wrote 23 baseline entries to queryspy-baseline.json
```

Updating is a **recording** run, not an enforcing one — it will not fail, even
with `--queryspy-strict`. Commit the file.

## Then turn the gate on

```bash
pytest --queryspy-baseline=queryspy-baseline.json --queryspy-strict
```

Those 23 are tolerated. Anything new fails the build.

## What counts as "the same finding"

Identity is `(kind, label, file, function)`. Three things are deliberately
**excluded**:

| Excluded | Why |
| --- | --- |
| Line number | An unrelated edit above shifts every finding down. Keying on the line would expire the whole baseline on a formatting change |
| Count | A bigger fixture turns 11 queries into 14. Same bug |
| Which test found it | Findings are attributed to the ORM call site, so two tests exercising the same helper are one entry |

That last one has a consequence worth being explicit about: **adding a test that
touches known-bad code is not a regression** and will not fail the build.
Changing the code so a *new* place has the problem is, and does.

## Pruning as you fix things

When a baselined finding stops occurring, queryspy says so:

```
queryspy: 2 baseline entries no longer occur:
  - lazy_load User.addresses at app/services/users.py:list_users()
  - column_load User at app/api/profile.py:get_profile()
  run with --queryspy-baseline-update to prune them
```

Stale entries are reported rather than removed automatically, because silently
dropping them would hide the one thing you want to see: that a fix landed.
Re-run with `--queryspy-baseline-update` when you are ready.

!!! warning "A baseline is not a budget"

    Baselines suppress **findings** only. `queryspy_budget` and
    `@pytest.mark.queryspy(max_queries=...)` are unaffected — a baselined N+1
    still counts toward how many queries a test is allowed to run.

## In CI

```yaml
- run: pytest --queryspy-baseline=queryspy-baseline.json --queryspy-strict
```

Pair it with a report so new findings still annotate the pull request:

```bash
pytest --queryspy-baseline=queryspy-baseline.json \
       --queryspy-strict \
       --queryspy-report=queryspy.sarif
```

Note that the report contains **all** findings, baselined or not — it is a
picture of the codebase, while the gate is a picture of the change.
