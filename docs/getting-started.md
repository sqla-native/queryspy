# Getting started

## Install

```bash
pip install queryspy
```

SQLAlchemy is the only runtime dependency. The pytest plugin registers itself
through an entry point, so there is nothing to add to `conftest.py`.

## See what your suite is already doing

The fastest way to find out whether you have a problem:

```bash
pytest --queryspy-strict
```

Every test that triggers an N+1 now fails, and each failure names the line
responsible:

```
E   queryspy.api.NPlusOneError:
E
E   N+1 detected: 3 queries for User.addresses (lazy load)
E     triggered from app/services/users.py:28 in list_users()
E     SELECT address.id AS address_id, address.email ...
E     fix: .options(selectinload(User.addresses))
```

If that turns your suite red in twenty places, do not adopt it as a gate on day
one. Start with assertions on the paths you care about, and turn the gate on
once the list is short.

## Assert deliberately

```python
from queryspy import assert_max_queries, no_n_plus_one


def test_list_users(session):
    with no_n_plus_one():
        list_users(session)


def test_list_users_is_two_queries(session):
    with assert_max_queries(2):
        list_users(session)
```

`assert_max_queries` is the stronger of the two. `no_n_plus_one` catches the
shape; a budget catches the count, including query growth that is not an N+1 at
all.

## Allow the deliberate ones

Some N+1s are a considered trade-off. Say so in the test rather than turning the
gate off:

```python
@pytest.mark.queryspy(allow_n_plus_one=True)
def test_admin_report(session): ...
```

## Then widen

Once the suite is clean, make it stay clean — in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
queryspy_fail_on = "n_plus_one"
queryspy_budget = 25
```

And in CI, so regressions annotate the pull request rather than waiting to be
noticed: see the [CI guide](ci.md).
