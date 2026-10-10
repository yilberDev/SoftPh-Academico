import type { Download, Locator, Page, Request } from '@playwright/test';
import { E2E_ORIGIN } from '../playwright.config';
import { addDays, periodOf, type Seed } from './support/data';
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

async function startPayment(page: Page, title: RegExp | string, amount?: string): Promise<void> {
  await obligation(page, title).getByRole('button', { name: 'Pagar esta obligación' }).click();
  if (amount !== undefined) await page.getByLabel('Monto a pagar (COP)').fill(amount);
  await page.getByRole('button', { name: 'Continuar' }).click();
  await expect(page.getByRole('heading', { name: 'Confirma el pago de demostración' })).toBeVisible();
  await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
  await expect(page.getByRole('heading', { name: 'Transacción de demostración en curso' })).toBeVisible();
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
    expect(posts).toHaveLength(0); // nothing sent before confirming

    await page.getByRole('button', { name: 'Iniciar pago de demostración' }).click();
    await expect(page.getByRole('heading', { name: 'Transacción de demostración en curso' })).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(posts[0].postDataJSON()).toEqual({ charge_id: expect.any(Number), amount: '40000.50' }); // text, never a float
    await expect(definition(gateway(page), 'Referencia de demostración')).toHaveText(/^DEMO-[0-9A-F]{16}$/);
    await expect(definition(gateway(page), 'Vence')).toBeVisible();
    await expect(page).toHaveURL(/\?transaction=\d+$/);

    const transactionId = Number(new URL(page.url()).searchParams.get('transaction'));
    await expect(receiptLinks(page)).toHaveCount(0); // INITIATED: no receipt
    await page.getByRole('button', { name: 'Simular aprobación' }).click();
    await expect(page.getByRole('heading', { name: 'Pago aprobado y registrado' })).toBeVisible();
    await expect(gateway(page)).toContainText('Aprobado');
    await expect(definition(gateway(page), 'Número del pago en tu historial')).toHaveText(/^RC-\d{8}$/);
    const receiptNumber = normalized(await definition(gateway(page), 'Número del pago en tu historial').textContent());
    expect(posts).toHaveLength(2);

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
  });

  test('rejection after a reload (resumed from the URL), a processing error and a cancellation: nothing is paid', async ({ page, seed, admin, signIn }) => {
    await prepare(admin.api, seed, signIn);
    await open(page);
    await startPayment(page, 'Penalización: Ruido E2E');
    const url = page.url();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Transacción de demostración en curso' })).toBeVisible(); // resumed
    await expect(definition(gateway(page), 'Obligación')).toHaveText('Penalización: Ruido E2E');
    expect(page.url()).toBe(url);
    await expect(receiptLinks(page)).toHaveCount(0); // INITIATED
    await page.getByRole('button', { name: 'Simular rechazo' }).click();
    await expect(page.getByRole('heading', { name: 'Pago rechazado' })).toBeVisible();
    await expect(gateway(page)).toContainText('la obligación sigue pendiente');
    await expect(receiptLinks(page)).toHaveCount(0); // REJECTED: nothing was paid, no receipt

    await page.getByRole('button', { name: 'Volver a mis obligaciones' }).click();
    await expect(page).toHaveURL(/\/resident\/demo-payments$/);
    await startPayment(page, 'Penalización: Ruido E2E', '20000');
    await page.getByRole('button', { name: 'Simular error de procesamiento' }).click();
    await expect(page.getByRole('heading', { name: 'El pago no pudo aplicarse' })).toBeVisible();
    await expect(gateway(page)).toContainText('no debe considerarse pagada');
    await expect(receiptLinks(page)).toHaveCount(0); // FAILED

    await page.getByRole('button', { name: 'Volver a mis obligaciones' }).click();
    await startPayment(page, 'Penalización: Ruido E2E');
    await page.getByRole('button', { name: 'Cancelar operación' }).click();
    const dialog = page.getByRole('dialog', { name: '¿Cancelar la operación?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir con la operación' }).click();
    await expect(page.getByRole('heading', { name: 'Transacción de demostración en curso' })).toBeVisible();
    await page.getByRole('button', { name: 'Cancelar operación' }).click();
    await dialog.getByRole('button', { name: 'Cancelar operación' }).click();
    await expect(page.getByRole('heading', { name: 'Operación cancelada' })).toBeVisible();
    await expect(receiptLinks(page)).toHaveCount(0); // CANCELLED

    await page.getByRole('button', { name: 'Volver a mis obligaciones' }).click();
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

  test('360 px: no horizontal scroll in the list, the form and the gateway', async ({ page, seed, admin, signIn }) => {
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
    await expect(page.getByRole('button', { name: 'Simular aprobación' })).toBeVisible();
    await noScroll('gateway');
    await page.getByRole('button', { name: 'Cancelar operación' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancelar operación' }).click();
    await expect(page.getByRole('heading', { name: 'Operación cancelada' })).toBeVisible();
    await noScroll('result');
  });
});
