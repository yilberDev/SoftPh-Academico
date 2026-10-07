import { ApiError } from '../api/errors.ts';
import type { ResidentPayment, ResidentPaymentPage, PaymentStatus } from '../api/resident-payments.ts';
import { createServerList, type ListState } from '../lists/server-list.ts';
import { fieldErrorsOf } from '../obligations/charge-format.ts';
import { dateRangeError, SEARCH_MAX_LENGTH } from '../payments/payments-list.ts';
import { isIsoDate } from '../payments/payment-form.ts';
import { METHOD_LABELS, STATUS_LABELS, formatCop, formatDate, isStatus, paymentConceptText } from './payment-format.ts';

/**
 * HU-008: "Mis pagos", the signed-in resident's payments (pure, tested with node --test).
 *
 * Search, filters, order (newest payment date first) and pagination run in the
 * backend, through server-list.ts; the options live in the page URL. The search
 * (a receipt number or a date) is read by the backend: no second set of rules
 * here. Nothing selects a person: the resident is the backend session's.
 */

export { dateRangeError, SEARCH_MAX_LENGTH };
export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Consultando tus pagos...';
export const EMPTY_MESSAGE = 'No tienes pagos registrados.';
export const NO_RESULTS_MESSAGE = 'No encontramos pagos con los filtros seleccionados.';
export const SESSION_EXPIRED_MESSAGE = 'Tu sesión ha expirado. Inicia sesión nuevamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para acceder a esta sección.';
export const CONNECTION_ERROR = 'No fue posible cargar tus pagos. Intenta nuevamente.';
export const INVALID_FILTERS_MESSAGE = 'Revisa los filtros señalados.';

export interface ResidentPaymentQuery {
  page: number;
  pageSize: number;
  /** A receipt number (RC-00000012 or 12) or a date (YYYY-MM-DD); read by the backend. */
  search: string;
  status: PaymentStatus | '';
  dateFrom: string;
  dateTo: string;
  /** Required by server-list.ts; the order is always the backend's (newest first). */
  sortBy: 'date';
  sortOrder: 'desc';
}

export const DEFAULT_QUERY: ResidentPaymentQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  sortBy: 'date',
  sortOrder: 'desc'
};

export type ResidentPaymentFilters = Pick<ResidentPaymentQuery, 'search' | 'status' | 'dateFrom' | 'dateTo'>;
export const NO_FILTERS: ResidentPaymentFilters = { search: '', status: '', dateFrom: '', dateTo: '' };

/** The filter fields, as the API names them (query.search, query.status, ...). */
export const FILTER_FIELDS = ['search', 'status', 'date_from', 'date_to'] as const;
export type FilterField = (typeof FILTER_FIELDS)[number];
export type FilterErrors = Partial<Record<FilterField, string>>;

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

/** Options from the page URL; anything unknown or invalid falls back to the default (ids are never read). */
export function parseQuery(params: URLSearchParams): ResidentPaymentQuery {
  const pageSize = positiveInt(params.get('page_size'), 100);
  const status = params.get('status') ?? '';
  const date = (name: string) => {
    const value = params.get(name) ?? '';
    return isIsoDate(value) ? value : '';
  };
  let dateFrom = date('date_from');
  let dateTo = date('date_to');
  if (dateFrom && dateTo && dateFrom > dateTo) [dateFrom, dateTo] = ['', ''];
  return {
    ...DEFAULT_QUERY,
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
    search: (params.get('search') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    status: isStatus(status) ? status : '',
    dateFrom,
    dateTo
  };
}

/** Query string for the API and the page URL: only the options with a value (defaults are left out). */
export function toQueryString(query: ResidentPaymentQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  if (query.status) params.set('status', query.status);
  if (query.dateFrom) params.set('date_from', query.dateFrom);
  if (query.dateTo) params.set('date_to', query.dateTo);
  const search = query.search.trim();
  if (search) params.set('search', search);
  return params.toString();
}

/** How many filters are applied (the date range counts once). */
export function activeFilterCount(query: ResidentPaymentFilters): number {
  const dates = query.dateFrom || query.dateTo ? 1 : 0;
  return dates + [query.search.trim(), query.status].filter(Boolean).length;
}

export function isFiltered(query: ResidentPaymentQuery): boolean {
  return activeFilterCount(query) > 0;
}

export function filtersText(count: number): string {
  return count === 0 ? 'Sin filtros' : count === 1 ? '1 filtro aplicado' : `${count} filtros aplicados`;
}

/** "Mostrando 11–20 de 42 pagos" */
export function rangeText(page: ResidentPaymentPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} ${page.total === 1 ? 'pago' : 'pagos'}`;
}

export function pageText(page: ResidentPaymentPage): string {
  return `Página ${page.page} de ${page.total_pages}`;
}

export function detailPath(id: number): string {
  return `/resident/payments/${id}`;
}

// --- Rows ---------------------------------------------------------------------------------------------

export const COLUMNS: readonly { label: string; numeric: boolean }[] = [
  { label: 'Fecha', numeric: false },
  { label: 'Concepto', numeric: false },
  { label: 'Monto', numeric: true },
  { label: 'Método', numeric: false },
  { label: 'Estado', numeric: false },
  { label: 'Recibo', numeric: false }
];

export interface PaymentRow {
  id: number;
  href: string;
  /** Accessible name of the "Ver detalle" link. */
  linkLabel: string;
  status: PaymentStatus;
  cells: { label: string; text: string; numeric: boolean }[];
}

/** The 6 cells of a row, as the API sent them (the notes are not in the list; nothing is recalculated). */
export function paymentRow(payment: ResidentPayment): PaymentRow {
  const date = formatDate(payment.payment_date);
  const texts = [
    date,
    paymentConceptText(payment.concept, payment.concept_description),
    formatCop(payment.amount),
    METHOD_LABELS[payment.method],
    STATUS_LABELS[payment.status],
    payment.receipt_number
  ];
  return {
    id: payment.id,
    href: detailPath(payment.id),
    linkLabel: `Ver detalle del pago ${payment.receipt_number} del ${date}`,
    status: payment.status,
    cells: texts.map((text, index) => ({ label: COLUMNS[index].label, text, numeric: COLUMNS[index].numeric }))
  };
}

// --- Controller -----------------------------------------------------------------------------------------

/** The list states of server-list.ts; an error also carries the 422 messages of each filter. */
export type ResidentPaymentsState =
  | Exclude<ListState<ResidentPaymentQuery, ResidentPaymentPage>, { kind: 'error' }>
  | { kind: 'error'; query: ResidentPaymentQuery; message: string; fieldErrors: FilterErrors; sessionExpired: boolean };

export function createResidentPaymentsList(
  initial: ResidentPaymentQuery,
  deps: {
    load: (queryString: string) => Promise<ResidentPaymentPage>;
    render: (state: ResidentPaymentsState) => void;
    remember: (queryString: string) => void;
  }
) {
  // The 422 details of the latest failed request (server-list.ts only shows the newest answer).
  let fieldErrors: FilterErrors = {};

  const list = createServerList<'date', ResidentPaymentQuery, ResidentPaymentPage>(
    initial,
    {
      toQueryString,
      isFiltered,
      pageSizes: PAGE_SIZES,
      defaultPageSize: DEFAULT_PAGE_SIZE,
      pageMax: PAGE_MAX,
      searchMaxLength: SEARCH_MAX_LENGTH,
      debounceMs: 0,
      forbiddenMessage: FORBIDDEN_MESSAGE,
      connectionError: CONNECTION_ERROR
    },
    {
      load: async (queryString) => {
        try {
          return await deps.load(queryString);
        } catch (error) {
          fieldErrors = error instanceof ApiError && error.status === 422 ? fieldErrorsOf(error, FILTER_FIELDS) : {};
          throw error;
        }
      },
      render: (state) => {
        if (state.kind !== 'error') {
          deps.render(state);
          return;
        }
        const hasFieldErrors = Object.keys(fieldErrors).length > 0;
        deps.render({ ...state, message: hasFieldErrors ? INVALID_FILTERS_MESSAGE : state.message, fieldErrors, sessionExpired: false });
      },
      remember: deps.remember,
      // 401: the session ended. The page says so and offers the login (nothing else is shown).
      navigate: () => deps.render({ kind: 'error', query: list.query, message: SESSION_EXPIRED_MESSAGE, fieldErrors: {}, sessionExpired: true })
    }
  );

  return {
    get query(): ResidentPaymentQuery {
      return list.query;
    },
    start: list.start,
    /** "Aplicar": every filter at once, back to page 1. */
    apply: (filters: ResidentPaymentFilters): Promise<void> =>
      list.update({ ...filters, search: filters.search.trim().slice(0, SEARCH_MAX_LENGTH) }),
    /** "Limpiar": no filters, back to page 1. */
    clear: (): Promise<void> => list.update(NO_FILTERS),
    /** Keeps the filters. */
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    /** Same options again: retry after an error. */
    refresh: list.refresh
  };
}
