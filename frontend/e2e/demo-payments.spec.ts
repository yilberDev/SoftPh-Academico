import type { Download, Locator, Page, Request } from '@playwright/test';
import { E2E_ORIGIN } from '../playwright.config';
import { addDays, periodOf, residentPassword, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-010-D "Realización de pagos por el residente" (internal demo gateway), in a real browser:
 * /resident/demo-payments as Laura Gómez (RESIDENTE), against the real API on the *_test
 * database with DEMO_PAYMENTS_ENABLED for the E2E processes only (playwright.config.ts).
 *
 * Scenario (T = Bogotá today), apartment B1-TO1-APTO-101 of Laura, created by the administrator:
 *   a monthly fee of last month (100.000, due day 5: Vencida) and a penalty "Ruido E2E" (50.000, due T+10).
 * Everything financial (balances, allocation, status) is the backend's; the tests only read what the pages show.
 *
 * HU-010-E: the receipt of an approved payment is downloaded for real (browser → Astro proxy → FastAPI),
 * read in memory from Playwright's temporary download (nothing is written to the repository).
 *
 * The demo checkout has its own page (/resident/demo-payments/checkout?transaction=<id>), opened once the
 * transaction is started; a reload reads it again from the server, and the legacy URL
 * (/resident/demo-payments?transaction=<id>) leads to it. It lists the backend's real catalog of fictitious
 * test instruments (GET /instruments); the resident chooses one and processes it, the page sends only its
 * id and the result shown is the backend's.
 */

type SignIn = (role: 'admin' | 'resident') => Promise<Api>;

async function prepare(api: Api, seed: Seed, signIn: SignIn): Promise<void> {
  const unit = seed.units['B1-TO1-APTO-101'];
  const period = periodOf(seed.today, -1);
  await createCharge(api, { unit_id: unit, concept: 'MONTHLY_FEE', amount: '100000', due_date: `${period}-05`, billing_period: period });
  await createCharge(api, { unit_id: unit, concept: 'PENALTY', amount: '50000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });
  await signIn('resident');
}

const main = (page: Page) => page.getByRole('main');
const obligations = (page: Page) => page.locator('[data-obligations] > li');
const obligation = (page: Page, title: RegExp | string) => obligations(page).filter({ has: page.getByRole('heading', { name: title }) });
const gateway = (page: Page) => page.locator('[data-section="transaction"]');

function definition(scope: Locator, label: string): Locator {
  return scope.locator('div').filter({ has: scope.page().getByText(label, { exact: true }) }).locator('dd');
}

async function open(page: Page): Promise<void> {
  await page.goto('/resident/demo-payments');
  await expect(page.locator('[data-section="list"]')).toBeVisible();
}

/** The POSTs of the demo gateway sent by the page. */
function demoPosts(page: Page): Request[] {
  const posts: Request[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/resident/demo-payments')) posts.push(request);
  });
  return posts;
}

/** The demo receipt link (the visible one: the result view keeps a hidden one until an approval). */
const receiptLinks = (page: Page) => page.getByRole('link', { name: /Descargar comprobante de pago \(demostración\)/ });
const RECEIPT_FILE = /^comprobante-demo-RC-\d{8}\.pdf$/;

/** The downloaded file, read in memory from Playwright's temporary copy. */
async function downloaded(download: Download): Promise<Buffer> {
  expect(await download.failure()).toBeNull();
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

async function download(page: Page, link: Locator): Promise<{ name: string; pdf: Buffer }> {
  const [file] = await Promise.all([page.waitForEvent('download'), link.click()]);
  const pdf = await downloaded(file);
  expect(pdf.length).toBeGreaterThan(1000);
  expect(pdf.subarray(0, 5).toString('latin1')).toBe('%PDF-'); // a real PDF, not an error page
  expect(pdf.toString('latin1').trimEnd().endsWith('%%EOF')).toBe(true);
  return { name: file.suggestedFilename(), pdf };
}

/** The backend's catalog (services/demo_instruments.py), in its order. */
const INSTRUMENTS = {
  approve: 'Tarjeta de prueba ficticia terminada en 0001',
  funds: 'Tarjeta de prueba ficticia terminada en 0002',
  technical: 'Cuenta de prueba ficticia'
} as const;
const instruments = (page: Page) => page.getByRole('group', { name: 'Instrumento de prueba ficticio' });
const CHECKOUT_URL = /\/resident\/demo-payments\/checkout\?transaction=\d+$/;
const choosing = (page: Page) => page.getByRole('heading', { name: 'Elige un instrumento de prueba' });
const currentStep = (page: Page) => page.getByRole('list', { name: 'Pasos del pago de demostración' }).locator('[aria-current="step"]');
const processButton = (page: Page) => page.getByRole('button', { name: 'Procesar pago de demostración' });

/** Chooses a test instrument by its name and processes the payment. */
async function processWith(page: Page, name: string): Promise<void> {
  await instruments(page).getByRole('radio', { name }).check();
  await processButton(page).click();
}

async function startPayment(page: Page, title: RegExp | string, amount?: string): Promise<void> {
  await obligation(page, title).getByRole('button', { name: 'Pagar esta obligación' }).click();
  if (amount !== undefined) await page.getByLabel('Monto a pagar (COP)').fill(amount);
  await page.getByRole('button', { name: 'Continuar' }).click();
  await expect(page.getByRole('heading', { name: 'Confirma el pago de demostración' })).toBeVisible();
  await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
  await expect(page).toHaveURL(CHECKOUT_URL); // the checkout of the transaction just started
  await expect(choosing(page)).toBeVisible();
}

test.describe('demo payment', () => {
  test('the permanent notice, the empty list and the links (no obligations yet)', async ({ page, signIn }) => {
    await signIn('resident');
    await page.goto('/resident');
    await page.getByRole('main').getByRole('link', { name: /Pago de demostración/ }).click();
    await expect(page).toHaveURL(/\/resident\/demo-payments$/);
    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link', { name: 'Pago de demostración' })).toHaveAttribute('aria-current', 'page');
    const note = page.getByRole('note');
    await expect(note).toContainText('Demostración académica');
    await expect(note).toContainText('no se realiza ningún cobro real');
    await expect(note).toContainText('proveedor financiero externo');
    await expect(main(page).getByText('No tienes obligaciones pendientes por pagar.')).toBeVisible();
    // Never a card form: no card number, CVV, bank or password field anywhere.
    await expect(page.locator('input[autocomplete^="cc-"], input[type="password"]')).toHaveCount(0);
    await expect(page.getByLabel(/tarjeta|cvv|cuenta|banco/i)).toHaveCount(0);
  });

  test('the checkout without a transaction, or with one that is not the resident\'s: a safe exit, without data', async ({ page, signIn }) => {
    await signIn('resident');
    const posts = demoPosts(page);
    await page.goto('/resident/demo-payments/checkout');
    await expect(page.getByRole('heading', { name: 'No se indicó ninguna transacción de demostración.' })).toBeVisible();
    await expect(main(page).getByRole('link', { name: 'Volver a mis obligaciones' })).toHaveAttribute('href', '/resident/demo-payments');
    await expect(page.getByText('Entorno de simulación: no se realizará ningún cobro real')).toBeVisible();
    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link', { name: 'Pago de demostración' })).toHaveAttribute('aria-current', 'page');
    // A transaction that does not exist answers like another resident's: nothing of it is shown.
    await page.goto('/resident/demo-payments/checkout?transaction=999999');
    await expect(page.getByRole('heading', { name: 'La transacción no existe o no pertenece a tu cuenta.' })).toBeVisible();
    await expect(page.getByRole('radio')).toHaveCount(0);
    await expect(page.getByText('Referencia de demostración')).toBeHidden();
    expect(posts).toHaveLength(0);
  });

  test('a partial approval: list, form with the balance, review, gateway, the result and the history', async ({ page, seed, admin, signIn }) => {
    await prepare(admin.api, seed, signIn);
    await open(page);
    await expect(obligations(page)).toHaveCount(2);
    const fee = obligation(page, /^Mensualidad — /);
    await expect(fee).toContainText('Vencida');
    expect(normalized(await definition(fee, 'Saldo pendiente').textContent())).toBe('$ 100.000,00');

    const posts = demoPosts(page);
    await fee.getByRole('button', { name: 'Pagar esta obligación' }).click();
    const amount = page.getByLabel('Monto a pagar (COP)');
    await expect(amount).toHaveValue('100000'); // the outstanding balance by default
    await amount.fill('40000,50');
    await page.getByRole('button', { name: 'Continuar' }).click();
    const review = page.locator('[data-section="review"]');
    expect(normalized(await definition(review, 'Monto a pagar').textContent())).toBe('$ 40.000,50');
    await expect(definition(review, 'Tipo de pago')).toHaveText('Pago parcial');
    expect(normalized(await definition(review, 'Saldo pendiente si se aprueba').textContent())).toBe('$ 59.999,50');
    expect(posts).toHaveLength(0); // nothing sent before confirming

    // Started on the obligations page, continued on the checkout of that transaction.
    await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
    await expect(page).toHaveURL(CHECKOUT_URL);
    await expect(choosing(page)).toBeVisible();
    await expect(choosing(page)).toBeFocused(); // the focus follows the new view
    expect(posts).toHaveLength(1);
    expect(posts[0].postDataJSON()).toEqual({ charge_id: expect.any(Number), amount: '40000.50' }); // text, never a float
    await expect(page.getByText('Entorno de simulación: no se realizará ningún cobro real')).toBeVisible();
    await expect(currentStep(page)).toContainText('Instrumento de prueba');
    await expect(main(page).locator('[data-summary-concept]')).toHaveText(/^Mensualidad — /);
    expect(normalized(await main(page).locator('[data-summary-amount]').textContent())).toBe('$ 40.000,50');
    await expect(definition(gateway(page), 'Referencia de demostración')).toHaveText(/^DEMO-[0-9A-F]{16}$/);
    await expect(definition(gateway(page), 'Vence')).toBeVisible();
    expect(normalized(await definition(gateway(page), 'Saldo pendiente actual').textContent())).toBe('$ 100.000,00');

    const transactionId = Number(new URL(page.url()).searchParams.get('transaction'));
    await expect(receiptLinks(page)).toHaveCount(0); // INITIATED: no receipt

    // The checkout: the three instruments of the backend's catalog, in its order, nothing chosen yet.
    const radios = instruments(page).getByRole('radio');
    await expect(radios).toHaveCount(3);
    await expect(radios.nth(0)).toHaveAccessibleName(INSTRUMENTS.approve);
    await expect(radios.nth(1)).toHaveAccessibleName(INSTRUMENTS.funds);
    await expect(radios.nth(2)).toHaveAccessibleName(INSTRUMENTS.technical);
    await expect(radios.nth(0)).toHaveAccessibleDescription(/Tarjeta de prueba · terminación ficticia 0001.*simula un pago aprobado.*Resultado esperado: simula una aprobación\./);
    await expect(radios.nth(2)).toHaveAccessibleDescription(/^Cuenta de prueba\s*Instrumento ficticio.*No es una cuenta real\.\s*Resultado esperado: simula un error de procesamiento\.$/);
    for (const radio of await radios.all()) await expect(radio).not.toBeChecked();
    await expect(processButton(page)).toBeDisabled(); // nothing is processed without an instrument
    await expect(page.getByText('Selecciona un instrumento de prueba para continuar.')).toBeVisible();
    await expect(page.getByRole('note')).toContainText('no se realiza ningún cobro real'); // the notice stays visible
    // Never a payment-data field: only the three radio buttons.
    await expect(gateway(page).locator('input:not([type="radio"]), textarea, select')).toHaveCount(0);
    await expect(page.locator('input[autocomplete^="cc-"], input[type="password"]')).toHaveCount(0);
    expect(posts).toHaveLength(1);

    await instruments(page).getByRole('radio', { name: INSTRUMENTS.approve }).check();
    await expect(processButton(page)).toBeEnabled();
    await expect(page.getByText(`Se procesará con «${INSTRUMENTS.approve}». El resultado lo confirma el servidor.`)).toBeVisible();
    await processButton(page).click();
    // Its own processing view first (announced), then the result the server confirmed.
    await expect(page.getByRole('heading', { name: 'Procesando el pago de demostración…' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Pago aprobado y registrado' })).toBeVisible();
    await expect(currentStep(page)).toContainText('Resultado');
    await expect(gateway(page)).toContainText('Aprobado');
    await expect(definition(gateway(page), 'Instrumento de prueba')).toHaveText(INSTRUMENTS.approve);
    await expect(definition(gateway(page), 'Número del pago en tu historial')).toHaveText(/^RC-\d{8}$/);
    const receiptNumber = normalized(await definition(gateway(page), 'Número del pago en tu historial').textContent());
    expect(posts).toHaveLength(2);
    expect(posts[1].url()).toMatch(new RegExp(`/api/v1/resident/demo-payments/${transactionId}/complete$`));
    expect(posts[1].postDataJSON()).toEqual({ instrument_id: 'TEST_CARD_APPROVE' }); // only the id: never an outcome or a status

    // HU-010-E: one receipt link, by the persisted payment's id (confirmed by the backend), never the transaction's.
    const historyHref = await page.getByRole('link', { name: 'Ver el pago en Mis pagos' }).getAttribute('href');
    const paymentId = Number(historyHref?.split('/').pop());
    expect(paymentId).toBe(Number(receiptNumber.slice(3))); // RC-00000012 is payment 12
    expect(paymentId).not.toBe(transactionId);
    const receiptUrl = `/api/v1/resident/payments/${paymentId}/receipt`;
    await expect(receiptLinks(page)).toHaveCount(1);
    await expect(receiptLinks(page)).toHaveAttribute('href', receiptUrl);

    // The real download from the result: the receipt of this payment.
    const fromResult = await download(page, receiptLinks(page));
    expect(fromResult.name).toMatch(RECEIPT_FILE);
    expect(fromResult.name).toBe(`comprobante-demo-${receiptNumber}.pdf`);
    expect(fromResult.pdf.includes(Buffer.from(receiptNumber))).toBe(true);
    const reference = normalized(await definition(gateway(page), 'Referencia de demostración').textContent());
    expect(fromResult.pdf.includes(Buffer.from(reference))).toBe(true); // the DEMO-… reference of this transaction
    // The same URL with the browser's session: private, never cached, a download.
    const response = await page.request.get(receiptUrl);
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toBe('application/pdf');
    expect(response.headers()['content-disposition']).toBe(`attachment; filename="comprobante-demo-${receiptNumber}.pdf"`);
    expect(response.headers()['cache-control']).toBe('no-store');
    expect(response.headers()['x-content-type-options']).toBe('nosniff');

    // HU-008: the payment is in the history, with the demo gateway as its method.
    await page.getByRole('link', { name: 'Ver el pago en Mis pagos' }).click();
    await expect(page).toHaveURL(new RegExp(`/resident/payments/${paymentId}$`));
    await expect(main(page)).toContainText('Pasarela de demostración');
    await expect(main(page)).not.toContainText('undefined');
    // The detail offers the same receipt (its accessible name includes the receipt number).
    const detailLink = page.getByRole('link', { name: `Descargar comprobante de pago (demostración) ${receiptNumber}` });
    await expect(detailLink).toHaveAttribute('href', receiptUrl);
    const fromDetail = await download(page, detailLink);
    expect(fromDetail.name).toBe(fromResult.name);
    expect(fromDetail.pdf.equals(fromResult.pdf)).toBe(true); // the same payment, the same document

    // The list reflects the balance computed by the backend.
    await open(page);
    const after = obligation(page, /^Mensualidad — /);
    expect(normalized(await definition(after, 'Abonado').textContent())).toBe('$ 40.000,50');
    expect(normalized(await definition(after, 'Saldo pendiente').textContent())).toBe('$ 59.999,50');

    // HU-010 criterion 6.1: the administrator sees this payment (made in this test) in the payments summary.
    await signIn('admin');
    await page.goto('/admin/payments');
    const adminRow = page.locator('[data-rows] tr').filter({ hasText: receiptNumber });
    await expect(adminRow).toHaveCount(1);
    await expect(adminRow).toContainText('Laura Gómez');
    await expect(adminRow).toContainText(`${receiptNumber} · B1-TO1-APTO-101`);
    await expect(adminRow).toContainText('Pasarela de demostración');
    await expect(adminRow).toContainText('Aplicado');
    await expect(adminRow).not.toContainText('Aplicado parcialmente');
    expect(normalized(await adminRow.textContent())).toContain('$ 40.000,50');
  });

  test('A2: paying the whole balance settles the obligation: "Pagada" with 0 left in both statements, never offered again, a reload pays nothing more', async ({ page, seed, admin, signIn }) => {
    const before = await admin.api.json<{ total: number }>(await admin.api.get('/admin/payments'));
    await prepare(admin.api, seed, signIn);
    // The resident signs in on the login page (prepare signed in through the API: that session is dropped first).
    await page.context().clearCookies();
    await page.goto('/login');
    await page.getByLabel('Correo electrónico').fill(seed.resident_email);
    await page.getByLabel('Contraseña').fill(residentPassword());
    await page.getByRole('button', { name: 'Ingresar' }).click();
    await expect(page).toHaveURL(/\/resident$/);
    const menu = page.getByRole('navigation', { name: 'Principal' });
    await menu.getByRole('link', { name: 'Pago de demostración' }).click();
    await expect(page.locator('[data-section="list"]')).toBeVisible();

    // 1. The penalty is pending and offered, with its whole balance.
    await expect(obligations(page)).toHaveCount(2);
    const penalty = obligation(page, 'Penalización: Ruido E2E');
    expect(normalized(await definition(penalty, 'Saldo pendiente').textContent())).toBe('$ 50.000,00');

    // 2. The whole balance (the form's default amount), through the real form and checkout.
    const posts = demoPosts(page);
    await penalty.getByRole('button', { name: 'Pagar esta obligación' }).click();
    await expect(page.getByLabel('Monto a pagar (COP)')).toHaveValue('50000');
    await page.getByRole('button', { name: 'Continuar' }).click();
    const review = page.locator('[data-section="review"]');
    expect(normalized(await definition(review, 'Monto a pagar').textContent())).toBe('$ 50.000,00');
    await expect(definition(review, 'Tipo de pago')).toHaveText('Pago del saldo completo');
    expect(normalized(await definition(review, 'Saldo pendiente si se aprueba').textContent())).toBe('$ 0,00');
    await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
    await expect(page).toHaveURL(CHECKOUT_URL);
    await expect(choosing(page)).toBeVisible();
    const { charge_id: chargeId } = posts[0].postDataJSON() as { charge_id: number };
    expect(posts[0].postDataJSON()).toEqual({ charge_id: chargeId, amount: '50000' }); // the field's text, as typed
    expect(normalized(await definition(gateway(page), 'Saldo pendiente actual').textContent())).toBe('$ 50.000,00');
    await processWith(page, INSTRUMENTS.approve);

    // 3. Approved; the server no longer lists the obligation, so the summary drops its balance (nothing left).
    await expect(page.getByRole('heading', { name: 'Pago aprobado y registrado' })).toBeVisible();
    await expect(gateway(page)).toContainText('Aprobado');
    const receiptNumber = normalized(await definition(gateway(page), 'Número del pago en tu historial').textContent());
    expect(receiptNumber).toMatch(/^RC-\d{8}$/);
    await expect(main(page).locator('[data-summary-concept]')).toHaveText('Pago de demostración');
    await expect(definition(gateway(page), 'Saldo pendiente actual')).toHaveCount(0);
    expect(posts.map((post) => new URL(post.url()).pathname)).toEqual([
      '/api/v1/resident/demo-payments', `/api/v1/resident/demo-payments/${new URL(page.url()).searchParams.get('transaction')}/complete`
    ]);

    // 6 (reload). The same result is read again from the server: no new request, nothing to process again.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Pago aprobado y registrado' })).toBeVisible();
    await expect(definition(gateway(page), 'Número del pago en tu historial')).toHaveText(receiptNumber);
    await expect(instruments(page)).toBeHidden();
    expect(posts).toHaveLength(2);

    // The payment in "Mis pagos": once, applied in full to the penalty.
    await page.getByRole('link', { name: 'Ver el pago en Mis pagos' }).click();
    await expect(page.getByRole('main').locator('p', { hasText: /^Estado: \S/ })).toHaveText('Estado: Aplicado');
    const result = (label: string) => definition(page.getByRole('region', { name: 'Resultado del pago' }), label);
    expect(normalized(await result('Monto aplicado').textContent())).toBe('$ 50.000,00');
    expect(normalized(await result('Saldo a favor').textContent())).toBe('$ 0,00');
    const covered = page.getByRole('region', { name: 'Obligaciones cubiertas' }).locator('tbody tr');
    await expect(covered).toHaveCount(1);
    await expect(covered.getByRole('rowheader')).toHaveText('Penalización: Ruido E2E');
    expect(normalized(await covered.getByRole('cell').nth(3).textContent())).toBe('$ 50.000,00');
    await menu.getByRole('link', { name: 'Mis pagos' }).click();
    await expect(page.locator('[data-status-line]')).not.toHaveText('Consultando tus pagos...');
    await expect(page.locator('[data-rows] tr')).toHaveCount(1);

    // 4. The resident's statement (account-statement.spec.ts): the penalty "Pagada" with 0 left; the fee still owed.
    const cell = (row: Locator, label: string) => row.locator(`td[data-label="${label}"]`);
    async function expectSettled(): Promise<void> {
      const statement = page.locator('[data-account-statement]');
      await expect(statement.locator('[data-content]')).toBeVisible();
      await expect(statement.locator('[data-status-line]')).toHaveText('');
      for (const [key, value] of Object.entries({
        pending_balance: '$ 100.000,00', overdue_balance: '$ 100.000,00', credit_balance: '$ 0,00',
        total_charged: '$ 150.000,00', total_applied: '$ 50.000,00'
      })) {
        expect(normalized(await page.locator(`[data-total="${key}"]`).textContent()), key).toBe(value);
      }
      const paid = page.locator('[data-section="paid"] [data-rows] tr');
      await expect(paid).toHaveCount(1);
      await expect(cell(paid, 'Obligación')).toHaveText('Penalización: Ruido E2E');
      expect(normalized(await cell(paid, 'Valor').textContent())).toBe('$ 50.000,00');
      expect(normalized(await cell(paid, 'Abonado').textContent())).toBe('$ 50.000,00');
      expect(normalized(await cell(paid, 'Saldo').textContent())).toBe('$ 0,00');
      await expect(cell(paid, 'Estado')).toHaveText('Pagada');
      await expect(page.locator('[data-section="pending"] [data-rows] tr')).toHaveCount(0);
      await expect(page.locator('[data-section="overdue"] [data-rows] tr')).toHaveCount(1);
      const payments = page.locator('[data-payment-rows] tr');
      await expect(payments).toHaveCount(1);
      await expect(cell(payments, 'Recibo')).toHaveText(receiptNumber);
      await expect(cell(payments, 'Estado')).toHaveText('Aplicado');
    }
    await menu.getByRole('link', { name: 'Estado de cuenta' }).click();
    await expect(page).toHaveURL(/\/resident\/account-statement$/);
    await expectSettled();

    // 6. No longer offered: only the fee is left, the API does not list it, and starting one is refused.
    await menu.getByRole('link', { name: 'Pago de demostración' }).click();
    await expect(page.locator('[data-section="list"]')).toBeVisible();
    await expect(obligations(page)).toHaveCount(1);
    await expect(obligation(page, 'Penalización: Ruido E2E')).toHaveCount(0);
    await expect(obligation(page, /^Mensualidad — /)).toHaveCount(1);
    const resident = await signIn('resident');
    const payable = await resident.json<{ items: { charge_id: number }[] }>(await resident.get('/resident/demo-payments/obligations'));
    expect(payable.items.map((item) => item.charge_id)).not.toContain(chargeId);
    const again = await resident.post('/resident/demo-payments', { charge_id: chargeId, amount: '1' });
    expect(again.status()).toBe(409);
    expect(((await again.json()) as { error: { code: string } }).error.code).toBe('CHARGE_NOT_PAYABLE');

    // 5. The administrator: one payment more in total, by the demo gateway; the obligation "Pagada" with 0 left.
    const asAdmin = await signIn('admin');
    const after = await asAdmin.json<{ total: number }>(await asAdmin.get('/admin/payments'));
    expect(after.total).toBe(before.total + 1);
    await page.goto('/admin/payments');
    const adminRow = page.locator('[data-rows] tr').filter({ hasText: receiptNumber });
    await expect(adminRow).toHaveCount(1);
    await expect(adminRow).toContainText('Laura Gómez');
    await expect(adminRow).toContainText('Pasarela de demostración');
    await expect(adminRow).toContainText('Aplicado');
    expect(normalized(await adminRow.textContent())).toContain('$ 50.000,00');
    await page.goto(`/admin/obligations/${chargeId}`);
    const information = (label: string) => definition(page.getByRole('region', { name: 'Información' }), label);
    expect(normalized(await information('Aplicado').textContent())).toBe('$ 50.000,00');
    expect(normalized(await information('Saldo').textContent())).toBe('$ 0,00');
    await expect(information('Estado')).toHaveText('Pagada');
    const applications = page.getByRole('region', { name: 'Pagos aplicados' }).locator('tbody tr');
    await expect(applications).toHaveCount(1);
    await expect(applications.getByRole('rowheader')).toHaveText(receiptNumber);
    await page.goto(`/admin/residents/${seed.laura_id}/account-statement`);
    await expectSettled();
  });

  test('a reload recovers the checkout without starting it again; a rejection, another instrument (a new transaction), a processing error and a cancellation: nothing is paid', async ({ page, seed, admin, signIn }) => {
    await prepare(admin.api, seed, signIn);
    await open(page);
    const posts = demoPosts(page);
    await startPayment(page, 'Penalización: Ruido E2E');
    const url = page.url();
    const transactionId = Number(new URL(url).searchParams.get('transaction'));
    expect(posts).toHaveLength(1); // the start

    // A reload reads the same transaction from the server: never a second one.
    await page.reload();
    await expect(choosing(page)).toBeVisible();
    expect(page.url()).toBe(url);
    await expect(definition(gateway(page), 'Obligación')).toHaveText('Penalización: Ruido E2E');
    // The legacy URL of the transaction leads to the same checkout.
    await page.goto(`/resident/demo-payments?transaction=${transactionId}`);
    await expect(page).toHaveURL(url);
    await expect(choosing(page)).toBeVisible();
    expect(posts).toHaveLength(1); // still only the start
    await expect(receiptLinks(page)).toHaveCount(0); // INITIATED

    // Keyboard only: the radio group is reached with Tab and the arrows move the choice.
    await instruments(page).getByRole('radio', { name: INSTRUMENTS.approve }).focus();
    await page.keyboard.press('ArrowDown');
    await expect(instruments(page).getByRole('radio', { name: INSTRUMENTS.funds })).toBeChecked();
    await expect(instruments(page).getByRole('radio', { name: INSTRUMENTS.approve })).not.toBeChecked();
    await page.keyboard.press('Tab');
    await expect(processButton(page)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Pago rechazado' })).toBeVisible();
    await expect(gateway(page)).toContainText('la obligación sigue pendiente');
    await expect(gateway(page)).toContainText('Motivo simulado: fondos insuficientes en la tarjeta de prueba ficticia');
    await expect(definition(gateway(page), 'Instrumento de prueba')).toHaveText(INSTRUMENTS.funds);
    await expect(receiptLinks(page)).toHaveCount(0); // REJECTED: nothing was paid, no receipt
    expect(posts.slice(1).map((post) => post.postDataJSON())).toEqual([{ instrument_id: 'TEST_CARD_INSUFFICIENT_FUNDS' }]);
    // Read again after a reload: the result, the instrument and the reason the backend stored.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Pago rechazado' })).toBeVisible();
    await expect(gateway(page)).toContainText('Motivo simulado: fondos insuficientes');
    await expect(definition(gateway(page), 'Instrumento de prueba')).toHaveText(INSTRUMENTS.funds);
    await expect(instruments(page)).toBeHidden(); // a result offers no instrument: it cannot be processed again

    // Another instrument: a NEW transaction (same obligation and amount) and its own checkout.
    await expect(page.getByText('Se iniciará una nueva transacción de demostración')).toBeVisible();
    await page.getByRole('button', { name: 'Intentar con otro instrumento' }).click();
    await page.waitForURL((next) => next.pathname === '/resident/demo-payments/checkout' &&
      Number(next.searchParams.get('transaction')) > transactionId);
    await expect(choosing(page)).toBeVisible();
    expect(posts.at(-1)!.postDataJSON()).toEqual({ charge_id: expect.any(Number), amount: '50000.00' });
    await expect(instruments(page).getByRole('radio', { name: INSTRUMENTS.funds })).not.toBeChecked(); // a new transaction, no choice
    await processWith(page, INSTRUMENTS.technical);
    await expect(page.getByRole('heading', { name: 'Error de procesamiento simulado' })).toBeVisible();
    await expect(gateway(page)).toContainText('no debe considerarse pagada');
    await expect(gateway(page)).toContainText('Motivo simulado: error técnico de procesamiento');
    await expect(definition(gateway(page), 'Instrumento de prueba')).toHaveText(INSTRUMENTS.technical);
    await expect(receiptLinks(page)).toHaveCount(0); // FAILED
    expect(posts.at(-1)!.postDataJSON()).toEqual({ instrument_id: 'TEST_ACCOUNT_TECHNICAL_ERROR' });

    // A cancellation: confirmed in a dialog, then back to the obligations, which say so.
    await main(page).getByRole('link', { name: 'Volver a mis obligaciones' }).click();
    await expect(page).toHaveURL(/\/resident\/demo-payments$/);
    await startPayment(page, 'Penalización: Ruido E2E', '20000');
    await page.getByRole('button', { name: 'Cancelar operación' }).click();
    const dialog = page.getByRole('dialog', { name: '¿Cancelar la operación?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir con la operación' }).click();
    await expect(choosing(page)).toBeVisible();
    await page.getByRole('button', { name: 'Cancelar operación' }).click();
    await dialog.getByRole('button', { name: 'Cancelar operación' }).click();
    await expect(page).toHaveURL(/\/resident\/demo-payments$/); // the notice is not kept in the URL
    await expect(page.getByRole('status').filter({ hasText: 'Cancelaste la operación. No se aplicó ningún pago' })).toBeVisible();

    const penalty = obligation(page, 'Penalización: Ruido E2E');
    expect(normalized(await definition(penalty, 'Saldo pendiente').textContent())).toBe('$ 50.000,00');
    await page.goto('/resident/payments');
    await expect(page.locator('[data-status-line]')).not.toHaveText('Consultando tus pagos...');
    await expect(page.locator('[data-rows] tr')).toHaveCount(0); // no payment was registered
  });

  test('a manual payment of the resident (HU-004) offers no demo receipt, and its receipt URL does not exist', async ({ page, seed, admin, signIn }) => {
    const unit = seed.units['B1-TO1-APTO-101'];
    await createCharge(admin.api, { unit_id: unit, concept: 'PENALTY', amount: '50000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });
    const manual = await admin.api.json<{ id: number; receipt_number: string }>(await admin.api.post('/admin/payments', {
      resident_id: seed.laura_id, amount: '20000', method: 'CASH', concept: 'PENALTY', payment_date: seed.today
    }), 201);
    await signIn('resident');

    await page.goto(`/resident/payments/${manual.id}`);
    await expect(page.getByRole('heading', { name: 'Detalle del pago', level: 1 })).toBeVisible();
    await expect(main(page)).toContainText('Efectivo'); // HU-004 labels unchanged
    await expect(page.getByRole('link', { name: /comprobante/i })).toHaveCount(0);
    // Asked directly, the endpoint has nothing for it: the same 404 as a missing payment, private.
    const response = await page.request.get(`/api/v1/resident/payments/${manual.id}/receipt`);
    expect(response.status()).toBe(404);
    expect(response.headers()['cache-control']).toBe('no-store');
    expect((await response.json()).error.code).toBe('PAYMENT_NOT_FOUND');
    // Without a session the receipt endpoint answers 401 (never a PDF).
    const anonymous = await page.context().browser()!.newContext({ baseURL: E2E_ORIGIN });
    const unsigned = await anonymous.request.get(`/api/v1/resident/payments/${manual.id}/receipt`);
    expect(unsigned.status()).toBe(401);
    expect(unsigned.headers()['cache-control']).toBe('no-store');
    await anonymous.close();
  });

  test('an amount above the balance is rejected next to its field, before any request (keyboard only)', async ({ page, seed, admin, signIn }) => {
    await prepare(admin.api, seed, signIn);
    await open(page);
    const posts = demoPosts(page);
    const select = obligation(page, 'Penalización: Ruido E2E').getByRole('button', { name: 'Pagar esta obligación' });
    await select.focus();
    await page.keyboard.press('Enter');
    const amount = page.getByLabel('Monto a pagar (COP)');
    await amount.fill('50000,01');
    await amount.press('Enter');
    await expect(amount).toHaveAttribute('aria-invalid', 'true');
    await expect(amount).toHaveAttribute('aria-describedby', /demo-amount-error/);
    await expect(page.locator('#demo-amount-error')).toContainText('no puede superar el saldo pendiente');
    await expect(amount).toBeFocused();
    expect(posts).toHaveLength(0);
    await page.getByRole('button', { name: 'Volver a la lista' }).click();
    await expect(obligations(page)).toHaveCount(2);
    expect(posts).toHaveLength(0);
  });

  test('360 px: no horizontal scroll in the list, the form, the review, the checkout and the result', async ({ page, seed, admin, signIn }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await prepare(admin.api, seed, signIn);
    await open(page);
    const noScroll = async (step: string) => {
      const { scrollWidth, width } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth, width: document.documentElement.clientWidth
      }));
      expect(scrollWidth, step).toBeLessThanOrEqual(width);
    };
    await noScroll('list');
    await obligation(page, /^Mensualidad — /).getByRole('button', { name: 'Pagar esta obligación' }).click();
    await noScroll('form');
    await page.getByRole('button', { name: 'Continuar' }).click();
    await noScroll('review');
    await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
    await expect(page).toHaveURL(CHECKOUT_URL);
    await expect(instruments(page).getByRole('radio')).toHaveCount(3);
    await instruments(page).getByRole('radio', { name: INSTRUMENTS.funds }).check();
    await noScroll('checkout');
    await processButton(page).click();
    await expect(page.getByRole('heading', { name: 'Pago rechazado' })).toBeVisible();
    await noScroll('result');
  });
});
