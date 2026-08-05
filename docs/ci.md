# CI and code scanning

## The GitHub Action

```yaml
permissions:
  contents: read
  security-events: write   # required to upload SARIF

jobs:
  queryspy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .[test] queryspy

      - uses: sqla-native/queryspy@v0
        with:
          args: tests/
```

Findings appear as annotations on the pull request, on the exact line that
caused them.

The action deliberately does **not** install Python or your dependencies —
every project installs differently, and guessing would be worse than composing.
Set your environment up however you already do, then add the step.

### Inputs

| Input | Default | Effect |
| --- | --- | --- |
| `args` | `""` | Extra arguments for pytest |
| `report` | `queryspy.sarif` | Where to write the SARIF |
| `strict` | `false` | Fail the job on any finding |
| `upload` | `true` | Upload to code scanning |
| `working-directory` | `.` | Where to run pytest |

Outputs: `findings` (count) and `report` (path).

A failing suite does not skip the report — the exit code is captured, the report
is written and uploaded, and the code is re-raised at the end.

## Without the action

The report is just a pytest flag:

```bash
pytest --queryspy-report=queryspy.sarif
```

```yaml
- run: pytest --queryspy-report=queryspy.sarif
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with:
    sarif_file: queryspy.sarif
    category: queryspy
```

## JSON instead

```bash
pytest --queryspy-report=queryspy.json
```

```json
{
  "tool": "queryspy",
  "version": "0.2.0",
  "findings": [
    {
      "kind": "lazy_load",
      "label": "User.addresses",
      "count": 11,
      "sql": "SELECT address.id FROM address WHERE address.user_id = :pk_1",
      "entity": "Address",
      "hint": ".options(selectinload(User.addresses))",
      "origin": "tests/test_users.py::test_list_users",
      "location": {
        "file": "app/services/users.py",
        "line": 42,
        "function": "list_users"
      }
    }
  ]
}
```

Format is chosen from the extension unless you pass
`--queryspy-report-format`. Paths in the report are relative to the pytest
rootdir, which is what code scanning needs to place an annotation.

## Gating versus reporting

These are different decisions and worth keeping separate:

- **Reporting** (`--queryspy-report`) collects without enforcing. Outcomes are
  unchanged. Good for a first look, and for tracking a number over time.
- **Gating** (`--queryspy-strict`, `strict: true`, `queryspy_budget`) fails the
  build.

Adopting the report first and the gate second is usually the shorter path,
especially on a suite that has never been measured.
