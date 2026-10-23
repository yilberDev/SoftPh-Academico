import { ApiError } from '../api/errors.ts';
import type { PendingScope } from '../api/reports.ts';
import { accountStatementPath } from '../residents/resident-detail.ts';

/**
 * HU-012: the financial reports of the administrator (pure, tested with node --test).
 *
 * CU-012: the administrator chooses the kind of report, gives its criteria and the server builds it.
 * The criteria are checked here only to say at once what is missing (the server checks them again);
 * every amount, total and status comes from one answer of the API, and a newer answer replaces the
 * whole report (never lines and totals of two moments).
 */

export const REPORTS_PATH = '/admin/reports';
export const PAYMENTS_REPORT_PATH = `${REPORTS_PATH}/payments`;
export const PENDING_REPORT_PATH = `${REPORTS_PATH}/pending-balances`;

export const DATE_FROM_REQUIRED = 'Indica la fecha inicial del reporte.';
export const DATE_TO_REQUIRED = 'Indica la fecha final del reporte.';
export const DATE_RANGE_MESSAGE = 'La fecha inicial no puede ser posterior a la fecha final.';
export const DATE_FORMAT_MESSAGE = 'Escribe una fecha válida.';
export const LOADING_MESSAGE = 'Generando el reporte…';
export const NO_PAYMENTS_MESSAGE = 'No existen pagos registrados para los criterios seleccionados.';
export const NO_PENDING_MESSAGE = 'Ninguna unidad residencial tiene saldo pendiente.';
export const NO_OVERDUE_MESSAGE = 'Ninguna unidad residencial está en mora.';
export const CRITERIA_PROMPT = 'Indica el rango de fechas de pago y genera el reporte.';
export const CONNECTION_ERROR = 'No fue posible generar el reporte. Intenta de nuevo.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para consultar los reportes financieros.';
export const NO_RESIDENT = 'Sin residente vigente';

export const SCOPE_LABELS: Record<PendingScope, string> = {
  ALL: 'Todas las unidades con saldo pendiente',
  OVERDUE: 'Solo unidades en mora'
};

export type DateField = 'date_from' | 'date_to';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function isDate(value: string): boolean {
  if (!ISO_DATE.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day;
}

/** CU-012 5a: the same rules and messages as the server; the query string when the criteria are complete. */
export function validatePaymentsCriteria(dateFrom: string, dateTo: string): {
  errors: Partial<Record<DateField, string>>;
  query: string | null;
} {
  const errors: Partial<Record<DateField, string>> = {};
  const from = dateFrom.trim();
  const to = dateTo.trim();
  if (!from) errors.date_from = DATE_FROM_REQUIRED;
  else if (!isDate(from)) errors.date_from = DATE_FORMAT_MESSAGE;
  if (!to) errors.date_to = DATE_TO_REQUIRED;
  else if (!isDate(to)) errors.date_to = DATE_FORMAT_MESSAGE;
  else if (!errors.date_from && from > to) errors.date_to = DATE_RANGE_MESSAGE;
  if (Object.keys(errors).length > 0) return { errors, query: null };
  return { errors, query: new URLSearchParams({ date_from: from, date_to: to }).toString() };
}

/** The criteria kept in the page URL (reloading or sharing it gives the same report). */
export function criteriaFromUrl(params: URLSearchParams): { dateFrom: string; dateTo: string } {
  return { dateFrom: params.get('date_from') ?? '', dateTo: params.get('date_to') ?? '' };
}

export function parseScope(params: URLSearchParams): PendingScope {
  return params.get('scope') === 'OVERDUE' ? 'OVERDUE' : 'ALL';
}

/** HU-011 criterion 3.2: from each apartment that owes to its resident's statement (none without a resident). */
export function statementLink(resident: { id: number } | null): string | null {
  return resident ? accountStatementPath(resident.id) : null;
}

export type ReportState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; report: T }
  | { kind: 'invalid'; errors: Partial<Record<DateField, string>> }
  | { kind: 'error'; message: string };

export interface ReportDeps<T> {
  load: (query: string) => Promise<T>;
  render: (state: ReportState<T>) => void;
  navigate: (path: string) => void;
}

/** Generates a report from its query; an older answer never replaces a newer one. */
export function createReportLoader<T>(deps: ReportDeps<T>) {
  let state: ReportState<T> = { kind: 'idle' };
  let request = 0;

  function set(next: ReportState<T>): void {
    state = next;
    deps.render(state);
  }

  return {
    get state(): ReportState<T> {
      return state;
    },
    /** Shows what is missing without asking the server (CU-012 6a). */
    invalid(errors: Partial<Record<DateField, string>>): void {
      request += 1; // an answer still on its way no longer applies
      set({ kind: 'invalid', errors });
    },
    async generate(query: string): Promise<void> {
      const mine = ++request;
      set({ kind: 'loading' });
      try {
        const report = await deps.load(query);
        if (mine === request) set({ kind: 'ready', report });
      } catch (error) {
        if (mine !== request) return;
        if (error instanceof ApiError && error.status === 401) {
          deps.navigate('/login');
          return;
        }
        if (error instanceof ApiError && error.status === 422) {
          const errors: Partial<Record<DateField, string>> = {};
          for (const detail of error.details) {
            const field = detail.field?.replace(/^query\./, '');
            if ((field === 'date_from' || field === 'date_to') && !errors[field]) errors[field] = detail.message;
          }
          if (Object.keys(errors).length > 0) {
            set({ kind: 'invalid', errors });
            return;
          }
        }
        const message = error instanceof ApiError && error.status === 403 ? FORBIDDEN_MESSAGE : CONNECTION_ERROR;
        // CU-012 6c: never an incomplete report, only the message and "Reintentar".
        set({ kind: 'error', message });
      }
    }
  };
}
