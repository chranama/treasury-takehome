# Frontend

**Status:** P0 and P1 implemented

**Last updated:** 2026-08-12

## Application shape

The interface is a React and TypeScript application built with Vite. It has two same-origin pages:

| Path | Purpose | Primary component |
| --- | --- | --- |
| `/` | One label review and five-check result | [`ReviewForm`](../frontend/src/ReviewForm.tsx), [`ReviewResults`](../frontend/src/ReviewResults.tsx) |
| `/batch` | Package preflight, correction, processing, triage, detail, and export | [`BatchWorkflow`](../frontend/src/BatchWorkflow.tsx) |

[`App`](../frontend/src/App.tsx) selects the page from the URL and provides the common header,
workflow navigation, context, and synthetic demo downloads. No client-side routing dependency is
required for two stable paths.

Vite compiles the application to `frontend/dist`. In the production-shaped service FastAPI serves
that build and the API from one origin. Node is not present in the runtime process.

## Single review

The P0 form keeps expected values and the selected file in component state. It validates required
text, numeric ranges, MIME hints, and file size before submitting, while the server repeats the
authoritative checks. A browser object URL provides a local preview and is revoked when it is
replaced or the component unmounts.

Each explicit submission receives a new browser-generated idempotency key. While processing, the
submit action is disabled and a live status replaces the empty result panel. Success displays the
overall outcome first, followed by five side-by-side expected and extracted comparisons. Error
categories map to plain-language actions without exposing provider details.

## Batch review

The batch page uses one spreadsheet control and one multiple-image control. It does not ask the
user to build an archive. A successful preflight places the unguessable batch identifier in the
URL query string so refresh can recover the server draft; expected values and filenames are not
copied into browser storage.

The interface presents readiness counts and row-specific issues. Expected values can be edited in
place, and an image can be replaced for one case without reconstructing the package. Starting
ready cases requires a focused confirmation dialog, especially when other rows still need
correction.

Active batches poll at the server-provided interval, bounded to one through two seconds. Temporary
network or server failures back off to at most eight seconds; polling stops for terminal batches or
when the component unmounts. The results view provides outcome filters, progress and state counts,
case detail, refresh recovery, and terminal CSV download.

## Accessibility and interaction

Implemented behavior includes:

- native labeled controls and a standard file picker;
- a skip link and visible keyboard focus;
- focus on the first invalid field;
- keyboard containment and initial focus in the start confirmation;
- focus transfer to selected batch detail;
- textual status and symbols rather than color alone;
- `aria-live` status for processing and progress;
- a keyboard-focusable horizontal result-table region; and
- a reduced-motion mode.

The application does not claim formal WCAG conformance without a complete audit. Browser tests
cover the principal keyboard path and the 25-row narrow-screen result layout.

## Network and privacy boundary

Runtime scripts, styles, fonts, downloads, previews, and API calls are same-origin. The frontend
does not call OpenAI, object storage, analytics, telemetry, an authentication provider, a public
CDN, or a third-party font service. The production Content Security Policy enforces that boundary;
`blob:` and `data:` image sources are allowed only for local previews.

The interface asks for synthetic or otherwise non-sensitive data. It does not store application
content in `localStorage` or `sessionStorage`. The only durable browser reference is the batch UUID
in the current URL.

## Build and tests

```bash
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Vitest exercises component behavior and request construction. Playwright exercises the compiled
interaction through real browser and API boundaries, including P0 results, batch correction,
refresh, progress, independent failure, CSV availability, keyboard use, and demo-file delivery.

The end-to-end product behavior is described in [Workflows](workflows.md); the route contracts are
listed in [API](api.md).
