import type { Page } from '@playwright/test';
import { expect, test } from './support/fixtures';

/**
 * RF-013 (HU-013): listing, creating and renumbering the residential structure from its pages, in a real browser on
 * the E2E stack (*_test database). Deleting is covered by structure-balance.spec.ts.
 *
 * Seed: blocks B1 and B2, each with one tower (TO1) and six apartments (B1: 101–106, B2: 201–206). Every test starts
 * from that seed (fixture `seed`) and the run leaves the database empty (global-teardown.ts).
 */

const INVALID_NUMBER = 'Ingresa un número entero positivo (1 o mayor).';

/** The structure requests the page sends ("POST /blocks", "PATCH /towers/7"). */
function structureWrites(page: Page): string[] {
  const sent: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname.replace(/^\/api\/v1/, '');
    if (request.method() !== 'GET' && /^\/(blocks|towers|units)\b/.test(path)) sent.push(`${request.method()} ${path}`);
  });
  return sent;
}

/** Each row of the level's table: its name (first column) and the other columns, as shown. */
const rows = (page: Page) => page.locator('[data-item]');
const rowNames = (page: Page) => rows(page).locator('th');

test.describe('RF-013: the residential structure', () => {
  test('case A: blocks with their counts, the way down to the apartments; creating a block, with the visible checks', async ({ admin: { page } }) => {
    const writes = structureWrites(page);

    // Criteria 2.1 and 2.2: the blocks, with their active towers and apartments.
    await page.goto('/admin/blocks');
    await expect(page.getByRole('heading', { level: 1, name: 'Bloques' })).toBeVisible();
    await expect(page.getByRole('columnheader')).toHaveText(['Bloque', 'Torres', 'Apartamentos', 'Acciones']);
    await expect(rowNames(page)).toHaveText(['B1', 'B2']);
    for (const block of ['B1', 'B2']) {
      await expect(rows(page).filter({ has: page.getByRole('link', { name: block, exact: true }) }).locator('td').nth(0)).toHaveText('1');
      await expect(rows(page).filter({ has: page.getByRole('link', { name: block, exact: true }) }).locator('td').nth(1)).toHaveText('6');
    }

    // Down to a tower and its apartments, each with its official identifier.
    await page.getByRole('link', { name: 'B1', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Torres de B1');
    await expect(rowNames(page)).toHaveText(['TO1']);
    await expect(page.getByRole('cell', { name: 'B1-TO1', exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'TO1', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Apartamentos de B1-TO1');
    const apartments = [101, 102, 103, 104, 105, 106];
    await expect(rowNames(page)).toHaveText(apartments.map((number) => `APTO-${number}`));
    for (const number of apartments) {
      await expect(page.getByRole('cell', { name: `B1-TO1-APTO-${number}`, exact: true })).toBeVisible();
    }

    // Criteria 1.2 and 5.2: 0 is refused by the form itself; nothing is sent.
    await page.goto('/admin/blocks');
    const create = page.locator('[data-create-form]');
    const number = page.getByRole('textbox', { name: 'Número del bloque' });
    await number.fill('0');
    await page.getByRole('button', { name: 'Agregar bloque' }).click();
    await expect(create.locator('[data-field-error]')).toHaveText(INVALID_NUMBER);
    await expect(number).toHaveAttribute('aria-invalid', 'true');
    await expect(number).toBeFocused();
    expect(writes).toEqual([]);

    // A number already used: the server refuses it and the page shows its reason; nothing is created.
    await number.fill('1');
    const duplicate = page.waitForResponse((answer) => answer.request().method() === 'POST');
    await page.getByRole('button', { name: 'Agregar bloque' }).click();
    expect((await duplicate).status()).toBe(409);
    await expect(create.getByRole('alert')).toHaveText('Ya existe el bloque B1.');
    await expect(create.locator('[data-field-error]')).toBeHidden();
    expect(writes).toEqual(['POST /blocks']);
    await page.reload();
    await expect(rowNames(page)).toHaveText(['B1', 'B2']);

    // Criterion 1.1: a valid number is registered and listed, still without towers or apartments.
    await page.getByRole('textbox', { name: 'Número del bloque' }).fill('3');
    await page.getByRole('button', { name: 'Agregar bloque' }).click();
    await expect(rowNames(page)).toHaveText(['B1', 'B2', 'B3']);
    const b3 = rows(page).filter({ has: page.getByRole('link', { name: 'B3', exact: true }) });
    await expect(b3.locator('td').nth(0)).toHaveText('0');
    await expect(b3.locator('td').nth(1)).toHaveText('0');
    expect(writes).toEqual(['POST /blocks', 'POST /blocks']);
  });

  test('case B: renumbering B2-TO1 warns about its 6 apartments; cancelling sends nothing; confirming renames them all', async ({ admin: { page, api }, seed }) => {
    const writes = structureWrites(page);
    await page.goto(`/admin/blocks/${seed.blocks.B2}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Torres de B2');

    // The warning before confirming a change that alters the identifiers of the descendants.
    const openEdit = async () => {
      await page.getByRole('button', { name: 'Editar B2-TO1' }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByRole('heading', { name: 'Editar B2-TO1' })).toBeVisible();
      await dialog.getByRole('textbox', { name: 'Número de la torre' }).fill('2');
      await expect(dialog.locator('[data-renumber-warning]')).toHaveText(
        'Vas a cambiar B2-TO1 por B2-TO2. Los registros y sus relaciones no cambian, ' +
          'pero el identificador visible de 6 apartamentos pasará a mostrarse bajo B2-TO2.'
      );
      await expect(dialog.getByRole('button', { name: 'Confirmar cambio' })).toBeVisible();
      return dialog;
    };

    // Cancelling: the dialog closes, no PATCH, the tower is still TO1 (also after reloading).
    let dialog = await openEdit();
    await dialog.getByRole('button', { name: 'Cancelar' }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([]);
    await page.reload();
    await expect(rowNames(page)).toHaveText(['TO1']);
    await expect(page.getByRole('cell', { name: 'B2-TO1', exact: true })).toBeVisible();

    // Criteria 3.1 and 3.2: confirming saves it, and the derived identifiers follow.
    dialog = await openEdit();
    const saved = page.waitForResponse((answer) => answer.request().method() === 'PATCH');
    await dialog.getByRole('button', { name: 'Confirmar cambio' }).click();
    expect((await saved).status()).toBe(200);
    expect(writes).toEqual([`PATCH /towers/${seed.towers['B2-TO1']}`]);
    await expect(rowNames(page)).toHaveText(['TO2']);
    await expect(page.getByRole('cell', { name: 'B2-TO2', exact: true })).toBeVisible();

    await page.getByRole('link', { name: 'TO2', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Apartamentos de B2-TO2');
    const apartments = [201, 202, 203, 204, 205, 206];
    await expect(rowNames(page)).toHaveText(apartments.map((number) => `APTO-${number}`));
    for (const number of apartments) {
      await expect(page.getByRole('cell', { name: `B2-TO2-APTO-${number}`, exact: true })).toBeVisible();
    }
    // The same records (ids), only their visible identifier changed.
    const unit = await api.json<{ label: string }>(await api.get(`/units/${seed.units['B2-TO1-APTO-204']}`));
    expect(unit.label).toBe('B2-TO2-APTO-204');
  });
});
