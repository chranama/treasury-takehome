# Evaluation

**Status:** Local fixture program and deployed P0/P1 evaluation complete

**Last updated:** 2026-08-13

## Evidence layers

The evaluation strategy separates deterministic application behavior from model behavior and from
the deployed network path. A passing unit test does not substitute for extraction evidence, and a
synthetic model result does not establish production accuracy.

| Layer | Question | Network use |
| --- | --- | --- |
| Domain unit tests | Do parsing, normalization, comparison, and aggregation implement the stated rules? | None |
| Intake and API tests | Are files, limits, errors, accounting, persistence, and cleanup bounded? | None |
| Frontend component tests | Do forms, correction, progress, filters, and errors behave as designed? | None |
| Browser tests | Do the compiled UI and real API work together by keyboard and at narrow widths? | None |
| Hosted visual suite | Does the selected model return the required visible observations and uncertainty? | Explicitly paid |
| Deployed smoke and timing | Does one attributable release work through HTTPS with its runtime controls? | Explicitly paid |

Ordinary `pytest`, Vitest, and Playwright runs do not call a model provider. Paid paths require an
explicit acknowledgement and refuse to overwrite an existing evidence file.

## Versioned fixtures

| Revision | Cases | Purpose |
| --- | ---: | --- |
| `live-evaluation-v1` | 4 | Frozen matching, net mismatch, altered warning, and unreadable P0 baseline |
| `hosted-visual-v2` | 18 | Expanded layouts, field variations, ambiguity, warning wording/style, degradation, rotation, small text, and dimensions |
| `p1-packages-v1` | 18 | CSV/XLSX preflight, correction, 25-case processing, export, cleanup, and restart |
| `reviewer-demo-v1` | 6 | Eleven committed P0/P1 files with exact inputs, outcomes, and hashes |

The authoritative v2 schema is [`evals.manifest.EvaluationManifestV2`](../evals/manifest.py).
Manifests record stable IDs, ownership, evaluation layers, source parameters, expected results, and
artifact SHA-256 values. The accepted v1 manifest remains byte-identical for historical attribution.

The F1 renderer uses deterministic source parameters and a recorded embedded-font identity. It
generates metadata-free PNGs and rejects a hash mismatch. Ambiguity uses explicit multiple strings;
degradation runs in a fixed order so blur, glare, obstruction, rotation, and crop remain attributable.

The P1 generator produces paired CSV/XLSX packages at two, five, and 25 cases plus invalid and
lifecycle variants. It records 121 artifact hashes without committing reproducible binary packages.
The reviewer demo is the deliberate exception: its exact downloadable bytes are part of the UI and
are therefore committed under `frontend/public/demo`.

## Automated gate

At F4 completion, the complete local gate contains:

- 418 backend tests;
- 21 frontend component tests; and
- 9 Chromium browser tests.

Ruff lint and formatting, frontend lint, TypeScript compilation, and the Vite production build also
pass. Browser coverage includes P0 match and mismatch, visible keyboard focus, downloadable demo
files, valid and mixed batch preflight, correction, refresh recovery, independent failure, progress,
CSV availability, and a keyboard-usable 25-row result table.

## Hosted extraction evidence

The frozen four-case baseline was run three times without changing its manifest or configuration.
All 12 cases passed: clear labels produced five matches, both known alterations were detected, and
the unreadable case returned uncertainty without fabricated text. Across those runs, median
latency was 2.70 seconds and the slowest request was 8.55 seconds.

The expanded `hosted-visual-v2` Standard-tier run passed all 18 observation and deterministic
correctness gates after fixture ground truth was independently reviewed. It produced no timeout,
retry, malformed response, or provider error. Median latency was 2.00 seconds; nearest-rank p95 and
maximum were 2.89 seconds. Estimated cost for the accepted pass was $0.014103 under the pricing
snapshot used on August 12, 2026.

The first expanded diagnostic exposed two fixture defects rather than model failures: one synthetic
brand phrase was ambiguous, and the missing-warning expectation could not represent both safe
absence outcomes. The ground truth was corrected, preserved observations were replayed against the
metadata-only change, and no extra provider call was made to manufacture a pass.

## Standard versus Fast

A paired benchmark held model, prompt, detail, and four fixtures constant across 40 Standard and
40 Fast requests.

| Tier | Passed | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Standard | 40/40 | 2.09 s | 3.19 s | 4.75 s |
| Fast | 39/40 | 1.70 s | 3.77 s | 7.68 s among successes |

The remaining Fast request reached the 12-second application deadline. Fast reduced median latency
by 18.6%, but did not improve the observed tail and cost approximately twice as much per successful
case at the median. Standard remains the deployment default; Fast is an explicit configuration,
not an automatic fallback.

## Deployed P0 evidence

The public release passed matching, net-contents-mismatch, and unreadable synthetic cases. Ten
consecutive warm matching reviews passed without retry, timeout, or malformed output. Server
duration was 2.59 seconds at the median and 4.32 seconds at the maximum. The command harness measured
3.45 seconds at the median and 5.15 seconds at the maximum end to end, including client startup,
fixture preparation, upload, and response handling.

An earlier matching request took 9.61 seconds against an already healthy process and remains an
observed provider-path outlier rather than being labeled a cold start. Across 21 scripted D3 public
attempts, every provider attempt succeeded without retry and estimated aggregate cost was $0.009145.
Two rapid additional submissions were rejected by the source throttle before provider access.

A current-Chrome run loaded only the same-origin compiled script, stylesheet, and review request,
with no remote runtime dependency or console warning. Controlled service restart and host reboot
restored public readiness without changing the durable accounting ledger.

## Deployed P1 evidence

The schema-2 release passed a zero-cost mixed preflight and in-place correction through HTTPS. The
bounded live batch then completed three independent cases in 8.31 seconds end to end: five matches
for the clear label, four matches and one mismatch for the known net-contents change, and five
`Needs review` checks for the unreadable label. Duplicate start, polling, detail, three-row export,
and refresh recovery passed without an extra job or provider attempt.

An additional 25-case synthetic matching batch completed 25/25 cases with no failure or retry. It
took 41.71 seconds end to end under the global two-request concurrency ceiling; median case duration
was 2.41 seconds and the maximum was 6.99 seconds. The export contained 25 rows. Across the P0
regression and both P1 batches, all 29 D5 provider attempts succeeded and cost an estimated
$0.012355 under the recorded pricing snapshot.

All 28 processed batch images were deleted immediately. A controlled service restart kept the
25-case result readable and did not change the attempt count. The two files still present after the
gate belong to an unstarted synthetic correction draft and remain subject to absolute 24-hour
expiry.

## Running the suites

```bash
uv run pytest
npm --prefix frontend run test
npm --prefix frontend run test:e2e

# Materialize offline fixtures for inspection
uv run python -m evals.batch_suite --materialize-dir .data/fixture-inspection/p1

# Explicitly billable hosted evaluation
uv run python -m evals.live --confirm-paid-run --output .data/evaluations/luna-high.json
```

Use `--manifest fixtures/hosted-visual-v2.json` for the expanded paid visual suite. Raw paid reports
remain local and ignored because they contain diagnostic observations and provider request IDs.

## Claim boundary

These results support a bounded synthetic prototype. They do not estimate accuracy on commercial
labels, prove a stable production latency distribution, demonstrate 200–300-case throughput, or
establish suitability for protected data. The deployed 25-case result demonstrates this configured
prototype ceiling, not production batch capacity.
