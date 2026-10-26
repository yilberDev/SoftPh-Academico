import type { Page } from '@playwright/test';
import { periodOf, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * The resident's home page (/resident), in a real browser against the real API on the *_test database:
 * the welcome with the resident's own apartment, the summary (the same the API gives, never invented), the
 * modules with their real pages, the recent activity (the resident's notifications), the header and its
 * menu on narrow screens, 360 and 320 px without horizontal scroll, and the administrator refused.
 *
 * Screenshots only when SHOTS_DIR is set (outside the repository), e.g.:
 *   SHOTS_DIR=/tmp/shots npx playwright test e2e/resident-dashboard.spec.ts
 */

const MODULE_LINKS: [string, string][] = [
  ['Estado de cuenta', '/resident/account-statement'],
  ['Mis pagos', '/resident/payments'],
  ['Pago de demostración', '/resident/demo-payments'],
  ['Enviar feedback', '/resident/feedback/new'],
  ['Mis feedback', '/resident/feedback'],
  ['Notificaciones', '/resident/notifications']
];
const NAV = ['Inicio', 'Estado de cuenta', 'Mis pagos', 'Pago de demostración', 'Mis feedback', 'Notificaciones'];
const STATUS_TEXT = { UP_TO_DATE: 'Al día', PENDING: 'Pendiente', OVERDUE: 'En mora' } as const;

interface Statement {
  resident: { full_name: string; unit: { label: string } };
  summary: { status: keyof typeof STATUS_TEXT; pending_balance: string; pending_obligations: number };
}

const tileValue = (page: Page, key: string) => page.locator(`[data-summary-tile="${key}"] [data-summary-value]`);
const digits = (text: string | null) => normalized(text).replace(/\D/g, '');

/** Errors of the page (console or uncaught), checked at the end of a test. */
function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

/** Laura owes last month's fee (en mora) and has a payment registered (a notification). */
async function lauraOwes(admin: Api, seed: Seed): Promise<void> {
  const period = periodOf(seed.today, -1);
  await createCharge(admin, { unit_id: seed.units['B1-TO1-APTO-101'], concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
  await admin.json(await admin.post('/admin/payments', { resident_id: seed.laura_id, amount: '30000', method: 'TRANSFER', concept: 'MONTHLY_FEE', payment_date: seed.today }), 201);
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.SHOTS_DIR;
  if (!dir) return;
  // The development toolbar of Astro (the E2E stack runs astro dev) is not part of the page.
  await page.addStyleTag({ content: 'astro-dev-toolbar { display: none !important; }' });
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test.describe('resident home', () => {
  test('welcome, summary and activity: what the API gives for the resident, never invented', async ({ admin: { page, api }, seed, signIn }) => {
    await lauraOwes(api, seed);
    const resident = await signIn('resident');
    const statement = await resident.json<Statement>(await resident.get('/resident/account-statement'));
    const feedback = await resident.json<{ items: { status: string }[]; total: number }>(await resident.get('/resident/feedback?page_size=100'));
    const notifications = await resident.json<{ items: { title: string; entity_type: string; entity_id: number }[] }>(
      await resident.get('/resident/notifications?page_size=5'));
    expect(statement.summary.status).toBe('OVERDUE');
    expect(notifications.items.length).toBeGreaterThanOrEqual(1);

    await page.goto('/resident');
    await expect(page).toHaveTitle('Panel del residente — SoftPh-Académico');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(`Hola, ${statement.resident.full_name}`);
    await expect(page.locator('[data-unit]')).toContainText(statement.resident.unit.label);
    await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Resumen', 'Accesos rápidos', 'Actividad reciente']);

    expect(digits(await tileValue(page, 'balance').textContent())).toBe(statement.summary.pending_balance.replace(/\D/g, ''));
    await expect(page.locator('[data-summary-tile="balance"] [data-summary-detail]')).toHaveText(
      `${statement.summary.pending_obligations} ${statement.summary.pending_obligations === 1 ? 'obligación' : 'obligaciones'} con saldo.`);
    await expect(tileValue(page, 'account-status')).toHaveText(STATUS_TEXT[statement.summary.status]);
    await expect(tileValue(page, 'pending-feedback')).toHaveText(String(feedback.items.filter((item) => item.status === 'PENDING').length));
    await expect(page.getByRole('main')).not.toContainText('No disponible');

    const items = page.locator('[data-activity-item]');
    await expect(items).toHaveCount(notifications.items.length);
    const first = notifications.items[0];
    await expect(items.first()).toContainText(first.title);
    await expect(items.first().getByRole('link')).toHaveAttribute('href',
      first.entity_type === 'payment' ? `/resident/payments/${first.entity_id}` : `/resident/feedback/${first.entity_id}`);
    await page.getByRole('link', { name: 'Ver todas las notificaciones' }).click();
    await expect(page).toHaveURL(/\/resident\/notifications$/);

    await page.goto('/resident');
    await page.locator('[data-summary-tile="account-status"]').click();
    await expect(page).toHaveURL(/\/resident\/account-statement$/);
  });

  test('the modules open their real pages; the header marks the page and has no menu button on a wide screen', async ({ page, signIn }) => {
    const errors = trackErrors(page);
    await signIn('resident');
    await page.goto('/resident');
    const main = page.getByRole('main');
    for (const [name, href] of MODULE_LINKS) {
      await expect(main.locator('[data-modules]').getByRole('link', { name, exact: true })).toHaveAttribute('href', href);
    }
    // One link per page in the panel's text (the links other tests open from here).
    await expect(main.getByRole('link', { name: /Estado de cuenta/ })).toHaveCount(1);
    await expect(main.getByRole('link', { name: /Notificaciones/ })).toHaveCount(1);

    const nav = page.getByRole('navigation', { name: 'Principal' });
    await expect(nav.getByRole('link')).toHaveText(NAV);
    await expect(nav.getByRole('link', { name: 'Inicio' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('button', { name: 'Abrir menú' })).toBeHidden();
    await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible();

    // The whole card opens its page (not only the title): a click near its bottom edge.
    const card = page.locator('[data-module]').filter({ hasText: 'Consulta los pagos registrados' });
    const box = (await card.boundingBox())!;
    await card.click({ position: { x: box.width / 2, y: box.height - 12 } });
    await expect(page).toHaveURL(/\/resident\/payments$/);
    await expect(nav.getByRole('link', { name: 'Mis pagos' })).toHaveAttribute('aria-current', 'page');
    expect(errors).toEqual([]);
  });

  test('screenshots (only with SHOTS_DIR)', async ({ admin: { page, api }, seed, signIn }) => {
    test.skip(!process.env.SHOTS_DIR, 'SHOTS_DIR is not set');
    await lauraOwes(api, seed);
    await signIn('resident');
    for (const width of [1440, 820, 360, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/resident');
      await shot(page, `resident-${width}`);
    }
    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await shot(page, 'resident-320-menu');
  });
});

for (const width of [360, 320]) {
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height: 800 }, isMobile: true, hasTouch: true });

    test('no horizontal scroll; the summary first, one column; the links in an accessible menu', async ({ page, signIn }) => {
      const errors = trackErrors(page);
      await signIn('resident');
      await page.goto('/resident');
      expect(await noHorizontalScroll(page)).toBe(true);
      for (const element of await page.locator('[data-summary-tile], [data-module], [data-activity]').all()) {
        const box = (await element.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      // The financial summary comes before the modules and the activity.
      const summary = (await page.locator('[data-summary]').boundingBox())!;
      expect(summary.y).toBeLessThan((await page.locator('[data-modules]').boundingBox())!.y);

      const menu = page.locator('#resident-menu');
      const openButton = page.getByRole('button', { name: 'Abrir menú' });
      await expect(openButton).toHaveAttribute('aria-expanded', 'false');
      await expect(menu).toBeHidden();
      await expect(page.getByRole('navigation', { name: 'Principal' })).toHaveCount(0);

      await openButton.click();
      await expect(page.getByRole('button', { name: 'Cerrar menú' })).toHaveAttribute('aria-expanded', 'true');
      const nav = page.getByRole('navigation', { name: 'Principal' });
      await expect(nav.getByRole('link')).toHaveText(NAV);
      await expect(nav.locator('[aria-current="page"]')).toHaveText('Inicio');
      for (const control of [...(await nav.getByRole('link').all()), nav.getByRole('button', { name: 'Cerrar sesión' })]) {
        const box = (await control.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      expect(await noHorizontalScroll(page)).toBe(true);

      await page.keyboard.press('Escape');
      await expect(menu).toBeHidden();
      await expect(openButton).toBeFocused();

      await openButton.click();
      await nav.getByRole('link', { name: 'Notificaciones' }).click();
      await expect(page).toHaveURL(/\/resident\/notifications$/);
      await expect(menu).toBeHidden();
      await openButton.click();
      await expect(nav.locator('[aria-current="page"]')).toHaveText('Notificaciones');
      expect(errors).toEqual([]);
    });
  });
}

test('the administrator gets 403 on /resident, without the panel', async ({ admin: { page } }) => {
  const response = await page.goto('/resident');
  expect(response?.status()).toBe(403);
  const html = await page.content();
  expect(html).not.toContain('Accesos rápidos');
  expect(html).not.toContain('data-summary');
});
