# ASGI middleware

A per-request query panel for the terminal. FastAPI, Starlette and Litestar have
never had a Django-Debug-Toolbar equivalent for SQL; this is the smallest useful
version of one.

Pure ASGI3 — it speaks the protocol directly, with no framework dependency and
no new packages.

## Mount it

=== "FastAPI / Starlette"

    ```python
    from queryspy.asgi import QuerySpyMiddleware

    app.add_middleware(QuerySpyMiddleware, budget=10)
    ```

=== "Any ASGI app"

    ```python
    from queryspy.asgi import QuerySpyMiddleware

    app = QuerySpyMiddleware(app, budget=10)
    ```

Every request now logs its query count, and any request that trips a detector
logs the full report:

```
WARNING queryspy: GET /projects - 12 queries in 41.3ms (38.9ms in the database, 31.2ms of it in one statement:
  SELECT task.id, task.title FROM task WHERE :param_1 = task.project_id)

N+1 detected: 11 queries for Project.tasks (lazy load)
  triggered from app/api/projects.py:31 in list_projects()
  SELECT task.id AS task_id, task.title AS task_title ...
  fix: .options(selectinload(Project.tasks))
```

## Options

| Argument | Default | Effect |
| --- | --- | --- |
| `budget` | `None` | Warn when a request exceeds this many statements |
| `threshold` | `2` | Repeats required before something is a finding |
| `capture_stacks` | `True` | Attribute findings to your source lines |
| `add_headers` | `True` | Add `x-queryspy-queries` / `x-queryspy-findings` |
| `logger` | `logging.getLogger("queryspy")` | Where reports go |
| `on_report` | `None` | Receive every `RequestReport` yourself |
| `panel` | `False` | Serve the debug panel |
| `panel_path` | `/__queryspy__` | Where to serve it |
| `history` | `50` | How many requests the panel remembers |

## The panel

The piece FastAPI has never had an equivalent of:

```python
app.add_middleware(QuerySpyMiddleware, panel=True)
```

Then open `/__queryspy__`. Recent requests, their query counts, how much of the
wall-clock time was actually spent in the database, and every finding with its
source line and fix — expandable per request.

A single self-contained HTML page: no CDN, no external stylesheet, nothing
fetched from anywhere. It works offline and behind a firewall.

!!! danger "Off by default, and it should stay off in production"

    The panel renders SQL. Bind parameters are **never** captured anywhere in
    queryspy — a recorded statement keeps its `:param_1` placeholders — so it
    shows query *shapes*, never values. That is what makes it safe to look at in
    staging. It still describes your schema in detail, and it is unauthenticated:
    mount it where your other debug surfaces live, or gate it behind whatever you
    already use.

    History is bounded (`history=50`) so a long-running process does not grow a
    buffer forever.

## Routing reports somewhere else

```python
def to_metrics(report):
    statsd.gauge("db.queries", report.query_count, tags=[f"path:{report.path}"])
    if not report.clean:
        statsd.increment("db.n_plus_one")


app.add_middleware(QuerySpyMiddleware, on_report=to_metrics)
```

`RequestReport` carries `method`, `path`, `query_count`, `findings`,
`duration_ms` (wall clock), `db_duration_ms` (time actually in the driver),
`slowest`, a `clean` property, and `render()` for the human-readable form.

Timing is reported per **request**, never per finding. Attributing milliseconds
to a specific finding would mean correlating the ORM and cursor layers, and that
correlation is the one thing the recorder refuses to do — see the
[design notes](design.md). Often the headline is not the N+1 at all: twelve
queries where one takes 31 of the 39 milliseconds is a slow query wearing an
N+1's clothes.

## Behaviour worth knowing

**Concurrent requests are isolated.** The recorder is scoped to a context
variable, not a module global, so interleaved requests never record each other's
queries. This holds for async lazy loads too, which run inside a greenlet that
SQLAlchemy spawns — context variables propagate across that bridge.

**Failed requests still report.** The report is emitted from a `finally`, so a
request that raised — exactly the one whose queries you want to see — is not
skipped.

**Headers reflect response-start.** Headers must be written with
`http.response.start`, which for a streaming response happens *before* the body
runs. The header is the count at that moment; the log line is emitted after the
request completes and is always final.

!!! warning "Development and staging"

    This records every request, and stack capture is not free. It is built for
    environments where you are looking at the logs. If you want it further on,
    run it with `capture_stacks=False` and route `on_report` at your metrics
    system rather than the logger.
