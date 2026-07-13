/**
 * Critical UI workflows against the live local stack (seeded demo tenant).
 * Also captures the repo's docs/screenshots/ set on the way through.
 */
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const SHOTS = '../../docs/screenshots'
const PASSWORD = 'relayiq-demo-password' // dev-only seeded credential

async function login(page: Page, email: string): Promise<void> {
  await page.goto('/login')
  await page.getByLabel(/email/i).fill(email)
  await page.getByLabel(/password/i).fill(PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByText(/Cost \/ usable lead/i)).toBeVisible({ timeout: 15_000 })
}

test.describe.configure({ mode: 'serial' })

test('login page renders and rejects a bad password', async ({ page }) => {
  await page.goto('/login')
  await page.screenshot({ path: `${SHOTS}/01-login.png` })
  await page.getByLabel(/email/i).fill('operator@demo.relayiq.test')
  await page.getByLabel(/password/i).fill('definitely-wrong-pass')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('alert')).toContainText(/invalid credentials/i)
})

test('overview shows the primary metrics from persisted data', async ({ page }) => {
  await login(page, 'operator@demo.relayiq.test')
  for (const label of ['Cost / usable lead', 'Fill rate', 'Conflict rate',
                       'P95 provider latency', 'CRM sync failure rate']) {
    await expect(page.getByText(label, { exact: false }).first()).toBeVisible()
  }
  await page.screenshot({ path: `${SHOTS}/02-overview.png`, fullPage: true })
})

test('submit an enrichment and inspect its lineage', async ({ page, request }) => {
  await login(page, 'operator@demo.relayiq.test')
  await page.getByRole('link', { name: 'Entities' }).click()
  await expect(page.getByRole('table')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/03-entities.png` })

  // Deterministically pick an entity that has enriched canonical fields via the API
  const api = process.env.API_URL || 'http://localhost:8000'
  const loginRes = await request.post(`${api}/v1/auth/login`, {
    data: { email: 'operator@demo.relayiq.test', password: PASSWORD },
  })
  const token = (await loginRes.json()).access_token as string
  const auth = { Authorization: `Bearer ${token}` }
  const contacts = await (await request.get(
    `${api}/v1/contacts?limit=100&q=jordan`, { headers: auth },
  )).json()
  let entityId: string | null = null
  for (const c of contacts.items as Array<{ work_email: string | null; full_name: string | null; company_domain: string | null }>) {
    if (!c.work_email) continue
    if (/loadtest|repeatpool/.test(c.work_email)) continue // load-test identities fill nothing
    const res = await request.post(`${api}/v1/enrichment/execute`, {
      headers: auth,
      data: {
        entity_type: 'contact',
        entity: { work_email: c.work_email, full_name: c.full_name, company_domain: c.company_domain },
        requested_fields: ['job_title', 'seniority', 'department'],
        mode: 'sync',
      },
    })
    const job = await res.json()
    if ((job.result_summary?.fields_filled ?? 0) > 0 || (job.result_summary?.served_from_cache?.length ?? 0) > 0) {
      entityId = job.entity_id
      break
    }
  }
  test.skip(!entityId, 'no enrichable contact found in seed data')

  await page.goto(`/entities/contact/${entityId}`)
  await expect(page.getByText('Canonical fields')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/04-entity-detail.png`, fullPage: true })

  await page.getByRole('link', { name: 'Lineage' }).first().click()
  await expect(page.getByText('Routing decisions')).toBeVisible()
  await expect(page.getByText('Conflict reconciliation')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/05-lineage.png`, fullPage: true })
})

test('reviewer accepts a conflict and reverses it — history preserved', async ({ page }) => {
  await login(page, 'reviewer@demo.relayiq.test')
  await page.getByRole('link', { name: 'Review Queue' }).click()
  await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/06-review-queue.png` })

  // Wait for the queue to finish loading before deciding whether it's empty
  await page.waitForLoadState('networkidle')
  const firstRow = page.locator('tbody tr', { has: page.locator('td') }).first()
  const hasTask = await firstRow.isVisible().catch(() => false)
  test.skip(!hasTask || (await firstRow.innerText()).includes('Queue is clear'),
    'no pending review tasks in seed data')
  await firstRow.click()
  await expect(page.getByText('Decide')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/07-review-detail.png`, fullPage: true })

  await page.getByRole('button', { name: /accept suggested/i }).click()
  await expect(page.getByText(/accepted/i).first()).toBeVisible()

  await page.getByRole('button', { name: /reverse decision/i }).click()
  await expect(page.getByRole('alertdialog')).toContainText(/nothing is deleted/i)
  await page.getByRole('alertdialog').getByRole('button', { name: /^reverse$/i }).click()
  await expect(page.getByText('reversed').first()).toBeVisible()
  // Decision history keeps both actions
  await expect(page.getByText('accept_suggested').first()).toBeVisible()
  await expect(page.getByText('reverse', { exact: false }).first()).toBeVisible()
})

test('analytics and CRM views render measured data', async ({ page }) => {
  await login(page, 'operator@demo.relayiq.test')
  await page.getByRole('link', { name: 'Analytics' }).click()
  await expect(page.getByText('Total spend').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/08-analytics.png`, fullPage: true })

  await page.getByRole('link', { name: 'CRM Sync' }).click()
  await expect(page.getByRole('heading', { name: 'CRM synchronization' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/09-crm.png` })

  await page.getByRole('link', { name: 'Providers' }).click()
  await expect(page.getByText('SIMULATED').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/10-providers.png`, fullPage: true })
})

test('analyst is read-only', async ({ page }) => {
  await login(page, 'analyst@demo.relayiq.test')
  await page.getByRole('link', { name: 'Audit Log' }).click()
  await expect(page.getByText(/requires/i)).toBeVisible() // 403 empty state
})
