# Fixture Coverage and Manifest Contract

**Status:** F0-F4 complete; deployed evaluation remains scheduled for F5

**Last updated:** 2026-08-12

## Purpose

This inventory assigns every acceptance scenario in the demo specification to an owning
suite, evaluation layer, and expected result. It distinguishes existing evidence from work
that later fixture milestones must add. A missing hosted-model case is not treated as covered
merely because the deterministic comparison rule already has a unit test.

## Acceptance-scenario inventory

The scenario IDs below are stable planning identifiers. `Existing` means the named committed
test or fixture revision covers that layer today. `Missing` is an explicit assignment to a later
fixture milestone, not evidence supplied by a different layer.

| ID | Specification scenario and layer | Coverage | Suite owner | Fixture family | Evaluation layer | Expected result |
| --- | --- | --- | --- | --- | --- | --- |
| A1-H | Clear distilled-spirits label with all five checks, hosted extraction | Existing: `clear-matching-label` and `clear-composite` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Five `match`; `all_checks_passed`; uncertainty forbidden |
| A2-D | Case, whitespace, and typographic-apostrophe brand variation, deterministic comparison | Existing: comparison normalization and review tests | `comparison` | `deterministic_domain` | `domain` | Brand `match`; five `match` for a complete case; `all_checks_passed` |
| A2-H | Brand-format variation as visible artwork | Existing: `brand-format-variation` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Visible brand preserved; deterministic result `match`; uncertainty forbidden |
| A3-D | Material brand or class/type difference, deterministic comparison | Existing: comparison review tests | `comparison` | `deterministic_domain` | `domain` | Affected check `needs_review`; overall `needs_review` |
| A3-H | Material brand or class/type difference as visible artwork | Existing: `material-brand-difference` and `material-class-difference` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Complete differing text preserved; affected check `needs_review`; overall `needs_review` |
| A4-D | Proof equivalence and conflicting ABV/proof, deterministic comparison | Existing: parsing and review tests | `comparison` | `deterministic_domain` | `domain` | Proof equivalent: alcohol `match`; conflict: alcohol `needs_review` and overall `needs_review` |
| A4-H | Proof-only and conflicting ABV/proof artwork | Existing: `proof-only` and `conflicting-alcohol` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | All visible alcohol statements preserved; proof equivalent passes; conflict needs review |
| A5-D | Equivalent and different metric net contents, deterministic comparison | Existing: parsing and review tests | `comparison` | `deterministic_domain` | `domain` | Equivalent quantity `match`; different quantity `mismatch` and overall `needs_review` |
| A5-H1 | Different net contents as visible artwork | Existing: `mismatched-net-contents` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Net contents `mismatch`; overall `needs_review`; uncertainty forbidden |
| A5-H2 | Equivalent `0.75 L` artwork against expected `750 mL` | Existing: `equivalent-net-contents` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Visible quantity preserved; net contents `match`; `all_checks_passed` |
| A6-D | Missing and altered Government Warning, deterministic comparison | Existing: comparison review tests | `comparison` | `deterministic_domain` | `domain` | Warning `mismatch`; overall `needs_review` |
| A6-H1 | One altered warning word as visible artwork | Existing: `altered-government-warning` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Exact altered text preserved; warning `mismatch`; overall `needs_review` |
| A6-H2 | No Government Warning visible in submitted artwork | Existing: `missing-warning` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | No warning text invented; `not_visible` or conservative uncertainty produces review, never a pass |
| A7-H | Low-quality or partially unreadable artwork | Existing: `small-warning-threshold`, `obscured-warning`, and `degraded-unreadable` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Readable small text is preserved; obscured or degraded evidence is not fabricated; affected checks need review |
| A8-I | Unsupported, empty, truncated, and corrupt image intake | Existing: generated image-intake and API tests | `image_intake` | `image_intake_security` | `image_intake` | Safe invalid-input response; no provider attempt; five checks are not evaluated if represented as a review result |

The inventory covers all eight categories in the specification. The `hosted-visual-v2` revision
supplies the F2 visual variants using the F1 renderer, `p1-packages-v1` owns P1 package coverage,
and `reviewer-demo-v1` owns the evaluator-downloadable artifacts.

## Suite ownership

Every v2 manifest has exactly one primary `owner`. Each case must include that owner's family,
although it may also belong to other families when the same source legitimately produces, for
example, both live-provider evidence and a reviewer demo artifact.

| Owner | Required family | Responsibility |
| --- | --- | --- |
| `comparison` | `deterministic_domain` | Normalization, parsing, deterministic comparison, and aggregation |
| `image_intake` | `image_intake_security` | Byte validation, preparation boundaries, metadata stripping, and cleanup |
| `hosted_extraction` | `hosted_model_visual` | Visible observation extraction and uncertainty behavior |
| `batch_workflow` | `p1_batch_package` | Spreadsheet association, preflight, case isolation, progress, and export |
| `demo_bundle` | `reviewer_demo` | Small synthetic artifacts intended for an evaluator to download |

## Manifest v2 contract

The authoritative executable contract is `evals.manifest.EvaluationManifestV2`. Tooling may
obtain its JSON Schema through `manifest_schema_v2()`; the Pydantic model remains the single
source of truth.

The shared top-level fields are:

- `schema_version`: exactly `2`;
- `revision`: stable lowercase hyphenated suite revision;
- `owner`: one suite owner from the table above;
- `purpose`: a human-readable statement of what the suite establishes; and
- `cases`: one or more uniquely identified cases owned by the suite.

Each case records the following common fields:

- stable `id` and human-readable `purpose`;
- applicable `families` and `layers`;
- layer-specific source parameters and expected results; and
- artifact basename, media type, and lowercase SHA-256 for every rendered artifact or package.

Hosted-model visual cases must include the live-provider layer and all renderer, visible-text,
expected-application, observation, review, uncertainty, and hash metadata. A visual check may list
multiple safe statuses only when accepted observation states deterministically lead to the same
review outcome.

P1 package cases must include the preflight layer, generator revision and variant, requested
spreadsheet formats, row and upload filenames, expected issue codes and counts, and hashes for every
generated spreadsheet and image. Lifecycle cases additionally record selection, terminal counts,
outcomes, concurrency, attempts, cleanup, replay, or export properties relevant to that case.

Reviewer demo cases must include their workflow, scenario, public relative directory, source
revision and case IDs, step-by-step instructions, and artifact hashes. P0 demos additionally carry
the exact expected application and review result; P1 example packages carry their preflight issue
and readiness expectations.

Rendered cases cannot omit their artwork parameters or artifact hashes. Extra fields, duplicate
case IDs, duplicate membership values, owner/family mismatches, incomplete layer-specific
expectations, contradictory overall outcomes, and duplicate expected values are invalid.

Candidate policies have deliberately narrow meanings:

- `exact`: the observed candidate set must equal the expected visible strings;
- `contains_all`: every expected visible string must be present, but an explicitly designed case
  may tolerate additional candidates; and
- `empty`: no candidate may be returned.

Warning text and heading policies are `exact`, `absent`, or `any`. Allowed visibility,
readability, and weight values are explicit nonempty sets. These policies let a later evaluator
distinguish known absence from unreadability and keep ground truth independent of the first model
response.

## Legacy evidence boundary

`fixtures/live-evaluation-v1.json` remains the immutable input for the accepted August 11, 2026
Luna evidence. It retains its original schema and SHA-256
`9521ca3e94a3ce88bd14fc783d7905b7a317454817c593294a622755873a1797`.
The existing `evals.fixtures.load_manifest` path continues to load it. The expanded
`hosted-visual-v2` revision uses the v2 contract; neither the old file nor its historical
interpretation was rewritten.

## F1 renderer decisions

The expanded renderer is additive: `evals.renderer` serves v2 suites, while the accepted v1
renderer and manifest remain unchanged. The plan intentionally left the following implementation
details open; F1 resolves them as follows:

- The renderer uses Pillow's embedded Aileron Regular font instead of adding a binary font asset.
  Each manifest records `pillow-embedded-aileron-regular` as the font identity, and the locked
  Pillow dependency supplies the font without network access.
- The renderer is currently fully deterministic and has no random effects. A v2 case therefore
  records a null seed; non-null seeds are rejected until a seeded effect is deliberately added.
- Ambiguity is represented by one to three explicit source strings per field rather than by a
  probabilistic text generator. This supports multiple plausible brands, quantities, or alcohol
  statements while keeping ground truth reviewable.
- Layout is either one panel or a front/back composite. The composite allocates 43 percent of its
  usable width to the front panel and permits only the back panel to rotate independently. A
  rotated panel is scaled and centered within its original slot so rotation does not silently add
  the separately controlled crop condition.
- Typography controls field sizes and the observable brand, warning-heading, and warning-body
  weights. Bold text is produced with a deterministic stroke around the same recorded font.
- Degradations run in a fixed order: contrast, glare, obstruction, blur, global rotation, then
  crop. Glare, obstruction, and crop geometry use normalized coordinates so the same source works
  at different canvas sizes.
- Canvas generation is bounded to 6,000 pixels per side and 40 megapixels, matching the current
  provisional intake ceiling without changing that product limit.
- Artifact SHA-256 is calculated over the final metadata-free RGB PNG. Manifest rendering checks
  that hash and deletes a mismatched output so it cannot be mistaken for accepted evidence.
- Reproducible test artifacts remain temporary. F1 itself did not commit generated binaries; F2
  later reused the renderer to create its manifest and still renders PNGs only when needed.

On August 12, 2026, the generated clear composite, single panel, typography variant, two-brand
and two-quantity ambiguity case, independently rotated back panel, combined degradation case,
and globally rotated/cropped case were manually inspected. Each intended condition was visibly
distinct. The inspection caught and corrected an initially clipped second quantity and a panel
rotation implementation that also cropped the panel. The final rotation control preserves the
complete panel boundary; crop remains an independent degradation. These temporary artifacts are
test evidence for the renderer controls, not provider-quality evidence or committed demo files.

## F2 hosted visual regression

The committed `hosted-visual-v2` manifest defines 18 deterministic cases spanning clear and
single-panel layouts, brand and class variation, proof and conflicting alcohol statements,
equivalent and mismatched quantities, warning wording and weight, ambiguity, rotation, small text,
obstruction, combined degradation, and a near-boundary wide composite. The manifest stores source
artwork parameters and artifact hashes; generated PNGs remain reproducible and uncommitted.

The live harness renders and hash-checks each artifact before provider access, evaluates visible
observations independently of deterministic comparison, and records both gates. Ordinary tests
exercise that complete path with fixed provider responses, including rejection of fabricated
candidates and required uncertainty for unreadable artwork.

On August 12, 2026, the accepted Standard-tier `gpt-5.6-luna` configuration at `high` detail met
all 18 observation and correctness gates. The paid pass produced no timeout, retry, malformed
response, or provider error. Median latency was 2.00 seconds and nearest-rank p95 and maximum were
2.89 seconds across 18 cases; estimated provider cost was $0.014103 under the existing pricing
snapshot. These synthetic fixtures support the prototype decision but do not estimate accuracy on
commercial labels or establish a production latency distribution.

The initial diagnostic exposed two ground-truth defects rather than product failures: an
ambiguous synthetic brand phrase and an expectation that could not represent both safe outcomes
for a warning absent from the submitted panels. The brand was made unambiguous, and the manifest
now permits `mismatch` or conservative `needs_review` for that warning while always requiring the
overall review outcome. The preserved accepted observations replayed 18/18 after this metadata-only
correction; no extra provider call was used to manufacture a passing result.

## F3 P1 package regression

The committed `p1-packages-v1` manifest defines 18 deterministic package and lifecycle cases. It
generates paired CSV/XLSX packages at two, five, and 25 cases plus missing, extra, duplicate,
Unicode, ambiguous, invalid-value, corrupt-image, and over-limit inputs. Named lifecycle cases own
correction and replacement, a mixed 25-case run, formula-safe export, ready-only expiry cleanup,
and partial-restart recovery.

The suite records 121 spreadsheet and image hashes without committing the reproducible binaries.
`evals.batch_suite` can materialize them for inspection, while ordinary tests generate them in
temporary directories. CSV and XLSX forms parse to identical normalized rows, every preflight case
meets its expected issue and readiness counts, and separately generated XLSX files remain
byte-identical across process timestamps.

The real API regressions consume the named correction, mixed-lifecycle, cleanup, and restart cases.
The mixed fixed-response package completes 23 cases and isolates two failures under observed
concurrency two; cleanup deletes processed images and later expires unselected content; restart
preserves completed work, interrupts uncertain work, and makes no replay attempt. Existing export
and browser tests cover formula neutralization, progress, filtering, detail, download, and a
keyboard-usable 25-row result set. All F3 gates are offline and make no provider request.

## F4 reviewer demo bundle

The committed `reviewer-demo-v1` manifest defines six reviewer scenarios and 11 downloadable
artifacts. It includes a matching P0 label, a material net-contents mismatch, a deliberately
unreadable label, blank CSV and XLSX templates, a two-case valid P1 package, and a mixed package
with one ready and one repairable row.

The generated files live under `frontend/public/demo`, so Vite copies them into the compiled site
and the existing same-origin FastAPI static-file service publishes them without a new API or data
store. Both the interface and README state the exact P0 inputs, expected outcomes, P1 file
selections, expected preflight counts, and mixed-case corrections. P1 examples remain separate
spreadsheet and image files rather than ZIP archives, matching the accepted product contract.

Unlike the broader generated suites, these binaries are committed because the exact downloadable
files are the reviewer-facing product artifact. `evals.demo_bundle` reproduces every byte from the
F2 visual renderer and P1 template generator. Offline regressions compare a fresh generation with
the committed tree, verify all manifest hashes and image decodability, and run both P1 packages
through the real preflight service. Frontend and browser tests verify that the instructions and
static URLs remain present and usable.
