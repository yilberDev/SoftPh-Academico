/**
 * The resident's home page (/resident): its modules, its summary and its recent activity. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { ResidentNotification } from '../src/lib/api/notifications.ts';
import { protectedRule } from '../src/lib/auth/roles.ts';
import { ICONS } from '../src/lib/landing/icons.ts';
import {
  ACTIVITY_SIZE,
  NO_UNIT_DETAIL,
  UNAVAILABLE_DETAIL,
  activityItems,
  pendingFeedback,
  residentModules,
  residentSummaryTiles,
  unitText,
  type ResidentSummarySources
} from '../src/lib/resident/dashboard.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');
const links = (demo: boolean) => residentModules(demo).flatMap((module) => (module.href ? [module.href] : (module.links ?? []).map((link) => link.href)));

describe('resident home: modules', () => {
  it('the modules, in order; the demo payment only while it is enabled', () => {
    assert.deepEqual(residentModules(false).map((module) => module.title), ['Estado de cuenta', 'Mis pagos', 'Feedback', 'Notificaciones']);
    assert.deepEqual(residentModules(true).map((module) => module.title),
      ['Estado de cuenta', 'Mis pagos', 'Pago de demostración', 'Feedback', 'Notificaciones']);
  });

  it('every link is an existing page of the resident area; feedback keeps its two actions', () => {
    assert.deepEqual(links(true), ['/resident/account-statement', '/resident/payments', '/resident/demo-payments',
      '/resident/feedback/new', '/resident/feedback', '/resident/notifications']);
    for (const href of links(true)) assert.deepEqual(protectedRule(href)?.roles, ['RESIDENTE'], href);
    const feedback = residentModules(false).find((module) => module.title === 'Feedback')!;
    assert.deepEqual(feedback.links?.map((link) => link.label), ['Enviar feedback', 'Mis feedback']);
  });

  it('every module has its icon and description; one link or the feedback actions, never both', () => {
    for (const module of residentModules(true)) {
      assert.ok(module.icon in ICONS, module.icon);
      assert.ok(module.description, module.title);
      assert.ok(Boolean(module.href) !== Boolean(module.links), module.title);
    }
  });
});

describe('resident home: summary', () => {
  const sources: ResidentSummarySources = {
    statement: { kind: 'ok', unit: 'B1-TO1-APTO-101', status: 'OVERDUE', pending_balance: '130000.00', pending_obligations: 2 },
    feedback: { items: [{ status: 'PENDING' }, { status: 'RESOLVED' }, { status: 'PENDING' }], total: 3 }
  };

  it('the balance and the status come from the statement, as the backend computed them', () => {
    const tiles = residentSummaryTiles(sources);
    assert.deepEqual(tiles.map((tile) => tile.key), ['balance', 'account-status', 'pending-feedback']);
    assert.equal(spaces(tiles[0].value!), '$ 130.000,00');
    assert.equal(tiles[0].detail, '2 obligaciones con saldo.');
    assert.equal(tiles[1].value, 'En mora');
    assert.equal(tiles[1].detail, 'Hay obligaciones con saldo pendiente después de su fecha de pago.');
    assert.deepEqual(tiles.map((tile) => tile.tone), ['attention', 'danger', 'attention']);
    assert.deepEqual(tiles.map((tile) => tile.href), ['/resident/account-statement', '/resident/account-statement', '/resident/feedback']);
  });

  it('up to date: no attention, and the wording of one obligation', () => {
    const tiles = residentSummaryTiles({
      statement: { kind: 'ok', unit: 'X', status: 'UP_TO_DATE', pending_balance: '0.00', pending_obligations: 0 },
      feedback: { items: [], total: 0 }
    });
    assert.deepEqual(tiles.map((tile) => spaces(tile.value!)), ['$ 0,00', 'Al día', '0']);
    assert.deepEqual(tiles.map((tile) => tile.detail), ['Sin obligaciones con saldo.', 'No hay obligaciones con saldo pendiente.',
      'Aún no has enviado feedback.']);
    assert.deepEqual(tiles.map((tile) => tile.tone), ['neutral', 'neutral', 'neutral']);
    const one = residentSummaryTiles({ ...sources, statement: { ...sources.statement, pending_obligations: 1 } as never });
    assert.equal(one[0].detail, '1 obligación con saldo.');
  });

  it('the pending feedback is counted only when the page holds all of them', () => {
    assert.equal(pendingFeedback(sources.feedback), 2);
    assert.equal(residentSummaryTiles(sources)[2].detail, 'De 3 enviados, sin resolver.');
    assert.equal(pendingFeedback({ items: [{ status: 'PENDING' }], total: 150 }), null);
    assert.equal(pendingFeedback(null), null);
  });

  it('without an apartment or without an answer: unavailable, never 0', () => {
    const noUnit = residentSummaryTiles({ statement: { kind: 'no-unit' }, feedback: null });
    assert.deepEqual(noUnit.map((tile) => tile.value), [null, null, null]);
    assert.deepEqual(noUnit.map((tile) => tile.detail), [NO_UNIT_DETAIL, NO_UNIT_DETAIL, UNAVAILABLE_DETAIL]);
    assert.deepEqual(noUnit.map((tile) => tile.tone), ['neutral', 'neutral', 'neutral']);
    const failed = residentSummaryTiles({ ...sources, statement: { kind: 'error' } });
    assert.deepEqual(failed.map((tile) => tile.value), [null, null, '2']);
    assert.equal(failed[0].detail, UNAVAILABLE_DETAIL);
  });

  it('the unit of the welcome: its label, none, or nothing when the statement could not be read', () => {
    assert.equal(unitText(sources.statement), 'B1-TO1-APTO-101');
    assert.equal(unitText({ kind: 'no-unit' }), 'Sin unidad residencial vigente');
    assert.equal(unitText({ kind: 'error' }), null);
  });
});

describe('resident home: recent activity', () => {
  const notification = (id: number, entity: 'payment' | 'feedback'): ResidentNotification => ({
    id,
    event_type: entity === 'payment' ? 'PAYMENT_REGISTERED' : 'FEEDBACK_RESOLVED',
    entity_type: entity,
    entity_id: 40 + id,
    title: `Aviso ${id}`,
    message: 'Mensaje',
    email_status: 'SENT',
    created_at: '2026-10-22T15:30:00Z',
    sent_at: '2026-10-22T15:31:00Z'
  });

  it('the latest notifications, with their event, date and the record they come from', () => {
    const items = activityItems([notification(1, 'payment'), notification(2, 'feedback')]);
    assert.deepEqual(items.map((item) => [item.event, item.title, item.href, item.linkText, item.icon]), [
      ['Pago registrado', 'Aviso 1', '/resident/payments/41', 'Ver el pago', 'receipt'],
      ['Feedback resuelto', 'Aviso 2', '/resident/feedback/42', 'Ver el feedback', 'message']
    ]);
    assert.match(items[0].date, /22/);
  });

  it(`at most ${ACTIVITY_SIZE}; none when there are none`, () => {
    const many = Array.from({ length: 8 }, (_, index) => notification(index + 1, 'payment'));
    assert.equal(activityItems(many).length, ACTIVITY_SIZE);
    assert.deepEqual(activityItems([]), []);
  });
});
