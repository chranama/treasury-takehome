#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG=C
export LC_ALL=C

APP_ROOT="/Users/chranama-server/treasury-takehome"
CURRENT_RELEASE="$APP_ROOT/current"
SERVICE_LABEL="dev.mealcheck.label-review"
SERVICE_TARGET="system/$SERVICE_LABEL"
EXPECTED_USER="chranama-server"
EXPECTED_LISTENER="127.0.0.1:8081"

fail() {
  printf 'restart-service: %s\n' "$*" >&2
  exit 1
}

[ "${1:-}" = "--confirm-no-active-reviews" ] && [ "$#" -eq 1 ] || {
  fail "usage: restart-service.sh --confirm-no-active-reviews"
}
[ "$(/usr/bin/id -un)" = "$EXPECTED_USER" ] || fail "run as $EXPECTED_USER"
[ -L "$CURRENT_RELEASE" ] || fail "current release symlink is missing"

release_dir=$(cd "$CURRENT_RELEASE" && pwd -P)
case "$release_dir" in
  "$APP_ROOT"/releases/*) ;;
  *) fail "current release points outside the release directory" ;;
esac
[ -f "$release_dir/RELEASE_COMMIT" ] || fail "release identity is missing"
[ -x "$release_dir/deploy/macos/check-service.sh" ] || fail "service checker is missing"

old_pid=$(
  /bin/launchctl print "$SERVICE_TARGET" 2>/dev/null |
    /usr/bin/awk '$1 == "pid" {print $3; exit}'
)
case "$old_pid" in
  ''|*[!0-9]*) fail "launchd service does not have a running process" ;;
esac
old_owner=$(/bin/ps -o user= -p "$old_pid" | /usr/bin/xargs)
[ "$old_owner" = "$EXPECTED_USER" ] || fail "running service has the wrong owner"
old_listener=$(
  /usr/sbin/lsof -nP -a -p "$old_pid" -iTCP -sTCP:LISTEN -Fn |
    /usr/bin/awk '/^n/ {print substr($0, 2)}'
)
[ "$old_listener" = "$EXPECTED_LISTENER" ] || fail "running service has an unexpected listener"

/bin/kill -TERM "$old_pid"

new_pid=""
attempt=0
while [ "$attempt" -lt 40 ]; do
  candidate=$(
    /bin/launchctl print "$SERVICE_TARGET" 2>/dev/null |
      /usr/bin/awk '$1 == "pid" {print $3; exit}'
  )
  if [ -n "$candidate" ] && [ "$candidate" != "$old_pid" ] && /bin/kill -0 "$candidate" 2>/dev/null; then
    new_pid=$candidate
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 1
done
[ -n "$new_pid" ] || fail "launchd did not start a replacement process"

ready=0
attempt=0
while [ "$attempt" -lt 20 ]; do
  if "$release_dir/deploy/macos/check-service.sh" local >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 1
done
[ "$ready" -eq 1 ] || fail "replacement process did not become ready"

active_release=$(cd "$CURRENT_RELEASE" && pwd -P)
[ "$active_release" = "$release_dir" ] || fail "current release changed during restart"
new_cwd=$(
  /usr/sbin/lsof -a -p "$new_pid" -d cwd -Fn |
    /usr/bin/awk '/^n/ {print substr($0, 2)}'
)
[ "$new_cwd" = "$release_dir" ] || fail "replacement process is running the wrong release"
new_listener=$(
  /usr/sbin/lsof -nP -a -p "$new_pid" -iTCP -sTCP:LISTEN -Fn |
    /usr/bin/awk '/^n/ {print substr($0, 2)}'
)
[ "$new_listener" = "$EXPECTED_LISTENER" ] || fail "replacement process has an unexpected listener"

commit=$(/usr/bin/tr -d '\r\n' <"$release_dir/RELEASE_COMMIT")
printf 'Restarted %s on release %s; local health and readiness passed.\n' \
  "$SERVICE_LABEL" "$commit"
