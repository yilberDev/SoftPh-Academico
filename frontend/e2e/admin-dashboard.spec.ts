import type { Page } from '@playwright/test';
import { periodOf } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * The administrator's home page (/admin), in a real browser against the real API on the *_test database:
 * the welcome, the eight modules with their real pages, the summary numbers (the same the API gives for the
 * seeded data, never invented), the shared header (a menu at 360 px), 360 px without horizontal scroll, and the resident refused.
 *
 * Screenshots only when SHOTS_DIR is set (outside the repository), e.g.:
 *   SHOTS_DIR=/tmp/shots npx playwright test e2e/admin-dashboard.spec.ts
 */

const MODULES: [string, string][] = [
  ['Unidades residenciales', '/admin/blocks'],
  ['Residentes', '/admin/residents'],
  ['Invitar residente', '/admin/residents/invite'],
  ['Obligaciones', '/admin/obligations'],
  ['Pagos', '/admin/payments'],
  ['Feedback', '/admin/feedback'],
  ['Estados de cuenta', '/admin/residents'],
  ['Reportes financieros', '/admin/reports']
];

interface Summary {
  residents: number;
  pendingFeedback: number;
  units: number;
  overdueUnits: number;
}

/** The numbers straight from the API, as the page must show them. */
async function apiSummary(api: Api): Promise<Summary> {
  const residents = await api.json<{ total: number }>(await api.get('/admin/residents?page_size=1'));
  const feedback = await api.json<{ total: number }>(await api.get('/admin/feedback?status=PENDING&page_size=1'));
  const balances = await api.json<{ units: number; overdue_units: number }>(await api.get('/admin/reports/pending-balances'));
  return { residents: residents.total, pendingFeedback: feedback.total, units: balances.units, overdueUnits: balances.overdue_units };
}

const tileValue = (page: Page, key: string) => page.locator(`[data-summary-tile="${key}"] [data-summary-value]`);

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.SHOTS_DIR;
  if (!dir) return;
  // The development toolbar of Astro (the E2E stack runs astro dev) is not part of the page.
  await page.addStyleTag({ content: 'astro-dev-toolbar { display: none !important; }' });
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test.describe('administrator home', () => {
  test('welcome, eight modules with their real pages, and the shared header', async ({ admin: { page } }) => {
    await page.goto('/admin');
    await expect(page).toHaveTitle('Panel principal — SoftPh-Académico');
    await expect(page.getByRole('heading', { level: 1, name: 'Hola, Administradora Pruebas' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Resumen', 'Módulos de gestión']);

    const modules = page.getByRole('main').getByRole('list').filter({ has: page.getByRole('heading', { level: 3 }) });
    await expect(modules.getByRole('heading', { level: 3 })).toHaveText(MODULES.map(([title]) => title));
    for (const [title, href] of MODULES) {
      await expect(modules.getByRole('link', { name: title, exact: true })).toHaveAttribute('href', href);
    }
    // One link per module, named by its title (the links other tests open from here).
    await expect(page.getByRole('main').getByRole('link', { name: /Obligaciones/ })).toHaveCount(1);
    await expect(page.getByRole('main').getByRole('link', { name: /Reportes financieros/ })).toHaveCount(1);

    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link')).toHaveText(['Inicio', 'Unidades residenciales', 'Residentes', 'Invitar residente', 'Obligaciones', 'Pagos', 'Feedback']);
    await expect(menu.getByRole('link', { name: 'Inicio' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible();
    // On a wide screen the links stay in the header: no menu button.
    await expect(page.getByRole('button', { name: 'Abrir menú' })).toBeHidden();

    // The whole card opens its module (not only the title): a click near its bottom edge.
    const payments = page.locator('[data-module]').filter({ hasText: 'Registra los pagos' });
    const card = (await payments.boundingBox())!;
    await payments.click({ position: { x: card.width / 2, y: card.height - 12 } });
    await expect(page).toHaveURL(/\/admin\/payments$/);
    await expect(menu.getByRole('link', { name: 'Pagos' })).toHaveAttribute('aria-current', 'page');
    await expect(menu.getByRole('link', { name: 'Inicio' })).not.toHaveAttribute('aria-current', 'page');
  });

  test('the summary shows what the API gives for the seeded data and opens its detail', async ({ admin: { page, api }, seed }) => {
    // A monthly fee of last month in Laura's apartment: one more unit with balance, and en mora.
    const before = await apiSummary(api);
    const period = periodOf(seed.today, -1);
    await createCharge(api, { unit_id: seed.units['B1-TO1-APTO-101'], concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
    const expected = await apiSummary(api);
    expect(expected.overdueUnits).toBe(before.overdueUnits + 1);
    expect(expected.residents).toBeGreaterThanOrEqual(2);
    expect(expected.pendingFeedback).toBeGreaterThanOrEqual(1);

    await page.goto('/admin');
    await expect(tileValue(page, 'residents')).toHaveText(String(expected.residents));
    await expect(tileValue(page, 'pending-feedback')).toHaveText(String(expected.pendingFeedback));
    await expect(tileValue(page, 'units-pending')).toHaveText(String(expected.units));
    await expect(tileValue(page, 'units-overdue')).toHaveText(String(expected.overdueUnits));
    const overdueDetail = normalized(await page.locator('[data-summary-tile="units-overdue"] [data-summary-detail]').textContent());
    expect(overdueDetail).toMatch(/^Total en mora: \$ [\d.]+,\d{2}$/);
    await expect(page.getByRole('main')).not.toContainText('No disponible');

    await page.locator('[data-summary-tile="units-overdue"]').click();
    await expect(page).toHaveURL(/\/admin\/reports\/pending-balances\?scope=OVERDUE$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Saldos pendientes' })).toBeVisible();

    await page.goto('/admin');
    await page.locator('[data-summary-tile="pending-feedback"]').click();
    await expect(page).toHaveURL(/\/admin\/feedback\?status=PENDING$/);
  });

  test('360 px: no horizontal scroll, one column, the links in the menu and 44 px touch areas', async ({ admin: { page } }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.goto('/admin');
    const { scrollWidth, width } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      width: document.documentElement.clientWidth
    }));
    expect(scrollWidth).toBeLessThanOrEqual(width);

    const cards = page.locator('[data-module]');
    await expect(cards).toHaveCount(8);
    for (const card of await cards.all()) {
      const box = (await card.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(360);
      expect(box.width).toBeGreaterThan(360 * 0.8);
    }
    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await expect(page.getByRole('navigation', { name: 'Principal' }).getByRole('link')).toHaveCount(7);
    for (const link of await page.getByRole('navigation', { name: 'Principal' }).getByRole('link').all()) {
      const box = (await link.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x + box.width).toBeLessThanOrEqual(360);
    }
    const logout = (await page.getByRole('button', { name: 'Cerrar sesión' }).boundingBox())!;
    expect(logout.height).toBeGreaterThanOrEqual(44);
    expect(logout.x + logout.width).toBeLessThanOrEqual(360);
  });

  test('screenshots (only with SHOTS_DIR)', async ({ admin: { page } }) => {
    test.skip(!process.env.SHOTS_DIR, 'SHOTS_DIR is not set');
    for (const width of [1440, 1024, 820, 360]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/admin');
      await shot(page, `admin-${width}`);
    }
    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await shot(page, 'admin-360-menu');
  });
});

test('the resident gets 403 on /admin, without the panel', async ({ page, signIn }) => {
  await signIn('resident');
  const response = await page.goto('/admin');
  expect(response?.status()).toBe(403);
  await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
  const html = await page.content();
  expect(html).not.toContain('Módulos de gestión');
  expect(html).not.toContain('data-summary');
});
