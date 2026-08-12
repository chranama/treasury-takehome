import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BatchWorkflow } from './BatchWorkflow'
import type {
  BatchCaseSummary,
  BatchPreflightResponse,
  PreflightIssue,
} from './batchTypes'

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
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(mixed, 201)))
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

    expect(screen.getByRole('status')).toHaveTextContent('Selection confirmed for 1 ready case')
    await waitFor(() => expect(start).toHaveFocus())
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
})
