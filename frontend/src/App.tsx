import { useState } from 'react'

import { BatchWorkflow } from './BatchWorkflow'
import { DemoExamples } from './DemoExamples'
import { submitReview, ReviewRequestError } from './reviewApi'
import { ReviewForm } from './ReviewForm'
import {
  EmptyResults,
  ProcessingResults,
  ReviewFailure,
  ReviewResults,
} from './ReviewResults'
import type { ApiErrorResponse, ReviewResponse, ReviewSubmission } from './reviewTypes'

function App() {
  const batchPage = window.location.pathname === '/batch' || window.location.pathname === '/batch/'
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [result, setResult] = useState<ReviewResponse | null>(null)
  const [error, setError] = useState<ApiErrorResponse | null>(null)

  async function handleSubmit(submission: ReviewSubmission) {
    if (isSubmitting) return
    setIsSubmitting(true)
    setResult(null)
    setError(null)

    try {
      setResult(await submitReview(submission))
    } catch (caught) {
      if (caught instanceof ReviewRequestError) setError(caught.details)
      else {
        setError({
          category: 'internal_error',
          message: 'The review could not be completed. Try again.',
          correlation_id: '',
          processing_duration_ms: 0,
        })
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <header className="site-header">
        <div className="header-inner">
          <a className="skip-link" href="#main-content">
            Skip to main content
          </a>
          <div className="wordmark">
            <span aria-hidden="true">LR</span>
            <span>Label Review</span>
          </div>
          <span className="prototype-tag">Prototype</span>
        </div>
      </header>

      <main className="app-shell" id="main-content">
        <section className="page-intro" aria-labelledby="page-title">
          <p className="eyebrow">Reviewer-assist workflow</p>
          <h1 id="page-title">Alcohol Label Verification</h1>
          <p className="summary">
            Compare expected application values with visible label artwork. Uncertain findings and
            regulatory decisions always remain with a human reviewer.
          </p>
        </section>

        <nav className="workflow-switcher" aria-label="Review workflow">
          <a
            className={!batchPage ? 'active' : ''}
            href="/"
            aria-current={!batchPage ? 'page' : undefined}
          >
            Single label
          </a>
          <a
            className={batchPage ? 'active' : ''}
            href="/batch"
            aria-current={batchPage ? 'page' : undefined}
          >
            Batch review
          </a>
        </nav>

        <DemoExamples batch={batchPage} />

        {batchPage ? (
          <BatchWorkflow />
        ) : (
          <div className="workflow-layout">
            <ReviewForm isSubmitting={isSubmitting} onSubmit={handleSubmit} />
            <div className="results-column">
              {isSubmitting ? (
                <ProcessingResults />
              ) : error ? (
                <ReviewFailure error={error} />
              ) : result ? (
                <ReviewResults result={result} />
              ) : (
                <EmptyResults />
              )}
            </div>
          </div>
        )}
      </main>

      <footer>
        <p>Demonstration only · Not a TTB production system</p>
      </footer>
    </>
  )
}

export default App
