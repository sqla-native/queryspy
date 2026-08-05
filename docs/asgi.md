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
WARNING queryspy: GET /projects - 12 queries in 41.3ms

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

## Routing reports somewhere else

```python
def to_metrics(report):
    statsd.gauge("db.queries", report.query_count, tags=[f"path:{report.path}"])
    if not report.clean:
        statsd.increment("db.n_plus_one")


app.add_middleware(QuerySpyMiddleware, on_report=to_metrics)
```

`RequestReport` carries `method`, `path`, `query_count`, `findings`,
`duration_ms`, a `clean` property, and `render()` for the human-readable form.

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
