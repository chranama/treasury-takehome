import type {
  ApiErrorResponse,
  CheckName,
  CheckStatus,
  ErrorCategory,
  ReviewOutcome,
  ReviewResult,
  ReviewResponse,
  ReviewSubmission,
} from './reviewTypes'

const reviewOutcomes = new Set<ReviewOutcome>([
  'all_checks_passed',
  'needs_review',
  'unable_to_process',
])
const checkNames = new Set<CheckName>([
  'brand_name',
  'class_type',
  'alcohol_content',
  'net_contents',
  'government_warning',
])
const checkStatuses = new Set<CheckStatus>([
  'match',
  'mismatch',
  'needs_review',
  'not_evaluated',
])
const errorCategories = new Set<ErrorCategory>([
  'invalid_input',
  'live_extraction_disabled',
  'capacity_reached',
  'traffic_throttled',
  'duplicate_submission',
  'provider_timeout',
  'provider_unavailable',
  'malformed_provider_output',
  'internal_error',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

export function isCheckResult(value: unknown): boolean {
  if (!isRecord(value)) return false

  return (
    typeof value.name === 'string' &&
    checkNames.has(value.name as CheckName) &&
    typeof value.status === 'string' &&
    checkStatuses.has(value.status as CheckStatus) &&
    (typeof value.expected_value === 'string' || value.expected_value === null) &&
    isStringArray(value.extracted_values) &&
    (typeof value.normalized_expected === 'string' || value.normalized_expected === null) &&
    isStringArray(value.normalized_extracted) &&
    typeof value.reason === 'string' &&
    isStringArray(value.limitations)
  )
}

export function isReviewResult(value: unknown): value is ReviewResult {
  if (!isRecord(value)) return false

  return (
    typeof value.outcome === 'string' &&
    reviewOutcomes.has(value.outcome as ReviewOutcome) &&
    Array.isArray(value.checks) &&
    value.checks.length === 5 &&
    value.checks.every(isCheckResult) &&
    typeof value.processing_duration_ms === 'number' &&
    value.processing_duration_ms >= 0
  )
}

function isReviewResponse(value: unknown): value is ReviewResponse {
  if (!isRecord(value)) return false

  return (
    isReviewResult(value) &&
    typeof value.correlation_id === 'string' &&
    (value.processing_mode === 'synthetic' || value.processing_mode === 'live')
  )
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!isRecord(value)) return false

  return (
    typeof value.category === 'string' &&
    errorCategories.has(value.category as ErrorCategory) &&
    typeof value.message === 'string' &&
    typeof value.correlation_id === 'string' &&
    typeof value.processing_duration_ms === 'number' &&
    value.processing_duration_ms >= 0
  )
}

export class ReviewRequestError extends Error {
  readonly details: ApiErrorResponse

  constructor(details: ApiErrorResponse) {
    super(details.message)
    this.name = 'ReviewRequestError'
    this.details = details
  }
}

function clientError(message: string, correlationId = ''): ReviewRequestError {
  return new ReviewRequestError({
    category: 'internal_error',
    message,
    correlation_id: correlationId,
    processing_duration_ms: 0,
  })
}

export async function submitReview(submission: ReviewSubmission): Promise<ReviewResponse> {
  const formData = new FormData()
  formData.set('brand_name', submission.brandName)
  formData.set('class_type', submission.classType)
  formData.set('expected_abv', submission.expectedAbv)
  formData.set('expected_net_contents', submission.expectedNetContents)
  formData.set('expected_net_contents_unit', submission.expectedNetContentsUnit)
  formData.set('image', submission.image)

  let response: Response
  try {
    response = await fetch('/api/reviews', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: formData,
    })
  } catch {
    throw new ReviewRequestError({
      category: 'internal_error',
      message: 'The review service could not be reached. Check your connection and try again.',
      correlation_id: '',
      processing_duration_ms: 0,
    })
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw clientError(
      'The review service returned an unreadable response. Try again.',
      response.headers.get('X-Correlation-ID') ?? '',
    )
  }

  if (!response.ok) {
    if (isApiErrorResponse(payload)) throw new ReviewRequestError(payload)
    throw clientError(
      'The review service returned an unexpected error. Try again.',
      response.headers.get('X-Correlation-ID') ?? '',
    )
  }

  if (!isReviewResponse(payload)) {
    throw clientError(
      'The review service returned an unexpected result. Try again.',
      response.headers.get('X-Correlation-ID') ?? '',
    )
  }

  return payload
}
