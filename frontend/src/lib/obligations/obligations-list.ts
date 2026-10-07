import type { Charge, ChargeConcept, ChargePage, ChargeSortField, ChargeStatus } from '../api/charges.ts';
import { createServerList, type ListState, type SortOrder } from '../lists/server-list.ts';
import { formatCop, formatDate, isIsoDate } from '../payments/payment-form.ts';
import { CONCEPTS, STATUSES, STATUS_LABELS, conceptText, formatPeriod, isPeriod } from './charge-format.ts';

/**
 * Obligations extension: obligations list (pure, tested with node --test).
 *
 * Filters (block, tower, apartment, concept, status, period, due date range),
 * sort and pagination run in the backend, through server-list.ts; the options
 * live in the page URL. Status, applied and outstanding are shown exactly as
 * the API sends them. There is no free search in this list.
 */

export const SORT_FIELDS: readonly ChargeSortField[] = ['due_date', 'unit', 'concept', 'amount', 'outstanding', 'status', 'created'];
export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Consultando obligaciones...';
export const EMPTY_MESSAGE = 'No hay obligaciones registradas.';
export const NO_RESULTS_MESSAGE = 'No se encontraron obligaciones con los filtros seleccionados.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para ver las obligaciones.';
export const CONNECTION_ERROR = 'No fue posible consultar las obligaciones. Intenta de nuevo.';

export interface ChargeQuery {
  page: number;
  pageSize: number;
  /** Required by server-list.ts; always empty (no free search here). */
  search: '';
  blockId: number | null;
  towerId: number | null;
  unitId: number | null;
  concept: ChargeConcept | '';
  status: ChargeStatus | '';
  period: string; // YYYY-MM
  dueFrom: string;
  dueTo: string;
  sortBy: ChargeSortField;
  sortOrder: SortOrder;
}

/** The API default order: nearest due date first. */
export const DEFAULT_QUERY: ChargeQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  blockId: null,
  towerId: null,
  unitId: null,
  concept: '',
  status: '',
  period: '',
  dueFrom: '',
  dueTo: '',
  sortBy: 'due_date',
  sortOrder: 'asc'
};

export type ChargeFilters = Pick<ChargeQuery, 'blockId' | 'towerId' | 'unitId' | 'concept' | 'status' | 'period' | 'dueFrom' | 'dueTo'>;

export const NO_FILTERS: ChargeFilters = {
  blockId: null,
  towerId: null,
  unitId: null,
  concept: '',
  status: '',
  period: '',
  dueFrom: '',
  dueTo: ''
};

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

function idParam(value: string | null): number | null {
  if (value === null || !/^[1-9]\d{0,15}$/.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : null;
}

/** Options from the page URL; anything unknown or invalid falls back to the default. */
export function parseQuery(params: URLSearchParams): ChargeQuery {
  const sortBy = params.get('sort_by');
  const sortOrder = params.get('sort_order');
  const pageSize = positiveInt(params.get('page_size'), 100);
  const concept = params.get('concept') ?? '';
  const status = params.get('status') ?? '';
  const period = params.get('billing_period') ?? '';
  const date = (name: string) => {
    const value = params.get(name) ?? '';
    return isIsoDate(value) ? value : '';
  };
  let dueFrom = date('due_from');
  let dueTo = date('due_to');
  if (dueFrom && dueTo && dueFrom > dueTo) [dueFrom, dueTo] = ['', ''];
  // A lower level only counts together with its parents.
  const blockId = idParam(params.get('block_id'));
  const towerId = blockId === null ? null : idParam(params.get('tower_id'));
  const unitId = towerId === null ? null : idParam(params.get('unit_id'));
  return {
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
    search: '',
    blockId,
    towerId,
    unitId,
    concept: (CONCEPTS as readonly string[]).includes(concept) ? (concept as ChargeConcept) : '',
    status: (STATUSES as readonly string[]).includes(status) ? (status as ChargeStatus) : '',
    period: isPeriod(period) ? period : '',
    dueFrom,
    dueTo,
    sortBy: (SORT_FIELDS as readonly string[]).includes(sortBy ?? '') ? (sortBy as ChargeSortField) : DEFAULT_QUERY.sortBy,
    sortOrder: sortOrder === 'asc' ? 'asc' : sortOrder === 'desc' ? 'desc' : DEFAULT_QUERY.sortOrder
  };
}

/** Query string for the API and the page URL (defaults are left out). */
export function toQueryString(query: ChargeQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  if (query.blockId !== null) params.set('block_id', String(query.blockId));
  if (query.towerId !== null) params.set('tower_id', String(query.towerId));
  if (query.unitId !== null) params.set('unit_id', String(query.unitId));
  if (query.concept) params.set('concept', query.concept);
  if (query.status) params.set('status', query.status);
  if (query.period) params.set('billing_period', query.period);
  if (query.dueFrom) params.set('due_from', query.dueFrom);
  if (query.dueTo) params.set('due_to', query.dueTo);
  if (query.sortBy !== DEFAULT_QUERY.sortBy) params.set('sort_by', query.sortBy);
  if (query.sortOrder !== DEFAULT_QUERY.sortOrder) params.set('sort_order', query.sortOrder);
  return params.toString();
}

/** How many filters are applied (shown next to "Limpiar filtros"). */
export function activeFilterCount(query: ChargeFilters): number {
  const unit = query.unitId !== null || query.towerId !== null || query.blockId !== null ? 1 : 0;
  const dates = query.dueFrom || query.dueTo ? 1 : 0;
  return unit + dates + [query.concept, query.status, query.period].filter(Boolean).length;
}

export function isFiltered(query: ChargeQuery): boolean {
  return activeFilterCount(query) > 0;
}

export function filtersText(count: number): string {
  return count === 0 ? 'Sin filtros' : count === 1 ? '1 filtro aplicado' : `${count} filtros aplicados`;
}

/** A due date range the API accepts: the end is never before the start. */
export function dateRangeError(dueFrom: string, dueTo: string): string | null {
  return dueFrom && dueTo && dueFrom > dueTo ? 'La fecha final no puede ser anterior a la inicial.' : null;
}

/** "Mostrando 11–20 de 42 obligaciones" */
export function rangeText(page: ChargePage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} ${page.total === 1 ? 'obligación' : 'obligaciones'}`;
}

// --- Rows ---------------------------------------------------------------------------------------------

/** Columns of the table; `field` is the API sort field. */
export const COLUMNS: readonly { field: ChargeSortField | null; label: string; numeric: boolean }[] = [
  { field: 'unit', label: 'Unidad', numeric: false },
  { field: 'concept', label: 'Concepto', numeric: false },
  { field: null, label: 'Periodo', numeric: false },
  { field: 'due_date', label: 'Vence', numeric: false },
  { field: 'amount', label: 'Valor', numeric: true },
  { field: null, label: 'Aplicado', numeric: true },
  { field: 'outstanding', label: 'Saldo', numeric: true },
  { field: 'status', label: 'Estado', numeric: false }
];

/** The extra order the table header does not show. */
export const EXTRA_SORTS: readonly { field: ChargeSortField; label: string }[] = [{ field: 'created', label: 'Fecha de creación' }];

export interface ChargeCell {
  /** The column label: on narrow screens each row stacks and shows it next to the value. */
  label: string;
  text: string;
  /** Second line (the apartment was deleted). */
  note: string | null;
  numeric: boolean;
}

/** The 8 cells of a row, as the API sent them (nothing is recalculated). */
export function chargeCells(charge: Charge): ChargeCell[] {
  const values: [string, string | null][] = [
    [charge.unit_label, charge.unit_deleted ? 'Apartamento eliminado' : null],
    [conceptText(charge.concept, charge.description), null],
    [charge.billing_period ? formatPeriod(charge.billing_period) : '—', null],
    [formatDate(charge.due_date), null],
    [formatCop(charge.amount), null],
    [formatCop(charge.applied), null],
    [formatCop(charge.outstanding), null],
    [STATUS_LABELS[charge.status], null]
  ];
  return values.map(([text, note], index) => ({ label: COLUMNS[index].label, text, note, numeric: COLUMNS[index].numeric }));
}

/** Amounts and dates start with the largest/newest first; the others with the smallest/first. */
export function firstOrderOf(field: ChargeSortField): SortOrder {
  return field === 'amount' || field === 'outstanding' || field === 'created' ? 'desc' : 'asc';
}

export type ObligationsListState = ListState<ChargeQuery, ChargePage>;

export function createObligationsList(
  initial: ChargeQuery,
  deps: {
    load: (queryString: string) => Promise<ChargePage>;
    render: (state: ObligationsListState) => void;
    remember: (queryString: string) => void;
    navigate: (path: string) => void;
  }
) {
  const list = createServerList<ChargeSortField, ChargeQuery, ChargePage>(
    initial,
    {
      toQueryString,
      isFiltered,
      pageSizes: PAGE_SIZES,
      defaultPageSize: DEFAULT_PAGE_SIZE,
      pageMax: PAGE_MAX,
      searchMaxLength: 0,
      debounceMs: 0,
      forbiddenMessage: FORBIDDEN_MESSAGE,
      connectionError: CONNECTION_ERROR
    },
    deps
  );
  return {
    get query(): ChargeQuery {
      return list.query;
    },
    start: list.start,
    /** Any filter: back to page 1. */
    filter: (changes: Partial<ChargeFilters>): Promise<void> => list.update(changes),
    clearFilters: (): Promise<void> => list.update(NO_FILTERS),
    /** Same column toggles the direction; back to page 1. */
    sort: (field: ChargeSortField): Promise<void> => list.sort(field, firstOrderOf(field)),
    /** A chosen field and direction (the order selector): back to page 1. */
    sortExactly: (sortBy: ChargeSortField, sortOrder: SortOrder): Promise<void> => list.update({ sortBy, sortOrder }),
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    /** Same options again: retry after an error, or show the data after a creation or a generation. */
    refresh: list.refresh
  };
}
