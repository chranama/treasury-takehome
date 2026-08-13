#!/bin/bash

set -euo pipefail

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PATH
export LANG=C
export LC_ALL=C

fail() {
  printf 'check-remote-service: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--connect] mealcheck-server|mealcheck-server-cf\n' "$0" >&2
  exit 64
}

CONNECT=0
case "$#:${1:-}" in
  1:mealcheck-server|1:mealcheck-server-cf)
    REMOTE_HOST=$1
    ;;
  2:--connect)
    REMOTE_HOST=$2
    CONNECT=1
    ;;
  *) usage ;;
esac
case "$REMOTE_HOST" in
  mealcheck-server|mealcheck-server-cf) ;;
  *) fail "unsupported administration alias: $REMOTE_HOST" ;;
esac

SSH_BIN=${TREASURY_REMOTE_SSH_BIN:-}
if [ -z "$SSH_BIN" ]; then
  SSH_BIN=$(command -v ssh) || fail "ssh is unavailable"
fi
[ -x "$SSH_BIN" ] || fail "SSH executable is unavailable"

effective=$("$SSH_BIN" -G -T "$REMOTE_HOST") || fail "cannot resolve SSH configuration for $REMOTE_HOST"
user=$(printf '%s\n' "$effective" | /usr/bin/awk '$1 == "user" {print $2; exit}')
host_key_alias=$(printf '%s\n' "$effective" | /usr/bin/awk '$1 == "hostkeyalias" {print $2; exit}')
[ "$user" = "chranama-server" ] || fail "effective SSH user is not chranama-server"
[ -n "$host_key_alias" ] || fail "effective SSH configuration lacks HostKeyAlias"

printf 'Selected Treasury administration alias: %s\n' "$REMOTE_HOST"
if [ "$CONNECT" -eq 0 ]; then
  printf 'Configuration-only check passed; no network connection was attempted.\n'
  exit 0
fi

printf 'Starting read-only Treasury checks; no fallback alias will be attempted.\n'
"$SSH_BIN" -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE_HOST" \
  /Users/chranama-server/treasury-takehome/current/deploy/macos/check-service.sh local
printf 'Read-only Treasury check passed through %s.\n' "$REMOTE_HOST"
