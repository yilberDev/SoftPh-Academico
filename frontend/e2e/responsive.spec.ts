import type { Locator, Page } from '@playwright/test';
import { addDays, periodOf } from './support/data';
import { chooseUnit, expect, generateMonthly, openNewCharge, test } from './support/fixtures';

/**
 * E2E 14: 360 px wide, in a real browser (Chromium), measured on the rendered page:
 * no horizontal scroll, one-column forms and filters, stacked rows with their
 * labels, readable errors and usable buttons, on the list and on the detail.
 */

test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

const WIDTH = 360;

/** The page never scrolls sideways, and nothing sticks out of the viewport (outside a scroll area). */
async function expectNoOverflow(page: Page, step: string): Promise<void> {
  const result = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const outside = [...document.querySelectorAll<HTMLElement>('body *')]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) return false;
        const style = getComputedStyle(element);
        if (style.visibility === 'hidden') return false;
        // Inside a closed <dialog> or an area that scrolls on its own: not part of the page width.
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const overflow = getComputedStyle(parent).overflowX;
          if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return false;
        }
        return box.left < -1 || box.right > width + 1;
      })
      .slice(0, 5)
      .map((element) => `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''} ${element.className}`.slice(0, 120));
    return { scrollWidth: document.documentElement.scrollWidth, width, outside };
  });
  expect(result.scrollWidth, `${step}: horizontal scroll of the page`).toBeLessThanOrEqual(result.width);
  expect(result.outside, `${step}: elements outside the viewport`).toEqual([]);
}

/**
 * A control inside the viewport that receives a tap (nothing covers it). Buttons are
 * at least 30 px high; text links keep the project's inline style (about 20 px).
 */
async function expectUsable(control: Locator, minHeight = 30): Promise<void> {
  await expect(control).toBeVisible();
  const box = (await control.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(WIDTH);
  expect(box.height).toBeGreaterThanOrEqual(minHeight);
  await control.click({ trial: true });
}

const LINK = 16;

/** Every control starts at the same x (one column) and fills most of the width. */
async function expectOneColumn(controls: Locator[]): Promise<void> {
  const boxes = await Promise.all(controls.map(async (control) => (await control.boundingBox())!));
  for (const box of boxes) {
    expect(Math.abs(box.x - boxes[0].x)).toBeLessThanOrEqual(1);
    expect(box.width).toBeGreaterThan(WIDTH * 0.7);
  }
}

test.describe('360 px', () => {
  test('/admin/obligations: panels, forms, errors, filters and stacked rows', async ({ admin: { page, api }, seed }) => {
    const period = periodOf(seed.today, 1);
    await generateMonthly(api, period, '180000', `${period}-10`);
    await page.goto('/admin/obligations');
    await expect(page.locator('[data-status-line]')).toHaveText('Mostrando 1–10 de 14 obligaciones');
    await expectNoOverflow(page, 'list');

    // Filters stack in one column.
    const filters = page.getByRole('search', { name: 'Filtros de obligaciones' });
    await expectOneColumn(
      ['Bloque', 'Torre', 'Apartamento', 'Concepto', 'Estado', 'Periodo', 'Vence desde', 'Vence hasta', 'Ordenar por', 'Sentido'].map(
        (label) => filters.getByLabel(label)
      )
    );
    await expectUsable(filters.getByRole('button', { name: 'Limpiar filtros' }));

    // Rows stack: no table header, every cell inside the viewport with its label.
    await expect(page.getByRole('columnheader', { name: 'Valor' })).toBeHidden();
    const firstRow = page.locator('[data-rows] tr').first();
    expect(await firstRow.evaluate((row) => getComputedStyle(row).display)).toBe('block');
    const labels = await firstRow.locator('td[data-label]').evaluateAll((cells) =>
      cells.map((cell) => getComputedStyle(cell, '::before').content.replace(/"/g, ''))
    );
    expect(labels).toEqual(['Concepto', 'Periodo', 'Vence', 'Valor', 'Aplicado', 'Saldo', 'Estado']);
    for (const cell of await firstRow.locator('th, td').all()) {
      const box = (await cell.boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(WIDTH);
    }
    await expectUsable(firstRow.getByRole('link', { name: /Ver detalle/ }), LINK);
    await expectUsable(page.getByRole('button', { name: 'Siguiente' }));

    // Monthly generation: one column, readable errors, the preview and the 409-free flow.
    await page.getByText('Generar mensualidades', { exact: true }).click();
    const monthly = page.locator('[data-monthly-form]');
    await expectOneColumn([monthly.getByLabel('Periodo'), monthly.getByLabel('Monto por apartamento'), monthly.getByLabel('Vencimiento')]);
    await page.getByRole('button', { name: 'Revisar' }).click();
    for (const field of ['billing_period', 'amount', 'due_date']) {
      const error = page.locator(`[data-monthly-error="${field}"]`);
      await expect(error).toBeVisible();
      const box = (await error.boundingBox())!;
      expect(box.x + box.width, field).toBeLessThanOrEqual(WIDTH);
      expect(await error.evaluate((element) => element.scrollWidth <= element.clientWidth), field).toBe(true);
    }
    await expectNoOverflow(page, 'monthly errors');
    await monthly.getByLabel('Periodo').fill(period);
    await monthly.getByLabel('Monto por apartamento').fill('180000');
    await monthly.getByLabel('Vencimiento').fill(`${period}-10`);
    await page.getByRole('button', { name: 'Revisar' }).click();
    await expect(page.getByText('Todas las unidades activas ya tienen esta mensualidad')).toBeVisible();
    await expectNoOverflow(page, 'preview with its fingerprint');
    await monthly.getByLabel('Periodo').fill(periodOf(seed.today, 2));
    await monthly.getByLabel('Vencimiento').fill(`${periodOf(seed.today, 2)}-10`);
    await page.getByRole('button', { name: 'Revisar' }).click();
    await expectUsable(page.getByRole('button', { name: 'Confirmar y generar' }));
    await page.getByRole('button', { name: 'Confirmar y generar' }).click();
    await expect(page.getByRole('region', { name: 'Generación terminada' })).toBeVisible();
    await expectNoOverflow(page, 'generation result');

    // New obligation: one column, the late fee origin (long option texts) and a business error.
    const form = await openNewCharge(page);
    await chooseUnit(page, 'charge', 'B2-TO1-APTO-206');
    await form.getByLabel('Concepto').selectOption({ label: 'Mora' });
    await expect(form.getByLabel('Obligación de origen')).toBeEnabled();
    await expectOneColumn(
      ['Bloque', 'Torre', 'Apartamento', 'Concepto', 'Obligación de origen', 'Descripción', 'Monto', 'Vencimiento'].map((label) =>
        form.getByLabel(label)
      )
    );
    await form.getByLabel('Obligación de origen').selectOption(String(seed.charges.debt));
    await form.getByLabel('Monto').fill('3000');
    await form.getByLabel('Vencimiento').fill(addDays(seed.today, 20));
    await form.getByRole('button', { name: 'Crear obligación' }).click();
    const sourceError = form.locator('[data-charge-error="source_charge_id"]');
    await expect(sourceError).toHaveText('La obligación de origen todavía no está vencida.');
    expect(await sourceError.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expectUsable(form.getByRole('button', { name: 'Crear obligación' }));
    await expectNoOverflow(page, 'new obligation with an error');
  });

  test('/admin/obligations/{id}: information, relations and the applications table', async ({ admin: { page }, seed }) => {
    await page.goto(`/admin/obligations/${seed.charges.history}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Otro: Reparación de la puerta E2E');
    await expectNoOverflow(page, 'detail');

    const information = page.getByRole('region', { name: 'Información' });
    const values = information.locator('dd');
    await expect(values).toHaveCount(11);
    for (const value of await values.all()) {
      const box = (await value.boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(WIDTH);
    }

    const table = page.getByRole('region', { name: 'Pagos aplicados' }).getByRole('table');
    const row = table.locator('tbody tr').first();
    expect(await row.evaluate((element) => getComputedStyle(element).display)).toBe('block');
    for (const cell of await row.locator('th, td').all()) {
      await expect(cell).toBeVisible();
      const box = (await cell.boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(WIDTH);
    }
    const labels = await row.locator('td').evaluateAll((cells) => cells.map((cell) => getComputedStyle(cell, '::before').content.replace(/"/g, '')));
    expect(labels).toEqual(['Fecha del pago', 'Método', 'Monto aplicado', 'Aplicado el']);

    await expectUsable(page.getByRole('link', { name: 'Volver a las obligaciones' }).last(), LINK);
    await expectUsable(page.getByRole('navigation', { name: 'Principal' }).getByRole('link', { name: 'Obligaciones' }), LINK);
  });
});
