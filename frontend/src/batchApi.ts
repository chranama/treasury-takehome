import type {
  BatchCaseDetail,
  BatchCaseSummary,
  BatchPatch,
  BatchPreflightResponse,
  BatchStateCounts,
  PreflightIssue,
} from './batchTypes'

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
    (value.state === 'ready' || value.state === 'needs_correction') &&
    Array.isArray(value.issues) &&
    value.issues.every(isIssue)
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

function isBatch(value: unknown): value is BatchPreflightResponse {
  if (!isRecord(value)) return false
  return (
    typeof value.batch_id === 'string' &&
    value.state === 'draft' &&
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
  return (
    typeof expected.brand_name === 'string' &&
    typeof expected.class_type === 'string' &&
    typeof expected.expected_abv === 'string' &&
    typeof expected.expected_net_contents === 'string'
  )
}

export class BatchRequestError extends Error {
  readonly issues: PreflightIssue[]
  readonly notFound: boolean
  readonly correlationId: string

  constructor(
    message: string,
    options: { issues?: PreflightIssue[]; notFound?: boolean; correlationId?: string } = {},
  ) {
    super(message)
    this.name = 'BatchRequestError'
    this.issues = options.issues ?? []
    this.notFound = options.notFound ?? false
    this.correlationId = options.correlationId ?? ''
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
    )
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new BatchRequestError('The batch review service returned an unreadable response.', {
      correlationId: response.headers.get('X-Correlation-ID') ?? '',
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
      })
    }
    throw new BatchRequestError('The batch request returned an unexpected error.')
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
  return (await requestJson('/api/batches/preflight', { method: 'POST', body }, isBatch)) as (
    BatchPreflightResponse
  )
}

export async function loadBatch(batchId: string): Promise<BatchPreflightResponse> {
  return (await requestJson(`/api/batches/${batchId}`, undefined, isBatch)) as (
    BatchPreflightResponse
  )
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
