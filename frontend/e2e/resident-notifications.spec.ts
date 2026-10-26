import type { Page } from '@playwright/test';
import { addDays, type Seed } from './support/data';
import { createCharge, expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-017 "Notificación al residente", in a real browser: /resident/notifications as Laura Gómez
 * (RESIDENTE), against the real API on the *_test database (playwright.config.ts).
 *
 * The events are produced by the real flows: the administrator responds and resolves a feedback
 * (HU-005) and registers a manual payment (HU-004); Laura pays with the internal demo gateway
 * (HU-010). Email goes to the configured development sandbox (Mailpit, example.com addresses):
 * its read-only API is only asked whether a receipt email exists. Whether a manual receipt
 * reaches the sandbox depends on it being up, so the email status shown is the backend's.
 */

const MAILPIT = 'http://127.0.0.1:8025/api/v1';

const items = (page: Page) => page.locator('[data-items] > li');
const item = (page: Page, title: string) => items(page).filter({ has: page.getByRole('heading', { name: title, exact: true }) });

async function open(page: Page): Promise<void> {
  await page.goto('/resident/notifications');
  await expect(page.locator('[data-status-line]')).not.toHaveText('Consultando tus notificaciones...');
}

interface Notification {
  id: number;
  event_type: string;
  entity_type: string;
  entity_id: number;
  title: string;
  message: string;
  email_status: string;
}

async function myNotifications(api: Api): Promise<Notification[]> {
  return (await api.json<{ items: Notification[] }>(await api.get('/resident/notifications?page_size=50'))).items;
}

/** Laura's feedback (HU-009), as she sends it. */
async function sendFeedback(resident: Api, subject: string): Promise<number> {
  const sent = await resident.json<{ id: number }>(
    await resident.post('/resident/feedback', { subject, message: 'Mensaje de prueba E2E.', category: 'REQUEST', urgency: 'LOW' }),
    201
  );
  return sent.id;
}

async function payManually(admin: Api, seed: Seed, amount: string): Promise<{ id: number; receipt_number: string; receipt_email_status: string }> {
  return admin.json(
    await admin.post('/admin/payments', { resident_id: seed.laura_id, amount, method: 'CASH', concept: 'MONTHLY_FEE', payment_date: seed.today }),
    201
  );
}

/** Receipt emails in the sandbox with that receipt number in the subject (read only). */
async function receiptEmails(page: Page, receipt: string): Promise<number> {
  const response = await page.request.get(`${MAILPIT}/search?query=${encodeURIComponent(`subject:"${receipt}"`)}`);
  expect(response.status(), 'the development mail sandbox (Mailpit)').toBe(200);
  return ((await response.json()) as { messages_count: number }).messages_count;
}

async function startDemo(resident: Api, charge: number, amount: string): Promise<number> {
  return (await resident.json<{ id: number }>(await resident.post('/resident/demo-payments', { charge_id: charge, amount }), 201)).id;
}

const complete = (resident: Api, transaction: number, instrument: string) =>
  resident.post(`/resident/demo-payments/${transaction}/complete`, { instrument_id: instrument });

test.describe('resident notifications', () => {
  test('reached from the panel and the header (aria-current); without events, the empty message', async ({ page, signIn }) => {
    await signIn('resident');
    await page.goto('/resident');
    await page.getByRole('main').getByRole('link', { name: /Notificaciones/ }).click();
    await expect(page).toHaveURL(/\/resident\/notifications$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Notificaciones' })).toBeVisible();
    const menu = page.getByRole('navigation', { name: 'Principal' });
    await expect(menu.getByRole('link', { name: 'Notificaciones' })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('[data-status-line]')).toHaveText('0 notificaciones');
    await expect(page.locator('[data-empty]')).toContainText('No tienes notificaciones');

    await page.goto('/resident/payments');
    await menu.getByRole('link', { name: 'Notificaciones' }).click();
    await expect(page).toHaveURL(/\/resident\/notifications$/);
  });

  test('a response and then a resolution: one notification each, for the feedback\'s resident only, without the internal note', async ({ page, seed, signIn }) => {
    const laura = await signIn('resident');
    const feedback = await sendFeedback(laura, 'Portón E2E');
    const admin = await signIn('admin');
    // Pedro's feedback is answered too: never shown to Laura.
    await admin.json(await admin.post(`/admin/feedback/${seed.feedback.pedro}/response`, { response: 'Respuesta para Pedro E2E.', version: 1 }));

    await page.goto(`/admin/feedback/${feedback}`);
    await page.getByRole('textbox', { name: 'Respuesta' }).fill('Revisaremos el portón mañana.');
    await page.getByRole('button', { name: 'Guardar respuesta' }).click();
    await expect(page.locator('[data-response-text]')).toHaveText('Revisaremos el portón mañana.');
    await page.getByRole('textbox', { name: 'Nota de resolución' }).fill('Nota interna reservada E2E.');
    await page.locator('[data-resolve-form]').getByRole('button', { name: 'Marcar como resuelto' }).click();
    await page.getByRole('dialog', { name: '¿Marcar este feedback como resuelto?' }).getByRole('button', { name: 'Marcar como resuelto' }).click();
    await expect(page.locator('[data-status-badge]')).toHaveText('Resuelto');

    await signIn('resident');
    await open(page);
    await expect(page.locator('[data-status-line]')).toHaveText('Mostrando 1–2 de 2 notificaciones');
    // Newest first: the resolution, then the response; each names its event and links to the feedback.
    await expect(items(page).nth(0).getByRole('heading')).toHaveText('Feedback resuelto');
    await expect(items(page).nth(1).getByRole('heading')).toHaveText('Respuesta a tu feedback');
    const resolved = item(page, 'Feedback resuelto');
    await expect(resolved).toContainText('Feedback resuelto ·');
    await expect(resolved).toContainText('La administración revisó tu feedback «Portón E2E» y lo marcó como resuelto.');
    await expect(item(page, 'Respuesta a tu feedback')).toContainText('«Portón E2E» y te respondió.');
    for (const notification of [resolved, item(page, 'Respuesta a tu feedback')]) {
      await expect(notification.getByRole('link', { name: /^Ver el feedback/ })).toHaveAttribute('href', `/resident/feedback/${feedback}`);
      await expect(notification.locator('span.rounded-full')).toHaveText(/Enviada a tu correo|No se pudo enviar a tu correo/);
    }
    const shown = await page.getByRole('main').textContent();
    for (const hidden of ['Nota interna reservada E2E.', 'Agua en la torre E2E', 'Respuesta para Pedro E2E.']) {
      expect(shown).not.toContain(hidden);
    }
    // The link opens the resident's own feedback.
    await item(page, 'Respuesta a tu feedback').getByRole('link', { name: /^Ver el feedback/ }).click();
    await expect(page).toHaveURL(new RegExp(`/resident/feedback/${feedback}$`));
  });

  test('a manual payment: the notification names its receipt, links to the payment, and the receipt follows HU-004', async ({ page, seed, admin, signIn }) => {
    const payment = await payManually(admin.api, seed, '120000');

    await signIn('resident');
    await open(page);
    const notification = item(page, 'Pago registrado');
    await expect(notification).toHaveCount(1);
    expect(normalized(await notification.textContent())).toContain(`con el recibo ${payment.receipt_number}`);
    await expect(notification).toContainText('$ 120.000,00');
    const badge = notification.locator('span.rounded-full');
    // The receipt email of HU-004 and the notification share one result.
    if (payment.receipt_email_status === 'SENT') {
      await expect(badge).toHaveText('Enviada a tu correo');
      expect(await receiptEmails(page, payment.receipt_number)).toBeGreaterThanOrEqual(1);
    } else {
      await expect(badge).toHaveText('No se pudo enviar a tu correo');
    }
    await notification.getByRole('link', { name: /^Ver el pago/ }).click();
    await expect(page).toHaveURL(new RegExp(`/resident/payments/${payment.id}$`));
    await expect(page.getByRole('main')).toContainText(payment.receipt_number);
  });

  test('nobody else\'s notifications: unknown parameters are refused and the page URL selects nobody', async ({ page, seed, signIn }) => {
    const admin = await signIn('admin');
    await admin.json(await admin.post(`/admin/feedback/${seed.feedback.pedro}/response`, { response: 'Respuesta para Pedro E2E.', version: 1 }));
    const laura = await signIn('resident');

    for (const query of [`resident_id=${seed.laura_id + 1}`, `user_id=${seed.laura_id + 1}`, 'page=0']) {
      const response = await laura.get(`/resident/notifications?${query}`);
      expect(response.status(), query).toBe(422);
      expect(await response.text()).not.toContain('Pedro');
    }
    expect(await myNotifications(laura)).toEqual([]);
    // The page ignores anything but page and page_size, and still lists only Laura's (none).
    await page.goto(`/resident/notifications?resident_id=${seed.laura_id + 1}`);
    await expect(page.locator('[data-status-line]')).toHaveText('0 notificaciones');
    await expect(page.getByRole('main')).not.toContainText('Agua en la torre E2E');
    // The administrator never reads a resident's notifications.
    const asAdmin = await signIn('admin');
    expect((await asAdmin.get('/resident/notifications')).status()).toBe(403);
  });

  test('an approved demo payment appears in the panel without email; rejected, failed, cancelled and not applied ones never do', async ({ page, seed, admin, signIn }) => {
    const unit = seed.units['B1-TO1-APTO-101'];
    // The earliest obligation is the one a manual payment covers first (FIFO, HU-004).
    const early = await createCharge(admin.api, { unit_id: unit, concept: 'PENALTY', amount: '40000', due_date: addDays(seed.today, 5), description: 'Parqueo E2E' });
    const fee = await createCharge(admin.api, { unit_id: unit, concept: 'PENALTY', amount: '90000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });
    let laura = await signIn('resident');

    // Rejected, technical error and cancelled: no payment, no notification.
    for (const instrument of ['TEST_CARD_INSUFFICIENT_FUNDS', 'TEST_ACCOUNT_TECHNICAL_ERROR']) {
      const result = await laura.json<{ status: string; payment_id: number | null }>(await complete(laura, await startDemo(laura, fee.id, '30000'), instrument));
      expect([result.status, result.payment_id]).toEqual([instrument === 'TEST_CARD_INSUFFICIENT_FUNDS' ? 'REJECTED' : 'FAILED', null]);
    }
    await laura.json(await laura.post(`/resident/demo-payments/${await startDemo(laura, fee.id, '30000')}/cancel`, {}));
    // Approved by the gateway but no longer applicable (the administrator covered it meanwhile): nothing paid.
    const late = await startDemo(laura, early.id, '40000');
    const manual = await payManually(await signIn('admin'), seed, '40000');
    laura = await signIn('resident');
    expect((await complete(laura, late, 'TEST_CARD_APPROVE')).status()).toBe(409);
    expect((await myNotifications(laura)).map((n) => n.entity_id)).toEqual([manual.id]);

    // Approved and applied: one notification, in the panel only.
    const approved = await laura.json<{ status: string; payment_id: number; receipt_number: string }>(
      await complete(laura, await startDemo(laura, fee.id, '30000'), 'TEST_CARD_APPROVE'));
    expect(approved.status).toBe('APPROVED');
    expect((await myNotifications(laura)).map((n) => [n.entity_id, n.email_status])).toEqual([
      [approved.payment_id, 'NOT_REQUIRED'], [manual.id, manual.receipt_email_status]
    ]);

    await open(page);
    await expect(page.locator('[data-status-line]')).toHaveText('Mostrando 1–2 de 2 notificaciones');
    const demo = item(page, 'Pago de demostración registrado');
    await expect(demo).toHaveCount(1);
    await expect(demo.locator('span.rounded-full')).toHaveText('Solo en el panel (sin correo)');
    expect(normalized(await demo.textContent())).toContain(`con el comprobante ${approved.receipt_number}`);
    await expect(demo).toContainText('no se envía por correo');
    await expect(demo.getByRole('link', { name: /^Ver el pago/ })).toHaveAttribute('href', `/resident/payments/${approved.payment_id}`);
    // HU-010 unchanged: no receipt email of the demo payment in the sandbox.
    expect(await receiptEmails(page, approved.receipt_number)).toBe(0);
  });
});

test.describe('360 × 800', () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

  test('the notifications: one column, readable items, no horizontal scroll', async ({ page, seed, admin, signIn }) => {
    await payManually(admin.api, seed, '75000');
    await sendFeedback(await signIn('resident'), 'Asunto largo para comprobar el ajuste de línea en pantallas pequeñas E2E');
    await open(page);
    await expect(items(page)).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth)).toBe(true);
    const box = await items(page).first().boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);
    // The header menu reaches the page too (on a narrow screen, behind its button).
    await page.goto('/resident');
    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await page.getByRole('navigation', { name: 'Principal' }).getByRole('link', { name: 'Notificaciones' }).click();
    await expect(page).toHaveURL(/\/resident\/notifications$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth)).toBe(true);
  });
});
