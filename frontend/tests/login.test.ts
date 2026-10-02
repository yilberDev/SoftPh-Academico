/**
 * HU-002 / HU-007: single login form and role pages.
 * Run with `npm test` (node:test, native TypeScript in Node 24; no extra dependencies).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { SessionInfo } from '../src/lib/api/auth.ts';
import { CONNECTION_ERROR, createLoginFlow, validateLogin, type LoginState } from '../src/lib/auth/login-form.ts';
import {
  FORGOT_PASSWORD_PATH,
  homePathForRole,
  isLoginPage,
  protectedRule,
  type UserRole
} from '../src/lib/auth/roles.ts';

const PASSWORD = 'una clave segura 2026';
const GENERIC = 'Correo electrónico o contraseña no válidos.';

function session(role: UserRole): SessionInfo {
  return { user: { id: 7, full_name: 'Laura Gómez', email: 'residente@example.com', role }, csrf_token: 'csrf' };
}

function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, { error: { code, message, details: [] } });
}

/** A login flow whose API answers with the given result. */
function setup(answer: SessionInfo | ApiError | Error) {
  const calls: { email: string; password: string }[] = [];
  const states: LoginState[] = [];
  const navigations: string[] = [];
  const flow = createLoginFlow({
    login: async (email, password) => {
      calls.push({ email, password });
      await new Promise((resolve) => setTimeout(resolve, 0)); // a real request takes time
      if (answer instanceof Error) throw answer;
      return answer;
    },
    navigate: (path) => navigations.push(path),
    render: (state) => states.push(state)
  });
  return { flow, calls, states, navigations };
}

describe('login form', () => {
  it('requires email and password, without calling the API', async () => {
    const t = setup(session('RESIDENTE'));
    await t.flow.submit('  ', '');
    assert.deepEqual(t.flow.state, {
      kind: 'idle',
      errors: { email: 'Ingresa tu correo electrónico.', password: 'Ingresa tu contraseña.' }
    });
    assert.equal(t.calls.length, 0);
    assert.deepEqual(validateLogin('a@b.co', 'x'), {});
  });

  it('resident: loading → success → resident panel', async () => {
    const t = setup(session('RESIDENTE'));
    await t.flow.submit('  residente@example.com ', PASSWORD);
    assert.deepEqual(t.states, [{ kind: 'loading' }, { kind: 'success', redirectTo: '/resident' }]);
    assert.deepEqual(t.navigations, ['/resident']);
    assert.deepEqual(t.calls, [{ email: 'residente@example.com', password: PASSWORD }]);
  });

  it('administrator: goes to the administration panel', async () => {
    const t = setup(session('ADMINISTRADOR'));
    await t.flow.submit('admin@example.com', PASSWORD);
    assert.deepEqual(t.navigations, ['/admin']);
  });

  it('the destination comes only from the role returned by the backend', async () => {
    // Whatever the user types, a RESIDENTE session never lands on /admin.
    const t = setup(session('RESIDENTE'));
    await t.flow.submit('admin@example.com', PASSWORD);
    assert.deepEqual(t.navigations, ['/resident']);
  });

  it('401: generic message, no navigation, can try again', async () => {
    const t = setup(apiError(401, 'INVALID_CREDENTIALS', GENERIC));
    await t.flow.submit('residente@example.com', 'mala');
    assert.deepEqual(t.flow.state, { kind: 'invalid', message: GENERIC });
    assert.deepEqual(t.navigations, []);
    await t.flow.submit('residente@example.com', 'mala');
    assert.equal(t.calls.length, 2);
  });

  it('429: too many attempts, with the message of the backend', async () => {
    const message = 'Demasiados intentos fallidos. El acceso está bloqueado temporalmente; intenta de nuevo en 15 minutos.';
    const t = setup(apiError(429, 'LOGIN_TEMPORARILY_LOCKED', message));
    await t.flow.submit('residente@example.com', PASSWORD);
    assert.deepEqual(t.flow.state, { kind: 'locked', message });
  });

  it('unexpected errors are shown without technical details', async () => {
    const network = setup(new TypeError('Failed to fetch'));
    await network.flow.submit('residente@example.com', PASSWORD);
    assert.deepEqual(network.flow.state, { kind: 'error', message: CONNECTION_ERROR });

    const forbidden = setup(apiError(403, 'CSRF_FAILED', 'No se pudo verificar la solicitud.'));
    await forbidden.flow.submit('residente@example.com', PASSWORD);
    assert.deepEqual(forbidden.flow.state, { kind: 'error', message: 'No se pudo verificar la solicitud.' });
  });

  it('blocks double submission while signing in and after success', async () => {
    const t = setup(session('RESIDENTE'));
    await Promise.all([t.flow.submit('residente@example.com', PASSWORD), t.flow.submit('residente@example.com', PASSWORD)]);
    await t.flow.submit('residente@example.com', PASSWORD);
    assert.equal(t.calls.length, 1);
  });

  it('never keeps the password in any state', async () => {
    for (const answer of [session('RESIDENTE'), apiError(401, 'INVALID_CREDENTIALS', GENERIC), new TypeError('x')]) {
      const t = setup(answer);
      await t.flow.submit('residente@example.com', PASSWORD);
      assert.equal(JSON.stringify(t.states).includes(PASSWORD), false);
    }
  });
});

describe('role pages', () => {
  it('home page of each role', () => {
    assert.equal(homePathForRole('ADMINISTRADOR'), '/admin');
    assert.equal(homePathForRole('RESIDENTE'), '/resident');
  });

  it('protected areas and their roles', () => {
    assert.deepEqual(protectedRule('/admin')?.roles, ['ADMINISTRADOR']);
    assert.deepEqual(protectedRule('/admin/blocks/3')?.roles, ['ADMINISTRADOR']);
    assert.deepEqual(protectedRule('/resident')?.roles, ['RESIDENTE']);
    assert.deepEqual(protectedRule('/resident/anything')?.roles, ['RESIDENTE']);
    for (const path of ['/', '/login', '/activate-account', '/forgot-password', '/administrator', '/residents']) {
      assert.equal(protectedRule(path), undefined, path);
    }
  });

  it('a resident is never allowed in the administration area (and vice versa)', () => {
    assert.equal(protectedRule('/admin')?.roles.includes('RESIDENTE'), false);
    assert.equal(protectedRule('/resident')?.roles.includes('ADMINISTRADOR'), false);
  });

  it('login page and recovery link', () => {
    assert.equal(isLoginPage('/login'), true);
    assert.equal(isLoginPage('/login/'), true);
    assert.equal(isLoginPage('/loginx'), false);
    assert.equal(FORGOT_PASSWORD_PATH, '/forgot-password');
  });
});
