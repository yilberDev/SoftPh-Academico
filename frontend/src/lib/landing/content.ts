import type { IconName } from './icons.ts';

/**
 * Content of the public landing page (pure, tested with node --test).
 *
 * It only describes what SoftPh already does (no prices, clients, testimonials, certifications,
 * exports or real payment processing). The dashboard and the table are illustrative: fixed example
 * values, never data of a real community; the table's totals are derived from its own rows so the
 * example never contradicts itself.
 */

export const LOGIN_PATH = '/login';

/** The sections of the page, in order: the navigation and the footer link to these ids. */
export const SECTIONS = {
  home: 'inicio',
  features: 'funcionalidades',
  howItWorks: 'como-funciona',
  benefits: 'beneficios'
} as const;

export const NAV_LINKS: readonly { label: string; href: string }[] = [
  { label: 'Inicio', href: `#${SECTIONS.home}` },
  { label: 'Funcionalidades', href: `#${SECTIONS.features}` },
  { label: 'Cómo funciona', href: `#${SECTIONS.howItWorks}` },
  { label: 'Beneficios', href: `#${SECTIONS.benefits}` }
];

export const HERO_POINTS: readonly string[] = ['Información organizada', 'Gestión más clara', 'Comunidad conectada'];

export interface Highlight {
  icon: IconName;
  title: string;
  text: string;
}

export const VALUE_POINTS: readonly Highlight[] = [
  { icon: 'layout', title: 'Administración centralizada',
    text: 'Residentes, unidades, obligaciones y pagos en un mismo lugar.' },
  { icon: 'wallet', title: 'Información financiera organizada',
    text: 'Saldos, pagos aplicados y estados de cuenta siempre consultables.' },
  { icon: 'message', title: 'Comunicación con los residentes',
    text: 'Solicitudes, respuestas y avisos en el panel de cada residente.' }
];

/** Who uses each feature, shown at the foot of its card (the role areas of the application). */
export type Audience = 'Administración' | 'Residentes' | 'Administración y residentes';

export interface Feature extends Highlight {
  audience: Audience;
}

export const FEATURES: readonly Feature[] = [
  { icon: 'users', title: 'Gestión de residentes', audience: 'Administración',
    text: 'Invitación por correo, activación de la cuenta, listado con búsqueda y filtros, y actualización o eliminación de residentes.' },
  { icon: 'receipt', title: 'Pagos y recibos', audience: 'Administración y residentes',
    text: 'Registro de pagos aplicados a las obligaciones de cada unidad, con el recibo en PDF enviado al correo del residente.' },
  { icon: 'fileText', title: 'Estados de cuenta', audience: 'Administración y residentes',
    text: 'Obligaciones, pagos aplicados, saldo pendiente, mora y saldo a favor de cada unidad, para la administración y el residente.' },
  { icon: 'barChart', title: 'Reportes financieros', audience: 'Administración',
    text: 'Pagos registrados por rango de fechas, con totales por método, y saldos pendientes por unidad, incluidas las que están en mora.' },
  { icon: 'message', title: 'Solicitudes y PQRS', audience: 'Administración y residentes',
    text: 'Quejas, sugerencias y solicitudes con categoría y urgencia; la administración responde y el residente consulta su estado.' },
  { icon: 'bell', title: 'Notificaciones', audience: 'Residentes',
    text: 'Avisos de pagos registrados y de respuestas a solicitudes, en el panel del residente y por correo electrónico.' }
];

/** What the financial section highlights next to its illustrative table. */
export const FINANCE_POINTS: readonly string[] = [
  'Obligaciones pagadas, pendientes y en mora de cada unidad.',
  'Reporte de pagos por rango de fechas y de saldos pendientes.',
  'Estado de cuenta para la administración y para el residente.'
];

export interface Step extends Highlight {
  number: string;
  /** A short note at the foot of the step: what it works with. */
  outcome: string;
}

export const STEPS: readonly Step[] = [
  { number: '01', icon: 'clipboard', title: 'Organiza la información', outcome: 'Estructura, residentes y obligaciones',
    text: 'Registra los bloques, torres y apartamentos, invita a los residentes y define las obligaciones de cada unidad.' },
  { number: '02', icon: 'wallet', title: 'Gestiona pagos y solicitudes', outcome: 'Pagos aplicados y respuestas',
    text: 'Registra los pagos, que se aplican a las obligaciones pendientes, y responde las solicitudes de los residentes.' },
  { number: '03', icon: 'eye', title: 'Revisa los resultados', outcome: 'Estados de cuenta y reportes',
    text: 'Consulta los estados de cuenta y los reportes de pagos y saldos; cada residente ve su propia información.' }
];

export interface RoleBlock {
  icon: IconName;
  /** The role, as a chip above the title. */
  badge: string;
  title: string;
  text: string;
  items: readonly string[];
  /** Where the role works in SoftPh, at the foot of the block. */
  area: string;
}

export const ROLES: readonly RoleBlock[] = [
  { icon: 'shieldUser', badge: 'Administración', title: 'Para administradores', area: 'Área de administración',
    text: 'Las herramientas para llevar el día a día de la copropiedad.',
    items: ['Gestión de residentes y unidades', 'Seguimiento de pagos y obligaciones', 'Consulta de reportes financieros',
      'Atención de solicitudes y respuestas'] },
  { icon: 'user', badge: 'Residentes', title: 'Para residentes', area: 'Panel del residente',
    text: 'Su información, siempre disponible desde su propio panel.',
    items: ['Consulta de su estado de cuenta', 'Revisión de movimientos y recibos', 'Consulta de notificaciones',
      'Seguimiento de sus solicitudes'] }
];

/** Each resident only reaches their own information (the API decides it from the session). */
export const PRIVACY_NOTE =
  'Cada residente consulta únicamente su propia información: su estado de cuenta, sus pagos, sus solicitudes y sus notificaciones.';

// --- Illustrative data (never real) ---------------------------------------------------------------

export const ILLUSTRATIVE_NOTE = 'Vista ilustrativa con datos de ejemplo.';

export type ExampleStatus = 'Pagado' | 'Pendiente' | 'En mora';

export const STATUS_BADGES: Record<ExampleStatus, string> = {
  Pagado: 'bg-emerald-50 text-emerald-700 ring-emerald-600/25',
  Pendiente: 'bg-amber-50 text-amber-800 ring-amber-600/25',
  'En mora': 'bg-rose-50 text-rose-700 ring-rose-600/25'
};

/** The color of an example total, by status. */
export const STATUS_TEXT: Record<ExampleStatus, string> = {
  Pagado: 'text-teal-700',
  Pendiente: 'text-amber-700',
  'En mora': 'text-rose-700'
};

export interface ExampleRow {
  unit: string;
  concept: string;
  date: string;
  amount: number; // whole pesos
  status: ExampleStatus;
}

export const EXAMPLE_ROWS: readonly ExampleRow[] = [
  { unit: 'B1-TO1-APTO-101', concept: 'Mensualidad', date: '5 oct.', amount: 180000, status: 'Pagado' },
  { unit: 'B1-TO1-APTO-102', concept: 'Mensualidad', date: '5 oct.', amount: 180000, status: 'Pendiente' },
  { unit: 'B1-TO2-APTO-201', concept: 'Mensualidad', date: '5 sep.', amount: 180000, status: 'En mora' },
  { unit: 'B2-TO1-APTO-104', concept: 'Penalización', date: '20 oct.', amount: 50000, status: 'Pendiente' },
  { unit: 'B2-TO1-APTO-105', concept: 'Mensualidad', date: '5 oct.', amount: 180000, status: 'Pagado' }
];

/** The example's totals by status, from its own rows. */
export function exampleTotals(rows: readonly ExampleRow[]): Record<ExampleStatus, number> {
  const totals: Record<ExampleStatus, number> = { Pagado: 0, Pendiente: 0, 'En mora': 0 };
  for (const row of rows) totals[row.status] += row.amount;
  return totals;
}

/** How many example rows have each status, from the rows themselves. */
export function exampleCounts(rows: readonly { status: ExampleStatus }[]): Record<ExampleStatus, number> {
  const counts: Record<ExampleStatus, number> = { Pagado: 0, Pendiente: 0, 'En mora': 0 };
  for (const row of rows) counts[row.status] += 1;
  return counts;
}

/** "1 obligación", "2 obligaciones" */
export function obligationsLabel(count: number): string {
  return `${count} ${count === 1 ? 'obligación' : 'obligaciones'}`;
}

const COP = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });

/** "$ 180.000" */
export function formatExampleAmount(amount: number): string {
  return COP.format(amount);
}

/** The icon-only sidebar of the illustrative dashboard (the first one is the active summary). */
export const DASHBOARD_NAV: readonly IconName[] = ['home', 'users', 'receipt', 'fileText', 'barChart', 'message'];

export type KpiTone = 'neutral' | 'danger' | 'warning';

export const DASHBOARD_KPIS: readonly { label: string; value: string; detail: string; tone: KpiTone }[] = [
  { label: 'Recaudo del mes', value: '$ 12.600.000', detail: '70 pagos aplicados', tone: 'neutral' },
  { label: 'Saldo pendiente', value: '$ 3.240.000', detail: '18 unidades', tone: 'danger' },
  { label: 'Residentes', value: '86', detail: 'registrados', tone: 'neutral' },
  { label: 'Solicitudes', value: '5', detail: 'pendientes', tone: 'warning' }
];

/** Relative heights (0-100) of the illustrative collection chart, oldest month first. */
export const DASHBOARD_CHART: readonly { month: string; value: number }[] = [
  { month: 'May', value: 58 }, { month: 'Jun', value: 66 }, { month: 'Jul', value: 61 },
  { month: 'Ago', value: 74 }, { month: 'Sep', value: 70 }, { month: 'Oct', value: 86 }
];

/** The latest obligations of the illustrative dashboard (its own example, separate from the table). */
export const DASHBOARD_MOVEMENTS: readonly Omit<ExampleRow, 'date'>[] = [
  { unit: 'B1-TO2-APTO-104', concept: 'Mensualidad', amount: 180000, status: 'Pagado' },
  { unit: 'B2-TO1-APTO-203', concept: 'Mensualidad', amount: 180000, status: 'En mora' },
  { unit: 'B1-TO1-APTO-302', concept: 'Penalización', amount: 50000, status: 'Pendiente' }
];
