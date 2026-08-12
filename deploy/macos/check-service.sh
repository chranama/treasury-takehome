#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

mode="${1:-local}"
case "$mode" in
  local) base_url="http://127.0.0.1:8081" ;;
  public) base_url="https://label-review.mealcheck.dev" ;;
  *) printf 'usage: check-service.sh [local|public]\n' >&2; exit 2 ;;
esac

CURL_BIN=$(command -v curl)
PYTHON_BIN=$(command -v python3)

check_json_status() {
  endpoint=$1
  expected=$2
  payload=$(
    "$CURL_BIN" --fail --silent --show-error \
      --connect-timeout 5 --max-time 10 "$base_url/$endpoint"
  )
  printf '%s' "$payload" | "$PYTHON_BIN" -c '
import json
import sys

expected = sys.argv[1]
payload = json.load(sys.stdin)
if payload.get("status") != expected:
    raise SystemExit(f"expected status {expected!r}")
if expected == "ready":
    checks = payload.get("checks", {})
    required = {"configuration", "database", "temporary_storage"}
    if set(checks) != required or any(value != "ok" for value in checks.values()):
        raise SystemExit("readiness checks are missing or failed")
' "$expected"
}

check_json_status healthz ok
check_json_status readyz ready
printf '%s service health and readiness are OK\n' "$mode"
