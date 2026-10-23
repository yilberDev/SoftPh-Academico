/**
 * HU-017: the resident's notifications — query, items (event, record link,
 * email result) and list states. The API is a fake injected into the
 * controller (no server). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError, type ApiErrorBody } from '../src/lib/api/errors.ts';
import type { ResidentNotification, ResidentNotificationPage } from '../src/lib/api/notifications.ts';
import {
  CONNECTION_ERROR,
  DEFAULT_QUERY,
  EMAIL_STATUS_LABELS,
  EMPTY_MESSAGE,
  EVENT_LABELS,
  FORBIDDEN_MESSAGE,
  SESSION_EXPIRED_MESSAGE,
  createResidentNotificationList,
  notificationView,
  pageText,
  parseQuery,
  rangeText,
  recordLink,
  toQueryString,
  type ResidentNotificationQuery,
  type ResidentNotificationState
} from '../src/lib/resident/notifications-list.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');

function apiError(status: number, code: string, message = 'x', details: ApiErrorBody['error']['details'] = []): ApiError {
  return new ApiError(status, { error: { code, message, details } });
}

function notification(id: number, overrides: Partial<ResidentNotification> = {}): ResidentNotification {
  return {
    id,
    event_type: 'PAYMENT_REGISTERED',
    entity_type: 'payment',
    entity_id: 10,
    title: 'Pago registrado',
    message: 'Se registró tu pago por $ 150.000,00 del 5 de octubre de 2026 con el recibo RC-00000010.',
    email_status: 'SENT',
    created_at: '2026-10-08T03:30:00Z',
    sent_at: '2026-10-08T03:30:01Z',
    ...overrides
  };
}

function pageOf(items: ResidentNotification[], page = 1, pageSize = 10, total = items.length): ResidentNotificationPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

describe('resident notifications: query', () => {
  it('only page and page_size, from and to the URL; defaults left out, anything else ignored', () => {
    assert.deepEqual(parseQuery(new URLSearchParams('page=3&page_size=25&resident_id=7')), { ...DEFAULT_QUERY, page: 3, pageSize: 25 });
    assert.deepEqual(parseQuery(new URLSearchParams('page=0&page_size=13')), DEFAULT_QUERY);
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    assert.equal(toQueryString({ ...DEFAULT_QUERY, page: 2, pageSize: 50 }), 'page=2&page_size=50');
  });

  it('range and page texts', () => {
    assert.equal(rangeText(pageOf([notification(1)], 1, 10, 1)), 'Mostrando 1–1 de 1 notificación');
    const second = pageOf([notification(11), notification(12)], 2, 10, 12);
    assert.equal(rangeText(second), 'Mostrando 11–12 de 12 notificaciones');
    assert.equal(pageText(second), 'Página 2 de 2');
  });
});

describe('resident notifications: items (HU-017 criterion 4)', () => {
  it('each notification names its event and links to its own record', () => {
    assert.deepEqual(recordLink({ entity_type: 'payment', entity_id: 10 }), { href: '/resident/payments/10', text: 'Ver el pago' });
    assert.deepEqual(recordLink({ entity_type: 'feedback', entity_id: 4 }), { href: '/resident/feedback/4', text: 'Ver el feedback' });
    assert.deepEqual(EVENT_LABELS, {
      PAYMENT_REGISTERED: 'Pago registrado', FEEDBACK_RESPONDED: 'Respuesta a feedback', FEEDBACK_RESOLVED: 'Feedback resuelto'
    });
    const view = notificationView(notification(1));
    assert.equal(view.event, 'Pago registrado');
    assert.equal(view.title, 'Pago registrado');
    assert.match(view.message, /RC-00000010/);
    assert.match(spaces(view.date), /^7 de octubre de 2026/); // Bogotá time
    assert.equal(view.href, '/resident/payments/10');
  });

  it('the email result in the resident\'s words, with what to do when it did not arrive (CU-017 2a, 4a)', () => {
    assert.deepEqual(EMAIL_STATUS_LABELS, {
      SENT: 'Enviada a tu correo', PENDING: 'Envío por correo sin confirmar', FAILED: 'No se pudo enviar a tu correo',
      NO_CONTACT: 'Sin correo de contacto válido', NOT_REQUIRED: 'Solo en el panel (sin correo)'
    });
    assert.equal(notificationView(notification(1)).emailHelp, null);
    assert.equal(notificationView(notification(1, { email_status: 'PENDING', sent_at: null })).emailHelp, null);
    // HU-010: a demo payment is never emailed; nothing to do about it.
    assert.equal(notificationView(notification(1, { email_status: 'NOT_REQUIRED', sent_at: null })).emailHelp, null);
    assert.match(notificationView(notification(1, { email_status: 'FAILED', sent_at: null })).emailHelp ?? '', /administración/);
    assert.match(notificationView(notification(1, { email_status: 'NO_CONTACT', sent_at: null })).emailHelp ?? '', /correo electrónico registrado/);
  });

  it('never shows data the API does not send for the resident, even if an answer carried it', () => {
    const leaked = { ...notification(1), event_key: 'payment:10:registered', email_error: 'SMTPException',
                     resident_id: 7, email: 'laura@example.com' } as ResidentNotification;
    const shown = JSON.stringify(notificationView(leaked));
    for (const secret of ['payment:10', 'SMTPException', 'laura@example.com', 'resident_id']) {
      assert.ok(!shown.includes(secret), secret);
    }
  });
});

function listSetup(answer: ResidentNotificationPage | Error, initial: ResidentNotificationQuery = DEFAULT_QUERY) {
  const requests: string[] = [];
  const states: ResidentNotificationState[] = [];
  const remembered: string[] = [];
  const list = createResidentNotificationList(initial, {
    load: async (query) => {
      requests.push(query);
      if (answer instanceof Error) throw answer;
      return answer;
    },
    render: (state) => states.push(state),
    remember: (query) => remembered.push(query)
  });
  return { list, requests, states, remembered };
}

describe('resident notifications: list', () => {
  it('loading, then the page; the page size and the page go to the API and the URL', async () => {
    const setup = listSetup(pageOf([notification(2), notification(1)]));
    await setup.list.start();
    assert.deepEqual(setup.states.map((s) => s.kind), ['loading', 'results']);
    assert.deepEqual(setup.requests, ['']);
    await setup.list.setPageSize(25);
    assert.equal(setup.requests.at(-1), 'page_size=25');
    assert.equal(setup.remembered.at(-1), 'page_size=25');
  });

  it('no notifications yet: empty (never "no results"), with its message', async () => {
    const setup = listSetup(pageOf([]));
    await setup.list.start();
    assert.equal(setup.states.at(-1)?.kind, 'empty');
    assert.match(EMPTY_MESSAGE, /No tienes notificaciones/);
  });

  it('errors: 401 (session ended, login offered), 403, and 500, network and 422 never as a technical text', async () => {
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
  });
});
