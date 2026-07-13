import { defineConfig } from '@playwright/test'

// Requires the local stack: API on :8000 (seeded demo tenant) and the dashboard on :5173.
//   make dev-deps && make migrate && make seed && make api   (terminal 1)
//   make dashboard                                           (terminal 2)
//   cd apps/dashboard && npx playwright test                 (terminal 3)
export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  retries: 0,
  use: {
    baseURL: process.env.DASHBOARD_URL || 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
  },
  reporter: [['list']],
})
