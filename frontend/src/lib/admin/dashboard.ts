import type { IconName } from '../landing/icons.ts';
import { formatCop } from '../payments/payment-form.ts';
import { PENDING_REPORT_PATH, REPORTS_PATH } from '../reports/reports.ts';

/**
 * The administrator's home page (/admin): its modules and its summary (pure, tested with node --test).
 *
 * Every module points to a page that exists. Every number of the summary comes from an existing read-only
 * endpoint of the API (lib/api/dashboard-server.ts), as the backend computed it; nothing is added up here.
 * A number the API could not give is shown as "No disponible", never as 0 or as an estimate.
 */

export interface AdminModule {
  title: string;
  href: string;
  category: string;
  description: string;
  /** The short call to action shown at the bottom of the card (the card's link is named by its title). */
  action: string;
  icon: IconName;
}

export const ADMIN_MODULES: readonly AdminModule[] = [
  {
    title: 'Unidades residenciales',
    href: '/admin/blocks',
    category: 'Estructura',
    description: 'Consulta y gestiona los bloques, las torres y los apartamentos de la urbanización.',
    action: 'Ver bloques',
    icon: 'building'
  },
  {
    title: 'Residentes',
    href: '/admin/residents',
    category: 'Directorio',
    description: 'Consulta los residentes registrados, su apartamento y sus datos de contacto, y actualiza su información.',
    action: 'Ver residentes',
    icon: 'users'
  },
  {
    title: 'Invitar residente',
    href: '/admin/residents/invite',
    category: 'Registro',
    description: 'Registra un residente, asígnale un apartamento disponible y envíale la invitación para activar su cuenta.',
    action: 'Enviar invitación',
    icon: 'userPlus'
  },
  {
    title: 'Obligaciones',
    href: '/admin/obligations',
    category: 'Cobros',
    description: 'Genera las mensualidades, crea moras, penalizaciones y otros cobros, y consulta su estado.',
    action: 'Ver obligaciones',
    icon: 'clipboard'
  },
  {
    title: 'Pagos',
    href: '/admin/payments',
    category: 'Recaudo',
    description: 'Registra los pagos de los residentes y consulta el resumen. El recibo en PDF llega a su correo.',
    action: 'Ir a pagos',
    icon: 'wallet'
  },
  {
    title: 'Feedback',
    href: '/admin/feedback',
    category: 'Atención',
    description: 'Revisa, responde y resuelve las quejas, solicitudes y sugerencias de los residentes.',
    action: 'Ver feedback',
    icon: 'message'
  },
  {
    title: 'Estados de cuenta',
    href: '/admin/residents',
    category: 'Consulta',
    description: 'Elige un residente para consultar sus obligaciones, sus pagos, su saldo y el estado de su cuenta.',
    action: 'Elegir residente',
    icon: 'fileText'
  },
  {
    title: 'Reportes financieros',
    href: REPORTS_PATH,
    category: 'Informes',
    description: 'Pagos registrados por rango de fechas, saldos pendientes y unidades en mora.',
    action: 'Ver reportes',
    icon: 'barChart'
  }
];

/** What the API answered for the summary; null where the request failed. Amounts are decimal text. */
export interface AdminSummarySources {
  /** GET /admin/residents → total (registered residents: active or inactive account). */
  residents: number | null;
  /** GET /admin/feedback?status=PENDING → total. */
  pendingFeedback: number | null;
  /** GET /admin/reports/pending-balances → its totals, computed by the backend. */
  balances: { units: number; overdue_units: number; total_pending: string; total_overdue: string } | null;
}

export type SummaryTone = 'neutral' | 'attention';

export interface SummaryTile {
  key: 'residents' | 'pending-feedback' | 'units-pending' | 'units-overdue';
  label: string;
  /** The number to show, or null: "No disponible". */
  value: string | null;
  detail: string;
  icon: IconName;
  tone: SummaryTone;
  /** The page with the detail behind the number (its own filters in the URL). */
  href: string;
}

export const UNAVAILABLE_VALUE = 'No disponible';
export const UNAVAILABLE_DETAIL = 'No fue posible consultar este dato.';

const COUNT = new Intl.NumberFormat('es-CO');

const count = (value: number | null): string | null => (value === null ? null : COUNT.format(value));
const attention = (value: number | null | undefined): SummaryTone => (value ? 'attention' : 'neutral');

/** The four tiles of the summary, in order; each one honest about a value the API did not give. */
export function summaryTiles({ residents, pendingFeedback, balances }: AdminSummarySources): SummaryTile[] {
  return [
    {
      key: 'residents',
      label: 'Residentes registrados',
      value: count(residents),
      detail: residents === null ? UNAVAILABLE_DETAIL : 'Con cuenta activa o inactiva.',
      icon: 'users',
      tone: 'neutral',
      href: '/admin/residents'
    },
    {
      key: 'pending-feedback',
      label: 'Feedback pendiente',
      value: count(pendingFeedback),
      detail: pendingFeedback === null ? UNAVAILABLE_DETAIL : 'Quejas, solicitudes y sugerencias sin resolver.',
      icon: 'message',
      tone: attention(pendingFeedback),
      href: '/admin/feedback?status=PENDING'
    },
    {
      key: 'units-pending',
      label: 'Unidades con saldo pendiente',
      value: count(balances?.units ?? null),
      detail: balances === null ? UNAVAILABLE_DETAIL : `Total pendiente: ${formatCop(balances.total_pending)}`,
      icon: 'receipt',
      tone: 'neutral',
      href: PENDING_REPORT_PATH
    },
    {
      key: 'units-overdue',
      label: 'Unidades en mora',
      value: count(balances?.overdue_units ?? null),
      detail: balances === null ? UNAVAILABLE_DETAIL : `Total en mora: ${formatCop(balances.total_overdue)}`,
      icon: 'calendar',
      tone: attention(balances?.overdue_units),
      href: `${PENDING_REPORT_PATH}?scope=OVERDUE`
    }
  ];
}
