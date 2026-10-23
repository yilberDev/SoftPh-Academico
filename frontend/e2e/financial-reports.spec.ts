import type { Locator, Page, Request } from '@playwright/test';
import { addDays, periodOf, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-012 "Generación de reportes financieros", in a real browser as the administrator, against the real API
 * on the *_test database (playwright.config.ts).
 *
 * Seeded data (backend/tests/e2e/seed.py, T = Bogotá today): two manual payments in cash dated T — Jorge León
 * 80.000 (applied to the obligation of B2-TO1-APTO-204) and Marta Díaz 50.000 (nothing to cover: saldo a favor)
 * — and an unpaid penalty of 30.000 in B2-TO1-APTO-206 (due T+10, no resident). Some tests add, in Laura's
 * B1-TO1-APTO-101, a monthly fee of last month (100.000, en mora) and a transfer of 30.000 that pays part of it.
 * Every expected amount follows from those numbers.
 */

async function lauraOwesAndPays(admin: Api, seed: Seed): Promise<{ receipt_number: string }> {
  const period = periodOf(seed.today, -1);
  await createCharge(admin, { unit_id: seed.units['B1-TO1-APTO-101'], concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
  return admin.json(
    await admin.post('/admin/payments', { resident_id: seed.laura_id, amount: '30000', method: 'TRANSFER', concept: 'MONTHLY_FEE', payment_date: seed.today }),
    201
  );
}

const text = async (locator: Locator) => normalized(await locator.textContent());
const total = (page: Page, key: string) => page.locator(`[data-total="${key}"]`);
const rows = (page: Page) => page.locator('[data-rows] tr');
const cell = (row: Locator, label: string) => row.locator(`td[data-label="${label}"]`);

/** The report requests the page sends (none while the criteria are incomplete). */
function reportRequests(page: Page): Request[] {
  const sent: Request[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/admin/reports/')) sent.push(request);
  });
  return sent;
}

async function openPaymentsReport(page: Page): Promise<void> {
  await page.goto('/admin');
  await page.getByRole('main').getByRole('link', { name: /Reportes financieros/ }).click();
  await expect(page).toHaveURL(/\/admin\/reports$/);
  await page.getByRole('main').getByRole('link', { name: /Pagos registrados/ }).click();
  await expect(page).toHaveURL(/\/admin\/reports\/payments$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Reporte de pagos registrados' })).toBeVisible();
}

async function generate(page: Page, from: string, to: string): Promise<void> {
  await page.getByLabel('Fecha inicial').fill(from);
  await page.getByLabel('Fecha final').fill(to);
  await page.getByRole('button', { name: 'Generar reporte' }).click();
}

test.describe('payments report', () => {
  test('from the menu, a valid range: every payment identified, its status, the totals and the totals by method', async ({ page, seed, admin }) => {
    const laura = await lauraOwesAndPays(admin.api, seed);
    await openPaymentsReport(page);
    await generate(page, seed.today, seed.today);
    await expect(page).toHaveURL(new RegExp(`\\?date_from=${seed.today}&date_to=${seed.today}$`));
    await expect(page.locator('[data-result]')).toBeVisible();

    await expect(total(page, 'payments')).toHaveText('3');
    expect(await text(total(page, 'total_amount'))).toBe('$ 160.000,00');
    expect(await text(total(page, 'total_applied'))).toBe('$ 110.000,00');
    expect(await text(total(page, 'total_credit'))).toBe('$ 50.000,00');
    const methods = await page.locator('[data-by-method] li').allTextContents();
    expect(methods.map(normalized)).toEqual(['Efectivo: 2 · $ 130.000,00', 'Transferencia: 1 · $ 30.000,00']);

    // Oldest first (same date: registration order): Jorge, Marta, Laura.
    await expect(rows(page)).toHaveCount(3);
    const expected = [
      { who: 'Jorge León', unit: 'B2-TO1-APTO-204', amount: '$ 80.000,00', applied: '$ 80.000,00', credit: '$ 0,00', status: 'Aplicado' },
      { who: 'Marta Díaz', unit: 'B2-TO1-APTO-205', amount: '$ 50.000,00', applied: '$ 0,00', credit: '$ 50.000,00', status: 'Saldo a favor' },
      { who: 'Laura Gómez', unit: 'B1-TO1-APTO-101', amount: '$ 30.000,00', applied: '$ 30.000,00', credit: '$ 0,00', status: 'Aplicado' }
    ];
    for (const [index, item] of expected.entries()) {
      const row = rows(page).nth(index);
      expect(await text(cell(row, 'Residente y unidad'))).toBe(`${item.who}${item.unit}`);
      expect(await text(cell(row, 'Valor'))).toBe(item.amount);
      expect(await text(cell(row, 'Aplicado'))).toBe(item.applied);
      expect(await text(cell(row, 'Saldo a favor'))).toBe(item.credit);
      await expect(cell(row, 'Estado')).toHaveText(item.status);
      await expect(cell(row, 'Recibo')).toHaveText(/^RC-\d{8}$/);
    }
    await expect(cell(rows(page).nth(2), 'Recibo')).toHaveText(laura.receipt_number);

    // The same criteria come back from the URL (a reload).
    await page.reload();
    await expect(total(page, 'payments')).toHaveText('3');
  });

  test('a range without payments says so; missing dates and a reversed range are refused next to their fields', async ({ page, seed, admin }) => {
    await openPaymentsReport(page);
    const sent = reportRequests(page);
    await page.getByRole('button', { name: 'Generar reporte' }).click();
    await expect(page.locator('[data-error-for="date_from"]')).toHaveText('Indica la fecha inicial del reporte.');
    await expect(page.locator('[data-error-for="date_to"]')).toHaveText('Indica la fecha final del reporte.');
    await expect(page.getByLabel('Fecha inicial')).toHaveAttribute('aria-invalid', 'true');
    await generate(page, seed.today, addDays(seed.today, -1));
    await expect(page.locator('[data-error-for="date_to"]')).toHaveText('La fecha inicial no puede ser posterior a la fecha final.');
    await expect(page.locator('[data-error-for="date_from"]')).toBeHidden();
    await expect(page.locator('[data-result]')).toBeHidden();
    expect(sent).toHaveLength(0); // nothing is asked with incomplete criteria

    // The server refuses the same criteria on its own.
    const reversed = await admin.api.get(`/admin/reports/payments?date_from=${seed.today}&date_to=${addDays(seed.today, -1)}`);
    expect(reversed.status()).toBe(422);
    expect((await admin.api.get('/admin/reports/payments')).status()).toBe(422);

    await generate(page, addDays(seed.today, -40), addDays(seed.today, -30));
    await expect(page.locator('[data-empty]')).toHaveText('No existen pagos registrados para los criterios seleccionados.');
    await expect(total(page, 'payments')).toHaveText('0');
    await expect(page.locator('[data-list]')).toBeHidden();
  });
});

test.describe('pending balances report', () => {
  test('all the apartments that owe or only those en mora, with their totals; each resident opens their statement', async ({ page, seed, admin }) => {
    await lauraOwesAndPays(admin.api, seed);
    await page.goto('/admin/reports');
    await page.getByRole('main').getByRole('link', { name: /Saldos pendientes/ }).click();
    await expect(page).toHaveURL(/\/admin\/reports\/pending-balances$/);
    await expect(page.locator('[data-result]')).toBeVisible();

    // ALL: Laura's apartment (70.000 en mora) and the seeded penalty of B2-TO1-APTO-206 (30.000, within its date).
    await expect(rows(page)).toHaveCount(2);
    await expect(total(page, 'units')).toHaveText('2');
    await expect(total(page, 'overdue_units')).toHaveText('1');
    expect(await text(total(page, 'total_pending'))).toBe('$ 100.000,00');
    expect(await text(total(page, 'total_overdue'))).toBe('$ 70.000,00');
    const laura = rows(page).filter({ hasText: 'B1-TO1-APTO-101' });
    expect(await text(cell(laura, 'Pendiente'))).toBe('$ 70.000,00');
    expect(await text(cell(laura, 'En mora'))).toBe('$ 70.000,00');
    await expect(cell(laura, 'Estado')).toHaveText('En mora');
    const empty = rows(page).filter({ hasText: 'B2-TO1-APTO-206' });
    expect(await text(cell(empty, 'Pendiente'))).toBe('$ 30.000,00');
    await expect(cell(empty, 'Estado')).toHaveText('Pendiente');
    await expect(cell(empty, 'Residente')).toHaveText('Sin residente vigente');
    await expect(cell(empty, 'Residente').getByRole('link')).toHaveCount(0);

    // OVERDUE: only Laura's apartment, kept in the URL.
    await page.getByLabel('Unidades', { exact: true }).selectOption('OVERDUE');
    await expect(page).toHaveURL(/\?scope=OVERDUE$/);
    await expect(rows(page)).toHaveCount(1);
    await expect(total(page, 'units')).toHaveText('1');
    expect(await text(total(page, 'total_pending'))).toBe('$ 70.000,00');
    await page.getByRole('link', { name: 'Estado de cuenta de Laura Gómez' }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/residents/${seed.laura_id}/account-statement$`));
    await expect(page.locator('[data-account-badge]')).toHaveText('En mora');
  });

  test('nobody en mora: the message instead of an empty table', async ({ page, signIn }) => {
    // Seeded data only: the penalty of B2-TO1-APTO-206 is within its due date.
    await signIn('admin');
    await page.goto('/admin/reports/pending-balances?scope=OVERDUE');
    await expect(page.locator('[data-empty]')).toHaveText('Ninguna unidad residencial está en mora.');
    await expect(page.locator('[data-list]')).toBeHidden();
    await expect(total(page, 'units')).toHaveText('0');
    await page.getByLabel('Unidades', { exact: true }).selectOption('ALL');
    await expect(rows(page)).toHaveCount(1);
  });

  test('a resident gets 403 on the reports pages and API', async ({ page, signIn }) => {
    const resident = await signIn('resident');
    for (const path of ['/admin/reports', '/admin/reports/payments', '/admin/reports/pending-balances']) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(403);
      await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
    }
    for (const path of ['/admin/reports/pending-balances', '/admin/reports/payments?date_from=2026-01-01&date_to=2026-12-31']) {
      const response = await resident.get(path);
      expect(response.status(), path).toBe(403);
      expect((await response.json()).error.code).toBe('FORBIDDEN');
    }
  });
});
