import type {
  BatchCaseDetail,
  BatchCaseSummary,
  BatchPatch,
  BatchPreflightResponse,
  BatchResponse,
  BatchStateCounts,
  PreflightIssue,
} from './batchTypes'
import { isReviewResult } from './reviewApi'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isIssue(value: unknown): value is PreflightIssue {
  if (!isRecord(value)) return false
  return (
    typeof value.code === 'string' &&
    (value.scope === 'batch' || value.scope === 'row' || value.scope === 'image') &&
    (typeof value.row_number === 'number' || value.row_number === null) &&
    (typeof value.field === 'string' || value.field === null) &&
    (value.severity === 'error' || value.severity === 'warning') &&
    typeof value.message === 'string'
  )
}

function isCaseSummary(value: unknown): value is BatchCaseSummary {
  if (!isRecord(value)) return false
  return (
    typeof value.case_id === 'string' &&
    typeof value.row_number === 'number' &&
    typeof value.application_id === 'string' &&
    typeof value.label_image_filename === 'string' &&
    [
      'needs_correction',
      'ready',
      'queued',
      'processing',
      'completed',
      'failed',
      'interrupted',
      'not_selected',
    ].includes(String(value.state)) &&
    Array.isArray(value.issues) &&
    value.issues.every(isIssue) &&
    (value.outcome === null ||
      value.outcome === 'all_checks_passed' ||
      value.outcome === 'needs_review' ||
      value.outcome === 'unable_to_process') &&
    (typeof value.processing_duration_ms === 'number' || value.processing_duration_ms === null) &&
    (typeof value.short_reason === 'string' || value.short_reason === null)
  )
}

function isCounts(value: unknown): value is BatchStateCounts {
  if (!isRecord(value)) return false
  return [
    'total',
    'needs_correction',
    'ready',
    'queued',
    'processing',
    'completed',
    'failed',
    'interrupted',
    'not_selected',
  ].every((field) => typeof value[field] === 'number' && value[field] >= 0)
}

function isBatch(value: unknown): value is BatchResponse {
  if (!isRecord(value)) return false
  return (
    typeof value.batch_id === 'string' &&
    ['draft', 'queued', 'processing', 'completed', 'interrupted'].includes(
      String(value.state),
    ) &&
    typeof value.created_at === 'string' &&
    typeof value.expires_at === 'string' &&
    isCounts(value.counts) &&
    Array.isArray(value.cases) &&
    value.cases.every(isCaseSummary)
  )
}

function isCaseDetail(value: unknown): value is BatchCaseDetail {
  if (!isRecord(value) || !isCaseSummary(value.summary) || !isRecord(value.expected_input)) {
    return false
  }
  const expected = value.expected_input
  const validResult =
    value.result === null ||
    (isRecord(value.result) &&
      isReviewResult(value.result.result) &&
      (value.result.processing_mode === 'synthetic' || value.result.processing_mode === 'live') &&
      typeof value.result.correlation_id === 'string' &&
      typeof value.result.completed_at === 'string' &&
      typeof value.result.expires_at === 'string')
  return (
    typeof expected.brand_name === 'string' &&
    typeof expected.class_type === 'string' &&
    typeof expected.expected_abv === 'string' &&
    typeof expected.expected_net_contents === 'string' &&
    validResult
  )
}

export class BatchRequestError extends Error {
  readonly issues: PreflightIssue[]
  readonly notFound: boolean
  readonly correlationId: string
  readonly temporary: boolean

  constructor(
    message: string,
    options: { issues?: PreflightIssue[]; notFound?: boolean; correlationId?: string; temporary?: boolean } = {},
  ) {
    super(message)
    this.name = 'BatchRequestError'
    this.issues = options.issues ?? []
    this.notFound = options.notFound ?? false
    this.correlationId = options.correlationId ?? ''
    this.temporary = options.temporary ?? false
  }
}

async function requestJson(
  path: string,
  init: RequestInit | undefined,
  validate: (value: unknown) => boolean,
): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new BatchRequestError(
      'The batch review service could not be reached. Check your connection and try again.',
      { temporary: true },
    )
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new BatchRequestError('The batch review service returned an unreadable response.', {
      correlationId: response.headers.get('X-Correlation-ID') ?? '',
      temporary: response.status >= 500,
    })
  }

  if (!response.ok) {
    if (isRecord(payload)) {
      const issues = Array.isArray(payload.issues) ? payload.issues.filter(isIssue) : []
      const message =
        issues[0]?.message ??
        (typeof payload.message === 'string'
          ? payload.message
          : 'The batch request could not be completed.')
      throw new BatchRequestError(message, {
        issues,
        notFound: payload.code === 'batch_not_found',
        correlationId:
          typeof payload.correlation_id === 'string' ? payload.correlation_id : '',
        temporary: response.status >= 500,
      })
    }
    throw new BatchRequestError('The batch request returned an unexpected error.', {
      temporary: response.status >= 500,
    })
  }

  if (!validate(payload)) {
    throw new BatchRequestError('The batch review service returned an unexpected result.', {
      correlationId: response.headers.get('X-Correlation-ID') ?? '',
    })
  }
  return payload
}

export async function preflightBatch(
  spreadsheet: File,
  images: File[],
): Promise<BatchPreflightResponse> {
  const body = new FormData()
  body.set('spreadsheet', spreadsheet)
  images.forEach((image) => body.append('images', image))
  return (await requestJson('/api/batches/preflight', { method: 'POST', body }, isBatch)) as BatchPreflightResponse
}

export async function loadBatch(batchId: string): Promise<BatchResponse> {
  return (await requestJson(`/api/batches/${batchId}`, undefined, isBatch)) as BatchResponse
}

export async function startBatch(
  batchId: string,
  selection: 'all_cases' | 'ready_cases_only',
  idempotencyKey: string,
): Promise<BatchResponse> {
  return (await requestJson(
    `/api/batches/${batchId}/start`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({ selection }),
    },
    isBatch,
  )) as BatchResponse
}

export async function loadBatchCase(
  batchId: string,
  caseId: string,
): Promise<BatchCaseDetail> {
  return (await requestJson(
    `/api/batches/${batchId}/cases/${caseId}`,
    undefined,
    isCaseDetail,
  )) as BatchCaseDetail
}

export async function correctBatchCase(
  batchId: string,
  caseId: string,
  patch: BatchPatch,
): Promise<BatchCaseDetail> {
  return (await requestJson(
    `/api/batches/${batchId}/cases/${caseId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
    isCaseDetail,
  )) as BatchCaseDetail
}

export async function replaceBatchCaseImage(
  batchId: string,
  caseId: string,
  image: File,
): Promise<BatchCaseDetail> {
  const body = new FormData()
  body.set('image', image)
  return (await requestJson(
    `/api/batches/${batchId}/cases/${caseId}/image`,
    { method: 'PUT', body },
    isCaseDetail,
  )) as BatchCaseDetail
}
