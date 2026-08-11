import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('identifies the prototype as a reviewer-assist workflow', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: 'Alcohol Label Verification' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/keeping uncertain and regulatory decisions/i)).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('P0 review workflow is being implemented')
  })
})
