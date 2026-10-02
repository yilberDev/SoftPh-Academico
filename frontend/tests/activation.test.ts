/**
 * HU-019: /activate-account flow (states, token handling, validation and API errors).
 * Run with `npm test` (node:test, native TypeScript in Node 24; no extra dependencies).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { ActivationPayload, InvitationPreview } from '../src/lib/api/activation.ts';
import {
  CONNECTION_ERROR,
  createActivationFlow,
  readToken,
  validateActivationForm,
  type ActivationFormValues,
  type ActivationState
} from '../src/lib/residents/activation.ts';

const TOKEN = 'A'.repeat(20) + 'b_-'.repeat(7) + '12';
const PREVIEW: InvitationPreview = {
  email: 'residente@example.com',
  full_name: 'Laura Gómez',
  unit_code: 'B4-TO2-APTO-101',
  expires_at: '2026-10-08T20:05:00Z'
};
const VALUES: ActivationFormValues = {
  documentNumber: ' cc1234567 ',
  phone: '+57 300-123-4567',
  password: 'una clave segura 2026',
  passwordConfirmation: 'una clave segura 2026'
};

function apiError(status: number, code: string, details: { field?: string; message: string }[] = []): ApiError {
  return new ApiError(status, { error: { code, message: `Mensaje ${code}.`, details } });
}

type Answer = 'ok' | ApiError | Error;

/** A flow whose verify/activate answer with the given results, recording every call. */
function setup(verifyAnswer: Answer = 'ok', ...activateAnswers: Answer[]) {
  const calls: string[] = [];
  const states: ActivationState[] = [];
  const sent: ActivationPayload[] = [];
  const flow = createActivationFlow({
    verify: async (token) => {
      calls.push(`verify:${token}`);
      if (verifyAnswer !== 'ok') throw verifyAnswer;
      return PREVIEW;
    },
    activate: async (payload) => {
      calls.push('activate');
      sent.push(payload);
      const answer = activateAnswers.shift() ?? 'ok';
      await new Promise((resolve) => setTimeout(resolve, 0)); // a real request takes time
      if (answer !== 'ok') throw answer;
      return { status: 'ACTIVATED' };
    },
    clearToken: () => calls.push('clearToken'),
    render: (state) => states.push(state)
  });
  return { flow, calls, states, sent };
}

describe('token from the URL fragment', () => {
  it('reads only #token=... with the right shape', () => {
    assert.equal(readToken(`#token=${TOKEN}`), TOKEN);
    for (const hash of ['', '#', '#token=', '#token=abc', `#token=${TOKEN}x`, `#tok=${TOKEN}`, `#token=${'!'.repeat(43)}`]) {
      assert.equal(readToken(hash), null, hash);
    }
  });

  it('removes the token from the address bar before calling the backend', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    assert.deepEqual(t.calls, ['clearToken', `verify:${TOKEN}`]);
  });

  it('removes it even when the link is malformed, and does not call the backend', async () => {
    const t = setup();
    await t.flow.start('#token=roto');
    assert.deepEqual(t.calls, ['clearToken']);
    assert.deepEqual(t.flow.state, { kind: 'invalid' });
  });
});

describe('verification states', () => {
  it('verifying → ready with the invitation data', async () => {
    const t = setup();
    await t.flow.start(`#token=${TOKEN}`);
    assert.deepEqual(t.states, [
      { kind: 'verifying' },
      { kind: 'ready', invitation: PREVIEW, errors: {}, alert: null }
    ]);
  });

  const cases: [ApiError | Error, ActivationState][] = [
    [apiError(404, 'INVITATION_INVALID'), { kind: 'invalid' }],
    [apiError(410, 'INVITATION_EXPIRED'), { kind: 'expired' }],
    [apiError(410, 'INVITATION_USED'), { kind: 'used' }],
    [apiError(403, 'CSRF_FAILED'), { kind: 'error', message: 'Mensaje CSRF_FAILED.' }],
    [new TypeError('Failed to fetch'), { kind: 'error', message: CONNECTION_ERROR }]
  ];
  for (const [error, expected] of cases) {
    it(`${error instanceof ApiError ? error.code : 'network'} → ${expected.kind}`, async () => {
      const t = setup(error);
      await t.flow.start(`#token=${TOKEN}`);
      assert.deepEqual(t.flow.state, expected);
    });
  }
});

describe('activation', () => {
  async function ready(...answers: Answer[]) {
    const t = setup('ok', ...answers);
    await t.flow.start(`#token=${TOKEN}`);
    return t;
  }

  it('activating → activated, sending the token only in the body', async () => {
    const t = await ready('ok');
    await t.flow.submit(VALUES);
    assert.deepEqual(
      t.states.slice(2).map((s) => s.kind),
      ['activating', 'activated']
    );
    assert.deepEqual(t.sent, [
      {
        token: TOKEN,
        document_number: 'cc1234567',
        phone: '+573001234567',
        password: 'una clave segura 2026',
        password_confirmation: 'una clave segura 2026'
      }
    ]);
  });

  it('phone is optional', async () => {
    const t = await ready('ok');
    await t.flow.submit({ ...VALUES, phone: '  ' });
    assert.equal(t.sent[0]?.phone, null);
  });

  it('blocks a double submission', async () => {
    const t = await ready('ok');
    await Promise.all([t.flow.submit(VALUES), t.flow.submit(VALUES), t.flow.submit(VALUES)]);
    assert.equal(t.calls.filter((c) => c === 'activate').length, 1);
    await t.flow.submit(VALUES); // after activation: nothing else is sent
    assert.equal(t.calls.filter((c) => c === 'activate').length, 1);
  });

  it('format errors stay in the form and send nothing', async () => {
    const t = await ready();
    await t.flow.submit({ documentNumber: '', phone: '12', password: 'corta', passwordConfirmation: 'otra' });
    assert.equal(t.calls.includes('activate'), false);
    assert.deepEqual(t.flow.state.kind === 'ready' && t.flow.state.errors, {
      document_number: 'Ingresa tu número de documento.',
      phone: 'El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional.',
      password: 'La contraseña debe tener entre 12 y 128 caracteres.',
      password_confirmation: 'La confirmación no coincide con la contraseña.'
    });
  });

  it('a different confirmation is reported on its field', () => {
    const { errors } = validateActivationForm({ ...VALUES, passwordConfirmation: 'una clave segura 2027' });
    assert.deepEqual(errors, { password_confirmation: 'La confirmación no coincide con la contraseña.' });
  });

  it('password limits and blank passwords', () => {
    assert.equal(validateActivationForm({ ...VALUES, password: 'a'.repeat(12), passwordConfirmation: 'a'.repeat(12) }).payload !== null, true);
    assert.equal(validateActivationForm({ ...VALUES, password: 'a'.repeat(128), passwordConfirmation: 'a'.repeat(128) }).payload !== null, true);
    assert.equal(
      validateActivationForm({ ...VALUES, password: 'a'.repeat(129), passwordConfirmation: 'a'.repeat(129) }).errors.password,
      'La contraseña debe tener entre 12 y 128 caracteres.'
    );
    assert.equal(
      validateActivationForm({ ...VALUES, password: ' '.repeat(12), passwordConfirmation: ' '.repeat(12) }).errors.password,
      'La contraseña no puede estar formada solo por espacios.'
    );
  });

  it('422 from the backend: errors on their fields', async () => {
    const t = await ready(apiError(422, 'VALIDATION_ERROR', [{ field: 'password', message: 'La contraseña no puede ser igual al correo electrónico.' }]));
    await t.flow.submit(VALUES);
    assert.deepEqual(t.flow.state, {
      kind: 'ready',
      invitation: PREVIEW,
      errors: { password: 'La contraseña no puede ser igual al correo electrónico.' },
      alert: null
    });
  });

  it('409 DOCUMENT_ALREADY_REGISTERED: on the document field, the form can be sent again', async () => {
    const t = await ready(apiError(409, 'DOCUMENT_ALREADY_REGISTERED'), 'ok');
    await t.flow.submit(VALUES);
    assert.deepEqual(t.flow.state.kind === 'ready' && t.flow.state.errors, { document_number: 'Mensaje DOCUMENT_ALREADY_REGISTERED.' });
    await t.flow.submit({ ...VALUES, documentNumber: 'CC9999999' });
    assert.equal(t.flow.state.kind, 'activated');
  });

  it('409 UNIT_NOT_AVAILABLE: error state', async () => {
    const t = await ready(apiError(409, 'UNIT_NOT_AVAILABLE'));
    await t.flow.submit(VALUES);
    assert.equal(t.flow.state.kind, 'error');
  });

  it('410 while activating: expired or used, and no further submissions', async () => {
    for (const [code, kind] of [['INVITATION_EXPIRED', 'expired'], ['INVITATION_USED', 'used']] as const) {
      const t = await ready(apiError(410, code));
      await t.flow.submit(VALUES);
      assert.equal(t.flow.state.kind, kind);
      await t.flow.submit(VALUES);
      assert.equal(t.calls.filter((c) => c === 'activate').length, 1);
    }
  });

  it('404 while activating (link rotated by a resend): invalid', async () => {
    const t = await ready(apiError(404, 'INVITATION_INVALID'));
    await t.flow.submit(VALUES);
    assert.deepEqual(t.flow.state, { kind: 'invalid' });
  });

  it('network failure: back to the form with a message, can retry', async () => {
    const t = await ready(new TypeError('Failed to fetch'), 'ok');
    await t.flow.submit(VALUES);
    assert.deepEqual(t.flow.state, { kind: 'ready', invitation: PREVIEW, errors: {}, alert: CONNECTION_ERROR });
    await t.flow.submit(VALUES);
    assert.equal(t.flow.state.kind, 'activated');
  });

  it('never exposes the token in any rendered state', async () => {
    const t = await ready('ok');
    await t.flow.submit(VALUES);
    assert.equal(JSON.stringify(t.states).includes(TOKEN), false);
  });
});
