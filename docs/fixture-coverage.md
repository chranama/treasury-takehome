# Fixture Coverage and Manifest Contract

**Status:** F0 inventory and schema complete; missing fixtures are scheduled for F1-F4

**Last updated:** 2026-08-12

## Purpose

This inventory assigns every acceptance scenario in the demo specification to an owning
suite, evaluation layer, and expected result. It distinguishes existing evidence from work
that later fixture milestones must add. A missing hosted-model case is not treated as covered
merely because the deterministic comparison rule already has a unit test.

## Acceptance-scenario inventory

The scenario IDs below are stable planning identifiers. `Existing` means the named committed
test or `live-evaluation-v1` case covers that layer today. `Missing` is an explicit assignment
to a later fixture milestone, not an F0 blocker.

| ID | Specification scenario and layer | Coverage | Suite owner | Fixture family | Evaluation layer | Expected result |
| --- | --- | --- | --- | --- | --- | --- |
| A1-H | Clear distilled-spirits label with all five checks, hosted extraction | Existing: `clear-matching-label` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Five `match`; `all_checks_passed`; uncertainty forbidden |
| A2-D | Case, whitespace, and typographic-apostrophe brand variation, deterministic comparison | Existing: comparison normalization and review tests | `comparison` | `deterministic_domain` | `domain` | Brand `match`; five `match` for a complete case; `all_checks_passed` |
| A2-H | Brand-format variation as visible artwork | Missing: F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Visible brand preserved; deterministic result `match`; uncertainty forbidden |
| A3-D | Material brand or class/type difference, deterministic comparison | Existing: comparison review tests | `comparison` | `deterministic_domain` | `domain` | Affected check `needs_review`; overall `needs_review` |
| A3-H | Material brand or class/type difference as visible artwork | Missing: F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Complete differing text preserved; affected check `needs_review`; overall `needs_review` |
| A4-D | Proof equivalence and conflicting ABV/proof, deterministic comparison | Existing: parsing and review tests | `comparison` | `deterministic_domain` | `domain` | Proof equivalent: alcohol `match`; conflict: alcohol `needs_review` and overall `needs_review` |
| A4-H | Proof-only and conflicting ABV/proof artwork | Missing: F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | All visible alcohol statements preserved; proof equivalent passes; conflict needs review |
| A5-D | Equivalent and different metric net contents, deterministic comparison | Existing: parsing and review tests | `comparison` | `deterministic_domain` | `domain` | Equivalent quantity `match`; different quantity `mismatch` and overall `needs_review` |
| A5-H1 | Different net contents as visible artwork | Existing: `mismatched-net-contents` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Net contents `mismatch`; overall `needs_review`; uncertainty forbidden |
| A5-H2 | Equivalent `0.75 L` artwork against expected `750 mL` | Missing: F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Visible quantity preserved; net contents `match`; `all_checks_passed` |
| A6-D | Missing and altered Government Warning, deterministic comparison | Existing: comparison review tests | `comparison` | `deterministic_domain` | `domain` | Warning `mismatch`; overall `needs_review` |
| A6-H1 | One altered warning word as visible artwork | Existing: `altered-government-warning` | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Exact altered text preserved; warning `mismatch`; overall `needs_review` |
| A6-H2 | Missing Government Warning artwork | Missing: F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | Absence reported, not uncertainty; warning `mismatch`; overall `needs_review` |
| A7-H | Low-quality or partially unreadable artwork | Existing baseline: `unreadable-label`; progressive and partial variants missing for F2 | `hosted_extraction` | `hosted_model_visual` | `live_provider` | No fabricated text; affected checks `needs_review`; overall `needs_review`; uncertainty required |
| A8-I | Unsupported, empty, truncated, and corrupt image intake | Existing: generated image-intake and API tests | `image_intake` | `image_intake_security` | `image_intake` | Safe invalid-input response; no provider attempt; five checks are not evaluated if represented as a review result |

The inventory covers all eight categories in the specification. F2 owns the missing hosted
visual variants; F1 must first supply the renderer controls they require. F3 owns P1 package
coverage and F4 owns evaluator-downloadable artifacts.

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

Each case records:

- stable `id` and human-readable `purpose`;
- applicable `families` and `layers`;
- renderer identity, version, and optional deterministic seed;
- renderer-specific `artwork` parameters;
- expected visible text, separate from expected application values;
- required observation properties for all four fields and the warning;
- all five deterministic check statuses and the overall outcome;
- `uncertainty` as `required`, `allowed`, or `forbidden`; and
- artifact basename, media type, and lowercase SHA-256 for every rendered artifact or package.

Hosted-model visual cases must include the live-provider layer and all renderer, visible-text,
expected-application, observation, review, and hash metadata. Rendered cases cannot omit their
artwork parameters or artifact hashes. Extra fields, duplicate case IDs, duplicate membership
values, owner/family mismatches, incomplete five-check expectations, and contradictory overall
outcomes are invalid.

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
The existing `evals.fixtures.load_manifest` path continues to load it. Expanded fixtures will use
a new revision and the v2 contract; neither the old file nor its historical interpretation will
be rewritten.
