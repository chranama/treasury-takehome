#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG=C
export LC_ALL=C
umask 077

APP_ROOT="${TREASURY_DEPLOY_APP_ROOT:-/Users/chranama-server/treasury-takehome}"
DATA_ROOT="${TREASURY_DEPLOY_DATA_ROOT:-/Users/chranama-server/treasury-takehome-data}"
ENV_FILE="$DATA_ROOT/config/treasury.env"
EXPECTED_USER="chranama-server"

rollback_file=""
setting_changed=0
completed=0

fail() {
  printf 'set-live-extraction: %s\n' "$*" >&2
  exit 1
}

restore_on_failure() {
  status=$?
  trap - EXIT INT TERM
  set +e

  if [ "$status" -ne 0 ] && [ "$setting_changed" -eq 1 ] && \
    [ "$completed" -eq 0 ] && [ -f "$rollback_file" ]; then
    /bin/cp "$rollback_file" "$ENV_FILE.restore"
    /bin/chmod 600 "$ENV_FILE.restore"
    /bin/mv -f "$ENV_FILE.restore" "$ENV_FILE"
    "$APP_ROOT/current/deploy/macos/restart-service.sh" \
      --confirm-no-active-reviews >/dev/null 2>&1 || true
    printf 'set-live-extraction: restored the prior protected setting after failure\n' >&2
  fi

  if [ -n "$rollback_file" ]; then
    /bin/rm -f -- "$rollback_file"
  fi
  exit "$status"
}
trap restore_on_failure EXIT
trap 'exit 130' INT TERM

case "${1:-}:${2:-}:$#" in
  --disable:--confirm-no-active-reviews:2)
    current=true
    desired=false
    ;;
  --enable:--confirm-p1-smoke-complete:2)
    current=false
    desired=true
    ;;
  *)
    fail "usage: set-live-extraction.sh --disable --confirm-no-active-reviews | --enable --confirm-p1-smoke-complete"
    ;;
esac

[ "$(/usr/bin/id -un)" = "$EXPECTED_USER" ] || fail "run as $EXPECTED_USER"
[ -f "$ENV_FILE" ] || fail "protected environment file is missing"
[ "$(/usr/bin/stat -f '%Su' "$ENV_FILE")" = "$EXPECTED_USER" ] || {
  fail "protected environment has the wrong owner"
}
[ "$(/usr/bin/stat -f '%OLp' "$ENV_FILE")" = "600" ] || {
  fail "protected environment must have mode 600"
}
"$APP_ROOT/current/deploy/macos/check-service.sh" local >/dev/null

current_count=$(
  /usr/bin/grep -Fxc "TREASURY_LIVE_EXTRACTION_ENABLED=$current" "$ENV_FILE" || true
)
desired_count=$(
  /usr/bin/grep -Fxc "TREASURY_LIVE_EXTRACTION_ENABLED=$desired" "$ENV_FILE" || true
)
if [ "$current_count" -eq 0 ] && [ "$desired_count" -eq 1 ]; then
  completed=1
  printf 'Live extraction is already set to %s; local readiness passed.\n' "$desired"
  exit 0
fi
[ "$current_count" -eq 1 ] && [ "$desired_count" -eq 0 ] || {
  fail "protected environment must contain exactly one recognized live-extraction setting"
}

rollback_file=$(/usr/bin/mktemp "$DATA_ROOT/config/treasury.env.rollback.XXXXXX")
/bin/cp "$ENV_FILE" "$rollback_file"
/bin/chmod 600 "$rollback_file"
next_file=$(/usr/bin/mktemp "$DATA_ROOT/config/treasury.env.next.XXXXXX")
/usr/bin/awk -v current="$current" -v desired="$desired" '
BEGIN { changed = 0 }
$0 == "TREASURY_LIVE_EXTRACTION_ENABLED=" current {
  print "TREASURY_LIVE_EXTRACTION_ENABLED=" desired
  changed += 1
  next
}
{ print }
END { if (changed != 1) exit 42 }
' "$ENV_FILE" >"$next_file" || {
  /bin/rm -f -- "$next_file"
  fail "could not prepare the protected setting change"
}
/bin/chmod 600 "$next_file"
/bin/mv -f "$next_file" "$ENV_FILE"
setting_changed=1

"$APP_ROOT/current/deploy/macos/restart-service.sh" --confirm-no-active-reviews
"$APP_ROOT/current/deploy/macos/check-service.sh" local
[ "$(/usr/bin/grep -Fxc "TREASURY_LIVE_EXTRACTION_ENABLED=$desired" "$ENV_FILE")" -eq 1 ] || {
  fail "protected setting did not remain in the requested state"
}

completed=1
printf 'Set live extraction to %s; local readiness passed after restart.\n' "$desired"
