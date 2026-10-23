/**
 * HU-012: the financial reports of the administrator (criteria, links, loading). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import {
  CONNECTION_ERROR,
  DATE_FORMAT_MESSAGE,
  DATE_FROM_REQUIRED,
  DATE_RANGE_MESSAGE,
  DATE_TO_REQUIRED,
  FORBIDDEN_MESSAGE,
  createReportLoader,
  criteriaFromUrl,
  parseScope,
  statementLink,
  validatePaymentsCriteria,
  type ReportState
} from '../src/lib/reports/reports.ts';

describe('payments report criteria (CU-012 5a)', () => {
  it('both dates are required: each missing one says so', () => {
    assert.deepEqual(validatePaymentsCriteria('', ' '), {
      errors: { date_from: DATE_FROM_REQUIRED, date_to: DATE_TO_REQUIRED }, query: null
    });
    assert.deepEqual(validatePaymentsCriteria('2026-10-01', '').errors, { date_to: DATE_TO_REQUIRED });
  });

  it('a real date, and the first one not after the last one', () => {
    assert.deepEqual(validatePaymentsCriteria('2026-02-30', '2026-03-01').errors, { date_from: DATE_FORMAT_MESSAGE });
    assert.deepEqual(validatePaymentsCriteria('2026-10-31', '2026-10-01').errors, { date_to: DATE_RANGE_MESSAGE });
  });

  it('complete criteria give the query string; the same day is a valid range', () => {
    assert.deepEqual(validatePaymentsCriteria(' 2026-10-01 ', '2026-10-31'), {
      errors: {}, query: 'date_from=2026-10-01&date_to=2026-10-31'
    });
    assert.equal(validatePaymentsCriteria('2026-10-01', '2026-10-01').query, 'date_from=2026-10-01&date_to=2026-10-01');
  });

  it('the criteria and the scope come back from the page URL', () => {
    assert.deepEqual(criteriaFromUrl(new URLSearchParams('date_from=2026-10-01&date_to=2026-10-31')),
      { dateFrom: '2026-10-01', dateTo: '2026-10-31' });
    assert.equal(parseScope(new URLSearchParams('scope=OVERDUE')), 'OVERDUE');
    assert.equal(parseScope(new URLSearchParams('scope=otro')), 'ALL');
  });
});

describe('pending balances: who owes (HU-011 criterion 3.2)', () => {
  it('each resident listed opens their statement; an apartment without a resident has no link', () => {
    assert.equal(statementLink({ id: 7 }), '/admin/residents/7/account-statement');
    assert.equal(statementLink(null), null);
  });
});

function setup<T>(load: (query: string) => Promise<T>) {
  const states: ReportState<T>[] = [];
  const navigations: string[] = [];
  const loader = createReportLoader<T>({ load, render: (state) => states.push(state), navigate: (p) => navigations.push(p) });
  return { loader, states, navigations };
}

const apiError = (status: number, code: string, details: ApiError['details'] = []) =>
  new ApiError(status, { error: { code, message: 'Mensaje del servidor.', details } });

describe('generating a report', () => {
  it('loading, then the whole report of one answer', async () => {
    const { loader, states } = setup(async (query) => ({ query }));
    await loader.generate('date_from=2026-10-01&date_to=2026-10-31');
    assert.deepEqual(states.map((state) => state.kind), ['loading', 'ready']);
    assert.deepEqual(loader.state, { kind: 'ready', report: { query: 'date_from=2026-10-01&date_to=2026-10-31' } });
  });

  it('an older answer never replaces a newer one; incomplete criteria cancel the one on its way', async () => {
    const answers: ((value: string) => void)[] = [];
    const { loader } = setup((query) => new Promise<string>((resolve) => answers.push(() => resolve(query))));
    const first = loader.generate('a');
    const second = loader.generate('b');
    answers[1]('b');
    await second;
    answers[0]('a');
    await first;
    assert.deepEqual(loader.state, { kind: 'ready', report: 'b' });

    const third = loader.generate('c');
    loader.invalid({ date_from: DATE_FROM_REQUIRED });
    answers[2]('c');
    await third;
    assert.deepEqual(loader.state, { kind: 'invalid', errors: { date_from: DATE_FROM_REQUIRED } });
  });

  it('the server\'s 422 goes next to the dates', async () => {
    const { loader } = setup(async () => {
      throw apiError(422, 'VALIDATION_ERROR', [{ field: 'query.date_to', message: DATE_RANGE_MESSAGE }]);
    });
    await loader.generate('x');
    assert.deepEqual(loader.state, { kind: 'invalid', errors: { date_to: DATE_RANGE_MESSAGE } });
  });

  it('an error never shows an incomplete report (CU-012 6c); 403 and 401 are said plainly', async () => {
    for (const [error, message] of [
      [new TypeError('Failed to fetch'), CONNECTION_ERROR],
      [apiError(500, 'INTERNAL_ERROR'), CONNECTION_ERROR],
      [apiError(403, 'FORBIDDEN'), FORBIDDEN_MESSAGE]
    ] as const) {
      const { loader } = setup(async () => { throw error; });
      await loader.generate('x');
      assert.deepEqual(loader.state, { kind: 'error', message });
    }
    const expired = setup(async () => { throw apiError(401, 'NOT_AUTHENTICATED'); });
    await expired.loader.generate('x');
    assert.deepEqual(expired.navigations, ['/login']);
  });
});
