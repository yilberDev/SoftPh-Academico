import type {
  NotificationEmailStatus,
  NotificationEvent,
  ResidentNotification,
  ResidentNotificationPage
} from '../api/notifications.ts';
import { formatDateTime } from '../feedback/feedback-format.ts';
import { createServerList, type ListState } from '../lists/server-list.ts';
import { detailPath as feedbackPath } from './feedback-form.ts';
import { detailPath as paymentPath } from './payments-list.ts';

/**
 * HU-017: "Notificaciones", the signed-in resident's notifications (pure, tested with node --test).
 *
 * Order (newest first) and pagination run in the backend, through
 * server-list.ts; the page lives in the page URL. Each notification says which
 * event originated it and links to its record (the resident's own payment or
 * feedback), and whether its email reached the registered address.
 */

export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;
const PAGE_MAX = 10_000;

export const LOADING_MESSAGE = 'Consultando tus notificaciones...';
export const EMPTY_MESSAGE = 'No tienes notificaciones. Aquí verás los pagos registrados y las respuestas a tus feedback.';
export const SESSION_EXPIRED_MESSAGE = 'Tu sesión ha expirado. Inicia sesión nuevamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para acceder a esta sección.';
export const CONNECTION_ERROR = 'No fue posible cargar tus notificaciones. Intenta nuevamente.';

export const EVENT_LABELS: Record<NotificationEvent, string> = {
  PAYMENT_REGISTERED: 'Pago registrado',
  FEEDBACK_RESPONDED: 'Respuesta a feedback',
  FEEDBACK_RESOLVED: 'Feedback resuelto'
};

/** CU-017 6, 2a and 4a: the result of the email, in the resident's words. */
export const EMAIL_STATUS_LABELS: Record<NotificationEmailStatus, string> = {
  SENT: 'Enviada a tu correo',
  PENDING: 'Envío por correo sin confirmar',
  FAILED: 'No se pudo enviar a tu correo',
  NO_CONTACT: 'Sin correo de contacto válido',
  NOT_REQUIRED: 'Solo en el panel (sin correo)'
};

export const EMAIL_STATUS_BADGES: Record<NotificationEmailStatus, string> = {
  SENT: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
  PENDING: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  FAILED: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  NO_CONTACT: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  NOT_REQUIRED: 'bg-slate-100 text-slate-700 ring-slate-500/20'
};

/** What the resident can do when the email did not arrive (the notification itself stays here). */
export const EMAIL_HELP: Partial<Record<NotificationEmailStatus, string>> = {
  FAILED: 'Si necesitas el comprobante o la respuesta por correo, comunícate con la administración.',
  NO_CONTACT: 'Comunícate con la administración para revisar tu correo electrónico registrado.'
};

export interface ResidentNotificationQuery {
  page: number;
  pageSize: number;
  /** Required by server-list.ts; never sent (no search in HU-017). */
  search: '';
  /** Required by server-list.ts; the order is always the backend's (newest first). */
  sortBy: 'created_at';
  sortOrder: 'desc';
}

export const DEFAULT_QUERY: ResidentNotificationQuery = {
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
export function parseQuery(params: URLSearchParams): ResidentNotificationQuery {
  const pageSize = positiveInt(params.get('page_size'), 100);
  return {
    ...DEFAULT_QUERY,
    page: positiveInt(params.get('page'), PAGE_MAX) ?? DEFAULT_QUERY.page,
    pageSize: pageSize !== null && (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE
  };
}

/** Query string for the API and the page URL: page and page_size only, defaults left out. */
export function toQueryString(query: ResidentNotificationQuery): string {
  const params = new URLSearchParams();
  if (query.page !== DEFAULT_QUERY.page) params.set('page', String(query.page));
  if (query.pageSize !== DEFAULT_QUERY.pageSize) params.set('page_size', String(query.pageSize));
  return params.toString();
}

/** "Mostrando 11–20 de 42 notificaciones" */
export function rangeText(page: ResidentNotificationPage): string {
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  return `Mostrando ${first}–${last} de ${page.total} ${page.total === 1 ? 'notificación' : 'notificaciones'}`;
}

export function pageText(page: ResidentNotificationPage): string {
  return `Página ${page.page} de ${page.total_pages}`;
}

// --- Items ----------------------------------------------------------------------------------------------

export interface NotificationView {
  id: number;
  event: string;
  title: string;
  message: string;
  date: string;
  emailStatus: NotificationEmailStatus;
  emailText: string;
  emailHelp: string | null;
  /** The record that originated it (HU-017 criterion 4.1). */
  href: string;
  linkText: string;
}

/** The resident's own payment (HU-008 detail) or feedback (HU-009 detail). */
export function recordLink(notification: Pick<ResidentNotification, 'entity_type' | 'entity_id'>): { href: string; text: string } {
  return notification.entity_type === 'payment'
    ? { href: paymentPath(notification.entity_id), text: 'Ver el pago' }
    : { href: feedbackPath(notification.entity_id), text: 'Ver el feedback' };
}

export function notificationView(notification: ResidentNotification): NotificationView {
  const link = recordLink(notification);
  return {
    id: notification.id,
    event: EVENT_LABELS[notification.event_type],
    title: notification.title,
    message: notification.message,
    date: formatDateTime(notification.created_at),
    emailStatus: notification.email_status,
    emailText: EMAIL_STATUS_LABELS[notification.email_status],
    emailHelp: EMAIL_HELP[notification.email_status] ?? null,
    href: link.href,
    linkText: link.text
  };
}

// --- Controller -----------------------------------------------------------------------------------------

/** The list states of server-list.ts; a 401 says so and offers the login. */
export type ResidentNotificationState =
  | Exclude<ListState<ResidentNotificationQuery, ResidentNotificationPage>, { kind: 'error' }>
  | { kind: 'error'; query: ResidentNotificationQuery; message: string; sessionExpired: boolean };

export function createResidentNotificationList(
  initial: ResidentNotificationQuery,
  deps: {
    load: (queryString: string) => Promise<ResidentNotificationPage>;
    render: (state: ResidentNotificationState) => void;
    remember: (queryString: string) => void;
  }
) {
  const list = createServerList<'created_at', ResidentNotificationQuery, ResidentNotificationPage>(
    initial,
    {
      toQueryString,
      isFiltered: () => false, // no filters: zero rows is always "no notifications yet"
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
    get query(): ResidentNotificationQuery {
      return list.query;
    },
    start: list.start,
    goToPage: list.goToPage,
    setPageSize: list.setPageSize,
    /** Same page again: retry after an error, or show the latest data. */
    refresh: list.refresh
  };
}
