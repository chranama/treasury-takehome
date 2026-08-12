#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
umask 077

APP_ROOT="${TREASURY_DEPLOY_APP_ROOT:-/Users/chranama-server/treasury-takehome}"
DATA_ROOT="${TREASURY_DEPLOY_DATA_ROOT:-/Users/chranama-server/treasury-takehome-data}"
UV_BIN="${TREASURY_DEPLOY_UV_BIN:-/usr/local/bin/uv}"
ARCHIVE="${1:-}"
CHECKSUM_FILE="${2:-}"

fail() {
  printf 'install-release: %s\n' "$*" >&2
  exit 1
}

[ -n "$ARCHIVE" ] && [ -n "$CHECKSUM_FILE" ] || {
  fail "usage: install-release.sh RELEASE.tar.gz RELEASE.tar.gz.sha256"
}
[ -f "$ARCHIVE" ] || fail "release archive does not exist"
[ -f "$CHECKSUM_FILE" ] || fail "checksum file does not exist"
[ -x "$UV_BIN" ] || fail "uv is not executable at the configured path"

expected_checksum=$(/usr/bin/awk 'NR == 1 {print $1}' "$CHECKSUM_FILE")
case "$expected_checksum" in
  *[!0-9a-fA-F]*|'') fail "checksum file does not contain a SHA-256 digest" ;;
esac
[ "${#expected_checksum}" -eq 64 ] || fail "checksum must contain 64 hexadecimal characters"
actual_checksum=$(/usr/bin/shasum -a 256 "$ARCHIVE" | /usr/bin/awk '{print $1}')
[ "$actual_checksum" = "$expected_checksum" ] || fail "release checksum does not match"

while IFS= read -r entry; do
  normalized=${entry#./}
  case "$normalized" in
    /*|../*|*/../*|*/..|..) fail "release archive contains an unsafe path" ;;
  esac
done < <(/usr/bin/tar -tzf "$ARCHIVE")

/bin/mkdir -p "$APP_ROOT/releases"
/bin/mkdir -p "$DATA_ROOT/config" "$DATA_ROOT/db" "$DATA_ROOT/tmp" "$DATA_ROOT/logs"
/bin/chmod 700 "$DATA_ROOT" "$DATA_ROOT/config" "$DATA_ROOT/db" "$DATA_ROOT/tmp" "$DATA_ROOT/logs"

staging=$(/usr/bin/mktemp -d "$APP_ROOT/releases/.incoming.XXXXXX")
release_dir=""
activated=0
cleanup() {
  if [ -d "$staging" ]; then
    /bin/rm -rf -- "$staging"
  fi
  if [ "$activated" -eq 0 ] && [ -n "$release_dir" ] && [ -d "$release_dir" ]; then
    case "$release_dir" in
      "$APP_ROOT"/releases/*) /bin/rm -rf -- "$release_dir" ;;
      *) printf 'install-release: refused unsafe cleanup path\n' >&2 ;;
    esac
  fi
}
trap cleanup EXIT INT TERM

/usr/bin/tar -xzf "$ARCHIVE" -C "$staging"
if /usr/bin/find "$staging" -type l -print -quit | /usr/bin/grep -q .; then
  fail "release archive must not contain symbolic links"
fi
[ -f "$staging/RELEASE_COMMIT" ] || fail "release identity is missing"
commit=$(/usr/bin/tr -d '\r\n' <"$staging/RELEASE_COMMIT")
case "$commit" in
  *[!0-9a-f]*|'') fail "release identity is not a Git SHA-1" ;;
esac
[ "${#commit}" -eq 40 ] || fail "release identity must be a full Git SHA-1"

[ -f "$staging/uv.lock" ] || fail "release is missing uv.lock"
[ -f "$staging/pyproject.toml" ] || fail "release is missing pyproject.toml"
[ -f "$staging/frontend/dist/index.html" ] || fail "release is missing compiled frontend assets"
[ -x "$staging/deploy/macos/start-label-review.sh" ] || fail "release start wrapper is not executable"

release_dir="$APP_ROOT/releases/$commit"
[ ! -e "$release_dir" ] || fail "release is already installed"
/bin/mv "$staging" "$release_dir"
staging=""

printf 'Installing locked Python runtime for %s...\n' "$commit"
(
  cd "$release_dir"
  "$UV_BIN" sync --locked --no-dev --python 3.12
)
[ -x "$release_dir/.venv/bin/python" ] || fail "release Python environment was not created"
[ -x "$release_dir/.venv/bin/uvicorn" ] || fail "release Uvicorn executable was not created"

next_link="$APP_ROOT/.current.$$.next"
/bin/ln -s "$release_dir" "$next_link"
/bin/mv -f -h "$next_link" "$APP_ROOT/current"
activated=1

printf 'Installed and activated release %s\n' "$commit"
printf 'The service was not restarted. Verify schema compatibility, then restart launchd deliberately.\n'
