import type { APIRequestContext, Page, Response } from '@playwright/test';
import { residentPassword } from './support/data';
import { expect, test } from './support/fixtures';

/**
 * RF-016 (HU-016): a resident recovers the access, in a real browser on the E2E stack (*_test database).
 * The email goes to the development sandbox (Mailpit), as configured; the link is read from it without deleting
 * any message. The seed's resident (Laura) has a fixed address, so the test notes the reset emails already in
 * the sandbox and waits for a new one. Without Mailpit the test is skipped (it says so) instead of faking it.
 *
 * Not covered here: an expired link (one hour) and a link replaced by a newer request; the API tests cover them.
 */

const MAILPIT = 'http://127.0.0.1:8025';
const SUBJECT = 'Restablece tu contraseña de SoftPh-Académico';
const REQUEST_API = '/api/v1/auth/password-reset/request';
const RECEIVED =
  'Si la cuenta corresponde a un residente registrado, recibirás en tu correo un enlace para restablecer la contraseña. El enlace vence en 1 hora.';
const NEW_PASSWORD = 'nueva clave segura E2E 2026';
const GENERIC_LOGIN_ERROR = 'Correo electrónico o contraseña no válidos.';

const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });

async function mailpitAvailable(request: APIRequestContext): Promise<boolean> {
  try {
    return (await request.get(`${MAILPIT}/api/v1/info`, { timeout: 3000 })).ok();
  } catch {
    return false;
  }
}

/** The ids of the reset emails to `recipient` in the sandbox, newest first (read-only). */
async function resetEmails(request: APIRequestContext, recipient: string): Promise<string[]> {
  const query = encodeURIComponent(`to:"${recipient}" subject:"${SUBJECT}"`);
  const found = (await (await request.get(`${MAILPIT}/api/v1/search?query=${query}`)).json()) as { messages: { ID: string }[] };
  return found.messages.map((message) => message.ID);
}

/** The reset link of the one new email to `recipient` (not among `before`). */
async function newResetLink(request: APIRequestContext, recipient: string, before: string[]): Promise<string> {
  let fresh: string[] = [];
  await expect.poll(async () => {
    fresh = (await resetEmails(request, recipient)).filter((id) => !before.includes(id));
    return fresh.length;
  }, { message: `a new reset email to ${recipient} in Mailpit` }).toBe(1);
  const message = (await (await request.get(`${MAILPIT}/api/v1/message/${fresh[0]}`)).json()) as { Text: string; To: { Address: string }[] };
  expect(message.To.map((to) => to.Address)).toEqual([recipient]);
  const link = message.Text.match(/https?:\/\/\S+\/reset-password#token=[A-Za-z0-9_-]+/)?.[0];
  expect(link, 'reset link in the email').toBeTruthy();
  return link!;
}

/** Sends the request form; returns what the API answered and what the page shows. */
async function requestReset(page: Page, email: string): Promise<{ status: number; body: string; shown: string }> {
  await page.goto('/forgot-password');
  await field(page, 'Correo electrónico').fill(email);
  const [response]: [Response, void] = await Promise.all([
    page.waitForResponse((answer) => new URL(answer.url()).pathname === REQUEST_API && answer.request().method() === 'POST'),
    page.getByRole('button', { name: 'Enviar enlace' }).click()
  ]);
  const received = page.locator('[data-received]');
  await expect(received).toContainText('Revisa tu correo');
  return { status: response.status(), body: await response.text(), shown: (await received.locator('[data-received-message]').textContent()) ?? '' };
}

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login');
  await field(page, 'Correo electrónico').fill(email);
  await field(page, 'Contraseña').fill(password);
  await page.getByRole('button', { name: 'Ingresar' }).click();
}

test('RF-016: from the login link to a new password; the same answer for any email; the link works once', async ({ page, seed }) => {
  test.skip(!(await mailpitAvailable(page.request)), `Mailpit is not reachable at ${MAILPIT}: the reset email cannot be read`);
  const unknown = `no-registrado.${Date.now()}@example.com`; // no account, never used before
  const before = await resetEmails(page.request, seed.resident_email);

  // Criterion 1: the login offers the recovery, which asks for the account's email.
  await page.goto('/login');
  await page.getByRole('link', { name: '¿Olvidaste tu contraseña?' }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Recuperar contraseña' })).toBeVisible();
  await expect(field(page, 'Correo electrónico')).toBeVisible();

  // Criteria 2 and 3 (approved anti-enumeration reading): the same answer for an unknown and a registered email.
  const forUnknown = await requestReset(page, unknown);
  const forResident = await requestReset(page, seed.resident_email);
  expect(forUnknown.status).toBe(202);
  expect(forResident).toEqual(forUnknown);
  expect(forResident.shown).toBe(RECEIVED);

  // Only the registered resident gets an email with the link; the unknown address gets none.
  const link = await newResetLink(page.request, seed.resident_email, before);
  expect(await resetEmails(page.request, unknown)).toEqual([]);

  // Criterion 4: the link opens the form; a password outside the policy is refused and nothing changes.
  await page.goto(link);
  await expect(page).toHaveURL(/\/reset-password$/); // the token leaves the address bar
  await expect(page.getByRole('heading', { level: 1, name: 'Restablecer contraseña' })).toBeVisible();
  await field(page, 'Nueva contraseña').fill('corta');
  await field(page, 'Confirmar nueva contraseña').fill('corta');
  await page.getByRole('button', { name: 'Actualizar contraseña' }).click();
  await expect(page.locator('[data-error-for="password"]')).toHaveText(/12 y 128 caracteres/);

  // Criterion 5: a valid password is saved and confirmed.
  await field(page, 'Nueva contraseña').fill(NEW_PASSWORD);
  await field(page, 'Confirmar nueva contraseña').fill(NEW_PASSWORD);
  await page.getByRole('button', { name: 'Actualizar contraseña' }).click();
  const done = page.locator('[data-view="done"]');
  await expect(done).toContainText('Tu contraseña fue actualizada correctamente.');

  // The link works once. (Leave the page first: on /reset-password itself, only the #fragment would change and
  // the browser would not load the page again.)
  await page.goto('/login');
  await page.goto(link);
  await expect(page.getByText('Este enlace ya fue utilizado')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Actualizar contraseña' })).toBeHidden();

  // Criterion 5 (sign in again): the old password no longer works; the new one opens the resident's panel.
  await signIn(page, seed.resident_email, residentPassword());
  await expect(page.getByRole('alert')).toHaveText(GENERIC_LOGIN_ERROR);
  await expect(page).toHaveURL(/\/login$/);
  await signIn(page, seed.resident_email, NEW_PASSWORD);
  await expect(page).toHaveURL(/\/resident$/);
});
