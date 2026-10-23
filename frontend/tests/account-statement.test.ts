/**
 * HU-011 / HU-014: the account statement shown to the administrator and to the resident. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { AccountStatement, StatementObligation, StatementPayment } from '../src/lib/api/account-statement.ts';
import { ApiError } from '../src/lib/api/errors.ts';
import {
  ACCOUNT_STATUS_LABELS,
  ADMIN_NOT_AVAILABLE,
  CONNECTION_ERROR,
  FORBIDDEN_MESSAGE,
  allocationLines,
  createStatementLoader,
  groupObligations,
  obligationText,
  payerText,
  type StatementState
} from '../src/lib/account-statement/statement-view.ts';
import { accountStatementPath } from '../src/lib/residents/resident-detail.ts';

function obligation(overrides: Partial<StatementObligation> = {}): StatementObligation {
  return {
    id: 1, concept: 'PENALTY', description: 'Ruido', billing_period: null, due_date: '2026-10-10',
    amount: '80000.00', applied_amount: '0.00', outstanding_amount: '80000.00', status: 'OVERDUE', ...overrides
  };
}

function payment(overrides: Partial<StatementPayment> = {}): StatementPayment {
  return {
    id: 9, receipt_number: 'RC-00000009', payment_date: '2026-10-01', registered_at: '2026-10-01T15:00:00Z',
    amount: '90000.00', applied_amount: '90000.00', credit_amount: '0.00', status: 'APPLIED', method: 'CASH',
    source: 'MANUAL', concept: 'OTHER', concept_description: 'Abono', by_this_resident: true,
    payer_name: 'Laura Gómez', allocations: [{ charge_id: 2, amount: '50000.00' }], ...overrides
  };
}

function statement(overrides: Partial<AccountStatement> = {}): AccountStatement {
  return {
    resident: { id: 7, full_name: 'Laura Gómez', status: 'ACTIVE', unit: { id: 3, label: 'B1-TO2-APTO-104' } },
    as_of: '2026-10-22', generated_at: '2026-10-22T15:00:00Z', has_movements: true,
    summary: {
      status: 'OVERDUE', total_charged: '130000.00', total_applied: '50000.00', pending_balance: '80000.00',
      overdue_balance: '80000.00', credit_balance: '40000.00', obligations: 2, pending_obligations: 1,
      overdue_obligations: 1, payments: 1
    },
    obligations: [obligation(), obligation({ id: 2, description: 'Pagada', status: 'PAID', amount: '50000.00',
      applied_amount: '50000.00', outstanding_amount: '0.00' })],
    payments: [payment()],
    ...overrides
  };
}

describe('what the statement shows', () => {
  it('the account status in the words of HU-011: al día, pendiente, en mora', () => {
    assert.deepEqual(ACCOUNT_STATUS_LABELS, { UP_TO_DATE: 'Al día', PENDING: 'Pendiente', OVERDUE: 'En mora' });
  });

  it('groups the obligations as the server classified them, keeping their order', () => {
    const items = [
      obligation({ id: 1, status: 'OVERDUE' }), obligation({ id: 2, status: 'PAID' }),
      obligation({ id: 3, status: 'PARTIAL' }), obligation({ id: 4, status: 'PENDING' }),
      obligation({ id: 5, status: 'OVERDUE' })
    ];
    const groups = groupObligations(items);
    assert.deepEqual(groups.overdue.map((item) => item.id), [1, 5]);
    assert.deepEqual(groups.pending.map((item) => item.id), [3, 4]);
    assert.deepEqual(groups.paid.map((item) => item.id), [2]);
  });

  it('names each obligation by its concept, description and period', () => {
    assert.equal(obligationText(obligation()), 'Penalización: Ruido');
    assert.equal(obligationText(obligation({ concept: 'MONTHLY_FEE', description: null, billing_period: '2026-10' })),
      'Mensualidad · Octubre de 2026');
  });

  it('says what each payment covered, with the obligations of the same statement', () => {
    const lines = allocationLines(payment(), statement().obligations);
    assert.equal(lines.length, 1);
    assert.match(lines[0], /^Penalización: Pagada: \$\s?50\.000,00$/);
    assert.deepEqual(allocationLines(payment({ allocations: [] }), []), []);
  });

  it('keeps who made each payment (administrator view)', () => {
    assert.equal(payerText(payment()), 'Este residente');
    assert.equal(payerText(payment({ by_this_resident: false, payer_name: 'Pedro Ruiz' })),
      'Pedro Ruiz (otro residente de la unidad)');
    assert.equal(payerText(payment({ by_this_resident: false, payer_name: null })), 'Otro residente de la unidad');
  });

  it('the administrator reaches it from the resident', () => {
    assert.equal(accountStatementPath(7), '/admin/residents/7/account-statement');
  });
});

function setup(load: () => Promise<AccountStatement>, notFoundMessage?: string) {
  const states: StatementState[] = [];
  const navigations: string[] = [];
  const loader = createStatementLoader({ load, render: (state) => states.push(state), navigate: (path) => navigations.push(path),
    notFoundMessage });
  return { loader, states, navigations };
}

const apiError = (status: number, code: string, message = 'Mensaje del servidor.') =>
  new ApiError(status, { error: { code, message, details: [] } });

describe('loading the statement', () => {
  it('loading, then the whole statement from one answer', async () => {
    const { loader, states } = setup(async () => statement());
    await loader.start();
    assert.deepEqual(states.map((state) => state.kind), ['loading', 'ready']);
    assert.deepEqual(loader.state, { kind: 'ready', statement: statement(), refreshing: false, notice: null });
  });

  it('a statement without movements is not an error (CU-011 5a, CU-014 3a)', async () => {
    const empty = statement({ has_movements: false, obligations: [], payments: [] });
    const { loader } = setup(async () => empty);
    await loader.start();
    assert.equal(loader.state.kind, 'ready');
  });

  it('refresh keeps the statement on screen, then replaces it whole (never two moments mixed)', async () => {
    let answer = statement();
    const { loader, states } = setup(async () => answer);
    await loader.start();
    answer = statement({ summary: { ...statement().summary, status: 'UP_TO_DATE', pending_balance: '0.00' } });
    await loader.refresh();
    const refreshing = states.at(-2);
    assert.equal(refreshing?.kind === 'ready' && refreshing.refreshing, true);
    assert.equal(refreshing?.kind === 'ready' && refreshing.statement.summary.pending_balance, '80000.00');
    assert.equal(loader.state.kind === 'ready' && loader.state.statement.summary.pending_balance, '0.00');
  });

  it('an older answer never replaces a newer one', async () => {
    const answers: ((value: AccountStatement) => void)[] = [];
    const { loader } = setup(() => new Promise((resolve) => answers.push(resolve)));
    const first = loader.start();
    const second = loader.refresh();
    answers[1](statement({ as_of: '2026-10-23' }));
    await second;
    answers[0](statement({ as_of: '2026-10-22' }));
    await first;
    assert.equal(loader.state.kind === 'ready' && loader.state.statement.as_of, '2026-10-23');
  });

  it('a failed update keeps the whole previous statement and says so', async () => {
    let fail = false;
    const { loader } = setup(async () => {
      if (fail) throw new TypeError('Failed to fetch');
      return statement();
    });
    await loader.start();
    fail = true;
    await loader.refresh();
    assert.deepEqual(loader.state, { kind: 'ready', statement: statement(), refreshing: false, notice: CONNECTION_ERROR });
  });

  it('no connection or a server error on the first load: a recoverable error, never a partial statement', async () => {
    for (const error of [new TypeError('Failed to fetch'), apiError(500, 'INTERNAL_ERROR')]) {
      const { loader } = setup(async () => { throw error; });
      await loader.start();
      assert.deepEqual(loader.state, { kind: 'error', message: CONNECTION_ERROR });
    }
  });

  it('404: the administrator sees "not available"; the resident, the message of the server', async () => {
    const admin = setup(async () => { throw apiError(404, 'RESIDENT_NOT_FOUND'); }, ADMIN_NOT_AVAILABLE);
    await admin.loader.start();
    assert.deepEqual(admin.loader.state, { kind: 'unavailable', message: ADMIN_NOT_AVAILABLE });

    const message = 'No tienes una unidad residencial vigente, por lo que no hay un estado de cuenta para consultar.';
    const resident = setup(async () => { throw apiError(404, 'ACCOUNT_STATEMENT_UNAVAILABLE', message); });
    await resident.loader.start();
    assert.deepEqual(resident.loader.state, { kind: 'unavailable', message });
  });

  it('a statement that became unavailable is not kept on screen', async () => {
    let gone = false;
    const { loader } = setup(async () => {
      if (gone) throw apiError(404, 'RESIDENT_NOT_FOUND');
      return statement();
    }, ADMIN_NOT_AVAILABLE);
    await loader.start();
    gone = true;
    await loader.refresh();
    assert.deepEqual(loader.state, { kind: 'unavailable', message: ADMIN_NOT_AVAILABLE });
  });

  it('401: back to the login page; 403: not allowed', async () => {
    const expired = setup(async () => { throw apiError(401, 'NOT_AUTHENTICATED'); });
    await expired.loader.start();
    assert.deepEqual(expired.navigations, ['/login']);

    const forbidden = setup(async () => { throw apiError(403, 'FORBIDDEN'); });
    await forbidden.loader.start();
    assert.deepEqual(forbidden.loader.state, { kind: 'error', message: FORBIDDEN_MESSAGE });
  });
});
