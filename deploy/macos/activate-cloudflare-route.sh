#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG=C
export LC_ALL=C
umask 077

CONFIG="/Users/chranama-server/.cloudflared/mealcheck-api.yml"
APP_ROOT="/Users/chranama-server/treasury-takehome"
SERVICE_TARGET="system/dev.mealcheck.tunnel"
EXPECTED_USER="chranama-server"
MEALCHECK_HOST="api.mealcheck.dev"
TREASURY_HOST="label-review.mealcheck.dev"

candidate="${2:-}"
backup=""
config_installed=0
completed=0

fail() {
  printf 'activate-cloudflare-route: %s\n' "$*" >&2
  exit 1
}

service_pid() {
  /bin/launchctl print "$SERVICE_TARGET" 2>/dev/null |
    /usr/bin/awk '$1 == "pid" {print $3; exit}'
}

wait_for_replacement() {
  replaced_pid=""
  attempt=0
  while [ "$attempt" -lt 40 ]; do
    candidate_pid=$(service_pid)
    if [ -n "$candidate_pid" ] && [ "$candidate_pid" != "$1" ] && \
      /bin/kill -0 "$candidate_pid" 2>/dev/null; then
      replaced_pid=$candidate_pid
      return 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
  done
  return 1
}

restore_on_failure() {
  status=$?
  trap - EXIT INT TERM
  set +e

  if [ "$status" -ne 0 ] && [ "$config_installed" -eq 1 ] && \
    [ "$completed" -eq 0 ] && [ -f "$backup" ]; then
    /bin/cp -p "$backup" "$CONFIG.rollback"
    /bin/chmod 600 "$CONFIG.rollback"
    /bin/mv -f "$CONFIG.rollback" "$CONFIG"

    rollback_pid=$(service_pid)
    rollback_owner=$(/bin/ps -o user= -p "$rollback_pid" | /usr/bin/xargs)
    if [ "$rollback_owner" = "$EXPECTED_USER" ]; then
      /bin/kill -TERM "$rollback_pid"
      wait_for_replacement "$rollback_pid" || true
    fi
    printf 'activate-cloudflare-route: restored the protected pre-change config after failure\n' >&2
  fi

  exit "$status"
}
trap restore_on_failure EXIT
trap 'exit 130' INT TERM

[ "${1:-}" = "--confirm-shared-tunnel-change" ] && [ "$#" -eq 2 ] || {
  fail "usage: activate-cloudflare-route.sh --confirm-shared-tunnel-change CANDIDATE_CONFIG"
}
[ "$(/usr/bin/id -un)" = "$EXPECTED_USER" ] || fail "run as $EXPECTED_USER"
case "$candidate" in
  /Users/chranama-server/.treasury-d3-staging/*) ;;
  *) fail "candidate must be in the private D3 staging directory" ;;
esac
[ -f "$CONFIG" ] || fail "current tunnel config is missing"
[ -f "$candidate" ] || fail "candidate tunnel config is missing"
[ "$(/usr/bin/stat -f '%Su' "$candidate")" = "$EXPECTED_USER" ] || {
  fail "candidate has the wrong owner"
}
[ "$(/usr/bin/stat -f '%OLp' "$candidate")" = "600" ] || fail "candidate must have mode 600"

/usr/local/bin/cloudflared tunnel --config "$CONFIG" ingress validate
/usr/local/bin/cloudflared tunnel --config "$candidate" ingress validate

[ "$(/usr/bin/grep -Fc -- "- hostname: $MEALCHECK_HOST" "$CONFIG")" -eq 1 ] || {
  fail "current MealCheck hostname is missing or duplicated"
}
[ "$(/usr/bin/grep -Fc -- "service: http://127.0.0.1:8080" "$CONFIG")" -eq 1 ] || {
  fail "current MealCheck service mapping is unexpected"
}
[ "$(/usr/bin/grep -Fc -- "- hostname: $TREASURY_HOST" "$CONFIG")" -eq 0 ] || {
  fail "Treasury hostname is already active"
}
[ "$(/usr/bin/grep -Fc -- "- hostname: $MEALCHECK_HOST" "$candidate")" -eq 1 ] || {
  fail "candidate changed or duplicated the MealCheck hostname"
}
[ "$(/usr/bin/grep -Fc -- "service: http://127.0.0.1:8080" "$candidate")" -eq 1 ] || {
  fail "candidate changed the MealCheck service mapping"
}
[ "$(/usr/bin/grep -Fc -- "- hostname: $TREASURY_HOST" "$candidate")" -eq 1 ] || {
  fail "candidate Treasury hostname is missing or duplicated"
}
[ "$(/usr/bin/grep -Fc -- "service: http://127.0.0.1:8081" "$candidate")" -eq 1 ] || {
  fail "candidate Treasury service mapping is missing or duplicated"
}

/usr/local/bin/cloudflared tunnel --config "$candidate" ingress rule \
  "https://$MEALCHECK_HOST/" | /usr/bin/grep -Fq 'Matched rule #0'
/usr/local/bin/cloudflared tunnel --config "$candidate" ingress rule \
  "https://$TREASURY_HOST/healthz" | /usr/bin/grep -Fq 'Matched rule #1'

old_pid=$(service_pid)
case "$old_pid" in
  ''|*[!0-9]*) fail "tunnel service does not have a running process" ;;
esac
old_owner=$(/bin/ps -o user= -p "$old_pid" | /usr/bin/xargs)
[ "$old_owner" = "$EXPECTED_USER" ] || fail "tunnel process has the wrong owner"
old_command=$(/bin/ps -o command= -p "$old_pid")
case "$old_command" in
  *"cloudflared tunnel --config $CONFIG run mealcheck-api"*) ;;
  *) fail "tunnel process command is unexpected" ;;
esac

baseline_status=$(
  /usr/bin/curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 5 --max-time 10 "https://$MEALCHECK_HOST/"
)
case "$baseline_status" in
  000|'') fail "existing MealCheck route is not reachable" ;;
esac

release_commit=$(/usr/bin/tr -d '\r\n' <"$APP_ROOT/current/RELEASE_COMMIT")
case "$release_commit" in
  *[!0-9a-f]*|'') fail "active Treasury release identity is invalid" ;;
esac
backup="$CONFIG.pre-treasury-$release_commit"
[ ! -e "$backup" ] || fail "protected pre-change backup already exists"
/bin/cp -p "$CONFIG" "$backup"
/bin/chmod 600 "$backup"

/bin/cp "$candidate" "$CONFIG.next"
/bin/chmod 600 "$CONFIG.next"
/bin/mv -f "$CONFIG.next" "$CONFIG"
config_installed=1

/bin/kill -TERM "$old_pid"
wait_for_replacement "$old_pid" || fail "launchd did not start a replacement tunnel process"
new_owner=$(/bin/ps -o user= -p "$replaced_pid" | /usr/bin/xargs)
[ "$new_owner" = "$EXPECTED_USER" ] || fail "replacement tunnel process has the wrong owner"
/usr/local/bin/cloudflared tunnel --config "$CONFIG" ingress validate

ready=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  mealcheck_status=$(
    /usr/bin/curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --connect-timeout 5 --max-time 10 "https://$MEALCHECK_HOST/" 2>/dev/null || true
  )
  if [ "$mealcheck_status" = "$baseline_status" ] && \
    "$APP_ROOT/current/deploy/macos/check-service.sh" public >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 2
done
[ "$ready" -eq 1 ] || fail "public routes did not become healthy after tunnel restart"

completed=1
printf 'Activated the additive Treasury route; MealCheck remained reachable and Treasury health and readiness passed.\n'
printf 'Protected rollback config: %s\n' "$backup"
