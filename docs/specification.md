# Demo Specification

**Status:** Implemented

**Version:** 1.0

**Last updated:** 2026-08-12

**Source:** [Treasury take-home instructions](https://github.com/treasurytakehome-rgb/instructions)

## Purpose

The Alcohol Label Verification Prototype is a standalone review aid that compares expected application information with visible alcohol-label artwork. It is intended to reduce routine visual matching while returning ambiguous, conflicting, or unreadable cases to a human reviewer.

The prototype does not approve or reject a Certificate of Label Approval (COLA), perform a complete regulatory review, or replace reviewer judgment.

## Demo scope and priorities

### P0: single-label review

P0 is the required finished demo. It covers one distilled-spirits review containing:

- expected brand name;
- expected class/type;
- expected alcohol by volume (ABV);
- expected net contents; and
- one label image containing the relevant artwork.

The application extracts those four visible fields, checks the Government Health Warning, performs deterministic comparisons, and presents an understandable result.

### P1: bounded batch review

P1 demonstrates how the single-review model can be applied to multiple applications. It is capped at 25 cases and is not a claim of production capacity for the 200–300-application batches described by stakeholders.

## Single-review workflow

The reviewer shall be able to:

1. enter the four expected application values;
2. choose one label image;
3. start the review through one prominent action; and
4. inspect the overall result and five individual checks.

The Government Warning does not require a manually entered expected value. It is compared with the canonical warning defined by regulation.

### Single-review input

The interface shall accept:

- JPEG, PNG, or WebP images;
- files no larger than 10 MB;
- brand name as text;
- class/type as text;
- ABV as a percentage; and
- net contents with a supported unit.

The recommended minimum image dimension is 800 pixels on the shortest side. One image may contain a single label or a composite of front and back artwork. PDFs and real multi-image application packages are outside the demo scope.

Required fields, supported image formats, and file limits shall be visible near the corresponding controls. The reviewer shall see an image preview before processing. Entered values shall remain available after a recoverable error.

## Review results

The result shall display:

- an overall outcome;
- one result for brand name, class/type, ABV, net contents, and Government Warning;
- expected and extracted values side by side;
- normalized values when conversion or normalization occurred;
- a brief reason for every discrepancy, uncertainty, or unevaluated check; and
- processing duration.

### Overall outcomes

- **All checks passed:** all five checks are matches.
- **Needs review:** at least one check is a mismatch, uncertain, ambiguous, or not visible.
- **Unable to process:** the file is invalid or processing fails before the checks can run.

### Individual check results

- **Match**
- **Mismatch**
- **Needs review**
- **Not evaluated**

The application shall not use **Approved** or **Rejected**, because those terms imply an official TTB determination. Numeric model-confidence scores, if displayed, are supporting information only and shall not decide an outcome.

## Comparison requirements

Structured extraction shall occur before comparison. Missing or unreadable values shall remain unknown rather than being invented. Deterministic application logic—not a free-form model judgment—shall own normalization, comparison, and overall-status calculation.

### Brand name

Brand comparison shall ignore case, leading and trailing whitespace, repeated internal whitespace, and typographic-versus-straight apostrophes. It shall not silently ignore missing words, reordered words, or other material differences.

- `STONE'S THROW` and `Stone’s Throw` shall match.
- `Stone Throw` and `Stone's Throw` shall return **Needs review**.

Raw expected and extracted values shall remain visible when normalized values match.

### Class/type

Class/type comparison shall ignore case and repeated whitespace but otherwise remain conservative. Missing, added, or reordered material words shall return **Needs review**. Semantic similarity alone shall not be presented as proof of regulatory equivalence.

### Alcohol content

The application shall recognize common ABV forms and unambiguous U.S. proof statements. When proof is used, it shall calculate and display `ABV = proof / 2`.

- Expected `45%` shall match `45% Alc./Vol.`.
- Expected `45%` shall match `90 Proof`.
- Conflicting ABV and proof statements shall return **Needs review**.

Values shall match to their displayed precision. The prototype shall not invent a regulatory tolerance.

### Net contents

The application shall parse common metric expressions and normalize equivalent units.

- `750 mL`, `750 ml`, and `0.75 L` shall match.
- Different quantities shall return **Mismatch**.
- Unrecognized or ambiguous units shall return **Needs review**.

### Government Health Warning

The warning shall be compared with the wording in [27 CFR 16.21](https://www.ecfr.gov/current/title-27/chapter-I/subchapter-A/part-16/subpart-C/section-16.21):

> GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

The check shall behave as follows:

- Missing warning text returns **Mismatch**.
- Any word, number, or clause difference returns **Mismatch**, apart from normalized whitespace and line breaks.
- `GOVERNMENT WARNING` must be uppercase.
- The heading must appear bold and the remaining text non-bold when style is visually determinable.
- Uncertain heading style or poor readability returns **Needs review**.
- Physical type size, characters per inch, and label affixation are **Not evaluated** because they cannot be established reliably from an unscaled image.

## Batch-review workflow

P1 shall use a guided workflow intended for reviewers who are comfortable with ordinary spreadsheets but not technical packaging formats.

### Batch input

The primary workflow shall accept:

1. an Excel workbook (`.xlsx`) or UTF-8 CSV containing the expected values; and
2. multiple JPEG, PNG, or WebP label images selected together.

The application shall provide a downloadable spreadsheet template with these human-readable columns:

- Application ID
- Label Image Filename
- Expected Brand
- Expected Class/Type
- Expected ABV
- Expected Net Contents

ZIP is not an accepted input. The spreadsheet and images shall be selected separately.

### Preflight review

Before processing, the interface shall:

- match each spreadsheet row to an image by filename;
- identify missing images, duplicate application IDs, duplicate filenames, unsupported files, invalid values, and batches over 25 cases;
- show counts of cases that are ready and cases that need correction; and
- explain each problem in plain language near the affected row.

The reviewer shall be able to replace an image or correct an invalid value without reconstructing the entire batch. Invalid rows shall not be processed. If some rows are ready, the reviewer may either correct all problems or explicitly process the ready rows only.

### Processing and results

- Each case shall be processed independently so one failure does not fail the batch.
- Progress shall show completed and total cases.
- The results table shall include application ID, overall outcome, duration, and a short reason when review is required.
- Results shall be filterable by outcome, with **Needs review** and failed cases easy to find.
- Selecting a row shall open the same detailed comparison used by the single-review workflow.
- Results shall be downloadable as CSV.

Durable queue resume, long-term history, and production-scale throughput are outside P1.

## Performance and reliability

- The supplied warm-path demo case should return a useful result in approximately five seconds or less after the server receives a validated upload.
- Observed performance shall be measured over at least 10 consecutive warm runs, reporting the median and slowest result.
- Browser upload time and deployment cold starts shall be reported separately when material.
- A provider or processing timeout shall return an actionable response within 15 seconds.
- Unsupported, oversized, corrupt, or empty files shall produce specific corrective messages.
- Provider failures and malformed extraction responses shall produce a retry message without exposing stack traces, secrets, or provider payloads.

## Usability and accessibility

- The single-review flow shall remain linear: **Enter expected values → Upload label → Review results**.
- The overall outcome shall appear before the individual checks.
- Status shall never be communicated by color alone.
- Controls shall have visible labels, keyboard access, visible focus, sufficient contrast, and useful error messages.
- A standard file picker shall always be available; drag-and-drop shall not be required.
- Uncertainty shall be explained in plain language rather than hidden behind a confidence score.
- The project shall not claim formal WCAG conformance without a complete audit.

## Deployment and network access

The submitted application URL shall provide a working prototype over HTTPS in a current desktop browser. An evaluator shall be able to complete P0 without cloning the repository, installing software, using a VPN, or requesting applicant-controlled credentials.

To reduce exposure to restricted outbound networks:

- required frontend assets shall load from the application origin;
- browser uploads and application API requests shall use that origin;
- the browser shall not call AI/OCR providers or external object storage directly; and
- public CDNs, third-party fonts, analytics, telemetry, and external authentication shall not be required for P0.

External AI/OCR or storage services may be called by the deployed backend. The implementation documentation shall identify the browser-visible origin, backend external dependencies, and the fact that Treasury firewall allowlisting has not been verified.

The application shall remain available during Treasury's evaluation period and shall display useful errors for cold starts, unavailable dependencies, and configuration failures.

## Security, privacy, and retention

- The interface shall require synthetic or otherwise non-sensitive test data.
- File type and content shall be validated server-side.
- File size, image dimensions, request rate, and processing time shall be bounded.
- Credentials shall remain in deployment secrets and shall never be delivered in client code.
- Image contents, complete extracted text, secrets, and provider payloads shall not be logged.
- Uploaded images and derived artifacts shall be deleted after the result when practical; otherwise the documented automatic deletion window shall not exceed 24 hours.
- If an external provider is used, the implementation documentation shall state what is transmitted and describe the relevant retention setting.

## Demo acceptance scenarios

The repository shall provide synthetic fixtures for:

1. a clear distilled-spirits label for which all five checks match;
2. a case-only, whitespace-only, or apostrophe-style brand variation;
3. a material brand or class/type difference;
4. ABV expressed as proof and a conflicting ABV/proof case;
5. equivalent and different net-content quantities;
6. a missing or altered Government Warning;
7. a low-quality or partially unreadable image; and
8. an unsupported or corrupt file.

For the primary demonstration, an evaluator can enter:

- Brand: `OLD TOM`
- Class/type: `Kentucky Straight Bourbon Whiskey`
- ABV: `45`
- Net contents: `750 mL`

After uploading the supplied matching fixture, the evaluator shall receive an overall outcome, five individual results, expected and extracted values, explanations, and processing duration. The evaluator shall also be able to try at least one mismatch and one unreadable-image fixture.

## Assumptions and limitations

- The primary demo covers the distilled-spirits example fields rather than separate rule engines for wine, malt beverages, and distilled spirits.
- Expected values are entered manually because the prototype does not integrate with COLAs Online.
- One image or composite represents the relevant artwork for a case.
- Class/type is treated as expected application metadata for the demo.
- P1 is capped at 25 cases to demonstrate interaction and failure handling, not production throughput.
- Producer or bottler address, country of origin, permit data, formula data, vintage, varietal, and appellation are not checked.
- Severe blur, glare, distortion, occlusion, and perspective problems may remain unreadable.
- Authentication, role-based access, audit history, long-term storage, official workflow states, and reviewer overrides are not implemented.
- The prototype is not certified for FedRAMP, Treasury allowlisting, government records management, PII processing, or use in a restricted production environment.
- The deployed application may use an external AI/OCR provider through its backend; provider choice, transmitted data, and retention behavior shall be documented with the implementation.
