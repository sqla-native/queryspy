#!/usr/bin/env bash
# The full local verification ladder. See TESTING.md for what each layer proves.
#
#   ./scripts/verify.sh          layers 1-3, 5-8. No Docker.
#   ./scripts/verify.sh --all    adds layer 4 (real databases). Needs Docker.
#
# Mutation testing (layer 9) is deliberately not here: it is an occasional,
# targeted audit, not something to run on every change.
set -uo pipefail

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python
  else PYTHON="$(command -v python || command -v python3)"; fi
fi
BIN="$(dirname "$PYTHON")"

WITH_DB=0
[ "${1:-}" = "--all" ] && WITH_DB=1

FAILED=()
step() {
  local name="$1"; shift
  printf '\n\033[1m▶ %s\033[0m\n' "$name"
  if "$@"; then
    printf '\033[32m  ✓ %s\033[0m\n' "$name"
  else
    printf '\033[31m  ✗ %s\033[0m\n' "$name"
    FAILED+=("$name")
  fi
}

step "lint"            "$BIN/ruff" check .
step "format"          "$BIN/ruff" format --check .
step "types"           "$BIN/mypy"
step "complexity"      "$BIN/complexipy" src/queryspy --max-complexity-allowed 15

step "tests + coverage" bash -c "'$PYTHON' -m coverage run -m pytest -q && '$PYTHON' -m coverage report"

step "examples"        "$PYTHON" -m pytest examples -q -p no:cacheprovider

# The examples' own gate. A stale baseline entry means detection stopped
# finding something it used to, which pytest reports as a pass - so grep for it.
step "examples: strict gate" bash -c "
  out=\$('$PYTHON' -m pytest examples -q -p no:cacheprovider \
        --queryspy-strict --queryspy-baseline=examples/queryspy-baseline.json 2>&1) || { echo \"\$out\"; exit 1; }
  echo \"\$out\" | tail -2
  if echo \"\$out\" | grep -q 'no longer occur'; then
    echo 'a baselined finding stopped occurring - detection may have regressed'
    exit 1
  fi"

step "docs"            "$BIN/mkdocs" build --strict
step "packaged consumer" "$PYTHON" scripts/check_packed_consumer.py
step "supply chain"    "$PYTHON" scripts/audit_production_surface.py

if [ "$WITH_DB" = "1" ]; then
  step "databases: up" docker compose up -d --wait
  step "real databases" ./scripts/test-integration.sh -q
  step "databases: down" docker compose down -v
else
  printf '\n\033[33m  ~ real databases skipped (pass --all)\033[0m\n'
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[32m▬▬ all layers passed ▬▬\033[0m\n'
  exit 0
fi
printf '\033[31m▬▬ %d failed: %s ▬▬\033[0m\n' "${#FAILED[@]}" "${FAILED[*]}"
exit 1
