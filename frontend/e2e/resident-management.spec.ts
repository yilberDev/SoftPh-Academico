import type { Page, Request } from '@playwright/test';
import { E2E_ORIGIN } from '../playwright.config';
import { addDays } from './support/data';
import { createCharge, expect, test, type Api } from './support/fixtures';

/**
 * HU-015 "Actualización y eliminación de residentes", in a real browser as the administrator, against the
 * real API on the *_test database (playwright.config.ts).
 *
 * Every test starts from the seeded data (backend/tests/e2e/seed.py), so changing or deleting a resident
 * here never touches another test: Pedro Ruiz (B1-TO1-APTO-102, nothing owed or paid: deletable) and
 * Laura Gómez (B1-TO1-APTO-101, who also signs in as the resident). The conditions that block a deletion
 * (CU-015 7b, approved technical policy) are prepared on Laura's apartment through the real API.
 */

const PEDRO = { name: 'Pedro Ruiz', email: 'pedro.e2e@example.com', document: '1012345679', unit: 'B1-TO1-APTO-102' };

interface ResidentDetail {
  id: number;
  full_name: string;
  document_number: string | null;
  phone: string | null;
  updated_at: string;
}

async function residentId(admin: Api, name: string): Promise<number> {
  const found = await admin.json<{ items: { id: number; full_name: string }[] }>(await admin.get(`/admin/residents?search=${encodeURIComponent(name)}`));
  return found.items.filter((item) => item.full_name === name)[0].id;
}

const shown = (page: Page, field: string) => page.locator(`[data-show="${field}"]`);
const notice = (page: Page) => page.locator('[data-notice-text]');
const dialog = (page: Page) => page.getByRole('dialog', { name: '¿Eliminar a este residente?' });

/** The PATCH and DELETE requests the detail page sends. */
function writes(page: Page): Request[] {
  const sent: Request[] = [];
  page.on('request', (request) => {
    if (['PATCH', 'DELETE'].includes(request.method()) && request.url().includes('/api/v1/admin/residents/')) sent.push(request);
  });
  return sent;
}

async function openDetailFromList(page: Page, name: string): Promise<void> {
  await page.goto('/admin/residents');
  await page.getByRole('link', { name: `Ver detalle de ${name}` }).click();
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
}

test.describe('update', () => {
  test('from the list to the detail; the corrected data is saved, confirmed and shown in the list', async ({ page, admin }) => {
    const id = await residentId(admin.api, PEDRO.name);
    await openDetailFromList(page, PEDRO.name);
    await expect(page).toHaveURL(new RegExp(`/admin/residents/${id}$`));
    await expect(shown(page, 'document_number')).toHaveText(PEDRO.document);
    await expect(shown(page, 'phone')).toHaveText('Sin teléfono');
    await expect(shown(page, 'email')).toHaveText(PEDRO.email);
    await expect(shown(page, 'unit')).toHaveText(PEDRO.unit);

    await page.getByRole('button', { name: 'Editar datos' }).click();
    await expect(page.getByLabel('Nombre completo')).toHaveValue(PEDRO.name);
    await expect(page.getByLabel('Número de documento')).toHaveValue(PEDRO.document);
    // Only the registration data can be edited: there is no field for the email, the status or the unit.
    await expect(page.locator('[data-edit-form] input')).toHaveCount(3);
    await page.getByLabel('Nombre completo').fill('Pedro Ruiz Gómez');
    await page.getByLabel(/Teléfono/).fill('300 123 4567');
    await page.getByRole('button', { name: 'Guardar cambios' }).click();

    await expect(notice(page)).toHaveText('Los datos del residente se actualizaron. El listado de residentes ya muestra la información nueva.');
    await expect(shown(page, 'full_name')).toHaveText('Pedro Ruiz Gómez');
    await expect(shown(page, 'phone')).toHaveText('3001234567');
    await expect(page.locator('[data-edit-section]')).toBeHidden();
    // Persisted: the API and a reload say the same; the list shows the new name.
    const stored = await admin.api.json<ResidentDetail>(await admin.api.get(`/admin/residents/${id}`));
    expect([stored.full_name, stored.document_number, stored.phone]).toEqual(['Pedro Ruiz Gómez', PEDRO.document, '3001234567']);
    await page.reload();
    await expect(shown(page, 'full_name')).toHaveText('Pedro Ruiz Gómez');
    await page.goto('/admin/residents');
    await expect(page.getByRole('link', { name: 'Ver detalle de Pedro Ruiz Gómez' })).toBeVisible();
  });

  test('incomplete or invalid data is not saved: each message next to its field', async ({ page, admin }) => {
    const id = await residentId(admin.api, PEDRO.name);
    await page.goto(`/admin/residents/${id}`);
    const sent = writes(page);
    await page.getByRole('button', { name: 'Editar datos' }).click();
    await page.getByLabel('Nombre completo').fill('');
    await page.getByLabel('Número de documento').fill('12');
    await page.getByRole('button', { name: 'Guardar cambios' }).click();
    await expect(page.locator('[data-error-for="full_name"]')).toHaveText('El nombre completo es obligatorio.');
    await expect(page.locator('[data-error-for="document_number"]')).toHaveText(
      'El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos.');
    await expect(page.getByLabel('Nombre completo')).toBeFocused();
    expect(sent).toHaveLength(0);
    // The server refuses it on its own too, and nothing changed.
    const before = await admin.api.json<ResidentDetail>(await admin.api.get(`/admin/residents/${id}`));
    const refused = await page.request.patch(`/api/v1/admin/residents/${id}`, {
      data: { full_name: '', document_number: '12', phone: null, updated_at: before.updated_at },
      headers: { Origin: E2E_ORIGIN, 'X-CSRF-Token': (await page.locator('[data-resident-page]').getAttribute('data-csrf-token')) ?? '' }
    });
    expect(refused.status()).toBe(422);
    const after = await admin.api.json<ResidentDetail>(await admin.api.get(`/admin/residents/${id}`));
    expect(after).toEqual(before);
  });

  test('cancelling the edit sends nothing and keeps the stored data', async ({ page, admin }) => {
    const id = await residentId(admin.api, PEDRO.name);
    await page.goto(`/admin/residents/${id}`);
    const sent = writes(page);
    await page.getByRole('button', { name: 'Editar datos' }).click();
    await page.getByLabel('Nombre completo').fill('Nombre Descartado');
    await page.getByRole('button', { name: 'Cancelar' }).click();
    await expect(notice(page)).toHaveText('Edición cancelada: no se guardó ningún cambio.');
    await expect(shown(page, 'full_name')).toHaveText(PEDRO.name);
    expect(sent).toHaveLength(0);
    await page.reload();
    await expect(shown(page, 'full_name')).toHaveText(PEDRO.name);
  });
});

test.describe('logical deletion', () => {
  test('the confirmation is asked first; cancelling it sends nothing and the resident stays', async ({ page, admin }) => {
    const id = await residentId(admin.api, PEDRO.name);
    await page.goto(`/admin/residents/${id}`);
    const sent = writes(page);
    await page.getByRole('button', { name: 'Eliminar residente' }).click();
    await expect(dialog(page)).toBeVisible();
    await expect(dialog(page)).toContainText(PEDRO.name);
    await expect(dialog(page)).toContainText(PEDRO.unit);
    await expect(dialog(page)).toContainText('Su correo electrónico queda reservado');
    await dialog(page).getByRole('button', { name: 'Cancelar' }).click();
    await expect(dialog(page)).toBeHidden();
    await expect(notice(page)).toHaveText('Eliminación cancelada: el residente se conserva sin cambios.');
    expect(sent).toHaveLength(0);
    expect((await admin.api.get(`/admin/residents/${id}`)).status()).toBe(200);
  });

  test('confirmed with nothing owed: the resident leaves the list, the detail is gone and the email stays reserved', async ({ page, seed, admin }) => {
    const id = await residentId(admin.api, PEDRO.name);
    await page.goto(`/admin/residents/${id}`);
    await page.getByRole('button', { name: 'Eliminar residente' }).click();
    await dialog(page).getByRole('button', { name: 'Eliminar residente' }).click();

    // The list says so (it arrives with ?deleted=1; the list keeps only its own options in the URL afterwards).
    await expect(page).toHaveURL(/\/admin\/residents(\?.*)?$/);
    await expect(page.getByRole('status').filter({ hasText: 'El residente fue eliminado del listado de residentes registrados.' })).toBeVisible();
    await expect(page.getByRole('link', { name: `Ver detalle de ${PEDRO.name}` })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Ver detalle de Laura Gómez' })).toBeVisible();

    // Logical: the detail is no longer available, by page and by API.
    const detail = await page.goto(`/admin/residents/${id}`);
    expect(detail?.status()).toBe(404);
    await expect(page.getByText('Este residente no existe o ya no está registrado.')).toBeVisible();
    expect((await admin.api.get(`/admin/residents/${id}`)).status()).toBe(404);
    // The apartment is free again, but the email of the deleted account cannot be invited again.
    const invited = await admin.api.post('/resident-invitations', {
      email: PEDRO.email, full_name: 'Pedro Ruiz', unit_id: seed.units[PEDRO.unit], validity_days: 7
    });
    expect(invited.status()).toBe(409);
    expect((await invited.json()).error.code).toBe('EMAIL_ALREADY_REGISTERED');
  });

  test('refused while the apartment owes, has a demo payment in progress or keeps a credit (approved policy, CU-015 7b)', async ({ page, seed, signIn }) => {
    const unit = seed.units['B1-TO1-APTO-101'];
    let admin = await signIn('admin');
    const fee = await createCharge(admin, { unit_id: unit, concept: 'PENALTY', amount: '40000', due_date: addDays(seed.today, 10), description: 'Ruido E2E' });

    async function tryToDelete(): Promise<string> {
      await page.goto(`/admin/residents/${seed.laura_id}`);
      await page.getByRole('button', { name: 'Eliminar residente' }).click();
      await dialog(page).getByRole('button', { name: 'Eliminar residente' }).click();
      await expect(dialog(page)).toBeHidden();
      await expect(notice(page)).toContainText('No se puede eliminar al residente porque');
      return (await notice(page).textContent()) ?? '';
    }

    // 1. A pending balance.
    expect(await tryToDelete()).toBe(
      'No se puede eliminar al residente porque su unidad residencial B1-TO1-APTO-101 tiene saldo pendiente por pagar. '
      + 'Resuelve esa condición e inténtalo de nuevo.');

    // 2. The resident starts a demo payment of that obligation: both conditions are named.
    const resident = await signIn('resident');
    const started = await resident.json<{ id: number }>(await resident.post('/resident/demo-payments', { charge_id: fee.id, amount: '40000' }), 201);
    admin = await signIn('admin');
    expect(await tryToDelete()).toBe(
      'No se puede eliminar al residente porque su unidad residencial B1-TO1-APTO-101 tiene saldo pendiente por pagar; '
      + 'hay un pago de demostración en curso para una obligación de su unidad residencial B1-TO1-APTO-101. '
      + 'Resuelve esas condiciones e inténtalo de nuevo.');

    // 3. The demo payment is cancelled and a manual payment covers the obligation with 10.000 left over: a credit.
    const again = await signIn('resident');
    await again.json(await again.post(`/resident/demo-payments/${started.id}/cancel`, {}));
    admin = await signIn('admin');
    await admin.json(await admin.post('/admin/payments', {
      resident_id: seed.laura_id, amount: '50000', method: 'CASH', concept: 'PENALTY', payment_date: seed.today
    }), 201);
    expect(await tryToDelete()).toBe(
      'No se puede eliminar al residente porque su unidad residencial B1-TO1-APTO-101 tiene saldo a favor sin aplicar. '
      + 'Resuelve esa condición e inténtalo de nuevo.');

    // Laura is still registered, and she can still sign in.
    await expect(page.getByRole('heading', { level: 1, name: 'Laura Gómez' })).toBeVisible();
    expect((await admin.get(`/admin/residents/${seed.laura_id}`)).status()).toBe(200);
    await signIn('resident');
  });
});

test.describe('access', () => {
  test('a resident gets 403 on the detail page and on reading, updating or deleting through the API', async ({ page, seed, signIn }) => {
    await signIn('resident');
    const response = await page.goto(`/admin/residents/${seed.laura_id}`);
    expect(response?.status()).toBe(403);
    await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
    const headers = { Origin: E2E_ORIGIN };
    for (const request of [
      page.request.get(`/api/v1/admin/residents/${seed.laura_id}`),
      page.request.patch(`/api/v1/admin/residents/${seed.laura_id}`, {
        headers, data: { full_name: 'Laura Cambiada', document_number: '1012345678', phone: null, updated_at: '2026-10-22T00:00:00Z' }
      }),
      page.request.delete(`/api/v1/admin/residents/${seed.laura_id}?updated_at=2026-10-22T00:00:00Z`, { headers })
    ]) {
      const answer = await request;
      expect(answer.status()).toBe(403);
      expect((await answer.json()).error.code).toBe('FORBIDDEN');
    }
    const admin = await signIn('admin');
    const laura = await admin.json<ResidentDetail>(await admin.get(`/admin/residents/${seed.laura_id}`));
    expect(laura.full_name).toBe('Laura Gómez');
  });
});
