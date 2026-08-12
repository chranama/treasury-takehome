#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PATH
umask 077

PROJECT_ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
REMOTE_HOST="mealcheck-server"
REMOTE_APP_ROOT="/Users/chranama-server/treasury-takehome"
REMOTE_CURRENT="$REMOTE_APP_ROOT/current"
REMOTE_STAGE_PREFIX="/Users/chranama-server/.treasury-deploy."

local_output=""
remote_stage=""
previous_commit=""
activated=0
disable_live_during_deploy=0

fail() {
  printf 'deploy-release: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  status=$?
  cleanup_failed=0
  trap - EXIT INT TERM
  set +e

  if [ -n "$remote_stage" ]; then
    stage_suffix=${remote_stage#"$REMOTE_STAGE_PREFIX"}
    case "$stage_suffix" in
      ''|*[!A-Za-z0-9]*)
        printf 'deploy-release: refusing to remove unexpected remote staging path: %s\n' \
          "$remote_stage" >&2
        ;;
      *)
        ssh "$REMOTE_HOST" /bin/rm -rf -- "$remote_stage" >/dev/null 2>&1 || {
          printf 'deploy-release: remote staging cleanup failed: %s\n' "$remote_stage" >&2
          cleanup_failed=1
        }
        ;;
    esac
  fi

  if [ -n "$local_output" ]; then
    /bin/rm -rf -- "$local_output" || {
      printf 'deploy-release: local staging cleanup failed: %s\n' "$local_output" >&2
      cleanup_failed=1
    }
  fi

  if [ "$status" -eq 0 ] && [ "$cleanup_failed" -ne 0 ]; then
    status=1
  fi

  if [ "$status" -ne 0 ] && [ "$activated" -eq 1 ]; then
    printf '\nRelease activation completed before the failure. Automatic rollback was not attempted.\n' >&2
    printf 'Previous release: %s\n' "$previous_commit" >&2
    printf 'Inspect the host with:\n' >&2
    printf '  ssh %s %s/deploy/macos/check-service.sh local\n' \
      "$REMOTE_HOST" "$REMOTE_CURRENT" >&2
    printf '  ssh %s /bin/launchctl print system/dev.mealcheck.label-review\n' \
      "$REMOTE_HOST" >&2
    printf 'Confirm schema compatibility and active work before selecting or restarting a release.\n' >&2
  fi

  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

[ "${1:-}" = "--confirm-no-active-reviews" ] || {
  fail "usage: deploy-release.sh --confirm-no-active-reviews [--disable-live-during-deploy]"
}
case "$#:${2:-}" in
  1:) ;;
  2:--disable-live-during-deploy) disable_live_during_deploy=1 ;;
  *) fail "usage: deploy-release.sh --confirm-no-active-reviews [--disable-live-during-deploy]" ;;
esac

for command_name in git ssh scp; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

cd "$PROJECT_ROOT"
[ "$(git branch --show-current)" = "main" ] || fail "deployments must run from main"
[ -z "$(git status --porcelain --untracked-files=all)" ] || fail "working tree must be clean"

upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)
[ "$upstream" = "origin/main" ] || fail "main must track origin/main"
git fetch --quiet origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || {
  fail "main must match the current origin/main before deployment"
}

commit=$(git rev-parse HEAD)
case "$commit" in
  *[!0-9a-f]*|'') fail "Git returned an invalid deployment commit" ;;
esac
[ "${#commit}" -eq 40 ] || fail "deployment commit must be a full SHA-1"

previous_commit=$(
  ssh "$REMOTE_HOST" \
    "/bin/test -f '$REMOTE_CURRENT/RELEASE_COMMIT' && /usr/bin/tr -d '\\r\\n' < '$REMOTE_CURRENT/RELEASE_COMMIT'"
)
case "$previous_commit" in
  *[!0-9a-f]*|'') fail "remote current release has an invalid commit identity" ;;
esac
[ "${#previous_commit}" -eq 40 ] || fail "remote current release identity must be a full SHA-1"
[ "$previous_commit" != "$commit" ] || fail "commit $commit is already the active release"

ssh "$REMOTE_HOST" "$REMOTE_CURRENT/deploy/macos/check-service.sh" local >/dev/null
printf 'Current remote release %s is healthy and ready.\n' "$previous_commit"

local_output=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/treasury-deploy-release.XXXXXX")
"$PROJECT_ROOT/deploy/macos/build-release.sh" "$local_output"

archive="$local_output/treasury-takehome-$commit.tar.gz"
checksum="$archive.sha256"
installer="$PROJECT_ROOT/deploy/macos/install-release.sh"
live_switch="$PROJECT_ROOT/deploy/macos/set-live-extraction.sh"
[ -f "$archive" ] || fail "release builder did not produce the expected archive"
[ -f "$checksum" ] || fail "release builder did not produce the expected checksum"
[ -x "$installer" ] || fail "release installer is missing or not executable"
[ -x "$live_switch" ] || fail "live-extraction switch is missing or not executable"

remote_stage=$(
  ssh "$REMOTE_HOST" /usr/bin/mktemp -d "/Users/chranama-server/.treasury-deploy.XXXXXX"
)
stage_suffix=${remote_stage#"$REMOTE_STAGE_PREFIX"}
case "$stage_suffix" in
  ''|*[!A-Za-z0-9]*) fail "remote host returned an unexpected staging path" ;;
esac
ssh "$REMOTE_HOST" /bin/chmod 700 "$remote_stage"

scp "$archive" "$checksum" "$installer" "$live_switch" "$REMOTE_HOST:$remote_stage/"

archive_name=$(basename "$archive")
checksum_name=$(basename "$checksum")
ssh "$REMOTE_HOST" /bin/chmod 700 "$remote_stage/install-release.sh"
ssh "$REMOTE_HOST" /bin/chmod 700 "$remote_stage/set-live-extraction.sh"
if [ "$disable_live_during_deploy" -eq 1 ]; then
  ssh "$REMOTE_HOST" "$remote_stage/set-live-extraction.sh" \
    --disable --confirm-no-active-reviews
fi
ssh "$REMOTE_HOST" "$remote_stage/install-release.sh" \
  "$remote_stage/$archive_name" "$remote_stage/$checksum_name"
activated=1

ssh "$REMOTE_HOST" "$REMOTE_CURRENT/deploy/macos/restart-service.sh" \
  --confirm-no-active-reviews

active_commit=$(
  ssh "$REMOTE_HOST" \
    "/usr/bin/tr -d '\\r\\n' < '$REMOTE_CURRENT/RELEASE_COMMIT'"
)
[ "$active_commit" = "$commit" ] || fail "remote host did not activate the expected commit"
ssh "$REMOTE_HOST" "$REMOTE_CURRENT/deploy/macos/check-service.sh" local

printf 'Deployed commit %s to %s; local health and readiness passed.\n' \
  "$commit" "$REMOTE_HOST"
if [ "$disable_live_during_deploy" -eq 1 ]; then
  printf 'Live extraction remains disabled pending explicit smoke validation.\n'
fi
