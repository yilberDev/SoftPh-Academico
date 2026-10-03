import type { PaymentPage, PaymentSortField } from '../api/payments.ts';
import { createServerList, type ListState, type SortOrder } from '../lists/server-list.ts';
import { isIsoDate } from './payment-form.ts';

/**
 * HU-004 criterion 4: payments summary (pure, tested with node --test).
 * Filters (resident, date range), sort and pagination run in the backend; the
 * options live in the page URL.
 */

export const SORT_FIELDS: readonly PaymentSortField[] = ['date', 'resident', 'amount', 'concept', 'method', 'status'];
export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
export const SEARCH_MAX_LENGTH = 100;
export const SEARCH_DEBOUNCE_MS = 300;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Consultando pagos...';
export const EMPTY_MESSAGE = 'No hay pagos registrados.';
export const NO_RESULTS_MESSAGE = 'No se encontraron pagos con los filtros seleccionados.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para ver los pagos.';
export const CONNECTION_ERROR = 'No fue posible consultar los pagos. Intenta de nuevo.';

export interface PaymentQuery {
  page: number;
  pageSize: number;
  /** Resident: name or document. */
  search: string;
  dateFrom: string;
  dateTo: string;
  sortBy: PaymentSortField;
  sortOrder: SortOrder;
}

export const DEFAULT_QUERY: PaymentQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  dateFrom: '',
  dateTo: '',
  sortBy: 'date',
  sortOrder: 'desc'
};

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

/** Options from the page URL; anything unknown or invalid falls back to the default. */
export function parseQuery(params: URLSearchParams): PaymentQuery {
  const sortBy = params.get('sort_by');
  const sortOrder = params.get('sort_order');
  const pageSize = positiveInt(params.get('page_size'), 100);
  const date = (name: string) => {
    const value = params.get(name) ?? '';
    return isIsoDate(value) ? value : '';
  };
  let dateFrom = date('date_from');
  let dateTo = date('date_to');
  if (dateFrom && dateTo && dateFrom > dateTo) [dateFrom, dateTo] = ['', ''];
  return {
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
    search: (params.get('search') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    dateFrom,
    dateTo,
    sortBy: (SORT_FIELDS as readonly string[]).includes(sortBy ?? '') ? (sortBy as PaymentSortField) : DEFAULT_QUERY.sortBy,
    sortOrder: sortOrder === 'asc' ? 'asc' : sortOrder === 'desc' ? 'desc' : DEFAULT_QUERY.sortOrder
  };
}

/** Query string for the API and the page URL (defaults are left out). */
export function toQueryString(query: PaymentQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  const search = query.search.trim();
  if (search) params.set('search', search);
  if (query.dateFrom) params.set('date_from', query.dateFrom);
  if (query.dateTo) params.set('date_to', query.dateTo);
  if (query.sortBy !== DEFAULT_QUERY.sortBy) params.set('sort_by', query.sortBy);
  if (query.sortOrder !== DEFAULT_QUERY.sortOrder) params.set('sort_order', query.sortOrder);
  return params.toString();
}

export function isFiltered(query: PaymentQuery): boolean {
  return query.search.trim() !== '' || query.dateFrom !== '' || query.dateTo !== '';
}

/** A date range the API accepts: the end is never before the start. */
export function dateRangeError(dateFrom: string, dateTo: string): string | null {
  return dateFrom && dateTo && dateFrom > dateTo ? 'La fecha final no puede ser anterior a la inicial.' : null;
}

/** "Mostrando 11–20 de 42 pagos" */
export function rangeText(page: PaymentPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} ${page.total === 1 ? 'pago' : 'pagos'}`;
}

export type PaymentsListState = ListState<PaymentQuery, PaymentPage>;

export function createPaymentsList(
  initial: PaymentQuery,
  deps: {
    load: (queryString: string) => Promise<PaymentPage>;
    render: (state: PaymentsListState) => void;
    remember: (queryString: string) => void;
    navigate: (path: string) => void;
    schedule?: (callback: () => void, ms: number) => () => void;
  }
) {
  const list = createServerList<PaymentSortField, PaymentQuery, PaymentPage>(
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
    get query(): PaymentQuery {
      return list.query;
    },
    start: list.start,
    searchResident: list.search,
    setDates: (dateFrom: string, dateTo: string): Promise<void> => list.update({ dateFrom, dateTo }),
    /** Date and amount start with the newest/largest first; the others alphabetically. */
    sort: (field: PaymentSortField): Promise<void> => list.sort(field, field === 'date' || field === 'amount' ? 'desc' : 'asc'),
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    refresh: list.refresh
  };
}
