import type { Locator, Page } from '@playwright/test';
import { addDays, periodOf, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-008 "Visualización de pagos registrados por el residente", in a real browser:
 * /resident/payments and /resident/payments/{id} as Laura Gómez (RESIDENTE), with
 * the normal login only (no resident, user or unit id is ever sent).
 *
 * Every test starts from the seed (reset) and prepares its payments through the
 * administrator's API, as the obligations tests do (HU-004 registration: FIFO,
 * credit and status are the backend's). Nothing financial is computed here.
 *
 * Scenario (T = Bogotá today), apartment B1-TO1-APTO-101 of Laura:
 *   obligations  monthly fee of last month (100.000, due day 5) and a penalty "Ruido E2E" (50.000, due T+10)
 *   applied      100.000 on T-30, transfer: covers the monthly fee            → Aplicado
 *   partial      80.000 on T-20, transfer, reference + notes: 50.000 to the penalty, 30.000 left → Aplicado parcialmente
 *   credit       40.000 on T-10, cash, "Otro: Anticipo E2E", nothing pending  → Saldo a favor
 *   extra        (pagination tests only) small transfers on T-40, T-41, ... → Saldo a favor
 * Pedro Ruiz (B1-TO1-APTO-102): 25.000 on T-10 (the same day as Laura's credit), with his own reference and notes.
 */

interface Registered {
  id: number;
  receipt_number: string;
}

interface Scenario {
  applied: Registered;
  partial: Registered;
  credit: Registered;
  pedro: Registered;
  dates: { applied: string; partial: string; credit: string };
  period: string;
}

const REFERENCE = 'TRF-E2E-0042';
const NOTES = 'Pago recibido en la portería.\nTrajo el soporte de la transferencia.';
const PEDRO = { reference: 'TRF-PEDRO-E2E-77', notes: 'Nota del pago de Pedro E2E', amount: '25000' };

async function register(api: Api, payment: Record<string, unknown>): Promise<Registered> {
  return api.json<Registered>(await api.post('/admin/payments', payment), 201);
}

async function residentId(api: Api, name: string): Promise<number> {
  const page = await api.json<{ items: { id: number; full_name: string }[] }>(await api.get(`/admin/residents?search=${encodeURIComponent(name)}`));
  const [resident] = page.items.filter((item) => item.full_name === name);
  return resident.id;
}

/** The scenario above, then Laura signs in (her session replaces the administrator's in this browser). */
type SignIn = (role: 'admin' | 'resident') => Promise<Api>;

async function prepare(api: Api, seed: Seed, signIn: SignIn, extra = 0): Promise<Scenario> {
  const unit = seed.units['B1-TO1-APTO-101'];
  const period = periodOf(seed.today, -1);
  await createCharge(api, { unit_id: unit, concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
  await createCharge(api, { unit_id: unit, concept: 'PENALTY', amount: '50000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });
  const dates = { applied: addDays(seed.today, -30), partial: addDays(seed.today, -20), credit: addDays(seed.today, -10) };
  const laura = { resident_id: seed.laura_id, method: 'TRANSFER', concept: 'MONTHLY_FEE' };
  const applied = await register(api, { ...laura, amount: '100000', payment_date: dates.applied });
  const partial = await register(api, {
    ...laura, amount: '80000', concept: 'PENALTY', payment_date: dates.partial, transaction_reference: REFERENCE, notes: NOTES
  });
  const credit = await register(api, {
    ...laura, amount: '40000', method: 'CASH', concept: 'OTHER', concept_description: 'Anticipo E2E', payment_date: dates.credit
  });
  for (let index = 0; index < extra; index += 1) {
    await register(api, { ...laura, amount: String(1000 + index), payment_date: addDays(seed.today, -40 - index) });
  }
  const pedro = await register(api, {
    resident_id: await residentId(api, 'Pedro Ruiz'), amount: PEDRO.amount, method: 'CARD', concept: 'MONTHLY_FEE',
    payment_date: dates.credit, transaction_reference: PEDRO.reference, notes: PEDRO.notes
  });
  await signIn('resident');
  return { applied, partial, credit, pedro, dates, period };
}

// --- Page helpers -----------------------------------------------------------------------------------------

/** "6 de octubre de 2026" (calendar date, as the pages show it). */
function longDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(year, month - 1, day))
  );
}

/** "Septiembre de 2026" for "2026-09". */
function monthLabel(period: string): string {
  const [year, month] = period.split('-').map(Number);
  const text = new Intl.DateTimeFormat('es-CO', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, 1)));
  return text.charAt(0).toLocaleUpperCase('es-CO') + text.slice(1);
}

const statusLine = (page: Page) => page.locator('[data-status-line]');
const rows = (page: Page) => page.locator('[data-rows] tr');
const filters = (page: Page) => page.getByRole('search', { name: 'Filtros de mis pagos' });
/** The receipt column (5th data cell; the date is the row header). */
const receipts = (page: Page) => page.locator('[data-rows] tr td:nth-of-type(5)');
/** The status column of each row (4th data cell): a badge with its text. */
const statuses = (page: Page) => page.locator('[data-rows] tr td:nth-of-type(4)');
/** The status of the detail: "Estado: " (for screen readers) and the badge. */
const detailStatus = (page: Page) => page.getByRole('main').locator('p', { hasText: /^Estado: \S/ });

async function openList(page: Page, query = ''): Promise<void> {
  await page.goto(`/resident/payments${query}`);
  await expect(statusLine(page)).not.toHaveText('Consultando tus pagos...');
}

async function apply(page: Page, values: { search?: string; status?: string; from?: string; to?: string }): Promise<void> {
  const form = filters(page);
  if (values.search !== undefined) await form.getByLabel('Buscar').fill(values.search);
  if (values.status !== undefined) await form.getByLabel('Estado').selectOption({ label: values.status });
  if (values.from !== undefined) await form.getByLabel('Fecha desde').fill(values.from);
  if (values.to !== undefined) await form.getByLabel('Fecha hasta').fill(values.to);
  await form.getByRole('button', { name: 'Aplicar' }).click();
}

async function expectReceipts(page: Page, expected: Registered[]): Promise<void> {
  await expect(receipts(page)).toHaveText(expected.map((payment) => payment.receipt_number));
}

function definition(scope: Locator, label: string): Locator {
  return scope.locator('div').filter({ has: scope.page().getByText(label, { exact: true }) }).locator('dd');
}

const information = (page: Page, label: string) => definition(page.getByRole('region', { name: 'Información del pago' }), label);
const result = (page: Page, label: string) => definition(page.getByRole('region', { name: 'Resultado del pago' }), label);

async function amountOf(locator: Locator): Promise<string> {
  return normalized(await locator.textContent());
}

const NOT_FOUND = 'Pago no encontrado.';

// --- 1. Access -----------------------------------------------------------------------------------------------

test.describe('access', () => {
  test('the resident reaches "Mis pagos" from the panel and the header; aria-current marks it', async ({ page, signIn }) => {
    await signIn('resident');
    await page.goto('/resident');
    const nav = page.getByRole('navigation', { name: 'Principal' });
    await expect(nav.getByRole('link', { name: 'Inicio' })).toHaveAttribute('aria-current', 'page');
    await expect(nav.getByRole('link', { name: 'Mis pagos', exact: true })).not.toHaveAttribute('aria-current', 'page');

    await page.getByRole('main').getByRole('link', { name: /^Mis pagos/ }).click();
    await expect(page).toHaveURL(/\/resident\/payments$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Mis pagos');
    await expect(page.getByText('Consulta los pagos registrados en tu cuenta y revisa su estado.')).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Mis pagos', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(nav.getByRole('link', { name: 'Inicio' })).not.toHaveAttribute('aria-current', 'page');

    await page.goto('/resident');
    await nav.getByRole('link', { name: 'Mis pagos', exact: true }).click();
    await expect(page).toHaveURL(/\/resident\/payments$/);
  });

  test('the administrator gets 403 on the pages and on the API, without payment data', async ({ admin: { page, api }, seed, signIn }) => {
    const scenario = await prepare(api, seed, signIn);
    // Back to the administrator (prepare() signed Laura in).
    const adminApi = await signIn('admin');
    for (const path of ['/resident/payments', `/resident/payments/${scenario.partial.id}`]) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(403);
      expect(response?.headers()['cache-control']).toBe('no-store');
      await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
      const html = await page.content();
      for (const secret of [scenario.partial.receipt_number, REFERENCE, 'Pago recibido en la portería', 'data-payments-page']) {
        expect(html, path).not.toContain(secret);
      }
    }
    for (const path of ['/resident/payments', `/resident/payments/${scenario.partial.id}`]) {
      const response = await adminApi.get(path);
      expect(response.status(), path).toBe(403);
      expect(((await response.json()) as { error: { code: string } }).error.code).toBe('FORBIDDEN');
    }
  });

  test('without a session the pages go to the login', async ({ page, seed }) => {
    for (const path of ['/resident/payments', `/resident/payments/${seed.charges.history}`]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
    }
  });
});

// --- 2. List -------------------------------------------------------------------------------------------------

test.describe('list', () => {
  test('only the resident\'s payments, newest payment date first, with the main columns', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);

    await expect(statusLine(page)).toHaveText('Mostrando 1–3 de 3 pagos');
    await expect(page.getByRole('columnheader')).toHaveText(['Fecha', 'Concepto', 'Monto', 'Método', 'Estado', 'Recibo', 'Acción']);
    await expectReceipts(page, [s.credit, s.partial, s.applied]);

    const credit = rows(page).nth(0);
    await expect(credit.getByRole('rowheader')).toHaveText(longDate(s.dates.credit));
    const cells = credit.getByRole('cell');
    await expect(cells.nth(0)).toHaveText('Otro: Anticipo E2E');
    expect(await amountOf(cells.nth(1))).toBe('$ 40.000,00');
    await expect(cells.nth(2)).toHaveText('Efectivo');
    await expect(cells.nth(3)).toHaveText('Saldo a favor');
    await expect(cells.nth(4)).toHaveText(s.credit.receipt_number);
    await expect(credit.getByRole('link', { name: `Ver detalle del pago ${s.credit.receipt_number} del ${longDate(s.dates.credit)}` })).toBeVisible();

    // Pedro's payment (same day as Laura's credit) is never listed; notes and references stay in the detail.
    const table = page.locator('[data-rows]');
    for (const absent of [s.pedro.receipt_number, PEDRO.reference, PEDRO.notes, 'Pago recibido en la portería', REFERENCE]) {
      await expect(table).not.toContainText(absent);
    }
  });

  test('"No tienes pagos registrados." is not "no results"', async ({ page, signIn }) => {
    await signIn('resident');
    await openList(page);
    await expect(page.getByText('No tienes pagos registrados.')).toBeVisible();
    await expect(page.getByText('No encontramos pagos con los filtros seleccionados.')).toBeHidden();
    await expect(statusLine(page)).toHaveText('0 pagos');
    await expect(rows(page)).toHaveCount(0);
  });
});

// --- 3. Search -----------------------------------------------------------------------------------------------

test.describe('search', () => {
  test('by receipt number (with and without prefix) and by date', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);

    await apply(page, { search: s.partial.receipt_number });
    await expectReceipts(page, [s.partial]);
    await expect(page).toHaveURL(`/resident/payments?search=${s.partial.receipt_number}`);

    await apply(page, { search: String(s.partial.id) });
    await expectReceipts(page, [s.partial]);

    // Pedro paid that day too: only Laura's payment.
    await apply(page, { search: s.dates.credit });
    await expectReceipts(page, [s.credit]);
    await expect(statusLine(page)).toHaveText('Mostrando 1–1 de 1 pago');
  });

  test('no results and an invalid search, next to the field', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);

    await apply(page, { search: addDays(s.dates.credit, 1) });
    await expect(page.getByText('No encontramos pagos con los filtros seleccionados.')).toBeVisible();
    await expect(page.getByText('No tienes pagos registrados.')).toBeHidden();

    await apply(page, { search: 'Laura' });
    const field = filters(page).getByLabel('Buscar');
    const error = page.locator('#filter-search-error');
    await expect(error).toHaveText('Busca por número de recibo (p. ej., RC-00000012 o 12) o por fecha (AAAA-MM-DD).');
    await expect(field).toHaveAttribute('aria-invalid', 'true');
    await expect(field).toHaveAttribute('aria-describedby', /filter-search-error/);
    await expect(field).toBeFocused();
    await expect(page.getByRole('alert').filter({ hasText: 'Revisa los filtros señalados.' })).toBeVisible();

    // A valid search clears the error.
    await apply(page, { search: s.applied.receipt_number });
    await expectReceipts(page, [s.applied]);
    await expect(error).toBeHidden();
    await expect(field).toHaveAttribute('aria-invalid', 'false');
  });
});

// --- 4. Filters ----------------------------------------------------------------------------------------------

test.describe('filters', () => {
  test('each status, the date range, combinations, the count and "Limpiar filtros"', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);
    await expect(page.getByText('Sin filtros')).toBeVisible();
    await expect(filters(page).getByRole('button', { name: 'Limpiar filtros' })).toBeHidden();

    for (const [label, expected] of [['Aplicado', s.applied], ['Aplicado parcialmente', s.partial], ['Saldo a favor', s.credit]] as const) {
      await apply(page, { status: label });
      await expectReceipts(page, [expected]);
      await expect(statuses(page)).toHaveText(label);
    }
    await expect(page.getByText('1 filtro aplicado')).toBeVisible();

    await apply(page, { status: 'Todos', from: s.dates.partial });
    await expectReceipts(page, [s.credit, s.partial]);
    await apply(page, { from: '', to: s.dates.partial });
    await expectReceipts(page, [s.partial, s.applied]);

    await apply(page, { status: 'Aplicado parcialmente', from: s.dates.applied, to: s.dates.credit });
    await expectReceipts(page, [s.partial]);
    await expect(page.getByText('2 filtros aplicados')).toBeVisible();
    await expect(page).toHaveURL(`/resident/payments?status=PARTIALLY_APPLIED&date_from=${s.dates.applied}&date_to=${s.dates.credit}`);

    await apply(page, { status: 'Aplicado', from: s.dates.partial, to: '' });
    await expect(page.getByText('No encontramos pagos con los filtros seleccionados.')).toBeVisible();

    await filters(page).getByRole('button', { name: 'Limpiar filtros' }).click();
    await expectReceipts(page, [s.credit, s.partial, s.applied]);
    await expect(page).toHaveURL(/\/resident\/payments$/);
    await expect(page.getByText('Sin filtros')).toBeVisible();
    await expect(filters(page).getByLabel('Estado')).toHaveValue('');
    await expect(filters(page).getByLabel('Fecha desde')).toHaveValue('');
  });

  test('the filters in the URL are applied when the page opens', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page, `?status=CREDIT&search=${s.dates.credit}`);
    await expectReceipts(page, [s.credit]);
    await expect(filters(page).getByLabel('Estado')).toHaveValue('CREDIT');
    await expect(filters(page).getByLabel('Buscar')).toHaveValue(s.dates.credit);
  });
});

// --- 5. Pagination -------------------------------------------------------------------------------------------

test.describe('pagination', () => {
  test('pages of 10, previous/next, the filters kept, a new filter back to page 1', async ({ admin: { page, api }, seed, signIn }) => {
    // 3 payments + 10 older ones (all "Saldo a favor").
    const s = await prepare(api, seed, signIn, 10);
    await openList(page);
    const previous = page.getByRole('button', { name: 'Anterior' });
    const next = page.getByRole('button', { name: 'Siguiente' });
    const pageInfo = page.locator('[data-page-info]');

    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 13 pagos');
    await expect(rows(page)).toHaveCount(10);
    await expect(pageInfo).toHaveText('Página 1 de 2');
    await expect(pageInfo).toHaveAttribute('aria-current', 'page');
    await expect(previous).toBeDisabled();
    await expect(next).toBeEnabled();
    await expect(receipts(page).first()).toHaveText(s.credit.receipt_number);

    await next.click();
    await expect(statusLine(page)).toHaveText('Mostrando 11–13 de 13 pagos');
    await expect(rows(page)).toHaveCount(3);
    await expect(pageInfo).toHaveText('Página 2 de 2');
    await expect(next).toBeDisabled();
    await expect(previous).toBeEnabled();
    await expect(page).toHaveURL('/resident/payments?page=2');

    await previous.click();
    await expect(pageInfo).toHaveText('Página 1 de 2');
    await expect(page).toHaveURL(/\/resident\/payments$/);

    // A filter with 11 results: the pages keep it.
    await apply(page, { status: 'Saldo a favor' });
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 11 pagos');
    await next.click();
    await expect(statusLine(page)).toHaveText('Mostrando 11–11 de 11 pagos');
    await expect(page).toHaveURL('/resident/payments?page=2&status=CREDIT');
    await expect(statuses(page)).toHaveText('Saldo a favor');

    // Applying a filter or a search from page 2 goes back to page 1.
    await apply(page, { search: s.credit.receipt_number });
    await expect(page).toHaveURL(`/resident/payments?status=CREDIT&search=${s.credit.receipt_number}`);
    await expect(pageInfo).toHaveText('Página 1 de 1');
    await expectReceipts(page, [s.credit]);

    // "Por página" also starts again on page 1.
    await filters(page).getByLabel('Buscar').fill('');
    await apply(page, { status: 'Todos' });
    await next.click();
    await expect(pageInfo).toHaveText('Página 2 de 2');
    await page.getByLabel('Por página').selectOption('25');
    await expect(statusLine(page)).toHaveText('Mostrando 1–13 de 13 pagos');
    await expect(page).toHaveURL('/resident/payments?page_size=25');
  });
});

// --- 6. Statuses ---------------------------------------------------------------------------------------------

test.describe('statuses', () => {
  test('the three statuses in words, and the help that explains them', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);
    await expect(statuses(page)).toHaveText(['Saldo a favor', 'Aplicado parcialmente', 'Aplicado']);
    await expectReceipts(page, [s.credit, s.partial, s.applied]);

    const help = page.getByText('¿Qué significa el estado de un pago?');
    const panel = page.locator('details').filter({ has: help });
    await expect(panel.getByText('Todo el valor del pago se usó para cubrir obligaciones.')).toBeHidden();
    await help.click();
    await expect(panel.getByText('Todo el valor del pago se usó para cubrir obligaciones.')).toBeVisible();
    await expect(panel.getByText('Una parte del pago cubrió obligaciones y el resto quedó como saldo a favor.')).toBeVisible();
    await expect(panel.getByText(/su valor quedó disponible para obligaciones posteriores/)).toBeVisible();
    await expect(panel.getByText(/cuando el saldo a favor se usa para cubrir una nueva obligación.*no indica un error/)).toBeVisible();
  });
});

// --- 7, 8, 9. Detail, obligations covered, credit ------------------------------------------------------------

test.describe('detail', () => {
  test('a payment with reference, notes and an obligation covered, opened from the list', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);
    await page.getByRole('link', { name: `Ver detalle del pago ${s.partial.receipt_number} del ${longDate(s.dates.partial)}` }).click();
    await expect(page).toHaveURL(`/resident/payments/${s.partial.id}`);

    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Detalle del pago');
    await expect(page.getByText(/^Recibo RC-/)).toHaveText(`Recibo ${s.partial.receipt_number}`);
    await expect(detailStatus(page)).toHaveText('Estado: Aplicado parcialmente');

    await expect(information(page, 'Fecha del pago')).toHaveText(longDate(s.dates.partial));
    await expect(information(page, 'Concepto')).toHaveText('Penalización');
    expect(await amountOf(information(page, 'Monto'))).toBe('$ 80.000,00');
    await expect(information(page, 'Método')).toHaveText('Transferencia');
    await expect(information(page, 'Unidad')).toHaveText('B1-TO1-APTO-101');
    // Stored in UTC, shown in Bogotá time.
    await expect(information(page, 'Registrado el')).toHaveText(new RegExp(`^${longDate(seed.today)} a las \\d{1,2}:\\d{2}`));
    // Two different data: the receipt number and the transaction reference.
    await expect(information(page, 'Número de recibo')).toHaveText(s.partial.receipt_number);
    await expect(information(page, 'Referencia de transacción')).toHaveText(REFERENCE);
    expect(REFERENCE).not.toBe(s.partial.receipt_number);

    const notes = page.getByRole('region', { name: 'Nota de la administración' });
    await expect(notes.getByText('Registrada por la administración.')).toBeVisible();
    expect(await notes.locator('p').last().innerText()).toBe(NOTES);
    expect((await page.locator('main').innerText()).toLowerCase()).not.toContain('interna');

    expect(await amountOf(result(page, 'Monto pagado'))).toBe('$ 80.000,00');
    expect(await amountOf(result(page, 'Monto aplicado'))).toBe('$ 50.000,00');
    expect(await amountOf(result(page, 'Saldo a favor'))).toBe('$ 30.000,00');

    const covered = page.getByRole('region', { name: 'Obligaciones cubiertas' }).getByRole('table');
    await expect(covered.getByRole('columnheader')).toHaveText(['Concepto', 'Periodo', 'Vencimiento', 'Valor de la obligación', 'Valor aplicado']);
    const row = covered.locator('tbody tr');
    await expect(row).toHaveCount(1);
    await expect(row.getByRole('rowheader')).toHaveText('Penalización: Ruido E2E');
    await expect(row.getByRole('cell').nth(0)).toHaveText('—');
    await expect(row.getByRole('cell').nth(1)).toHaveText(longDate(addDays(seed.today, 10)));
    expect(await amountOf(row.getByRole('cell').nth(2))).toBe('$ 50.000,00');
    expect(await amountOf(row.getByRole('cell').nth(3))).toBe('$ 50.000,00');

    await page.getByRole('main').getByRole('link', { name: 'Volver a Mis pagos' }).click();
    await expect(page).toHaveURL(/\/resident\/payments$/);
    await expect(statusLine(page)).toHaveText('Mostrando 1–3 de 3 pagos');
  });

  test('a monthly fee covered: its period; no reference and no notes', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await page.goto(`/resident/payments/${s.applied.id}`);
    await expect(detailStatus(page)).toHaveText('Estado: Aplicado');
    await expect(information(page, 'Referencia de transacción')).toHaveText('No registrada');
    await expect(information(page, 'Número de recibo')).toHaveText(s.applied.receipt_number);
    await expect(page.getByRole('region', { name: 'Nota de la administración' }).getByText('Sin notas')).toBeVisible();
    expect(await amountOf(result(page, 'Monto aplicado'))).toBe('$ 100.000,00');
    expect(await amountOf(result(page, 'Saldo a favor'))).toBe('$ 0,00');

    const row = page.getByRole('region', { name: 'Obligaciones cubiertas' }).locator('tbody tr');
    await expect(row.getByRole('rowheader')).toHaveText('Mensualidad');
    await expect(row.getByRole('cell').nth(0)).toHaveText(monthLabel(s.period));
    await expect(row.getByRole('cell').nth(1)).toHaveText(longDate(`${s.period}-05`));
    expect(await amountOf(row.getByRole('cell').nth(2))).toBe('$ 100.000,00');
    expect(await amountOf(row.getByRole('cell').nth(3))).toBe('$ 100.000,00');
  });

  test('credit: nothing covered yet; when a new obligation uses it, the status shown is the current one', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await page.goto(`/resident/payments/${s.credit.id}`);
    await expect(detailStatus(page)).toHaveText('Estado: Saldo a favor');
    await expect(information(page, 'Concepto')).toHaveText('Otro: Anticipo E2E');
    await expect(information(page, 'Método')).toHaveText('Efectivo');
    expect(await amountOf(result(page, 'Monto pagado'))).toBe('$ 40.000,00');
    expect(await amountOf(result(page, 'Monto aplicado'))).toBe('$ 0,00');
    expect(await amountOf(result(page, 'Saldo a favor'))).toBe('$ 40.000,00');
    await expect(page.getByText('Este pago no se ha aplicado a ninguna obligación.')).toBeVisible();

    // The administration creates an obligation of 70.000: the backend uses the credit left (30.000 + 40.000).
    const adminApi = await signIn('admin');
    await createCharge(adminApi, {
      unit_id: seed.units['B1-TO1-APTO-101'], concept: 'OTHER', amount: '70000', due_date: addDays(seed.today, 20), description: 'Arreglo E2E'
    });
    await signIn('resident');

    await page.reload();
    await expect(detailStatus(page)).toHaveText('Estado: Aplicado');
    expect(await amountOf(result(page, 'Monto aplicado'))).toBe('$ 40.000,00');
    expect(await amountOf(result(page, 'Saldo a favor'))).toBe('$ 0,00');
    const row = page.getByRole('region', { name: 'Obligaciones cubiertas' }).locator('tbody tr');
    await expect(row.getByRole('rowheader')).toHaveText('Otro: Arreglo E2E');
    expect(await amountOf(row.getByRole('cell').nth(3))).toBe('$ 40.000,00');

    await openList(page);
    await expect(statuses(page)).toHaveText(['Aplicado', 'Aplicado', 'Aplicado']);
  });
});

// --- 10. Missing and foreign payments; 3. no leak through the search -----------------------------------------

test.describe('isolation', () => {
  test('another resident\'s payment looks exactly like a missing one, by URL and by search', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    const texts: string[] = [];
    for (const id of [String(s.pedro.id), '999999999', String(s.pedro.id + 1000)]) {
      const response = await page.goto(`/resident/payments/${id}`);
      expect(response?.status(), id).toBe(404);
      expect(response?.headers()['cache-control']).toBe('no-store');
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(NOT_FOUND);
      await expect(page.getByRole('main').getByRole('link', { name: 'Volver a Mis pagos' })).toBeVisible();
      texts.push(await page.locator('main').innerText());
      const html = await page.content();
      for (const secret of ['Pedro', 'pedro.e2e@example.com', 'B1-TO1-APTO-102', PEDRO.reference, PEDRO.notes, s.pedro.receipt_number, '25.000']) {
        expect(html, `${id}: ${secret}`).not.toContain(secret);
      }
    }
    expect(new Set(texts).size).toBe(1);

    // An invalid id: the same page (never reaches the API).
    const invalid = await page.goto('/resident/payments/abc');
    expect(invalid?.status()).toBe(404);
    expect(await page.locator('main').innerText()).toBe(texts[0]);

    // Search by Pedro's receipt number, in both forms: nothing.
    await openList(page);
    for (const search of [s.pedro.receipt_number, String(s.pedro.id)]) {
      await apply(page, { search });
      await expect(page.getByText('No encontramos pagos con los filtros seleccionados.')).toBeVisible();
      await expect(rows(page)).toHaveCount(0);
      await expect(page.locator('main')).not.toContainText(PEDRO.reference);
    }
  });
});

// --- 12. Errors ----------------------------------------------------------------------------------------------

test.describe('errors', () => {
  test('a session that ended: the list says so and offers the login; the pages go to the login', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);
    await expectReceipts(page, [s.credit, s.partial, s.applied]);

    // The session ends elsewhere (the normal logout of the application).
    const resident = await signIn('resident');
    expect((await resident.post('/auth/logout', {})).status()).toBe(204);
    await apply(page, { status: 'Aplicado' });
    const alert = page.getByRole('alert').filter({ hasText: 'Tu sesión ha expirado. Inicia sesión nuevamente.' });
    await expect(alert).toBeVisible();
    await expect(alert.getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute('href', '/login');
    await expect(alert.getByRole('button', { name: 'Reintentar' })).toBeHidden();
    await expect(rows(page)).toHaveCount(0);

    await page.goto(`/resident/payments/${s.partial.id}`);
    await expect(page).toHaveURL(/\/login$/);
  });

  test('an unexpected API error shows a plain message and "Reintentar" recovers', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    // The browser's request only (test-side interception); the application is unchanged.
    const isList = (url: URL) => url.pathname === '/api/v1/resident/payments';
    await page.route(isList, (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'INTERNAL_ERROR', message: 'Traceback', details: [] } }) })
    );
    await page.goto('/resident/payments');
    const alert = page.getByRole('alert').filter({ hasText: 'No fue posible cargar tus pagos. Intenta nuevamente.' });
    await expect(alert).toBeVisible();
    await expect(page.locator('main')).not.toContainText('Traceback');

    await page.unroute(isList);
    await alert.getByRole('button', { name: 'Reintentar' }).click();
    await expectReceipts(page, [s.credit, s.partial, s.applied]);
    await expect(alert).toBeHidden();
  });
});

// --- 11. Responsive ------------------------------------------------------------------------------------------

/** The page never scrolls sideways, and nothing sticks out of the viewport (same check as responsive.spec.ts). */
async function expectNoOverflow(page: Page, step: string): Promise<void> {
  const check = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const outside = [...document.querySelectorAll<HTMLElement>('body *')]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        if (box.width === 0 || box.height === 0 || getComputedStyle(element).visibility === 'hidden') return false;
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const overflow = getComputedStyle(parent).overflowX;
          if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return false;
        }
        return box.left < -1 || box.right > width + 1;
      })
      .slice(0, 5)
      .map((element) => `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}`);
    return { equal: document.documentElement.scrollWidth === document.documentElement.clientWidth, outside };
  });
  expect(check.equal, `${step}: horizontal scroll of the page`).toBe(true);
  expect(check.outside, `${step}: elements outside the viewport`).toEqual([]);
}

async function expectInside(locator: Locator, width: number): Promise<void> {
  await expect(locator).toBeVisible();
  const box = (await locator.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
}

async function cellLabels(row: Locator): Promise<string[]> {
  return row.locator('td[data-label]').evaluateAll((cells) => cells.map((cell) => getComputedStyle(cell, '::before').content.replace(/"/g, '')));
}

test.describe('360 × 800', () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

  test('list: stacked filters and rows with their labels, readable errors, usable buttons', async ({ admin: { page, api }, seed, signIn }) => {
    await prepare(api, seed, signIn, 10);
    await openList(page);
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 13 pagos');
    await expectNoOverflow(page, 'list');

    // One column of filters.
    const boxes = await Promise.all(['Buscar', 'Estado', 'Fecha desde', 'Fecha hasta'].map(async (label) => (await filters(page).getByLabel(label).boundingBox())!));
    for (const box of boxes) {
      expect(Math.abs(box.x - boxes[0].x)).toBeLessThanOrEqual(1);
      expect(box.width).toBeGreaterThan(360 * 0.7);
    }

    // Rows stack: no header row, every cell inside the viewport with its label.
    await expect(page.getByRole('columnheader', { name: 'Monto' })).toBeHidden();
    const first = rows(page).first();
    expect(await first.evaluate((row) => getComputedStyle(row).display)).toBe('block');
    expect(await cellLabels(first)).toEqual(['Concepto', 'Monto', 'Método', 'Estado', 'Recibo']);
    for (const cell of await first.locator('th, td').all()) await expectInside(cell, 360);
    await expectInside(first.getByRole('link', { name: /Ver detalle/ }), 360);
    for (const name of ['Aplicar', 'Siguiente']) await expectInside(page.getByRole('button', { name }), 360);

    await page.getByRole('button', { name: 'Siguiente' }).click();
    await expect(page.locator('[data-page-info]')).toHaveText('Página 2 de 2');
    await expectNoOverflow(page, 'page 2');

    // A long invalid search: the message is fully visible.
    await apply(page, { search: 'x'.repeat(100) });
    const error = page.locator('#filter-search-error');
    await expect(error).toBeVisible();
    await expectInside(error, 360);
    expect(await error.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expectNoOverflow(page, 'invalid search');

    await page.getByText('¿Qué significa el estado de un pago?').click();
    await expectNoOverflow(page, 'status help');
  });

  test('detail: one column, the obligations covered stacked with their labels', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await page.goto(`/resident/payments/${s.partial.id}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Detalle del pago');
    await expectNoOverflow(page, 'detail');

    const values = page.getByRole('region', { name: 'Información del pago' }).locator('dd');
    await expect(values).toHaveCount(8);
    const xs = new Set<number>();
    for (const value of await values.all()) {
      await expectInside(value, 360);
      xs.add(Math.round((await value.boundingBox())!.x));
    }
    expect(xs.size, 'one column').toBe(1);

    const row = page.getByRole('region', { name: 'Obligaciones cubiertas' }).locator('tbody tr');
    expect(await row.evaluate((element) => getComputedStyle(element).display)).toBe('block');
    expect(await cellLabels(row)).toEqual(['Periodo', 'Vencimiento', 'Valor de la obligación', 'Valor aplicado']);
    for (const cell of await row.locator('th, td').all()) await expectInside(cell, 360);
    await expectInside(page.getByRole('main').getByRole('link', { name: 'Volver a Mis pagos' }), 360);

    await page.goto(`/resident/payments/${s.pedro.id}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(NOT_FOUND);
    await expectNoOverflow(page, 'not found');
  });
});

test.describe('1280 × 800', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('list and detail as tables, without horizontal scroll', async ({ admin: { page, api }, seed, signIn }) => {
    const s = await prepare(api, seed, signIn);
    await openList(page);
    await expect(statusLine(page)).toHaveText('Mostrando 1–3 de 3 pagos');
    await expect(page.getByRole('columnheader', { name: 'Monto' })).toBeVisible();
    expect(await rows(page).first().evaluate((row) => getComputedStyle(row).display)).toBe('table-row');
    await expectNoOverflow(page, 'list');

    await page.goto(`/resident/payments/${s.partial.id}`);
    await expect(page.getByRole('region', { name: 'Obligaciones cubiertas' }).getByRole('columnheader', { name: 'Valor aplicado' })).toBeVisible();
    await expectNoOverflow(page, 'detail');
  });
});
