import { ApiError } from '../api/errors.ts';
import type { AccountStatement, AccountStatus, StatementObligation, StatementPayment } from '../api/account-statement.ts';
import { conceptText } from '../obligations/charge-format.ts';
import { formatCop } from '../payments/payment-form.ts';
import { periodText } from '../resident/payment-format.ts';

/**
 * HU-011 / HU-014: what the account statement shows and how it is loaded, for both the
 * administrator and the resident (pure, tested with node --test). Every amount, status and total
 * comes from one response of the API: nothing here adds money up or decides a status, so the screen
 * never shows lines and totals of two different moments (a newer answer replaces the whole statement).
 */

export const ACCOUNT_STATUS_LABELS: Record<AccountStatus, string> = {
  UP_TO_DATE: 'Al día',
  PENDING: 'Pendiente',
  OVERDUE: 'En mora'
};

/** Always with its text, never color alone. */
export const ACCOUNT_STATUS_BADGES: Record<AccountStatus, string> = {
  UP_TO_DATE: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  PENDING: 'bg-amber-50 text-amber-900 ring-amber-200',
  OVERDUE: 'bg-red-50 text-red-800 ring-red-200'
};

export const ACCOUNT_STATUS_HELP: Record<AccountStatus, string> = {
  UP_TO_DATE: 'No hay obligaciones con saldo pendiente.',
  PENDING: 'Hay obligaciones con saldo pendiente, ninguna con la fecha de pago vencida.',
  OVERDUE: 'Hay obligaciones con saldo pendiente después de su fecha de pago.'
};

export const LOADING_MESSAGE = 'Consultando el estado de cuenta…';
export const REFRESHING_MESSAGE = 'Actualizando el estado de cuenta…';
export const EMPTY_MESSAGE = 'No existen obligaciones ni movimientos registrados para esta unidad residencial.';
export const NO_PAYMENTS_MESSAGE = 'No hay pagos registrados.';
export const NO_OVERDUE_MESSAGE = 'No hay obligaciones en mora.';
export const NO_PENDING_MESSAGE = 'No hay obligaciones pendientes dentro de su plazo.';
export const NO_PAID_MESSAGE = 'Aún no hay obligaciones pagadas.';
export const CONNECTION_ERROR = 'No fue posible obtener el estado de cuenta. Intenta de nuevo.';
export const ADMIN_NOT_AVAILABLE = 'El residente seleccionado no está disponible: no existe o ya no está registrado.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para consultar este estado de cuenta.';
export const UNIT_NOTE =
  'El estado de cuenta corresponde a la unidad residencial: sus obligaciones y los pagos aplicados a ellas.';
export const RESIDENT_PAYMENTS_NOTE =
  'Se muestran tus pagos. Los abonos de cada obligación incluyen todos los pagos aplicados a tu unidad residencial.';

export interface ObligationGroups {
  /** En mora: past their due date with a balance (criterion 3.1). */
  overdue: StatementObligation[];
  /** With a balance, still within their due date (PENDING or PARTIAL). */
  pending: StatementObligation[];
  paid: StatementObligation[];
}

/** The obligations as the server classified them; the order (FIFO) is kept inside each group. */
export function groupObligations(obligations: readonly StatementObligation[]): ObligationGroups {
  return {
    overdue: obligations.filter((item) => item.status === 'OVERDUE'),
    pending: obligations.filter((item) => item.status === 'PENDING' || item.status === 'PARTIAL'),
    paid: obligations.filter((item) => item.status === 'PAID')
  };
}

/** "Penalización: Ruido", "Mensualidad · Octubre de 2026". */
export function obligationText(item: StatementObligation): string {
  const concept = conceptText(item.concept, item.description);
  return item.billing_period ? `${concept} · ${periodText(item.billing_period)}` : concept;
}

/** What a payment covered: "Mensualidad · Octubre de 2026: $ 50.000,00" for each obligation of this statement. */
export function allocationLines(payment: StatementPayment, obligations: readonly StatementObligation[]): string[] {
  const byId = new Map(obligations.map((item) => [item.id, item]));
  return payment.allocations.map(({ charge_id, amount }) => {
    const obligation = byId.get(charge_id);
    return `${obligation ? obligationText(obligation) : `Obligación #${charge_id}`}: ${formatCop(amount)}`;
  });
}

/** Who made the payment, as the administrator sees it (the resident only sees their own payments). */
export function payerText(payment: StatementPayment): string {
  if (payment.by_this_resident) return 'Este residente';
  return payment.payer_name ? `${payment.payer_name} (otro residente de la unidad)` : 'Otro residente de la unidad';
}

export type StatementState =
  | { kind: 'loading' }
  | { kind: 'ready'; statement: AccountStatement; refreshing: boolean; notice: string | null }
  | { kind: 'unavailable'; message: string }
  | { kind: 'error'; message: string };

export interface StatementDeps {
  load: () => Promise<AccountStatement>;
  render: (state: StatementState) => void;
  navigate: (path: string) => void;
  /** Message for a 404 (administrator: the resident is not available; resident: the server's message). */
  notFoundMessage?: string;
}

/** Loads the statement; refresh() keeps the statement on screen until a whole new one replaces it. */
export function createStatementLoader(deps: StatementDeps) {
  let state: StatementState = { kind: 'loading' };
  let request = 0;

  function set(next: StatementState): void {
    state = next;
    deps.render(state);
  }

  function failure(error: unknown): { kind: 'unavailable' | 'error'; message: string } | null {
    if (error instanceof ApiError) {
      if (error.status === 401) {
        deps.navigate('/login');
        return null;
      }
      if (error.status === 404) return { kind: 'unavailable', message: deps.notFoundMessage ?? error.message };
      if (error.status === 403) return { kind: 'error', message: FORBIDDEN_MESSAGE };
    }
    // A server error or no connection: never a partial statement (CU-011 6c, CU-014 4b).
    return { kind: 'error', message: CONNECTION_ERROR };
  }

  async function run(): Promise<void> {
    const mine = ++request;
    try {
      const statement = await deps.load();
      if (mine !== request) return; // an older answer never replaces a newer one
      set({ kind: 'ready', statement, refreshing: false, notice: null });
    } catch (error) {
      if (mine !== request) return;
      const failed = failure(error);
      if (!failed) return;
      if (state.kind === 'ready' && failed.kind === 'error') {
        // The statement on screen stays whole and consistent; only the update failed.
        set({ ...state, refreshing: false, notice: failed.message });
        return;
      }
      set(failed);
    }
  }

  return {
    get state(): StatementState {
      return state;
    },
    async start(): Promise<void> {
      set({ kind: 'loading' });
      await run();
    },
    /** "Actualizar", retry after an error, or coming back to the tab (criterion 4.2: the latest data). */
    async refresh(): Promise<void> {
      if (state.kind === 'ready') set({ ...state, refreshing: true, notice: null });
      else set({ kind: 'loading' });
      await run();
    }
  };
}
