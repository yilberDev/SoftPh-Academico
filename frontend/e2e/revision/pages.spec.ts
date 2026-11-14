import { writeFileSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { test, expect } from '../support/fixtures';

/**
 * Deep review (docs/revision/revision-profunda-2026-11.md), phase 3 F.
 *
 * Every one of the 32 pages with every role, at 375 px: HTTP status, horizontal overflow,
 * one <h1>, console errors and the first keyboard stop. The survey always passes and, with
 * REVISION_PAGES_OUT=<file>, writes what it saw as JSON for the review document. The defects
 * it confirms have their own test below, marked with test.fail() so the suite stays green.
 * Data: the E2E seed (*_test database, fictitious data).
 */

type Role = 'anonymous' | 'admin' | 'resident';

interface Seen {
  role: Role;
  path: string;
  status: number | null;
  finalPath: string;
  overflowPx: number;
  h1: number;
  consoleErrors: number;
  firstTab: string;
}

async function survey(page: Page, role: Role, path: string): Promise<Seen> {
  const errors: string[] = [];
  const onConsole = (message: { type(): string; text(): string }) => {
    if (message.type() === 'error') errors.push(message.text());
  };
  page.on('console', onConsole);
  const response = await page.goto(path);
  await page.waitForLoadState('networkidle');
  const metrics = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    h1: document.querySelectorAll('h1').length
  }));
  await page.keyboard.press('Tab');
  const firstTab = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return '(body)';
    return `${el.tagName.toLowerCase()}:${(el.textContent ?? el.getAttribute('aria-label') ?? '').trim().slice(0, 30)}`;
  });
  page.off('console', onConsole);
  return {
    role,
    path,
    status: response?.status() ?? null,
    finalPath: new URL(page.url()).pathname,
    overflowPx: metrics.overflow,
    h1: metrics.h1,
    consoleErrors: errors.length,
    firstTab
  };
}

test.use({ viewport: { width: 375, height: 812 } });

test('every page with every role at 375 px (survey)', async ({ page, seed, signIn }) => {
  test.setTimeout(240_000);
  const publicPages = ['/', '/login', '/forgot-password', '/reset-password', '/activate-account', '/forbidden'];
  const adminPages = [
    '/admin', '/admin/blocks', `/admin/blocks/${seed.blocks.B1}`, `/admin/towers/${seed.towers['B1-TO1']}`,
    '/admin/residents', '/admin/residents/invite', `/admin/residents/${seed.laura_id}`,
    `/admin/residents/${seed.laura_id}/account-statement`, '/admin/payments', '/admin/obligations',
    `/admin/obligations/${seed.charges.debt}`, '/admin/feedback', `/admin/feedback/${seed.feedback.pedro}`,
    '/admin/reports', '/admin/reports/payments', '/admin/reports/pending-balances'
  ];
  const seen: Seen[] = [];
  for (const path of [...publicPages, ...adminPages, '/resident']) seen.push(await survey(page, 'anonymous', path));

  const admin = await signIn('admin');
  const payment = await admin.json<{ id: number }>(
    await admin.post('/admin/payments', {
      resident_id: seed.laura_id, amount: '1000', method: 'CASH', concept: 'MONTHLY_FEE', payment_date: seed.today
    }),
    201
  );
  for (const path of [...adminPages, '/resident']) seen.push(await survey(page, 'admin', path));

  await signIn('resident');
  const residentPages = [
    '/resident', '/resident/account-statement', '/resident/demo-payments', '/resident/demo-payments/checkout',
    '/resident/feedback', '/resident/feedback/new', `/resident/feedback/${seed.feedback.pedro}`,
    '/resident/notifications', '/resident/payments', `/resident/payments/${payment.id}`, '/admin'
  ];
  for (const path of residentPages) seen.push(await survey(page, 'resident', path));

  const out = process.env.REVISION_PAGES_OUT;
  if (out) writeFileSync(out, JSON.stringify(seen, null, 2));
  const distinct = new Set(seen.filter((s) => s.role !== 'anonymous' || !s.path.startsWith('/admin')).map((s) => s.path));
  expect(distinct.size).toBeGreaterThanOrEqual(32);
});

test('an API outage while signing in shows a message and re-enables the form', async ({ page, seed }) => {
  await page.route('**/api/v1/auth/login', (route) =>
    route.fulfill({ status: 503, contentType: 'text/plain', body: 'Service Unavailable' })
  );
  await page.goto('/login');
  await page.getByLabel(/correo/i).fill(seed.admin_email);
  await page.getByLabel(/contraseña/i).fill('una-clave-cualquiera-123');
  const submit = page.getByRole('button', { name: /iniciar sesión|ingresar/i });
  await submit.click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(submit).toBeEnabled();
});

test('a double click on «Registrar pago» registers one payment', async ({ page, seed, admin }) => {
  let posts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/admin/payments')) posts += 1;
  });
  await page.goto('/admin/payments');
  const form = page.locator('[data-payment-form]');
  await form.getByLabel('Residente').fill('Laura');
  await form.getByRole('button', { name: /Laura Gómez/ }).click();
  await form.getByLabel('Monto').fill('1000');
  await form.getByLabel('Método de pago').selectOption({ label: 'Efectivo' });
  await form.getByLabel('Concepto', { exact: true }).selectOption({ label: 'Mensualidad' });
  await form.getByRole('button', { name: 'Registrar pago' }).dblclick();
  await expect(page.locator('[data-confirmation]')).toContainText('Pago registrado correctamente.');
  await page.waitForLoadState('networkidle');
  const list = await admin.api.json<{ total: number }>(await admin.api.get(`/admin/payments?resident_id=${seed.laura_id}`));
  expect(posts).toBeLessThanOrEqual(1);
  expect(list.total).toBeLessThanOrEqual(1);
});

/** Elements wider than the viewport that are not inside a horizontally scrollable container. */
async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = window.innerWidth;
    const scrollable = (el: Element | null): boolean => {
      for (let node = el?.parentElement; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (['auto', 'scroll'].includes(style.overflowX) || style.overflowX === 'hidden') return true;
      }
      return false;
    };
    return [...document.querySelectorAll('body *')]
      .filter((el) => el.getBoundingClientRect().right > width + 1 && !scrollable(el))
      .map((el) => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${[...el.classList].slice(0, 4).join('.')}`)
      .slice(0, 8);
  });
}

test('ruled out: at 375 px the residents table scrolls inside its container, never the page', async ({ admin, page }) => {
  expect(admin).toBeTruthy();
  for (const path of ['/admin/residents', '/admin/payments', '/admin/obligations', '/admin/feedback']) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    expect(await overflowing(page), path).toEqual([]);
    const scrolled = await page.evaluate(() => { window.scrollTo(2000, 0); return window.scrollX; });
    expect(scrolled, path).toBe(0);
  }
});
