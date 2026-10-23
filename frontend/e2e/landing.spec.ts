import type { Page } from '@playwright/test';
import { expect, normalized, test } from './support/fixtures';

/**
 * The public landing page ("/"), in a real browser, on the E2E stack (playwright.config.ts). It needs no
 * session and reads no data; access goes through the real login page (/login). The dashboard and the table
 * are illustrative.
 */

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth);

test.describe('landing page', () => {
  test('public without a session: the hero, the sections and the illustrative notes', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle('SoftPh — Gestión de propiedad horizontal');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tu comunidad, mejor administrada.');
    for (const title of ['Todo lo que necesitas para gestionar tu comunidad', 'Información clara para tomar mejores decisiones',
      'Proceso simple y transparente para tu conjunto', 'Una experiencia pensada para cada rol', 'Una gestión más organizada empieza aquí']) {
      await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible();
    }
    await expect(page.getByRole('img', { name: /Vista ilustrativa del resumen administrativo/ })).toBeVisible();
    await expect(page.getByText('Vista ilustrativa con datos de ejemplo.')).toHaveCount(2);
    await expect(page.getByText(/Cada residente consulta únicamente su propia información/)).toBeVisible();
    // Nothing the product does not do: no export buttons or files in the illustrative views.
    await expect(page.getByRole('main').getByText(/Exportar|Excel|XLS/i)).toHaveCount(0);
    expect(await noHorizontalScroll(page)).toBe(true);
  });

  test('the footer sign-in opens the real login page', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('contentinfo').getByRole('link', { name: 'Iniciar sesión' }).click();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('the section links lead to their sections; "Ingresar a SoftPh" opens the real login page', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Secciones' }).first();
    for (const [label, id] of [['Funcionalidades', 'funcionalidades'], ['Cómo funciona', 'como-funciona'], ['Beneficios', 'beneficios']]) {
      await nav.getByRole('link', { name: label }).click();
      await expect(page).toHaveURL(new RegExp(`#${id}$`));
      await expect(page.locator(`#${id}`)).toBeInViewport();
    }
    await page.getByRole('main').getByRole('link', { name: 'Conocer funcionalidades' }).click();
    await expect(page.locator('#funcionalidades')).toBeInViewport();

    await page.getByRole('banner').getByRole('link', { name: 'Ingresar a SoftPh' }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Iniciar sesión' })).toBeVisible();
  });

  test('with a session the page stays public, and the access sends each role to its own home page', async ({ page, signIn }) => {
    await signIn('resident');
    expect((await page.goto('/'))?.status()).toBe(200);
    await page.getByRole('banner').getByRole('link', { name: 'Ingresar a SoftPh' }).click();
    await expect(page).toHaveURL(/\/resident$/);

    await signIn('admin');
    await page.goto('/');
    await page.getByRole('link', { name: 'Ingresar a SoftPh' }).last().click(); // the final call to action
    await expect(page).toHaveURL(/\/admin$/);
  });

  test('the illustrative table: its totals match its rows', async ({ page }) => {
    await page.goto('/');
    const preview = page.locator('[data-financial-preview]');
    await expect(preview.locator('[data-example-row]')).toHaveCount(5);
    const totals: Record<string, number> = {};
    for (const row of await preview.locator('[data-example-row]').all()) {
      const status = normalized(await row.locator('td[data-label="Estado"]').textContent());
      const amount = Number(normalized(await row.locator('td[data-label="Valor"]').textContent()).replace(/\D/g, ''));
      totals[status] = (totals[status] ?? 0) + amount;
    }
    for (const status of ['Pagado', 'Pendiente', 'En mora']) {
      const shown = Number(normalized(await preview.locator(`[data-example-total="${status}"]`).textContent()).replace(/\D/g, ''));
      expect(shown, status).toBe(totals[status]);
    }
  });
});

test.describe('360 × 800', () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

  test('the menu opens and closes (button, Escape, a chosen link); nothing scrolls sideways; keyboard first stop', async ({ page }) => {
    await page.goto('/');
    expect(await noHorizontalScroll(page)).toBe(true);
    // The first Tab reaches the skip link.
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Saltar al contenido' })).toBeFocused();

    const button = page.getByRole('button', { name: 'Abrir menú' });
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await button.click();
    const close = page.getByRole('button', { name: 'Cerrar menú' });
    await expect(close).toHaveAttribute('aria-expanded', 'true');
    const menu = page.locator('#site-menu');
    await expect(menu).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    await expect(page.getByRole('button', { name: 'Abrir menú' })).toBeFocused();

    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await menu.getByRole('link', { name: 'Cómo funciona' }).click();
    await expect(menu).toBeHidden();
    await expect(page).toHaveURL(/#como-funciona$/);
    await expect(page.locator('#como-funciona')).toBeInViewport();

    await page.getByRole('button', { name: 'Abrir menú' }).click();
    await menu.getByRole('link', { name: 'Ingresar a SoftPh' }).click();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('the table stacks with its labels and every section fits the screen', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-financial-preview] thead')).toBeHidden();
    await expect(page.locator('[data-example-row]').first().locator('td[data-label="Unidad"]')).toBeVisible();
    for (const section of await page.locator('main > section').all()) {
      const box = await section.boundingBox();
      expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);
    }
    expect(await noHorizontalScroll(page)).toBe(true);
  });
});

for (const width of [820, 1024]) {
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test('nothing scrolls sideways and the hero actions stay inside their column', async ({ page }) => {
      await page.goto('/');
      expect(await noHorizontalScroll(page)).toBe(true);
      const preview = await page.getByRole('img', { name: /Vista ilustrativa del resumen administrativo/ }).boundingBox();
      const hero = page.locator('#inicio');
      for (const name of ['Ingresar a SoftPh', 'Conocer funcionalidades']) {
        const box = await hero.getByRole('link', { name }).boundingBox();
        expect(box && preview, name).toBeTruthy();
        // Side by side (wide screens) the action ends before the preview; stacked (tablet) it sits above it.
        const clear = box!.x + box!.width <= preview!.x || box!.y + box!.height <= preview!.y;
        expect(clear, name).toBe(true);
      }
    });
  });
}
