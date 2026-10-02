/**
 * HU-018: page access by role (decision of the SSR middleware) and its use of
 * the session endpoint. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { SessionInfo } from '../src/lib/api/auth.ts';
import { decidePageAccess, sessionOrNull, type PageAccess } from '../src/lib/auth/page-access.ts';
import type { UserRole } from '../src/lib/auth/roles.ts';

function session(role: UserRole): SessionInfo {
  return { user: { id: 7, full_name: 'Laura Gómez', email: 'laura@example.com', role }, csrf_token: 'csrf' };
}

/** A fake GET /auth/session: answers with the given result and counts the calls. */
function endpoint(answer: SessionInfo | ApiError | Error) {
  const calls = { count: 0 };
  const load = async (): Promise<SessionInfo> => {
    calls.count += 1;
    if (answer instanceof Error) throw answer;
    return answer;
  };
  // The middleware path: the endpoint's 401 means "no session".
  return { calls, loadSession: () => sessionOrNull(load) };
}

const unauthenticated = () => new ApiError(401, { error: { code: 'NOT_AUTHENTICATED', message: 'x', details: [] } });

async function access(path: string, answer: SessionInfo | ApiError | Error): Promise<PageAccess> {
  return decidePageAccess(path, endpoint(answer).loadSession);
}

const ADMIN_PAGES = ['/admin', '/admin/', '/admin/residents', '/admin/blocks', '/admin/blocks/3', '/admin/towers/9', '/admin/residents/invite'];
const RESIDENT_PAGES = ['/resident', '/resident/', '/resident/anything'];
const ALLOWED = (role: UserRole): PageAccess => ({ kind: 'allowed', session: session(role) });
const TO_LOGIN: PageAccess = { kind: 'redirect', location: '/login' };
const FORBIDDEN: PageAccess = { kind: 'forbidden' };

describe('role matrix of the protected pages', () => {
  for (const path of ADMIN_PAGES) {
    it(`${path}: administrator in, resident 403, no session → login`, async () => {
      assert.deepEqual(await access(path, session('ADMINISTRADOR')), ALLOWED('ADMINISTRADOR'));
      assert.deepEqual(await access(path, session('RESIDENTE')), FORBIDDEN);
      assert.deepEqual(await access(path, unauthenticated()), TO_LOGIN);
    });
  }

  for (const path of RESIDENT_PAGES) {
    it(`${path}: resident in, administrator 403, no session → login`, async () => {
      assert.deepEqual(await access(path, session('RESIDENTE')), ALLOWED('RESIDENTE'));
      assert.deepEqual(await access(path, session('ADMINISTRADOR')), FORBIDDEN);
      assert.deepEqual(await access(path, unauthenticated()), TO_LOGIN);
    });
  }

  it('a forbidden answer carries no session data', async () => {
    const decision = await access('/admin', session('RESIDENTE'));
    assert.deepEqual(Object.keys(decision), ['kind']);
  });

  it('an unknown role is allowed nowhere', async () => {
    const other = { ...session('RESIDENTE'), user: { ...session('RESIDENTE').user, role: 'SUPERADMIN' as UserRole } };
    for (const path of [...ADMIN_PAGES, ...RESIDENT_PAGES]) {
      assert.deepEqual(await access(path, other), FORBIDDEN, path);
    }
  });

  it('similar paths are not protected areas (no prefix confusion)', async () => {
    for (const path of ['/administrator', '/admins', '/residents', '/residential']) {
      const { calls, loadSession } = endpoint(session('RESIDENTE'));
      assert.deepEqual(await decidePageAccess(path, loadSession), { kind: 'public' }, path);
      assert.equal(calls.count, 0, path);
    }
  });
});

describe('login page', () => {
  it('a signed-in user goes to the home page of the role', async () => {
    assert.deepEqual(await access('/login', session('ADMINISTRADOR')), { kind: 'redirect', location: '/admin' });
    assert.deepEqual(await access('/login', session('RESIDENTE')), { kind: 'redirect', location: '/resident' });
  });

  it('without a session the login page is shown', async () => {
    assert.deepEqual(await access('/login', unauthenticated()), { kind: 'public' });
  });
});

describe('interaction with the session endpoint', () => {
  it('public pages never call it', async () => {
    for (const path of ['/', '/forgot-password', '/reset-password', '/activate-account', '/forbidden']) {
      const { calls, loadSession } = endpoint(session('ADMINISTRADOR'));
      assert.deepEqual(await decidePageAccess(path, loadSession), { kind: 'public' }, path);
      assert.equal(calls.count, 0, path);
    }
  });

  it('protected pages call it once per request (the backend decides every time)', async () => {
    const { calls, loadSession } = endpoint(session('ADMINISTRADOR'));
    await decidePageAccess('/admin', loadSession);
    await decidePageAccess('/admin/blocks', loadSession);
    assert.equal(calls.count, 2);
  });

  it('401 (no, expired or revoked session) means no session', async () => {
    assert.equal(await sessionOrNull(async () => { throw unauthenticated(); }), null);
  });

  it('other failures are not "no session": the page is not served', async () => {
    const failures = [
      new ApiError(500, null),
      new ApiError(503, null),
      new ApiError(403, { error: { code: 'FORBIDDEN', message: 'x', details: [] } }),
      new TypeError('fetch failed')
    ];
    for (const failure of failures) {
      await assert.rejects(sessionOrNull(async () => { throw failure; }));
      for (const path of ['/admin', '/resident', '/login']) {
        assert.deepEqual(await access(path, failure), { kind: 'unavailable' }, `${path} ${failure.message}`);
      }
    }
  });

  it('the role is the one answered by the backend', async () => {
    // The same browser request, two different backend answers: only the session decides.
    assert.equal((await access('/admin', session('ADMINISTRADOR'))).kind, 'allowed');
    assert.equal((await access('/admin', session('RESIDENTE'))).kind, 'forbidden');
  });
});
