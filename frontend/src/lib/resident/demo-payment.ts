import { ApiError } from '../api/errors.ts';
import type { DemoOutcome, DemoTransaction, DemoTransactionStatus, PayableObligation } from '../api/demo-payments.ts';
import { CONNECTION_ERROR, CSRF_MESSAGE, capitalize, conceptText, formatPeriod } from '../obligations/charge-format.ts';
import { formatCop, formatDate, formatDateTime } from './payment-format.ts';
import { parseAmount } from '../payments/payment-form.ts';
import { DEMO_RECEIPT_LINK, demoReceiptPath } from './payment-detail.ts';

export { DEMO_RECEIPT_LINK };

/**
 * HU-010-D: the resident's demo payment flow (pure, tested with node --test).
 *
 * list → form (amount, the outstanding balance by default) → review → start → the demo gateway
 * (simulate approval, rejection or a processing error; cancel) → the result the server answers.
 *
 * The backend is the only authority: it decides whose obligations are listed, the balances,
 * whether an amount can be paid, the transaction's status, its expiry and the payment. Nothing
 * here computes a balance or a status, and the amount travels as the exact decimal text typed
 * (never a JS number). A result is shown only as the server answers it, never because a button
 * was pressed. While a transaction is INITIATED its state is read again every POLL_INTERVAL_MS,
 * at most MAX_POLLS times and never after a terminal state, an error that needs the resident or
 * dispose(). Nothing is stored in the browser: only the transaction id travels in the URL.
 *
 * Answers arrive in any order: every request that reads or changes the transaction (a reading,
 * an outcome, a cancellation, a resumption) takes a new sequence number, and only the answer of the
 * latest one is shown; an older answer (or its error) is dropped. The same holds for the list.
 */

export const DEMO_NOTICE_TITLE = 'Demostración académica';
export const DEMO_NOTICE =
  'Esta pasarela es una simulación interna de la aplicación: no se realiza ningún cobro real, no se mueve dinero ' +
  'y no hay conexión con una pasarela ni con un proveedor financiero externo. Nunca se solicitan datos de tarjetas, ' +
  'cuentas bancarias ni claves.';
export const APPROVAL_EFFECT =
  'Si simulas la aprobación, la aplicación registra internamente un pago en tu historial y lo aplica a esta ' +
  'obligación. El rechazo, el error simulado y la cancelación no aplican ningún pago.';

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
export const TRANSACTION_MISSING = 'La transacción no existe o no pertenece a tu cuenta.';
export const FINISHED_MESSAGE = 'La transacción ya había terminado. Este es su estado actual.';
export const UNCONFIRMED_MESSAGE =
  'No fue posible confirmar el resultado con el servidor. Consulta el estado de la transacción antes de intentarlo de nuevo.';
export const CHECK_ERROR = 'No fue posible consultar el estado de la transacción. Intenta de nuevo con «Consultar estado».';
export const EXPIRED_CHECK = 'El tiempo de la transacción terminó. Se consultó su estado en el servidor.';
export const POLLING_STOPPED = 'La actualización automática se detuvo. Usa «Consultar estado» para ver el estado actual.';

/** Every 20 s while INITIATED (a transaction lasts 15 minutes: at most 45 automatic reads plus the expiry). */
export const POLL_INTERVAL_MS = 20_000;
export const MAX_POLLS = 50;
/** After the local expires_at, the server is asked once its clock has surely reached it too. */
export const EXPIRY_GRACE_MS = 2_000;

export const OUTCOMES: readonly DemoOutcome[] = ['APPROVED', 'REJECTED', 'FAILED'];
export const OUTCOME_LABELS: Record<DemoOutcome, string> = {
  APPROVED: 'Simular aprobación',
  REJECTED: 'Simular rechazo',
  FAILED: 'Simular error de procesamiento'
};
export const OUTCOME_HINTS: Record<DemoOutcome, string> = {
  APPROVED: 'Registra el pago en tu historial y lo aplica a la obligación.',
  REJECTED: 'No aplica ningún pago: la obligación sigue pendiente.',
  FAILED: 'Simula una falla: no aplica ningún pago.'
};

export const TRANSACTION_STATUS_LABELS: Record<DemoTransactionStatus, string> = {
  INITIATED: 'En curso',
  APPROVED: 'Aprobado',
  REJECTED: 'Rechazado',
  FAILED: 'Fallido',
  CANCELLED: 'Cancelado',
  EXPIRED: 'Vencido'
};

/** Status badge: always with its text, never color alone. */
export const TRANSACTION_STATUS_BADGES: Record<DemoTransactionStatus, string> = {
  INITIATED: 'bg-sky-50 text-sky-900 ring-sky-200',
  APPROVED: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  REJECTED: 'bg-red-50 text-red-800 ring-red-200',
  FAILED: 'bg-red-50 text-red-800 ring-red-200',
  CANCELLED: 'bg-slate-100 text-slate-800 ring-slate-300',
  EXPIRED: 'bg-amber-50 text-amber-900 ring-amber-200'
};

export const RESULT_TEXTS: Record<Exclude<DemoTransactionStatus, 'INITIATED'>, { title: string; text: string }> = {
  APPROVED: {
    title: 'Pago aprobado y registrado',
    text: 'La pasarela de demostración aprobó el pago. La aplicación lo registró en tu historial y lo aplicó a la ' +
      'obligación seleccionada. No se movió dinero real.'
  },
  REJECTED: {
    title: 'Pago rechazado',
    text: 'La pasarela de demostración rechazó el pago. No se aplicó ningún pago y la obligación sigue pendiente.'
  },
  FAILED: {
    title: 'El pago no pudo aplicarse',
    text: 'La operación terminó con un error. No se registró ningún pago: la obligación no debe considerarse pagada.'
  },
  CANCELLED: {
    title: 'Operación cancelada',
    text: 'Cancelaste la operación antes de completarla. No se aplicó ningún pago.'
  },
  EXPIRED: {
    title: 'Transacción vencida',
    text: 'La transacción venció 15 minutos después de iniciarse sin completarse. No se aplicó ningún pago.'
  }
};

export const IN_PROGRESS_TEXT = {
  title: 'Transacción de demostración en curso',
  text: 'Elige el resultado que debe simular la pasarela. El estado que se muestra es siempre el que responde el servidor.'
};

/** The heading of a transaction: the gateway while INITIATED, otherwise the result the server answered. */
export function transactionHeading(transaction: DemoTransaction): { title: string; text: string } {
  return transaction.status === 'INITIATED' ? IN_PROGRESS_TEXT : RESULT_TEXTS[transaction.status];
}

export const HISTORY_PATH = '/resident/payments';

export function paymentPath(paymentId: number): string {
  return `${HISTORY_PATH}/${paymentId}`;
}

/**
 * HU-010: the receipt of the payment, only when the server answered APPROVED with the persisted
 * payment's id; never for INITIATED, REJECTED, FAILED, CANCELLED or EXPIRED.
 */
export function receiptLinkOf(transaction: DemoTransaction): string | null {
  return transaction.status === 'APPROVED' && transaction.payment_id !== null ? demoReceiptPath(transaction.payment_id) : null;
}

// --- Amounts: exact decimal text, compared in cents (BigInt), never binary floating point ------------------

/** "150000.50" → 15000050n; null for anything that is not the API's decimal text. */
export function toCents(amount: string): bigint | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(amount);
  if (!match) return null;
  return BigInt(match[1]) * 100n + BigInt((match[2] ?? '').padEnd(2, '0'));
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

export function transactionLines(
  transaction: DemoTransaction,
  obligation: PayableObligation | null
): { label: string; value: string; mono?: boolean }[] {
  const lines: { label: string; value: string; mono?: boolean }[] = [
    { label: 'Referencia de demostración', value: transaction.reference, mono: true },
    { label: 'Monto', value: formatCop(transaction.amount) }
  ];
  if (obligation) lines.push({ label: 'Obligación', value: obligationTitle(obligation) });
  lines.push({ label: 'Iniciada', value: formatDateTime(transaction.created_at) });
  if (transaction.status === 'INITIATED') {
    lines.push({ label: 'Vence', value: formatDateTime(transaction.expires_at) });
  } else if (transaction.finished_at) {
    lines.push({ label: 'Finalizada', value: formatDateTime(transaction.finished_at) });
  }
  if (transaction.status === 'APPROVED' && transaction.receipt_number) {
    lines.push({ label: 'Número del pago en tu historial', value: transaction.receipt_number, mono: true });
  }
  return lines;
}

/** "?transaction=12" → 12; anything else (missing, 0, "abc", "1e3", too long) → null. */
export function parseTransactionParam(search: string): number | null {
  const value = new URLSearchParams(search).get('transaction');
  if (!value || !/^[1-9]\d{0,15}$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

// --- State ----------------------------------------------------------------------------------------------

export type DemoView =
  | { kind: 'loading' }
  | { kind: 'unavailable' }
  | { kind: 'load-error'; message: string }
  | { kind: 'list' }
  | { kind: 'form'; obligation: PayableObligation; amountText: string; error: string | null }
  | { kind: 'review'; obligation: PayableObligation; amount: string }
  /** INITIATED: the demo gateway; any other status: the result. */
  | { kind: 'transaction'; transaction: DemoTransaction; obligation: PayableObligation | null };

export type DemoBusy = 'loading' | 'starting' | DemoOutcome | 'cancelling' | 'checking' | null;

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
  /** The outcome of the last request is unknown: only "Consultar estado" is allowed. */
  mustCheck: boolean;
  /** The automatic reading of an INITIATED transaction is running. */
  polling: boolean;
}

export interface DemoApi {
  list(): Promise<{ items: PayableObligation[] }>;
  start(chargeId: number, amount: string): Promise<DemoTransaction>;
  get(id: number): Promise<DemoTransaction>;
  complete(id: number, outcome: DemoOutcome): Promise<DemoTransaction>;
  cancel(id: number): Promise<DemoTransaction>;
}

export interface DemoDeps {
  api: DemoApi;
  render: (state: DemoState) => void;
  /** Keeps the transaction id in the URL (null removes it), so a reload resumes it. */
  remember: (transactionId: number | null) => void;
  now?: () => number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

const isApiError = (error: unknown, status: number, code?: string): error is ApiError =>
  error instanceof ApiError && error.status === status && (code === undefined || error.code === code);

/** The route itself does not exist: the backend has the demo gateway disabled (never "obligation not found"). */
export function isUnavailable(error: unknown): boolean {
  return isApiError(error, 404, 'NOT_FOUND');
}

/** A message for the resident: the backend's own Spanish message for a known domain error, never a technical text. */
function messageOf(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return CONNECTION_ERROR;
  if (error.status === 403) return error.code === 'CSRF_FAILED' ? CSRF_MESSAGE : FORBIDDEN_MESSAGE;
  if (error.status >= 500 || error.code === 'UNEXPECTED_RESPONSE') return fallback;
  return error.message;
}

export function createDemoPaymentFlow(deps: DemoDeps) {
  const now = deps.now ?? (() => Date.now());
  const setTimer = deps.setTimer ?? ((callback: () => void, ms: number) => setTimeout(callback, ms));
  const clearTimer = deps.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  let state: DemoState = {
    view: { kind: 'loading' },
    obligations: [],
    busy: null,
    notice: null,
    sessionExpired: false,
    mustCheck: false,
    polling: false
  };
  /** Every obligation seen, by id: the result keeps its concept after it leaves the list (paid). */
  const known = new Map<number, PayableObligation>();
  let timer: unknown = null;
  let polls = 0;
  let disposed = false;
  /** The id whose local expiry was already confirmed with the server (then a POST may follow). */
  let expiryChecked: number | null = null;
  /** Sequence of the requests about the transaction and of the list: only the latest answer counts. */
  let transactionRequest = 0;
  let listRequest = 0;
  /** The transaction of the URL while it is being resumed (kept there until the server answers for it). */
  let resuming: number | null = null;
  /** The transaction whose reading aimed at its expiry was already sent: afterwards only the interval counts. */
  let expiryReadFor: number | null = null;

  /** A new request about the transaction: every answer to an earlier one becomes stale. */
  function nextTransactionRequest(): number {
    transactionRequest += 1;
    return transactionRequest;
  }

  function isLatest(request: number): boolean {
    return !disposed && request === transactionRequest;
  }

  function set(changes: Partial<DemoState>): void {
    if (disposed) return;
    state = { ...state, ...changes };
    deps.render(state);
  }

  function stopPolling(): void {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function current(): DemoTransaction | null {
    return state.view.kind === 'transaction' ? state.view.transaction : null;
  }

  function sessionExpired(): void {
    stopPolling();
    set({ busy: null, sessionExpired: true, polling: false, notice: { tone: 'error', text: SESSION_EXPIRED_MESSAGE } });
  }

  function unavailable(): void {
    stopPolling();
    deps.remember(null);
    set({ view: { kind: 'unavailable' }, busy: null, polling: false, notice: null, mustCheck: false });
  }

  function schedule(transaction: DemoTransaction): void {
    stopPolling();
    if (disposed || transaction.status !== 'INITIATED') {
      set({ polling: false });
      return;
    }
    if (polls >= MAX_POLLS) {
      set({ polling: false, notice: { tone: 'info', text: POLLING_STOPPED } });
      return;
    }
    // One reading aimed at the local expiry (plus a margin), once per transaction; if the server still
    // says INITIATED (its clock decides, the browser's may be ahead), the normal interval again: never
    // a burst of immediate readings.
    const untilExpiry = Date.parse(transaction.expires_at) - now() + EXPIRY_GRACE_MS;
    const atExpiry = expiryReadFor !== transaction.id && untilExpiry < POLL_INTERVAL_MS;
    const delay = atExpiry ? Math.max(0, untilExpiry) : POLL_INTERVAL_MS;
    timer = setTimer(() => {
      timer = null;
      void poll(transaction.id, atExpiry);
    }, delay);
    if (!state.polling) set({ polling: true });
  }

  async function poll(id: number, atExpiry = false): Promise<void> {
    if (disposed || current()?.id !== id) return;
    if (state.busy !== null) {
      // An action of the resident is running: its own answer decides; read again later.
      schedule(current()!);
      return;
    }
    if (atExpiry) expiryReadFor = id; // sent: the next readings wait the interval
    polls += 1;
    const request = nextTransactionRequest();
    try {
      const transaction = await deps.api.get(id);
      if (!isLatest(request) || current()?.id !== id) return; // an action or a reading started after it decides
      show(transaction);
    } catch (error) {
      if (!isLatest(request)) return;
      stopPolling();
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      set({ polling: false, mustCheck: true, notice: { tone: 'error', text: CHECK_ERROR } });
    }
  }

  /** The transaction as the server answered it. */
  function show(transaction: DemoTransaction, notice: DemoNotice | null = state.notice): void {
    const obligation = known.get(transaction.charge_id) ?? null;
    set({ view: { kind: 'transaction', transaction, obligation }, busy: null, mustCheck: false, notice });
    resuming = null;
    deps.remember(transaction.id);
    if (transaction.status === 'INITIATED') {
      schedule(transaction);
    } else {
      stopPolling();
      set({ polling: false });
      void refreshObligations(); // the balances changed (APPROVED) or the obligation is free again
    }
  }

  async function refreshObligations(): Promise<boolean> {
    const request = ++listRequest;
    try {
      const { items } = await deps.api.list();
      if (request !== listRequest) return false; // a newer list was asked for
      for (const item of items) known.set(item.charge_id, item);
      set({ obligations: items });
      return true;
    } catch (error) {
      if (request !== listRequest) return false;
      if (isApiError(error, 401)) sessionExpired();
      else if (isUnavailable(error)) unavailable();
      return false;
    }
  }

  /** The list; `forget` removes the transaction id of the URL (not while a reload is resuming it). */
  async function loadList(notice: DemoNotice | null = null, forget = true): Promise<void> {
    stopPolling();
    nextTransactionRequest(); // leaving the transaction: a late answer about it no longer shows it
    const request = ++listRequest;
    if (forget) {
      resuming = null;
      deps.remember(null);
    }
    set({ view: { kind: 'loading' }, busy: 'loading', notice, mustCheck: false, polling: false });
    try {
      const { items } = await deps.api.list();
      if (request !== listRequest || disposed) return;
      for (const item of items) known.set(item.charge_id, item);
      set({ view: { kind: 'list' }, obligations: items, busy: null });
    } catch (error) {
      if (request !== listRequest || disposed) return;
      if (isApiError(error, 401)) {
        set({ view: { kind: 'load-error', message: SESSION_EXPIRED_MESSAGE }, busy: null, sessionExpired: true, notice: null });
      } else if (isUnavailable(error)) {
        unavailable();
      } else {
        set({ view: { kind: 'load-error', message: messageOf(error, LOAD_ERROR) }, busy: null });
      }
    }
  }

  /** Reads a transaction of the resident (a reload with ?transaction=, "Continuar", "Consultar estado"). */
  async function check(id: number, notice: DemoNotice | null = null): Promise<void> {
    if (state.busy !== null) return;
    stopPolling();
    polls = 0;
    const request = nextTransactionRequest();
    set({ busy: 'checking', notice });
    try {
      const transaction = await deps.api.get(id);
      if (!isLatest(request)) return;
      show(transaction, notice);
    } catch (error) {
      if (!isLatest(request)) return;
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      if (isApiError(error, 404)) return loadList({ tone: 'error', text: TRANSACTION_MISSING });
      if (isApiError(error, 422)) return loadList({ tone: 'error', text: TRANSACTION_MISSING });
      set({ busy: null, mustCheck: true, notice: { tone: 'error', text: messageOf(error, CHECK_ERROR) } });
    }
  }

  /** After a 409 of complete or cancel: the server's state is what is shown. */
  async function reread(id: number, notice: DemoNotice): Promise<void> {
    const request = nextTransactionRequest();
    set({ busy: 'checking', notice });
    try {
      const transaction = await deps.api.get(id);
      if (!isLatest(request)) return;
      show(transaction, notice);
    } catch (error) {
      if (!isLatest(request)) return;
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      set({ busy: null, mustCheck: true, notice: { tone: 'error', text: `${notice.text} ${CHECK_ERROR}` } });
    }
  }

  /** The page opens: the list, then the transaction of the URL if there is one. */
  async function init(transactionId: number | null): Promise<void> {
    resuming = transactionId;
    await loadList(null, transactionId === null);
    if (transactionId !== null && state.view.kind === 'list') await check(transactionId);
  }

  async function finish(action: DemoOutcome | 'cancelling'): Promise<void> {
    const transaction = current();
    if (!transaction || transaction.status !== 'INITIATED' || state.busy !== null || state.mustCheck) return;
    if (state.sessionExpired) return;
    // Expired on this screen: ask the server before sending anything (its clock decides).
    if (now() >= Date.parse(transaction.expires_at) && expiryChecked !== transaction.id) {
      expiryChecked = transaction.id;
      return check(transaction.id, { tone: 'info', text: EXPIRED_CHECK });
    }
    stopPolling();
    const request = nextTransactionRequest(); // a reading already sent can no longer overwrite this answer
    set({ busy: action, notice: null });
    try {
      const answered = action === 'cancelling'
        ? await deps.api.cancel(transaction.id)
        : await deps.api.complete(transaction.id, action);
      if (!isLatest(request)) return;
      show(answered, null);
    } catch (error) {
      if (!isLatest(request)) return;
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      if (isApiError(error, 404)) return loadList({ tone: 'error', text: TRANSACTION_MISSING });
      if (isApiError(error, 409, 'DEMO_PAYMENT_NOT_APPLIED')) {
        // Approved by the gateway but not applied: never a receipt; the server's state (FAILED) is shown.
        return reread(transaction.id, { tone: 'error', text: error.message });
      }
      if (isApiError(error, 409, 'DEMO_TRANSACTION_EXPIRED')) return reread(transaction.id, { tone: 'info', text: error.message });
      if (isApiError(error, 409)) return reread(transaction.id, { tone: 'info', text: FINISHED_MESSAGE });
      if (error instanceof ApiError && (error.status === 403 || error.status === 422)) {
        set({ busy: null, notice: { tone: 'error', text: messageOf(error, UNCONFIRMED_MESSAGE) } });
        schedule(transaction);
        return;
      }
      // No answer (network, 5xx): it may or may not have been applied. Read it before anything else.
      set({ busy: null, mustCheck: true, polling: false, notice: { tone: 'error', text: UNCONFIRMED_MESSAGE } });
    }
  }

  return {
    get state(): DemoState {
      return state;
    },

    init,

    /**
     * "Reintentar" after the list failed: a transaction being resumed is still resumed (its id stays in the URL).
     * Never while a request is pending: a new list would make its answer (a confirmed approval) stale.
     */
    retry(): Promise<void> {
      if (state.busy !== null) return Promise.resolve();
      return resuming !== null ? init(resuming) : loadList();
    },

    select(chargeId: number): void {
      if (state.busy !== null || state.view.kind !== 'list') return;
      const obligation = state.obligations.find((item) => item.charge_id === chargeId);
      if (!obligation || toCents(obligation.outstanding_amount)! <= 0n) return; // only what still has a balance
      if (obligation.in_progress_transaction_id !== null) {
        void check(obligation.in_progress_transaction_id, { tone: 'info', text: RESUMED_IN_PROGRESS });
        return;
      }
      set({ view: { kind: 'form', obligation, amountText: amountInputText(obligation.outstanding_amount), error: null }, notice: null });
    },

    /** "Volver a la lista": nothing was started (or the transaction already ended). */
    back(): Promise<void> | void {
      if (state.busy !== null) return;
      const transaction = current();
      if (transaction?.status === 'INITIATED') return; // cancel it first, or let it expire
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

    async start(): Promise<void> {
      if (state.busy !== null || state.view.kind !== 'review' || state.sessionExpired) return;
      const { obligation, amount } = state.view;
      const request = nextTransactionRequest();
      set({ busy: 'starting', notice: null });
      try {
        polls = 0;
        const started = await deps.api.start(obligation.charge_id, amount); // exactly the text validated above
        if (!isLatest(request)) return;
        show(started, null);
      } catch (error) {
        if (!isLatest(request)) return;
        if (isApiError(error, 401)) return sessionExpired();
        if (isUnavailable(error)) return unavailable();
        if (isApiError(error, 422)) {
          const detail = error.details.find((d) => d.field?.replace(/^body\./, '') === 'amount');
          if (detail) {
            set({ view: { kind: 'form', obligation, amountText: amountInputText(amount), error: detail.message }, busy: null });
            return;
          }
          set({ busy: null, notice: { tone: 'error', text: INVALID_MESSAGE } });
          return;
        }
        if (isApiError(error, 409, 'DEMO_PAYMENT_IN_PROGRESS')) {
          // Perhaps the resident's own, started in another tab: offered to continue, never a second one.
          await loadList({ tone: 'error', text: error.message });
          const own = state.obligations.find((item) => item.charge_id === obligation.charge_id)?.in_progress_transaction_id;
          if (own) await check(own, { tone: 'info', text: RESUMED_IN_PROGRESS });
          return;
        }
        if (isApiError(error, 409, 'CHARGE_NOT_PAYABLE')) return loadList({ tone: 'error', text: error.message });
        if (isApiError(error, 404)) return loadList({ tone: 'error', text: OBLIGATION_GONE });
        set({ busy: null, notice: { tone: 'error', text: messageOf(error, START_ERROR) } });
      }
    },

    complete(outcome: DemoOutcome): Promise<void> {
      return finish(outcome);
    },

    cancel(): Promise<void> {
      return finish('cancelling');
    },

    /** "Consultar estado". */
    refresh(): Promise<void> {
      const transaction = current();
      if (!transaction) return Promise.resolve();
      return check(transaction.id);
    },

    /** The page is left: no more timers or renders. */
    dispose(): void {
      disposed = true;
      stopPolling();
    }
  };
}

export type DemoPaymentFlow = ReturnType<typeof createDemoPaymentFlow>;
