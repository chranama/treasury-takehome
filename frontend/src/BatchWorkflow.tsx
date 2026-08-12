import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from 'react'

import {
  BatchRequestError,
  correctBatchCase,
  loadBatch,
  loadBatchCase,
  preflightBatch,
  replaceBatchCaseImage,
  startBatch,
} from './batchApi'
import type {
  BatchCaseDetail,
  BatchCaseSummary,
  BatchPatch,
  BatchPreflightResponse,
  BatchResponse,
  PreflightIssue,
} from './batchTypes'

const MAX_SPREADSHEET_BYTES = 1024 * 1024
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_AGGREGATE_BYTES = 100 * 1024 * 1024
const MAX_IMAGES = 25
const SUPPORTED_SPREADSHEET_SUFFIXES = ['.csv', '.xlsx']

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function batchIdFromUrl(): string | null {
  return new URL(window.location.href).searchParams.get('batch')
}

function writeBatchId(batchId: string | null) {
  const url = new URL(window.location.href)
  if (batchId) url.searchParams.set('batch', batchId)
  else url.searchParams.delete('batch')
  window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
}

function issueKey(issue: PreflightIssue, index: number): string {
  return `${issue.code}-${issue.field ?? 'none'}-${issue.row_number ?? 'none'}-${index}`
}

function IssueList({ issues }: { issues: PreflightIssue[] }) {
  if (issues.length === 0) return null
  return (
    <ul className="batch-issue-list">
      {issues.map((issue, index) => (
        <li key={issueKey(issue, index)} className={`issue-${issue.severity}`}>
          <span aria-hidden="true">{issue.severity === 'warning' ? '!' : '×'}</span>
          {issue.message}
        </li>
      ))}
    </ul>
  )
}

function BatchUploadForm({ onCreated }: { onCreated: (draft: BatchPreflightResponse) => void }) {
  const [spreadsheet, setSpreadsheet] = useState<File | null>(null)
  const [images, setImages] = useState<File[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [serverIssues, setServerIssues] = useState<PreflightIssue[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const spreadsheetRef = useRef<HTMLInputElement>(null)
  const imagesRef = useRef<HTMLInputElement>(null)

  function validate(): string[] {
    const next: string[] = []
    if (!spreadsheet) next.push('Choose one XLSX workbook or UTF-8 CSV file.')
    else {
      const lowerName = spreadsheet.name.toLowerCase()
      if (!SUPPORTED_SPREADSHEET_SUFFIXES.some((suffix) => lowerName.endsWith(suffix))) {
        next.push('Choose a file ending in .xlsx or .csv.')
      }
      if (spreadsheet.size > MAX_SPREADSHEET_BYTES) {
        next.push('Choose a spreadsheet no larger than 1 MB.')
      }
    }
    if (images.length > MAX_IMAGES) next.push('Select no more than 25 label images.')
    if (images.some((image) => image.size > MAX_IMAGE_BYTES)) {
      next.push('Each selected image must be no larger than 10 MB.')
    }
    const aggregateBytes = (spreadsheet?.size ?? 0) + images.reduce((sum, file) => sum + file.size, 0)
    if (aggregateBytes > MAX_AGGREGATE_BYTES) {
      next.push('The spreadsheet and selected images together must not exceed 100 MB.')
    }
    return next
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (isSubmitting) return
    const nextErrors = validate()
    setErrors(nextErrors)
    setServerIssues([])
    if (nextErrors.length > 0 || !spreadsheet) {
      if (!spreadsheet || nextErrors.some((message) => /spreadsheet|\.xlsx|\.csv/i.test(message))) {
        spreadsheetRef.current?.focus()
      } else {
        imagesRef.current?.focus()
      }
      return
    }

    setIsSubmitting(true)
    try {
      onCreated(await preflightBatch(spreadsheet, images))
    } catch (caught) {
      if (caught instanceof BatchRequestError) {
        setServerIssues(caught.issues)
        setErrors(caught.issues.length > 0 ? [] : [caught.message])
      } else {
        setErrors(['The batch could not be checked. Try again.'])
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const selectedBytes =
    (spreadsheet?.size ?? 0) + images.reduce((sum, image) => sum + image.size, 0)

  return (
    <section className="panel batch-upload-panel" aria-labelledby="batch-upload-title">
      <div className="section-heading">
        <p className="step-label">Batch step 1</p>
        <h2 id="batch-upload-title">Prepare a batch package</h2>
        <p>Use one spreadsheet and select the label images separately. Do not create a ZIP file.</p>
      </div>

      <div className="template-links" aria-label="Batch template downloads">
        <a className="secondary-button" href="/api/batch-template.xlsx" download>
          Download XLSX template
        </a>
        <a className="secondary-button" href="/api/batch-template.csv" download>
          Download CSV template
        </a>
      </div>

      <div className="batch-limits" role="note" aria-label="Batch upload limits">
        <strong>Accepted package</strong>
        <ul>
          <li>Up to 25 application rows and 25 images</li>
          <li>Spreadsheet up to 1 MB; each image up to 10 MB</li>
          <li>Spreadsheet and images together up to 100 MB</li>
          <li>JPEG, PNG, or WebP images using the spreadsheet filenames</li>
        </ul>
      </div>

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting}>
        <div className="field-group">
          <label htmlFor="batch-spreadsheet">Spreadsheet</label>
          <input
            id="batch-spreadsheet"
            ref={spreadsheetRef}
            type="file"
            accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
            aria-describedby="batch-spreadsheet-hint"
            onChange={(event) => {
              setSpreadsheet(event.target.files?.[0] ?? null)
              setErrors([])
              setServerIssues([])
            }}
          />
          <p className="field-hint" id="batch-spreadsheet-hint">
            {spreadsheet
              ? `${spreadsheet.name} · ${formatBytes(spreadsheet.size)}`
              : 'Choose one completed template.'}
          </p>
        </div>

        <div className="field-group">
          <label htmlFor="batch-images">Label images</label>
          <input
            id="batch-images"
            ref={imagesRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp"
            aria-describedby="batch-images-hint"
            onChange={(event) => {
              setImages(Array.from(event.target.files ?? []))
              setErrors([])
              setServerIssues([])
            }}
          />
          <p className="field-hint" id="batch-images-hint">
            {images.length === 0
              ? 'No images selected.'
              : `${images.length} image${images.length === 1 ? '' : 's'} selected.`}
          </p>
        </div>

        <p className="selection-total">
          Selected package: <strong>{formatBytes(selectedBytes)}</strong>
        </p>

        {(errors.length > 0 || serverIssues.length > 0) && (
          <div className="batch-error-summary" role="alert" tabIndex={-1}>
            <strong>Correct the package and try again.</strong>
            {errors.length > 0 && (
              <ul>
                {errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
            <IssueList issues={serverIssues} />
          </div>
        )}

        <div className="privacy-note">
          <span className="privacy-icon" aria-hidden="true">i</span>
          <p>
            <strong>Use synthetic or non-sensitive data.</strong> Draft content is recoverable for
            up to 24 hours, then its records and images are deleted.
          </p>
        </div>

        <button className="primary-button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? (
            <><span className="spinner" aria-hidden="true" /> Checking batch…</>
          ) : (
            'Check batch'
          )}
        </button>
      </form>
    </section>
  )
}

interface ExpectedEditorProps {
  detail: BatchCaseDetail
  isSaving: boolean
  onCancel: () => void
  onSave: (patch: BatchPatch) => Promise<void>
}

function ExpectedEditor({ detail, isSaving, onCancel, onSave }: ExpectedEditorProps) {
  const [brandName, setBrandName] = useState(detail.expected_input.brand_name)
  const [classType, setClassType] = useState(detail.expected_input.class_type)
  const [abv, setAbv] = useState(detail.expected_input.expected_abv)
  const [netContents, setNetContents] = useState(detail.expected_input.expected_net_contents)
  const row = detail.summary.row_number

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void onSave({
      brand_name: brandName,
      class_type: classType,
      expected_abv: abv,
      expected_net_contents: netContents,
    })
  }

  return (
    <form className="case-editor" onSubmit={submit} aria-label={`Expected values for row ${row}`}>
      <div className="field-row">
        <div className="field-group">
          <label htmlFor={`brand-${detail.summary.case_id}`}>Expected brand</label>
          <input id={`brand-${detail.summary.case_id}`} maxLength={200} value={brandName} onChange={(event) => setBrandName(event.target.value)} />
        </div>
        <div className="field-group">
          <label htmlFor={`class-${detail.summary.case_id}`}>Expected class or type</label>
          <input id={`class-${detail.summary.case_id}`} maxLength={200} value={classType} onChange={(event) => setClassType(event.target.value)} />
        </div>
      </div>
      <div className="field-row">
        <div className="field-group">
          <label htmlFor={`abv-${detail.summary.case_id}`}>Expected ABV</label>
          <input id={`abv-${detail.summary.case_id}`} maxLength={32} value={abv} onChange={(event) => setAbv(event.target.value)} />
        </div>
        <div className="field-group">
          <label htmlFor={`net-${detail.summary.case_id}`}>Expected net contents</label>
          <input id={`net-${detail.summary.case_id}`} maxLength={64} value={netContents} onChange={(event) => setNetContents(event.target.value)} />
        </div>
      </div>
      <div className="case-editor-actions">
        <button className="primary-button compact-button" type="submit" disabled={isSaving}>
          {isSaving ? 'Saving…' : 'Save expected values'}
        </button>
        <button className="secondary-button" type="button" onClick={onCancel} disabled={isSaving}>
          Cancel
        </button>
      </div>
    </form>
  )
}

interface CaseCardProps {
  summary: BatchCaseSummary
  detail: BatchCaseDetail | undefined
  isEditing: boolean
  isBusy: boolean
  error: string | undefined
  onEdit: () => Promise<void>
  onCancel: () => void
  onSave: (patch: BatchPatch) => Promise<void>
  onReplace: (image: File) => Promise<void>
}

function CaseCard({
  summary,
  detail,
  isEditing,
  isBusy,
  error,
  onEdit,
  onCancel,
  onSave,
  onReplace,
}: CaseCardProps) {
  const rowLabel = `Spreadsheet row ${summary.row_number}`
  const replaceId = `replacement-${summary.case_id}`
  return (
    <article className={`batch-case case-${summary.state}`} aria-labelledby={`case-${summary.case_id}`}>
      <header className="batch-case-header">
        <div>
          <p className="case-row">{rowLabel}</p>
          <h3 id={`case-${summary.case_id}`}>{summary.application_id || 'Missing application ID'}</h3>
          <p className="case-filename">Image: {summary.label_image_filename || 'Not specified'}</p>
        </div>
        <span className={`case-state state-${summary.state}`}>
          <span aria-hidden="true">{summary.state === 'ready' ? '✓' : '!'}</span>
          {summary.state === 'ready' ? 'Ready' : 'Needs correction'}
        </span>
      </header>

      <IssueList issues={summary.issues} />
      {error && <p className="case-action-error" role="alert">{error}</p>}

      {isEditing && detail ? (
        <ExpectedEditor detail={detail} isSaving={isBusy} onCancel={onCancel} onSave={onSave} />
      ) : (
        <div className="case-actions">
          <button className="secondary-button" type="button" onClick={() => void onEdit()} disabled={isBusy}>
            Edit expected values
          </button>
          <label className="secondary-button" htmlFor={replaceId}>Replace image</label>
          <input
            className="visually-hidden-file"
            id={replaceId}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label={`Replace image for row ${summary.row_number}`}
            disabled={isBusy}
            onChange={(event) => {
              const image = event.target.files?.[0]
              if (image) void onReplace(image)
              event.target.value = ''
            }}
          />
        </div>
      )}
    </article>
  )
}

function StartConfirmation({
  draft,
  confirmRef,
  isStarting,
  onCancel,
  onConfirm,
}: {
  draft: BatchResponse
  confirmRef: RefObject<HTMLButtonElement | null>
  isStarting: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  return (
    <div className="dialog-backdrop">
      <section
        className="confirmation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-title"
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            onCancel()
            return
          }
          if (event.key !== 'Tab') return
          const first = confirmRef.current
          const last = cancelRef.current
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault()
            last?.focus()
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first?.focus()
          }
        }}
      >
        <p className="step-label">Confirm selection</p>
        <h2 id="confirmation-title">Process {draft.counts.ready} ready case{draft.counts.ready === 1 ? '' : 's'}?</h2>
        {draft.counts.needs_correction > 0 ? (
          <p>{draft.counts.needs_correction} case{draft.counts.needs_correction === 1 ? '' : 's'} still need correction and will not be selected.</p>
        ) : (
          <p>Every case is ready and will be selected.</p>
        )}
        <div className="dialog-actions">
          <button ref={confirmRef} className="primary-button compact-button" type="button" disabled={isStarting} onClick={onConfirm}>{isStarting ? 'Starting batch…' : 'Confirm ready cases'}</button>
          <button ref={cancelRef} className="secondary-button" type="button" onClick={onCancel}>Cancel</button>
        </div>
      </section>
    </div>
  )
}

function BatchDraftView({ draft, onBatchChange, onNewBatch }: {
  draft: BatchResponse
  onBatchChange: (batch: BatchResponse) => void
  onNewBatch: () => void
}) {
  const [details, setDetails] = useState<Record<string, BatchCaseDetail>>({})
  const [editingCaseId, setEditingCaseId] = useState<string | null>(null)
  const [busyCaseId, setBusyCaseId] = useState<string | null>(null)
  const [caseErrors, setCaseErrors] = useState<Record<string, string>>({})
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [selectionNotice, setSelectionNotice] = useState('')
  const [isStarting, setIsStarting] = useState(false)
  const startButtonRef = useRef<HTMLButtonElement>(null)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)
  const startKeyRef = useRef(`batch-start:${crypto.randomUUID()}`)

  useEffect(() => {
    if (showConfirmation) confirmButtonRef.current?.focus()
  }, [showConfirmation])

  async function refreshDraft() {
    onBatchChange(await loadBatch(draft.batch_id))
  }

  async function editCase(summary: BatchCaseSummary) {
    setBusyCaseId(summary.case_id)
    setCaseErrors((current) => ({ ...current, [summary.case_id]: '' }))
    try {
      const detail = await loadBatchCase(draft.batch_id, summary.case_id)
      setDetails((current) => ({ ...current, [summary.case_id]: detail }))
      setEditingCaseId(summary.case_id)
    } catch (caught) {
      setCaseErrors((current) => ({
        ...current,
        [summary.case_id]: caught instanceof BatchRequestError ? caught.message : 'The case could not be loaded.',
      }))
    } finally {
      setBusyCaseId(null)
    }
  }

  async function saveCase(summary: BatchCaseSummary, patch: BatchPatch) {
    setBusyCaseId(summary.case_id)
    try {
      const detail = await correctBatchCase(draft.batch_id, summary.case_id, patch)
      setDetails((current) => ({ ...current, [summary.case_id]: detail }))
      setEditingCaseId(null)
      await refreshDraft()
    } catch (caught) {
      setCaseErrors((current) => ({
        ...current,
        [summary.case_id]: caught instanceof BatchRequestError ? caught.message : 'The correction could not be saved.',
      }))
    } finally {
      setBusyCaseId(null)
    }
  }

  async function replaceImage(summary: BatchCaseSummary, image: File) {
    setBusyCaseId(summary.case_id)
    setCaseErrors((current) => ({ ...current, [summary.case_id]: '' }))
    try {
      const detail = await replaceBatchCaseImage(draft.batch_id, summary.case_id, image)
      setDetails((current) => ({ ...current, [summary.case_id]: detail }))
      await refreshDraft()
    } catch (caught) {
      setCaseErrors((current) => ({
        ...current,
        [summary.case_id]: caught instanceof BatchRequestError ? caught.message : 'The image could not be replaced.',
      }))
    } finally {
      setBusyCaseId(null)
    }
  }

  function closeConfirmation() {
    setShowConfirmation(false)
    window.setTimeout(() => startButtonRef.current?.focus(), 0)
  }

  async function confirmStart() {
    setIsStarting(true)
    setSelectionNotice('')
    try {
      const selection = draft.counts.needs_correction > 0 ? 'ready_cases_only' : 'all_cases'
      const started = await startBatch(draft.batch_id, selection, startKeyRef.current)
      setShowConfirmation(false)
      onBatchChange(started)
    } catch (caught) {
      setSelectionNotice(
        caught instanceof BatchRequestError
          ? caught.message
          : 'The batch could not be started. Try again.',
      )
    } finally {
      setIsStarting(false)
    }
  }

  return (
    <section className="batch-draft" aria-labelledby="batch-draft-title">
      <div className="batch-draft-heading">
        <div>
          <p className="step-label">Batch step 2</p>
          <h2 id="batch-draft-title">Review preflight results</h2>
          <p>Correct only the rows that need attention. This draft remains available until {new Date(draft.expires_at).toLocaleString()}.</p>
        </div>
        <button className="secondary-button" type="button" onClick={onNewBatch}>Start a new batch</button>
      </div>

      <div className="batch-counts" aria-label="Batch readiness summary">
        <div><strong>{draft.counts.total}</strong><span>Total cases</span></div>
        <div className="count-ready"><strong>{draft.counts.ready}</strong><span>Ready</span></div>
        <div className="count-correction"><strong>{draft.counts.needs_correction}</strong><span>Need correction</span></div>
      </div>

      <div className="batch-case-list">
        {draft.cases.map((summary) => (
          <CaseCard
            key={summary.case_id}
            summary={summary}
            detail={details[summary.case_id]}
            isEditing={editingCaseId === summary.case_id}
            isBusy={busyCaseId === summary.case_id}
            error={caseErrors[summary.case_id]}
            onEdit={() => editCase(summary)}
            onCancel={() => setEditingCaseId(null)}
            onSave={(patch) => saveCase(summary, patch)}
            onReplace={(image) => replaceImage(summary, image)}
          />
        ))}
      </div>

      <div className="batch-start-panel">
        <div>
          <h3>Ready to continue?</h3>
          <p>Only cases marked Ready can be selected for processing.</p>
          <p>Cases finish independently. A started batch may complete partially if provider or global service capacity becomes unavailable; completed cases remain valid.</p>
        </div>
        <button
          ref={startButtonRef}
          className="primary-button compact-button"
          type="button"
          disabled={draft.counts.ready === 0}
          onClick={() => {
            setSelectionNotice('')
            setShowConfirmation(true)
          }}
        >
          Process all ready cases
        </button>
      </div>

      {selectionNotice && <p className="selection-notice" role="status">{selectionNotice}</p>}
      {showConfirmation && (
        <StartConfirmation
          draft={draft}
          confirmRef={confirmButtonRef}
          isStarting={isStarting}
          onCancel={closeConfirmation}
          onConfirm={() => void confirmStart()}
        />
      )}
    </section>
  )
}

function BatchProcessingAccepted({ batch, onNewBatch }: {
  batch: BatchResponse
  onNewBatch: () => void
}) {
  const terminal = batch.state === 'completed' || batch.state === 'interrupted'
  return (
    <section className="empty-results processing-results" role="status">
      {!terminal && <span className="large-spinner" aria-hidden="true" />}
      <p className="step-label">Batch step 3</p>
      <h2>{terminal ? 'Batch processing finished' : 'Batch processing started'}</h2>
      <p>
        {batch.counts.completed} completed, {batch.counts.failed} failed, and{' '}
        {batch.counts.queued + batch.counts.processing} still active.
      </p>
      <p>Detailed polling and result review are added in the next milestone. Refreshing this page safely recovers the current durable state.</p>
      <button className="secondary-button" type="button" onClick={onNewBatch}>Start a new batch</button>
    </section>
  )
}

export function BatchWorkflow() {
  const [draft, setDraft] = useState<BatchResponse | null>(null)
  const [isRecovering, setIsRecovering] = useState(Boolean(batchIdFromUrl()))
  const [recoveryError, setRecoveryError] = useState('')

  useEffect(() => {
    const batchId = batchIdFromUrl()
    if (!batchId) return
    let active = true
    void loadBatch(batchId)
      .then((recovered) => {
        if (active) setDraft(recovered)
      })
      .catch((caught) => {
        if (!active) return
        writeBatchId(null)
        setRecoveryError(
          caught instanceof BatchRequestError && caught.notFound
            ? 'This batch is unavailable or has expired. Upload the package again.'
            : caught instanceof BatchRequestError
              ? caught.message
              : 'The saved batch could not be recovered.',
        )
      })
      .finally(() => {
        if (active) setIsRecovering(false)
      })
    return () => {
      active = false
    }
  }, [])

  if (isRecovering) {
    return <section className="empty-results processing-results" role="status"><span className="large-spinner" aria-hidden="true" /><h2>Recovering batch draft</h2><p>Loading the saved preflight results.</p></section>
  }

  if (draft?.state === 'draft') {
    return (
      <BatchDraftView
        draft={draft}
        onBatchChange={setDraft}
        onNewBatch={() => {
          writeBatchId(null)
          setDraft(null)
          setRecoveryError('')
        }}
      />
    )
  }

  if (draft) {
    return (
      <BatchProcessingAccepted
        batch={draft}
        onNewBatch={() => {
          writeBatchId(null)
          setDraft(null)
          setRecoveryError('')
        }}
      />
    )
  }

  return (
    <div className="batch-workflow">
      {recoveryError && <div className="batch-error-summary" role="alert">{recoveryError}</div>}
      <BatchUploadForm
        onCreated={(created) => {
          writeBatchId(created.batch_id)
          setDraft(created)
          setRecoveryError('')
        }}
      />
    </div>
  )
}
