# Testing procedure

Nine layers, each answering a question the one before it cannot. The point of
writing them down is that "the tests pass" is not a claim until you know which
tests, on what, proving what.

| # | Layer | Question it answers | Where it runs |
| --- | --- | --- | --- |
| 1 | Unit + robustness | Does the logic do what it says on ordinary and unusual input? | every run |
| 2 | Coverage (100% branch) | Did every branch execute? | every run |
| 3 | Types, lint, complexity | Is it the kind of code we agreed to keep? | every run |
| 4 | Real databases | Does it work anywhere other than SQLite? | CI + on demand |
| 5 | Examples | Does it work against a real app, end to end? | CI + on demand |
| 6 | The examples' own gate | Has detection regressed in either direction? | CI + on demand |
| 7 | Packaging | Does what we publish work for someone who installs it? | CI + release |
| 8 | Docs | Do the links and pages still build? | CI + release |
| 9 | Mutation | Are the tests any *good*, or merely numerous? | occasional, local |

## The short version

```bash
./scripts/verify.sh          # layers 1-3, 5-8. No Docker. ~1 minute.
./scripts/verify.sh --all    # adds layer 4. Needs Docker.
```

Run `--all` before any PR touching `_recorder.py`, `_detect.py` or `_frames.py`.

## What to run for what you changed

| You changed | Run |
| --- | --- |
| Detection (`_detect`, `_recorder`, `_frames`) | `verify.sh --all`, then a scoped mutation run |
| A middleware (`asgi`, `wsgi`, `_middleware`) | `verify.sh` |
| The pytest plugin | `verify.sh` |
| Output (`_report`, `_serialize`, `_panel`, `_hints`) | `verify.sh` |
| Anything in the hot path | `verify.sh` **plus** `python scripts/benchmark.py` |
| Docs only | `mkdocs build --strict` |

---

## 1-3. The default run

```bash
coverage run -m pytest && coverage report   # 100% branch, enforced
ruff check . && ruff format --check .
mypy
complexipy src/queryspy --max-complexity-allowed 15
```

Hermetic: SQLite in-memory, no Docker, no network. A fork clones and gets green.

Coverage is 100% **including branches** and is not negotiable. Do not lower
`fail_under`, do not reach for `# pragma: no cover`, and do not weaken a gate to
fit a change — restructure the code so the branch does not exist. There are two
examples of that in the codebase (`_library_roots` uses a set intersection;
`capture_app_frame` uses one loop over two frame sources).

## 4. Real databases

```bash
docker compose up -d --wait
./scripts/test-integration.sh
docker compose down -v
```

Postgres and MySQL, sync and async, across `psycopg`, `asyncpg`, `pymysql` and
`aiomysql`. SQLite is in-process, uses `?` parameters and has its own driver, so
a green SQLite suite is evidence about SQLite.

These skip when the URLs are unset. **CI fails if they skip** — a skipped suite
reporting green looks like coverage that is not there.

## 5-6. Examples

```bash
pytest examples -q
pytest examples -q --queryspy-strict --queryspy-baseline=examples/queryspy-baseline.json
```

The examples are validation, not decoration: they assert middleware counts,
response headers, source attribution, concurrent-request isolation, streaming
WSGI responses, timing, the panel and all three detectors against real FastAPI
and Flask apps.

The second command is the interesting one. The examples' deliberate N+1s live in
a committed baseline, so it fails if detection regresses in **either**
direction — a new finding appears, or a known one stops being found and shows up
as stale. CI greps for `no longer occur` to catch the second case, which would
otherwise pass silently.

When you deliberately change what the examples do, regenerate:

```bash
pytest examples -q --queryspy-baseline=examples/queryspy-baseline.json \
                   --queryspy-baseline-update
```

## 7. Packaging

```bash
python scripts/check_packed_consumer.py     # wheel -> clean venv -> real use
python scripts/audit_production_surface.py  # pip-audit the production closure
```

The first builds the wheel, installs it into a throwaway virtualenv with none of
the dev tooling, and uses it the way a consumer would — including checking
`py.typed` shipped and the `pytest11` entry point resolves. Neither is provable
from the source tree.

The second audits **what consumers install** (queryspy plus SQLAlchemy), not the
development tree. Advisories in dev tooling are Dependabot's job and do not
block a release.

## 8. Docs

```bash
mkdocs build --strict
```

`--strict` turns a broken link or an orphaned page into a failure rather than a
warning nobody reads.

## 9. Mutation testing

**An occasional, targeted audit. Not a per-PR gate, and never in CI.**

100% coverage proves every line *ran*. It says nothing about whether an
assertion would notice if the line changed. Mutation testing is the only layer
that answers that.

```bash
mutmut run --max-children 2 "src.queryspy._detect.*"
mutmut results
mutmut show <mutant>
```

Scope every run to the one or two files whose logic you reworked. The runner
re-executes the suite per mutant, so a whole-package run is slow to impractical.
`--max-children 2` is deliberate: this machine is short on RAM, and two
concurrent runs are never acceptable.

**Verify a kill without re-running mutmut.** Hand-apply the surviving mutation
to the source, run the plain suite, confirm your new test fails, then
`git checkout --` to revert. That decouples the slow "find survivors" step from
a fast "prove the kill" step.

Treat each survivor by the doctrine:

- add a test that kills it; or
- delete or simplify code whose mutant is behaviourally equivalent, with a
  CHANGELOG note; or
- for a genuine equivalent, leave a comment saying so and why.

A survivor is information either way. Some of them mean the test suite has a
hole; some mean the code has a branch that does not need to exist.

### The 2026-08-09 audit

First full run: **1,547 mutants, 1,190 killed, 356 survived, 1 timeout — a 77%
score** against a suite at 100% branch coverage. That gap is the whole argument
for this layer.

Seven survivors were real gaps in the detection core and were fixed
(`tests/test_mutation_gaps.py`, which names the mutant each test kills):

| Mutation | What it proved was unchecked |
| --- | --- |
| `-f.count` → `+f.count` | "Findings are worst first" is claimed in the README, the API reference and three docstrings. Nothing asserted it. |
| `continue` → `break` in `_collect` | A group below the threshold must be skipped, not stop the scan. One harmless early query would have suppressed every later finding. |
| `* 1000` → `/ 1000` in timing | Every timing test asserted only `> 0`, which holds in any unit. A conversion wrong by a factor of a million would have shipped. |
| `or` → `and` in `_lazy_key` | The fallback chain when a lazy load has no path — would have collapsed unrelated relationships into one group. |
| `or` → `and` in `_column_key` | Same, for column loads. |
| `entity` → `None` in `_column_hint` | All four mutants of this function survived: the hint was rendered in tests but never actually read. |
| `slowest` → `None` in `_detail` | The dominant-statement line in a count failure was rendered and never asserted. |

The remaining ~349 survivors were triaged and deliberately left. They cluster in
`_panel` (HTML attribute strings), `_serialize` (JSON key names), and the
`__init__`/config plumbing of the middlewares — places where a mutant changes
output cosmetically and pinning it would mean asserting on exact markup, which
makes tests brittle without making the library safer. Revisit if any of that
output becomes a contract someone depends on.

**Verify every fix by hand.** After adding a test, hand-apply the mutation, run
the suite, and confirm it fails. Six of the seven above passed that check
immediately; the seventh did not, and the reason is worth remembering — the
first "worst first" test used two real relationships but the second lazy load
hit the identity map, so only one finding was produced. Sorting a one-element
list cannot fail, so the test passed against the mutant and proved nothing. A
test written for a survivor is not a kill until you have watched it fail.

## Benchmarks

```bash
python scripts/benchmark.py
```

Best-of-7, because a single run swung from 14% to 0.9% on consecutive attempts
and was useless for publishing a number. Re-run it before making any performance
claim in the docs — the docs blamed the wrong bottleneck in three files once
already, and the benchmark is committed so that stays checkable.
