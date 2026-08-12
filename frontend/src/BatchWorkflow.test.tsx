import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BatchWorkflow } from './BatchWorkflow'
import type {
  BatchCaseSummary,
  BatchCaseDetail,
  BatchPreflightResponse,
  BatchResponse,
  PreflightIssue,
} from './batchTypes'
import type { CheckResult } from './reviewTypes'

function issue(code: string, message: string, field: PreflightIssue['field']): PreflightIssue {
  return {
    code,
    scope: 'row',
    row_number: 3,
    field,
    severity: 'error',
    message,
  }
}

function summary(overrides: Partial<BatchCaseSummary> = {}): BatchCaseSummary {
  return {
    case_id: '5eb714c5-28d3-457d-b4dc-a9214b59878e',
    row_number: 2,
    application_id: 'APP-1',
    label_image_filename: 'label.png',
    state: 'ready',
    issues: [],
    outcome: null,
    processing_duration_ms: null,
    short_reason: null,
    ...overrides,
  }
}

function batch(cases: BatchCaseSummary[]): BatchPreflightResponse {
  const ready = cases.filter((item) => item.state === 'ready').length
  return {
    batch_id: '718117a6-8284-4946-8d65-7af8c333340c',
    state: 'draft',
    created_at: '2026-08-12T12:00:00Z',
    expires_at: '2026-08-13T12:00:00Z',
    counts: {
      total: cases.length,
      ready,
      needs_correction: cases.length - ready,
      queued: 0,
      processing: 0,
      completed: 0,
      failed: 0,
      interrupted: 0,
      not_selected: 0,
    },
    cases,
    next_poll_after_ms: null,
  }
}

function startedBatch(
  cases: BatchCaseSummary[],
  state: BatchResponse['state'] = 'processing',
): BatchResponse {
  const count = (caseState: BatchCaseSummary['state']) =>
    cases.filter((item) => item.state === caseState).length
  return {
    batch_id: '718117a6-8284-4946-8d65-7af8c333340c',
    state,
    created_at: '2026-08-12T12:00:00Z',
    expires_at: '2026-08-13T12:00:00Z',
    counts: {
      total: cases.length,
      needs_correction: count('needs_correction'),
      ready: count('ready'),
      queued: count('queued'),
      processing: count('processing'),
      completed: count('completed'),
      failed: count('failed'),
      interrupted: count('interrupted'),
      not_selected: count('not_selected'),
    },
    cases,
    next_poll_after_ms: state === 'queued' || state === 'processing' ? 1500 : null,
  }
}

const checkNames = [
  'brand_name',
  'class_type',
  'alcohol_content',
  'net_contents',
  'government_warning',
] as const

function caseDetail(caseSummary: BatchCaseSummary): BatchCaseDetail {
  const checks: CheckResult[] = checkNames.map((name) => ({
    name,
    status: caseSummary.outcome === 'all_checks_passed' ? 'match' : 'mismatch',
    expected_value: 'Expected',
    extracted_values: ['Observed'],
    normalized_expected: null,
    normalized_extracted: [],
    reason: caseSummary.short_reason ?? 'The visible value differs.',
    limitations: [],
  }))
  return {
    summary: caseSummary,
    expected_input: {
      brand_name: 'Brand',
      class_type: 'Bourbon',
      expected_abv: '45',
      expected_net_contents: '750 mL',
    },
    normalized_expected: {},
    result: caseSummary.state === 'completed' ? {
      result: {
        outcome: caseSummary.outcome ?? 'needs_review',
        checks,
        processing_duration_ms: caseSummary.processing_duration_ms ?? 1000,
      },
      processing_mode: 'synthetic',
      correlation_id: '5eb714c5-28d3-457d-b4dc-a9214b59878e',
      completed_at: '2026-08-12T12:01:00Z',
      expires_at: '2026-08-13T12:00:00Z',
    } : null,
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function selectPackage(images = [new File(['image'], 'label.png', { type: 'image/png' })]) {
  fireEvent.change(screen.getByLabelText('Spreadsheet'), {
    target: { files: [new File(['header,row'], 'batch.csv', { type: 'text/csv' })] },
  })
  fireEvent.change(screen.getByLabelText('Label images'), { target: { files: images } })
}

describe('BatchWorkflow', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('shows templates, separate selectors, visible limits, and a valid ready draft', async () => {
    const readyBatch = batch([summary()])
    const fetchMock = vi.fn(async () => jsonResponse(readyBatch, 201))
    vi.stubGlobal('fetch', fetchMock)
    render(<BatchWorkflow />)

    expect(screen.getByRole('link', { name: 'Download XLSX template' })).toHaveAttribute(
      'href',
      '/api/batch-template.xlsx',
    )
    expect(screen.getByRole('link', { name: 'Download CSV template' })).toHaveAttribute(
      'href',
      '/api/batch-template.csv',
    )
    expect(screen.getByText(/up to 25 application rows and 25 images/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Spreadsheet')).not.toBe(screen.getByLabelText('Label images'))

    selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check batch' }))

    expect(await screen.findByRole('heading', { name: 'Review preflight results' })).toBeInTheDocument()
    expect(screen.getAllByText('Ready')).toHaveLength(2)
    expect(screen.getByText('0', { selector: '.count-correction strong' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/batches/preflight',
      expect.objectContaining({ method: 'POST', body: expect.any(FormData) }),
    )
    expect(new URL(window.location.href).searchParams.get('batch')).toBe(readyBatch.batch_id)
  })

  it('explains an entirely invalid draft and prevents processing with zero ready cases', async () => {
    const correction = summary({
      state: 'needs_correction',
      application_id: '',
      label_image_filename: 'missing.png',
      issues: [
        issue('missing_application_id', 'Enter an application ID.', 'application_id'),
        issue('missing_image', 'Select the label image named by this row.', 'label_image_filename'),
      ],
    })
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(batch([correction]), 201)))
    render(<BatchWorkflow />)
    selectPackage([])

    fireEvent.click(screen.getByRole('button', { name: 'Check batch' }))

    expect(await screen.findByText('Missing application ID')).toBeInTheDocument()
    expect(screen.getByText('Enter an application ID.')).toBeInTheDocument()
    expect(screen.getByText('Select the label image named by this row.')).toBeInTheDocument()
    expect(screen.getByText('Needs correction')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Process all ready cases' })).toBeDisabled()
  })

  it('requires explicit confirmation when mixed preflight results have corrections', async () => {
    const mixed = batch([
      summary(),
      summary({
        case_id: 'd2ac9a6b-0740-4425-941f-067b891d0f3f',
        row_number: 3,
        application_id: 'APP-2',
        state: 'needs_correction',
        issues: [issue('missing_image', 'Select the label image named by this row.', 'label_image_filename')],
      }),
    ])
    const started: BatchResponse = {
      ...mixed,
      state: 'queued',
      counts: {
        ...mixed.counts,
        ready: 0,
        needs_correction: 0,
        queued: 1,
        not_selected: 1,
      },
      cases: [
        { ...mixed.cases[0], state: 'queued' },
        { ...mixed.cases[1], state: 'not_selected' },
      ],
      next_poll_after_ms: 1500,
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(mixed, 201))
      .mockResolvedValueOnce(jsonResponse(started, 202))
    vi.stubGlobal('fetch', fetchMock)
    render(<BatchWorkflow />)
    selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check batch' }))

    const start = await screen.findByRole('button', { name: 'Process all ready cases' })
    fireEvent.click(start)

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/1 case still need correction/i)).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: 'Confirm ready cases' })
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    expect(confirm).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(cancel).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(confirm).toHaveFocus()
    fireEvent.click(confirm)

    expect(await screen.findByRole('heading', { name: 'Batch processing started' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith(
      `/api/batches/${mixed.batch_id}/start`,
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': expect.stringMatching(/^batch-start:/) }),
        body: JSON.stringify({ selection: 'ready_cases_only' }),
      }),
    )
  })

  it('recovers an unexpired draft from the batch identifier after refresh', async () => {
    const recovered = batch([summary()])
    window.history.replaceState({}, '', `/?batch=${recovered.batch_id}`)
    const fetchMock = vi.fn(async () => jsonResponse(recovered))
    vi.stubGlobal('fetch', fetchMock)

    render(<BatchWorkflow />)

    expect(screen.getByRole('status')).toHaveTextContent('Recovering batch draft')
    expect(await screen.findByRole('heading', { name: 'Review preflight results' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(`/api/batches/${recovered.batch_id}`, undefined)
  })

  it('shows terminal progress, filters outcomes, reuses comparison detail, and offers export', async () => {
    const passed = summary({
      state: 'completed',
      outcome: 'all_checks_passed',
      processing_duration_ms: 900,
      short_reason: 'All five checks matched.',
    })
    const review = summary({
      case_id: 'cc0b750d-384c-4ca2-b2d8-e8ba0eca5e68',
      row_number: 3,
      application_id: 'APP-2',
      state: 'completed',
      outcome: 'needs_review',
      processing_duration_ms: 1200,
      short_reason: 'Expected 750 mL, but the image shows 700 mL.',
    })
    const failed = summary({
      case_id: 'fb939d25-81bd-4bd5-bfd6-80607c5261cc',
      row_number: 4,
      application_id: 'APP-3',
      state: 'failed',
      short_reason: 'The extraction service was unavailable.',
    })
    const completed = startedBatch([passed, review, failed], 'completed')
    window.history.replaceState({}, '', `/batch?batch=${completed.batch_id}`)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(completed))
      .mockResolvedValueOnce(jsonResponse(caseDetail(review)))
    vi.stubGlobal('fetch', fetchMock)

    render(<BatchWorkflow />)

    expect(await screen.findByRole('heading', { name: 'Batch processing finished' })).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '3')
    expect(screen.getByRole('link', { name: 'Download results CSV' })).toHaveAttribute(
      'href',
      `/api/batches/${completed.batch_id}/results.csv`,
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Needs review 1/ }))
    expect(screen.getByRole('rowheader', { name: /APP-2/ })).toBeInTheDocument()
    expect(screen.queryByRole('rowheader', { name: /APP-1/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'View details' }))

    await screen.findByRole('heading', { name: 'APP-2' })
    await waitFor(() => expect(screen.getByRole('heading', { name: 'APP-2' })).toHaveFocus())
    expect(screen.getByRole('heading', { name: 'Needs review' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Review checks' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Failed \/ interrupted 1/ }))
    expect(screen.getByRole('rowheader', { name: /APP-3/ })).toBeInTheDocument()
  })

  it('keeps progress accurate when a later row finishes first and stops polling at terminal state', async () => {
    vi.useFakeTimers()
    const initial = startedBatch([
      summary({ state: 'processing' }),
      summary({ case_id: 'cc0b750d-384c-4ca2-b2d8-e8ba0eca5e68', row_number: 3, application_id: 'APP-2', state: 'queued' }),
      summary({ case_id: 'fb939d25-81bd-4bd5-bfd6-80607c5261cc', row_number: 4, application_id: 'APP-3', state: 'queued' }),
    ])
    const outOfOrder = startedBatch([
      initial.cases[0],
      initial.cases[1],
      { ...initial.cases[2], state: 'completed', outcome: 'all_checks_passed', processing_duration_ms: 800, short_reason: 'All five checks matched.' },
    ])
    const terminal = startedBatch([
      { ...initial.cases[0], state: 'completed', outcome: 'needs_review', processing_duration_ms: 1100, short_reason: 'A visible value differs.' },
      { ...initial.cases[1], state: 'failed', short_reason: 'The extraction service was unavailable.' },
      outOfOrder.cases[2],
    ], 'completed')
    window.history.replaceState({}, '', `/batch?batch=${initial.batch_id}`)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(initial))
      .mockResolvedValueOnce(jsonResponse(outOfOrder))
      .mockResolvedValueOnce(jsonResponse(terminal))
    vi.stubGlobal('fetch', fetchMock)
    render(<BatchWorkflow />)

    await act(async () => { await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAccessibleName(/1 of 3 selected cases finished/)

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByRole('heading', { name: 'Batch processing finished' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAccessibleName(/3 of 3 selected cases finished/)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('backs off after a temporary polling failure and then recovers', async () => {
    vi.useFakeTimers()
    const active = startedBatch([summary({ state: 'processing' })])
    const terminal = startedBatch([
      summary({ state: 'completed', outcome: 'all_checks_passed', processing_duration_ms: 700, short_reason: 'All five checks matched.' }),
    ], 'completed')
    window.history.replaceState({}, '', `/batch?batch=${active.batch_id}`)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(active))
      .mockRejectedValueOnce(new TypeError('network unavailable'))
      .mockResolvedValueOnce(jsonResponse(terminal))
    vi.stubGlobal('fetch', fetchMock)
    render(<BatchWorkflow />)

    await act(async () => { await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByRole('status')).toHaveTextContent('Retrying in 3 seconds')
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(2999) })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(screen.getByRole('heading', { name: 'Batch processing finished' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
