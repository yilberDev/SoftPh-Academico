import type { Page } from '@playwright/test';
import { expect, test } from './support/fixtures';

/**
 * RF-006 (HU-006): the administrator's list of registered residents (/admin/residents), in a real browser on the
 * E2E stack (*_test database), with the seed as it is: Laura Gómez and Pedro Ruiz live in B1-TO1-APTO-101 and 102;
 * Jorge León and Marta Díaz are former residents (no current apartment), so they are not registered residents.
 *
 * Not covered here (the API and unit tests cover them; the seed has two residents, both active): pages of results
 * and the "Inactivo" filter. With two residents, the name, document, unit and email orders coincide; the test
 * checks that each header asks the server for its own column and direction, and shows the order it returns.
 */

const LIST_API = '/api/v1/admin/residents';
const COLUMNS = ['Nombre', 'Documento', 'Unidad', 'Teléfono', 'Correo', 'Estado'];
const LAURA = ['Laura Gómez', '1012345678', 'B1-TO1-APTO-101', '—', 'laura.e2e@example.com', 'Activo'];
const PEDRO = ['Pedro Ruiz', '1012345679', 'B1-TO1-APTO-102', '—', 'pedro.e2e@example.com', 'Activo'];

const rows = (page: Page) => page.locator('[data-rows] tr');
const names = (page: Page) => rows(page).locator('th');
const statusLine = (page: Page) => page.locator('[data-status-line]');
const header = (page: Page, label: string) => page.getByRole('columnheader', { name: label, exact: true });

/** The query string of the next list request the page sends (filtering and sorting happen on the server). */
async function nextListQuery(page: Page, action: () => Promise<void>): Promise<URLSearchParams> {
  const [request] = await Promise.all([
    page.waitForRequest((sent) => new URL(sent.url()).pathname === LIST_API),
    action()
  ]);
  return new URL(request.url()).searchParams;
}

test.describe('RF-006: the list of registered residents', () => {
  test('criterion 1.1: the columns and each resident\'s data; former residents are not listed', async ({ admin: { page } }) => {
    await page.goto('/admin/residents');
    await expect(statusLine(page)).toHaveText('Mostrando 1–2 de 2 residentes');
    for (const label of [...COLUMNS, 'Acciones']) await expect(header(page, label)).toBeVisible();

    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).nth(0).locator('th, td').filter({ hasNotText: 'Ver detalle' })).toHaveText(LAURA);
    await expect(rows(page).nth(1).locator('th, td').filter({ hasNotText: 'Ver detalle' })).toHaveText(PEDRO);
    await expect(page.getByRole('link', { name: 'Ver detalle de Laura Gómez' })).toBeVisible();
    await expect(page.getByRole('main')).not.toContainText('Jorge León');
    await expect(page.getByRole('main')).not.toContainText('Marta Díaz');
  });

  test('criteria 2.1 and 3.2: search by name (ignoring case and accents) or document; no match shows its message', async ({ admin: { page } }) => {
    await page.goto('/admin/residents');
    await expect(names(page)).toHaveText(['Laura Gómez', 'Pedro Ruiz']);
    const search = page.getByLabel('Buscar por nombre o documento');

    // By name, lowercase and without the accent of "Gómez": the server finds Laura.
    let query = await nextListQuery(page, () => search.fill('gomez'));
    expect(query.get('search')).toBe('gomez');
    await expect(names(page)).toHaveText(['Laura Gómez']);
    await expect(statusLine(page)).toHaveText('Mostrando 1–1 de 1 residente');
    await expect(page).toHaveURL(/\/admin\/residents\?search=gomez$/);

    // By document.
    query = await nextListQuery(page, () => search.fill('1012345679'));
    expect(query.get('search')).toBe('1012345679');
    await expect(names(page)).toHaveText(['Pedro Ruiz']);

    // No match: the message, no rows, no pages.
    await search.fill('Nadie Coincide');
    await expect(page.locator('[data-message]')).toHaveText('Ningún residente coincide con la búsqueda o el filtro.');
    await expect(statusLine(page)).toHaveText('0 residentes');
    await expect(rows(page)).toHaveCount(0);
    await expect(page.locator('[data-pagination]')).toBeHidden();

    // Clearing the search lists everyone again.
    await search.fill('');
    await expect(names(page)).toHaveText(['Laura Gómez', 'Pedro Ruiz']);
    await expect(page.locator('[data-message]')).toBeHidden();
  });

  test('criterion 3.1: each column header sorts on the server; a second click reverses it; aria-sort and the URL follow', async ({ admin: { page } }) => {
    await page.goto('/admin/residents');
    await expect(header(page, 'Nombre')).toHaveAttribute('aria-sort', 'ascending'); // the default order
    await expect(names(page)).toHaveText(['Laura Gómez', 'Pedro Ruiz']);

    const fields: [string, string][] = [
      ['Documento', 'document'], ['Unidad', 'unit'], ['Teléfono', 'phone'], ['Correo', 'email'], ['Estado', 'status'], ['Nombre', 'name']
    ];
    for (const [label, field] of fields) {
      const button = header(page, label).getByRole('button');
      // Another column was active, so this one starts ascending ("Nombre" comes last, after "Estado").
      let query = await nextListQuery(page, () => button.click());
      expect([query.get('sort_by') ?? 'name', query.get('sort_order') ?? 'asc'], label).toEqual([field, 'asc']);
      await expect(header(page, label)).toHaveAttribute('aria-sort', 'ascending');
      for (const other of COLUMNS.filter((column) => column !== label)) {
        await expect(header(page, other)).toHaveAttribute('aria-sort', 'none');
      }
      // Where the two residents differ (not phone or status), the rows follow the direction.
      const differ = !['phone', 'status'].includes(field);
      if (differ) await expect(names(page)).toHaveText(['Laura Gómez', 'Pedro Ruiz']);
      query = await nextListQuery(page, () => button.click());
      expect([query.get('sort_by') ?? 'name', query.get('sort_order') ?? 'asc'], label).toEqual([field, 'desc']);
      await expect(header(page, label)).toHaveAttribute('aria-sort', 'descending');
      if (differ) await expect(names(page)).toHaveText(['Pedro Ruiz', 'Laura Gómez']);
    }

    // The order stays in the URL: opening it again shows the same order.
    await page.goto('/admin/residents?sort_by=unit&sort_order=desc');
    await expect(header(page, 'Unidad')).toHaveAttribute('aria-sort', 'descending');
    await expect(names(page)).toHaveText(['Pedro Ruiz', 'Laura Gómez']);
  });
});
