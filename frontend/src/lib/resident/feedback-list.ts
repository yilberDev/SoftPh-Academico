import type { FeedbackStatus, ResidentFeedback, ResidentFeedbackPage } from '../api/resident-feedback.ts';
import { STATUS_LABELS, classificationOf, formatDateTime, subjectText } from '../feedback/feedback-format.ts';
import { createServerList, type ListState } from '../lists/server-list.ts';
import { detailPath } from './feedback-form.ts';

/**
 * HU-009: "Mis feedback", the signed-in resident's feedback (pure, tested with node --test).
 *
 * Order (newest first) and pagination run in the backend, through
 * server-list.ts; the page lives in the page URL. There is no search and no
 * filter (HU-009 asks for none). Nothing selects a person: the resident is the
 * backend session's.
 */

export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Consultando tus feedback...';
export const EMPTY_MESSAGE = 'No tienes feedback registrados.';
export const SESSION_EXPIRED_MESSAGE = 'Tu sesión ha expirado. Inicia sesión nuevamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para acceder a esta sección.';
export const CONNECTION_ERROR = 'No fue posible cargar tus feedback. Intenta nuevamente.';

export interface ResidentFeedbackQuery {
  page: number;
  pageSize: number;
  /** Required by server-list.ts; never sent (no search in HU-009). */
  search: '';
  /** Required by server-list.ts; the order is always the backend's (newest first). */
  sortBy: 'created_at';
  sortOrder: 'desc';
}

export const DEFAULT_QUERY: ResidentFeedbackQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  search: '',
  sortBy: 'created_at',
  sortOrder: 'desc'
};

function positiveInt(value: string | null, max: number): number | null {
  if (value === null || !/^[1-9]\d{0,5}$/.test(value)) return null;
  const number = Number(value);
  return number <= max ? number : null;
}

/** page and page_size from the page URL; anything else is ignored (the API would reject it). */
export function parseQuery(params: URLSearchParams): ResidentFeedbackQuery {
  const pageSize = positiveInt(params.get('page_size'), 100);
  return {
    ...DEFAULT_QUERY,
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE
  };
}

/** Query string for the API and the page URL: page and page_size only, defaults left out. */
export function toQueryString(query: ResidentFeedbackQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  return params.toString();
}

/** "Mostrando 11–20 de 42 feedback" */
export function rangeText(page: ResidentFeedbackPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} feedback`;
}

export function pageText(page: ResidentFeedbackPage): string {
  return `Página ${page.page} de ${page.total_pages}`;
}

// --- Rows ---------------------------------------------------------------------------------------------

export const COLUMNS = ['Fecha de envío', 'Asunto', 'Categoría', 'Urgencia', 'Estado'] as const;

export interface FeedbackRow {
  id: number;
  href: string;
  /** Accessible name of the "Ver detalle" link. */
  linkLabel: string;
  status: FeedbackStatus;
  cells: { label: (typeof COLUMNS)[number]; text: string; muted: boolean }[];
}

/** The cells of a row, as the API sent them (the message is only in the detail). */
export function feedbackRow(feedback: ResidentFeedback): FeedbackRow {
  const date = formatDateTime(feedback.created_at);
  const subject = subjectText(feedback.subject);
  const classification = classificationOf(feedback);
  const texts: [string, boolean][] = [
    [date, false],
    [subject, !feedback.subject],
    [classification.category, !classification.categoryRecorded],
    [classification.urgency, !classification.urgencyRecorded],
    [STATUS_LABELS[feedback.status], false]
  ];
  return {
    id: feedback.id,
    href: detailPath(feedback.id),
    linkLabel: `Ver detalle del feedback «${subject}» enviado el ${date}`,
    status: feedback.status,
    cells: texts.map(([text, muted], index) => ({ label: COLUMNS[index], text, muted }))
  };
}

// --- Controller -----------------------------------------------------------------------------------------

/** The list states of server-list.ts; a 401 says so and offers the login. */
export type ResidentFeedbackState =
  | Exclude<ListState<ResidentFeedbackQuery, ResidentFeedbackPage>, { kind: 'error' }>
  | { kind: 'error'; query: ResidentFeedbackQuery; message: string; sessionExpired: boolean };

export function createResidentFeedbackList(
  initial: ResidentFeedbackQuery,
  deps: {
    load: (queryString: string) => Promise<ResidentFeedbackPage>;
    render: (state: ResidentFeedbackState) => void;
    remember: (queryString: string) => void;
  }
) {
  const list = createServerList<'created_at', ResidentFeedbackQuery, ResidentFeedbackPage>(
    initial,
    {
      toQueryString,
      isFiltered: () => false, // no filters: zero rows is always "no feedback yet"
      pageSizes: PAGE_SIZES,
      defaultPageSize: DEFAULT_PAGE_SIZE,
      pageMax: PAGE_MAX,
      searchMaxLength: 0,
      debounceMs: 0,
      forbiddenMessage: FORBIDDEN_MESSAGE,
      connectionError: CONNECTION_ERROR
    },
    {
      load: deps.load,
      render: (state) =>
        deps.render(
          state.kind === 'error'
            ? // A 422 (an impossible page) is not shown as a technical text.
              { ...state, message: state.message === FORBIDDEN_MESSAGE ? FORBIDDEN_MESSAGE : CONNECTION_ERROR, sessionExpired: false }
            : state
        ),
      remember: deps.remember,
      // 401: the session ended. The page says so and offers the login (nothing else is shown).
      navigate: () => deps.render({ kind: 'error', query: list.query, message: SESSION_EXPIRED_MESSAGE, sessionExpired: true })
    }
  );

  return {
    get query(): ResidentFeedbackQuery {
      return list.query;
    },
    start: list.start,
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    /** Same page again: retry after an error, or show the latest data. */
    refresh: list.refresh
  };
}
