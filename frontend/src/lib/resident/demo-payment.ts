import { ApiError } from '../api/errors.ts';
import type { DemoTransaction, PayableObligation } from '../api/demo-payments.ts';
import { CONNECTION_ERROR, CSRF_MESSAGE, capitalize, conceptText, formatPeriod } from '../obligations/charge-format.ts';
import { formatCop, formatDate } from './payment-format.ts';
import { parseAmount } from '../payments/payment-form.ts';

/**
 * HU-010-D: the resident's demo payment, first part (pure, tested with node --test): the obligations.
 *
 * list → form (amount, the outstanding balance by default) → review → start → the checkout of that
 * transaction, on its own page (/resident/demo-payments/checkout?transaction=<id>, demo-checkout.ts),
 * which chooses the fictitious test instrument, processes it, follows and cancels the transaction and
 * shows the result the server answers. An obligation with the resident's own transaction in progress
 * opens that checkout instead of starting another one.
 *
 * The backend is the only authority: it decides whose obligations are listed, the balances,
 * whether an amount can be paid and the transaction. Nothing here computes a balance or a status:
 * the amount travels as the exact decimal text typed (never a JS number), and the projected balance of
 * the review is only a preview of that text against the outstanding balance the server sent. Nothing is
 * stored in the browser: only the transaction id travels, in the checkout's URL.
 *
 * Answers arrive in any order: every list request takes a new sequence number and only the answer
 * of the latest one is shown; an older answer (or its error) is dropped.
 */

export const DEMO_NOTICE_TITLE = 'Demostración académica';
export const DEMO_NOTICE =
  'Esta pasarela es una simulación interna de la aplicación: no se realiza ningún cobro real, no se mueve dinero ' +
  'y no hay conexión con una pasarela ni con un proveedor financiero externo. Nunca se solicitan datos de tarjetas, ' +
  'cuentas bancarias ni claves.';
export const SIMULATION_BADGE = 'Entorno de simulación: no se realizará ningún cobro real';

export const LOADING_MESSAGE = 'Cargando tus obligaciones pendientes...';
export const EMPTY_MESSAGE = 'No tienes obligaciones pendientes por pagar.';
export const EMPTY_HINT = 'Cuando la administración registre una nueva obligación de tu apartamento, aparecerá aquí.';
export const UNAVAILABLE_TITLE = 'El pago electrónico de demostración no está disponible.';
export const UNAVAILABLE_TEXT = 'La administración no lo ha habilitado. Puedes consultar tus pagos registrados en «Mis pagos».';
export const SESSION_EXPIRED_MESSAGE = 'Tu sesión ha expirado. Inicia sesión nuevamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para realizar pagos de demostración.';
export const LOAD_ERROR = 'No fue posible cargar tus obligaciones pendientes. Intenta de nuevo.';
export const START_ERROR = 'No fue posible iniciar el pago de demostración. Intenta de nuevo.';
export const INVALID_MESSAGE = 'Revisa el monto e intenta de nuevo.';
export const OBLIGATION_GONE = 'La obligación seleccionada ya no está disponible para pago. Revisa la lista actualizada.';
export const RESUMED_IN_PROGRESS = 'Ya tenías un pago de demostración en curso para esta obligación. Puedes continuarlo aquí.';
export const CANCELLED_NOTICE = 'Cancelaste la operación. No se aplicó ningún pago; puedes iniciar uno nuevo cuando quieras.';

export const DEMO_PAYMENTS_PATH = '/resident/demo-payments';
export const CHECKOUT_PATH = `${DEMO_PAYMENTS_PATH}/checkout`;
export const HISTORY_PATH = '/resident/payments';

export function paymentPath(paymentId: number): string {
  return `${HISTORY_PATH}/${paymentId}`;
}

/** The checkout of a transaction; `resumed` says it was already in progress (the checkout tells the resident). */
export function checkoutPath(transactionId: number, { resumed = false } = {}): string {
  return `${CHECKOUT_PATH}?transaction=${transactionId}${resumed ? '&resumed=1' : ''}`;
}

/** The obligations, after a cancellation confirmed by the server (the list says so once). */
export const CANCELLED_PATH = `${DEMO_PAYMENTS_PATH}?cancelled=1`;

/** "?transaction=12" → 12; anything else (missing, 0, "abc", "1e3", too long) → null. */
export function parseTransactionParam(search: string): number | null {
  const value = new URLSearchParams(search).get('transaction');
  if (!value || !/^[1-9]\d{0,15}$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

/**
 * The legacy URL of a transaction (/resident/demo-payments?transaction=<id>, before the checkout had its
 * own page) → its checkout; null when there is no valid id (the obligations are shown).
 */
export function legacyCheckoutRedirect(search: string): string | null {
  const id = parseTransactionParam(search);
  return id === null ? null : checkoutPath(id);
}

// --- Amounts: exact decimal text, compared in cents (BigInt), never binary floating point ------------------

/** "150000.50" → 15000050n; null for anything that is not the API's decimal text. */
export function toCents(amount: string): bigint | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(amount);
  if (!match) return null;
  return BigInt(match[1]) * 100n + BigInt((match[2] ?? '').padEnd(2, '0'));
}

/** 15000050n → "150000.50" (the API's decimal text). */
function fromCents(cents: bigint): string {
  return `${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`;
}

/** The API's "100000.00" → "100000" and "40000.25" → "40000,25", as the resident types it. */
export function amountInputText(amount: string): string {
  const match = /^(\d+)(?:\.(\d{2}))?$/.exec(amount);
  if (!match) return amount;
  return !match[2] || match[2] === '00' ? match[1] : `${match[1]},${match[2]}`;
}

/**
 * The amount typed for an obligation → the exact text for the API ("40000.50"). Greater than
 * zero, at most 2 decimals and within the server's limit (parseAmount), and not above the
 * outstanding balance the server sent. The server checks all of it again when starting.
 */
export function validateAmount(text: string, outstanding: string): { value: string } | { error: string } {
  const parsed = parseAmount(text);
  if ('error' in parsed) return parsed;
  const amount = toCents(parsed.value);
  const limit = toCents(outstanding);
  if (amount === null || limit === null) return { error: INVALID_MESSAGE };
  if (amount > limit) {
    return { error: `El monto no puede superar el saldo pendiente de la obligación (${formatCop(outstanding)}).` };
  }
  return { value: parsed.value };
}

/** True when the amount covers the whole outstanding balance (exact comparison). */
export function isFullPayment(amount: string, outstanding: string): boolean {
  const a = toCents(amount);
  return a !== null && a === toCents(outstanding);
}

/**
 * The review's preview: the outstanding balance the server sent minus the amount, exactly (cents), as
 * the API's text; null when it cannot be computed. Only a preview: the server applies the payment.
 */
export function projectedOutstanding(amount: string, outstanding: string): string | null {
  const a = toCents(amount);
  const o = toCents(outstanding);
  if (a === null || o === null || a > o) return null;
  return fromCents(o - a);
}

// --- Display --------------------------------------------------------------------------------------------

/** "Mensualidad — octubre de 2026", "Penalización: Ruido". */
export function obligationTitle(obligation: PayableObligation): string {
  const concept = conceptText(obligation.concept, obligation.description);
  return obligation.billing_period ? `${concept} — ${formatPeriod(obligation.billing_period)}` : concept;
}

export function obligationLines(obligation: PayableObligation): { label: string; value: string }[] {
  return [
    { label: 'Período', value: obligation.billing_period ? capitalize(formatPeriod(obligation.billing_period)) : 'No aplica' },
    { label: 'Vencimiento', value: formatDate(obligation.due_date) },
    { label: 'Valor', value: formatCop(obligation.amount) },
    { label: 'Abonado', value: formatCop(obligation.applied_amount) },
    { label: 'Saldo pendiente', value: formatCop(obligation.outstanding_amount) }
  ];
}

// --- Errors (shared with the checkout) --------------------------------------------------------------------

export const isApiError = (error: unknown, status: number, code?: string): error is ApiError =>
  error instanceof ApiError && error.status === status && (code === undefined || error.code === code);

/** The route itself does not exist: the backend has the demo gateway disabled (never "obligation not found"). */
export function isUnavailable(error: unknown): boolean {
  return isApiError(error, 404, 'NOT_FOUND');
}

/** A message for the resident: the backend's own Spanish message for a known domain error, never a technical text. */
export function messageOf(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return CONNECTION_ERROR;
  if (error.status === 403) return error.code === 'CSRF_FAILED' ? CSRF_MESSAGE : FORBIDDEN_MESSAGE;
  if (error.status >= 500 || error.code === 'UNEXPECTED_RESPONSE') return fallback;
  return error.message;
}

/** The message of a 422 on the amount (the backend validates it again), if that is what it is about. */
export function amountErrorOf(error: unknown): string | null {
  if (!isApiError(error, 422)) return null;
  return error.details.find((d) => d.field?.replace(/^body\./, '') === 'amount')?.message ?? null;
}

// --- State ----------------------------------------------------------------------------------------------

export type DemoView =
  | { kind: 'loading' }
  | { kind: 'unavailable' }
  | { kind: 'load-error'; message: string }
  | { kind: 'list' }
  | { kind: 'form'; obligation: PayableObligation; amountText: string; error: string | null }
  | { kind: 'review'; obligation: PayableObligation; amount: string };

/** "opening": the checkout is being opened (the page is left): everything stays disabled. */
export type DemoBusy = 'loading' | 'starting' | 'opening' | null;

export interface DemoNotice {
  tone: 'error' | 'info';
  text: string;
}

export interface DemoState {
  view: DemoView;
  /** The last list the server sent (only obligations with an outstanding balance). */
  obligations: PayableObligation[];
  /** What is being sent: every action is disabled meanwhile (no double submission). */
  busy: DemoBusy;
  notice: DemoNotice | null;
  sessionExpired: boolean;
}

export interface DemoApi {
  list(): Promise<{ items: PayableObligation[] }>;
  start(chargeId: number, amount: string): Promise<DemoTransaction>;
}

export interface DemoDeps {
  api: DemoApi;
  render: (state: DemoState) => void;
  /** Opens another page of the application (the checkout). */
  navigate: (path: string) => void;
}

export function createDemoPaymentFlow(deps: DemoDeps) {
  let state: DemoState = {
    view: { kind: 'loading' },
    obligations: [],
    busy: null,
    notice: null,
    sessionExpired: false
  };
  let disposed = false;
  /** Sequence of the list requests: only the latest answer counts. */
  let listRequest = 0;

  function set(changes: Partial<DemoState>): void {
    if (disposed) return;
    state = { ...state, ...changes };
    deps.render(state);
  }

  /** The checkout of a transaction: once, and nothing else can be sent meanwhile. */
  function open(transactionId: number, resumed: boolean): void {
    set({ busy: 'opening' });
    deps.navigate(checkoutPath(transactionId, { resumed }));
  }

  async function loadList(notice: DemoNotice | null = null): Promise<void> {
    const request = ++listRequest;
    set({ view: { kind: 'loading' }, busy: 'loading', notice });
    try {
      const { items } = await deps.api.list();
      if (request !== listRequest || disposed) return;
      set({ view: { kind: 'list' }, obligations: items, busy: null });
    } catch (error) {
      if (request !== listRequest || disposed) return;
      if (isApiError(error, 401)) {
        set({ view: { kind: 'load-error', message: SESSION_EXPIRED_MESSAGE }, busy: null, sessionExpired: true, notice: null });
      } else if (isUnavailable(error)) {
        set({ view: { kind: 'unavailable' }, busy: null, notice: null });
      } else {
        set({ view: { kind: 'load-error', message: messageOf(error, LOAD_ERROR) }, busy: null });
      }
    }
  }

  return {
    get state(): DemoState {
      return state;
    },

    /** The page opens: the list (with a notice, e.g. after a cancellation confirmed in the checkout). */
    init(notice: DemoNotice | null = null): Promise<void> {
      return loadList(notice);
    },

    /** "Reintentar" after the list failed; never while a request is pending. */
    retry(): Promise<void> {
      if (state.busy !== null) return Promise.resolve();
      return loadList();
    },

    select(chargeId: number): void {
      if (state.busy !== null || state.view.kind !== 'list' || state.sessionExpired) return;
      const obligation = state.obligations.find((item) => item.charge_id === chargeId);
      if (!obligation || toCents(obligation.outstanding_amount)! <= 0n) return; // only what still has a balance
      if (obligation.in_progress_transaction_id !== null) {
        open(obligation.in_progress_transaction_id, true); // "Continuar pago en curso": never a second one
        return;
      }
      set({ view: { kind: 'form', obligation, amountText: amountInputText(obligation.outstanding_amount), error: null }, notice: null });
    },

    /** "Volver a la lista": nothing was started. */
    back(): Promise<void> | void {
      if (state.busy !== null) return;
      return loadList();
    },

    review(amountText: string): void {
      if (state.busy !== null || state.view.kind !== 'form') return;
      const { obligation } = state.view;
      const checked = validateAmount(amountText, obligation.outstanding_amount);
      if ('error' in checked) {
        set({ view: { kind: 'form', obligation, amountText, error: checked.error } });
        return;
      }
      set({ view: { kind: 'review', obligation, amount: checked.value }, notice: null });
    },

    edit(): void {
      if (state.busy !== null || state.view.kind !== 'review') return;
      const { obligation, amount } = state.view;
      set({ view: { kind: 'form', obligation, amountText: amountInputText(amount), error: null } });
    },

    /** One POST (never retried automatically); the transaction created is continued in its checkout. */
    async start(): Promise<void> {
      if (state.busy !== null || state.view.kind !== 'review' || state.sessionExpired) return;
      const { obligation, amount } = state.view;
      set({ busy: 'starting', notice: null });
      try {
        const started = await deps.api.start(obligation.charge_id, amount); // exactly the text validated above
        if (disposed) return;
        open(started.id, false);
      } catch (error) {
        if (disposed) return;
        if (isApiError(error, 401)) {
          set({ busy: null, sessionExpired: true, notice: { tone: 'error', text: SESSION_EXPIRED_MESSAGE } });
          return;
        }
        if (isUnavailable(error)) {
          set({ view: { kind: 'unavailable' }, busy: null, notice: null });
          return;
        }
        if (isApiError(error, 422)) {
          const detail = amountErrorOf(error);
          if (detail) {
            set({ view: { kind: 'form', obligation, amountText: amountInputText(amount), error: detail }, busy: null });
            return;
          }
          set({ busy: null, notice: { tone: 'error', text: INVALID_MESSAGE } });
          return;
        }
        if (isApiError(error, 409, 'DEMO_PAYMENT_IN_PROGRESS')) {
          // Perhaps the resident's own, started in another tab: its checkout is offered, never a second one.
          await loadList({ tone: 'error', text: error.message });
          const own = state.obligations.find((item) => item.charge_id === obligation.charge_id)?.in_progress_transaction_id;
          if (own) open(own, true);
          return;
        }
        if (isApiError(error, 409, 'CHARGE_NOT_PAYABLE')) return loadList({ tone: 'error', text: error.message });
        if (isApiError(error, 404)) return loadList({ tone: 'error', text: OBLIGATION_GONE });
        set({ busy: null, notice: { tone: 'error', text: messageOf(error, START_ERROR) } });
      }
    },

    /** The page is left: no more renders. */
    dispose(): void {
      disposed = true;
    }
  };
}

export type DemoPaymentFlow = ReturnType<typeof createDemoPaymentFlow>;
