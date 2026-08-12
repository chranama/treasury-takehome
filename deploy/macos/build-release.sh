#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PATH
umask 077

PROJECT_ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
OUTPUT_DIR="${1:-$PROJECT_ROOT/.data/releases}"

fail() {
  printf 'build-release: %s\n' "$*" >&2
  exit 1
}

for command_name in git uv npm tar shasum; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

cd "$PROJECT_ROOT"
[ "$(git branch --show-current)" = "main" ] || fail "releases must be built from main"
[ -z "$(git status --porcelain --untracked-files=all)" ] || fail "working tree must be clean"
upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)
[ -n "$upstream" ] || fail "main must have a configured upstream"
[ "$(git rev-parse HEAD)" = "$(git rev-parse '@{upstream}')" ] || {
  fail "main must match its fetched upstream before release"
}

commit=$(git rev-parse HEAD)
case "$commit" in
  *[!0-9a-f]*|'') fail "Git returned an invalid release commit" ;;
esac
[ "${#commit}" -eq 40 ] || fail "release commit must be a full SHA-1"

printf 'Running backend checks...\n'
uv run pytest
uv run ruff check .
uv run ruff format --check .

printf 'Installing and checking frontend dependencies...\n'
npm --prefix frontend ci
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e

[ -f frontend/dist/index.html ] || fail "frontend build did not produce index.html"

/bin/mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd -P)
archive="$OUTPUT_DIR/treasury-takehome-$commit.tar.gz"
checksum_file="$archive.sha256"
[ ! -e "$archive" ] || fail "release archive already exists: $archive"
[ ! -e "$checksum_file" ] || fail "release checksum already exists: $checksum_file"

staging=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/treasury-release.XXXXXX")
cleanup() {
  /bin/rm -rf -- "$staging"
}
trap cleanup EXIT INT TERM

git archive --format=tar HEAD | /usr/bin/tar -xf - -C "$staging"
/bin/mkdir -p "$staging/frontend/dist"
/bin/cp -R frontend/dist/. "$staging/frontend/dist/"
printf '%s\n' "$commit" >"$staging/RELEASE_COMMIT"
/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' >"$staging/RELEASE_BUILT_AT"

/usr/bin/tar -czf "$archive" -C "$staging" .
(
  cd "$OUTPUT_DIR"
  /usr/bin/shasum -a 256 "$(basename "$archive")" >"$(basename "$checksum_file")"
)

printf 'Release archive: %s\n' "$archive"
printf 'Checksum file: %s\n' "$checksum_file"
printf 'Commit: %s\n' "$commit"
