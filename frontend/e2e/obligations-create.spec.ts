import type { Page } from '@playwright/test';
import { addDays, periodOf } from './support/data';
import { chooseUnit, createCharge, expect, generateMonthly, normalized, openNewCharge, test, type Api } from './support/fixtures';

/**
 * E2E 4, 5 and the financial errors of "Nueva obligación": a late fee linked to its
 * origin, a penalty, business errors shown by field, and one creation per click.
 */

const UNIT = 'B1-TO1-APTO-103';

async function startCharge(page: Page, unit: string, concept: string) {
  await page.goto('/admin/obligations');
  const form = await openNewCharge(page);
  await chooseUnit(page, 'charge', unit);
  await form.getByLabel('Concepto').selectOption({ label: concept });
  return form;
}

/** The value of a field of the detail ("Información"). */
function info(page: Page, label: string) {
  return page.getByRole('region', { name: 'Información' }).locator('div').filter({ has: page.getByText(label, { exact: true }) }).locator('dd');
}

async function overdueFee(api: Api, seedToday: string, unitId: number): Promise<number> {
  const previous = periodOf(seedToday, -1);
  return (await createCharge(api, { unit_id: unitId, concept: 'MONTHLY_FEE', amount: '180000', due_date: `${previous}-10`, billing_period: previous })).id;
}

test.describe('new obligation', () => {
  test('late fee linked to an origin of the same apartment; late fees are never offered', async ({ admin: { page, api }, seed }) => {
    const unitId = seed.units[UNIT];
    const origin = await overdueFee(api, seed.today, unitId);
    const penalty = (await createCharge(api, { unit_id: unitId, concept: 'PENALTY', amount: '20000', due_date: addDays(seed.today, -5), description: 'Mascota sin correa' })).id;
    const lateFee = (await createCharge(api, { unit_id: unitId, concept: 'LATE_FEE', amount: '2000', due_date: addDays(seed.today, 5), source_charge_id: penalty })).id;
    // An obligation of another apartment: never an option.
    await overdueFee(api, seed.today, seed.units['B1-TO1-APTO-104']);

    const form = await startCharge(page, UNIT, 'Mora');
    const source = form.getByLabel('Obligación de origen');
    await expect(source).toBeVisible();
    await expect(source).toBeEnabled();
    const options = source.locator('option');
    await expect(options).toHaveCount(3); // "Sin obligación de origen" + the monthly fee + the penalty
    await expect(options.filter({ hasText: `n.º ${origin} · Mensualidad` })).toHaveCount(1);
    await expect(options.filter({ hasText: `n.º ${penalty} · Penalización` })).toHaveCount(1);
    await expect(options.filter({ hasText: `n.º ${lateFee} ` })).toHaveCount(0);
    await expect(options.filter({ hasText: ' · Mora' })).toHaveCount(0);
    await expect(form.getByLabel('Descripción (opcional)')).toBeVisible();
    await expect(form.locator('#charge-period')).toBeHidden();

    await source.selectOption(String(origin));
    await form.getByLabel('Monto').fill('18000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 10));
    await form.getByRole('button', { name: 'Crear obligación' }).click();

    const confirmation = page.locator('[data-charge-confirmation]');
    await expect(confirmation).toContainText(/Obligación creada correctamente\. Obligación n\.º \d+\./);
    await confirmation.getByRole('link', { name: 'Ver detalle de la obligación' }).click();

    await expect(page.getByRole('heading', { level: 1, name: 'Mora' })).toBeVisible();
    expect(normalized(await info(page, 'Valor').textContent())).toBe('$ 18.000,00');
    expect(normalized(await info(page, 'Saldo').textContent())).toBe('$ 18.000,00');
    await expect(info(page, 'Estado')).toHaveText('Pendiente');
    const relation = page.getByRole('region', { name: 'Relación' });
    const originLink = relation.getByRole('link', { name: new RegExp(`^n\\.º ${origin} · Mensualidad`) });
    await expect(originLink).toBeVisible();
    await originLink.click();
    await expect(page).toHaveURL(new RegExp(`/admin/obligations/${origin}$`));
    await expect(page.getByRole('region', { name: 'Relación' }).getByText('Moras derivadas')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Relación' }).getByRole('link', { name: / · Mora · / })).toHaveCount(1);
  });

  test('penalty: the description is required, then the obligation is created', async ({ admin: { page }, seed }) => {
    const form = await startCharge(page, 'B1-TO1-APTO-104', 'Penalización');
    const description = form.getByLabel('Descripción');
    await expect(description).toBeVisible();
    await expect(description).toHaveAccessibleName('Descripción'); // required: no "(opcional)"
    await form.getByLabel('Monto').fill('45000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 15));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(form.locator('[data-charge-error="description"]')).toHaveText('Escribe la descripción.');
    await expect(description).toHaveAttribute('aria-invalid', 'true');
    await expect(description).toBeFocused();

    await description.fill('Ruido después de las 10 p. m.');
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await page.locator('[data-charge-confirmation]').getByRole('link', { name: 'Ver detalle de la obligación' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Penalización: Ruido después de las 10 p. m.' })).toBeVisible();
    await expect(info(page, 'Concepto')).toHaveText('Penalización');
    await expect(info(page, 'Descripción')).toHaveText('Ruido después de las 10 p. m.');
    expect(normalized(await info(page, 'Valor').textContent())).toBe('$ 45.000,00');
    expect(normalized(await info(page, 'Aplicado').textContent())).toBe('$ 0,00');
    expect(normalized(await info(page, 'Saldo').textContent())).toBe('$ 45.000,00');
    await expect(info(page, 'Estado')).toHaveText('Pendiente');
  });

  test('a double click on "Crear obligación" creates one obligation', async ({ admin: { page, api }, seed }) => {
    const form = await startCharge(page, 'B1-TO1-APTO-105', 'Otro');
    await form.getByLabel('Descripción').fill('Copia de llaves');
    await form.getByLabel('Monto').fill('15000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 20));
    const sent: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/api/v1/admin/charges')) sent.push(request.url());
    });
    await form.getByRole('button', { name: 'Crear obligación' }).dblclick();
    await expect(page.locator('[data-charge-confirmation]')).toBeVisible();
    expect(sent).toHaveLength(1);
    const page105 = await api.json<{ total: number }>(await api.get(`/admin/charges?unit_id=${seed.units['B1-TO1-APTO-105']}`));
    expect(page105.total).toBe(1);
  });
});

test.describe('financial errors in the form', () => {
  test('duplicated monthly fee (409) and format errors', async ({ admin: { page, api }, seed }) => {
    const period = periodOf(seed.today, 1);
    await generateMonthly(api, period, '180000', `${period}-10`);

    const form = await startCharge(page, UNIT, 'Mensualidad');
    await expect(form.getByLabel('Descripción')).toBeHidden();
    // Format help before any request.
    await form.getByLabel('Monto').fill('0');
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(form.locator('[data-charge-error="billing_period"]')).toHaveText('Selecciona el periodo de la mensualidad.');
    await expect(form.locator('[data-charge-error="amount"]')).toHaveText('El monto debe ser mayor que cero.');
    await expect(form.locator('[data-charge-error="due_date"]')).toHaveText('Selecciona la fecha de vencimiento.');
    await form.getByLabel('Monto').fill('150000,555');
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(form.locator('[data-charge-error="amount"]')).toHaveText(/sin puntos de miles/);

    await form.locator('#charge-period').fill(period);
    await form.getByLabel('Monto').fill('180000');
    await form.getByLabel('Vencimiento').fill(`${period}-10`);
    const answer = page.waitForResponse((r) => r.url().endsWith('/api/v1/admin/charges') && r.request().method() === 'POST');
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    expect((await answer).status()).toBe(409);
    await expect(form.locator('[data-charge-error="billing_period"]')).toHaveText(`${UNIT} ya tiene la mensualidad de ${period}.`);
    await expect(form.getByRole('alert').filter({ hasText: `${UNIT} ya tiene la mensualidad de ${period}.` })).toBeVisible();
    expect(await page.content()).not.toMatch(/Traceback|IntegrityError|Exception/);
  });

  test('invalid late fees: origin not overdue (422) and origin that already has a late fee (409)', async ({ admin: { page, api }, seed }) => {
    const unitId = seed.units[UNIT];
    const notDue = (await createCharge(api, { unit_id: unitId, concept: 'PENALTY', amount: '10000', due_date: addDays(seed.today, 10), description: 'Basura fuera de horario' })).id;
    const origin = await overdueFee(api, seed.today, unitId);
    await createCharge(api, { unit_id: unitId, concept: 'LATE_FEE', amount: '1800', due_date: addDays(seed.today, 5), source_charge_id: origin });

    const form = await startCharge(page, UNIT, 'Mora');
    const source = form.getByLabel('Obligación de origen');
    await source.selectOption(String(notDue));
    await form.getByLabel('Monto').fill('1000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 10));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(form.locator('[data-charge-error="source_charge_id"]')).toHaveText('La obligación de origen todavía no está vencida.');
    await expect(source).toHaveAttribute('aria-invalid', 'true');

    await source.selectOption(String(origin));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    await expect(form.locator('[data-charge-error="source_charge_id"]')).toHaveText('La obligación de origen ya tiene una mora.');
  });

  test('origins the form never offers are refused by the API (422 by field)', async ({ admin: { api }, seed }) => {
    const unitId = seed.units[UNIT];
    const otherUnitOrigin = await overdueFee(api, seed.today, seed.units['B1-TO1-APTO-104']);
    const origin = await overdueFee(api, seed.today, unitId);
    const lateFee = (await createCharge(api, { unit_id: unitId, concept: 'LATE_FEE', amount: '1800', due_date: addDays(seed.today, 5), source_charge_id: origin })).id;

    for (const [source, message] of [
      [otherUnitOrigin, 'La obligación de origen es de otro apartamento.'],
      [lateFee, 'Una mora no puede originar otra mora.']
    ] as const) {
      const response = await api.post('/admin/charges', { unit_id: unitId, concept: 'LATE_FEE', amount: '1000', due_date: addDays(seed.today, 10), source_charge_id: source });
      expect(response.status()).toBe(422);
      expect(response.headers()['cache-control']).toBe('no-store');
      const body = (await response.json()) as { error: { code: string; details: { field: string; message: string }[] } };
      expect(body.error.details).toContainEqual(expect.objectContaining({ field: 'source_charge_id', message }));
    }
  });
});
