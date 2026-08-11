import type {
  ApiErrorResponse,
  CheckName,
  CheckResult,
  CheckStatus,
  ErrorCategory,
  ReviewOutcome,
  ReviewResponse,
} from './reviewTypes'

const checkLabels: Record<CheckName, string> = {
  brand_name: 'Brand name',
  class_type: 'Class or type',
  alcohol_content: 'Alcohol content',
  net_contents: 'Net contents',
  government_warning: 'Government Warning',
}

const statusLabels: Record<CheckStatus, string> = {
  match: 'Match',
  mismatch: 'Mismatch',
  needs_review: 'Needs review',
  not_evaluated: 'Not evaluated',
}

const statusSymbols: Record<CheckStatus, string> = {
  match: '✓',
  mismatch: '!',
  needs_review: '?',
  not_evaluated: '—',
}

const outcomeLabels: Record<ReviewOutcome, string> = {
  all_checks_passed: 'All checks passed',
  needs_review: 'Needs review',
  unable_to_process: 'Unable to process',
}

const outcomeSymbols: Record<ReviewOutcome, string> = {
  all_checks_passed: '✓',
  needs_review: '!',
  unable_to_process: '×',
}

const errorTitles: Record<ErrorCategory, string> = {
  invalid_input: 'Check the submitted information',
  live_extraction_disabled: 'Live extraction is unavailable',
  capacity_reached: 'Review capacity is temporarily full',
  traffic_throttled: 'Please wait before trying again',
  provider_timeout: 'The extraction service timed out',
  provider_unavailable: 'The extraction service is unavailable',
  malformed_provider_output: 'The extraction service returned an invalid result',
  internal_error: 'The application encountered an error',
}

function Values({ values, emptyLabel }: { values: string[]; emptyLabel: string }) {
  if (values.length === 0) return <span className="empty-value">{emptyLabel}</span>
  if (values.length === 1) return <>{values[0]}</>

  return (
    <ul className="value-list">
      {values.map((value, index) => (
        <li key={`${value}-${index}`}>{value}</li>
      ))}
    </ul>
  )
}

function CheckCard({ check }: { check: CheckResult }) {
  const showNormalized =
    check.normalized_expected !== null || check.normalized_extracted.length > 0

  return (
    <article className={`check-card status-${check.status}`}>
      <header className="check-header">
        <h3>{checkLabels[check.name]}</h3>
        <span className="check-status">
          <span className="status-symbol" aria-hidden="true">
            {statusSymbols[check.status]}
          </span>
          {statusLabels[check.status]}
        </span>
      </header>

      <dl className="value-comparison">
        <div>
          <dt>Expected</dt>
          <dd>{check.expected_value ?? <span className="empty-value">Not applicable</span>}</dd>
        </div>
        <div>
          <dt>Found in image</dt>
          <dd>
            <Values values={check.extracted_values} emptyLabel="Not found" />
          </dd>
        </div>
      </dl>

      {showNormalized && (
        <dl className="normalized-values">
          <div>
            <dt>Normalized expected</dt>
            <dd>{check.normalized_expected ?? '—'}</dd>
          </div>
          <div>
            <dt>Normalized image value</dt>
            <dd>
              <Values values={check.normalized_extracted} emptyLabel="—" />
            </dd>
          </div>
        </dl>
      )}

      <p className="check-reason">{check.reason}</p>
      {check.limitations.length > 0 && (
        <details className="limitations">
          <summary>Image-only limitations</summary>
          <ul>
            {check.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </details>
      )}
    </article>
  )
}

function needsClearerImageGuidance(checks: CheckResult[]): boolean {
  return checks.some(
    (check) =>
      check.status === 'needs_review' &&
      (check.extracted_values.length === 0 ||
        /unreadable|unclear|read|visible|image|determine/i.test(check.reason)),
  )
}

export function ReviewResults({ result }: { result: ReviewResponse }) {
  return (
    <section
      className="results-panel"
      aria-labelledby="result-title"
      aria-live="polite"
      role="status"
    >
      <div className={`result-summary outcome-${result.outcome}`}>
        <p className="step-label">Review result</p>
        <div className="outcome-line">
          <span className="outcome-symbol" aria-hidden="true">
            {outcomeSymbols[result.outcome]}
          </span>
          <h2 id="result-title">{outcomeLabels[result.outcome]}</h2>
        </div>
        <p>
          This is a reviewer-assist finding, not a regulatory approval or rejection.
        </p>
        <div className="result-metadata">
          <span>{(result.processing_duration_ms / 1000).toFixed(2)} seconds</span>
          <span>Reference: {result.correlation_id}</span>
        </div>
      </div>

      {result.processing_mode === 'synthetic' && (
        <div className="synthetic-notice" role="note">
          <span aria-hidden="true">◇</span>
          <p>
            <strong>Synthetic extraction mode.</strong> These findings demonstrate the workflow
            with fixed extraction output, not a live vision model.
          </p>
        </div>
      )}

      {needsClearerImageGuidance(result.checks) && (
        <div className="guidance-note">
          <strong>A clearer image may resolve this review.</strong>
          <p>
            Retake the image straight-on, avoid glare and shadows, and make every label panel large
            enough to read before trying again.
          </p>
        </div>
      )}

      <div className="check-list" role="group" aria-label="Review checks">
        {result.checks.map((check) => (
          <CheckCard key={check.name} check={check} />
        ))}
      </div>
    </section>
  )
}

export function ReviewFailure({ error }: { error: ApiErrorResponse }) {
  return (
    <section className="results-panel" aria-labelledby="failure-title" role="alert">
      <div className="result-summary outcome-unable_to_process">
        <p className="step-label">Review result</p>
        <div className="outcome-line">
          <span className="outcome-symbol" aria-hidden="true">
            ×
          </span>
          <h2 id="failure-title">Unable to process</h2>
        </div>
        <h3>{errorTitles[error.category]}</h3>
        <p>{error.message}</p>
        <p>Your entered values and selected image are still available so you can try again.</p>
        <div className="result-metadata">
          <span>{(error.processing_duration_ms / 1000).toFixed(2)} seconds</span>
          {error.correlation_id && <span>Reference: {error.correlation_id}</span>}
        </div>
      </div>
    </section>
  )
}

export function EmptyResults() {
  return (
    <section className="empty-results" aria-labelledby="empty-results-title">
      <div className="empty-results-icon" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <p className="step-label">Step 2</p>
      <h2 id="empty-results-title">Review findings appear here</h2>
      <p>
        The prototype will compare five visible label elements and identify anything that needs a
        human decision.
      </p>
      <ul className="pending-checks">
        {Object.values(checkLabels).map((label) => (
          <li key={label}>
            <span aria-hidden="true">○</span> {label}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function ProcessingResults() {
  return (
    <section className="empty-results processing-results" role="status" aria-live="polite">
      <span className="large-spinner" aria-hidden="true" />
      <p className="step-label">Review in progress</p>
      <h2>Reading and comparing the label</h2>
      <p>Keep this page open. Most reviews should finish in a few seconds.</p>
    </section>
  )
}
