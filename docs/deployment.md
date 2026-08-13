# Deployment

**Status:** P0 public and validated; P1 host rollout pending

**Last updated:** 2026-08-12

## Runtime topology

The public prototype is available at
[`https://label-review.mealcheck.dev`](https://label-review.mealcheck.dev). It runs as one native
Uvicorn process on an existing Intel MacBook Air that was already available for the
[MealCheck](https://github.com/chranama/MealCheck) project.

```mermaid
flowchart LR
    B["Desktop browser"] -->|"HTTPS"| C["Cloudflare tunnel"]
    C -->|"127.0.0.1:8081"| U["Uvicorn / FastAPI"]
    U --> F["Compiled React assets"]
    U --> Q["SQLite and private files"]
    U -->|"HTTPS"| O["OpenAI API"]
```

The `mealcheck.dev` domain is reused, but `label-review.mealcheck.dev` has its own route. The
Treasury process, port, releases, database, images, logs, and configuration are separate from
MealCheck. Only the existing cloudflared process and its additive ingress configuration are shared.

This shape was chosen because it was already available, inexpensive, and adequate for a short-lived
single-process demo. Docker would add another runtime and memory cost without improving isolation
on this host. It is not presented as a production government architecture.

## Fixed host contract

| Setting | Value |
| --- | --- |
| Listener | `127.0.0.1:8081` |
| Service label | `dev.mealcheck.label-review` |
| Application root | `/Users/chranama-server/treasury-takehome` |
| Data root | `/Users/chranama-server/treasury-takehome-data` |
| Runtime workers | One |
| Public ingress | Dedicated Cloudflare tunnel hostname |

The application root contains immutable `releases/<commit>` directories and an atomically selected
`current` symlink. The data root holds the protected environment, SQLite database, temporary
images, batch images, and bounded logs outside every release.

A system `launchd` service starts the application after reboot and restarts it after process
failure. The process runs as the existing server account. Application paths are isolated from
MealCheck, but that shared account is not an operating-system sandbox.

## Release process

[`deploy/macos/build-release.sh`](../deploy/macos/build-release.sh) refuses a dirty, unpushed, stale,
or non-`main` checkout. It runs the non-network backend, frontend, lint, build, and browser gates,
then packages tracked source, the lockfile, compiled assets, and the full commit identity into a
checksum-protected archive.

[`deploy/macos/deploy-release.sh`](../deploy/macos/deploy-release.sh) performs the deliberate
release operation:

1. confirm no review is active;
2. build and verify the release locally;
3. transfer it through a private staging directory;
4. verify its checksum and install locked Python dependencies;
5. atomically select the immutable release;
6. ask launchd to replace the old process; and
7. verify the exact commit, listener, health, and readiness.

Pushing `main` does not deploy it. This avoids an unattended paid endpoint update and keeps every
running build attributable to one explicit release decision. Exact operator commands and script
responsibilities are in [`deploy/macos/README.md`](../deploy/macos/README.md).

## Health, readiness, and logs

`GET /healthz` confirms that the process can answer. `GET /readyz` verifies local configuration,
schema version, SQLite access, and temporary storage without making a provider request. Live
extraction cannot become ready unless the API key and all private cost and throttle settings are
present.

Uvicorn access logging is disabled because its default line contains the client address. Error and
service logs rotate at 1 MiB with five archives; a separate startup log rotates at 256 KiB with two
archives. Files inherit private permissions. The SQLite ledger—not logs—is authoritative for
attempt, timing, token, cost, and outcome metadata.

The public P0 service has passed local and HTTPS health/readiness checks, controlled service restart,
host reboot recovery, same-origin browser inspection, and live matching, mismatch, and unreadable
smoke cases. After reboot, public service recovery succeeded even though the Tailscale management
session did not return until local user login; that affects remote administration, not public
availability.

## Network boundary

The Uvicorn listener is localhost-only. The browser loads scripts, styles, downloads, and APIs from
the application origin and requires no VPN or applicant-controlled credentials. The browser has no
direct provider, storage, analytics, telemetry, CDN, font, or authentication dependency. The only
required backend runtime destination is `api.openai.com`.

HTML responses use a same-origin Content Security Policy and `Cache-Control: no-transform` to
prevent browser runtime injection. Trusted Cloudflare client-address handling is enabled only when
the origin is confirmed to be reachable exclusively through the tunnel.

## P1 rollout boundary

The repository's `main` branch contains P1, but the public host still requires its schema-transition
deployment and live batch validation. Schema version 2 is additive and idempotent; however, the old
P0 binary does not correctly own P1 cleanup after migration. The first P1 rollout is therefore a
forward-only transition: retain the predecessor for evidence, but respond to a defect with a tested
forward fix rather than restarting that binary against the upgraded database.

The rollout keeps paid starts disabled through activation, verifies P0 first, runs P1 preflight
without a provider call, then uses one explicitly bounded synthetic live batch before ordinary
starts are re-enabled. Until that gate is completed, public P1 behavior and throughput are not
claimed as deployed evidence.

## Limitations

The deployment has no uptime SLA, authentication, dedicated service account, multi-host failover,
managed queue, FedRAMP authorization, government records controls, or demonstrated Treasury
allowlisting. It is an attributable evaluated prototype, not a production TTB service.
