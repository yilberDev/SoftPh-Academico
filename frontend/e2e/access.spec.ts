import type { Page } from '@playwright/test';
import { E2E_ORIGIN } from '../playwright.config';
import { adminPassword, residentPassword } from './support/data';
import { expect, test } from './support/fixtures';

/** E2E 1, 12 and 13: sign-in, navigation, RESIDENTE (403) and no session (login). */

test.describe('administrator', () => {
  test('signs in, sees "Obligaciones" in the panel and the menu, and keeps the session', async ({ page, seed }) => {
    await page.goto('/login');
    await page.getByLabel('Correo electrónico').fill(seed.admin_email);
    await page.getByLabel('Contraseña').fill(adminPassword());
    await page.getByRole('button', { name: 'Ingresar' }).click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole('heading', { name: /Hola, Administradora Pruebas/ })).toBeVisible();

    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link', { name: 'Obligaciones' })).toBeVisible();
    await expect(page.getByRole('main').getByRole('link', { name: /Obligaciones/ })).toBeVisible();

    await page.getByRole('main').getByRole('link', { name: /Obligaciones/ }).click();
    await expect(page).toHaveURL(/\/admin\/obligations$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Obligaciones' })).toBeVisible();
    await expect(menu.getByRole('link', { name: 'Obligaciones' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText(/Mostrando 1–2 de 2 obligaciones/)).toBeVisible();

    // The session stays valid along the flow: detail, payments and back.
    await page.getByRole('link', { name: /Ver detalle de la obligación/ }).first().click();
    await expect(page).toHaveURL(/\/admin\/obligations\/\d+$/);
    await menu.getByRole('link', { name: 'Pagos' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Pagos' })).toBeVisible();
    await menu.getByRole('link', { name: 'Obligaciones' }).click();
    await expect(page.getByText(/Mostrando 1–2 de 2 obligaciones/)).toBeVisible();
  });
});

test.describe('RESIDENTE', () => {
  test('gets 403 "Acceso no autorizado" on the list and the detail, without obligation data', async ({ page, seed }) => {
    await page.goto('/login');
    await page.getByLabel('Correo electrónico').fill(seed.resident_email);
    await page.getByLabel('Contraseña').fill(residentPassword());
    await page.getByRole('button', { name: 'Ingresar' }).click();
    await expect(page).toHaveURL(/\/resident$/);

    for (const path of ['/admin/obligations', `/admin/obligations/${seed.charges.debt}`]) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(403);
      expect(response?.headers()['cache-control']).toBe('no-store');
      await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
      const html = await page.content();
      expect(html).not.toContain('Penalización por ruido E2E');
      expect(html).not.toContain('B2-TO1-APTO-206');
      expect(html).not.toContain('data-obligations-page');
    }
  });

  test('the API itself refuses the resident (not only the page): 403 with no-store', async ({ page, seed, signIn }) => {
    const api = await signIn('resident');
    for (const path of ['/admin/charges', `/admin/charges/${seed.charges.debt}`, '/admin/charges/monthly/preview?billing_period=2026-10&amount=1&due_date=2026-10-10']) {
      const response = await api.get(path);
      expect(response.status(), path).toBe(403);
      expect(response.headers()['cache-control']).toBe('no-store');
      expect(((await response.json()) as { error: { code: string } }).error.code).toBe('FORBIDDEN');
    }
    const created = await api.post('/admin/charges', { unit_id: seed.units['B1-TO1-APTO-101'], concept: 'OTHER', amount: '1', due_date: seed.today, description: 'x' });
    expect(created.status()).toBe(403);
    await page.goto('/resident');
    await expect(page.getByRole('navigation', { name: 'Principal' }).getByRole('link', { name: 'Obligaciones' })).toHaveCount(0);
  });
});

test.describe('no session', () => {
  test('the list and a detail URL go to the login page', async ({ page, seed }) => {
    for (const path of ['/admin/obligations', `/admin/obligations/${seed.charges.history}`]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
    }
  });

  test('the API answers 401 with no-store; a POST without the CSRF token is refused', async ({ page, seed, signIn }) => {
    const anonymous = await page.request.get('/api/v1/admin/charges');
    expect(anonymous.status()).toBe(401);
    expect(anonymous.headers()['cache-control']).toBe('no-store');

    await signIn('admin');
    const withoutToken = await page.request.post('/api/v1/admin/charges', {
      data: { unit_id: seed.units['B1-TO1-APTO-103'], concept: 'OTHER', amount: '1', due_date: seed.today, description: 'x' },
      headers: { Origin: E2E_ORIGIN }
    });
    expect(withoutToken.status()).toBe(403);
    expect(((await withoutToken.json()) as { error: { code: string } }).error.code).toBe('CSRF_FAILED');
    expect(withoutToken.headers()['cache-control']).toBe('no-store');
  });
});

/** The real login form, filled and sent. */
async function submit(page: Page, email: string, password: string): Promise<void> {
  await page.getByLabel('Correo electrónico').fill(email);
  await page.getByLabel('Contraseña').fill(password);
  await page.getByRole('button', { name: 'Ingresar' }).click();
}

/** RF-002 criterion 2 (administrator) and RF-007 criterion 2 (resident): wrong credentials on the real form. */
test.describe('invalid credentials', () => {
  const GENERIC = 'Correo electrónico o contraseña no válidos.';
  const UNKNOWN_EMAIL = 'no-registrado.e2e@example.com'; // not in the seed

  /** Not signed in: the generic alert, still on /login, no session cookie and the API sees no session. */
  async function expectRefused(page: Page): Promise<void> {
    await expect(page.getByRole('alert')).toHaveText(GENERIC);
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel('Contraseña')).toHaveValue('');
    await expect(page.getByRole('button', { name: 'Ingresar' })).toBeEnabled();
    expect((await page.context().cookies()).map((cookie) => cookie.name)).not.toContain('softph_session');
    expect((await page.request.get('/api/v1/auth/session')).status()).toBe(401);
  }

  const cases = [
    { title: 'RF-002: the administrator with a wrong password', role: 'admin', home: /\/admin$/ },
    { title: 'RF-007: the resident with a wrong password', role: 'resident', home: /\/resident$/ }
  ] as const;

  for (const { title, role, home } of cases) {
    test(`${title}: the generic alert, no session; then the right password signs in`, async ({ page, seed }) => {
      const email = role === 'admin' ? seed.admin_email : seed.resident_email;
      const password = role === 'admin' ? adminPassword() : residentPassword();
      await page.goto('/login');
      await submit(page, email, `${password}-incorrecta`);
      await expectRefused(page);

      await submit(page, email, password);
      await expect(page).toHaveURL(home);
    });
  }

  test('an email that is not registered: the same generic alert, no session; then a valid account signs in', async ({ page, seed }) => {
    await page.goto('/login');
    await submit(page, UNKNOWN_EMAIL, residentPassword());
    await expectRefused(page);

    await submit(page, seed.resident_email, residentPassword());
    await expect(page).toHaveURL(/\/resident$/);
  });
});

/**
 * A3 (RF-002, RF-007, RF-018): "Cerrar sesión" in the header ends the session on the server, not only in this
 * browser. The session cookie is copied before logging out into a separate browser context (another tab or a
 * stolen copy): it works there first, and after the logout the same cookie is refused by the pages and the API.
 */
test.describe('logout', () => {
  const COOKIE = 'softph_session';
  const cases = [
    // Protected APIs with NoStoreRoute: their 401 is never cached either.
    { role: 'administrator', home: /\/admin$/, page: '/admin/obligations', api: '/api/v1/admin/charges' },
    { role: 'resident', home: /\/resident$/, page: '/resident/payments', api: '/api/v1/resident/payments' }
  ] as const;

  for (const { role, home, page: protectedPage, api } of cases) {
    test(`the ${role} logs out from the header: back to the login, and the old session cookie no longer opens anything`, async ({ page, seed, browser }) => {
      const [email, password, expectedRole] = role === 'administrator'
        ? [seed.admin_email, adminPassword(), 'ADMINISTRADOR']
        : [seed.resident_email, residentPassword(), 'RESIDENTE'];

      // 1. The real login form, to the role's home page.
      await page.goto('/login');
      await submit(page, email, password);
      await expect(page).toHaveURL(home);
      const session = (await page.context().cookies()).find((cookie) => cookie.name === COOKIE);
      expect(session, 'the session cookie after signing in').toBeTruthy();

      // The copied cookie is a working session elsewhere (the control for step 4).
      const copy = await browser.newContext({ baseURL: E2E_ORIGIN });
      try {
        await copy.addCookies([session!]);
        const active = await copy.request.get('/api/v1/auth/session');
        expect(active.status()).toBe(200);
        expect(((await active.json()) as { user: { role: string } }).user.role).toBe(expectedRole);
        expect((await copy.request.get(api)).status()).toBe(200);

        // 2. The header's "Cerrar sesión": the server answers the logout, then the login page.
        const [logout] = await Promise.all([
          page.waitForResponse((answer) => new URL(answer.url()).pathname === '/api/v1/auth/logout'),
          page.getByRole('button', { name: 'Cerrar sesión' }).click() // the header's control (admin-dashboard.spec.ts)
        ]);
        expect(logout.request().method()).toBe('POST');
        expect(logout.status()).toBe(204);
        await expect(page).toHaveURL(/\/login$/);
        await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
        expect((await page.context().cookies()).map((cookie) => cookie.name)).not.toContain(COOKIE);

        // 3. This browser: no session for the API; a protected page goes to the login.
        expect((await page.request.get('/api/v1/auth/session')).status()).toBe(401);
        await page.goto(protectedPage);
        await expect(page).toHaveURL(/\/login$/);

        // 4. The old cookie, still sent by the other context, was revoked on the server.
        expect((await copy.cookies()).find((cookie) => cookie.name === COOKIE)?.value).toBe(session!.value);
        expect((await copy.request.get('/api/v1/auth/session')).status()).toBe(401);
        const refused = await copy.request.get(api);
        expect(refused.status()).toBe(401);
        expect(refused.headers()['cache-control']).toBe('no-store');
        const other = await copy.newPage();
        await other.goto(protectedPage);
        await expect(other).toHaveURL(/\/login$/);
        await expect(other.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
      } finally {
        await copy.close();
      }
    });
  }
});
