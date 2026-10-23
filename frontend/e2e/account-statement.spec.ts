import type { Locator, Page } from '@playwright/test';
import { addDays, periodOf, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-011 (administrator) and HU-014 (resident) "Estado de cuenta", in a real browser, against the real API
 * on the *_test database (playwright.config.ts). Both use the same statement (AccountStatement.astro).
 *
 * Scenario prepared by the administrator through the API, in apartment B1-TO1-APTO-101 of Laura Gómez
 * (T = Bogotá today): a monthly fee of last month (100.000, due day 5: en mora) and a penalty "Ruido E2E"
 * (50.000, due T+10), then a manual payment of 30.000 by transfer, which HU-004 applies FIFO to the fee.
 * Expected, from those amounts only: pending 120.000 (70.000 en mora + 50.000), applied 30.000, no credit.
 */

interface RegisteredPayment {
  id: number;
  receipt_number: string;
}

async function lauraOwes(admin: Api, seed: Seed): Promise<RegisteredPayment> {
  const unit = seed.units['B1-TO1-APTO-101'];
  const period = periodOf(seed.today, -1);
  await createCharge(admin, { unit_id: unit, concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
  await createCharge(admin, { unit_id: unit, concept: 'PENALTY', amount: '50000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });
  return pay(admin, seed.laura_id, seed.today, '30000');
}

/** A manual payment (HU-004). The API helper uses the page's session: call it while signed in as the administrator. */
async function pay(admin: Api, residentId: number, date: string, amount: string): Promise<RegisteredPayment> {
  return admin.json<RegisteredPayment>(
    await admin.post('/admin/payments', { resident_id: residentId, amount, method: 'TRANSFER', concept: 'MONTHLY_FEE', payment_date: date }),
    201
  );
}

async function residentId(admin: Api, name: string): Promise<number> {
  const page = await admin.json<{ items: { id: number; full_name: string }[] }>(await admin.get(`/admin/residents?search=${encodeURIComponent(name)}`));
  const [match] = page.items.filter((item) => item.full_name === name);
  return match.id;
}

const statement = (page: Page) => page.locator('[data-account-statement]');
const section = (page: Page, key: 'overdue' | 'pending' | 'paid') => page.locator(`[data-section="${key}"]`);
const rowsOf = (scope: Locator) => scope.locator('[data-rows] tr');
const paymentRows = (page: Page) => page.locator('[data-payment-rows] tr');
const cell = (row: Locator, label: string) => row.locator(`td[data-label="${label}"]`);
const text = async (locator: Locator) => normalized(await locator.textContent());

async function ready(page: Page): Promise<void> {
  await expect(statement(page).locator('[data-content]')).toBeVisible();
  await expect(statement(page).locator('[data-status-line]')).toHaveText('');
}

async function expectTotals(page: Page, expected: Record<string, string>): Promise<void> {
  for (const [key, value] of Object.entries(expected)) {
    expect(await text(page.locator(`[data-total="${key}"]`)), key).toBe(value);
  }
}

/** The statement prepared by lauraOwes, as both roles see it (the obligations and the totals are the unit's). */
async function expectLaurasStatement(page: Page, payment: RegisteredPayment): Promise<void> {
  await ready(page);
  await expect(page.locator('[data-resident-name]')).toHaveText('Laura Gómez');
  await expect(page.locator('[data-unit]')).toHaveText('B1-TO1-APTO-101');
  await expect(page.locator('[data-account-badge]')).toHaveText('En mora');
  await expectTotals(page, {
    pending_balance: '$ 120.000,00', overdue_balance: '$ 70.000,00', credit_balance: '$ 0,00',
    total_charged: '$ 150.000,00', total_applied: '$ 30.000,00'
  });
  await expect(page.locator('[data-count]')).toHaveText('2 de 2');

  const [overdue] = await rowsOf(section(page, 'overdue')).all();
  await expect(rowsOf(section(page, 'overdue'))).toHaveCount(1);
  expect(await text(cell(overdue, 'Obligación'))).toMatch(/^Mensualidad · /);
  expect(await text(cell(overdue, 'Valor'))).toBe('$ 100.000,00');
  expect(await text(cell(overdue, 'Abonado'))).toBe('$ 30.000,00');
  expect(await text(cell(overdue, 'Saldo'))).toBe('$ 70.000,00');
  await expect(cell(overdue, 'Estado')).toHaveText('Vencida');

  await expect(rowsOf(section(page, 'pending'))).toHaveCount(1);
  const pending = rowsOf(section(page, 'pending')).first();
  await expect(cell(pending, 'Obligación')).toHaveText('Penalización: Ruido E2E');
  expect(await text(cell(pending, 'Saldo'))).toBe('$ 50.000,00');
  await expect(cell(pending, 'Estado')).toHaveText('Pendiente');
  await expect(section(page, 'paid').locator('[data-section-empty]')).toHaveText('Aún no hay obligaciones pagadas.');

  await expect(paymentRows(page)).toHaveCount(1);
  const row = paymentRows(page).first();
  await expect(cell(row, 'Recibo')).toHaveText(payment.receipt_number);
  expect(await text(cell(row, 'Valor'))).toBe('$ 30.000,00');
  expect(await text(cell(row, 'Saldo a favor'))).toBe('$ 0,00');
  await expect(cell(row, 'Estado')).toHaveText('Aplicado');
  expect(await text(cell(row, 'Concepto y aplicación'))).toMatch(/Transferencia.*Aplicado a Mensualidad · .*: \$ 30\.000,00/);
}

test.describe('HU-011: the administrator', () => {
  test('from the residents list to the statement: obligations, payments, balances and status as recorded', async ({ page, seed, admin }) => {
    const payment = await lauraOwes(admin.api, seed);
    await page.goto('/admin/residents');
    await page.getByRole('link', { name: 'Estado de cuenta de Laura Gómez' }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/residents/${seed.laura_id}/account-statement$`));
    await expectLaurasStatement(page, payment);
    // The administrator sees who made each payment.
    expect(await text(cell(paymentRows(page).first(), 'Concepto y aplicación'))).toContain('Pagó: Este residente');
    // From the resident's detail too.
    await page.goto(`/admin/residents/${seed.laura_id}`);
    await page.getByRole('link', { name: 'Estado de cuenta', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/residents/${seed.laura_id}/account-statement$`));
    await ready(page);
  });

  test('a new payment is reflected when the statement is read again (criterion 4)', async ({ page, seed, admin }) => {
    await lauraOwes(admin.api, seed);
    await page.goto(`/admin/residents/${seed.laura_id}/account-statement`);
    await ready(page);
    await pay(admin.api, seed.laura_id, seed.today, '70000'); // covers the rest of the fee
    await page.getByRole('button', { name: 'Actualizar' }).click();
    await ready(page);
    await expect(page.locator('[data-account-badge]')).toHaveText('Pendiente');
    await expectTotals(page, { pending_balance: '$ 50.000,00', overdue_balance: '$ 0,00', total_applied: '$ 100.000,00' });
    await expect(rowsOf(section(page, 'paid'))).toHaveCount(1);
    await expect(paymentRows(page)).toHaveCount(2);
  });

  test('the statement leads to every resident that owes, and the report back to each statement (criterion 3.2)', async ({ page, seed, admin }) => {
    await lauraOwes(admin.api, seed);
    await page.goto(`/admin/residents/${seed.laura_id}/account-statement`);
    await ready(page);
    await page.getByRole('link', { name: 'Ver todos los residentes con obligaciones pendientes' }).click();
    await expect(page).toHaveURL(/\/admin\/reports\/pending-balances$/);
    const rows = page.locator('[data-rows] tr');
    await expect(rows).toHaveCount(2); // Laura's apartment and the seeded penalty of B2-TO1-APTO-206
    const laura = rows.filter({ hasText: 'B1-TO1-APTO-101' });
    await expect(laura.getByRole('link', { name: 'Estado de cuenta de Laura Gómez' })).toHaveAttribute(
      'href', `/admin/residents/${seed.laura_id}/account-statement`);
    await expect(rows.filter({ hasText: 'B2-TO1-APTO-206' })).toContainText('Sin residente vigente');
    await laura.getByRole('link', { name: 'Estado de cuenta de Laura Gómez' }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/residents/${seed.laura_id}/account-statement$`));
    await ready(page);
    await expect(page.locator('[data-account-badge]')).toHaveText('En mora');
  });

  test('a resident who is not registered is "not available"; a resident gets 403 on the page and the API', async ({ page, seed, signIn }) => {
    await signIn('admin');
    await page.goto('/admin/residents/999999/account-statement');
    await expect(statement(page).locator('[data-alert-message]')).toHaveText(
      'El residente seleccionado no está disponible: no existe o ya no está registrado.');
    await expect(statement(page).locator('[data-content]')).toBeHidden();

    const resident = await signIn('resident');
    const response = await page.goto(`/admin/residents/${seed.laura_id}/account-statement`);
    expect(response?.status()).toBe(403);
    await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
    const api = await resident.get(`/admin/residents/${seed.laura_id}/account-statement`);
    expect(api.status()).toBe(403);
    expect((await api.json()).error.code).toBe('FORBIDDEN');
  });
});

test.describe('HU-014: the resident', () => {
  test('from the panel and the menu to the statement of the own apartment, the same as the administrator\'s', async ({ page, seed, admin, signIn }) => {
    const payment = await lauraOwes(admin.api, seed);
    await signIn('resident');
    await page.goto('/resident');
    await page.getByRole('main').getByRole('link', { name: /Estado de cuenta/ }).click();
    await expect(page).toHaveURL(/\/resident\/account-statement$/);
    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link', { name: 'Estado de cuenta' })).toHaveAttribute('aria-current', 'page');
    await expectLaurasStatement(page, payment);
    // The resident sees their own payments, without the administrator's "who paid" line.
    await expect(page.locator('[data-payments-note]')).toHaveText(
      'Se muestran tus pagos. Los abonos de cada obligación incluyen todos los pagos aplicados a tu unidad residencial.');
    expect(await text(cell(paymentRows(page).first(), 'Concepto y aplicación'))).not.toContain('Pagó:');

    await page.goto('/resident/payments');
    await menu.getByRole('link', { name: 'Estado de cuenta' }).click();
    await expect(page).toHaveURL(/\/resident\/account-statement$/);
  });

  test('only a payment and no obligations: up to date with the credit; nothing yet: the empty message', async ({ page, seed, signIn }) => {
    await signIn('resident');
    await page.goto('/resident/account-statement');
    await ready(page);
    await expect(page.locator('[data-empty]')).toHaveText('No existen obligaciones ni movimientos registrados para esta unidad residencial.');
    await expect(page.locator('[data-account-badge]')).toHaveText('Al día');
    await expect(page.locator('[data-movements]')).toBeHidden();

    await pay(await signIn('admin'), seed.laura_id, seed.today, '50000');
    await signIn('resident');
    await page.reload();
    await ready(page);
    await expect(page.locator('[data-account-badge]')).toHaveText('Al día');
    await expectTotals(page, { pending_balance: '$ 0,00', credit_balance: '$ 50.000,00', total_applied: '$ 0,00' });
    await expect(paymentRows(page)).toHaveCount(1);
    await expect(cell(paymentRows(page).first(), 'Estado')).toHaveText('Saldo a favor');
  });

  test('another resident\'s account is never reachable: the session decides, not a parameter or an id', async ({ page, seed, admin, signIn }) => {
    await lauraOwes(admin.api, seed);
    // Pedro (B1-TO1-APTO-102) has an obligation and a payment of his own.
    const pedro = await residentId(admin.api, 'Pedro Ruiz');
    await createCharge(admin.api, { unit_id: seed.units['B1-TO1-APTO-102'], concept: 'PENALTY', amount: '45000', due_date: addDays(seed.today, 7), description: 'Parqueo Pedro E2E' });
    const pedros = await pay(admin.api, pedro, seed.today, '45000');

    const laura = await signIn('resident');
    for (const query of [`resident_id=${pedro}`, `user_id=${pedro}`, `unit_id=${seed.units['B1-TO1-APTO-102']}`]) {
      const response = await laura.get(`/resident/account-statement?${query}`);
      expect(response.status(), query).toBe(422);
      expect(await response.text()).not.toContain('Pedro');
    }
    const forbidden = await laura.get(`/admin/residents/${pedro}/account-statement`);
    expect(forbidden.status()).toBe(403);
    const own = await laura.json<{ resident: { id: number }; payments: { id: number }[] }>(await laura.get('/resident/account-statement'));
    expect(own.resident.id).toBe(seed.laura_id);
    expect(own.payments.map((p) => p.id)).not.toContain(pedros.id);

    // The page ignores anything in its URL: still Laura's apartment, nothing of Pedro's.
    await page.goto(`/resident/account-statement?resident_id=${pedro}`);
    await ready(page);
    await expect(page.locator('[data-unit]')).toHaveText('B1-TO1-APTO-101');
    const html = await page.locator('main').textContent();
    for (const secret of ['Pedro', 'Parqueo Pedro E2E', pedros.receipt_number]) expect(html).not.toContain(secret);
  });

  test('the administrator gets 403 on the resident\'s statement page and API', async ({ page, admin }) => {
    const response = await page.goto('/resident/account-statement');
    expect(response?.status()).toBe(403);
    await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
    expect((await admin.api.get('/resident/account-statement')).status()).toBe(403);
  });
});
