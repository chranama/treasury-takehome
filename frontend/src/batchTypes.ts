import type { ProcessingMode, ReviewResult } from './reviewTypes'

export type BatchCaseState =
  | 'needs_correction'
  | 'ready'
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'not_selected'

export type BatchState = 'draft' | 'queued' | 'processing' | 'completed' | 'interrupted'

export type BatchField =
  | 'application_id'
  | 'label_image_filename'
  | 'expected_brand'
  | 'expected_class_type'
  | 'expected_abv'
  | 'expected_net_contents'

export interface PreflightIssue {
  code: string
  scope: 'batch' | 'row' | 'image'
  row_number: number | null
  field: BatchField | null
  severity: 'error' | 'warning'
  message: string
}

export interface BatchCaseSummary {
  case_id: string
  row_number: number
  application_id: string
  label_image_filename: string
  state: BatchCaseState
  issues: PreflightIssue[]
  outcome: 'all_checks_passed' | 'needs_review' | 'unable_to_process' | null
  processing_duration_ms: number | null
  short_reason: string | null
}

export interface BatchStateCounts {
  total: number
  needs_correction: number
  ready: number
  queued: number
  processing: number
  completed: number
  failed: number
  interrupted: number
  not_selected: number
}

export interface BatchResponse {
  batch_id: string
  state: BatchState
  created_at: string
  expires_at: string
  counts: BatchStateCounts
  cases: BatchCaseSummary[]
  next_poll_after_ms: number | null
}

export interface BatchPreflightResponse extends BatchResponse {
  state: 'draft'
  next_poll_after_ms: null
}

export interface BatchExpectedInput {
  brand_name: string
  class_type: string
  expected_abv: string
  expected_net_contents: string
}

export interface BatchCaseDetail {
  summary: BatchCaseSummary
  expected_input: BatchExpectedInput
  normalized_expected: unknown | null
  result: StoredBatchCaseResult | null
}

export interface StoredBatchCaseResult {
  result: ReviewResult
  processing_mode: ProcessingMode
  correlation_id: string
  completed_at: string
  expires_at: string
}

export interface BatchPatch {
  brand_name?: string
  class_type?: string
  expected_abv?: string
  expected_net_contents?: string
}
