# Examples

Two services with the same deliberate N+1 — one async on FastAPI, one sync on
Flask — and tests that assert every queryspy feature against them. These run in
CI; they are validation, not decoration.

```bash
uv pip install -r requirements.txt
pytest examples -q
```

## What is in here

Each app exposes the same data two ways:

| Endpoint | Queries | |
| --- | --- | --- |
| `GET /projects` | 4 | The bug — one query per project |
| `GET /projects-fixed` | 2 | `selectinload`, flat regardless of project count |

The app mounts `QuerySpyMiddleware`, so both endpoints report themselves.

## What the tests prove

| File | Validates |
| --- | --- |
| `fastapi_app/test_endpoints.py` | The N+1 is detected; the fixed endpoint holds a 2-query budget |
| `fastapi_app/test_middleware.py` | ASGI counts, response headers, source attribution, **and concurrent-request isolation** |
| `fastapi_app/test_reporting.py` | SARIF points code scanning at the offending line; JSON carries the fix |
| `fastapi_app/test_panel.py` | The panel reflects real traffic, leaks no parameter values, and fetches nothing externally |
| `fastapi_app/test_detectors.py` | **All three detectors** end to end — `lazy_load`, `column_load`, `repeated_statement` |
| `flask_app/test_flask.py` | The **WSGI** middleware on a real Flask app, streaming responses, timing, thread concurrency, the panel, and `ignore()` |

`test_endpoints.py::test_list_projects_has_no_n_plus_one` is an
`xfail(strict=True)`: it is *expected* to fail, because the endpoint really does
have the bug. Apply the fix queryspy prints and the test flips to passing — and
the strict xfail then fails the suite, telling you to update it. That is the
whole demo in one test.

## Seeing it for yourself

```bash
pytest examples --runxfail -q
```

```
N+1 detected: 3 queries for Project.tasks (lazy load)
  triggered from examples/fastapi_app/app.py:91 in list_projects()
  SELECT task.id AS task_id, task.title AS task_title ...
  fix: .options(selectinload(Project.tasks))
```

## The examples dogfood the gate

Their deliberate N+1s are recorded in `queryspy-baseline.json`, and CI runs:

```bash
pytest examples --queryspy-strict --queryspy-baseline=examples/queryspy-baseline.json
```

That fails if detection regresses in **either** direction — a new finding
appears, or a known one stops being found and shows up as a stale entry. It is
the closest thing to a real user's workflow that can run in our own CI.

This directory sits outside the core package's strictness scope — see
`GUIDELINES_QUERYSPY.md` section 8.
