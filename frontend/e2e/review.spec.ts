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

const batchHeader =
  'Application ID,Label Image Filename,Expected Brand,Expected Class/Type,Expected ABV,Expected Net Contents\r\n'

async function openBatchWorkflow(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Batch preflight' }).click()
  await expect(page.getByRole('heading', { name: 'Prepare a batch package' })).toBeVisible()
}

async function uploadBatch(page: Page, rows: string, images: string[] = ['label.png']) {
  await page.getByLabel('Spreadsheet').setInputFiles({
    name: 'batch.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(batchHeader + rows),
  })
  if (images.length > 0) {
    await page.getByLabel('Label images').setInputFiles(
      images.map((name) => ({ name, mimeType: 'image/png', buffer: pngPixel })),
    )
  }
  await page.getByRole('button', { name: 'Check batch' }).click()
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
  await expect(page.getByRole('button', { name: 'Single label' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Batch preflight' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('Expected brand name')).toBeFocused()

  const outlineStyle = await page.getByLabel('Expected brand name').evaluate((element) =>
    getComputedStyle(element).outlineStyle,
  )
  expect(outlineStyle).toBe('solid')
})

test('preflights a valid package and recovers it after refresh', async ({ page }) => {
  await openBatchWorkflow(page)
  await uploadBatch(page, 'APP-1,label.png,Brand,Bourbon,45,750 mL\r\n')

  await expect(page.getByRole('heading', { name: 'Review preflight results' })).toBeVisible()
  await expect(page.locator('.count-ready strong')).toHaveText('1')
  await expect(page.locator('.count-correction strong')).toHaveText('0')
  await expect(page.getByText('Ready', { exact: true }).last()).toBeVisible()
  await expect(page).toHaveURL(/\?batch=[0-9a-f-]+$/)

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Review preflight results' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'APP-1' })).toBeVisible()
})

test('corrects a mixed package and explicitly confirms ready-only selection', async ({ page }) => {
  await openBatchWorkflow(page)
  await uploadBatch(
    page,
    'APP-1,label.png,Brand,Bourbon,45,750 mL\r\n' +
      'APP-2,missing.png,Brand,Bourbon,101,750 mL\r\n',
  )

  await expect(page.locator('.count-ready strong')).toHaveText('1')
  await expect(page.locator('.count-correction strong')).toHaveText('1')
  await page.getByRole('button', { name: 'Process all ready cases' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(/1 case still need correction/i)).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Confirm ready cases' })).toBeFocused()
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  const secondCase = page.locator('.batch-case').filter({ hasText: 'APP-2' })
  await secondCase.getByRole('button', { name: 'Edit expected values' }).click()
  await secondCase.getByLabel('Expected ABV').fill('45')
  await secondCase.getByRole('button', { name: 'Save expected values' }).click()
  await expect(secondCase.getByText('Select the label image named by this row.')).toBeVisible()
  await secondCase.getByLabel('Replace image for row 3').setInputFiles({
    name: 'replacement.png',
    mimeType: 'image/png',
    buffer: pngPixel,
  })

  await expect(page.locator('.count-ready strong')).toHaveText('2')
  await expect(page.locator('.count-correction strong')).toHaveText('0')
  await page.getByRole('button', { name: 'Process all ready cases' }).click()
  await page.getByRole('button', { name: 'Confirm ready cases' }).click()
  await expect(page.getByRole('heading', { name: /Batch processing (started|finished)/ })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Batch processing finished' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('2 completed')
})

test('keeps an entirely invalid package as an understandable correction draft', async ({ page }) => {
  await openBatchWorkflow(page)
  await uploadBatch(page, ',missing.png,,,101,750 oz\r\n', [])

  await expect(page.getByText('Missing application ID')).toBeVisible()
  await expect(page.getByText('Enter an application ID.')).toBeVisible()
  await expect(page.getByText('Enter an expected ABV from 0 through 100.')).toBeVisible()
  await expect(page.locator('.count-ready strong')).toHaveText('0')
  await expect(page.locator('.count-correction strong')).toHaveText('1')
  await expect(page.getByRole('button', { name: 'Process all ready cases' })).toBeDisabled()
})
