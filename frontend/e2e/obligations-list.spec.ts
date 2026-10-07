import type { Page } from '@playwright/test';
import { addDays, periodOf } from './support/data';
import { expect, generateMonthly, normalized, test, type Api } from './support/fixtures';

/**
 * E2E 9: filters, server-side sort and pagination of /admin/obligations.
 *
 * Data: the seed (B2-TO1-APTO-204 "Otro" paid, B2-TO1-APTO-206 penalty pending,
 * B2-TO1-APTO-205 with 50.000 of credit) plus next month's fees for the 12
 * apartments (the credit leaves B2-TO1-APTO-205's fee partial): 14 obligations.
 */

const SORTS = ['due_date', 'unit', 'concept', 'amount', 'outstanding', 'status', 'created'] as const;
const SORT_LABELS: Record<(typeof SORTS)[number], string> = {
  due_date: 'Vence',
  unit: 'Unidad',
  concept: 'Concepto',
  amount: 'Valor',
  outstanding: 'Saldo',
  status: 'Estado',
  created: 'Fecha de creación'
};

const statusLine = (page: Page) => page.locator('[data-status-line]');
const filters = (page: Page) => page.getByRole('search', { name: 'Filtros de obligaciones' });
const rows = (page: Page) => page.locator('[data-rows] tr');

async function prepare(api: Api, today: string): Promise<string> {
  const period = periodOf(today, 1);
  // Day 20: never inside the "today + 10" range used below, whatever today is.
  await generateMonthly(api, period, '180000', `${period}-20`);
  return period;
}

/** Goes to page 2, so a change that must go back to page 1 is visible. */
async function toSecondPage(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Siguiente' }).click();
  await expect(page.locator('[data-page-info]')).toHaveText(/^Página 2 de \d+$/);
  await expect(page).toHaveURL(/[?&]page=2/);
}

test.describe('obligations list', () => {
  test('every filter changes the result, stays in the URL and goes back to page 1', async ({ admin: { page, api }, seed }) => {
    const period = await prepare(api, seed.today);
    await page.goto('/admin/obligations');
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 14 obligaciones');

    await toSecondPage(page);
    await filters(page).getByLabel('Bloque').selectOption({ label: 'B2' });
    await expect(statusLine(page)).toHaveText('Mostrando 1–8 de 8 obligaciones');
    await expect(page).toHaveURL(new RegExp(`block_id=${seed.blocks.B2}`));
    await expect(page).not.toHaveURL(/[?&]page=/);
    await expect(page.locator('[data-filter-count]')).toHaveText('1 filtro aplicado');

    await filters(page).getByLabel('Torre').selectOption({ label: 'TO1' });
    await expect(page).toHaveURL(new RegExp(`tower_id=${seed.towers['B2-TO1']}`));
    await expect(statusLine(page)).toHaveText('Mostrando 1–8 de 8 obligaciones');
    await filters(page).getByLabel('Apartamento').selectOption({ label: 'B2-TO1-APTO-206' });
    await expect(page).toHaveURL(new RegExp(`unit_id=${seed.units['B2-TO1-APTO-206']}`));
    await expect(statusLine(page)).toHaveText('Mostrando 1–2 de 2 obligaciones');
    for (const row of await rows(page).all()) await expect(row).toContainText('B2-TO1-APTO-206');

    // The filters survive a reload (they live in the URL) and are restored in the selects.
    await page.reload();
    await expect(statusLine(page)).toHaveText('Mostrando 1–2 de 2 obligaciones');
    await expect(filters(page).getByLabel('Apartamento')).toHaveValue(String(seed.units['B2-TO1-APTO-206']));

    await page.getByRole('button', { name: 'Limpiar filtros' }).click();
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 14 obligaciones');
    await expect(page).toHaveURL(/\/admin\/obligations$/);

    const cases: [string, () => Promise<void>, RegExp, string][] = [
      ['concept', () => filters(page).getByLabel('Concepto').selectOption({ label: 'Penalización' }).then(() => undefined), /concept=PENALTY/, 'Mostrando 1–1 de 1 obligación'],
      ['status PAID', () => filters(page).getByLabel('Estado').selectOption({ label: 'Pagada' }).then(() => undefined), /status=PAID/, 'Mostrando 1–1 de 1 obligación'],
      ['status PARTIAL', () => filters(page).getByLabel('Estado').selectOption({ label: 'Parcial' }).then(() => undefined), /status=PARTIAL/, 'Mostrando 1–1 de 1 obligación'],
      ['period', () => filters(page).getByLabel('Periodo').fill(period), new RegExp(`billing_period=${period}`), 'Mostrando 1–10 de 12 obligaciones'],
      ['due range', async () => {
        await filters(page).getByLabel('Vence desde').fill(addDays(seed.today, 10));
        await filters(page).getByLabel('Vence hasta').fill(addDays(seed.today, 10));
      }, new RegExp(`due_from=${addDays(seed.today, 10)}&due_to=${addDays(seed.today, 10)}`), 'Mostrando 1–1 de 1 obligación']
    ];
    for (const [name, apply, url, text] of cases) {
      await page.goto('/admin/obligations');
      await toSecondPage(page);
      await apply();
      await expect(page, name).toHaveURL(url);
      await expect(page, name).not.toHaveURL(/[?&]page=/);
      await expect(statusLine(page), name).toHaveText(text);
    }
    // The status shown is the backend's.
    await page.goto('/admin/obligations?status=PAID');
    await expect(rows(page)).toContainText(['Pagada']);
    await expect(rows(page).first()).toContainText('B2-TO1-APTO-204');
    await page.goto(`/admin/obligations?status=PARTIAL`);
    await expect(rows(page).first()).toContainText('B2-TO1-APTO-205');
    await expect(rows(page).first()).toContainText('Parcial');

    // A due range the API refuses never leaves the page.
    await page.goto('/admin/obligations');
    await filters(page).getByLabel('Vence desde').fill(addDays(seed.today, 10));
    await filters(page).getByLabel('Vence hasta').fill(addDays(seed.today, 1));
    await expect(page.getByText('La fecha final no puede ser anterior a la inicial.')).toBeVisible();
  });

  test('the 7 sort fields, ascending and descending, in the order of the server', async ({ admin: { page, api }, seed }) => {
    await prepare(api, seed.today);
    await page.goto('/admin/obligations');
    for (const field of SORTS) {
      for (const order of ['asc', 'desc'] as const) {
        await toSecondPage(page);
        await filters(page).getByLabel('Ordenar por').selectOption({ label: SORT_LABELS[field] });
        await filters(page).getByLabel('Sentido').selectOption(order);
        await expect(page).not.toHaveURL(/[?&]page=/);
        await expect(page.locator('[data-page-info]')).toHaveText(/^Página 1 de 2$/);
        const expected = await api.json<{ items: { id: number }[] }>(await api.get(`/admin/charges?sort_by=${field}&sort_order=${order}`));
        // The rows are exactly the API's page, in its order (nothing sorted in the browser).
        const links = rows(page).getByRole('link', { name: /Ver detalle/ });
        const shown = () => links.evaluateAll((elements) => elements.map((a) => Number(a.getAttribute('href')!.split('/').pop())));
        await expect.poll(shown, { message: `${field} ${order}` }).toEqual(expected.items.map((item) => item.id));
        if (field !== 'due_date' || order !== 'asc') {
          // The default order (due_date asc) is left out of the URL.
          await expect(page).toHaveURL(new RegExp(field === 'due_date' ? `sort_order=${order}` : `sort_by=${field}`));
        }
      }
    }
    // Clicking a column header: same server-side sort, toggling the direction.
    await page.getByRole('button', { name: 'Valor' }).click();
    await expect(page).toHaveURL(/sort_by=amount&sort_order=desc/);
    await expect(page.getByRole('columnheader', { name: 'Valor' })).toHaveAttribute('aria-sort', 'descending');
    await page.getByRole('button', { name: 'Valor' }).click();
    await expect(page.getByRole('columnheader', { name: 'Valor' })).toHaveAttribute('aria-sort', 'ascending');
    await expect.poll(async () => normalized(await rows(page).first().textContent())).toContain('$ 30.000,00');
  });

  test('pagination is done by the server, one page at a time', async ({ admin: { page, api }, seed }) => {
    await prepare(api, seed.today);
    const requests: URL[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/admin/charges') requests.push(url);
    });
    await page.goto('/admin/obligations');
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 14 obligaciones');
    await expect(rows(page)).toHaveCount(10);
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeDisabled();

    const second = page.waitForResponse((r) => r.url().includes('/api/v1/admin/charges?page=2'));
    await page.getByRole('button', { name: 'Siguiente' }).click();
    const body = (await (await second).json()) as { items: unknown[]; page: number; page_size: number; total: number };
    expect(body).toMatchObject({ page: 2, page_size: 10, total: 14 });
    expect(body.items).toHaveLength(4);
    await expect(statusLine(page)).toHaveText('Mostrando 11–14 de 14 obligaciones');
    await expect(rows(page)).toHaveCount(4);
    await expect(page.getByRole('button', { name: 'Siguiente' })).toBeDisabled();

    await page.getByLabel('Por página').selectOption('25');
    await expect(statusLine(page)).toHaveText('Mostrando 1–14 de 14 obligaciones');
    await expect(page).toHaveURL(/page_size=25/);
    // Every request asked for one page: never the whole table.
    for (const url of requests) {
      expect(Number(url.searchParams.get('page_size') ?? '10')).toBeLessThanOrEqual(25);
    }
  });
});
