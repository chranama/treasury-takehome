import { expect, test, type Page } from '@playwright/test'

const pngPixel = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
)

async function submitReview(page: Page) {
  await page.getByLabel('Expected brand name').fill('Treasury Reserve')
  await page.getByLabel('Expected class or type').fill('Kentucky Straight Bourbon Whiskey')
  await page.getByLabel('Expected alcohol by volume').fill('45')
  await page.getByLabel('Expected net contents').fill('750')
  await page.getByLabel('Label image').setInputFiles({
    name: 'label.png',
    mimeType: 'image/png',
    buffer: pngPixel,
  })
  await expect(page.getByAltText('Preview of label.png')).toBeVisible()
  await page.getByRole('button', { name: 'Review label' }).click()
}

test('completes a clear matching review through the fake adapter', async ({ page }) => {
  await page.goto('/')
  await submitReview(page)

  await expect(page.getByRole('heading', { name: 'All checks passed' })).toBeVisible()
  await expect(page.getByText('Synthetic extraction mode.')).toBeVisible()
  await expect(page.locator('.check-card.status-match .check-status')).toHaveCount(5)
  await expect(page.getByText(/not a regulatory approval or rejection/i)).toBeVisible()
})

test('completes a known net-contents mismatch through the fake adapter', async ({ page }) => {
  await page.goto('http://127.0.0.1:5174')
  await submitReview(page)

  await expect(page.getByRole('heading', { name: 'Needs review' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Net contents' })).toBeVisible()
  await expect(page.locator('.check-card.status-mismatch .check-status')).toContainText('Mismatch')
  await expect(page.getByText('700 mL', { exact: true }).first()).toBeVisible()
})

test('provides a visible keyboard focus path into the form', async ({ page }) => {
  await page.goto('/')

  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('Expected brand name')).toBeFocused()

  const outlineStyle = await page.getByLabel('Expected brand name').evaluate((element) =>
    getComputedStyle(element).outlineStyle,
  )
  expect(outlineStyle).toBe('solid')
})
