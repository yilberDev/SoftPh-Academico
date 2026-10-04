import type { FeedbackPage, FeedbackSortField, FeedbackStatus } from '../api/feedback.ts';
import { createServerList, type ListState, type SortOrder } from '../lists/server-list.ts';

/**
 * HU-005 criterion 1: feedback list (pure, tested with node --test). Status
 * filter, search (resident or subject), sort and pagination run in the backend;
 * the options live in the page URL.
 */

export const STATUS_LABELS: Record<FeedbackStatus, string> = { PENDING: 'Pendiente', RESOLVED: 'Resuelto' };
export const SORT_FIELDS: readonly FeedbackSortField[] = ['created_at', 'resident', 'subject', 'status'];
export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
export const SEARCH_MAX_LENGTH = 100;
export const SEARCH_DEBOUNCE_MS = 300;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Cargando feedback...';
export const EMPTY_MESSAGE = 'No hay feedback registrados.';
export const NO_RESULTS_MESSAGE = 'No se encontraron registros con los filtros seleccionados.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para ver el feedback.';
export const CONNECTION_ERROR = 'No fue posible cargar el feedback. Intenta de nuevo.';
export const NO_SUBJECT = 'Sin asunto';

export interface FeedbackQuery {
  page: number;
  pageSize: number;
  search: string;
  status: FeedbackStatus | '';
  sortBy: FeedbackSortField;
  sortOrder: SortOrder;
}

export const DEFAULT_QUERY: FeedbackQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  status: '',
  sortBy: 'created_at',
  sortOrder: 'desc'
};

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

export function parseQuery(params: URLSearchParams): FeedbackQuery {
  const status = params.get('status');
  const sortBy = params.get('sort_by');
  const sortOrder = params.get('sort_order');
  const pageSize = positiveInt(params.get('page_size'), 100);
  return {
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
    search: (params.get('search') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    status: status === 'PENDING' || status === 'RESOLVED' ? status : '',
    sortBy: (SORT_FIELDS as readonly string[]).includes(sortBy ?? '') ? (sortBy as FeedbackSortField) : DEFAULT_QUERY.sortBy,
    sortOrder: sortOrder === 'asc' ? 'asc' : sortOrder === 'desc' ? 'desc' : DEFAULT_QUERY.sortOrder
  };
}

export function toQueryString(query: FeedbackQuery): string {
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

export function isFiltered(query: FeedbackQuery): boolean {
  return query.search.trim() !== '' || query.status !== '';
}

export function rangeText(page: FeedbackPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} ${page.total === 1 ? 'registro' : 'registros'}`;
}

export type FeedbackListState = ListState<FeedbackQuery, FeedbackPage>;

export function createFeedbackList(
  initial: FeedbackQuery,
  deps: {
    load: (queryString: string) => Promise<FeedbackPage>;
    render: (state: FeedbackListState) => void;
    remember: (queryString: string) => void;
    navigate: (path: string) => void;
    schedule?: (callback: () => void, ms: number) => () => void;
  }
) {
  const list = createServerList<FeedbackSortField, FeedbackQuery, FeedbackPage>(
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
    get query(): FeedbackQuery {
      return list.query;
    },
    start: list.start,
    search: list.search,
    filter: (status: FeedbackStatus | ''): Promise<void> => list.update({ status }),
    /** The date starts with the newest first; the others alphabetically. */
    sort: (field: FeedbackSortField): Promise<void> => list.sort(field, field === 'created_at' ? 'desc' : 'asc'),
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    refresh: list.refresh
  };
}
