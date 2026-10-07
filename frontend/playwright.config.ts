import { defineConfig, devices } from '@playwright/test';

/**
 * E2E tests (Playwright) on the dedicated *_test database. Run with `npm run test:e2e`.
 *
 * Playwright starts its own stack and never reuses the development one: the
 * same FastAPI application on the *_test database (backend/tests/e2e/serve.py,
 * port 8100) and Astro dev pointing at it (port 4400). Nothing in .env changes.
 * One worker: every test starts from the same data (backend/tests/e2e/seed.py).
 */

export const E2E_ORIGIN = 'http://localhost:4400';
const API_URL = 'http://127.0.0.1:8100';

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results/e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  globalSetup: './e2e/support/global-setup.ts',
  globalTeardown: './e2e/support/global-teardown.ts',
  use: {
    baseURL: E2E_ORIGIN,
    locale: 'es-CO',
    timezoneId: 'America/Bogota',
    // Evidence of a failure only (no secrets: the password fields are never typed into a trace).
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `.venv/bin/python -m tests.e2e.serve --port 8100 --origin ${E2E_ORIGIN}`,
      cwd: '../backend',
      url: `${API_URL}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      // --ignore-lock: runs beside a developer's own dev server without touching its lock file.
      command: 'npx astro dev --port 4400 --host localhost --ignore-lock',
      url: E2E_ORIGIN,
      env: { API_INTERNAL_URL: API_URL },
      reuseExistingServer: false,
      timeout: 120_000
    }
  ]
});
