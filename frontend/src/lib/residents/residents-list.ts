import { ApiError } from '../api/errors.ts';
import type { ResidentPage, ResidentSortField, ResidentStatus, SortOrder } from '../api/residents.ts';

/**
 * HU-006: state of the registered residents list (pure, tested with node --test).
 *
 * Search, filter, sort and pagination are done by the backend: this module only
 * keeps the chosen options (always whitelisted), asks for the page and decides
 * what to show. The options live in the URL, so reloading or sharing it keeps them.
 */

export const STATUS_LABELS: Record<ResidentStatus, string> = { ACTIVE: 'Activo', INACTIVE: 'Inactivo' };
export const SORT_FIELDS: readonly ResidentSortField[] = ['name', 'document', 'unit', 'phone', 'email', 'status'];
export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
export const SEARCH_MAX_LENGTH = 100;
export const SEARCH_DEBOUNCE_MS = 300;
// Same technical limit as the API (PAGE_MAX).
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Cargando residentes…';
export const EMPTY_MESSAGE = 'Aún no hay residentes registrados.';
export const NO_RESULTS_MESSAGE = 'Ningún residente coincide con la búsqueda o el filtro.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para ver los residentes.';
export const CONNECTION_ERROR = 'No fue posible cargar los residentes. Intenta de nuevo.';

export interface ResidentQuery {
  page: number;
  pageSize: number;
  search: string;
  status: ResidentStatus | '';
  sortBy: ResidentSortField;
  sortOrder: SortOrder;
}

export const DEFAULT_QUERY: ResidentQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  status: '',
  sortBy: 'name',
  sortOrder: 'asc'
};

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

/** Options from the page URL; anything unknown or invalid falls back to the default. */
export function parseQuery(params: URLSearchParams): ResidentQuery {
  const status = params.get('status');
  const sortBy = params.get('sort_by');
  const sortOrder = params.get('sort_order');
  const pageSize = positiveInt(params.get('page_size'), 100);
  return {
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
    search: (params.get('search') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    status: status === 'ACTIVE' || status === 'INACTIVE' ? status : '',
    sortBy: (SORT_FIELDS as readonly string[]).includes(sortBy ?? '') ? (sortBy as ResidentSortField) : DEFAULT_QUERY.sortBy,
    sortOrder: sortOrder === 'desc' ? 'desc' : 'asc'
  };
}

/** Query string for the API and the page URL (defaults are left out). */
export function toQueryString(query: ResidentQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  const search = query.search.trim();
  if (search) params.set('search', search);
  if (query.status) params.set('status', query.status);
  if (query.sortBy !== DEFAULT_QUERY.sortBy) params.set('sort_by', query.sortBy);
  if (query.sortOrder !== DEFAULT_QUERY.sortOrder) params.set('sort_order', query.sortOrder);
  return params.toString();
}

/** Clicking a column: same column toggles the direction; another column starts ascending. */
export function sortedBy(query: ResidentQuery, field: ResidentSortField): ResidentQuery {
  const sortOrder: SortOrder = query.sortBy === field && query.sortOrder === 'asc' ? 'desc' : 'asc';
  return { ...query, sortBy: field, sortOrder, page: 1 };
}

export function isFiltered(query: ResidentQuery): boolean {
  return query.search.trim() !== '' || query.status !== '';
}

/** "Mostrando 11–20 de 42 residentes" */
export function rangeText(page: ResidentPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  const noun = page.total === 1 ? 'residente' : 'residentes';
  return `Mostrando ${first}–${last} de ${page.total} ${noun}`;
}

export type ListState =
  | { kind: 'loading'; query: ResidentQuery }
  | { kind: 'results'; query: ResidentQuery; page: ResidentPage }
  | { kind: 'empty'; query: ResidentQuery }
  | { kind: 'no-results'; query: ResidentQuery }
  | { kind: 'error'; query: ResidentQuery; message: string };

interface ListDeps {
  load: (queryString: string) => Promise<ResidentPage>;
  render: (state: ListState) => void;
  /** Keeps the options in the page URL (history.replaceState). */
  remember: (queryString: string) => void;
  /** Used when the session ended (401): back to the login page. */
  navigate: (path: string) => void;
  schedule?: (callback: () => void, ms: number) => () => void;
}

function defaultSchedule(callback: () => void, ms: number): () => void {
  const timer = setTimeout(callback, ms);
  return () => clearTimeout(timer);
}

export function createResidentsList(initial: ResidentQuery, deps: ListDeps) {
  const schedule = deps.schedule ?? defaultSchedule;
  let query = initial;
  let latest = 0;
  let cancelSearch: (() => void) | null = null;

  async function fetchPage(next: ResidentQuery): Promise<void> {
    query = next;
    const request = ++latest;
    const queryString = toQueryString(query);
    deps.remember(queryString);
    deps.render({ kind: 'loading', query });
    let page: ResidentPage;
    try {
      page = await deps.load(queryString);
    } catch (error) {
      if (request !== latest) return; // a newer request is on its way
      if (error instanceof ApiError && error.status === 401) {
        deps.navigate('/login');
        return;
      }
      const message =
        error instanceof ApiError && error.status === 403
          ? FORBIDDEN_MESSAGE
          : error instanceof ApiError && error.status === 422
            ? error.message
            : CONNECTION_ERROR;
      deps.render({ kind: 'error', query, message });
      return;
    }
    if (request !== latest) return; // an older answer never replaces a newer one
    if (page.total === 0) {
      deps.render({ kind: isFiltered(query) ? 'no-results' : 'empty', query });
    } else if (page.items.length === 0 && query.page > page.total_pages) {
      // The page no longer exists (fewer residents now): go to the last one.
      await fetchPage({ ...query, page: page.total_pages });
    } else {
      deps.render({ kind: 'results', query, page });
    }
  }

  /** Text typed but not searched yet: any other action searches it right away. */
  let draft: string | null = null;

  function withDraft(next: ResidentQuery): ResidentQuery {
    cancelSearch?.();
    cancelSearch = null;
    const typed = draft;
    draft = null;
    return typed !== null && typed.trim() !== next.search.trim() ? { ...next, search: typed, page: 1 } : next;
  }

  return {
    get query(): ResidentQuery {
      return query;
    },
    start: () => fetchPage(query),
    /** Debounced: one request after the user stops typing. */
    search(text: string): void {
      cancelSearch?.();
      draft = text.slice(0, SEARCH_MAX_LENGTH);
      cancelSearch = schedule(() => {
        cancelSearch = null;
        const next = withDraft(query);
        if (next !== query) void fetchPage(next);
      }, SEARCH_DEBOUNCE_MS);
    },
    filter(status: ResidentStatus | ''): Promise<void> {
      return fetchPage(withDraft({ ...query, status, page: 1 }));
    },
    sort(field: ResidentSortField): Promise<void> {
      return fetchPage(withDraft(sortedBy(query, field)));
    },
    goToPage(page: number): Promise<void> {
      return fetchPage(withDraft({ ...query, page: Math.min(Math.max(1, page), PAGE_MAX) }));
    },
    setPageSize(pageSize: number): Promise<void> {
      const size = (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE;
      return fetchPage(withDraft({ ...query, pageSize: size, page: 1 }));
    },
    /** Same options again: retry after an error, or show the latest data (criterion 4). */
    refresh: () => fetchPage(withDraft(query))
  };
}
