import { createServerList, sortedBy as sortedByField, type ListState as ServerListState } from '../lists/server-list.ts';
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
  return sortedByField<ResidentSortField, ResidentQuery>(query, field);
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

export type ListState = ServerListState<ResidentQuery, ResidentPage>;

interface ListDeps {
  load: (queryString: string) => Promise<ResidentPage>;
  render: (state: ListState) => void;
  /** Keeps the options in the page URL (history.replaceState). */
  remember: (queryString: string) => void;
  /** Used when the session ended (401): back to the login page. */
  navigate: (path: string) => void;
  schedule?: (callback: () => void, ms: number) => () => void;
}

/** The residents list on the shared server-side list controller (lib/lists/server-list.ts). */
export function createResidentsList(initial: ResidentQuery, deps: ListDeps) {
  const list = createServerList<ResidentSortField, ResidentQuery, ResidentPage>(
    initial,
    {
      toQueryString,
      isFiltered,
      pageSizes: PAGE_SIZES,
      defaultPageSize: DEFAULT_PAGE_SIZE,
      pageMax: PAGE_MAX,
      searchMaxLength: SEARCH_MAX_LENGTH,
      debounceMs: SEARCH_DEBOUNCE_MS,
      forbiddenMessage: FORBIDDEN_MESSAGE,
      connectionError: CONNECTION_ERROR
    },
    deps
  );
  return {
    get query(): ResidentQuery {
      return list.query;
    },
    start: list.start,
    /** Debounced: one request after the user stops typing. */
    search: list.search,
    filter: (status: ResidentStatus | ''): Promise<void> => list.update({ status }),
    sort: (field: ResidentSortField): Promise<void> => list.sort(field),
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    /** Same options again: retry after an error, or show the latest data (criterion 4). */
    refresh: list.refresh
  };
}
