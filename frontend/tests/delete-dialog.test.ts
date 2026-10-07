/**
 * HU-013: delete confirmation dialog of the structure pages.
 * Run with `npm test` (node:test, native TypeScript in Node 24; no extra dependencies).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import {
  CONNECTION_ERROR,
  createDeleteDialog,
  type DeleteDialogState
} from '../src/lib/structure/delete-dialog.ts';

function apiError(status: number, code: string, message = 'Mensaje del backend.'): ApiError {
  return new ApiError(status, { error: { code, message, details: [] } });
}

/** A dialog whose DELETE answers with the given results, in order. */
function setup(...answers: (ApiError | Error | 'ok')[]) {
  const rendered: DeleteDialogState[] = [];
  let deleted = 0;
  let calls = 0;
  const dialog = createDeleteDialog({
    render: (state) => rendered.push(state),
    onDeleted: () => {
      deleted += 1;
    }
  });
  const remove = async (): Promise<null> => {
    const answer = answers[calls] ?? 'ok';
    calls += 1;
    if (answer !== 'ok') {
      throw answer;
    }
    return null;
  };
  return {
    dialog,
    remove,
    rendered,
    get calls() {
      return calls;
    },
    get deleted() {
      return deleted;
    }
  };
}

describe('delete dialog', () => {
  it('opens a confirmation without sending anything', () => {
    const t = setup();
    t.dialog.open('B4', t.remove);

    assert.deepEqual(t.dialog.state, { kind: 'confirm', label: 'B4', submitting: false, error: null });
    assert.equal(t.calls, 0);
  });

  it('204: deletes once, closes and returns to the list', async () => {
    const t = setup('ok');
    t.dialog.open('B4', t.remove);

    await t.dialog.confirm();

    assert.equal(t.calls, 1);
    assert.equal(t.deleted, 1);
    assert.deepEqual(t.dialog.state, { kind: 'closed' });
    assert.deepEqual(
      t.rendered.map((state) => state.kind === 'confirm' ? `confirm:${state.submitting}` : state.kind),
      ['confirm:false', 'confirm:true', 'closed']
    );
  });

  it('cancel before confirming never sends DELETE', async () => {
    const t = setup();
    t.dialog.open('B4', t.remove);

    t.dialog.close();
    await t.dialog.confirm();

    assert.equal(t.calls, 0);
    assert.equal(t.deleted, 0);
    assert.deepEqual(t.dialog.state, { kind: 'closed' });
  });

  it('409 BLOCK_HAS_ACTIVE_TOWERS: informative state with the reason', async () => {
    const t = setup(apiError(409, 'BLOCK_HAS_ACTIVE_TOWERS'));
    t.dialog.open('B4', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, {
      kind: 'blocked',
      label: 'B4',
      reason: 'El bloque tiene torres activas. Elimínalas primero.'
    });
    assert.equal(t.deleted, 0);
  });

  it('409 TOWER_HAS_ACTIVE_UNITS: informative state with the reason', async () => {
    const t = setup(apiError(409, 'TOWER_HAS_ACTIVE_UNITS'));
    t.dialog.open('B1-TO2', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, {
      kind: 'blocked',
      label: 'B1-TO2',
      reason: 'La torre tiene apartamentos activos. Elimínalos primero.'
    });
  });

  for (const code of ['BLOCK_HAS_ACTIVE_TOWERS', 'TOWER_HAS_ACTIVE_UNITS', 'UNIT_IN_USE', 'UNIT_HAS_BALANCE']) {
    it(`${code}: no further DELETE can be sent from the informative state`, async () => {
      const t = setup(apiError(409, code));
      t.dialog.open('B4', t.remove);
      await t.dialog.confirm();

      await t.dialog.confirm();
      await t.dialog.confirm();

      assert.equal(t.calls, 1);
      assert.equal(t.dialog.state.kind, 'blocked');
    });
  }

  it('409 UNIT_IN_USE (HU-003): informative state with the reason', async () => {
    const t = setup(apiError(409, 'UNIT_IN_USE'));
    t.dialog.open('B1-TO2-APTO-104', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, {
      kind: 'blocked',
      label: 'B1-TO2-APTO-104',
      reason: 'El apartamento tiene un residente o una invitación pendiente.'
    });
  });

  it('409 UNIT_HAS_BALANCE (obligations extension): informative state with the reason, no amounts', async () => {
    const t = setup(apiError(409, 'UNIT_HAS_BALANCE'));
    t.dialog.open('B1-TO2-APTO-104', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, {
      kind: 'blocked',
      label: 'B1-TO2-APTO-104',
      reason: 'El apartamento tiene obligaciones pendientes o saldo a favor. Su saldo debe quedar en cero antes de eliminarlo.'
    });
    assert.equal(t.calls, 1);
  });

  it('closing the informative state sends nothing', async () => {
    const t = setup(apiError(409, 'BLOCK_HAS_ACTIVE_TOWERS'));
    t.dialog.open('B4', t.remove);
    await t.dialog.confirm();

    t.dialog.close();

    assert.equal(t.calls, 1);
    assert.deepEqual(t.dialog.state, { kind: 'closed' });
  });

  it('reopening starts a new confirmation and the backend validates again', async () => {
    // The frontend does not assume the item is still blocked (or free).
    const t = setup(apiError(409, 'BLOCK_HAS_ACTIVE_TOWERS'), 'ok');
    t.dialog.open('B4', t.remove);
    await t.dialog.confirm();
    t.dialog.close();

    t.dialog.open('B4', t.remove);
    assert.equal(t.dialog.state.kind, 'confirm');
    await t.dialog.confirm();

    assert.equal(t.calls, 2);
    assert.equal(t.deleted, 1);
  });

  it('other API errors keep the existing handling: message and the action stays available', async () => {
    const t = setup(apiError(409, 'VERSION_CONFLICT', 'El registro fue modificado por otra persona.'), 'ok');
    t.dialog.open('B4', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, {
      kind: 'confirm',
      label: 'B4',
      submitting: false,
      error: 'El registro fue modificado por otra persona.'
    });
    await t.dialog.confirm();
    assert.equal(t.calls, 2);
  });

  it('a 404 or 500 is not treated as an informative state', async () => {
    for (const error of [apiError(404, 'NOT_FOUND'), apiError(500, 'INTERNAL_ERROR')]) {
      const t = setup(error);
      t.dialog.open('B4', t.remove);
      await t.dialog.confirm();
      assert.equal(t.dialog.state.kind, 'confirm');
    }
  });

  it('unknown 409 codes (including prototype names) are not informative states', async () => {
    for (const code of ['CONFLICT', 'toString', '__proto__']) {
      const t = setup(apiError(409, code));
      t.dialog.open('B4', t.remove);
      await t.dialog.confirm();
      assert.equal(t.dialog.state.kind, 'confirm', code);
    }
  });

  it('network errors show the connection message', async () => {
    const t = setup(new TypeError('Failed to fetch'));
    t.dialog.open('B4', t.remove);

    await t.dialog.confirm();

    assert.deepEqual(t.dialog.state, { kind: 'confirm', label: 'B4', submitting: false, error: CONNECTION_ERROR });
  });

  it('a double click while sending sends only one DELETE', async () => {
    const t = setup('ok');
    t.dialog.open('B4', t.remove);

    await Promise.all([t.dialog.confirm(), t.dialog.confirm()]);

    assert.equal(t.calls, 1);
    assert.equal(t.deleted, 1);
  });

  it('a late error of a dialog that was already closed is ignored', async () => {
    const t = setup(apiError(409, 'BLOCK_HAS_ACTIVE_TOWERS'));
    t.dialog.open('B4', t.remove);

    const pending = t.dialog.confirm();
    t.dialog.close();
    await pending;

    assert.deepEqual(t.dialog.state, { kind: 'closed' });
  });
});
