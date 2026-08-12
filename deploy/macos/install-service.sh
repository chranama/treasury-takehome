#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG=C
export LC_ALL=C
umask 077

APP_ROOT="/Users/chranama-server/treasury-takehome"
DATA_ROOT="/Users/chranama-server/treasury-takehome-data"
SERVICE_LABEL="dev.mealcheck.label-review"
SERVICE_TARGET="system/$SERVICE_LABEL"
PLIST_TARGET="/Library/LaunchDaemons/$SERVICE_LABEL.plist"
ENV_FILE="$DATA_ROOT/config/treasury.env"

fail() {
  printf 'install-service: %s\n' "$*" >&2
  exit 1
}

[ "$(/usr/bin/id -u)" -eq 0 ] || fail "run this installer as root with sudo"
[ -L "$APP_ROOT/current" ] || fail "current release symlink is missing"

release_dir=$(cd "$APP_ROOT/current" && pwd -P)
case "$release_dir" in
  "$APP_ROOT"/releases/*) ;;
  *) fail "current release points outside the release directory" ;;
esac

plist_source="$release_dir/deploy/macos/dev.mealcheck.label-review.plist.template"
start_wrapper="$release_dir/deploy/macos/start-label-review.sh"

[ -f "$release_dir/RELEASE_COMMIT" ] || fail "release identity is missing"
[ -x "$release_dir/.venv/bin/python" ] || fail "release Python environment is missing"
[ -f "$release_dir/frontend/dist/index.html" ] || fail "compiled frontend is missing"
[ -x "$start_wrapper" ] || fail "start wrapper is not executable"
[ -f "$plist_source" ] || fail "launchd plist template is missing"
[ -f "$ENV_FILE" ] || fail "protected environment file is missing"

env_owner=$(/usr/bin/stat -f '%Su' "$ENV_FILE")
env_mode=$(/usr/bin/stat -f '%Lp' "$ENV_FILE")
[ "$env_owner" = "chranama-server" ] || fail "environment file has the wrong owner"
case "$env_mode" in
  400|600) ;;
  *) fail "environment file permissions must be 400 or 600" ;;
esac

[ ! -e "$PLIST_TARGET" ] || fail "service plist already exists; refusing to replace it"
if /bin/launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
  fail "service is already loaded; refusing to replace it"
fi
if /usr/sbin/lsof -nP -iTCP:8081 -sTCP:LISTEN >/dev/null 2>&1; then
  fail "localhost port 8081 is already in use"
fi

/usr/bin/plutil -lint "$plist_source" >/dev/null

plist_installed=0
service_loaded=0
cleanup_failed_install() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ]; then
    if [ "$service_loaded" -eq 1 ]; then
      /bin/launchctl bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
    fi
    if [ "$plist_installed" -eq 1 ]; then
      /bin/rm -f -- "$PLIST_TARGET"
    fi
  fi
  exit "$status"
}
trap cleanup_failed_install EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

/usr/bin/install -o root -g wheel -m 644 "$plist_source" "$PLIST_TARGET"
plist_installed=1
/bin/launchctl bootstrap system "$PLIST_TARGET"
service_loaded=1
/bin/launchctl print "$SERVICE_TARGET" >/dev/null

trap - EXIT INT TERM
printf 'Installed and loaded %s from release %s\n' \
  "$SERVICE_LABEL" "$(/usr/bin/tr -d '\r\n' <"$release_dir/RELEASE_COMMIT")"
printf 'Verify localhost health, readiness, listener binding, and logs before enabling live extraction.\n'
