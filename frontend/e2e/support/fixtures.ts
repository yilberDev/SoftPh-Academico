import { test as base, expect, type APIResponse, type Page } from '@playwright/test';
import { E2E_ORIGIN } from '../../playwright.config';
import { adminPassword, residentPassword, resetDatabase, type Seed } from './data';

/**
 * Every test starts from the same data (seed.py "reset"), so tests are independent.
 *
 * `signIn` uses the real login endpoint (Argon2, session cookie); the browser
 * context keeps the HttpOnly cookie. The CSRF token stays in memory for the data
 * preparation calls of a test (`api`), as the pages keep it; it is never printed.
 */

export interface Api {
  get(path: string): Promise<APIResponse>;
  post(path: string, data: unknown): Promise<APIResponse>;
  /** A JSON answer with the expected status (data preparation). */
  json<T>(response: APIResponse, status?: number): Promise<T>;
}

interface Fixtures {
  seed: Seed;
  signIn: (role: 'admin' | 'resident') => Promise<Api>;
  /** The page signed in as the administrator, with its API helper. */
  admin: { page: Page; api: Api };
}

export const test = base.extend<Fixtures>({
  seed: async ({}, use) => {
    await use(resetDatabase());
  },
  signIn: async ({ page, seed }, use) => {
    await use(async (role) => {
      const email = role === 'admin' ? seed.admin_email : seed.resident_email;
      const password = role === 'admin' ? adminPassword() : residentPassword();
      const login = await page.request.post('/api/v1/auth/login', { data: { email, password }, headers: { Origin: E2E_ORIGIN } });
      expect(login.status(), 'login of the E2E user').toBe(200);
      const { csrf_token: csrf } = (await login.json()) as { csrf_token: string };
      return apiFor(page, csrf);
    });
  },
  admin: async ({ page, signIn }, use) => {
    const api = await signIn('admin');
    await use({ page, api });
  }
});

function apiFor(page: Page, csrf: string): Api {
  return {
    get: (path) => page.request.get(`/api/v1${path}`),
    post: (path, data) =>
      page.request.post(`/api/v1${path}`, { data, headers: { Origin: E2E_ORIGIN, 'X-CSRF-Token': csrf } }),
    async json<T>(response: APIResponse, status = 200): Promise<T> {
      expect(response.status(), await response.text()).toBe(status);
      return (await response.json()) as T;
    }
  };
}

export { expect };

// --- Shared UI steps --------------------------------------------------------------------------------

/** The "Nueva obligación" panel, opened. */
export async function openNewCharge(page: Page) {
  await page.getByText('Nueva obligación', { exact: true }).click();
  return page.locator('[data-charge-form]');
}

/** Chooses Bloque → Torre → Apartamento in a form (labels of /admin/obligations). */
export async function chooseUnit(page: Page, scope: 'charge' | 'filter', label: string): Promise<void> {
  const [block, tower] = label.split('-');
  const ids = scope === 'charge' ? ['#charge-block', '#charge-tower', '#charge-unit'] : ['#filter-block', '#filter-tower', '#filter-unit'];
  await page.locator(ids[0]).selectOption({ label: block });
  await expect(page.locator(ids[1])).toBeEnabled();
  await page.locator(ids[1]).selectOption({ label: tower });
  await expect(page.locator(ids[2])).toBeEnabled();
  await page.locator(ids[2]).selectOption({ label });
}

/** Monthly generation through the API (data preparation of the list and flow tests). */
export async function generateMonthly(api: Api, period: string, amount: string, dueDate: string) {
  const query = new URLSearchParams({ billing_period: period, amount, due_date: dueDate });
  const preview = await api.json<{ to_create: number; preview_fingerprint: string }>(await api.get(`/admin/charges/monthly/preview?${query}`));
  return api.json<{ created: number }>(
    await api.post('/admin/charges/monthly', {
      billing_period: period,
      amount,
      due_date: dueDate,
      expected_to_create: preview.to_create,
      preview_fingerprint: preview.preview_fingerprint
    })
  );
}

export async function createCharge(api: Api, payload: Record<string, unknown>): Promise<{ id: number }> {
  return api.json<{ id: number }>(await api.post('/admin/charges', payload), 201);
}

/** Text of an element with the narrow no-break spaces of Intl normalized. */
export const normalized = (text: string | null) => (text ?? '').replace(/\s+/g, ' ').trim();
