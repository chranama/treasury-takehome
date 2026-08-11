export type ProcessingMode = 'synthetic' | 'live'

export type ReviewOutcome = 'all_checks_passed' | 'needs_review' | 'unable_to_process'

export type CheckName =
  | 'brand_name'
  | 'class_type'
  | 'alcohol_content'
  | 'net_contents'
  | 'government_warning'

export type CheckStatus = 'match' | 'mismatch' | 'needs_review' | 'not_evaluated'

export type ErrorCategory =
  | 'invalid_input'
  | 'live_extraction_disabled'
  | 'capacity_reached'
  | 'traffic_throttled'
  | 'duplicate_submission'
  | 'provider_timeout'
  | 'provider_unavailable'
  | 'malformed_provider_output'
  | 'internal_error'

export interface CheckResult {
  name: CheckName
  status: CheckStatus
  expected_value: string | null
  extracted_values: string[]
  normalized_expected: string | null
  normalized_extracted: string[]
  reason: string
  limitations: string[]
}

export interface ReviewResponse {
  outcome: ReviewOutcome
  checks: CheckResult[]
  processing_duration_ms: number
  correlation_id: string
  processing_mode: ProcessingMode
}

export interface ApiErrorResponse {
  category: ErrorCategory
  message: string
  correlation_id: string
  processing_duration_ms: number
}

export interface ReviewSubmission {
  brandName: string
  classType: string
  expectedAbv: string
  expectedNetContents: string
  expectedNetContentsUnit: 'mL' | 'L'
  image: File
}
