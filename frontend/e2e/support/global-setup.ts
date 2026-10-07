import { randomBytes } from 'node:crypto';
import { request } from '@playwright/test';
import { E2E_ORIGIN } from '../../playwright.config';
import { adminPassword, migrateDatabase, resetDatabase } from './data';

/**
 * Runs once, after Playwright started its own API (*_test) and Astro instances.
 *
 * - Random passwords for this run only (never hardcoded or printed); the
 *   workers inherit them through the environment.
 * - Alembic upgrade of the *_test database.
 * - Guard: the stack really is the E2E one. The E2E administrator only exists on
 *   the *_test database, so its login through Astro (browser proxy) and its
 *   seeded obligation in an SSR page prove that both paths reach it.
 */
export default async function globalSetup(): Promise<void> {
  process.env.E2E_ADMIN_PASSWORD = randomBytes(18).toString('base64url');
  process.env.E2E_RESIDENT_PASSWORD = randomBytes(18).toString('base64url');
  migrateDatabase();
  const seed = resetDatabase();

  const context = await request.newContext({ baseURL: E2E_ORIGIN });
  try {
    const login = await context.post('/api/v1/auth/login', {
      data: { email: seed.admin_email, password: adminPassword() },
      headers: { Origin: E2E_ORIGIN }
    });
    if (login.status() !== 200) throw new Error(`E2E login through Astro failed (${login.status()}): not the *_test stack.`);
    const detail = await context.get(`/admin/obligations/${seed.charges.history}`);
    if (!(await detail.text()).includes('Reparación de la puerta E2E')) {
      throw new Error('The SSR pages do not reach the *_test API.');
    }
  } finally {
    await context.dispose();
  }
}
