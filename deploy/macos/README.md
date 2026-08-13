# macOS Deployment Assets

These assets deploy the label-review prototype as one native Uvicorn process on the existing Intel MacBook Air. They contain no credentials or private usage limits. The internal deployment plan owns the complete rollout and evidence gates; this directory supplies the repeatable mechanics.

## Fixed deployment contract

| Setting | Value |
| --- | --- |
| Host account | `chranama-server:staff` |
| Public hostname | `label-review.mealcheck.dev` |
| Application listener | `127.0.0.1:8081` |
| Service label | `dev.mealcheck.label-review` |
| Application root | `/Users/chranama-server/treasury-takehome` |
| Data root | `/Users/chranama-server/treasury-takehome-data` |
| Protected environment | `treasury-takehome-data/config/treasury.env` |
| Database | `treasury-takehome-data/db/treasury.sqlite3` |
| Temporary images | `treasury-takehome-data/tmp` |
| Short-lived P1 images | `treasury-takehome-data/batch-images` |
| Logs | `treasury-takehome-data/logs` |

The application root contains immutable `releases/<full-commit>` directories and an atomically replaced `current` symlink. The start wrapper resolves that link once and gives the process immutable code and frontend paths, so activating a release cannot mix a new frontend with the still-running old backend. SQLite, temporary and short-lived batch images, logs, and secrets remain outside every release. The immediately previous compatible release is retained for rollback.

## Files

- `preflight-host.sh` repeats the safe, read-only host checks.
- `activate-cloudflare-route.sh` is a fail-closed compatibility notice retained for the historical
  D3 rollout. Reusable shared-ingress source is now owned by
  [`chranama/web-server-infrastructure`](https://github.com/chranama/web-server-infrastructure),
  and the server uses an explicitly promoted copy in its protected infrastructure runtime.
- `build-release.sh` validates a clean `main`, runs non-network checks, builds the frontend, and creates a commit-attributed archive and SHA-256 file.
- `deploy-release.sh` orchestrates a deliberate local build, private transfer, installation, guarded restart, and exact-release verification on `mealcheck-server`.
- `enable-cloudflare-client-ip.sh` atomically enables the protected trusted-client setting only after local and public checks pass, with failure rollback.
- `install-release.sh` verifies and installs that archive, creates an Intel-native locked Python 3.12 environment, and atomically activates it without restarting the service.
- `install-service.sh` validates the active release and protected configuration, then installs and loads the one-time system LaunchDaemon without replacing an existing service.
- `restart-service.sh` requires confirmation that no review is active, then asks launchd to replace the account-owned process and verifies its release, listener, health, and readiness.
- `rollback-release.sh` activates an already installed, schema-compatible release without changing SQLite or restarting the service.
- `set-live-extraction.sh` atomically disables or enables new paid work, restarts the service, and restores the prior protected setting if validation fails.
- `start-label-review.sh` validates the protected environment, fixes production paths, and starts the selected release.
- `run_server.py` fixes the single-process listener and provides bounded runtime logging without request access logs.
- `dev.mealcheck.label-review.plist.template` is the reviewed system LaunchDaemon definition.
- `treasury.env.example` is the non-secret protected-environment template.
- `check-service.sh` checks local or public health and readiness without a provider request.
- `smoke_p0.py` runs one explicitly confirmed synthetic live review without printing expected or extracted label content.

## Logging decision

Uvicorn access logging is disabled because its default record includes the client address. Application and Uvicorn error logs use Python's `RotatingFileHandler` at 1 MiB with five retained archives. The startup wrapper keeps a separate bootstrap log, rotating it at startup at 256 KiB with two archives. Files inherit mode 600 from the service's `umask 077`; log directories are mode 700.

This avoids sending `SIGHUP` to the single Uvicorn process during `newsyslog` rotation, which could interrupt a paid request. The content-free SQLite usage ledger remains the authoritative attempt, timing, token, cost, and outcome record.

## Build a release

Run from a clean, synchronized `main` worktree. The builder deliberately refuses feature branches, dirty files, and an existing output archive.

```bash
deploy/macos/build-release.sh
```

The default output is the ignored `.data/releases/` directory. The archive includes the Git-tracked source, locked dependencies, compiled `frontend/dist`, full release commit, and UTC build time. The script runs backend tests and Ruff checks, frontend tests/lint/build, and the Chromium browser suite before creating the archive. Ordinary release checks do not use OpenAI.

## Deploy a release manually

After confirming that no label review is active, run this from the original `main` worktree:

```bash
deploy/macos/deploy-release.sh --confirm-no-active-reviews
```

The orchestrator fetches `origin/main` and refuses a dirty, unpushed, stale, or non-`main`
checkout. It verifies the current server release, runs the complete release build in a fresh local
directory, creates a private staging directory on `mealcheck-server`, transfers the archive,
checksum, and installer, activates the immutable release, and invokes the existing guarded
restart. It succeeds only when the server reports the exact local commit and passes localhost
health and readiness checks. Local and remote staging files are removed when the command exits.

For a schema transition such as the first P1 deployment, keep paid work disabled after activation:

```bash
deploy/macos/deploy-release.sh \
  --confirm-no-active-reviews \
  --disable-live-during-deploy
```

This mode transfers the guarded live-extraction switch, disables new paid starts before release
activation, and leaves extraction disabled after the new process passes local readiness. It does
not replace the operator's obligation to confirm that no P0 request or P1 job is active. After the
P0 regression and P1 preflight gates pass, re-enable starts from the server only after explicitly
accepting that the bounded paid smoke is ready:

```bash
/Users/chranama-server/treasury-takehome/current/deploy/macos/set-live-extraction.sh \
  --enable --confirm-p1-smoke-complete
```

The server does not clone or pull the repository and receives no GitHub credential. Pushing
`main` does not deploy it; running this command is the explicit release decision. If a check fails
after activation, the command reports the previous commit and inspection steps but does not
silently roll back. Confirm database compatibility and active work before any manual rollback or
restart.

## Install without starting the service

Transfer both generated files to a private staging location on the server, then run as `chranama-server`:

```bash
deploy/macos/install-release.sh \
  /private/path/treasury-takehome-COMMIT.tar.gz \
  /private/path/treasury-takehome-COMMIT.tar.gz.sha256
```

Installation creates protected data directories, verifies the artifact, installs Python 3.12 and locked production dependencies with the server's x86_64 `uv`, and changes `current`. It does not install the plist, edit Cloudflare, restart launchd, or enable live extraction.

Copy `treasury.env.example` to the protected environment path, replace placeholders privately, and set mode 600. Begin with live extraction disabled. The wrapper supplies production mode and the database, temporary, short-lived batch-image, frontend, and log paths; those values do not belong in the protected file.

Install the system service only after one release and the protected data directory exist. The
installer requires administrator authorization, refuses an existing plist or loaded service, and
removes a newly installed plist if launchd loading fails:

```bash
sudo /Users/chranama-server/treasury-takehome/current/deploy/macos/install-service.sh
```

This is a one-time D1 action. Release upgrades change `current` and restart the already installed
service separately; they do not rerun the installer.

After activating a release or changing protected configuration, first confirm that no review is
active and then use the account-owned restart path:

```bash
/Users/chranama-server/treasury-takehome/current/deploy/macos/restart-service.sh \
  --confirm-no-active-reviews
```

The helper does not edit configuration or release links. It terminates only the launchd-managed
process owned by `chranama-server`, waits for launchd to replace it, and verifies that the new
process uses the selected immutable release and localhost listener.

## Verify and smoke test

Neither health nor readiness makes a provider call:

```bash
deploy/macos/check-service.sh local
deploy/macos/check-service.sh public
```

The live P0 smoke test can make one provider attempt plus the one bounded eligible retry. It refuses to run without explicit acknowledgment:

```bash
.venv/bin/python -m deploy.macos.smoke_p0 local \
  --fixture clear-matching-label \
  --confirm-live-request
```

The available fixture IDs come from `fixtures/live-evaluation-v1.json`. The smoke output contains only fixture identity, outcome, status counts, duration, and correlation ID—not submitted or extracted text.

## Roll back

List installed releases:

```bash
deploy/macos/rollback-release.sh --list
```

After confirming that the target application supports the current database schema, activate it deliberately:

```bash
deploy/macos/rollback-release.sh --confirm-schema-compatible FULL_COMMIT
```

The script changes only `current`. After checking the selected commit, current database schema,
and active workload, restart with `restart-service.sh --confirm-no-active-reviews`. Never restore
an older SQLite copy as an application rollback.

The P1 migration advances the database from schema version 1 to version 2 by adding tables. The
P0 release does not safely preserve that version or clean P1 content, so the first P1 migration is
a forward-only transition: use a tested forward fix rather than selecting the P0 binary afterward.

## Cloudflare route

The host runs its named tunnel through the system service `dev.mealcheck.tunnel`, using
`/Users/chranama-server/.cloudflared/mealcheck-api.yml`. Treasury uses one hostname-to-origin rule:

```yaml
- hostname: label-review.mealcheck.dev
  service: http://127.0.0.1:8081
```

The historical D3 activation kept the existing `api.mealcheck.dev` service value unchanged, made a
protected backup, validated both ingress rules, and replaced the user-owned tunnel process through
launchd. That application helper is now retired rather than maintained as a second control plane.
Future validation, activation, restart, and rollback use the neutral host-infrastructure runbook;
ordinary Treasury installation, release deployment, and restart never edit or restart
`cloudflared`.

`TREASURY_TRUST_CLOUDFLARE_CLIENT_IP=true` is enabled only after the application is reachable through this tunnel and the listener is confirmed on localhost. The transition helper atomically edits that one protected setting, restarts the application, and restores the prior file if public readiness fails.

Cloudflare Web Analytics can inject a browser beacon into proxied HTML. The application prevents this by serving HTML with `Cache-Control: no-transform` and a same-origin Content Security Policy. Browser inspection remains part of the public release gate; routing success alone is insufficient.

## Boundaries

- Do not run deployment from a feature worktree.
- Do not copy `.env`, `.data`, raw evaluation reports, provider identifiers, or local virtual environments into an archive.
- Do not put secrets or private thresholds in the plist, repository, command arguments, logs, or release evidence.
- Do not restart the service while a paid request or P1 job is active.
- Do not silently replace unavailable live extraction with fake results.

The process runs as the existing `chranama-server` account. Its configured database, temporary,
batch-image, and log writes are confined to the Treasury data root, but this is not an operating-system sandbox
from other files owned by that account.
