import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import type { CheckResult, ReviewResponse } from './reviewTypes'

const checkNames = [
  'brand_name',
  'class_type',
  'alcohol_content',
  'net_contents',
  'government_warning',
] as const

function makeChecks(status: CheckResult['status'] = 'match'): CheckResult[] {
  return checkNames.map((name) => ({
    name,
    status,
    expected_value: name === 'government_warning' ? 'Required warning text and styling' : 'Expected',
    extracted_values: ['Observed'],
    normalized_expected: name === 'net_contents' ? '750 mL' : null,
    normalized_extracted: name === 'net_contents' ? ['750 mL'] : [],
    reason: status === 'match' ? 'The visible value matches.' : 'The visible value differs.',
    limitations: name === 'government_warning' ? ['Physical type size is not measurable.'] : [],
  }))
}

function makeResult(overrides: Partial<ReviewResponse> = {}): ReviewResponse {
  return {
    outcome: 'all_checks_passed',
    checks: makeChecks(),
    processing_duration_ms: 1250,
    correlation_id: 'review-123',
    processing_mode: 'synthetic',
    ...overrides,
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText('Expected brand name'), {
    target: { value: 'Treasury Reserve' },
  })
  fireEvent.change(screen.getByLabelText('Expected class or type'), {
    target: { value: 'Kentucky Straight Bourbon Whiskey' },
  })
  fireEvent.change(screen.getByLabelText('Expected alcohol by volume'), {
    target: { value: '45' },
  })
  fireEvent.change(screen.getByLabelText('Expected net contents'), {
    target: { value: '750' },
  })
  fireEvent.change(screen.getByLabelText('Label image'), {
    target: {
      files: [new File(['image'], 'label.png', { type: 'image/png' })],
    },
  })
}

describe('App', () => {
  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:label-preview'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('presents the complete reviewer-assist form and upload guidance', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: 'Alcohol Label Verification' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Expected brand name')).toBeInTheDocument()
    expect(screen.getByLabelText('Expected class or type')).toBeInTheDocument()
    expect(screen.getByLabelText('Expected alcohol by volume')).toBeInTheDocument()
    expect(screen.getByLabelText('Expected net contents')).toBeInTheDocument()
    expect(screen.getByLabelText('Net-contents unit')).toBeInTheDocument()
    expect(screen.getByLabelText('Label image')).toHaveAttribute(
      'accept',
      'image/jpeg,image/png,image/webp',
    )
    expect(screen.getByText(/up to 10 MB/i)).toBeInTheDocument()
    expect(screen.getByText(/synthetic or non-sensitive data/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Review findings appear here' })).toBeInTheDocument()
  })

  it('validates required values before making a request', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(screen.getByText('Enter the expected brand name.')).toBeInTheDocument()
    expect(screen.getByText('Enter the expected class or type.')).toBeInTheDocument()
    expect(screen.getByText('Enter an ABV from 0 to 100.')).toBeInTheDocument()
    expect(screen.getByText('Enter a net-content value greater than 0.')).toBeInTheDocument()
    expect(screen.getByText('Choose one label image or composite.')).toBeInTheDocument()
    expect(screen.getByLabelText('Expected brand name')).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('disables duplicate submission and announces loading', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))
    render(<App />)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(screen.getByRole('button', { name: 'Reviewing label…' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Reading and comparing the label')
  })

  it('shows all five checks and synthetic mode for a successful review', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(makeResult()))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fillValidForm()

    expect(screen.getByAltText('Preview of label.png')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(await screen.findByRole('heading', { name: 'All checks passed' })).toBeInTheDocument()
    expect(screen.getByText(/synthetic extraction mode/i)).toBeInTheDocument()
    const checks = screen.getByRole('group', { name: 'Review checks' })
    expect(within(checks).getAllByText('Match')).toHaveLength(5)
    expect(within(checks).getByRole('heading', { name: 'Government Warning' })).toBeInTheDocument()
    expect(screen.getByText('1.25 seconds')).toBeInTheDocument()
    expect(screen.getByText('Reference: review-123')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/reviews',
      expect.objectContaining({
        headers: {
          'Idempotency-Key': expect.stringMatching(
            /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
          ),
        },
      }),
    )
  })

  it('shows a Needs review outcome and the mismatch reason', async () => {
    const checks = makeChecks()
    checks[3] = {
      ...checks[3],
      status: 'mismatch',
      extracted_values: ['700 mL'],
      normalized_extracted: ['700 mL'],
      reason: 'Expected 750 mL, but the image shows 700 mL.',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(makeResult({ outcome: 'needs_review', checks }))),
    )
    render(<App />)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(await screen.findByRole('heading', { name: 'Needs review' })).toBeInTheDocument()
    expect(screen.getByText('Expected 750 mL, but the image shows 700 mL.')).toBeInTheDocument()
    expect(screen.getByText('Mismatch')).toBeInTheDocument()
  })

  it.each([
    ['traffic_throttled', 'Please wait before trying again', 429],
    ['capacity_reached', 'Review capacity is temporarily full', 503],
    ['duplicate_submission', 'This review was already submitted', 409],
    ['internal_error', 'The application encountered an error', 500],
  ] as const)('explains the %s failure category in plain language', async (category, title, status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            category,
            message: 'The review could not run right now.',
            correlation_id: 'category-test',
            processing_duration_ms: 20,
          },
          status,
        ),
      ),
    )
    render(<App />)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument()
  })

  it('identifies an unreachable application separately from a provider response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('network failure'))))
    render(<App />)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(
      await screen.findByRole('heading', { name: 'The application encountered an error' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/review service could not be reached/i)).toBeInTheDocument()
  })

  it('explains a recoverable failure and retains the entered values', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            category: 'provider_timeout',
            message: 'Label extraction timed out.',
            correlation_id: 'failure-456',
            processing_duration_ms: 12000,
          },
          504,
        ),
      ),
    )
    render(<App />)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Review label' }))

    expect(await screen.findByRole('heading', { name: 'Unable to process' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'The extraction service timed out' })).toBeInTheDocument()
    expect(screen.getByText('Label extraction timed out.')).toBeInTheDocument()
    expect(screen.getByLabelText('Expected brand name')).toHaveValue('Treasury Reserve')
    expect((screen.getByLabelText('Label image') as HTMLInputElement).files).toHaveLength(1)
    expect(screen.getByText('Reference: failure-456')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review label' })).toBeEnabled())
  })
})
