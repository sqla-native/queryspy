# Example: FastAPI + async SQLAlchemy

A minimal service with one deliberate N+1, and the test that catches it.

```bash
uv pip install -r requirements.txt
pytest --queryspy-strict
```

`test_endpoints.py::test_list_projects_has_no_n_plus_one` fails and names the
exact line in `app.py` responsible:

```
N+1 detected: 3 queries for Project.tasks (lazy load)
  triggered from examples/fastapi_app/app.py:NN in list_projects()
  SELECT task.id AS task_id, ...
  fix: .options(selectinload(Project.tasks))
```

Apply that fix to `list_projects` and the test passes. The sibling endpoint
`list_projects_fixed` shows the corrected version.

This directory is illustrative and sits outside the core package's strictness
scope (see GUIDELINES_QUERYSPY.md section 6).
