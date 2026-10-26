import type { APIRequestContext, Page } from '@playwright/test';
import { expect, normalized, test } from './support/fixtures';

/**
 * RF-003 (the administrator invites a resident) and RF-019 (the resident activates the account), in a real
 * browser on the E2E stack (*_test database). The invitation email goes to the development sandbox (Mailpit),
 * as configured; the activation link is read from it, by its unique recipient, without deleting any message.
 * Without Mailpit the activation test is skipped (it says so) instead of faking the email.
 *
 * Not covered here: an expired link (RF-019 criterion 7). An invitation lasts at least one day and the seed has
 * no expired one, so it cannot be reached without changing data or the clock; the API tests cover it.
 */

const MAILPIT = 'http://127.0.0.1:8025';
const INVITE_PATH = '/admin/residents/invite';
const INVITATIONS_API = '/api/v1/resident-invitations';
const NEW_PASSWORD = 'clave de activación E2E 2026';

/** A recipient no other run has used, so the sandbox holds exactly this test's email. */
const uniqueEmail = (prefix: string) => `${prefix}.${Date.now()}@example.com`;

const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });
const fieldError = (page: Page, name: string) => page.locator(`[data-error-for="${name}"]`);
const formAlert = (page: Page) => page.locator('[data-form-alert]');

async function fillInvitation(page: Page, data: { email: string; name: string; unit: string; days?: string }): Promise<void> {
  await field(page, 'Correo electrónico').fill(data.email);
  await field(page, 'Nombre completo').fill(data.name);
  await field(page, 'Apartamento').selectOption({ label: data.unit });
  await field(page, 'Vigencia de la invitación').fill(data.days ?? '7');
  await page.getByRole('button', { name: 'Enviar invitación' }).click();
}

/** Every POST the page sends to create an invitation. */
function invitationPosts(page: Page): string[] {
  const posts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === INVITATIONS_API) posts.push(request.url());
  });
  return posts;
}

async function mailpitAvailable(request: APIRequestContext): Promise<boolean> {
  try {
    return (await request.get(`${MAILPIT}/api/v1/info`, { timeout: 3000 })).ok();
  } catch {
    return false;
  }
}

/** The activation link of the only email sent to `recipient` (read-only use of the Mailpit API). */
async function activationLink(request: APIRequestContext, recipient: string): Promise<string> {
  const query = encodeURIComponent(`to:"${recipient}"`);
  let id = '';
  await expect.poll(async () => {
    const found = (await (await request.get(`${MAILPIT}/api/v1/search?query=${query}`)).json()) as { messages: { ID: string }[] };
    id = found.messages[0]?.ID ?? '';
    return found.messages.length;
  }, { message: `email to ${recipient} in Mailpit` }).toBe(1);
  const message = (await (await request.get(`${MAILPIT}/api/v1/message/${id}`)).json()) as { Text: string; To: { Address: string }[] };
  expect(message.To.map((to) => to.Address)).toEqual([recipient]);
  const link = message.Text.match(/https?:\/\/\S+\/activate-account#token=[A-Za-z0-9_-]+/)?.[0];
  expect(link, 'activation link in the email').toBeTruthy();
  return link!;
}

test.describe('RF-003: the invitation form', () => {
  test('reached from the panel; only available apartments; empty or invalid data is refused next to each field', async ({ admin: { page } }) => {
    const posts = invitationPosts(page);
    await page.goto('/admin');
    await page.getByRole('main').getByRole('link', { name: 'Invitar residente', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${INVITE_PATH}$`));
    await expect(page.getByRole('heading', { level: 1, name: 'Invitar residente' })).toBeVisible();

    // Criterion 2: Laura (101) and Pedro (102) live in their apartments; 103 is free.
    const options = await field(page, 'Apartamento').locator('option').allTextContents();
    expect(options).not.toContain('B1-TO1-APTO-101');
    expect(options).not.toContain('B1-TO1-APTO-102');
    expect(options).toContain('B1-TO1-APTO-103');

    // Criterion 8: nothing filled in (the validity keeps its default): a message per field, nothing sent.
    await page.getByRole('button', { name: 'Enviar invitación' }).click();
    await expect(fieldError(page, 'email')).toHaveText('Ingresa el correo electrónico del residente.');
    await expect(fieldError(page, 'full_name')).toHaveText('Ingresa el nombre completo del residente.');
    await expect(fieldError(page, 'unit_id')).toHaveText('Selecciona un apartamento disponible.');
    await expect(field(page, 'Correo electrónico')).toBeFocused();
    await expect(field(page, 'Correo electrónico')).toHaveAttribute('aria-invalid', 'true');

    // Criterion 3: invalid email and validity out of range, checked before sending.
    await fillInvitation(page, { email: 'sin-arroba', name: 'Valentina Ríos', unit: 'B1-TO1-APTO-103', days: '31' });
    await expect(fieldError(page, 'email')).toHaveText('Ingresa un correo electrónico válido.');
    await expect(fieldError(page, 'validity_days')).toHaveText('La vigencia debe estar entre 1 y 30 días.');
    await expect(fieldError(page, 'full_name')).toBeHidden();
    expect(posts).toEqual([]);

    // The server's own rule (the name's characters) also lands next to its field; nothing is created.
    await fillInvitation(page, { email: uniqueEmail('invalida.e2e'), name: 'Valentina 123', unit: 'B1-TO1-APTO-103' });
    await expect(fieldError(page, 'full_name')).toHaveText('El nombre completo solo puede contener letras, espacios, apóstrofos y guiones.');
    expect(posts).toHaveLength(1);
    await expect(page).toHaveURL(new RegExp(`${INVITE_PATH}$`));
    await expect(page.getByText('No hay invitaciones pendientes.')).toBeVisible();
  });

  test('criterion 4: an email that already has an account is refused with the reason; nothing is created', async ({ admin: { page, api }, seed }) => {
    await page.goto(INVITE_PATH);
    await fillInvitation(page, { email: seed.resident_email, name: 'Laura Gómez', unit: 'B1-TO1-APTO-103' });
    await expect(formAlert(page)).toHaveText('El correo electrónico ya está registrado.');
    await expect(page).toHaveURL(new RegExp(`${INVITE_PATH}$`));

    const pending = await api.json<{ items: unknown[] }>(await api.get('/resident-invitations?status=PENDING'));
    expect(pending.items).toEqual([]);
    const available = await api.json<{ items: { label: string }[] }>(await api.get('/units/available'));
    expect(available.items.map((unit) => unit.label)).toContain('B1-TO1-APTO-103');
  });
});

test.describe('RF-003 and RF-019: from the invitation to the first sign-in', () => {
  test('confirmation and email; the link shows the invitation, activates the account, then signs in; it cannot be used again', async ({ admin: { page }, browser }) => {
    test.skip(!(await mailpitAvailable(page.request)), `Mailpit is not reachable at ${MAILPIT}: the invitation email cannot be read`);
    const email = uniqueEmail('invitada.e2e');
    const name = 'Valentina Ríos';

    // RF-003 criteria 5 to 7: created, sent and confirmed; the apartment is reserved.
    await page.goto(INVITE_PATH);
    await fillInvitation(page, { email, name, unit: 'B2-TO1-APTO-203', days: '7' });
    await expect(page).toHaveURL(/\/admin\/residents\/invite\?invitation=\d+$/);
    const status = page.getByRole('status');
    await expect(status).toContainText(`Invitación enviada a ${email}`);
    expect(normalized(await status.textContent())).toMatch(/B2-TO1-APTO-203\. Vence el .+ \(hora de Colombia\)\./);
    const pendingRow = page.getByRole('region', { name: 'Invitaciones pendientes' }).getByRole('row').filter({ hasText: email });
    await expect(pendingRow).toContainText('B2-TO1-APTO-203');
    expect(await field(page, 'Apartamento').locator('option').allTextContents()).not.toContain('B2-TO1-APTO-203');

    // RF-003 criterion 6: the email with its link reached the sandbox.
    const link = await activationLink(page.request, email);

    // RF-019, in a browser without any session (the resident's).
    const context = await browser.newContext();
    try {
      const resident = await context.newPage();

      // Criterion 6.2 needs the activation first: before it, the same email cannot sign in.
      await resident.goto(new URL('/login', link).toString());
      await field(resident, 'Correo electrónico').fill(email);
      await field(resident, 'Contraseña').fill(NEW_PASSWORD);
      await resident.getByRole('button', { name: 'Ingresar' }).click();
      await expect(resident.getByRole('alert')).toHaveText('Correo electrónico o contraseña no válidos.');

      // Criterion 4: the link, within its validity, shows the invitation and the form.
      await resident.goto(link);
      await expect(resident).toHaveURL(/\/activate-account$/); // the token leaves the address bar
      await expect(resident.locator('[data-field="full_name"]')).toHaveText(name);
      await expect(resident.locator('[data-field="email"]')).toHaveText(email);
      await expect(resident.locator('[data-field="unit_code"]')).toHaveText('B2-TO1-APTO-203');
      await expect(resident.locator('[data-field="expires_at"]')).toContainText('(hora de Colombia)');

      // Criteria 5 and 6: document, password and confirmation; the account is active.
      await field(resident, 'Número de documento').fill('1098765432');
      await field(resident, 'Contraseña').fill(NEW_PASSWORD);
      await field(resident, 'Confirmar contraseña').fill(NEW_PASSWORD);
      await resident.getByRole('button', { name: 'Activar mi cuenta' }).click();
      await expect(resident.getByRole('status')).toContainText('¡Tu cuenta está activa!');

      // Criterion 6.2 (with RF-007): the new credentials open the resident's panel, in the apartment invited to.
      await resident.getByRole('link', { name: 'Iniciar sesión' }).click();
      await expect(resident).toHaveURL(/\/login$/);
      await field(resident, 'Correo electrónico').fill(email);
      await field(resident, 'Contraseña').fill(NEW_PASSWORD);
      await resident.getByRole('button', { name: 'Ingresar' }).click();
      await expect(resident).toHaveURL(/\/resident$/);
      await expect(resident.getByRole('heading', { level: 1 })).toHaveText(`Hola, ${name}`);
      const statement = await resident.request.get('/api/v1/resident/account-statement');
      expect(((await statement.json()) as { resident: { unit: { label: string } } }).resident.unit.label).toBe('B2-TO1-APTO-203');

      // Criterion 8: the same link again is refused.
      await resident.goto(link);
      await expect(resident.getByText('Este enlace ya fue utilizado')).toBeVisible();
      await expect(resident.getByRole('button', { name: 'Activar mi cuenta' })).toBeHidden();
    } finally {
      await context.close();
    }

    // The administrator's side: no longer pending.
    await page.goto(INVITE_PATH);
    await expect(page.getByRole('region', { name: 'Invitaciones pendientes' }).getByRole('row').filter({ hasText: email })).toHaveCount(0);
  });
});
