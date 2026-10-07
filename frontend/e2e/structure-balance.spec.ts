import type { Page } from '@playwright/test';
import { expect, normalized, test, type Api } from './support/fixtures';

/**
 * E2E 10 and 11: deleting an apartment from the structure pages (HU-013).
 *
 * Seed (no resident lives in these apartments):
 * - B2-TO1-APTO-204: an obligation fully paid by a former resident: balance 0.
 * - B2-TO1-APTO-205: a former resident's payment with nothing to cover: credit 50.000.
 * - B2-TO1-APTO-206: a pending penalty of 30.000.
 */

const BLOCKED_REASON =
  'El apartamento tiene obligaciones pendientes o saldo a favor. Su saldo debe quedar en cero antes de eliminarlo.';

async function totals(api: Api): Promise<{ charges: number; payments: number }> {
  const charges = await api.json<{ total: number }>(await api.get('/admin/charges'));
  const payments = await api.json<{ total: number }>(await api.get('/admin/payments'));
  return { charges: charges.total, payments: payments.total };
}

async function askDelete(page: Page, towerId: number, label: string) {
  await page.goto(`/admin/towers/${towerId}`);
  await page.getByRole('button', { name: `Eliminar ${label}` }).click();
  return page.getByRole('dialog');
}

test.describe('deleting an apartment', () => {
  test('balance 0: it is deleted and its financial history stays', async ({ admin: { page, api }, seed }) => {
    const before = await totals(api);
    const dialog = await askDelete(page, seed.towers['B2-TO1'], 'B2-TO1-APTO-204');
    await expect(dialog.getByRole('heading', { name: '¿Eliminar B2-TO1-APTO-204?' })).toBeVisible();
    const answer = page.waitForResponse((r) => r.request().method() === 'DELETE');
    await dialog.getByRole('button', { name: 'Eliminar' }).click();
    expect((await answer).status()).toBe(204);
    await expect(page.getByRole('cell', { name: 'B2-TO1-APTO-204', exact: true })).toHaveCount(0);

    // Nothing financial disappears.
    expect(await totals(api)).toEqual(before);
    await page.goto(`/admin/obligations?block_id=${seed.blocks.B2}`);
    const row = page.locator('[data-rows] tr').filter({ hasText: 'B2-TO1-APTO-204' });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Apartamento eliminado');
    await expect(row).toContainText('Pagada');

    await row.getByRole('link', { name: /Ver detalle/ }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Otro: Reparación de la puerta E2E' })).toBeVisible();
    const information = page.getByRole('region', { name: 'Información' });
    await expect(information).toContainText('B2-TO1-APTO-204 (apartamento eliminado)');
    await expect(information).toContainText('Pagada');
    const applications = page.getByRole('region', { name: 'Pagos aplicados' }).locator('tbody tr');
    await expect(applications).toHaveCount(1);
    expect(normalized(await applications.getByRole('cell').nth(2).textContent())).toBe('$ 80.000,00');
  });

  for (const [label, kind] of [
    ['B2-TO1-APTO-206', 'pending balance'],
    ['B2-TO1-APTO-205', 'credit (saldo a favor)']
  ] as const) {
    test(`UNIT_HAS_BALANCE with ${kind}: refused, the apartment and its finances stay`, async ({ admin: { page, api }, seed }) => {
      const before = await totals(api);
      const dialog = await askDelete(page, seed.towers['B2-TO1'], label);
      const answer = page.waitForResponse((r) => r.request().method() === 'DELETE');
      await dialog.getByRole('button', { name: 'Eliminar' }).click();
      const response = await answer;
      expect(response.status()).toBe(409);
      expect(((await response.json()) as { error: { code: string } }).error.code).toBe('UNIT_HAS_BALANCE');

      await expect(dialog.getByRole('heading', { name: `No se puede eliminar ${label}` })).toBeVisible();
      await expect(dialog.getByText(BLOCKED_REASON)).toBeVisible();
      // Informative only: no second DELETE can be sent from it.
      await expect(dialog.getByRole('button', { name: 'Eliminar' })).toBeHidden();
      await dialog.getByRole('button', { name: 'Cerrar' }).click();
      await expect(dialog).toBeHidden();

      await page.reload();
      await expect(page.getByRole('cell', { name: label, exact: true })).toBeVisible();
      expect(await totals(api)).toEqual(before);
      const unit = await api.json<{ label: string }>(await api.get(`/units/${seed.units[label]}`));
      expect(unit.label).toBe(label);
    });
  }
});
