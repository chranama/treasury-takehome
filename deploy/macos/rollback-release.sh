#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
umask 077

APP_ROOT="${TREASURY_DEPLOY_APP_ROOT:-/Users/chranama-server/treasury-takehome}"

fail() {
  printf 'rollback-release: %s\n' "$*" >&2
  exit 1
}

if [ "${1:-}" = "--list" ]; then
  if [ ! -d "$APP_ROOT/releases" ]; then
    exit 0
  fi
  /usr/bin/find "$APP_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -name '[0-9a-f]*' -print \
    | /usr/bin/sed 's#.*/##' \
    | /usr/bin/sort
  exit 0
fi

[ "${1:-}" = "--confirm-schema-compatible" ] && [ -n "${2:-}" ] || {
  fail "usage: rollback-release.sh --confirm-schema-compatible COMMIT"
}
commit=$2
case "$commit" in
  *[!0-9a-f]*|'') fail "commit is not a Git SHA-1" ;;
esac
[ "${#commit}" -eq 40 ] || fail "commit must be a full Git SHA-1"

release_dir="$APP_ROOT/releases/$commit"
[ -d "$release_dir" ] || fail "requested release is not installed"
[ -f "$release_dir/RELEASE_COMMIT" ] || fail "requested release has no identity file"
[ "$(/usr/bin/tr -d '\r\n' <"$release_dir/RELEASE_COMMIT")" = "$commit" ] || {
  fail "release identity does not match its directory"
}
[ -x "$release_dir/.venv/bin/python" ] || fail "requested release has no Python environment"
[ -f "$release_dir/frontend/dist/index.html" ] || fail "requested release has no frontend build"

next_link="$APP_ROOT/.current.$$.next"
/bin/ln -s "$release_dir" "$next_link"
/bin/mv -f -h "$next_link" "$APP_ROOT/current"

printf 'Activated rollback release %s\n' "$commit"
printf 'The database was not changed and the service was not restarted.\n'
