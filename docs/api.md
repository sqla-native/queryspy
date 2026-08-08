# API reference

## Assertions

All four context managers live in `queryspy`:

```python
from queryspy import (
    assert_max_queries,  # at most n statements
    assert_num_queries,  # exactly n statements
    no_n_plus_one,  # no findings
    record,  # record only
)
```

Each accepts an optional `session=` to narrow ORM-level records to one session.
`record()` also accepts `capture_stacks=False`. `no_n_plus_one()` accepts
`threshold=`.

## Recorder

Returned by every context manager.

| Member | Type | Meaning |
| --- | --- | --- |
| `query_count` | `int` | Statements that reached the driver, flushes included |
| `db_duration_ms` | `float` | Time actually spent in the driver |
| `slowest` | `SlowStatement \| None` | The single slowest statement, with its duration |
| `orm_records` | `list[QueryRecord]` | ORM-level executions |
| `findings(threshold=2)` | `list[Finding]` | Problems, worst first |

## Finding

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | `str` | `lazy_load`, `column_load`, or `repeated_statement` |
| `label` | `str` | `User.addresses`, an entity name, or the SQL |
| `count` | `int` | Round trips this finding aggregates |
| `sql` | `str` | The statement template |
| `frame` | `AppFrame \| None` | Where in your code it came from |
| `entity` | `str \| None` | Mapped class, when known |
| `uselist` | `bool \| None` | Whether the relationship is a collection |
| `origin` | `str \| None` | Set by the pytest plugin to the test node id |

## QueryRecord

One ORM-level statement execution.

| Field | Meaning |
| --- | --- |
| `sql` | Statement template — bind parameters stay as placeholders. Rendered lazily and cached, because rendering is expensive and most records are never reported |
| `is_lazy_load` | `lazy_loaded_from` was set. **Not** `is_relationship_load` |
| `is_column_load` | Per-instance column round trip |
| `entity`, `path`, `uselist`, `frame` | Attribution |

## Exceptions

```
AssertionError
└── QuerySpyError
    ├── QueryCountError
    └── NPlusOneError
```

## Rendering and serialisation

```python
from queryspy import render_finding, render_findings, to_dict, to_json, to_sarif
```

`to_json` and `to_sarif` take `version=` and an optional `root=` for
repo-relative paths.

## ASGI and WSGI

```python
from queryspy.asgi import QuerySpyMiddleware, RequestReport  # FastAPI, Starlette, Litestar
from queryspy.wsgi import QuerySpyMiddleware, RequestReport  # Flask, Pyramid, Bottle
```

See the [ASGI](asgi.md) and [WSGI](wsgi.md) guides.

`RequestReport` carries `method`, `path`, `query_count`, `findings`,
`duration_ms`, `db_duration_ms`, `slowest`, a `clean` property and `render()`.
