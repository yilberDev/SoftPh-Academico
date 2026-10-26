import { createHash } from 'node:crypto';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { addDays, residentPassword } from './support/data';
import { createCharge, expect, normalized, test } from './support/fixtures';

/**
 * A1: a manual payment (HU-004) registered by the administrator on the real form, then seen by the resident
 * (HU-008 "Mis pagos", HU-014 "Estado de cuenta", HU-017 notification) and emailed as a PDF receipt (HU-004
 * criterion 3, HU-017 criterion 3), in a real browser on the E2E stack (*_test database).
 *
 * Seed: Laura Gómez lives in B1-TO1-APTO-101 with nothing owed or paid. The administrator adds one penalty of
 * 120.000 (due T+10) through the API, as the other financial tests do; the payment of 50.000 is the only one,
 * so HU-004 applies it to that penalty: 70.000 left, no credit. Every amount shown comes from the backend.
 *
 * The receipt goes to the development sandbox (Mailpit), which keeps the messages of earlier runs: the test notes
 * the receipt emails already sent to Laura and waits for exactly one new one, with this payment's receipt number
 * (read-only use of the Mailpit API; nothing is deleted). Unlike the reset and activation tests, this one is never
 * skipped: without Mailpit it fails, because the emailed PDF is part of what it checks.
 */

const MAILPIT = 'http://127.0.0.1:8025/api/v1';
const UNIT = 'B1-TO1-APTO-101';
const DESCRIPTION = 'Daño en zona común A1 E2E';
const REFERENCE = `TRF-A1-${Date.now()}`; // identifiable: never used by an earlier run
const NOTES = 'Pago recibido en la portería (A1 E2E).\nSoporte de la transferencia revisado.';

interface Registered {
  id: number;
  receipt_number: string;
  transaction_reference: string | null;
  notes: string | null;
  receipt_email_status: string;
}

interface MailpitMessage {
  ID: string;
  Subject: string;
  Text: string;
  To: { Address: string }[];
  Attachments: { PartID: string; FileName: string; ContentType: string; Size: number; Checksums: { SHA256: string } }[];
}

/** "6 de octubre de 2026" (calendar date, as the pages show it). */
function longDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(year, month - 1, day))
  );
}

function definition(scope: Locator, label: string): Locator {
  return scope.locator('div').filter({ has: scope.page().getByText(label, { exact: true }) }).locator('dd');
}

const text = async (locator: Locator) => normalized(await locator.textContent());

// --- Account statement (account-statement.spec.ts) ------------------------------------------------------------

const statement = (page: Page) => page.locator('[data-account-statement]');
const cell = (row: Locator, label: string) => row.locator(`td[data-label="${label}"]`);

async function ready(page: Page): Promise<void> {
  await expect(statement(page).locator('[data-content]')).toBeVisible();
  await expect(statement(page).locator('[data-status-line]')).toHaveText('');
}

async function expectTotals(page: Page, expected: Record<string, string>): Promise<void> {
  for (const [key, value] of Object.entries(expected)) {
    expect(await text(page.locator(`[data-total="${key}"]`)), key).toBe(value);
  }
}

// --- Mailpit (read only) ----------------------------------------------------------------------------------------

/** The ids of the receipt emails to `recipient` already in the sandbox. */
async function receiptEmails(request: APIRequestContext, recipient: string): Promise<string[]> {
  const query = encodeURIComponent(`to:"${recipient}" subject:"Recibo de pago"`);
  const response = await request.get(`${MAILPIT}/search?query=${query}&limit=1000`);
  expect(response.status(), `the development mail sandbox (Mailpit) at ${MAILPIT}`).toBe(200);
  return ((await response.json()) as { messages: { ID: string }[] }).messages.map((message) => message.ID);
}

/** The one new receipt email to `recipient` with `receipt` in its subject (not among `before`). */
async function newReceiptEmail(request: APIRequestContext, recipient: string, receipt: string, before: string[]): Promise<MailpitMessage> {
  const query = encodeURIComponent(`to:"${recipient}" subject:"${receipt}"`);
  let fresh: string[] = [];
  await expect.poll(async () => {
    const found = (await (await request.get(`${MAILPIT}/search?query=${query}`)).json()) as { messages: { ID: string }[] };
    fresh = found.messages.map((message) => message.ID).filter((id) => !before.includes(id));
    return fresh.length;
  }, { message: `a new receipt email ${receipt} to ${recipient} in Mailpit` }).toBe(1);
  return (await (await request.get(`${MAILPIT}/message/${fresh[0]}`)).json()) as MailpitMessage;
}

/** A complete PDF file: header, one page, and a trailer whose startxref points at its cross-reference table. */
function expectValidPdf(pdf: Buffer): void {
  expect(pdf.subarray(0, 7).toString('latin1'), 'PDF header').toBe('%PDF-1.');
  const trailer = /startxref\s+(\d+)\s+%%EOF\s*$/.exec(pdf.subarray(-64).toString('latin1'));
  expect(trailer, 'startxref and %%EOF at the end').not.toBeNull();
  const offset = Number(trailer![1]);
  expect(offset).toBeLessThan(pdf.length);
  expect(pdf.subarray(offset, offset + 4).toString('latin1'), 'cross-reference table at startxref').toBe('xref');
  expect(pdf.toString('latin1').match(/\/Type \/Page\b(?!s)/g) ?? [], 'one page').toHaveLength(1);
}

test('A1: a manual payment registered on the form reaches the resident: history, statement, notification and the PDF receipt by email', async ({
  admin: { page, api },
  seed
}) => {
  const charge = (await createCharge(api, {
    unit_id: seed.units[UNIT], concept: 'PENALTY', amount: '120000', due_date: addDays(seed.today, 10), description: DESCRIPTION
  })).id;

  // Before: the statement of Laura's apartment, as the administrator reads it (HU-011).
  await page.goto(`/admin/residents/${seed.laura_id}/account-statement`);
  await ready(page);
  await expect(page.locator('[data-account-badge]')).toHaveText('Pendiente');
  await expectTotals(page, {
    pending_balance: '$ 120.000,00', overdue_balance: '$ 0,00', credit_balance: '$ 0,00',
    total_charged: '$ 120.000,00', total_applied: '$ 0,00'
  });
  await expect(page.locator('[data-payment-rows] tr')).toHaveCount(0);
  const before = await receiptEmails(page.request, seed.resident_email);

  // 1. The administrator registers it on the real form (financial-flow.spec.ts), with a reference and notes.
  await page.goto('/admin/payments');
  const form = page.locator('[data-payment-form]');
  await form.getByLabel('Residente').fill('Laura');
  await form.getByRole('button', { name: /Laura Gómez/ }).click();
  await form.getByLabel('Monto').fill('50000');
  await form.getByLabel('Método de pago').selectOption({ label: 'Transferencia' });
  await form.getByLabel('Concepto', { exact: true }).selectOption({ label: 'Penalización' });
  await form.getByLabel('Referencia de transacción').fill(REFERENCE);
  await form.getByLabel('Notas').fill(NOTES);
  const [answer] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/admin/payments'),
    form.getByRole('button', { name: 'Registrar pago' }).click()
  ]);
  expect(answer.request().postDataJSON()).toMatchObject({
    resident_id: seed.laura_id, amount: '50000', method: 'TRANSFER', concept: 'PENALTY', payment_date: seed.today,
    transaction_reference: REFERENCE, notes: NOTES
  });
  expect(answer.status()).toBe(201);
  const payment = (await answer.json()) as Registered;
  expect(payment).toMatchObject({ transaction_reference: REFERENCE, notes: NOTES, receipt_email_status: 'SENT' });
  expect(payment.receipt_number).toMatch(/^RC-\d{8}$/);

  // 2. Registered, with the unit's new balance; the receipt was emailed (no warning).
  const confirmation = page.locator('[data-confirmation]');
  await expect(confirmation).toContainText('Pago registrado correctamente.');
  await expect(definition(confirmation, 'Recibo')).toHaveText(payment.receipt_number);
  expect(await text(definition(confirmation, 'Residente'))).toBe(`Laura Gómez · ${UNIT}`);
  expect(await text(definition(confirmation, 'Monto'))).toBe('$ 50.000,00');
  expect(await text(definition(confirmation, 'Valor aplicado'))).toBe('$ 50.000,00');
  await expect(definition(confirmation, 'Estado del pago')).toHaveText('Aplicado');
  expect(await text(definition(confirmation, 'Saldo pendiente de la unidad'))).toBe('$ 70.000,00');
  expect(await text(definition(confirmation, 'Saldo a favor de la unidad'))).toBe('$ 0,00');
  await expect(definition(confirmation, 'Referencia de transacción')).toHaveText(REFERENCE);
  expect(await definition(confirmation, 'Notas').textContent()).toBe(NOTES);
  await expect(page.locator('[data-email-warning]')).toBeHidden();

  // Persisted: the summary after a reload and the obligation it covered.
  await page.reload();
  const summaryRow = page.locator('[data-rows] tr').filter({ hasText: payment.receipt_number });
  await expect(summaryRow).toHaveCount(1);
  await expect(summaryRow).toContainText('Laura Gómez');
  await expect(summaryRow).toContainText('Aplicado');
  await page.goto(`/admin/obligations/${charge}`);
  const information = (label: string) => definition(page.getByRole('region', { name: 'Información' }), label);
  expect(await text(information('Aplicado'))).toBe('$ 50.000,00');
  expect(await text(information('Saldo'))).toBe('$ 70.000,00');
  await expect(information('Estado')).toHaveText('Parcial');
  const applications = page.getByRole('region', { name: 'Pagos aplicados' }).locator('tbody tr');
  await expect(applications).toHaveCount(1);
  await expect(applications.getByRole('rowheader')).toHaveText(payment.receipt_number);

  // 3. The resident signs in on the login page (the administrator's session is gone first).
  await page.context().clearCookies();
  await page.goto('/login');
  await page.getByLabel('Correo electrónico').fill(seed.resident_email);
  await page.getByLabel('Contraseña').fill(residentPassword());
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/\/resident$/);

  // "Mis pagos" (resident-payments.spec.ts): the payment, then its detail with the reference and the notes.
  const menu = page.getByRole('navigation', { name: 'Principal' });
  await menu.getByRole('link', { name: 'Mis pagos' }).click();
  await expect(page).toHaveURL(/\/resident\/payments$/);
  await expect(page.locator('[data-status-line]')).not.toHaveText('Consultando tus pagos...');
  await expect(page.locator('[data-rows] tr')).toHaveCount(1);
  await page.getByRole('link', { name: `Ver detalle del pago ${payment.receipt_number} del ${longDate(seed.today)}` }).click();
  await expect(page).toHaveURL(`/resident/payments/${payment.id}`);
  await expect(page.getByText(/^Recibo RC-/)).toHaveText(`Recibo ${payment.receipt_number}`);
  await expect(page.getByRole('main').locator('p', { hasText: /^Estado: \S/ })).toHaveText('Estado: Aplicado');
  const info = (label: string) => definition(page.getByRole('region', { name: 'Información del pago' }), label);
  await expect(info('Fecha del pago')).toHaveText(longDate(seed.today));
  await expect(info('Concepto')).toHaveText('Penalización');
  expect(await text(info('Monto'))).toBe('$ 50.000,00');
  await expect(info('Método')).toHaveText('Transferencia');
  await expect(info('Unidad')).toHaveText(UNIT);
  await expect(info('Número de recibo')).toHaveText(payment.receipt_number);
  await expect(info('Referencia de transacción')).toHaveText(REFERENCE);
  expect(await page.getByRole('region', { name: 'Nota de la administración' }).locator('p').last().innerText()).toBe(NOTES);
  const result = (label: string) => definition(page.getByRole('region', { name: 'Resultado del pago' }), label);
  expect(await text(result('Monto pagado'))).toBe('$ 50.000,00');
  expect(await text(result('Monto aplicado'))).toBe('$ 50.000,00');
  expect(await text(result('Saldo a favor'))).toBe('$ 0,00');
  const covered = page.getByRole('region', { name: 'Obligaciones cubiertas' }).locator('tbody tr');
  await expect(covered).toHaveCount(1);
  await expect(covered.getByRole('rowheader')).toHaveText(`Penalización: ${DESCRIPTION}`);
  expect(await text(covered.getByRole('cell').nth(3))).toBe('$ 50.000,00');

  // 4. The statement: 120.000 owed before, 70.000 now; the penalty partly paid by this payment.
  await menu.getByRole('link', { name: 'Estado de cuenta' }).click();
  await expect(page).toHaveURL(/\/resident\/account-statement$/);
  await ready(page);
  await expect(page.locator('[data-unit]')).toHaveText(UNIT);
  await expect(page.locator('[data-account-badge]')).toHaveText('Pendiente');
  await expectTotals(page, {
    pending_balance: '$ 70.000,00', overdue_balance: '$ 0,00', credit_balance: '$ 0,00',
    total_charged: '$ 120.000,00', total_applied: '$ 50.000,00'
  });
  const pending = page.locator('[data-section="pending"] [data-rows] tr');
  await expect(pending).toHaveCount(1);
  await expect(cell(pending, 'Obligación')).toHaveText(`Penalización: ${DESCRIPTION}`);
  expect(await text(cell(pending, 'Valor'))).toBe('$ 120.000,00');
  expect(await text(cell(pending, 'Abonado'))).toBe('$ 50.000,00');
  expect(await text(cell(pending, 'Saldo'))).toBe('$ 70.000,00');
  await expect(cell(pending, 'Estado')).toHaveText('Parcial');
  const paid = page.locator('[data-payment-rows] tr');
  await expect(paid).toHaveCount(1);
  await expect(cell(paid, 'Recibo')).toHaveText(payment.receipt_number);
  expect(await text(cell(paid, 'Valor'))).toBe('$ 50.000,00');
  await expect(cell(paid, 'Estado')).toHaveText('Aplicado');

  // 5. The notification of HU-017: this receipt, emailed, linked to the payment.
  await menu.getByRole('link', { name: 'Notificaciones' }).click();
  await expect(page).toHaveURL(/\/resident\/notifications$/);
  await expect(page.locator('[data-status-line]')).not.toHaveText('Consultando tus notificaciones...');
  const notification = page.locator('[data-items] > li').filter({ has: page.getByRole('heading', { name: 'Pago registrado', exact: true }) });
  await expect(notification).toHaveCount(1);
  expect(await text(notification)).toContain(`con el recibo ${payment.receipt_number}`);
  await expect(notification).toContainText('$ 50.000,00');
  await expect(notification.locator('span.rounded-full')).toHaveText('Enviada a tu correo');
  await expect(notification.getByRole('link', { name: /^Ver el pago/ })).toHaveAttribute('href', `/resident/payments/${payment.id}`);

  // 6. The email in Mailpit: the one new receipt to Laura, with this payment's PDF attached.
  const email = await newReceiptEmail(page.request, seed.resident_email, payment.receipt_number, before);
  expect(email.To.map((to) => to.Address)).toEqual([seed.resident_email]);
  expect(email.Subject).toBe(`Recibo de pago ${payment.receipt_number} — SoftPh-Académico`);
  expect(email.Text).toContain(`Adjuntamos el recibo ${payment.receipt_number} en formato PDF.`);
  expect(email.Attachments).toHaveLength(1);
  const [attachment] = email.Attachments;
  expect([attachment.FileName, attachment.ContentType]).toEqual([`recibo-${payment.receipt_number}.pdf`, 'application/pdf']);

  const part = await page.request.get(`${MAILPIT}/message/${email.ID}/part/${attachment.PartID}`);
  expect(part.status()).toBe(200);
  const pdf = await part.body();
  // The bytes Mailpit stored, complete.
  expect(pdf.length).toBe(attachment.Size);
  expect(createHash('sha256').update(pdf).digest('hex')).toBe(attachment.Checksums.SHA256);
  expectValidPdf(pdf);
  // This payment's receipt (uncompressed text, services/receipts.py).
  const content = pdf.toString('latin1');
  expect(content).toContain(`Recibo de pago ${payment.receipt_number}`);
  expect(content).toContain(UNIT);
  expect(content).toContain('$ 50.000,00');
  expect(content).toContain('Transferencia');
});
