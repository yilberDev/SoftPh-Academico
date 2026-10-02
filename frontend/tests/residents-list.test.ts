/**
 * HU-006: registered residents list (state, query string and API interaction).
 * Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { Resident, ResidentPage } from '../src/lib/api/residents.ts';
import { decidePageAccess } from '../src/lib/auth/page-access.ts';
import type { SessionInfo } from '../src/lib/api/auth.ts';
import type { UserRole } from '../src/lib/auth/roles.ts';
import {
  CONNECTION_ERROR,
  DEFAULT_QUERY,
  FORBIDDEN_MESSAGE,
  createResidentsList,
  parseQuery,
  rangeText,
  sortedBy,
  toQueryString,
  type ListState,
  type ResidentQuery
} from '../src/lib/residents/residents-list.ts';

function resident(id: number, name: string): Resident {
  return {
    id,
    full_name: name,
    document_number: `10000${id}`,
    phone: null,
    email: `r${id}@example.com`,
    unit: { id, label: `B1-TO1-APTO-${100 + id}` },
    status: 'ACTIVE'
  };
}

function pageOf(items: Resident[], page = 1, pageSize = 10, total = items.length): ResidentPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

const apiError = (status: number, message = 'x') =>
  new ApiError(status, { error: { code: 'X', message, details: [] } });

type Answer = ResidentPage | Error | ((queryString: string) => ResidentPage | Error);

/** A list whose API answers with `answer`; the debounce timer is fired by hand. */
function setup(answer: Answer, initial: ResidentQuery = DEFAULT_QUERY) {
  const requests: string[] = [];
  const states: ListState[] = [];
  const urls: string[] = [];
  const navigations: string[] = [];
  let timer: (() => void) | null = null;
  const list = createResidentsList(initial, {
    load: async (queryString) => {
      requests.push(queryString);
      const result = typeof answer === 'function' ? answer(queryString) : answer;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    remember: (queryString) => urls.push(queryString),
    navigate: (path) => navigations.push(path),
    schedule: (callback) => {
      timer = callback;
      return () => {
        timer = null;
      };
    }
  });
  const fireTimer = async () => {
    const callback = timer;
    timer = null;
    callback?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  return { list, requests, states, urls, navigations, fireTimer, hasTimer: () => timer !== null };
}

const kinds = (states: ListState[]) => states.map((state) => state.kind);

describe('query string (whitelist)', () => {
  it('defaults are left out', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
  });

  it('round trip of every option', () => {
    const query: ResidentQuery = { page: 3, pageSize: 25, search: 'Gómez', status: 'INACTIVE', sortBy: 'unit', sortOrder: 'desc' };
    const text = toQueryString(query);
    assert.equal(text, 'page=3&page_size=25&search=G%C3%B3mez&status=INACTIVE&sort_by=unit&sort_order=desc');
    assert.deepEqual(parseQuery(new URLSearchParams(text)), query);
  });

  it('unknown or invalid values fall back to the defaults', () => {
    const parsed = parseQuery(
      new URLSearchParams('page=-1&page_size=1000&status=PENDING_ACTIVATION&sort_by=password_hash&sort_order=DROP&role=ADMINISTRADOR')
    );
    assert.deepEqual(parsed, DEFAULT_QUERY);
    assert.deepEqual(parseQuery(new URLSearchParams('page=abc&page_size=7&sort_order=desc')), { ...DEFAULT_QUERY, sortOrder: 'desc' });
  });

  it('the search is trimmed and limited', () => {
    assert.equal(parseQuery(new URLSearchParams(`search=${'x'.repeat(150)}`)).search.length, 100);
    assert.equal(parseQuery(new URLSearchParams('search=%20%20laura%20')).search, 'laura');
  });
});

describe('sorting', () => {
  it('a new column starts ascending; the same column toggles; page goes back to 1', () => {
    const byUnit = sortedBy({ ...DEFAULT_QUERY, page: 4 }, 'unit');
    assert.deepEqual([byUnit.sortBy, byUnit.sortOrder, byUnit.page], ['unit', 'asc', 1]);
    const toggled = sortedBy(byUnit, 'unit');
    assert.equal(toggled.sortOrder, 'desc');
    assert.equal(sortedBy(toggled, 'unit').sortOrder, 'asc');
  });

  it('asks the backend for the sorted page', async () => {
    const { list, requests } = setup(pageOf([resident(1, 'Ana')]));
    await list.sort('document');
    await list.sort('document');
    assert.deepEqual(requests, ['sort_by=document', 'sort_by=document&sort_order=desc']);
  });
});

describe('states', () => {
  it('loading, then results', async () => {
    const { list, states } = setup(pageOf([resident(1, 'Ana'), resident(2, 'Beto')]));
    await list.start();
    assert.deepEqual(kinds(states), ['loading', 'results']);
    const last = states.at(-1)!;
    assert.equal(last.kind === 'results' && last.page.items.length, 2);
  });

  it('empty: there are no residents at all', async () => {
    const { list, states } = setup(pageOf([]));
    await list.start();
    assert.deepEqual(kinds(states), ['loading', 'empty']);
  });

  it('no results: nobody matches the search or the filter', async () => {
    const { list, states } = setup(pageOf([]), { ...DEFAULT_QUERY, search: 'zzz' });
    await list.start();
    assert.equal(states.at(-1)!.kind, 'no-results');
    const filtered = setup(pageOf([]), { ...DEFAULT_QUERY, status: 'INACTIVE' });
    await filtered.list.start();
    assert.equal(filtered.states.at(-1)!.kind, 'no-results');
  });

  it('range text', () => {
    assert.equal(rangeText(pageOf([resident(11, 'A'), resident(12, 'B')], 2, 10, 12)), 'Mostrando 11–12 de 12 residentes');
    assert.equal(rangeText(pageOf([resident(1, 'A')])), 'Mostrando 1–1 de 1 residente');
  });
});

describe('search (debounced, server-side)', () => {
  it('one request after the user stops typing, from page 1', async () => {
    const { list, requests, hasTimer, fireTimer } = setup(pageOf([resident(1, 'Laura')]), { ...DEFAULT_QUERY, page: 3 });
    list.search('L');
    list.search('La');
    list.search('Laura');
    assert.equal(requests.length, 0);
    assert.equal(hasTimer(), true);
    await fireTimer();
    assert.deepEqual(requests, ['search=Laura']);
  });

  it('the same text (or only spaces around it) does not ask again', async () => {
    const { list, requests, fireTimer } = setup(pageOf([resident(1, 'Laura')]), { ...DEFAULT_QUERY, search: 'Laura' });
    list.search(' Laura ');
    await fireTimer();
    assert.deepEqual(requests, []);
  });

  it('text typed right before changing the filter is not lost', async () => {
    const { list, requests, hasTimer } = setup(pageOf([resident(1, 'Laura')]));
    list.search('103');
    await list.filter('ACTIVE');
    assert.equal(hasTimer(), false);
    assert.deepEqual(requests, ['search=103&status=ACTIVE']);
  });

  it('an older answer never replaces a newer one', async () => {
    let release: (value: ResidentPage) => void = () => {};
    const slow = new Promise<ResidentPage>((resolve) => (release = resolve));
    const states: ListState[] = [];
    let calls = 0;
    const list = createResidentsList(DEFAULT_QUERY, {
      load: (queryString) => (++calls === 1 ? slow : Promise.resolve(pageOf([resident(2, `nuevo ${queryString}`)]))),
      render: (state) => states.push(state),
      remember: () => {},
      navigate: () => {}
    });
    const first = list.start();
    await list.filter('ACTIVE');
    release(pageOf([resident(1, 'viejo')]));
    await first;
    const last = states.at(-1)!;
    assert.equal(last.kind === 'results' && last.page.items[0].full_name, 'nuevo status=ACTIVE');
  });
});

describe('filter and pagination', () => {
  it('filter by status from page 1', async () => {
    const { list, requests } = setup(pageOf([resident(1, 'Ana')]), { ...DEFAULT_QUERY, page: 2 });
    await list.filter('INACTIVE');
    await list.filter('');
    assert.deepEqual(requests, ['status=INACTIVE', '']);
  });

  it('pages and page size', async () => {
    const { list, requests, urls } = setup(pageOf([resident(1, 'Ana')], 1, 10, 30));
    await list.goToPage(2);
    await list.goToPage(0);
    await list.setPageSize(25);
    await list.setPageSize(999);
    assert.deepEqual(requests, ['page=2', '', 'page_size=25', '']);
    assert.deepEqual(urls, requests); // the URL keeps the options
  });

  it('a page that no longer exists goes to the last one', async () => {
    const { list, requests, states } = setup((queryString) =>
      queryString === 'page=9' ? pageOf([], 9, 10, 12) : pageOf([resident(11, 'A'), resident(12, 'B')], 2, 10, 12)
    );
    await list.goToPage(9);
    assert.deepEqual(requests, ['page=9', 'page=2']);
    assert.equal(states.at(-1)!.kind, 'results');
  });
});

describe('errors', () => {
  it('401: the session ended, back to the login page', async () => {
    const { list, navigations, states } = setup(apiError(401));
    await list.start();
    assert.deepEqual(navigations, ['/login']);
    assert.deepEqual(kinds(states), ['loading']);
  });

  it('403: not allowed (another role)', async () => {
    const { list, states, navigations } = setup(apiError(403, 'No tienes permiso para realizar esta acción.'));
    await list.start();
    assert.deepEqual(states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: FORBIDDEN_MESSAGE });
    assert.deepEqual(navigations, []);
  });

  it('server or connection errors: generic message, and retry asks again', async () => {
    for (const failure of [apiError(500), new TypeError('fetch failed')]) {
      const { list, states, requests } = setup(failure);
      await list.start();
      assert.deepEqual(states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: CONNECTION_ERROR });
      await list.refresh();
      assert.equal(requests.length, 2);
    }
  });

  it('422 shows the message of the API', async () => {
    const { list, states } = setup(apiError(422, 'La búsqueda no puede superar 100 caracteres.'));
    await list.start();
    const last = states.at(-1)!;
    assert.equal(last.kind === 'error' && last.message, 'La búsqueda no puede superar 100 caracteres.');
  });
});

describe('navigation (HU-018 access decision)', () => {
  const session = (role: UserRole): SessionInfo => ({
    user: { id: 1, full_name: 'X', email: 'x@example.com', role },
    csrf_token: 'c'
  });
  it('/admin/residents: administrator only', async () => {
    assert.equal((await decidePageAccess('/admin/residents', async () => session('ADMINISTRADOR'))).kind, 'allowed');
    assert.equal((await decidePageAccess('/admin/residents', async () => session('RESIDENTE'))).kind, 'forbidden');
    assert.deepEqual(await decidePageAccess('/admin/residents', async () => null), { kind: 'redirect', location: '/login' });
  });
});
