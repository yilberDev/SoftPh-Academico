import type { AccountStatus } from '../api/account-statement.ts';
import type { ResidentNotification } from '../api/notifications.ts';
import type { ResidentFeedback } from '../api/resident-feedback.ts';
import type { IconName } from '../landing/icons.ts';
import { ACCOUNT_STATUS_HELP, ACCOUNT_STATUS_LABELS } from '../account-statement/statement-view.ts';
import { formatCop } from '../payments/payment-form.ts';
import { notificationView } from './notifications-list.ts';

/**
 * The resident's home page (/resident): its modules, its summary and its recent activity (pure, tested
 * with node --test).
 *
 * Every module points to a page that exists. The balance and the account status come as the backend
 * computed them in the resident's own statement (HU-014); nothing is added up here. The pending feedback
 * is counted only when the API returned every feedback of the resident; otherwise it is unavailable.
 * A number the API could not give is shown as "No disponible", never as 0 or as an estimate.
 */

export const STATEMENT_PATH = '/resident/account-statement';
export const FEEDBACK_PATH = '/resident/feedback';
export const NOTIFICATIONS_PATH = '/resident/notifications';

/** At most this many feedback are read to count the pending ones (the API's maximum page size). */
export const FEEDBACK_COUNT_PAGE_SIZE = 100;
/** The latest notifications shown as recent activity. */
export const ACTIVITY_SIZE = 5;

export interface ResidentModuleLink {
  label: string;
  href: string;
}

export interface ResidentModule {
  title: string;
  description: string;
  icon: IconName;
  /** One main link, named by the title; or, for feedback, its two actions (send, list). */
  href?: string;
  action?: string;
  links?: ResidentModuleLink[];
}

/** The modules of the panel, in order; the demo payment only while DEMO_PAYMENTS_ENABLED (HU-010). */
export function residentModules(demoPayments: boolean): ResidentModule[] {
  return [
    {
      title: 'Estado de cuenta',
      href: STATEMENT_PATH,
      description: 'Consulta las obligaciones de tu unidad residencial, tus pagos, el total pendiente y el estado de tu cuenta.',
      action: 'Ver mi estado',
      icon: 'fileText'
    },
    {
      title: 'Mis pagos',
      href: '/resident/payments',
      description: 'Consulta los pagos registrados en tu cuenta, su estado y su detalle.',
      action: 'Ver pagos',
      icon: 'receipt'
    },
    ...(demoPayments
      ? [
          {
            title: 'Pago de demostración',
            href: '/resident/demo-payments',
            description: 'Simula el pago de una obligación pendiente con la pasarela interna. Sin dinero real ni proveedores externos.',
            action: 'Simular un pago',
            icon: 'wallet' as const
          }
        ]
      : []),
    {
      title: 'Feedback',
      description: 'Comparte una queja, sugerencia o solicitud con la administración y consulta su estado.',
      icon: 'message',
      links: [
        { label: 'Enviar feedback', href: '/resident/feedback/new' },
        { label: 'Mis feedback', href: FEEDBACK_PATH }
      ]
    },
    {
      title: 'Notificaciones',
      href: NOTIFICATIONS_PATH,
      description: 'Consulta los avisos de tus pagos registrados y de las respuestas a tus feedback, y si llegaron a tu correo.',
      action: 'Ver avisos',
      icon: 'bell'
    }
  ];
}

// --- Summary --------------------------------------------------------------------------------------------

/** The resident's own statement, as far as the panel needs it; or why it is missing. */
export type StatementSource =
  | {
      kind: 'ok';
      unit: string;
      status: AccountStatus;
      pending_balance: string;
      pending_obligations: number;
    }
  /** ACCOUNT_STATEMENT_UNAVAILABLE: the resident has no current apartment. */
  | { kind: 'no-unit' }
  | { kind: 'error' };

export interface ResidentSummarySources {
  statement: StatementSource;
  /** GET /resident/feedback (one page of up to FEEDBACK_COUNT_PAGE_SIZE); null where the request failed. */
  feedback: { items: Pick<ResidentFeedback, 'status'>[]; total: number } | null;
}

export type SummaryTone = 'neutral' | 'attention' | 'danger';

export interface ResidentSummaryTile {
  key: 'balance' | 'account-status' | 'pending-feedback';
  label: string;
  /** The value to show, or null: "No disponible" (or the reason in the detail). */
  value: string | null;
  detail: string;
  icon: IconName;
  tone: SummaryTone;
  href: string;
}

export const UNAVAILABLE_VALUE = 'No disponible';
export const UNAVAILABLE_DETAIL = 'No fue posible consultar este dato.';
/** The backend's own words for ACCOUNT_STATEMENT_UNAVAILABLE. */
export const NO_UNIT_DETAIL = 'No tienes una unidad residencial vigente.';

const ACCOUNT_TONES: Record<AccountStatus, SummaryTone> = { UP_TO_DATE: 'neutral', PENDING: 'attention', OVERDUE: 'danger' };

export const obligationsWithBalance = (count: number): string =>
  count === 0 ? 'Sin obligaciones con saldo.' : `${count} ${count === 1 ? 'obligación' : 'obligaciones'} con saldo.`;

/** The feedback still pending, only when the page holds every feedback of the resident. */
export function pendingFeedback(feedback: ResidentSummarySources['feedback']): number | null {
  if (feedback === null || feedback.items.length < feedback.total) return null;
  return feedback.items.filter((item) => item.status === 'PENDING').length;
}

/** The three tiles of the summary, in order; each one honest about a value the API did not give. */
export function residentSummaryTiles({ statement, feedback }: ResidentSummarySources): ResidentSummaryTile[] {
  const missing = statement.kind === 'no-unit' ? NO_UNIT_DETAIL : UNAVAILABLE_DETAIL;
  const pending = pendingFeedback(feedback);
  return [
    {
      key: 'balance',
      label: 'Saldo pendiente',
      value: statement.kind === 'ok' ? formatCop(statement.pending_balance) : null,
      detail: statement.kind === 'ok' ? obligationsWithBalance(statement.pending_obligations) : missing,
      icon: 'barChart',
      tone: statement.kind === 'ok' && statement.pending_obligations > 0 ? 'attention' : 'neutral',
      href: STATEMENT_PATH
    },
    {
      key: 'account-status',
      label: 'Estado de la cuenta',
      value: statement.kind === 'ok' ? ACCOUNT_STATUS_LABELS[statement.status] : null,
      detail: statement.kind === 'ok' ? ACCOUNT_STATUS_HELP[statement.status] : missing,
      icon: 'shieldCheck',
      tone: statement.kind === 'ok' ? ACCOUNT_TONES[statement.status] : 'neutral',
      href: STATEMENT_PATH
    },
    {
      key: 'pending-feedback',
      label: 'Feedback pendiente',
      value: pending === null ? null : String(pending),
      detail:
        pending === null
          ? UNAVAILABLE_DETAIL
          : feedback!.total === 0
            ? 'Aún no has enviado feedback.'
            : `De ${feedback!.total} ${feedback!.total === 1 ? 'enviado' : 'enviados'}, sin resolver.`,
      icon: 'message',
      tone: pending ? 'attention' : 'neutral',
      href: FEEDBACK_PATH
    }
  ];
}

/** The unit in the welcome: its label, none, or nothing to say when the statement could not be read. */
export function unitText(statement: StatementSource): string | null {
  if (statement.kind === 'ok') return statement.unit;
  return statement.kind === 'no-unit' ? 'Sin unidad residencial vigente' : null;
}

// --- Recent activity ------------------------------------------------------------------------------------

export interface ActivityItem {
  id: number;
  event: string;
  title: string;
  message: string;
  date: string;
  icon: IconName;
  href: string;
  linkText: string;
}

export const ACTIVITY_EMPTY = 'Aún no tienes actividad. Aquí verás tus pagos registrados y las respuestas a tus feedback.';
export const ACTIVITY_ERROR = 'No fue posible consultar tu actividad reciente.';

/** The latest notifications (HU-017), as the notifications page words them. */
export function activityItems(notifications: readonly ResidentNotification[]): ActivityItem[] {
  return notifications.slice(0, ACTIVITY_SIZE).map((notification) => {
    const view = notificationView(notification);
    return {
      id: view.id,
      event: view.event,
      title: view.title,
      message: view.message,
      date: view.date,
      icon: notification.entity_type === 'payment' ? 'receipt' : 'message',
      href: view.href,
      linkText: view.linkText
    };
  });
}
