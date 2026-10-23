/**
 * The administrator's home page (/admin): its modules and its summary. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ADMIN_MODULES, UNAVAILABLE_DETAIL, summaryTiles } from '../src/lib/admin/dashboard.ts';
import { protectedRule } from '../src/lib/auth/roles.ts';
import { ICONS } from '../src/lib/landing/icons.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');

describe('admin home: modules', () => {
  it('lists the eight modules, in order, each with an existing administrator page', () => {
    assert.deepEqual(
      ADMIN_MODULES.map(({ title, href }) => [title, href]),
      [
        ['Unidades residenciales', '/admin/blocks'],
        ['Residentes', '/admin/residents'],
        ['Invitar residente', '/admin/residents/invite'],
        ['Obligaciones', '/admin/obligations'],
        ['Pagos', '/admin/payments'],
        ['Feedback', '/admin/feedback'],
        ['Estados de cuenta', '/admin/residents'],
        ['Reportes financieros', '/admin/reports']
      ]
    );
    for (const { href } of ADMIN_MODULES) assert.deepEqual(protectedRule(href)?.roles, ['ADMINISTRADOR'], href);
  });

  it('every module has its icon, category, description and call to action', () => {
    for (const module of ADMIN_MODULES) {
      assert.ok(module.icon in ICONS, module.icon);
      assert.ok(module.category && module.description && module.action, module.title);
    }
    assert.equal(new Set(ADMIN_MODULES.map((module) => module.title)).size, ADMIN_MODULES.length);
  });

  it('only one title says "Obligaciones" or "Reportes financieros" (the links the E2E tests open)', () => {
    for (const name of [/Obligaciones/, /Reportes financieros/]) {
      assert.equal(ADMIN_MODULES.filter((module) => name.test(module.title)).length, 1, String(name));
    }
  });
});

describe('admin home: summary', () => {
  const sources = {
    residents: 1250,
    pendingFeedback: 2,
    balances: { units: 3, overdue_units: 0, total_pending: '450000.00', total_overdue: '0.00' }
  };

  it('shows the numbers the API gave, formatted, with the amounts of the report', () => {
    const tiles = summaryTiles(sources);
    assert.deepEqual(tiles.map((tile) => tile.key), ['residents', 'pending-feedback', 'units-pending', 'units-overdue']);
    assert.deepEqual(tiles.map((tile) => tile.value), ['1.250', '2', '3', '0']);
    assert.equal(spaces(tiles[2].detail), 'Total pendiente: $ 450.000,00');
    assert.equal(spaces(tiles[3].detail), 'Total en mora: $ 0,00');
  });

  it('draws attention only to pending feedback or overdue units that exist', () => {
    const tones = (input: typeof sources) => summaryTiles(input).map((tile) => tile.tone);
    assert.deepEqual(tones(sources), ['neutral', 'attention', 'neutral', 'neutral']);
    assert.deepEqual(tones({ ...sources, pendingFeedback: 0, balances: { ...sources.balances, overdue_units: 1 } }), [
      'neutral',
      'neutral',
      'neutral',
      'attention'
    ]);
  });

  it('a number the API did not give is unavailable, never 0', () => {
    const tiles = summaryTiles({ residents: null, pendingFeedback: null, balances: null });
    for (const tile of tiles) {
      assert.equal(tile.value, null, tile.key);
      assert.equal(tile.detail, UNAVAILABLE_DETAIL, tile.key);
      assert.equal(tile.tone, 'neutral', tile.key);
    }
    const partial = summaryTiles({ ...sources, pendingFeedback: null });
    assert.deepEqual(partial.map((tile) => tile.value), ['1.250', null, '3', '0']);
  });

  it('each number opens the page with its detail', () => {
    assert.deepEqual(summaryTiles(sources).map((tile) => tile.href), [
      '/admin/residents',
      '/admin/feedback?status=PENDING',
      '/admin/reports/pending-balances',
      '/admin/reports/pending-balances?scope=OVERDUE'
    ]);
  });
});
