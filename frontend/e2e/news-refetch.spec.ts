import { expect, test } from '@playwright/test'

// Market news: the re-fetch button asks every source again (refresh=true) and the page keeps polling every 2 minutes,
// including while the tab is hidden and outside market hours. Runs against whichever stack E2E_BASE_URL points at.
test('news page re-fetches all sources on demand and shows the 2-minute auto-update', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('broker-login-dismissed', '1'))
  await page.goto('/news')
  await expect(page.getByTestId('news-autoupdate')).toContainText('Auto-updates every 2 min', { timeout: 60_000 })
  const refetch = page.waitForRequest((r) => r.url().includes('/api/v1/markets/news') && r.url().includes('refresh=true'))
  await page.getByTestId('news-refetch').click()
  await refetch
  await expect(page.getByTestId('news-autoupdate')).toContainText(/next in [12]:\d\d/, { timeout: 60_000 })
  await expect(page.getByRole('heading', { name: 'News', exact: true })).toBeVisible()
  await page.screenshot({ path: process.env.SHOT ?? 'test-results/news.png' })
})
