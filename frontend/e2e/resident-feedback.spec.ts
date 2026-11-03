import type { Locator, Page, Response } from '@playwright/test';
import { expect, normalized, test, type Api } from './support/fixtures';

/**
 * HU-009 "Realización de feedback por el residente", in a real browser, with the
 * HU-005 administration it reaches: /resident/feedback (new, list, detail) as
 * Laura Gómez (RESIDENTE) and /admin/feedback as the administrator, with the
 * normal login only (no resident, user or role is ever sent).
 *
 * Every test starts from the seed (reset): Laura has no feedback; Pedro Ruiz has
 * one of HU-009 ("Agua en la torre E2E", Queja, Alta) and one stored before
 * HU-009 (no category, no urgency). The resident's feedback is sent through the
 * page or, as data preparation, through the same resident API the page uses;
 * the administrator answers through the HU-005 page or API. Nothing is mocked:
 * a held request is still sent to the real backend.
 */

const COMPLETE = {
  subject: 'Ruido en la torre 1 E2E',
  message: 'Hay ruido después de las 10 p. m.\nTodos los días (E2E).',
  category: { label: 'Queja', code: 'COMPLAINT' },
  urgency: { label: 'Alta', code: 'HIGH' }
};
const PEDRO = { subject: 'Agua en la torre E2E', message: 'No hay agua desde la mañana (Pedro E2E).', name: 'Pedro Ruiz' };
const NOT_FOUND = 'Feedback no encontrado.';
const RESPONSE_KEYS = ['admin_response', 'category', 'created_at', 'id', 'message', 'resolved_at', 'status', 'subject', 'urgency'];

interface Sent {
  id: number;
  subject: string | null;
  message: string;
  category: string | null;
  urgency: string | null;
  status: string;
  created_at: string;
  admin_response: string | null;
  resolved_at: string | null;
}

// --- Helpers ----------------------------------------------------------------------------------------------

const isPost = (response: Response) =>
  response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/resident/feedback';

/** Collects every POST of a new feedback made by the page (the real requests). */
function watchPosts(page: Page): string[] {
  const bodies: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/resident/feedback') {
      bodies.push(request.postData() ?? '');
    }
  });
  return bodies;
}

const form = (page: Page) => page.locator('[data-feedback-form]');
const sendButton = (page: Page) => form(page).getByRole('button', { name: /Enviar feedback|Enviando/ });
const confirmation = (page: Page) => page.locator('[data-confirmation]');
const statusLine = (page: Page) => page.locator('[data-status-line]');
const rows = (page: Page) => page.locator('[data-rows] tr');
const cell = (row: Locator, label: string) => row.locator(`td[data-label="${label}"]`);

async function openForm(page: Page): Promise<void> {
  await page.goto('/resident/feedback/new');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Enviar feedback');
}

async function fill(page: Page, values: { subject?: string; message?: string; category?: string; urgency?: string }): Promise<void> {
  if (values.subject !== undefined) await page.getByLabel('Asunto').fill(values.subject);
  if (values.message !== undefined) await page.getByLabel('Mensaje').fill(values.message);
  if (values.category !== undefined) await page.getByLabel('Categoría').selectOption({ label: values.category });
  if (values.urgency !== undefined) await page.getByLabel('Urgencia').selectOption({ label: values.urgency });
}

/** Sends the form and returns the real answer of the API. */
async function send(page: Page): Promise<Response> {
  const [response] = await Promise.all([page.waitForResponse(isPost), sendButton(page).click()]);
  return response;
}

async function openList(page: Page, query = ''): Promise<void> {
  await page.goto(`/resident/feedback${query}`);
  await expect(statusLine(page)).not.toHaveText('Consultando tus feedback...');
}

/** The resident's own feedback, as the API counts them. */
async function myTotal(api: Api): Promise<number> {
  return (await api.json<{ total: number }>(await api.get('/resident/feedback'))).total;
}

/** Data preparation: a feedback sent through the resident API (the one the page uses). */
async function sendThroughApi(api: Api, values: Partial<Record<'subject' | 'message' | 'category' | 'urgency', string | null>> = {}): Promise<Sent> {
  return api.json<Sent>(
    await api.post('/resident/feedback', {
      subject: COMPLETE.subject, message: COMPLETE.message, category: COMPLETE.category.code, urgency: COMPLETE.urgency.code, ...values
    }),
    201
  );
}

function definition(scope: Locator, label: string): Locator {
  return scope.locator('div').filter({ has: scope.page().getByText(label, { exact: true }) }).locator('dd');
}

const information = (page: Page, label: string) => definition(page.getByRole('region', { name: 'Información del feedback' }), label);

/** "9 de octubre de 2026": the Bogotá calendar date of a UTC instant (the start of the date shown). */
function bogotaDate(iso: string): string {
  return new Intl.DateTimeFormat('es-CO', { timeZone: 'America/Bogota', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date(iso));
}

async function expectDetail(
  page: Page,
  sent: { subject: string; category: string; urgency: string; status: string; created_at: string; resolved_at?: string }
): Promise<void> {
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Detalle del feedback');
  const region = page.getByRole('region', { name: 'Información del feedback' });
  const labels = ['Fecha de envío', 'Asunto', 'Categoría', 'Urgencia', 'Estado'];
  await expect(region.locator('dt')).toHaveText(sent.resolved_at ? [...labels, 'Fecha de resolución'] : labels);
  if (sent.resolved_at) expect(normalized(await information(page, 'Fecha de resolución').textContent())).toContain(bogotaDate(sent.resolved_at));
  expect(normalized(await information(page, 'Fecha de envío').textContent())).toContain(bogotaDate(sent.created_at));
  await expect(information(page, 'Asunto')).toHaveText(sent.subject);
  await expect(information(page, 'Categoría')).toHaveText(sent.category);
  await expect(information(page, 'Urgencia')).toHaveText(sent.urgency);
  await expect(information(page, 'Estado')).toHaveText(sent.status);
}

async function expectNoOverflow(page: Page, step: string): Promise<void> {
  const check = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const outside = [...document.querySelectorAll<HTMLElement>('body *')]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        if (box.width === 0 || box.height === 0 || getComputedStyle(element).visibility === 'hidden') return false;
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const overflow = getComputedStyle(parent).overflowX;
          if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return false;
        }
        return box.left < -1 || box.right > width + 1;
      })
      .slice(0, 5)
      .map((element) => `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}`);
    return { equal: document.documentElement.scrollWidth === document.documentElement.clientWidth, outside };
  });
  expect(check.equal, `${step}: horizontal scroll of the page`).toBe(true);
  expect(check.outside, `${step}: elements outside the viewport`).toEqual([]);
}

async function expectInside(locator: Locator, width: number): Promise<void> {
  await expect(locator).toBeVisible();
  const box = (await locator.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
}

async function cellLabels(row: Locator): Promise<string[]> {
  return row.locator('td[data-label]').evaluateAll((cells) => cells.map((element) => getComputedStyle(element, '::before').content.replace(/"/g, '')));
}

/** The administrator answers and resolves through the HU-005 API (data preparation of the resident's view). */
async function answerAndResolve(admin: Api, id: number, response: string, note: string): Promise<void> {
  await admin.json(await admin.post(`/admin/feedback/${id}/response`, { response, version: 1 }));
  await admin.json(await admin.post(`/admin/feedback/${id}/resolve`, { resolution_note: note, version: 2 }));
}

// --- 1. Access ----------------------------------------------------------------------------------------------

test.describe('access', () => {
  test('the resident reaches the form and "Mis feedback" from the panel and the header; aria-current marks it', async ({ page, signIn }) => {
    await signIn('resident');
    await page.goto('/resident');
    const main = page.getByRole('main');
    await expect(main.getByRole('link', { name: /Mis pagos/ })).toBeVisible(); // HU-008 is still there
    await expect(main.getByRole('link', { name: 'Enviar feedback' })).toBeVisible();
    await expect(main.getByRole('link', { name: 'Mis feedback' })).toBeVisible();

    await main.getByRole('link', { name: 'Mis feedback' }).click();
    await expect(page).toHaveURL(/\/resident\/feedback$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Mis feedback');
    await expect(page.getByText('No tienes feedback registrados.')).toBeVisible();
    const nav = page.getByRole('navigation', { name: 'Principal' });
    await expect(nav.getByRole('link', { name: 'Mis feedback' })).toHaveAttribute('aria-current', 'page');
    await expect(nav.getByRole('link', { name: 'Mis pagos' })).not.toHaveAttribute('aria-current', 'page');

    await main.getByRole('link', { name: 'Enviar feedback' }).first().click();
    await expect(page).toHaveURL(/\/resident\/feedback\/new$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Enviar feedback');
    await expect(page.getByText('Comparte una queja, sugerencia o solicitud con la administración.')).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Mis feedback' })).toHaveAttribute('aria-current', 'page');

    await nav.getByRole('link', { name: 'Inicio' }).click();
    await expect(page).toHaveURL(/\/resident$/);
  });

  test('a resident gets 403 on the administration pages and on its API, without any feedback data', async ({ page, seed, signIn }) => {
    const api = await signIn('resident');
    for (const path of ['/admin/feedback', `/admin/feedback/${seed.feedback.pedro}`]) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(403);
      await expect(page.getByRole('heading', { name: 'Acceso no autorizado' })).toBeVisible();
      const html = await page.content();
      for (const secret of [PEDRO.subject, PEDRO.message, PEDRO.name, 'pedro.e2e@example.com']) expect(html, secret).not.toContain(secret);
    }
    for (const path of ['/admin/feedback', `/admin/feedback/${seed.feedback.pedro}`]) {
      const response = await api.get(path);
      expect(response.status(), path).toBe(403); // (HU-005 marks no-store on its 200 answers; this 403 carries no data)
      const body = await response.text();
      expect(JSON.parse(body).error.code).toBe('FORBIDDEN');
      expect(body).not.toContain(PEDRO.subject);
    }
    // Responding or resolving is refused by the API too (the session is a resident's).
    const write = await api.post(`/admin/feedback/${seed.feedback.pedro}/resolve`, { resolution_note: 'x', version: 1 });
    expect(write.status()).toBe(403);
  });

  test('the administrator gets 403 on the resident pages and API; without a session the pages go to the login', async ({ admin: { page, api }, seed }) => {
    for (const path of ['/resident/feedback', '/resident/feedback/new', `/resident/feedback/${seed.feedback.pedro}`]) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(403);
      await expect(page.getByRole('main')).not.toContainText(PEDRO.subject);
    }
    expect((await api.get('/resident/feedback')).status()).toBe(403);
    const created = await api.post('/resident/feedback', { subject: null, message: 'x', category: 'OTHER', urgency: 'LOW' });
    expect(created.status()).toBe(403);

    await page.context().clearCookies();
    for (const path of ['/resident/feedback', '/resident/feedback/new', `/resident/feedback/${seed.feedback.pedro}`]) {
      await page.goto(path);
      await expect(page, path).toHaveURL(/\/login$/);
    }
  });
});

// --- 2. Sending ---------------------------------------------------------------------------------------------

test.describe('sending', () => {
  test('a complete feedback: the exact body, 201 PENDING, the confirmation, then the list and the detail (also after reloading)', async ({ page, signIn }) => {
    const api = await signIn('resident');
    await openForm(page);
    await expect(page.getByLabel('Categoría').locator('option')).toHaveText(['Selecciona una categoría', 'Queja', 'Sugerencia', 'Solicitud', 'Otros']);
    await expect(page.getByLabel('Urgencia').locator('option')).toHaveText(['Selecciona la urgencia', 'Alta', 'Media', 'Baja']);
    await fill(page, { subject: COMPLETE.subject, message: COMPLETE.message, category: COMPLETE.category.label, urgency: COMPLETE.urgency.label });
    await expect(page.getByText(`${[...COMPLETE.message].length} de 2000 caracteres`)).toBeVisible();

    const response = await send(page);
    // The real request of the browser: exactly four fields, the API codes, the CSRF token; never a person or a status.
    const request = response.request();
    const body = request.postDataJSON() as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['category', 'message', 'subject', 'urgency']);
    expect(body).toEqual({ subject: COMPLETE.subject, message: COMPLETE.message, category: 'COMPLAINT', urgency: 'HIGH' });
    const headers = await request.allHeaders();
    expect(headers['x-csrf-token']).toBeTruthy();
    for (const name of Object.keys(headers)) expect(name).not.toMatch(/resident|user-id|role/);
    // The real answer: 201, PENDING (decided by the backend), the resident's contract only.
    expect(response.status()).toBe(201);
    expect(response.headers()['cache-control']).toBe('no-store');
    const sent = (await response.json()) as Sent;
    expect(Object.keys(sent).sort()).toEqual(RESPONSE_KEYS);
    expect(sent).toMatchObject({ subject: COMPLETE.subject, message: COMPLETE.message, category: 'COMPLAINT', urgency: 'HIGH', status: 'PENDING' });

    await expect(confirmation(page)).toBeVisible();
    await expect(confirmation(page)).toContainText('Feedback enviado correctamente.');
    await expect(confirmation(page)).toContainText('Tu feedback quedó registrado como pendiente.');
    await expect(confirmation(page)).toBeFocused();
    // The form starts again, empty, ready for another one.
    await expect(page.getByLabel('Asunto')).toHaveValue('');
    await expect(page.getByLabel('Mensaje')).toHaveValue('');
    await expect(page.getByLabel('Categoría')).toHaveValue('');
    await expect(sendButton(page)).toHaveText('Enviar feedback');
    await expect(sendButton(page)).toBeEnabled();
    expect(await myTotal(api)).toBe(1);

    // The detail, from the confirmation; then again after reloading.
    const expected = { subject: COMPLETE.subject, category: 'Queja', urgency: 'Alta', status: 'Pendiente', created_at: sent.created_at };
    await confirmation(page).getByRole('link', { name: 'Ver el feedback enviado' }).click();
    await expect(page).toHaveURL(new RegExp(`/resident/feedback/${sent.id}$`));
    await expectDetail(page, expected);
    await expect(page.getByRole('region', { name: 'Mensaje' })).toContainText('Hay ruido después de las 10 p. m.');
    expect(await page.getByRole('region', { name: 'Mensaje' }).locator('p').innerText()).toBe(COMPLETE.message); // its line break kept
    await expect(page.getByRole('main')).toContainText('Tu feedback está registrado y pendiente de gestión.');
    await page.reload();
    await expectDetail(page, expected);

    // The list: the same values; back to the detail from its row.
    await page.getByRole('main').getByRole('link', { name: 'Volver a Mis feedback' }).click();
    await expect(statusLine(page)).toHaveText('Mostrando 1–1 de 1 feedback');
    const row = rows(page).first();
    expect(normalized(await row.locator('th').textContent())).toContain(bogotaDate(sent.created_at));
    await expect(cell(row, 'Asunto')).toHaveText(COMPLETE.subject);
    await expect(cell(row, 'Categoría')).toHaveText('Queja');
    await expect(cell(row, 'Urgencia')).toHaveText('Alta');
    await expect(cell(row, 'Estado')).toHaveText('Pendiente');
    await page.reload();
    await expect(cell(rows(page).first(), 'Asunto')).toHaveText(COMPLETE.subject);
    await row.getByRole('link', { name: new RegExp(`Ver detalle del feedback «${COMPLETE.subject}»`) }).click();
    await expectDetail(page, expected);
  });

  test('one POST while it is on its way: the button says "Enviando...", everything is disabled, a second submission does nothing', async ({ page, signIn }) => {
    const api = await signIn('resident');
    const posts = watchPosts(page);
    // The real request is held until the page has been checked, then sent to the backend unchanged.
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/v1/resident/feedback', async (route) => {
      if (route.request().method() === 'POST') await held;
      await route.continue();
    });

    await openForm(page);
    await fill(page, { subject: 'Doble envío E2E', message: 'Un solo registro.', category: 'Solicitud', urgency: 'Media' });
    await sendButton(page).click();
    await expect(sendButton(page)).toHaveText('Enviando...');
    await expect(sendButton(page)).toBeDisabled();
    for (const label of ['Asunto', 'Mensaje', 'Categoría', 'Urgencia']) await expect(page.getByLabel(label)).toBeDisabled();
    await expect(form(page).getByRole('button', { name: 'Cancelar' })).toBeDisabled();
    await expect(form(page)).toHaveAttribute('aria-busy', 'true');
    // Another submission while sending (Enter, a script, a second click): ignored.
    await form(page).dispatchEvent('submit');
    await sendButton(page).click({ force: true });
    expect(posts).toHaveLength(1);

    const answer = page.waitForResponse(isPost);
    release();
    expect((await answer).status()).toBe(201);
    await expect(confirmation(page)).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(await myTotal(api)).toBe(1);

    // A fast double click (no request held): still one feedback.
    await page.unroute('**/api/v1/resident/feedback');
    await fill(page, { subject: 'Doble clic E2E', message: 'Otro registro, uno solo.', category: 'Otros', urgency: 'Baja' });
    const second = page.waitForResponse(isPost);
    await sendButton(page).dblclick();
    expect((await second).status()).toBe(201);
    await expect(confirmation(page)).toBeVisible();
    expect(posts).toHaveLength(2);
    expect(await myTotal(api)).toBe(2);
  });
});

// --- 3. Form checks -------------------------------------------------------------------------------------------

test.describe('form checks', () => {
  test('without a message nothing is sent: each message next to its field, the focus on the first, nothing technical', async ({ page, signIn }) => {
    const api = await signIn('resident');
    const posts = watchPosts(page);
    await openForm(page);
    await sendButton(page).click();
    await expect(page.locator('#feedback-message-error')).toHaveText('Escribe el mensaje.');
    await expect(page.locator('#feedback-category-error')).toHaveText('Selecciona una categoría: queja, sugerencia, solicitud u otros.');
    await expect(page.locator('#feedback-urgency-error')).toHaveText('Selecciona la urgencia: alta, media o baja.');
    await expect(page.locator('#feedback-subject-error')).toBeHidden();
    const message = page.getByLabel('Mensaje');
    await expect(message).toBeFocused();
    await expect(message).toHaveAttribute('aria-invalid', 'true');
    await expect(message).toHaveAttribute('aria-describedby', /feedback-message-error/);
    await expect(page.getByLabel('Asunto')).toHaveAttribute('aria-invalid', 'false');

    // Only spaces and line breaks: still no message.
    await fill(page, { subject: 'Sin mensaje E2E', message: '   \n\t  ', category: 'Queja', urgency: 'Alta' });
    await sendButton(page).click();
    await expect(page.locator('#feedback-message-error')).toHaveText('Escribe el mensaje.');
    await expect(page.locator('#feedback-category-error')).toBeHidden();
    await expect(message).toBeFocused();
    await expect(page.getByRole('main')).not.toContainText(/Traceback|Exception|undefined|VALIDATION_ERROR|422/);
    expect(posts).toHaveLength(0);
    expect(await myTotal(api)).toBe(0);
  });

  test('the subject can be left empty: stored without one, shown as "Sin asunto"', async ({ page, signIn }) => {
    await signIn('resident');
    await openForm(page);
    await fill(page, { message: 'Propongo más bancas en el parque (E2E).', category: 'Sugerencia', urgency: 'Baja' });
    const response = await send(page);
    expect(response.request().postDataJSON()).toEqual({ subject: null, message: 'Propongo más bancas en el parque (E2E).', category: 'SUGGESTION', urgency: 'LOW' });
    expect(response.status()).toBe(201);
    const sent = (await response.json()) as Sent;
    expect(sent.subject).toBeNull();

    await openList(page);
    await expect(cell(rows(page).first(), 'Asunto')).toHaveText('Sin asunto');
    await expect(cell(rows(page).first(), 'Categoría')).toHaveText('Sugerencia');
    await page.goto(`/resident/feedback/${sent.id}`);
    await expectDetail(page, { subject: 'Sin asunto', category: 'Sugerencia', urgency: 'Baja', status: 'Pendiente', created_at: sent.created_at });
  });

  test('limits: 150 and 2000 characters at the boundary are accepted; longer text is not, in the form or in the API', async ({ page, signIn }) => {
    const api = await signIn('resident');
    const posts = watchPosts(page);
    await openForm(page);
    // The fields do not take more than the limits.
    await fill(page, { subject: 'a'.repeat(151), message: 'b'.repeat(2001), category: 'Otros', urgency: 'Media' });
    await expect(page.getByLabel('Asunto')).toHaveValue('a'.repeat(150));
    await expect(page.getByLabel('Mensaje')).toHaveValue('b'.repeat(2000));
    await expect(page.getByText('2000 de 2000 caracteres')).toBeVisible();
    const response = await send(page);
    expect(response.status()).toBe(201);
    const sent = (await response.json()) as Sent;
    expect([sent.subject?.length, sent.message.length]).toEqual([150, 2000]);

    // A longer text that reaches the form anyway (no maxlength): the form says why and sends nothing.
    await page.locator('#feedback-subject, #feedback-message').evaluateAll((fields) => fields.forEach((field) => field.removeAttribute('maxlength')));
    await fill(page, { subject: 'a'.repeat(151), message: 'b'.repeat(2001), category: 'Otros', urgency: 'Media' });
    await sendButton(page).click();
    await expect(page.locator('#feedback-subject-error')).toHaveText('El asunto no puede superar 150 caracteres.');
    await expect(page.locator('#feedback-message-error')).toHaveText('El mensaje no puede superar 2000 caracteres.');
    await expect(page.getByLabel('Asunto')).toBeFocused();
    expect(posts).toHaveLength(1);

    // The backend has the same limits (the form is only a help).
    for (const [field, value, message] of [
      ['subject', 'a'.repeat(151), 'El asunto no puede superar 150 caracteres.'],
      ['message', 'b'.repeat(2001), 'El mensaje no puede superar 2000 caracteres.']
    ]) {
      const refused = await api.post('/resident/feedback', { subject: null, message: 'x', category: 'OTHER', urgency: 'LOW', [field]: value });
      expect(refused.status(), field).toBe(422);
      expect((await refused.json()).error.details).toContainEqual({ field, message });
    }
    expect(await myTotal(api)).toBe(1);
  });

  test('cancel: an empty form leaves at once; with content it asks, keeps the text or discards it, and nothing is sent or kept', async ({ page, signIn }) => {
    const api = await signIn('resident');
    const posts = watchPosts(page);
    await openForm(page);
    await form(page).getByRole('button', { name: 'Cancelar' }).click();
    await expect(page).toHaveURL(/\/resident\/feedback$/);

    await openForm(page);
    await fill(page, { subject: 'Borrador E2E', message: 'Texto que no se envía.' });
    await form(page).getByRole('button', { name: 'Cancelar' }).click();
    const dialog = page.getByRole('dialog', { name: '¿Descartar el feedback que estás escribiendo?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir escribiendo' }).click();
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/resident\/feedback\/new$/);
    await expect(page.getByLabel('Mensaje')).toHaveValue('Texto que no se envía.');

    await form(page).getByRole('button', { name: 'Cancelar' }).click();
    await dialog.getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/resident\/feedback$/);
    await expect(page.getByText('No tienes feedback registrados.')).toBeVisible();
    expect(posts).toHaveLength(0);
    expect(await myTotal(api)).toBe(0);
    // The draft is not kept anywhere in the browser.
    await openForm(page);
    await expect(page.getByLabel('Asunto')).toHaveValue('');
    await expect(page.getByLabel('Mensaje')).toHaveValue('');
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))).not.toContain('Borrador');
  });

  test('a session that ended while writing: the draft stays, the message is plain and the login is offered', async ({ page, signIn }) => {
    await signIn('resident');
    await openForm(page);
    await fill(page, { subject: 'Sesión E2E', message: 'Escrito antes de que terminara la sesión.', category: 'Solicitud', urgency: 'Alta' });
    await page.context().clearCookies(); // the session ends (as on logout in another tab)
    const response = await send(page);
    expect(response.status()).toBe(401);
    const alert = page.locator('[data-form-alert]');
    await expect(alert).toBeVisible();
    await expect(alert).toHaveAttribute('role', 'alert');
    await expect(alert).toContainText('Tu sesión ha expirado. Inicia sesión nuevamente.');
    await expect(alert.getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute('href', '/login');
    await expect(alert).not.toContainText(/UNAUTHENTICATED|401|Traceback/);
    await expect(page.getByLabel('Mensaje')).toHaveValue('Escrito antes de que terminara la sesión.');
    await expect(sendButton(page)).toBeEnabled();
    await expect(confirmation(page)).toBeHidden();
  });
});

// --- 4. List and detail -----------------------------------------------------------------------------------------

test.describe('list and detail', () => {
  test('only the resident\'s feedback, newest first, in pages of 10', async ({ page, signIn }) => {
    const api = await signIn('resident');
    for (let number = 1; number <= 11; number += 1) {
      await sendThroughApi(api, { subject: `Feedback E2E ${String(number).padStart(2, '0')}` });
    }
    await openList(page);
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 11 feedback');
    await expect(rows(page)).toHaveCount(10);
    await expect(cell(rows(page).first(), 'Asunto')).toHaveText('Feedback E2E 11');
    await expect(cell(rows(page).last(), 'Asunto')).toHaveText('Feedback E2E 02');
    await expect(page.getByRole('main')).not.toContainText(PEDRO.subject);
    await expect(page.getByRole('main')).not.toContainText('Antes de HU-009 E2E');

    await page.getByRole('button', { name: 'Siguiente' }).click();
    await expect(page.locator('[data-page-info]')).toHaveText('Página 2 de 2');
    await expect(rows(page)).toHaveCount(1);
    await expect(cell(rows(page).first(), 'Asunto')).toHaveText('Feedback E2E 01');
    await expect(page).toHaveURL(/\?page=2$/);
    await page.getByRole('button', { name: 'Anterior' }).click();
    await expect(cell(rows(page).first(), 'Asunto')).toHaveText('Feedback E2E 11');

    // Unknown options in the URL never select anything else (the page ignores them; the API refuses them).
    await openList(page, '?status=RESOLVED&resident_id=1&search=Agua');
    await expect(statusLine(page)).toHaveText('Mostrando 1–10 de 11 feedback');
    expect((await api.get('/resident/feedback?resident_id=1')).status()).toBe(422);
  });

  test('resolved by the administration: the resident sees "Resuelto", the response and the resolution date, never the internal note', async ({ page, signIn }) => {
    const resident = await signIn('resident');
    const sent = await sendThroughApi(resident, { subject: 'Portería E2E', category: 'REQUEST', urgency: 'MEDIUM' });
    const admin = await signIn('admin');
    const response = 'RESPUESTA-ADMIN-E2E: hablamos con portería.\nEl vigilante revisará la puerta.';
    await answerAndResolve(admin, sent.id, response, 'NOTA-INTERNA-E2E: llamada al vigilante.');
    const api = await signIn('resident');

    // The API answer: the resident's contract, with the response and the resolution date (HU-005 3.2, HU-017 1).
    const detail = await api.json<Sent>(await api.get(`/resident/feedback/${sent.id}`));
    expect(Object.keys(detail).sort()).toEqual(RESPONSE_KEYS);
    expect(detail.admin_response).toBe(response);
    expect(detail.resolved_at).toMatch(/(Z|\+00:00)$/);
    expect(JSON.stringify(detail)).not.toMatch(/NOTA-INTERNA-E2E|resolution_note|resident_id|version|ip_address/);

    await openList(page);
    await expect(cell(rows(page).first(), 'Estado')).toHaveText('Resuelto');
    await expect(page.getByRole('main')).not.toContainText('RESPUESTA-ADMIN-E2E'); // the list shows no response
    await page.goto(`/resident/feedback/${sent.id}`);
    await expectDetail(page, { subject: 'Portería E2E', category: 'Solicitud', urgency: 'Media', status: 'Resuelto',
                               created_at: sent.created_at, resolved_at: detail.resolved_at! });
    await expect(page.getByRole('main')).toContainText('Tu feedback ha sido marcado como resuelto.');
    const answer = page.getByRole('region', { name: 'Respuesta de la administración' });
    await expect(answer).toBeVisible();
    expect(await answer.locator('[data-response-text]').innerText()).toBe(response); // its line break kept
    const html = await page.content();
    for (const secret of ['NOTA-INTERNA-E2E', 'Nota interna', 'resolution_note', 'Administradora Pruebas']) {
      expect(html, secret).not.toContain(secret);
    }
  });

  test('another resident\'s feedback, a missing one and an invalid id: the same "Feedback no encontrado.", by URL and by API', async ({ page, seed, signIn }) => {
    const api = await signIn('resident');
    const own = await sendThroughApi(api);
    const texts: string[] = [];
    for (const id of [String(seed.feedback.pedro), String(seed.feedback.historical), '999999999', 'abc', '0', '-1']) {
      const response = await page.goto(`/resident/feedback/${id}`);
      expect(response?.status(), id).toBe(404);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(NOT_FOUND);
      await expect(page.getByRole('main').getByRole('link', { name: 'Volver a Mis feedback' })).toBeVisible();
      texts.push(await page.locator('main').innerText());
      const html = await page.content();
      for (const secret of [PEDRO.subject, PEDRO.message, PEDRO.name, 'Antes de HU-009 E2E', 'pedro.e2e@example.com']) {
        expect(html, `${id}: ${secret}`).not.toContain(secret);
      }
    }
    expect(new Set(texts).size).toBe(1);

    // The API: Pedro's feedback is answered exactly like a missing one.
    const foreign = await api.get(`/resident/feedback/${seed.feedback.pedro}`);
    const missing = await api.get('/resident/feedback/999999999');
    expect([foreign.status(), missing.status()]).toEqual([404, 404]);
    expect(await foreign.text()).toBe(await missing.text());
    expect((await foreign.json()).error).toEqual({ code: 'NOT_FOUND', message: 'El feedback no existe.', details: [] });

    // The resident's own feedback still opens.
    await page.goto(`/resident/feedback/${own.id}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Detalle del feedback');
  });
});

// --- 5. Administration (HU-005) ------------------------------------------------------------------------------

async function adminRow(page: Page, subject: string): Promise<Locator> {
  await page.goto('/admin/feedback');
  await expect(page.locator('[data-status-line]')).not.toHaveText('Cargando feedback...');
  return page.getByRole('row', { name: new RegExp(subject) });
}

const classification = (page: Page, label: string) => definition(page.getByRole('region', { name: 'Mensaje del residente' }), label);

test.describe('administration', () => {
  test('the administrator finds the resident\'s feedback with its category and urgency; the one before HU-009 shows "No registrada"', async ({ page, seed, signIn }) => {
    const resident = await signIn('resident');
    const sent = await sendThroughApi(resident, { subject: 'Piscina E2E', message: 'El horario es corto.', category: 'SUGGESTION', urgency: 'LOW' });
    const admin = await signIn('admin');

    const row = await adminRow(page, 'Piscina E2E');
    await expect(page.getByRole('columnheader', { name: 'Categoría y urgencia' })).toBeVisible();
    await expect(row).toContainText('Laura Gómez');
    await expect(row).toContainText('Sugerencia');
    await expect(row).toContainText('Urgencia: Baja');
    await expect(row).toContainText('Pendiente');
    const historical = page.getByRole('row', { name: /Antes de HU-009 E2E/ });
    await expect(historical).toContainText('No registrada');
    await expect(historical).toContainText('Urgencia: No registrada');
    await expect(page.getByRole('row', { name: new RegExp(PEDRO.subject) })).toContainText('Urgencia: Alta');

    await row.getByRole('link', { name: /Ver detalle/ }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/feedback/${sent.id}$`));
    await expect(classification(page, 'Categoría')).toHaveText('Sugerencia');
    await expect(classification(page, 'Urgencia')).toHaveText('Baja');

    await page.goto(`/admin/feedback/${seed.feedback.historical}`);
    await expect(classification(page, 'Categoría')).toHaveText('No registrada');
    await expect(classification(page, 'Urgencia')).toHaveText('No registrada');
    // The API keeps them null (nothing invented).
    const detail = await admin.json<{ category: unknown; urgency: unknown }>(await admin.get(`/admin/feedback/${seed.feedback.historical}`));
    expect([detail.category, detail.urgency]).toEqual([null, null]);
  });

  test('responding and resolving still work with category and urgency in the answers; the resident sees the new status', async ({ page, signIn }) => {
    const resident = await signIn('resident');
    const sent = await sendThroughApi(resident, { subject: 'Ascensor E2E', message: 'El ascensor se detiene.', category: 'COMPLAINT', urgency: 'MEDIUM' });
    const admin = await signIn('admin');

    await page.goto(`/admin/feedback/${sent.id}`);
    await expect(classification(page, 'Categoría')).toHaveText('Queja');
    await expect(classification(page, 'Urgencia')).toHaveText('Media');
    await expect(page.locator('[data-status-badge]')).toHaveText('Pendiente');

    // Response (the answer carries category and urgency: the page keeps working).
    await page.getByRole('textbox', { name: 'Respuesta' }).fill('Revisaremos el ascensor esta semana.');
    await page.getByRole('button', { name: 'Guardar respuesta' }).click();
    const notice = page.locator('[data-notice]');
    await expect(notice).toHaveText(/Respuesta guardada y enviada al residente\.|no fue posible enviar la notificación/);
    await expect(page.locator('[data-response-text]')).toHaveText('Revisaremos el ascensor esta semana.');
    await expect(page.locator('[data-history] li').first()).toContainText('Respuesta registrada');
    await expect(classification(page, 'Categoría')).toHaveText('Queja');

    // Resolution, confirmed in the dialog.
    await page.getByRole('textbox', { name: 'Nota de resolución' }).fill('Se programó mantenimiento.');
    await page.locator('[data-resolve-form]').getByRole('button', { name: 'Marcar como resuelto' }).click();
    const dialog = page.getByRole('dialog', { name: '¿Marcar este feedback como resuelto?' });
    await dialog.getByRole('button', { name: 'Marcar como resuelto' }).click();
    await expect(page.locator('[data-status-badge]')).toHaveText('Resuelto');
    await expect(notice).toHaveText(/Feedback marcado como resuelto|no fue posible enviar la notificación/);
    await expect(page.locator('[data-resolution-text]')).toHaveText('Se programó mantenimiento.');
    await expect(page.locator('[data-resolve-form]')).toBeHidden();

    await page.reload();
    await expect(page.locator('[data-status-badge]')).toHaveText('Resuelto');
    await expect(classification(page, 'Categoría')).toHaveText('Queja');
    await expect(classification(page, 'Urgencia')).toHaveText('Media');
    const detail = await admin.json<Record<string, unknown>>(await admin.get(`/admin/feedback/${sent.id}`));
    expect(detail).toMatchObject({ category: 'COMPLAINT', urgency: 'MEDIUM', status: 'RESOLVED', version: 3 });
    const row = await adminRow(page, 'Ascensor E2E');
    await expect(row).toContainText('Resuelto');
    await expect(row).toContainText('Urgencia: Media');

    await signIn('resident');
    await openList(page);
    await expect(cell(rows(page).first(), 'Estado')).toHaveText('Resuelto');
    await expect(cell(rows(page).first(), 'Categoría')).toHaveText('Queja');
  });
});

// --- 6. Screens -----------------------------------------------------------------------------------------------

test.describe('360 × 800', () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

  test('panel, form, list and detail: one column, stacked rows with their labels, no horizontal scroll', async ({ page, signIn }) => {
    await signIn('resident');
    await page.goto('/resident');
    await expectInside(page.getByRole('main').getByRole('link', { name: 'Enviar feedback' }), 360);
    await expectNoOverflow(page, 'panel');

    await openForm(page);
    await expectNoOverflow(page, 'form');
    const boxes = await Promise.all(['Asunto', 'Mensaje', 'Categoría', 'Urgencia'].map(async (label) => (await page.getByLabel(label).boundingBox())!));
    for (const box of boxes) {
      expect(Math.abs(box.x - boxes[0].x), 'one column').toBeLessThanOrEqual(1);
      expect(box.width).toBeGreaterThan(360 * 0.7);
    }
    await sendButton(page).click();
    for (const id of ['#feedback-message-error', '#feedback-category-error', '#feedback-urgency-error']) {
      const error = page.locator(id);
      await expectInside(error, 360);
      expect(await error.evaluate((element) => element.scrollWidth <= element.clientWidth), id).toBe(true);
    }
    await expectNoOverflow(page, 'form errors');
    for (const name of ['Enviar feedback', 'Cancelar']) await expectInside(form(page).getByRole('button', { name }), 360);

    await fill(page, { subject: 'Un asunto bastante largo para una pantalla pequeña E2E', message: COMPLETE.message, category: 'Solicitud', urgency: 'Alta' });
    const sent = (await (await send(page)).json()) as Sent;
    await expectInside(confirmation(page), 360);
    await expectNoOverflow(page, 'confirmation');

    await openList(page);
    await expect(page.getByRole('columnheader', { name: 'Categoría' })).toBeHidden();
    const row = rows(page).first();
    expect(await row.evaluate((element) => getComputedStyle(element).display)).toBe('block');
    expect(await cellLabels(row)).toEqual(['Asunto', 'Categoría', 'Urgencia', 'Estado']);
    for (const element of await row.locator('th, td').all()) await expectInside(element, 360);
    await expectInside(row.getByRole('link', { name: /Ver detalle/ }), 360);
    await expectNoOverflow(page, 'list');

    await row.getByRole('link', { name: /Ver detalle/ }).click();
    await expect(page).toHaveURL(new RegExp(`/resident/feedback/${sent.id}$`));
    const values = page.getByRole('region', { name: 'Información del feedback' }).locator('dd');
    await expect(values).toHaveCount(5);
    const xs = new Set<number>();
    for (const value of await values.all()) {
      await expectInside(value, 360);
      xs.add(Math.round((await value.boundingBox())!.x));
    }
    expect(xs.size, 'one column').toBe(1);
    await expectInside(page.getByRole('main').getByRole('link', { name: 'Volver a Mis feedback' }), 360);
    await expectNoOverflow(page, 'detail');

    await page.goto('/resident/feedback/999999999');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(NOT_FOUND);
    await expectNoOverflow(page, 'not found');
  });

  test('administration: the list scrolls inside its card, the detail shows category and urgency in the viewport', async ({ admin: { page }, seed }) => {
    await expect(await adminRow(page, PEDRO.subject)).toContainText('Urgencia: Alta');
    await expectNoOverflow(page, 'admin list');
    await page.goto(`/admin/feedback/${seed.feedback.historical}`);
    await expectInside(classification(page, 'Categoría'), 360);
    await expectInside(classification(page, 'Urgencia'), 360);
    await expectInside(page.getByRole('button', { name: 'Guardar respuesta' }), 360);
    await expectNoOverflow(page, 'admin detail');
  });
});

test.describe('1280 × 800', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('the resident\'s list as a table; the administrator\'s list fits its card ("Ver detalle" visible)', async ({ page, seed, signIn }) => {
    const resident = await signIn('resident');
    await sendThroughApi(resident);
    await openList(page);
    await expect(page.getByRole('columnheader', { name: 'Urgencia' })).toBeVisible();
    expect(await rows(page).first().evaluate((row) => getComputedStyle(row).display)).toBe('table-row');
    await expectNoOverflow(page, 'list');

    await signIn('admin');
    const row = await adminRow(page, COMPLETE.subject);
    const link = row.getByRole('link', { name: /Ver detalle/ });
    await expect(link).toBeVisible();
    const card = (await page.getByRole('region', { name: 'Listado de feedback' }).boundingBox())!;
    const box = (await link.boundingBox())!;
    expect(box.x + box.width, '"Ver detalle" inside the card').toBeLessThanOrEqual(card.x + card.width);
    await expectNoOverflow(page, 'admin list');
    await page.goto(`/admin/feedback/${seed.feedback.pedro}`);
    await expect(classification(page, 'Urgencia')).toHaveText('Alta');
    await expectNoOverflow(page, 'admin detail');
  });
});
