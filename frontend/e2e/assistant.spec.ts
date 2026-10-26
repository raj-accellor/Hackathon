import { expect, test } from '@playwright/test'

// Chat assistant (bottom-right). Runs against whichever stack E2E_BASE_URL points at; the reply may come from
// the configured LLM or from the keyword fallback, and either must open the page the user asked about.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('broker-login-dismissed', '1'))
})

test('assistant icon sits bottom-right and takes the user to the page they ask about @mobile', async ({ page }) => {
  await page.goto('/')
  const icon = page.getByRole('button', { name: 'Open MI007 bot chat assistant' })
  await expect(icon).toBeVisible()
  const box = (await icon.boundingBox())!
  const vp = page.viewportSize()!
  expect(box.x + box.width).toBeGreaterThan(vp.width - 40)
  expect(box.y + box.height).toBeGreaterThan(vp.height - 40)

  await icon.click()
  const dialog = page.getByRole('dialog', { name: 'MI007 bot chat' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Message the assistant').fill('take me to the sector rotation graph')
  await dialog.getByRole('button', { name: 'Send' }).click()
  await expect(page).toHaveURL(/\/rotation$/, { timeout: 60_000 })
  await expect(dialog.getByRole('link', { name: /Go to Sector rotation/ })).toBeVisible()
  await page.screenshot({ path: process.env.SHOT ?? 'test-results/assistant.png' })
})

test('robot icon shows the MI007 greeting popup, which opens the chat and stays dismissed', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('robot-icon')).toBeVisible()
  const greet = page.getByTestId('chat-greeting')
  await expect(greet).toHaveText("Hi, I'm MI007 bot. How can I help you?")
  await page.screenshot({ path: process.env.SHOT ?? 'test-results/greeting.png' })
  await greet.click()
  await expect(page.getByRole('dialog', { name: 'MI007 bot chat' })).toBeVisible()
  await page.getByRole('button', { name: 'Close MI007 bot' }).first().click()
  await expect(greet).toHaveCount(0)
  await page.reload()
  await expect(greet).toHaveCount(0)
})

test('robot icon can be dragged anywhere, still opens on click, and keeps its place after reload', async ({ page }) => {
  await page.goto('/')
  const icon = page.getByTestId('chat-icon')
  const start = (await icon.boundingBox())!
  // drag from the bottom-right corner to the upper-left area of the screen
  await page.mouse.move(start.x + 28, start.y + 28)
  await page.mouse.down()
  await page.mouse.move(400, 300, { steps: 12 })
  await page.mouse.move(300, 200, { steps: 12 })
  await page.mouse.up()
  await page.mouse.move(900, 700)
  // the icon's own left/top (its hover/drag scale animation would skew a bounding box)
  const place = () => icon.evaluate((el) => ({ x: parseFloat((el as HTMLElement).style.left), y: parseFloat((el as HTMLElement).style.top), width: 56, height: 56 }))
  const moved = await place()
  expect(Math.abs(moved.x + 28 - 300)).toBeLessThan(4)
  expect(Math.abs(moved.y + 28 - 200)).toBeLessThan(4)
  // the drag did not open the chat; the greeting now opens below the icon
  await expect(page.getByRole('dialog', { name: 'MI007 bot chat' })).toHaveCount(0)
  await expect(page.getByTestId('chat-greeting')).toBeVisible()
  expect((await page.getByTestId('chat-greeting').boundingBox())!.y).toBeGreaterThan(moved.y + moved.height)
  await page.screenshot({ path: process.env.SHOT ?? 'test-results/moved.png' })

  // a plain click opens the panel, which fits on screen
  await icon.click()
  const panel = page.getByRole('dialog', { name: 'MI007 bot chat' })
  await expect(panel).toBeVisible()
  const pb = (await panel.boundingBox())!
  const vp = page.viewportSize()!
  expect(pb.x).toBeGreaterThanOrEqual(0)
  expect(pb.y).toBeGreaterThanOrEqual(0)
  expect(pb.x + pb.width).toBeLessThanOrEqual(vp.width)
  expect(pb.y + pb.height).toBeLessThanOrEqual(vp.height)

  await page.reload()
  const after = await place()
  expect(after.x).toBe(moved.x)
  expect(after.y).toBe(moved.y)

  // keyboard move, then reset to the corner
  await icon.focus()
  await page.keyboard.press('ArrowRight')
  expect((await place()).x).toBe(after.x + 16)
  await icon.click()
  await page.getByRole('button', { name: 'Reset position' }).click()
  const reset = await place()
  expect(reset.x + reset.width).toBeGreaterThan(vp.width - 40)
  expect(reset.y + reset.height).toBeGreaterThan(vp.height - 40)
})
