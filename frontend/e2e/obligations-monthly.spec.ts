import type { Locator, Page } from '@playwright/test';
import { addDays, periodOf } from './support/data';
import { expect, generateMonthly, normalized, test, type Api } from './support/fixtures';

/**
 * E2E 2, 3 and the double submission: monthly generation with a preview, idempotency,
 * 409 MONTHLY_PREVIEW_OUTDATED with the real contract, and one generation per click.
 *
 * Seed: 12 active apartments, no monthly fee, B2-TO1-APTO-205 with 50.000 of credit.
 */

const AMOUNT = '180000';

async function openMonthly(page: Page, period: string, dueDate: string): Promise<void> {
  await page.goto('/admin/obligations');
  await page.getByText('Generar mensualidades', { exact: true }).click();
  await monthlyForm(page).getByLabel('Periodo').fill(period);
  await page.getByLabel('Monto por apartamento').fill(AMOUNT);
  await monthlyForm(page).getByLabel('Vencimiento').fill(dueDate);
}

const monthlyForm = (page: Page) => page.locator('[data-monthly-form]');

/** The value next to a label in a <dl>. */
function definition(scope: Locator, label: string): Locator {
  return scope.locator('div').filter({ has: scope.page().getByText(label, { exact: true }) }).locator('dd');
}

async function chargesOf(api: Api, period: string): Promise<number> {
  return (await api.json<{ total: number }>(await api.get(`/admin/charges?billing_period=${period}`))).total;
}

test.describe('monthly generation', () => {
  test('preview, confirmation and an idempotent second run', async ({ admin: { page, api }, seed }) => {
    const period = periodOf(seed.today, 1);
    const due = `${period}-10`;
    await openMonthly(page, period, due);
    await page.getByRole('button', { name: 'Revisar' }).click();

    const preview = page.getByRole('region', { name: 'Vista previa' });
    await expect(preview).toBeVisible();
    const text = normalized(await preview.locator('[data-preview-text]').textContent());
    expect(text).toContain('$ 180.000,00 por apartamento');
    expect(text).toContain('12 activos · 0 existentes · 12 por crear · total $ 2.160.000,00');
    await expect(definition(preview, 'Apartamentos activos')).toHaveText('12');
    await expect(definition(preview, 'Ya tienen la mensualidad')).toHaveText('0');
    await expect(definition(preview, 'Sin la mensualidad')).toHaveText('12');
    await expect(definition(preview, 'Por crear')).toHaveText('12');
    expect(normalized(await definition(preview, 'Total a generar').textContent())).toBe('$ 2.160.000,00');
    await expect(definition(preview, 'Huella de la vista previa')).toHaveText(/^[0-9a-f]{64}$/);
    expect(await chargesOf(api, period), 'the preview creates nothing').toBe(0);

    await page.getByRole('button', { name: 'Confirmar y generar' }).click();
    const result = page.getByRole('region', { name: 'Generación terminada' });
    await expect(result).toBeVisible();
    const lines: Record<string, string> = {
      'Apartamentos evaluados': '12',
      Creadas: '12',
      'Ya existían': '0',
      Omitidas: '0',
      Fallidas: '0',
      'Total generado': '$ 2.160.000,00',
      // The credit of B2-TO1-APTO-205 is consumed by its new fee (backend FIFO).
      'Saldo a favor aplicado': '$ 50.000,00',
      'Saldo a favor aplicado a las nuevas': '$ 50.000,00',
      'Apartamentos con saldo a favor aplicado': '1',
      'Saldo pendiente generado': '$ 2.110.000,00'
    };
    for (const [label, value] of Object.entries(lines)) {
      expect(normalized(await definition(result, label).textContent()), label).toBe(value);
    }
    await expect(result.getByText('Algunos apartamentos no recibieron la mensualidad.')).toBeHidden();
    // The list was refreshed: 2 seeded + 12 new.
    await expect(page.getByText('Mostrando 1–10 de 14 obligaciones')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Confirmar y generar' })).toBeHidden();

    await result.getByRole('link', { name: 'Ver las obligaciones del periodo' }).click();
    await expect(page).toHaveURL(new RegExp(`billing_period=${period}$`));
    await expect(page.getByText('Mostrando 1–10 de 12 obligaciones')).toBeVisible();

    // Second run, same period and parameters: nothing left to create.
    await page.getByText('Generar mensualidades', { exact: true }).click();
    await monthlyForm(page).getByLabel('Periodo').fill(period);
    await page.getByLabel('Monto por apartamento').fill(AMOUNT);
    await monthlyForm(page).getByLabel('Vencimiento').fill(due);
    await page.getByRole('button', { name: 'Revisar' }).click();
    await expect(preview).toBeVisible();
    expect(normalized(await preview.locator('[data-preview-text]').textContent())).toContain('12 activos · 12 existentes · 0 por crear · total $ 0,00');
    await expect(preview.getByText('Todas las unidades activas ya tienen esta mensualidad')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Confirmar y generar' })).toBeHidden();

    // The contract also answers a repeated confirmation without duplicates.
    const again = await generateMonthly(api, period, AMOUNT, due);
    expect(again).toMatchObject({ created: 0, existing: 12, omitted: 0, failed: 0, units_evaluated: 12 });
    expect(await chargesOf(api, period)).toBe(12);
  });

  test('changing a parameter discards the preview and asks for a new review', async ({ admin: { page }, seed }) => {
    const period = periodOf(seed.today, 1);
    await openMonthly(page, period, `${period}-10`);
    await page.getByRole('button', { name: 'Revisar' }).click();
    await expect(page.getByRole('button', { name: 'Confirmar y generar' })).toBeVisible();
    await page.getByLabel('Monto por apartamento').fill('190000');
    await expect(page.getByRole('region', { name: 'Vista previa' })).toBeHidden();
    await expect(page.getByRole('button', { name: 'Confirmar y generar' })).toBeHidden();
  });

  test('409 MONTHLY_PREVIEW_OUTDATED: the new preview replaces the old one and nothing is generated', async ({
    admin: { page, api },
    seed
  }) => {
    const period = periodOf(seed.today, 1);
    await openMonthly(page, period, `${period}-10`);
    await page.getByRole('button', { name: 'Revisar' }).click();
    const preview = page.getByRole('region', { name: 'Vista previa' });
    await expect(definition(preview, 'Por crear')).toHaveText('12');
    const oldFingerprint = await definition(preview, 'Huella de la vista previa').textContent();

    // Meanwhile, another tab adds an apartment through the structure pages (HU-013).
    const other = await page.context().newPage();
    await other.goto(`/admin/towers/${seed.towers['B1-TO1']}`);
    await other.getByRole('textbox', { name: 'Número del apartamento' }).fill('107');
    await other.getByRole('button', { name: 'Agregar apartamento' }).click();
    await expect(other.getByRole('cell', { name: 'B1-TO1-APTO-107', exact: true })).toBeVisible();
    await other.close();

    const sent: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/api/v1/admin/charges/monthly')) sent.push(request.url());
    });
    const answer = page.waitForResponse((r) => r.url().endsWith('/api/v1/admin/charges/monthly'));
    await page.getByRole('button', { name: 'Confirmar y generar' }).click();
    const response = await answer;
    expect(response.status()).toBe(409);
    expect(((await response.json()) as { error: { code: string } }).error.code).toBe('MONTHLY_PREVIEW_OUTDATED');

    await expect(page.getByRole('alert').filter({ hasText: 'Los datos cambiaron desde la vista previa. Revisa y confirma de nuevo.' })).toBeVisible();
    await expect(definition(preview, 'Apartamentos activos')).toHaveText('13');
    await expect(definition(preview, 'Por crear')).toHaveText('13');
    expect(normalized(await definition(preview, 'Total a generar').textContent())).toBe('$ 2.340.000,00');
    expect(await definition(preview, 'Huella de la vista previa').textContent()).not.toBe(oldFingerprint);
    await expect(page.getByRole('region', { name: 'Generación terminada' })).toBeHidden();
    expect(sent, 'no automatic generation after the 409').toHaveLength(1);
    expect(await chargesOf(api, period), 'nothing was created').toBe(0);

    // Only an explicit new confirmation generates.
    await page.getByRole('button', { name: 'Confirmar y generar' }).click();
    const result = page.getByRole('region', { name: 'Generación terminada' });
    await expect(definition(result, 'Creadas')).toHaveText('13');
    expect(sent).toHaveLength(2);
    expect(await chargesOf(api, period)).toBe(13);
  });

  test('a double click on "Confirmar y generar" sends one generation', async ({ admin: { page, api }, seed }) => {
    const period = periodOf(seed.today, 1);
    await openMonthly(page, period, `${period}-10`);
    await page.getByRole('button', { name: 'Revisar' }).click();
    const sent: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/api/v1/admin/charges/monthly')) sent.push(request.url());
    });
    await page.getByRole('button', { name: 'Confirmar y generar' }).dblclick();
    await expect(page.getByRole('region', { name: 'Generación terminada' })).toBeVisible();
    expect(sent).toHaveLength(1);
    expect(await chargesOf(api, period)).toBe(12);
  });

  test('format and business errors are shown by field', async ({ admin: { page }, seed }) => {
    const period = periodOf(seed.today, 1);
    // Format (client help): an amount with thousands separators.
    await openMonthly(page, period, `${period}-10`);
    await page.getByLabel('Monto por apartamento').fill('180.000');
    await page.getByRole('button', { name: 'Revisar' }).click();
    await expect(page.getByText('Escribe el monto solo con números, sin puntos de miles (p. ej., 150000 o 150000,50).')).toBeVisible();
    await expect(page.getByLabel('Monto por apartamento')).toHaveAttribute('aria-invalid', 'true');

    // Business rule of the backend (422 by field): due date before the period.
    await page.getByLabel('Monto por apartamento').fill(AMOUNT);
    await monthlyForm(page).getByLabel('Vencimiento').fill(addDays(`${period}-01`, -1));
    const answer = page.waitForResponse((r) => r.url().includes('/api/v1/admin/charges/monthly/preview'));
    await page.getByRole('button', { name: 'Revisar' }).click();
    expect((await answer).status()).toBe(422);
    await expect(page.locator('[data-monthly-error="due_date"]')).toHaveText('El vencimiento no puede ser anterior al inicio del periodo.');
    await expect(page.getByRole('region', { name: 'Vista previa' })).toBeHidden();
    expect(await page.content()).not.toMatch(/Traceback|Exception/);
  });
});
