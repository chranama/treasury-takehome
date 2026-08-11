import { expect, test } from '@playwright/test'

test('renders the local application shell', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Alcohol Label Verification' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('P0 review workflow is being implemented')
})
