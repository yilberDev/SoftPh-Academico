/**
 * HU-005: feedback list and detail (response, resolution, history), with the
 * category and urgency of HU-009. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { FeedbackActionResult, FeedbackDetail, FeedbackListItem, FeedbackPage } from '../src/lib/api/feedback.ts';
import { decidePageAccess } from '../src/lib/auth/page-access.ts';
import {
  ALREADY_RESOLVED_MESSAGE,
  CONNECTION_ERROR,
  FORBIDDEN_MESSAGE,
  NOTE_REQUIRED,
  NOT_FOUND_MESSAGE,
  NOTIFICATION_FAILED,
  RESOLVED_DONE,
  RESPONSE_REQUIRED,
  RESPONSE_SAVED,
  VERSION_CONFLICT_MESSAGE,
  actionLabel,
  createFeedbackDetail,
  formatDateTime,
  validateText,
  type DetailState
} from '../src/lib/feedback/feedback-detail.ts';
import {
  DEFAULT_QUERY,
  EMPTY_MESSAGE,
  LOADING_MESSAGE,
  NO_RESULTS_MESSAGE,
  STATUS_LABELS,
  createFeedbackList,
  parseQuery,
  rangeText,
  toQueryString,
  type FeedbackListState,
  type FeedbackQuery
} from '../src/lib/feedback/feedback-list.ts';
import { NOT_RECORDED, classificationOf } from '../src/lib/feedback/feedback-format.ts';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const apiError = (status: number, code = 'X', message = 'x', details: { field?: string; message: string }[] = []) =>
  new ApiError(status, { error: { code, message, details } });

function item(id: number): FeedbackListItem {
  return { id, resident: { id: 7, full_name: 'Laura Gómez' }, subject: id % 2 ? 'Ruido' : null, category: 'COMPLAINT',
           urgency: 'HIGH', status: 'PENDING', created_at: '2026-10-02T20:05:00Z', resolved_at: null };
}

function pageOf(items: FeedbackListItem[], page = 1, pageSize = 10, total = items.length): FeedbackPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

function detail(overrides: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return {
    id: 5,
    resident: { id: 7, full_name: 'Laura Gómez', email: 'laura@example.com', unit_label: 'B1-TO2-APTO-104' },
    subject: 'Ruido', message: 'Hay ruido.', category: 'REQUEST', urgency: 'LOW', status: 'PENDING', admin_response: null,
    resolution_note: null,
    created_at: '2026-10-01T15:00:00Z', updated_at: '2026-10-01T15:00:00Z', resolved_at: null, version: 1,
    action_history: [],
    ...overrides
  };
}

// --- List ----------------------------------------------------------------------------------------------

function listSetup(answer: FeedbackPage | Error | ((query: string) => FeedbackPage | Error), initial: FeedbackQuery = DEFAULT_QUERY) {
  let pending: (() => void) | null = null;
  const requests: string[] = [];
  const states: FeedbackListState[] = [];
  const navigations: string[] = [];
  const list = createFeedbackList(initial, {
    load: async (query) => {
      requests.push(query);
      const result = typeof answer === 'function' ? answer(query) : answer;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    remember: () => {},
    navigate: (path) => navigations.push(path),
    schedule: (callback) => {
      pending = callback;
      return () => (pending = null);
    }
  });
  const fire = async () => {
    const callback = pending;
    pending = null;
    callback?.();
    await tick();
  };
  return { list, requests, states, navigations, fire };
}

describe('feedback list', () => {
  it('labels and messages', () => {
    assert.deepEqual(STATUS_LABELS, { PENDING: 'Pendiente', RESOLVED: 'Resuelto' });
    assert.equal(LOADING_MESSAGE, 'Cargando feedback...');
    assert.equal(EMPTY_MESSAGE, 'No hay feedback registrados.');
    assert.equal(NO_RESULTS_MESSAGE, 'No se encontraron registros con los filtros seleccionados.');
    assert.equal(rangeText(pageOf([item(1)])), 'Mostrando 1–1 de 1 registro');
  });

  it('query string: whitelisted options, newest first by default', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    const query: FeedbackQuery = { page: 2, pageSize: 25, search: 'ruido', status: 'RESOLVED', sortBy: 'resident', sortOrder: 'asc' };
    assert.deepEqual(parseQuery(new URLSearchParams(toQueryString(query))), query);
    assert.deepEqual(parseQuery(new URLSearchParams('status=OPEN&sort_by=message&sort_order=up&page=0&page_size=7')), DEFAULT_QUERY);
  });

  it('loading, results, empty and no results', async () => {
    const results = listSetup(pageOf([item(1), item(2)]));
    await results.list.start();
    assert.deepEqual(results.states.map((s) => s.kind), ['loading', 'results']);
    const empty = listSetup(pageOf([]));
    await empty.list.start();
    assert.equal(empty.states.at(-1)?.kind, 'empty');
    const none = listSetup(pageOf([]), { ...DEFAULT_QUERY, status: 'RESOLVED' });
    await none.list.start();
    assert.equal(none.states.at(-1)?.kind, 'no-results');
  });

  it('search (debounced) and status filter from page 1', async () => {
    const { list, requests, fire } = listSetup(pageOf([item(1)]), { ...DEFAULT_QUERY, page: 3 });
    list.search('lau');
    list.search('laura');
    await fire();
    await list.filter('PENDING');
    assert.deepEqual(requests, ['search=laura', 'search=laura&status=PENDING']);
  });

  it('sort: date newest first, the others alphabetically; pagination', async () => {
    const { list, requests } = listSetup(pageOf([item(1)], 1, 10, 30));
    await list.sort('created_at');
    await list.sort('subject');
    await list.goToPage(2);
    await list.setPageSize(25);
    assert.deepEqual(requests, ['sort_order=asc', 'sort_by=subject&sort_order=asc', 'page=2&sort_by=subject&sort_order=asc',
      'page_size=25&sort_by=subject&sort_order=asc']);
  });

  it('401 → login; 403, 500 → message; retry', async () => {
    const expired = listSetup(apiError(401));
    await expired.list.start();
    assert.deepEqual(expired.navigations, ['/login']);
    const forbidden = listSetup(apiError(403, 'FORBIDDEN'));
    await forbidden.list.start();
    assert.deepEqual(forbidden.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: 'No tienes permiso para ver el feedback.' });
    let calls = 0;
    const flaky = listSetup(() => (++calls === 1 ? apiError(500) : pageOf([item(1)])));
    await flaky.list.start();
    assert.equal(flaky.states.at(-1)?.kind, 'error');
    await flaky.list.refresh();
    assert.equal(flaky.states.at(-1)?.kind, 'results');
  });
});

// --- Detail ---------------------------------------------------------------------------------------------------

function detailSetup(options: {
  respond?: FeedbackActionResult | Error;
  resolve?: FeedbackActionResult | Error;
  reload?: FeedbackDetail | Error;
  initial?: FeedbackDetail;
} = {}) {
  const calls: { kind: string; args: unknown[] }[] = [];
  const states: DetailState[] = [];
  const navigations: string[] = [];
  const answer = async <T,>(value: T | Error | undefined): Promise<T> => {
    await tick();
    if (value instanceof Error) throw value;
    if (value === undefined) throw new Error('unexpected call');
    return value;
  };
  const controller = createFeedbackDetail(options.initial ?? detail(), {
    respond: (...args) => (calls.push({ kind: 'respond', args }), answer(options.respond)),
    resolve: (...args) => (calls.push({ kind: 'resolve', args }), answer(options.resolve)),
    reload: (...args) => (calls.push({ kind: 'reload', args }), answer(options.reload)),
    render: (state) => states.push(state),
    navigate: (path) => navigations.push(path)
  });
  return { controller, calls, states, navigations };
}

const RESPONDED = detail({ admin_response: 'Revisaremos.', version: 2, action_history: [
  { action: 'FEEDBACK_RESPONDED', actor: { id: 1, full_name: 'Admin Uno' }, at: '2026-10-02T20:05:00Z' },
  { action: 'FEEDBACK_NOTIFICATION_SENT', actor: { id: 1, full_name: 'Admin Uno' }, at: '2026-10-02T20:05:01Z' }] });
const RESOLVED = detail({ status: 'RESOLVED', resolution_note: 'Nota interna', resolved_at: '2026-10-02T21:00:00Z', version: 2 });

describe('feedback detail: response', () => {
  it('validation (format only)', async () => {
    assert.deepEqual(validateText('  hola  ', RESPONSE_REQUIRED), { value: 'hola' });
    assert.deepEqual(validateText('   ', RESPONSE_REQUIRED), { error: RESPONSE_REQUIRED });
    assert.ok('error' in validateText('x'.repeat(2001), RESPONSE_REQUIRED));
    const { controller, calls, states } = detailSetup();
    await controller.respond('  ');
    assert.equal(calls.length, 0);
    assert.deepEqual(states.at(-1)?.errors, { response: RESPONSE_REQUIRED });
  });

  it('saves with the version seen and updates the detail and its history', async () => {
    const { controller, calls, states } = detailSetup({ respond: { feedback: RESPONDED, notification_status: 'SENT' } });
    await controller.respond('  Revisaremos.  ');
    assert.deepEqual(calls, [{ kind: 'respond', args: [5, 'Revisaremos.', 1] }]);
    assert.deepEqual(states.map((s) => s.busy), ['response', null]);
    const last = states.at(-1)!;
    assert.equal(last.feedback.admin_response, 'Revisaremos.');
    assert.equal(last.feedback.version, 2);
    assert.equal(last.feedback.action_history.length, 2);
    assert.deepEqual(last.notice, { tone: 'success', text: RESPONSE_SAVED });
  });

  it('a failed notification is a warning, the response stays saved', async () => {
    const { controller, states } = detailSetup({ respond: { feedback: RESPONDED, notification_status: 'FAILED' } });
    await controller.respond('Revisaremos.');
    assert.equal(states.at(-1)?.feedback.admin_response, 'Revisaremos.');
    assert.deepEqual(states.at(-1)?.notice, { tone: 'warning', text: NOTIFICATION_FAILED });
  });

  it('no double submission', async () => {
    const { controller, calls } = detailSetup({ respond: { feedback: RESPONDED, notification_status: 'SENT' } });
    await Promise.all([controller.respond('a'), controller.respond('b')]);
    assert.equal(calls.length, 1);
  });

  it('409 VERSION_CONFLICT: message and the detail is reloaded', async () => {
    const fresh = detail({ admin_response: 'Otra persona respondió', version: 3 });
    const { controller, calls, states } = detailSetup({ respond: apiError(409, 'VERSION_CONFLICT'), reload: fresh });
    await controller.respond('Mi respuesta');
    assert.deepEqual(calls.map((c) => c.kind), ['respond', 'reload']);
    assert.equal(states.at(-1)?.feedback.version, 3);
    assert.deepEqual(states.at(-1)?.notice, { tone: 'warning', text: VERSION_CONFLICT_MESSAGE });
  });

  it('409 FEEDBACK_ALREADY_RESOLVED: message and the detail is reloaded', async () => {
    const { controller, states } = detailSetup({ respond: apiError(409, 'FEEDBACK_ALREADY_RESOLVED'), reload: RESOLVED });
    await controller.respond('Tarde');
    assert.equal(states.at(-1)?.feedback.status, 'RESOLVED');
    assert.deepEqual(states.at(-1)?.notice, { tone: 'warning', text: ALREADY_RESOLVED_MESSAGE });
  });

  it('401 → login; 403; 404; 422 on the field; 500; network', async () => {
    const expired = detailSetup({ respond: apiError(401) });
    await expired.controller.respond('x');
    assert.deepEqual(expired.navigations, ['/login']);
    const forbidden = detailSetup({ respond: apiError(403, 'FORBIDDEN') });
    await forbidden.controller.respond('x');
    assert.deepEqual(forbidden.states.at(-1)?.notice, { tone: 'error', text: FORBIDDEN_MESSAGE });
    const missing = detailSetup({ respond: apiError(404, 'NOT_FOUND') });
    await missing.controller.respond('x');
    assert.deepEqual(missing.states.at(-1)?.notice, { tone: 'error', text: NOT_FOUND_MESSAGE });
    const invalid = detailSetup({ respond: apiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'response', message: 'Muy larga.' }]) });
    await invalid.controller.respond('x');
    assert.deepEqual(invalid.states.at(-1)?.errors, { response: 'Muy larga.' });
    const server = detailSetup({ respond: apiError(500, 'INTERNAL_ERROR', 'Ocurrió un error interno.') });
    await server.controller.respond('x');
    assert.deepEqual(server.states.at(-1)?.notice, { tone: 'error', text: 'Ocurrió un error interno.' });
    const offline = detailSetup({ respond: new TypeError('fetch failed') });
    await offline.controller.respond('x');
    assert.deepEqual(offline.states.at(-1)?.notice, { tone: 'error', text: CONNECTION_ERROR });
  });
});

describe('feedback detail: resolution', () => {
  it('the note is required; nothing is sent without confirmation', async () => {
    const { controller, calls, states } = detailSetup({ resolve: { feedback: RESOLVED, notification_status: 'SENT' } });
    controller.askResolve('   ');
    assert.deepEqual(states.at(-1)?.errors, { resolution_note: NOTE_REQUIRED });
    controller.askResolve('Nota interna');
    assert.equal(states.at(-1)?.confirming, true);
    assert.equal(calls.length, 0);
    controller.cancelResolve();
    assert.equal(states.at(-1)?.confirming, false);
    await controller.confirmResolve();  // cancelled: nothing to confirm
    assert.equal(calls.length, 0);
  });

  it('confirmed: sent once with the version seen; the detail shows Resuelto and the note apart', async () => {
    const { controller, calls, states } = detailSetup({ resolve: { feedback: RESOLVED, notification_status: 'SENT' } });
    controller.askResolve('  Nota interna  ');
    await Promise.all([controller.confirmResolve(), controller.confirmResolve()]);
    assert.deepEqual(calls, [{ kind: 'resolve', args: [5, 'Nota interna', 1] }]);
    const last = states.at(-1)!;
    assert.equal(last.feedback.status, 'RESOLVED');
    assert.equal(last.feedback.admin_response, null);  // the note is never shown as a response
    assert.equal(last.feedback.resolution_note, 'Nota interna');
    assert.equal(last.confirming, false);
    assert.deepEqual(last.notice, { tone: 'success', text: RESOLVED_DONE });
  });

  it('a resolved feedback accepts no more changes', async () => {
    const { controller, calls } = detailSetup({ initial: RESOLVED });
    await controller.respond('x');
    controller.askResolve('x');
    await controller.confirmResolve();
    assert.equal(calls.length, 0);
  });

  it('409 when resolving: reloads and explains', async () => {
    const { controller, states } = detailSetup({ resolve: apiError(409, 'FEEDBACK_ALREADY_RESOLVED'), reload: RESOLVED });
    controller.askResolve('Nota');
    await controller.confirmResolve();
    assert.equal(states.at(-1)?.feedback.status, 'RESOLVED');
    assert.equal(states.at(-1)?.confirming, false);
    assert.deepEqual(states.at(-1)?.notice, { tone: 'warning', text: ALREADY_RESOLVED_MESSAGE });
  });
});

describe('category and urgency (HU-009)', () => {
  it('list rows and detail show them in Spanish, from the codes of the API', () => {
    assert.deepEqual(classificationOf(item(1)), { category: 'Queja', urgency: 'Alta', categoryRecorded: true, urgencyRecorded: true });
    assert.deepEqual(classificationOf(detail()), { category: 'Solicitud', urgency: 'Baja', categoryRecorded: true, urgencyRecorded: true });
    assert.deepEqual(classificationOf({ category: 'SUGGESTION', urgency: 'MEDIUM' }),
      { category: 'Sugerencia', urgency: 'Media', categoryRecorded: true, urgencyRecorded: true });
    assert.equal(classificationOf({ category: 'OTHER', urgency: 'LOW' }).category, 'Otros');
  });

  it('a feedback sent before HU-009 (null): "No registrada", nothing invented', () => {
    assert.equal(NOT_RECORDED, 'No registrada');
    const old = { ...item(2), category: null, urgency: null };
    assert.deepEqual(classificationOf(old), { category: 'No registrada', urgency: 'No registrada', categoryRecorded: false, urgencyRecorded: false });
    assert.deepEqual(classificationOf(detail({ category: null, urgency: null })),
      { category: 'No registrada', urgency: 'No registrada', categoryRecorded: false, urgencyRecorded: false });
  });

  it('the actions answer with category and urgency: kept, with every field of HU-005', async () => {
    const answered = detail({ ...RESPONDED, category: null, urgency: null });
    const { controller, states } = detailSetup({ initial: detail({ category: null, urgency: null }),
                                                 respond: { feedback: answered, notification_status: 'SENT' } });
    await controller.respond('Revisaremos.');
    const shown = states.at(-1)!.feedback;
    assert.deepEqual(shown, answered); // nothing dropped or renamed
    assert.equal(shown.version, 2);
    assert.equal(classificationOf(shown).category, NOT_RECORDED);

    const resolved = detailSetup({ resolve: { feedback: RESOLVED, notification_status: 'SENT' } });
    resolved.controller.askResolve('Nota interna');
    await resolved.controller.confirmResolve();
    const last = resolved.states.at(-1)!.feedback;
    assert.deepEqual([last.category, last.urgency, last.status, last.resolution_note], ['REQUEST', 'LOW', 'RESOLVED', 'Nota interna']);
    assert.deepEqual(Object.keys(last).sort(), ['action_history', 'admin_response', 'category', 'created_at', 'id', 'message',
      'resident', 'resolution_note', 'resolved_at', 'status', 'subject', 'updated_at', 'urgency', 'version']);
  });
});

describe('history and dates', () => {
  it('labels of the actions and Bogotá time', () => {
    assert.equal(actionLabel('FEEDBACK_RESPONDED'), 'Respuesta registrada');
    assert.equal(actionLabel('FEEDBACK_RESOLVED'), 'Marcado como resuelto');
    assert.equal(actionLabel('FEEDBACK_NOTIFICATION_SENT'), 'Notificación enviada al residente');
    assert.equal(actionLabel('FEEDBACK_NOTIFICATION_FAILED'), 'No fue posible notificar al residente');
    // 03:05 UTC on Oct 3 is still Oct 2, 10:05 p. m. in Bogotá (the connector word depends on ICU).
    const text = formatDateTime('2026-10-03T03:05:00Z').replace(/\s/g, ' ');
    assert.match(text, /^2 de octubre de 2026.* 10:05 p\. m\.$/);
  });
});

describe('navigation (HU-018 access decision)', () => {
  it('/admin/feedback and its detail: administrator only', async () => {
    const session = (role: 'ADMINISTRADOR' | 'RESIDENTE') => async () => ({
      user: { id: 1, full_name: 'X', email: 'x@example.com', role }, csrf_token: 'c'
    });
    for (const path of ['/admin/feedback', '/admin/feedback/5']) {
      assert.equal((await decidePageAccess(path, session('ADMINISTRADOR'))).kind, 'allowed');
      assert.equal((await decidePageAccess(path, session('RESIDENTE'))).kind, 'forbidden');
      assert.deepEqual(await decidePageAccess(path, async () => null), { kind: 'redirect', location: '/login' });
    }
  });
});
