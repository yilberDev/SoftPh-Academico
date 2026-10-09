/**
 * HU-009: feedback of the resident — form (checks, exact body, one POST,
 * errors, cancel), "Mis feedback" (rows, pages, states) and detail. The API is
 * a fake injected into each controller (no server). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError, type ApiErrorBody } from '../src/lib/api/errors.ts';
import type { NewFeedbackPayload, ResidentFeedback, ResidentFeedbackDetail, ResidentFeedbackPage } from '../src/lib/api/resident-feedback.ts';
import {
  CATEGORIES,
  CATEGORY_LABELS,
  NOT_RECORDED,
  STATUSES,
  STATUS_BADGES,
  STATUS_HELP,
  STATUS_LABELS,
  URGENCIES,
  URGENCY_LABELS,
  formatDateTime
} from '../src/lib/feedback/feedback-format.ts';
import {
  CATEGORY_MESSAGE,
  DISCARD_QUESTION,
  EMPTY_VALUES,
  FORBIDDEN_MESSAGE as SEND_FORBIDDEN,
  INVALID_MESSAGE,
  MESSAGE_MAX_LENGTH,
  MESSAGE_REQUIRED,
  MESSAGE_TOO_LONG,
  SENDING_LABEL,
  SEND_ERROR,
  SESSION_EXPIRED_MESSAGE as SEND_SESSION_EXPIRED,
  SUBJECT_MAX_LENGTH,
  SUBJECT_TOO_LONG,
  SUBMIT_LABEL,
  SUCCESS_DETAIL,
  SUCCESS_TITLE,
  URGENCY_MESSAGE,
  cancelAction,
  counterText,
  createFeedbackFlow,
  detailPath,
  hasContent,
  stateAfterError,
  textLength,
  validateFeedbackForm,
  type FeedbackFormValues,
  type FeedbackSubmitState
} from '../src/lib/resident/feedback-form.ts';
import {
  CONNECTION_ERROR,
  DEFAULT_QUERY,
  EMPTY_MESSAGE,
  FORBIDDEN_MESSAGE,
  SESSION_EXPIRED_MESSAGE,
  COLUMNS,
  createResidentFeedbackList,
  feedbackRow,
  pageText,
  parseQuery,
  rangeText,
  toQueryString,
  type ResidentFeedbackState
} from '../src/lib/resident/feedback-list.ts';
import {
  FORBIDDEN_MESSAGE as DETAIL_FORBIDDEN,
  NOT_FOUND_HINT,
  NOT_FOUND_MESSAGE,
  detailLines,
  isFeedbackId,
  loadDetailPage
} from '../src/lib/resident/feedback-detail.ts';
import { CSRF_MESSAGE } from '../src/lib/obligations/charge-format.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');

function apiError(status: number, code: string, message = 'x', details: ApiErrorBody['error']['details'] = []): ApiError {
  return new ApiError(status, { error: { code, message, details } });
}

function feedback(id: number, overrides: Partial<ResidentFeedback> = {}): ResidentFeedback {
  return { id, subject: 'Ruido en la torre 2', category: 'COMPLAINT', urgency: 'HIGH', status: 'PENDING',
           created_at: '2026-10-03T15:00:00Z', ...overrides };
}

function detail(overrides: Partial<ResidentFeedbackDetail> = {}): ResidentFeedbackDetail {
  return { ...feedback(5), message: 'Hay ruido después de las 10 p. m.\nTodos los días.', ...overrides };
}

function pageOf(items: ResidentFeedback[], page = 1, pageSize = 10, total = items.length): ResidentFeedbackPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

const FILLED: FeedbackFormValues = { subject: 'Ruido', message: 'Hay ruido.', category: 'COMPLAINT', urgency: 'HIGH' };

// --- Labels -----------------------------------------------------------------------------------------------

describe('resident feedback: labels', () => {
  it('categories, urgencies and statuses: the API codes, shown in Spanish', () => {
    assert.deepEqual(CATEGORIES, ['COMPLAINT', 'SUGGESTION', 'REQUEST', 'OTHER']);
    assert.deepEqual(CATEGORIES.map((c) => CATEGORY_LABELS[c]), ['Queja', 'Sugerencia', 'Solicitud', 'Otros']);
    assert.deepEqual(URGENCIES, ['HIGH', 'MEDIUM', 'LOW']);
    assert.deepEqual(URGENCIES.map((u) => URGENCY_LABELS[u]), ['Alta', 'Media', 'Baja']);
    assert.deepEqual(STATUSES, ['PENDING', 'RESOLVED']); // no other status
    assert.deepEqual(STATUS_LABELS, { PENDING: 'Pendiente', RESOLVED: 'Resuelto' });
  });

  it('the status is explained in words (never color alone) and promises nothing', () => {
    assert.equal(STATUS_HELP.PENDING, 'Tu feedback está registrado y pendiente de gestión.');
    assert.equal(STATUS_HELP.RESOLVED, 'Tu feedback ha sido marcado como resuelto.');
    for (const status of STATUSES) {
      assert.ok(STATUS_BADGES[status]);
      assert.doesNotMatch(STATUS_HELP[status], /respuesta|días|horas/i);
    }
  });

  it('created_at is UTC and is shown in Bogotá time, without shifting the day wrongly', () => {
    // 03:30 UTC on Oct 8 is Oct 7, 10:30 p. m. in Bogotá (UTC-5).
    assert.match(spaces(formatDateTime('2026-10-08T03:30:00Z')), /^7 de octubre de 2026.* 10:30 p\. m\.$/);
    assert.match(spaces(formatDateTime('2026-10-08T15:00:00Z')), /^8 de octubre de 2026.* 10:00 a\. m\.$/);
  });
});

// --- Form -----------------------------------------------------------------------------------------------------

describe('resident feedback: form checks', () => {
  it('the limits of the backend: subject 150, message 2000 (counted as the backend does)', () => {
    assert.equal(SUBJECT_MAX_LENGTH, 150);
    assert.equal(MESSAGE_MAX_LENGTH, 2000);
    assert.ok(validateFeedbackForm({ ...FILLED, subject: 'a'.repeat(150), message: 'b'.repeat(2000) }).payload);
    assert.deepEqual(validateFeedbackForm({ ...FILLED, subject: 'a'.repeat(151) }).errors, { subject: SUBJECT_TOO_LONG });
    assert.deepEqual(validateFeedbackForm({ ...FILLED, message: 'b'.repeat(2001) }).errors, { message: MESSAGE_TOO_LONG });
    assert.equal(SUBJECT_TOO_LONG, 'El asunto no puede superar 150 caracteres.');
    assert.equal(MESSAGE_TOO_LONG, 'El mensaje no puede superar 2000 caracteres.');
    // Python counts code points: an emoji is one character, as in the backend.
    assert.equal(textLength('😀'.repeat(2000)), 2000);
    assert.ok(validateFeedbackForm({ ...FILLED, message: '😀'.repeat(2000) }).payload);
    // Surrounding spaces do not count (the backend trims them).
    assert.ok(validateFeedbackForm({ ...FILLED, message: `  ${'b'.repeat(2000)}  ` }).payload);
    assert.equal(counterText(''), '0 de 2000 caracteres');
    assert.equal(counterText('Hola'), '4 de 2000 caracteres');
  });

  it('the message is required and cannot be blank', () => {
    for (const message of ['', '   ', '\n\t  \n']) {
      assert.deepEqual(validateFeedbackForm({ ...FILLED, message }), { errors: { message: MESSAGE_REQUIRED }, payload: null });
    }
    assert.equal(MESSAGE_REQUIRED, 'Escribe el mensaje.');
  });

  it('the subject is optional: empty is null; one line, spaces collapsed', () => {
    for (const subject of ['', '   ', '\n']) {
      assert.equal(validateFeedbackForm({ ...FILLED, subject }).payload?.subject, null);
    }
    assert.equal(validateFeedbackForm({ ...FILLED, subject: '  Ruido \n en   la torre ' }).payload?.subject, 'Ruido en la torre');
    // The message keeps its line breaks, only trimmed.
    assert.equal(validateFeedbackForm({ ...FILLED, message: '  Línea 1\nLínea 2  ' }).payload?.message, 'Línea 1\nLínea 2');
  });

  it('category and urgency are required and only the API codes are accepted', () => {
    assert.deepEqual(validateFeedbackForm(EMPTY_VALUES).errors, {
      message: MESSAGE_REQUIRED, category: CATEGORY_MESSAGE, urgency: URGENCY_MESSAGE
    });
    for (const category of ['Queja', 'complaint', 'X']) {
      assert.deepEqual(validateFeedbackForm({ ...FILLED, category }).errors, { category: CATEGORY_MESSAGE });
    }
    for (const urgency of ['Alta', 'high', 'URGENT']) {
      assert.deepEqual(validateFeedbackForm({ ...FILLED, urgency }).errors, { urgency: URGENCY_MESSAGE });
    }
    for (const category of CATEGORIES) {
      for (const urgency of URGENCIES) {
        assert.deepEqual(validateFeedbackForm({ ...FILLED, category, urgency }).payload,
          { subject: 'Ruido', message: 'Hay ruido.', category, urgency });
      }
    }
  });

  it('the body is exactly subject, message, category and urgency: no resident, status, response or note', () => {
    const tampered = { ...FILLED, resident_id: 99, user_id: 99, role: 'ADMINISTRADOR', status: 'RESOLVED',
                       resolution_note: 'x', admin_response: 'x', resolved_at: 'x' } as FeedbackFormValues;
    const { payload } = validateFeedbackForm(tampered);
    assert.deepEqual(Object.keys(payload!).sort(), ['category', 'message', 'subject', 'urgency']);
    assert.deepEqual(payload, { subject: 'Ruido', message: 'Hay ruido.', category: 'COMPLAINT', urgency: 'HIGH' });
  });
});

function flowSetup(answer: ResidentFeedbackDetail | Error) {
  const sent: NewFeedbackPayload[] = [];
  const states: FeedbackSubmitState[] = [];
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  const flow = createFeedbackFlow({
    send: async (payload) => {
      sent.push(payload);
      await gate;
      if (answer instanceof Error) throw answer;
      return answer;
    },
    render: (state) => states.push(state)
  });
  return { flow, sent, states, release };
}

describe('resident feedback: sending', () => {
  it('sending: one POST while it is on its way (double click), "Enviando..." state, then the confirmation', async () => {
    const created = detail({ id: 41, status: 'PENDING' });
    const { flow, sent, states, release } = flowSetup(created);
    const first = flow.submit(FILLED);
    assert.equal(flow.busy, true);
    assert.deepEqual(states, [{ kind: 'submitting' }]);
    const clicks = [flow.submit(FILLED), flow.submit({ ...FILLED, message: 'Otro' })]; // more clicks: ignored
    release();
    await Promise.all([first, ...clicks]);
    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0], { subject: 'Ruido', message: 'Hay ruido.', category: 'COMPLAINT', urgency: 'HIGH' });
    assert.deepEqual(states.map((s) => s.kind), ['submitting', 'done']);
    assert.deepEqual(states.at(-1), { kind: 'done', feedback: created });
    assert.equal(flow.busy, false);
    assert.equal(SENDING_LABEL, 'Enviando...');
    assert.equal(SUBMIT_LABEL, 'Enviar feedback');
  });

  it('confirmation: sent and pending, never "reviewed"; links to the new feedback', () => {
    assert.equal(SUCCESS_TITLE, 'Feedback enviado correctamente.');
    assert.equal(SUCCESS_DETAIL, 'Tu feedback quedó registrado como pendiente.');
    assert.doesNotMatch(`${SUCCESS_TITLE} ${SUCCESS_DETAIL}`, /revis|respond|días|horas/i);
    assert.equal(detailPath(41), '/resident/feedback/41');
  });

  it('an invalid form is not sent', async () => {
    const { flow, sent, states, release } = flowSetup(detail());
    release();
    await flow.submit({ ...FILLED, message: '  ' });
    assert.equal(sent.length, 0);
    assert.deepEqual(states, [{ kind: 'invalid', errors: { message: MESSAGE_REQUIRED } }]);
  });

  it('errors: 401, 403, CSRF, 422 next to each field, 500 and network; the form is usable again', async () => {
    assert.deepEqual(stateAfterError(apiError(401, 'UNAUTHENTICATED')),
      { kind: 'error', message: SEND_SESSION_EXPIRED, errors: {}, sessionExpired: true });
    assert.equal(SEND_SESSION_EXPIRED, 'Tu sesión ha expirado. Inicia sesión nuevamente.');
    assert.equal(stateAfterError(apiError(403, 'FORBIDDEN')).message, 'No tienes permiso para enviar feedback.');
    assert.equal(SEND_FORBIDDEN, 'No tienes permiso para enviar feedback.');
    assert.equal(stateAfterError(apiError(403, 'CSRF_FAILED')).message, CSRF_MESSAGE);
    const invalid = apiError(422, 'VALIDATION_ERROR', 'Datos inválidos', [
      { field: 'message', message: 'Escribe el mensaje.' },
      { field: 'body.subject', message: 'El asunto no puede superar 150 caracteres.' },
      { field: 'category', message: CATEGORY_MESSAGE },
      { field: 'urgency', message: URGENCY_MESSAGE }
    ]);
    assert.deepEqual(stateAfterError(invalid), {
      kind: 'error', message: null, sessionExpired: false,
      errors: { message: 'Escribe el mensaje.', subject: 'El asunto no puede superar 150 caracteres.',
                category: CATEGORY_MESSAGE, urgency: URGENCY_MESSAGE }
    });
    assert.equal(stateAfterError(apiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'resident_id', message: 'x' }])).message, INVALID_MESSAGE);
    for (const error of [apiError(500, 'INTERNAL_ERROR', 'Traceback (most recent call last)'), new ApiError(502, null), new TypeError('Failed to fetch')]) {
      assert.deepEqual(stateAfterError(error), { kind: 'error', message: SEND_ERROR, errors: {}, sessionExpired: false });
    }
    assert.equal(SEND_ERROR, 'No fue posible enviar el feedback. Intenta nuevamente.');

    const { flow, sent, states, release } = flowSetup(apiError(500, 'INTERNAL_ERROR'));
    release();
    await flow.submit(FILLED);
    assert.equal(states.at(-1)?.kind, 'error');
    assert.equal(flow.busy, false); // not blocked: the resident can try again (never retried on its own)
    assert.equal(sent.length, 1);
    await flow.submit(FILLED);
    assert.equal(sent.length, 2);
  });

  it('cancel: an empty form leaves at once; one with content asks first', () => {
    assert.equal(cancelAction(EMPTY_VALUES), 'leave');
    assert.equal(cancelAction({ ...EMPTY_VALUES, subject: '   ', message: '\n' }), 'leave');
    for (const values of [{ subject: 'A' }, { message: 'Hola' }, { category: 'OTHER' }, { urgency: 'LOW' }]) {
      assert.equal(cancelAction({ ...EMPTY_VALUES, ...values }), 'confirm');
      assert.equal(hasContent({ ...EMPTY_VALUES, ...values }), true);
    }
    assert.equal(DISCARD_QUESTION, '¿Descartar el feedback que estás escribiendo?');
  });
});

// --- List ---------------------------------------------------------------------------------------------------

function listSetup(answer: ResidentFeedbackPage | Error | ((query: string) => ResidentFeedbackPage | Error), initial = DEFAULT_QUERY) {
  const requests: string[] = [];
  const states: ResidentFeedbackState[] = [];
  const remembered: string[] = [];
  const list = createResidentFeedbackList(initial, {
    load: async (query) => {
      requests.push(query);
      const result = typeof answer === 'function' ? answer(query) : answer;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    remember: (query) => remembered.push(query)
  });
  return { list, requests, states, remembered };
}

describe('resident feedback: list', () => {
  it('rows: date, subject, category, urgency and status as labels; "Ver detalle" names the feedback', () => {
    const row = feedbackRow(feedback(3, { status: 'RESOLVED', category: 'SUGGESTION', urgency: 'LOW', created_at: '2026-10-08T03:30:00Z' }));
    assert.deepEqual(COLUMNS, ['Fecha de envío', 'Asunto', 'Categoría', 'Urgencia', 'Estado']);
    assert.deepEqual(row.cells.map((c) => [c.label, spaces(c.text), c.muted]).slice(1), [
      ['Asunto', 'Ruido en la torre 2', false], ['Categoría', 'Sugerencia', false], ['Urgencia', 'Baja', false], ['Estado', 'Resuelto', false]
    ]);
    assert.match(spaces(row.cells[0].text), /^7 de octubre de 2026/);
    assert.equal(row.href, '/resident/feedback/3');
    assert.match(spaces(row.linkLabel), /^Ver detalle del feedback «Ruido en la torre 2» enviado el 7 de octubre de 2026/);
    assert.equal(feedbackRow(feedback(4)).cells[4].text, 'Pendiente');
  });

  it('a feedback sent before HU-009 (null): "No registrada"; without subject: "Sin asunto"', () => {
    const row = feedbackRow(feedback(9, { subject: null, category: null, urgency: null }));
    assert.equal(NOT_RECORDED, 'No registrada');
    assert.deepEqual(row.cells.slice(1, 4).map((c) => [c.text, c.muted]), [['Sin asunto', true], ['No registrada', true], ['No registrada', true]]);
  });

  it('query: page and page_size only (newest first is the API order)', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    assert.equal(toQueryString({ ...DEFAULT_QUERY, page: 3, pageSize: 25 }), 'page=3&page_size=25');
    assert.deepEqual(parseQuery(new URLSearchParams('page=3&page_size=25')), { ...DEFAULT_QUERY, page: 3, pageSize: 25 });
    // Unknown or invalid options never reach the API (no filters, no search, never a resident).
    assert.deepEqual(parseQuery(new URLSearchParams('page=0&page_size=7&status=RESOLVED&search=x&resident_id=2&sort_by=subject')), DEFAULT_QUERY);
  });

  it('loading, then data in the API order; pages and page size', async () => {
    const items = [feedback(3, { created_at: '2026-10-05T15:00:00Z' }), feedback(2, { created_at: '2026-10-04T15:00:00Z' })];
    const { list, requests, states, remembered } = listSetup((query) =>
      query.includes('page=2') ? pageOf([feedback(1)], 2, 2, 3) : pageOf(items, 1, query.includes('page_size') ? 25 : 2, 3));
    await list.start();
    assert.deepEqual(states.map((s) => s.kind), ['loading', 'results']);
    const shown = states.at(-1) as Extract<ResidentFeedbackState, { kind: 'results' }>;
    assert.deepEqual(shown.page.items.map((i) => i.id), [3, 2]); // never sorted again here
    assert.equal(rangeText(shown.page), 'Mostrando 1–2 de 3 feedback');
    assert.equal(pageText(shown.page), 'Página 1 de 2');
    await list.goToPage(2);
    assert.equal(requests.at(-1), 'page=2');
    assert.deepEqual(remembered.at(-1), 'page=2');
    await list.setPageSize(25);
    assert.equal(requests.at(-1), 'page_size=25'); // back to page 1
  });

  it('no feedback yet: the empty message (not an error)', async () => {
    const { list, states } = listSetup(pageOf([]));
    await list.start();
    assert.deepEqual(states.at(-1), { kind: 'empty', query: DEFAULT_QUERY });
    assert.equal(EMPTY_MESSAGE, 'No tienes feedback registrados.');
  });

  it('a page without rows (fewer feedback now): goes to the last page', async () => {
    const { list, requests, states } = listSetup((query) => (query === 'page=5' ? pageOf([], 5, 10, 12) : pageOf([feedback(1), feedback(2)], 2, 10, 12)));
    await list.goToPage(5);
    assert.deepEqual(requests, ['page=5', 'page=2']);
    assert.equal(states.at(-1)?.kind, 'results');
  });

  it('errors: 401 (session ended, login offered), 403, 500, network and 422 never as a technical text; retry', async () => {
    const expired = listSetup(apiError(401, 'UNAUTHENTICATED'));
    await expired.list.start();
    assert.deepEqual(expired.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: SESSION_EXPIRED_MESSAGE, sessionExpired: true });

    const forbidden = listSetup(apiError(403, 'FORBIDDEN'));
    await forbidden.list.start();
    assert.deepEqual(forbidden.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: FORBIDDEN_MESSAGE, sessionExpired: false });

    for (const error of [apiError(500, 'INTERNAL_ERROR', 'Traceback'), new TypeError('Failed to fetch'),
                         apiError(422, 'VALIDATION_ERROR', 'Input should be greater than or equal to 1')]) {
      const failed = listSetup(error);
      await failed.list.start();
      assert.deepEqual(failed.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: CONNECTION_ERROR, sessionExpired: false });
    }
    assert.equal(CONNECTION_ERROR, 'No fue posible cargar tus feedback. Intenta nuevamente.');

    let fail = true;
    const retried = listSetup(() => (fail ? apiError(500, 'INTERNAL_ERROR') : pageOf([feedback(1)])));
    await retried.list.start();
    fail = false;
    await retried.list.refresh();
    assert.equal(retried.states.at(-1)?.kind, 'results');
  });
});

// --- Detail -------------------------------------------------------------------------------------------------

describe('resident feedback: detail', () => {
  it('shows the sending date, subject, message, category, urgency and current status', async () => {
    const calls: string[] = [];
    const result = await loadDetailPage('5', async (id) => (calls.push(id), detail({ created_at: '2026-10-08T03:30:00Z' })));
    assert.deepEqual(calls, ['5']);
    assert.equal(result.kind, 'feedback');
    const shown = (result as Extract<typeof result, { kind: 'feedback' }>).feedback;
    assert.equal(shown.message, 'Hay ruido después de las 10 p. m.\nTodos los días.');
    const lines = detailLines(shown).map((l) => [l.label, spaces(l.value)]);
    assert.deepEqual(lines.map(([label]) => label), ['Fecha de envío', 'Asunto', 'Categoría', 'Urgencia', 'Estado']);
    assert.match(lines[0][1], /^7 de octubre de 2026.* 10:30 p\. m\.$/);
    assert.deepEqual(lines.slice(1), [['Asunto', 'Ruido en la torre 2'], ['Categoría', 'Queja'], ['Urgencia', 'Alta'], ['Estado', 'Pendiente']]);
    assert.equal(detailLines(detail({ status: 'RESOLVED' }))[4].value, 'Resuelto');
  });

  it('a feedback sent before HU-009: "No registrada", nothing invented', () => {
    const lines = detailLines(detail({ subject: null, category: null, urgency: null }));
    assert.deepEqual(lines.slice(1, 4).map((l) => [l.value, l.muted]), [['Sin asunto', true], ['No registrada', true], ['No registrada', true]]);
  });

  it('404 and 422: "Feedback no encontrado."; an invalid id never calls the API', async () => {
    assert.equal(NOT_FOUND_MESSAGE, 'Feedback no encontrado.');
    assert.ok(NOT_FOUND_HINT);
    for (const error of [apiError(404, 'NOT_FOUND', 'El feedback no existe.'), apiError(422, 'VALIDATION_ERROR')]) {
      assert.deepEqual(await loadDetailPage('5', async () => Promise.reject(error)), { kind: 'not-found' });
    }
    for (const id of [undefined, '', '0', '-1', 'abc', '1.5', '01', '5 ', '1'.repeat(20), '../payments/1']) {
      let called = false;
      assert.deepEqual(await loadDetailPage(id, async () => ((called = true), detail())), { kind: 'not-found' });
      assert.equal(called, false, String(id));
    }
    assert.equal(isFeedbackId('123'), true);
  });

  it('401 → login, 403, 500 and network', async () => {
    const fail = (error: unknown) => loadDetailPage('5', async () => Promise.reject(error));
    assert.deepEqual(await fail(apiError(401, 'UNAUTHENTICATED')), { kind: 'unauthenticated' });
    assert.deepEqual(await fail(apiError(403, 'FORBIDDEN')), { kind: 'forbidden' });
    assert.equal(DETAIL_FORBIDDEN, 'No tienes permiso para acceder a esta sección.');
    assert.deepEqual(await fail(apiError(500, 'INTERNAL_ERROR')), { kind: 'error' });
    assert.deepEqual(await fail(new TypeError('Failed to fetch')), { kind: 'error' });
  });

  it('never shows internal data, even if an answer carried it', () => {
    const leaked = { ...detail(), admin_response: 'Respuesta X', resolution_note: 'Nota interna X', resolved_at: '2026-10-09T15:00:00Z',
                     resident_id: 7, version: 3, ip_address: '10.0.0.1', action_history: [] } as ResidentFeedbackDetail;
    const shown = JSON.stringify(detailLines(leaked));
    for (const secret of ['Respuesta X', 'Nota interna X', '2026-10-09', '9 de octubre', '10.0.0.1', 'resident', 'version']) {
      assert.ok(!shown.includes(secret), secret);
    }
    const row = JSON.stringify(feedbackRow(leaked));
    for (const secret of ['Respuesta X', 'Nota interna X', 'Hay ruido']) assert.ok(!row.includes(secret), secret); // no message in the list
  });
});
