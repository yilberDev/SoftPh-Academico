/**
 * HU-016: /forgot-password and /reset-password flows.
 * Run with `npm test` (node:test, native TypeScript in Node 24; no extra dependencies).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { ResetConfirmPayload } from '../src/lib/api/password-reset.ts';
import { readToken } from '../src/lib/auth/fragment-token.ts';
import {
  CONNECTION_ERROR,
  REQUEST_RECEIVED,
  createForgotFlow,
  createResetFlow,
  validateResetEmail,
  type ForgotState,
  type ResetState
} from '../src/lib/auth/password-reset.ts';
import { FORGOT_PASSWORD_PATH } from '../src/lib/auth/roles.ts';

const TOKEN = 'Q'.repeat(21) + 'x_-'.repeat(7) + '9';
const PASSWORD = 'una frase nueva y segura 2026';
const PREVIEW = { expires_at: '2026-10-08T20:05:00Z' };

function apiError(status: number, code: string, details: { field?: string; message: string }[] = []): ApiError {
  return new ApiError(status, { error: { code, message: `Mensaje ${code}.`, details } });
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0)); // a real request takes time

describe('/forgot-password', () => {
  function setup(answer: 'ok' | Error = 'ok') {
    const calls: string[] = [];
    const states: ForgotState[] = [];
    const flow = createForgotFlow({
      request: async (email) => {
        calls.push(email);
        await tick();
        if (answer !== 'ok') throw answer;
        return { status: 'ACCEPTED' };
      },
      render: (state) => states.push(state)
    });
    return { flow, calls, states };
  }

  it('is the page the login links to', () => {
    assert.equal(FORGOT_PASSWORD_PATH, '/forgot-password');
  });

  it('validates the email format without calling the API', async () => {
    const t = setup();
    await t.flow.submit('  ');
    assert.deepEqual(t.flow.state, { kind: 'idle', error: 'Ingresa tu correo electrónico.' });
    await t.flow.submit('sin-arroba');
    assert.deepEqual(t.flow.state, { kind: 'idle', error: 'Ingresa un correo electrónico válido.' });
    assert.equal(t.calls.length, 0);
    assert.equal(validateResetEmail('residente@example.com'), null);
  });

  it('sending → the same received message, whatever the email', async () => {
    for (const email of ['residente@example.com', 'nadie@example.com', 'admin@example.com']) {
      const t = setup();
      await t.flow.submit(` ${email} `);
      assert.deepEqual(t.states, [{ kind: 'sending' }, { kind: 'received', message: REQUEST_RECEIVED }]);
      assert.deepEqual(t.calls, [email]);
    }
    assert.match(REQUEST_RECEIVED, /^Si la cuenta corresponde a un residente registrado/);
  });

  it('blocks double submission', async () => {
    const t = setup();
    await Promise.all([t.flow.submit('a@example.com'), t.flow.submit('a@example.com')]);
    await t.flow.submit('a@example.com');
    assert.equal(t.calls.length, 1);
  });

  it('network and server errors', async () => {
    const network = setup(new TypeError('Failed to fetch'));
    await network.flow.submit('a@example.com');
    assert.deepEqual(network.flow.state, { kind: 'error', message: CONNECTION_ERROR });
    const forbidden = setup(apiError(403, 'CSRF_FAILED'));
    await forbidden.flow.submit('a@example.com');
    assert.deepEqual(forbidden.flow.state, { kind: 'error', message: 'Mensaje CSRF_FAILED.' });
    const invalid = setup(apiError(422, 'VALIDATION_ERROR', [{ field: 'email', message: 'Demasiado largo.' }]));
    await invalid.flow.submit('a@example.com');
    assert.deepEqual(invalid.flow.state, { kind: 'idle', error: 'Demasiado largo.' });
  });
});

describe('/reset-password', () => {
  type Answer = 'ok' | Error;
  function setup(verifyAnswer: Answer = 'ok', ...confirmAnswers: Answer[]) {
    const calls: string[] = [];
    const states: ResetState[] = [];
    const sent: ResetConfirmPayload[] = [];
    const flow = createResetFlow({
      verify: async (token) => {
        calls.push(`verify:${token}`);
        if (verifyAnswer !== 'ok') throw verifyAnswer;
        return PREVIEW;
      },
      confirm: async (payload) => {
        calls.push('confirm');
        sent.push(payload);
        await tick();
        const answer = confirmAnswers.shift() ?? 'ok';
        if (answer !== 'ok') throw answer;
        return { status: 'PASSWORD_UPDATED' };
      },
      clearToken: () => calls.push('clearToken'),
      render: (state) => states.push(state)
    });
    return { flow, calls, states, sent };
  }

  it('reads the token from the fragment only', () => {
    assert.equal(readToken(`#token=${TOKEN}`), TOKEN);
    assert.equal(readToken(`?token=${TOKEN}`), null);
    for (const hash of ['', '#token=', '#token=corto', `#token=${TOKEN}x`]) {
      assert.equal(readToken(hash), null, hash);
    }
  });

  it('removes the token from the URL before calling the backend', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    assert.deepEqual(t.calls, ['clearToken', `verify:${TOKEN}`]);
    assert.deepEqual(t.states, [{ kind: 'verifying' }, { kind: 'ready', preview: PREVIEW, errors: {}, alert: null }]);
  });

  it('a malformed link is invalid, removed from the URL and never sent', async () => {
    const t = setup();
    await t.flow.start('#token=roto');
    assert.deepEqual(t.calls, ['clearToken']);
    assert.deepEqual(t.flow.state, { kind: 'invalid' });
  });

  const linkCases: [ApiError | Error, ResetState][] = [
    [apiError(404, 'PASSWORD_RESET_INVALID'), { kind: 'invalid' }],
    [apiError(410, 'PASSWORD_RESET_EXPIRED'), { kind: 'expired' }],
    [apiError(410, 'PASSWORD_RESET_USED'), { kind: 'used' }],
    [apiError(410, 'PASSWORD_RESET_REVOKED'), { kind: 'revoked' }],
    [new TypeError('Failed to fetch'), { kind: 'error', message: CONNECTION_ERROR }]
  ];
  for (const [error, expected] of linkCases) {
    it(`verify ${error instanceof ApiError ? error.code : 'network error'} → ${expected.kind}`, async () => {
      const t = setup(error);
      await t.flow.start(`#token=${TOKEN}`);
      assert.deepEqual(t.flow.state, expected);
    });
  }

  it('updating → done, with the token only in the body', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    await t.flow.submit(PASSWORD, PASSWORD);
    assert.deepEqual(t.states.slice(2).map((s) => s.kind), ['updating', 'done']);
    assert.deepEqual(t.sent, [{ token: TOKEN, password: PASSWORD, password_confirmation: PASSWORD }]);
  });

  it('password checks stay in the form and send nothing', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    await t.flow.submit('corta', 'otra');
    assert.deepEqual(t.flow.state.kind === 'ready' && t.flow.state.errors, {
      password: 'La contraseña debe tener entre 12 y 128 caracteres.',
      password_confirmation: 'La confirmación no coincide con la contraseña.'
    });
    await t.flow.submit(' '.repeat(12), ' '.repeat(12));
    assert.equal(t.flow.state.kind === 'ready' && t.flow.state.errors.password, 'La contraseña no puede estar formada solo por espacios.');
    assert.equal(t.calls.includes('confirm'), false);
  });

  it('blocks double submission and nothing is sent after success', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    await Promise.all([t.flow.submit(PASSWORD, PASSWORD), t.flow.submit(PASSWORD, PASSWORD)]);
    await t.flow.submit(PASSWORD, PASSWORD);
    assert.equal(t.calls.filter((c) => c === 'confirm').length, 1);
  });

  it('422 from the backend (e.g. equal to the email) goes to its field', async () => {
    const t = setup('ok', apiError(422, 'VALIDATION_ERROR', [{ field: 'password', message: 'La contraseña no puede ser igual al correo electrónico.' }]));
    await t.flow.start(`#token=${TOKEN}`);
    await t.flow.submit(PASSWORD, PASSWORD);
    assert.deepEqual(t.flow.state, {
      kind: 'ready', preview: PREVIEW, errors: { password: 'La contraseña no puede ser igual al correo electrónico.' }, alert: null
    });
  });

  it('410 while updating: the link state, and no further submissions', async () => {
    for (const [code, kind] of [['PASSWORD_RESET_USED', 'used'], ['PASSWORD_RESET_EXPIRED', 'expired'], ['PASSWORD_RESET_REVOKED', 'revoked']] as const) {
      const t = setup('ok', apiError(410, code));
      await t.flow.start(`#token=${TOKEN}`);
      await t.flow.submit(PASSWORD, PASSWORD);
      assert.equal(t.flow.state.kind, kind);
      await t.flow.submit(PASSWORD, PASSWORD);
      assert.equal(t.calls.filter((c) => c === 'confirm').length, 1);
    }
  });

  it('network failure: back to the form, can retry', async () => {
    const t = setup('ok', new TypeError('Failed to fetch'), 'ok');
    await t.flow.start(`#token=${TOKEN}`);
    await t.flow.submit(PASSWORD, PASSWORD);
    assert.deepEqual(t.flow.state, { kind: 'ready', preview: PREVIEW, errors: {}, alert: CONNECTION_ERROR });
    await t.flow.submit(PASSWORD, PASSWORD);
    assert.equal(t.flow.state.kind, 'done');
  });

  it('never keeps the token or the password in a rendered state', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    await t.flow.submit(PASSWORD, PASSWORD);
    const rendered = JSON.stringify(t.states);
    assert.equal(rendered.includes(TOKEN) || rendered.includes(PASSWORD), false);
  });

  it('uses no browser storage', () => {
    // The flows run in Node, where Web Storage does not exist: any use would throw.
    assert.equal(typeof (globalThis as { localStorage?: unknown }).localStorage, 'undefined');
    assert.equal(typeof (globalThis as { sessionStorage?: unknown }).sessionStorage, 'undefined');
  });
});
