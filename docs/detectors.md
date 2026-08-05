# What it catches

Three detectors, applied in order of precision. Each **claims** the queries it
explains, so the same round trips are never reported twice.

| Detector | Signal | Catches |
| --- | --- | --- |
| `lazy_load` | `ORMExecuteState.lazy_loaded_from` is set | A relationship lazily loaded once per parent row |
| `column_load` | `is_column_load` alone | A deferred column, or an attribute refreshed after commit, loaded per instance |
| `repeated_statement` | Identical SQL template repeated | Everything the ORM hooks cannot see |

The default threshold is **2**: two identical round trips is already the N+1
shape, one per parent row. A higher default would let a two-item collection slip
through unreported.

## lazy_load

The classic. One query for the parents, then one more per parent:

```python
for user in session.scalars(select(User)).all():
    print(user.addresses)  # one query, per user
```

```
N+1 detected: 3 queries for User.addresses (lazy load)
  fix: .options(selectinload(User.addresses))
```

For a many-to-one the suggestion changes to `joinedload`, because the row is
already on the other side of the join.

## column_load

Deferred columns behave the same way, one round trip per instance:

```python
for user in session.scalars(select(User)).all():
    print(user.bio)  # bio is deferred: one query, per user
```

The same signal fires for attributes refreshed after a commit — SQLAlchemy
expires them by default, so touching them reloads per instance. The two share a
signature at the event level, so the hint names both causes: `undefer()` for a
deferred column, `expire_on_commit=False` for the refresh case.

## repeated_statement

This one matters more than it looks. None of these are ORM lazy loads, so no
relationship-load hook will ever fire for them — but they are still N round
trips where one would do:

```python
for user_id in user_ids:
    session.get(User, user_id)  # N queries
```

```python
for user_id in user_ids:
    user = await repo.get_user(user_id)  # N queries, through your own layer
```

It also catches the case where parents are fetched one at a time while
relationships are eagerly configured — measured at 6 queries across two repeated
templates, with `lazy_loaded_from` unset on every one.

## What it will not flag

A tool that flags correct code gets uninstalled the same day, so the
false-positive suite is a release gate weighted equally with detection. None of
these produce a finding:

- a single query
- `selectinload` — two queries **by design**
- `subqueryload` — likewise
- `joinedload` — one query
- `undefer`
- a relationship configured `lazy="selectin"` loaded over a batch of parents
- bulk inserts and updates
- autoflush INSERTs preceding a SELECT

!!! warning "Why `is_relationship_load` is not the signal"

    `selectinload` and `subqueryload` both report `is_relationship_load=True`.
    A detector keyed on that flag would report the **fix** as the bug. Only
    `lazy_loaded_from` separates them — see the [design notes](design.md).

## Tuning the threshold

```python
with no_n_plus_one(threshold=5):
    ...
```

```python
@pytest.mark.queryspy(threshold=5)
def test_batch_job(session): ...
```

Raise it when a small, bounded number of round trips is genuinely acceptable.
Prefer a query budget when what you actually care about is the total.
