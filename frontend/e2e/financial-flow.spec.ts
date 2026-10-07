import type { Locator, Page } from '@playwright/test';
import { addDays, periodOf } from './support/data';
import { chooseUnit, createCharge, expect, normalized, openNewCharge, test } from './support/fixtures';

/**
 * E2E 6, 7 and 8: payments of HU-004 applied to obligations, credit (saldo a favor)
 * consumed by a new obligation, and the complete detail opened from the list.
 * Every amount and status shown comes from the backend (nothing is computed here).
 *
 * Seed: Pedro Ruiz lives in B1-TO1-APTO-102 and Laura Gómez in B1-TO1-APTO-101.
 */

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

const info = (page: Page, label: string) => definition(page.getByRole('region', { name: 'Información' }), label);

async function pay(page: Page, resident: string, amount: string, concept: string): Promise<Locator> {
  await page.goto('/admin/payments');
  const form = page.locator('[data-payment-form]');
  await form.getByLabel('Residente').fill(resident.split(' ')[0]);
  await form.getByRole('button', { name: new RegExp(resident) }).click();
  await form.getByLabel('Monto').fill(amount);
  await form.getByLabel('Método de pago').selectOption({ label: 'Transferencia' });
  await form.getByLabel('Concepto', { exact: true }).selectOption({ label: concept });
  await form.getByRole('button', { name: 'Registrar pago' }).click();
  const confirmation = page.locator('[data-confirmation]');
  await expect(confirmation).toContainText('Pago registrado correctamente.');
  return confirmation;
}

test.describe('payments and obligations', () => {
  test('a payment covers an obligation: partial, then paid, with its applications in the detail', async ({ admin: { page, api }, seed }) => {
    const charge = (await createCharge(api, {
      unit_id: seed.units['B1-TO1-APTO-102'], concept: 'PENALTY', amount: '120000', due_date: addDays(seed.today, 10),
      description: 'Daño en zona común'
    })).id;

    const first = await pay(page, 'Pedro Ruiz', '50000', 'Penalización');
    expect(normalized(await definition(first, 'Valor aplicado').textContent())).toBe('$ 50.000,00');
    await expect(definition(first, 'Estado del pago')).toHaveText('Aplicado');
    expect(normalized(await definition(first, 'Saldo pendiente de la unidad').textContent())).toBe('$ 70.000,00');
    // The payments summary keeps working and shows it.
    const row = page.locator('[data-rows] tr').filter({ hasText: 'Pedro Ruiz' });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Aplicado');

    await page.goto(`/admin/obligations/${charge}`);
    expect(normalized(await info(page, 'Aplicado').textContent())).toBe('$ 50.000,00');
    expect(normalized(await info(page, 'Saldo').textContent())).toBe('$ 70.000,00');
    await expect(info(page, 'Estado')).toHaveText('Parcial');

    await pay(page, 'Pedro Ruiz', '70000', 'Penalización');
    await page.goto(`/admin/obligations/${charge}`);
    expect(normalized(await info(page, 'Aplicado').textContent())).toBe('$ 120.000,00');
    expect(normalized(await info(page, 'Saldo').textContent())).toBe('$ 0,00');
    await expect(info(page, 'Estado')).toHaveText('Pagada');

    const table = page.getByRole('region', { name: 'Pagos aplicados' }).getByRole('table');
    await expect(table.getByRole('columnheader')).toHaveText(['Recibo', 'Fecha del pago', 'Método', 'Monto aplicado', 'Aplicado el']);
    const rows = table.locator('tbody tr');
    await expect(rows).toHaveCount(2);
    for (const [index, amount] of ['$ 50.000,00', '$ 70.000,00'].entries()) {
      const cells = rows.nth(index);
      await expect(cells.getByRole('rowheader')).toHaveText(/^RC-\d+$/);
      await expect(cells.getByRole('cell').nth(0)).toHaveText(longDate(seed.today));
      await expect(cells.getByRole('cell').nth(1)).toHaveText('Transferencia');
      expect(normalized(await cells.getByRole('cell').nth(2).textContent())).toBe(amount);
      // Stored in UTC, shown in Bogotá time.
      await expect(cells.getByRole('cell').nth(3)).toHaveText(/\d{4} a las \d{1,2}:\d{2}/);
    }
  });

  test('a previous credit is consumed by new obligations, and the payment status follows', async ({ admin: { page }, seed }) => {
    const payment = await pay(page, 'Laura Gómez', '200000', 'Mensualidad');
    await expect(definition(payment, 'Estado del pago')).toHaveText('Saldo a favor');
    expect(normalized(await definition(payment, 'Saldo a favor de la unidad').textContent())).toBe('$ 200.000,00');
    const receipt = normalized(await definition(payment, 'Recibo').textContent());

    // First obligation: fully covered by the credit.
    await page.goto('/admin/obligations');
    let form = await openNewCharge(page);
    await chooseUnit(page, 'charge', 'B1-TO1-APTO-101');
    await form.getByLabel('Concepto').selectOption({ label: 'Otro' });
    await form.getByLabel('Descripción').fill('Arreglo de citófono');
    await form.getByLabel('Monto').fill('150000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 10));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    const confirmation = page.locator('[data-charge-confirmation]');
    await expect(confirmation).toBeVisible();
    expect(normalized(await definition(confirmation, 'Aplicado').textContent())).toBe('$ 150.000,00');
    expect(normalized(await definition(confirmation, 'Saldo').textContent())).toBe('$ 0,00');
    await expect(definition(confirmation, 'Estado')).toHaveText('Pagada');
    expect(normalized(await definition(confirmation, 'Saldo a favor del apartamento').textContent())).toBe('$ 50.000,00');
    expect(normalized(await confirmation.locator('[data-charge-credit]').textContent())).toMatch(
      new RegExp(`Saldo a favor del recibo ${receipt}: \\$ 150\\.000,00 aplicado a la obligación n\\.º \\d+\\.`)
    );

    // Second obligation: the remaining 50.000 only.
    form = page.locator('[data-charge-form]');
    await form.getByLabel('Concepto').selectOption({ label: 'Otro' });
    await form.getByLabel('Descripción').fill('Parqueadero de visitantes');
    await form.getByLabel('Monto').fill('80000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 12));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(confirmation).toContainText('Parqueadero de visitantes');
    expect(normalized(await definition(confirmation, 'Aplicado').textContent())).toBe('$ 50.000,00');
    expect(normalized(await definition(confirmation, 'Saldo').textContent())).toBe('$ 30.000,00');
    await expect(definition(confirmation, 'Estado')).toHaveText('Parcial');
    expect(normalized(await definition(confirmation, 'Saldo a favor del apartamento').textContent())).toBe('$ 0,00');
    expect(normalized(await definition(confirmation, 'Saldo pendiente del apartamento').textContent())).toBe('$ 30.000,00');

    // The payment is fully applied now: its current status in the summary.
    await page.goto('/admin/payments');
    await expect(page.locator('[data-rows] tr').filter({ hasText: receipt })).toContainText('Aplicado');
    await expect(page.locator('[data-rows] tr').filter({ hasText: receipt })).not.toContainText('Saldo a favor');
  });

  test('the detail opened from the list: every field, the late fees and the applications', async ({ admin: { page, api }, seed }) => {
    const previous = periodOf(seed.today, -1);
    const unit = seed.units['B1-TO1-APTO-102'];
    const fee = (await createCharge(api, { unit_id: unit, concept: 'MONTHLY_FEE', amount: '180000', due_date: `${previous}-10`, billing_period: previous })).id;
    const lateFee = (await createCharge(api, { unit_id: unit, concept: 'LATE_FEE', amount: '5400', due_date: addDays(seed.today, 5), source_charge_id: fee })).id;
    await pay(page, 'Pedro Ruiz', '100000', 'Mensualidad');

    await page.goto('/admin/obligations');
    await chooseUnit(page, 'filter', 'B1-TO1-APTO-102');
    await expect(page.getByText('Mostrando 1–2 de 2 obligaciones')).toBeVisible();
    await page.getByRole('link', { name: `Ver detalle de la obligación n.º ${fee} de B1-TO1-APTO-102` }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/obligations/${fee}$`));

    const year = previous.slice(0, 4);
    const expected: Record<string, string | RegExp> = {
      Unidad: 'B1-TO1-APTO-102',
      Estado: 'Vencida',
      Concepto: 'Mensualidad',
      Descripción: '—',
      Periodo: new RegExp(`^[A-Z][a-z]+ de ${year}$`),
      Vencimiento: longDate(`${previous}-10`),
      Valor: '$ 180.000,00',
      Aplicado: '$ 100.000,00',
      Saldo: '$ 80.000,00',
      'Creada por': /^Usuario n\.º \d+$/,
      'Fecha de creación': new RegExp(`\\d{4} a las \\d{1,2}:\\d{2}`)
    };
    for (const [label, value] of Object.entries(expected)) {
      const text = normalized(await info(page, label).textContent());
      if (typeof value === 'string') expect(text, label).toBe(value);
      else expect(text, label).toMatch(value);
    }
    const relation = page.getByRole('region', { name: 'Relación' });
    await expect(relation.getByText('Moras derivadas')).toBeVisible();
    await expect(relation.getByRole('link', { name: new RegExp(`^n\\.º ${lateFee} · Mora`) })).toBeVisible();
    const applications = page.getByRole('region', { name: 'Pagos aplicados' }).locator('tbody tr');
    await expect(applications).toHaveCount(1);
    expect(normalized(await applications.getByRole('cell').nth(2).textContent())).toBe('$ 100.000,00');

    // The late fee links back to its origin.
    await relation.getByRole('link', { name: new RegExp(`^n\\.º ${lateFee} · Mora`) }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Mora' })).toBeVisible();
    await page.getByRole('region', { name: 'Relación' }).getByRole('link', { name: new RegExp(`^n\\.º ${fee} · Mensualidad`) }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/obligations/${fee}$`));
  });
});
