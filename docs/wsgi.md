# WSGI middleware

The same reporting as the [ASGI middleware](asgi.md), for Flask and anything
else that speaks WSGI. No framework dependency, no new packages.

## Mount it

=== "Flask"

    ```python
    from queryspy.wsgi import QuerySpyMiddleware

    app.wsgi_app = QuerySpyMiddleware(app.wsgi_app, budget=10)
    ```

=== "Any WSGI app"

    ```python
    from queryspy.wsgi import QuerySpyMiddleware

    application = QuerySpyMiddleware(application, budget=10)
    ```

Options are identical to the ASGI middleware — `budget`, `threshold`,
`capture_stacks`, `add_headers`, `logger`, `on_report`, `panel`, `panel_path`,
`history`. The panel works the same way; see the [ASGI guide](asgi.md#the-panel).

## When the report is emitted

This is the one thing that genuinely differs from ASGI.

A WSGI application returns an **iterable**, and for a streaming response the
queries keep coming after `__call__` has already returned. Finalising there
would report a fraction of the truth, so queryspy keeps the recording window
open and wraps the returned iterable: the report is emitted when that iterable
is **exhausted or closed**, whichever happens first.

Two consequences worth knowing:

**Counter headers reflect the moment the response started.** They have to —
headers go out before a streaming body runs. The log line is emitted at the end
and is always the final figure.

**In tests, consume the body.** A real server always does; test clients are
often lazy:

```python
def test_projects(client):
    response = client.get("/projects")
    response.get_data()  # <- without this, the report has not landed yet
    assert response.headers["x-queryspy-queries"] == "2"
```

## Concurrency

A threaded WSGI server gives each request its own thread, and a fresh thread
gets a fresh context — so the recorder's context variable isolates requests
exactly as it does for ASGI tasks. Interleaved requests never record each
other's queries.

## Which middleware do I want?

| Stack | Use |
| --- | --- |
| FastAPI, Starlette, Litestar, Quart | `queryspy.asgi` |
| Flask, Pyramid, Bottle, Django (WSGI) | `queryspy.wsgi` |

Neither is required — the [pytest integration](pytest.md) is independent of both
and is where most of the value is.
