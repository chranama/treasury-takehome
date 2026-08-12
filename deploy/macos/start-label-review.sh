#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export PYTHONUNBUFFERED=1
umask 077

APP_ROOT="${TREASURY_DEPLOY_APP_ROOT:-/Users/chranama-server/treasury-takehome}"
DATA_ROOT="${TREASURY_DEPLOY_DATA_ROOT:-/Users/chranama-server/treasury-takehome-data}"
CURRENT_RELEASE="$APP_ROOT/current"
ENV_FILE="$DATA_ROOT/config/treasury.env"
LOG_DIR="$DATA_ROOT/logs"
BOOTSTRAP_LOG="$LOG_DIR/bootstrap.log"
BOOTSTRAP_MAX_BYTES=262144
BOOTSTRAP_BACKUPS=2

fail() {
  printf 'label-review startup: %s\n' "$*" >&2
  exit 1
}

rotate_bootstrap_log() {
  [ -f "$BOOTSTRAP_LOG" ] || return 0
  size=$(/usr/bin/stat -f '%z' "$BOOTSTRAP_LOG" 2>/dev/null || printf '0')
  [ "$size" -ge "$BOOTSTRAP_MAX_BYTES" ] || return 0

  index=$BOOTSTRAP_BACKUPS
  while [ "$index" -gt 1 ]; do
    previous=$((index - 1))
    if [ -f "$BOOTSTRAP_LOG.$previous" ]; then
      /bin/mv -f "$BOOTSTRAP_LOG.$previous" "$BOOTSTRAP_LOG.$index"
    fi
    index=$previous
  done
  /bin/mv -f "$BOOTSTRAP_LOG" "$BOOTSTRAP_LOG.1"
}

[ -d "$DATA_ROOT" ] || fail "data directory is missing"
/bin/mkdir -p "$LOG_DIR"
/bin/chmod 700 "$DATA_ROOT" "$LOG_DIR"
rotate_bootstrap_log
exec >>"$BOOTSTRAP_LOG" 2>&1

[ -L "$CURRENT_RELEASE" ] || fail "current release symlink is missing"
RELEASE_DIR=$(cd "$CURRENT_RELEASE" && pwd -P)
case "$RELEASE_DIR" in
  "$APP_ROOT"/releases/*) ;;
  *) fail "current release points outside the release directory" ;;
esac
[ -f "$RELEASE_DIR/RELEASE_COMMIT" ] || fail "release identity is missing"
[ -x "$RELEASE_DIR/.venv/bin/python" ] || fail "release Python environment is missing"
[ -f "$RELEASE_DIR/frontend/dist/index.html" ] || fail "compiled frontend is missing"
[ -f "$ENV_FILE" ] || fail "protected environment file is missing"

env_owner=$(/usr/bin/stat -f '%Su' "$ENV_FILE")
env_mode=$(/usr/bin/stat -f '%Lp' "$ENV_FILE")
[ "$env_owner" = "$(/usr/bin/id -un)" ] || fail "environment file has the wrong owner"
case "$env_mode" in
  400|600) ;;
  *) fail "environment file permissions must be 400 or 600" ;;
esac

set -a
# The protected file is deployment-controlled shell environment syntax. It must contain values,
# never commands, and remains outside every release and backup path.
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# Non-secret runtime paths and production mode are fixed by the service rather than left to the
# protected file. This keeps release activation and content retention paths consistent.
export TREASURY_APP_ENV=production
export TREASURY_DATABASE_PATH="$DATA_ROOT/db/treasury.sqlite3"
export TREASURY_TEMP_DIR="$DATA_ROOT/tmp"
export TREASURY_FRONTEND_DIST_PATH="$RELEASE_DIR/frontend/dist"
export TREASURY_LOG_DIR="$LOG_DIR"

cd "$RELEASE_DIR"
exec "$RELEASE_DIR/.venv/bin/python" -m deploy.macos.run_server
