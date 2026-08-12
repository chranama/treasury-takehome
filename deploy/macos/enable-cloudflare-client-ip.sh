#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG=C
export LC_ALL=C
umask 077

APP_ROOT="/Users/chranama-server/treasury-takehome"
DATA_ROOT="/Users/chranama-server/treasury-takehome-data"
ENV_FILE="$DATA_ROOT/config/treasury.env"
EXPECTED_USER="chranama-server"

rollback_file=""
setting_changed=0
completed=0

fail() {
  printf 'enable-cloudflare-client-ip: %s\n' "$*" >&2
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
    printf 'enable-cloudflare-client-ip: restored the prior protected setting after failure\n' >&2
  fi

  if [ -n "$rollback_file" ]; then
    /bin/rm -f -- "$rollback_file"
  fi
  exit "$status"
}
trap restore_on_failure EXIT
trap 'exit 130' INT TERM

[ "${1:-}" = "--confirm-tunnel-exclusive" ] && [ "$#" -eq 1 ] || {
  fail "usage: enable-cloudflare-client-ip.sh --confirm-tunnel-exclusive"
}
[ "$(/usr/bin/id -un)" = "$EXPECTED_USER" ] || fail "run as $EXPECTED_USER"
[ -f "$ENV_FILE" ] || fail "protected environment file is missing"
[ "$(/usr/bin/stat -f '%Su' "$ENV_FILE")" = "$EXPECTED_USER" ] || {
  fail "protected environment has the wrong owner"
}
[ "$(/usr/bin/stat -f '%OLp' "$ENV_FILE")" = "600" ] || {
  fail "protected environment must have mode 600"
}
[ "$(/usr/bin/grep -Fxc 'TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=false' "$ENV_FILE")" -eq 1 ] || {
  fail "expected exactly one disabled Cloudflare client-IP setting"
}
[ "$(/usr/bin/grep -Fxc 'TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=true' "$ENV_FILE")" -eq 0 ] || {
  fail "Cloudflare client-IP trust is already enabled"
}

"$APP_ROOT/current/deploy/macos/check-service.sh" local >/dev/null
"$APP_ROOT/current/deploy/macos/check-service.sh" public >/dev/null

rollback_file=$(/usr/bin/mktemp "$DATA_ROOT/config/treasury.env.rollback.XXXXXX")
/bin/cp "$ENV_FILE" "$rollback_file"
/bin/chmod 600 "$rollback_file"
next_file=$(/usr/bin/mktemp "$DATA_ROOT/config/treasury.env.next.XXXXXX")
/usr/bin/awk '
BEGIN { changed = 0 }
$0 == "TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=false" {
  print "TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=true"
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
"$APP_ROOT/current/deploy/macos/check-service.sh" public
[ "$(/usr/bin/grep -Fxc 'TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=true' "$ENV_FILE")" -eq 1 ] || {
  fail "protected setting did not remain enabled"
}

completed=1
printf 'Enabled trusted Cloudflare client identity; local and public readiness passed after restart.\n'
