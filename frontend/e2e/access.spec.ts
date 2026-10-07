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
