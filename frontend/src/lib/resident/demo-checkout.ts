import type {
  DemoInstrument,
  DemoInstrumentKind,
  DemoResultReason,
  DemoTransaction,
  DemoTransactionStatus,
  PayableObligation
} from '../api/demo-payments.ts';
import { formatCop, formatDateTime } from './payment-format.ts';
import { DEMO_RECEIPT_LINK, demoReceiptPath } from './payment-detail.ts';
import {
  CANCELLED_PATH,
  INVALID_MESSAGE,
  OBLIGATION_GONE,
  RESUMED_IN_PROGRESS,
  SESSION_EXPIRED_MESSAGE,
  START_ERROR,
  amountErrorOf,
  checkoutPath,
  isApiError,
  isUnavailable,
  messageOf,
  obligationTitle,
  type DemoNotice
} from './demo-payment.ts';

export { DEMO_RECEIPT_LINK };

/**
 * HU-010-D: the checkout of one demo transaction (/resident/demo-payments/checkout?transaction=<id>;
 * pure, tested with node --test). The obligations page (demo-payment.ts) starts the transaction and
 * opens this page; a reload opens it again from the URL.
 *
 * transaction (read from the server) → choose a fictitious test instrument of the backend's catalog →
 * processing → the result the server answers; or cancel. After a simulated rejection or error, "Intentar
 * con otro instrumento" starts a NEW transaction (the same endpoint as the obligations page, for the same
 * obligation and amount) and opens its checkout: a finished transaction is never reused.
 *
 * The backend is the only authority: the transaction, its status, expiry, instrument and reason, the
 * payment, which instruments exist and the obligation's data. Processing sends only the id of an
 * instrument the server listed; its expected_result describes the instrument and is never taken as the
 * result. The processing view lasts at least MIN_PROCESSING_MS only when the server answers sooner (the
 * request itself is never delayed, and its answer never changed). Without an answer (network), the
 * transaction is read again before anything is shown, never sent again. While INITIATED its state is read
 * every POLL_INTERVAL_MS, at most MAX_POLLS times and never after a terminal state, an error that needs
 * the resident or dispose(). Nothing is stored in the browser: the id travels in the URL.
 *
 * Answers arrive in any order: every request that reads or changes the transaction takes a new sequence
 * number and only the latest one's answer is shown; the list of obligations and the catalog have their
 * own sequences. An older answer (or its error) is dropped.
 */

export const CHECKOUT_LOADING = 'Cargando la transacción de demostración...';
export const MISSING_TITLE = 'No se indicó ninguna transacción de demostración.';
export const MISSING_TEXT = 'Vuelve a tus obligaciones para iniciar un pago o continuar uno en curso.';
export const TRANSACTION_MISSING = 'La transacción no existe o no pertenece a tu cuenta.';
export const LOAD_TRANSACTION_ERROR = 'No fue posible cargar la transacción de demostración. Intenta de nuevo.';
export const FINISHED_MESSAGE = 'La transacción ya había terminado. Este es su estado actual.';
export const UNCONFIRMED_MESSAGE =
  'No fue posible confirmar el resultado con el servidor. Consulta el estado de la transacción antes de intentarlo de nuevo.';
export const UNCONFIRMED_CHECKED =
  'No llegó la respuesta del pago. Se consultó la transacción en el servidor: este es su estado actual.';
export const CHECK_ERROR = 'No fue posible consultar el estado de la transacción. Intenta de nuevo con «Consultar estado».';
export const EXPIRED_CHECK = 'El tiempo de la transacción terminó. Se consultó su estado en el servidor.';
export const POLLING_STOPPED = 'La actualización automática se detuvo. Usa «Consultar estado» para ver el estado actual.';
export const PROCESSING_MESSAGE = 'Procesando el pago de demostración…';
export const PROCESSING_HINT = 'El resultado lo confirma el servidor. No es necesario enviar el pago de nuevo.';
export const APPROVAL_EFFECT =
  'Si el servidor aprueba la simulación, la aplicación registra internamente un pago en tu historial y lo aplica a ' +
  'esta obligación. El rechazo, el error simulado y la cancelación no aplican ningún pago.';
export const NOT_LISTED_NOTE =
  'Esta obligación ya no figura entre tus obligaciones pendientes (por ejemplo, porque quedó pagada). Se muestran los ' +
  'datos guardados de la transacción.';
export const RETRY_LABEL = 'Intentar con otro instrumento';

export function retryHint(transaction: DemoTransaction): string {
  return `Se iniciará una nueva transacción de demostración por ${formatCop(transaction.amount)} para la misma ` +
    'obligación. Esta transacción ya terminó y no se reutiliza.';
}

/** Every 20 s while INITIATED (a transaction lasts 15 minutes: at most 45 automatic reads plus the expiry). */
export const POLL_INTERVAL_MS = 20_000;
export const MAX_POLLS = 50;
/** After the local expires_at, the server is asked once its clock has surely reached it too. */
export const EXPIRY_GRACE_MS = 2_000;
/** The processing view stays at least this long, only when the server answers sooner. */
export const MIN_PROCESSING_MS = 500;

// --- Steps --------------------------------------------------------------------------------------------------

export const CHECKOUT_STEPS = ['Obligación y monto', 'Instrumento de prueba', 'Procesamiento', 'Resultado'] as const;

/** What the checkout shows: choosing, processing, the result, or a result the server has not confirmed yet. */
export type CheckoutStep = 'select' | 'processing' | 'uncertain' | 'result';

/** The index in CHECKOUT_STEPS of the current step (the first one, the amount, was done on the obligations page). */
export function stepIndex(step: CheckoutStep): number {
  return step === 'select' ? 1 : step === 'result' ? 3 : 2;
}

// --- Test instruments: the backend's catalog, shown as it is sent (never a copy kept here) ----------------------

export const INSTRUMENTS_TITLE = 'Instrumento de prueba ficticio';
export const INSTRUMENTS_HINT =
  'Elige con qué instrumento ficticio la pasarela de demostración simulará el pago. Ninguno es una tarjeta ni una ' +
  'cuenta real, y no se te pedirá ningún dato de pago.';
export const INSTRUMENTS_LOADING = 'Cargando los instrumentos de prueba...';
export const INSTRUMENTS_ERROR = 'No fue posible cargar los instrumentos de prueba. Intenta de nuevo.';
export const INSTRUMENTS_EMPTY = 'No hay instrumentos de prueba disponibles en este momento.';
export const PROCESS_LABEL = 'Procesar pago de demostración';
export const SELECT_FIRST = 'Selecciona un instrumento de prueba para continuar.';

export const INSTRUMENT_KIND_LABELS: Record<DemoInstrumentKind, string> = {
  CARD: 'Tarjeta de prueba',
  ACCOUNT: 'Cuenta de prueba'
};

/** What the instrument simulates (a description of the instrument, never the transaction's result). */
export const EXPECTED_RESULT_LABELS: Record<DemoInstrument['expected_result'], string> = {
  APPROVED: 'Simula una aprobación',
  REJECTED: 'Simula un rechazo',
  FAILED: 'Simula un error de procesamiento'
};

/** The details of an instrument, below its name: kind, fictitious ending, description, what it simulates. */
export function instrumentDetails(instrument: DemoInstrument): string[] {
  const kind = INSTRUMENT_KIND_LABELS[instrument.kind] ?? 'Instrumento de prueba';
  return [
    instrument.ending ? `${kind} · terminación ficticia ${instrument.ending}` : kind,
    instrument.description,
    `Resultado esperado: ${(EXPECTED_RESULT_LABELS[instrument.expected_result] ?? '').toLowerCase()}.`
  ];
}

/** The name of the instrument that resolved a transaction: its label in the catalog, or its id if it is no longer listed. */
export function instrumentName(instrumentId: string, instruments: readonly DemoInstrument[]): string {
  return instruments.find((instrument) => instrument.id === instrumentId)?.label ?? instrumentId;
}

// --- Results ------------------------------------------------------------------------------------------------

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

/**
 * The kind of a terminal result, from the status and the reason the server stored: a simulated rejection
 * or error is never presented as the backend's own failure to apply the payment (NOT_APPLIED), nor the reverse.
 */
export type ResultKind = 'approved' | 'rejected' | 'technical' | 'not-applied' | 'failed' | 'cancelled' | 'expired';

export function resultKind(transaction: DemoTransaction): ResultKind | null {
  switch (transaction.status) {
    case 'INITIATED':
      return null;
    case 'APPROVED':
      return 'approved';
    case 'REJECTED':
      return 'rejected';
    case 'FAILED':
      return transaction.result_reason === 'TECHNICAL_ERROR' ? 'technical'
        : transaction.result_reason === 'NOT_APPLIED' ? 'not-applied' : 'failed';
    case 'CANCELLED':
      return 'cancelled';
    case 'EXPIRED':
      return 'expired';
  }
}

export const RESULT_TEXTS: Record<ResultKind, { title: string; text: string }> = {
  approved: {
    title: 'Pago aprobado y registrado',
    text: 'La pasarela de demostración aprobó el pago. La aplicación lo registró en tu historial y lo aplicó a la ' +
      'obligación seleccionada. No se movió dinero real.'
  },
  rejected: {
    title: 'Pago rechazado',
    text: 'La pasarela de demostración rechazó el pago. No se aplicó ningún pago y la obligación sigue pendiente.'
  },
  technical: {
    title: 'Error de procesamiento simulado',
    text: 'La pasarela de demostración simuló un error técnico. No se registró ningún pago: la obligación no debe ' +
      'considerarse pagada.'
  },
  'not-applied': {
    title: 'El pago no pudo aplicarse',
    text: 'No se registró ningún pago: la obligación no debe considerarse pagada.'
  },
  failed: {
    title: 'El pago no pudo aplicarse',
    text: 'La operación terminó con un error. No se registró ningún pago: la obligación no debe considerarse pagada.'
  },
  cancelled: {
    title: 'Operación cancelada',
    text: 'La operación se canceló antes de completarse. No se aplicó ningún pago.'
  },
  expired: {
    title: 'Transacción vencida',
    text: 'La transacción venció 15 minutos después de iniciarse sin completarse. No se aplicó ningún pago.'
  }
};

export const IN_PROGRESS_TEXT = {
  title: 'Elige un instrumento de prueba',
  text: 'Elige un instrumento de prueba ficticio y procesa el pago. El resultado lo decide el servidor: el estado que ' +
    'se muestra es siempre el que responde.'
};
export const UNCERTAIN_TEXT = { title: 'No fue posible confirmar el resultado', text: UNCONFIRMED_MESSAGE };

/** The heading of a transaction: the choice while INITIATED, otherwise the result the server answered. */
export function transactionHeading(transaction: DemoTransaction): { title: string; text: string } {
  const kind = resultKind(transaction);
  return kind === null ? IN_PROGRESS_TEXT : RESULT_TEXTS[kind];
}

/**
 * Why the transaction ended as it did, as the server stored it (result_reason). A simulated reason
 * says so; NOT_APPLIED is the backend's own decision (the payment could not be applied), never a simulation.
 */
export const RESULT_REASON_TEXTS: Record<DemoResultReason, string> = {
  INSUFFICIENT_FUNDS:
    'Motivo simulado: fondos insuficientes en la tarjeta de prueba ficticia. Es un resultado de demostración: ' +
    'no se consultó ningún banco ni se intentó un cobro real.',
  TECHNICAL_ERROR:
    'Motivo simulado: error técnico de procesamiento de la pasarela de demostración. No hubo ninguna falla real ' +
    'de un proveedor financiero.',
  NOT_APPLIED:
    'La pasarela de demostración aprobó la simulación, pero la aplicación no pudo aplicar el pago (por ejemplo, ' +
    'porque el saldo de la obligación cambió). No se registró ningún pago y tu cuenta no cambió.'
};

export function resultReasonText(transaction: DemoTransaction): string | null {
  return transaction.result_reason ? RESULT_REASON_TEXTS[transaction.result_reason] ?? null : null;
}

/**
 * HU-010-E: the receipt of the payment, only when the server answered APPROVED with the persisted
 * payment's id; never for INITIATED, REJECTED, FAILED, CANCELLED or EXPIRED.
 */
export function receiptLinkOf(transaction: DemoTransaction): string | null {
  return transaction.status === 'APPROVED' && transaction.payment_id !== null ? demoReceiptPath(transaction.payment_id) : null;
}

/** The summary: the transaction as the server keeps it, and the obligation only while the server lists it. */
export function transactionLines(
  transaction: DemoTransaction,
  obligation: PayableObligation | null,
  instruments: readonly DemoInstrument[] = []
): { label: string; value: string; mono?: boolean }[] {
  const lines: { label: string; value: string; mono?: boolean }[] = [];
  if (obligation) lines.push({ label: 'Obligación', value: obligationTitle(obligation) });
  lines.push(
    { label: 'Monto', value: formatCop(transaction.amount) },
    { label: 'Referencia de demostración', value: transaction.reference, mono: true }
  );
  if (obligation) lines.push({ label: 'Saldo pendiente actual', value: formatCop(obligation.outstanding_amount) });
  lines.push({ label: 'Iniciada', value: formatDateTime(transaction.created_at) });
  if (transaction.status === 'INITIATED') {
    lines.push({ label: 'Vence', value: formatDateTime(transaction.expires_at) });
  } else if (transaction.finished_at) {
    lines.push({ label: 'Finalizada', value: formatDateTime(transaction.finished_at) });
  }
  // Only the instrument the server stored: none for a cancelled, expired or older transaction (never guessed).
  if (transaction.test_instrument) {
    lines.push({ label: 'Instrumento de prueba', value: instrumentName(transaction.test_instrument, instruments) });
  }
  if (transaction.status === 'APPROVED' && transaction.receipt_number) {
    lines.push({ label: 'Número del pago en tu historial', value: transaction.receipt_number, mono: true });
  }
  return lines;
}

/** "?resumed=1": the checkout was opened for a transaction already in progress. */
export function parseResumedParam(search: string): boolean {
  return new URLSearchParams(search).get('resumed') === '1';
}

// --- State ----------------------------------------------------------------------------------------------

export type CheckoutView =
  | { kind: 'loading' }
  /** No (valid) ?transaction= in the URL: nothing is requested. */
  | { kind: 'missing' }
  /** The server has no such transaction of this resident (missing or another resident's: the same answer). */
  | { kind: 'not-found' }
  | { kind: 'unavailable' }
  | { kind: 'load-error'; message: string }
  | { kind: 'transaction'; transaction: DemoTransaction };

/** "opening": another page is being opened (a new checkout, or the obligations after a cancellation). */
export type CheckoutBusy = 'loading' | 'processing' | 'cancelling' | 'checking' | 'retrying' | 'opening' | null;

/** The backend's catalog of test instruments: read once, when a transaction that needs it is shown. */
export interface DemoCatalog {
  status: 'idle' | 'loading' | 'ready' | 'error';
  items: DemoInstrument[];
  error: string | null;
}

export interface CheckoutState {
  view: CheckoutView;
  /** The transaction's obligation as the last list read sends it; null when it is not listed (or not read). */
  obligation: PayableObligation | null;
  /** Whether the last list read lists the obligation: null until a list was read. */
  listed: boolean | null;
  /** What is being sent: every action is disabled meanwhile (no double submission). */
  busy: CheckoutBusy;
  notice: DemoNotice | null;
  sessionExpired: boolean;
  /** The outcome of the last request is unknown: only "Consultar estado" is allowed. */
  mustCheck: boolean;
  /** The automatic reading of an INITIATED transaction is running. */
  polling: boolean;
  catalog: DemoCatalog;
  /** The id of the instrument chosen for the INITIATED transaction shown (one the server listed), or null. */
  selectedInstrument: string | null;
}

export interface CheckoutApi {
  get(id: number): Promise<DemoTransaction>;
  list(): Promise<{ items: PayableObligation[] }>;
  instruments(): Promise<{ items: DemoInstrument[] }>;
  complete(id: number, instrumentId: string): Promise<DemoTransaction>;
  cancel(id: number): Promise<DemoTransaction>;
  start(chargeId: number, amount: string): Promise<DemoTransaction>;
}

export interface CheckoutDeps {
  api: CheckoutApi;
  render: (state: CheckoutState) => void;
  /** Opens another page of the application (a new checkout, the obligations). */
  navigate: (path: string) => void;
  now?: () => number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  minProcessingMs?: number;
}

function transactionOf(state: CheckoutState): DemoTransaction | null {
  return state.view.kind === 'transaction' ? state.view.transaction : null;
}

function live(state: CheckoutState): boolean {
  return transactionOf(state)?.status === 'INITIATED' && state.busy === null && !state.mustCheck && !state.sessionExpired;
}

/** The selected instrument is one the server listed (exact id), or null. */
function selectedOf(state: CheckoutState): DemoInstrument | null {
  if (state.catalog.status !== 'ready' || state.selectedInstrument === null) return null;
  return state.catalog.items.find((instrument) => instrument.id === state.selectedInstrument) ?? null;
}

/** What the checkout shows now; null while there is no transaction to show. */
export function checkoutStep(state: CheckoutState): CheckoutStep | null {
  const transaction = transactionOf(state);
  if (!transaction) return null;
  if (state.busy === 'processing') return 'processing';
  if (transaction.status !== 'INITIATED') return 'result';
  return state.mustCheck ? 'uncertain' : 'select';
}

/** "Procesar pago de demostración" is possible: an INITIATED transaction, a listed instrument chosen, nothing pending. */
export function canProcess(state: CheckoutState): boolean {
  return live(state) && selectedOf(state) !== null;
}

/** The instruments can be chosen: an INITIATED transaction, the catalog loaded and nothing pending. */
export function canChooseInstrument(state: CheckoutState): boolean {
  return live(state) && state.catalog.status === 'ready';
}

export function canCancel(state: CheckoutState): boolean {
  return live(state);
}

/**
 * "Intentar con otro instrumento": after a simulated rejection or error (never after an approval, a
 * cancellation, an expiry or NOT_APPLIED, where the obligation itself has to be reviewed again).
 */
export function isRetryable(transaction: DemoTransaction): boolean {
  return transaction.status === 'REJECTED' || (transaction.status === 'FAILED' && transaction.result_reason !== 'NOT_APPLIED');
}

export function canRetryWithAnother(state: CheckoutState): boolean {
  const transaction = transactionOf(state);
  return transaction !== null && state.busy === null && !state.sessionExpired && isRetryable(transaction);
}

export function createDemoCheckoutFlow(deps: CheckoutDeps) {
  const now = deps.now ?? (() => Date.now());
  const setTimer = deps.setTimer ?? ((callback: () => void, ms: number) => setTimeout(callback, ms));
  const clearTimer = deps.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  const minProcessingMs = deps.minProcessingMs ?? MIN_PROCESSING_MS;

  let state: CheckoutState = {
    view: { kind: 'loading' },
    obligation: null,
    listed: null,
    busy: null,
    notice: null,
    sessionExpired: false,
    mustCheck: false,
    polling: false,
    catalog: { status: 'idle', items: [], error: null },
    selectedInstrument: null
  };
  let transactionId: number | null = null;
  let timer: unknown = null;
  /** The pending wait of the processing view (cleared by dispose()). */
  let delayTimer: unknown = null;
  let polls = 0;
  let disposed = false;
  /** The id whose local expiry was already confirmed with the server (then a POST may follow). */
  let expiryChecked: number | null = null;
  /** Sequences: only the latest answer of each counts. */
  let transactionRequest = 0;
  let listRequest = 0;
  let catalogRequest = 0;
  /** The transaction whose reading aimed at its expiry was already sent: afterwards only the interval counts. */
  let expiryReadFor: number | null = null;

  function nextTransactionRequest(): number {
    transactionRequest += 1;
    return transactionRequest;
  }

  function isLatest(request: number): boolean {
    return !disposed && request === transactionRequest;
  }

  function set(changes: Partial<CheckoutState>): void {
    if (disposed) return;
    state = { ...state, ...changes };
    deps.render(state);
  }

  function stopPolling(): void {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function current(): DemoTransaction | null {
    return transactionOf(state);
  }

  function open(path: string): void {
    stopPolling();
    set({ busy: 'opening', polling: false });
    deps.navigate(path);
  }

  function sessionExpired(): void {
    stopPolling();
    set({ busy: null, sessionExpired: true, polling: false, notice: { tone: 'error', text: SESSION_EXPIRED_MESSAGE } });
  }

  function unavailable(): void {
    stopPolling();
    set({ view: { kind: 'unavailable' }, busy: null, polling: false, notice: null, mustCheck: false });
  }

  function notFound(): void {
    stopPolling();
    set({ view: { kind: 'not-found' }, busy: null, polling: false, notice: null, mustCheck: false, selectedInstrument: null });
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
    // The choice belongs to the INITIATED transaction it was made for: never carried to a result.
    const before = current();
    const keep = transaction.status === 'INITIATED' && before?.id === transaction.id;
    set({
      view: { kind: 'transaction', transaction }, busy: null, mustCheck: false, notice,
      selectedInstrument: keep ? state.selectedInstrument : null
    });
    // The catalog: to choose an instrument, or to name the one that resolved this transaction.
    if ((transaction.status === 'INITIATED' || transaction.test_instrument !== null) && state.catalog.status === 'idle') {
      void loadInstruments();
    }
    if (transaction.status === 'INITIATED') {
      schedule(transaction);
    } else {
      stopPolling();
      set({ polling: false });
      // It has just ended here: the obligation's balance changed (APPROVED) or it is free again.
      if (before?.id === transaction.id && before.status === 'INITIATED') void refreshObligation(transaction.charge_id);
    }
  }

  /** The obligation of the transaction, only as the server lists it (never invented when it is not listed). */
  async function refreshObligation(chargeId: number): Promise<PayableObligation[] | null> {
    const request = ++listRequest;
    try {
      const { items } = await deps.api.list();
      if (request !== listRequest || disposed) return null;
      const obligation = items.find((item) => item.charge_id === chargeId) ?? null;
      set({ obligation, listed: obligation !== null });
      return items;
    } catch (error) {
      if (request !== listRequest || disposed) return null;
      if (isApiError(error, 401)) sessionExpired();
      else if (isUnavailable(error)) unavailable();
      return null; // the summary keeps the transaction's own data
    }
  }

  /**
   * The backend's test instruments, in its order. Its own track: it never blocks the transaction's actions
   * (cancel, "Consultar estado") and an older answer never overwrites a newer one.
   */
  async function loadInstruments(): Promise<void> {
    if (disposed || state.sessionExpired) return;
    const request = ++catalogRequest;
    set({ catalog: { status: 'loading', items: state.catalog.items, error: null } });
    try {
      const { items } = await deps.api.instruments();
      if (request !== catalogRequest) return;
      // A choice that is no longer listed is dropped (never sent).
      const selected = items.some((instrument) => instrument.id === state.selectedInstrument) ? state.selectedInstrument : null;
      set({ catalog: { status: 'ready', items, error: null }, selectedInstrument: selected });
    } catch (error) {
      if (request !== catalogRequest || disposed) return;
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      set({ catalog: { status: 'error', items: [], error: messageOf(error, INSTRUMENTS_ERROR) }, selectedInstrument: null });
    }
  }

  /** The transaction of the URL, from the server (the page opens, a reload, "Reintentar"). */
  async function load(notice: DemoNotice | null): Promise<void> {
    if (transactionId === null) return;
    const id = transactionId;
    stopPolling();
    polls = 0;
    const request = nextTransactionRequest();
    set({ view: { kind: 'loading' }, busy: 'loading', notice: null, mustCheck: false, polling: false });
    const transaction = deps.api.get(id);
    const listing = transaction.then((t) => refreshObligation(t.charge_id), () => null);
    try {
      const answered = await transaction;
      if (!isLatest(request)) return;
      // "Puedes continuarlo aquí" only while it can still be continued.
      show(answered, answered.status === 'INITIATED' ? notice : null);
      await listing;
    } catch (error) {
      if (!isLatest(request)) return;
      if (isApiError(error, 401)) {
        set({ view: { kind: 'load-error', message: SESSION_EXPIRED_MESSAGE }, busy: null, sessionExpired: true, notice: null });
        return;
      }
      if (isUnavailable(error)) return unavailable();
      // Another resident's transaction answers exactly like a missing one: nothing of it is shown.
      if (isApiError(error, 404) || isApiError(error, 422)) return notFound();
      set({ view: { kind: 'load-error', message: messageOf(error, LOAD_TRANSACTION_ERROR) }, busy: null });
    }
  }

  /** "Consultar estado", and the reading before sending anything once the transaction expired on screen. */
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
      if (isApiError(error, 404) || isApiError(error, 422)) return notFound();
      set({ busy: null, mustCheck: true, notice: { tone: 'error', text: messageOf(error, CHECK_ERROR) } });
    }
  }

  /**
   * After a 409 or no answer to a processing or a cancellation: the server's state is what is shown.
   * `busy` keeps the view of the action (the processing view stays until the state is known).
   */
  async function reread(id: number, notice: DemoNotice, busy: CheckoutBusy, failure: DemoNotice): Promise<void> {
    const request = nextTransactionRequest();
    set({ busy, notice });
    try {
      const transaction = await deps.api.get(id);
      if (!isLatest(request)) return;
      show(transaction, notice);
    } catch (error) {
      if (!isLatest(request)) return;
      if (isApiError(error, 401)) return sessionExpired();
      if (isUnavailable(error)) return unavailable();
      // Still unknown: only "Consultar estado"; nothing is sent again.
      set({ busy: null, mustCheck: true, polling: false, notice: failure });
    }
  }

  /** The processing view lasts at least minProcessingMs: waits only for what is left (nothing if the server took longer). */
  function minimumView(startedAt: number): Promise<void> {
    const remaining = minProcessingMs - (now() - startedAt);
    if (remaining <= 0 || disposed) return Promise.resolve();
    return new Promise((resolve) => {
      delayTimer = setTimer(() => {
        delayTimer = null;
        resolve();
      }, remaining);
    });
  }

  async function finish(action: 'processing' | 'cancelling'): Promise<void> {
    const transaction = current();
    if (!transaction || transaction.status !== 'INITIATED' || state.busy !== null || state.mustCheck) return;
    if (state.sessionExpired) return;
    // Processing needs an instrument the server listed: nothing is sent without one.
    const instrument = action === 'processing' ? selectedOf(state) : null;
    if (action === 'processing' && instrument === null) return;
    // Expired on this screen: ask the server before sending anything (its clock decides).
    if (now() >= Date.parse(transaction.expires_at) && expiryChecked !== transaction.id) {
      expiryChecked = transaction.id;
      return check(transaction.id, { tone: 'info', text: EXPIRED_CHECK });
    }
    stopPolling();
    const request = nextTransactionRequest(); // a reading already sent can no longer overwrite this answer
    set({ busy: action, notice: null, polling: false });
    const startedAt = now();
    let outcome: { answered: DemoTransaction } | { error: unknown };
    try {
      // Only the instrument's id: the result is the server's answer, never its expected_result.
      outcome = { answered: instrument === null ? await deps.api.cancel(transaction.id) : await deps.api.complete(transaction.id, instrument.id) };
    } catch (error) {
      outcome = { error };
    }
    if (!isLatest(request)) return;
    if (action === 'processing') {
      await minimumView(startedAt);
      if (!isLatest(request)) return;
    }
    if ('answered' in outcome) {
      // A cancellation confirmed by the server: back to the obligations (they say so).
      if (action === 'cancelling' && outcome.answered.status === 'CANCELLED') return open(CANCELLED_PATH);
      return show(outcome.answered, null);
    }
    const { error } = outcome;
    if (isApiError(error, 401)) return sessionExpired();
    if (isUnavailable(error)) return unavailable();
    if (isApiError(error, 404)) return notFound();
    const view = action === 'processing' ? 'processing' : 'checking';
    if (isApiError(error, 409, 'DEMO_PAYMENT_NOT_APPLIED')) {
      // Approved by the gateway but not applied: never a receipt; the server's state (FAILED) is shown.
      const notice: DemoNotice = { tone: 'error', text: error.message };
      return reread(transaction.id, notice, view, { tone: 'error', text: `${notice.text} ${CHECK_ERROR}` });
    }
    if (isApiError(error, 409)) {
      // Finished or expired meanwhile: its real result, never the cancellation or the payment assumed.
      const notice: DemoNotice = { tone: 'info', text: error.code === 'DEMO_TRANSACTION_EXPIRED' ? error.message : FINISHED_MESSAGE };
      return reread(transaction.id, notice, view, { tone: 'error', text: `${notice.text} ${CHECK_ERROR}` });
    }
    const rejected = isApiError(error, 422) ? error.details.find((d) => d.field?.replace(/^body\./, '') === 'instrument_id') : undefined;
    if (rejected) {
      // The instrument is no longer available: its message, the choice dropped and the catalog read again.
      set({ busy: null, selectedInstrument: null, notice: { tone: 'error', text: rejected.message } });
      schedule(transaction);
      void loadInstruments();
      return;
    }
    if (isApiError(error, 403) || isApiError(error, 422)) {
      set({ busy: null, notice: { tone: 'error', text: messageOf(error, UNCONFIRMED_MESSAGE) } });
      schedule(transaction);
      return;
    }
    // No answer (network, 5xx): it may or may not have been applied. Read it before showing anything.
    return reread(transaction.id, { tone: 'info', text: UNCONFIRMED_CHECKED }, view, { tone: 'error', text: UNCONFIRMED_MESSAGE });
  }

  return {
    get state(): CheckoutState {
      return state;
    },

    /** The page opens with the id of its URL (null: none or invalid; nothing is requested). */
    init(id: number | null, { resumed = false } = {}): Promise<void> {
      if (id === null) {
        set({ view: { kind: 'missing' }, busy: null });
        return Promise.resolve();
      }
      transactionId = id;
      return load(resumed ? { tone: 'info', text: RESUMED_IN_PROGRESS } : null);
    },

    /** "Reintentar" after the transaction could not be read. */
    retry(): Promise<void> {
      if (state.busy !== null || state.view.kind !== 'load-error' || state.sessionExpired) return Promise.resolve();
      return load(null);
    },

    /** Chooses an instrument of the server's catalog for the INITIATED transaction (an unknown id is ignored). */
    selectInstrument(instrumentId: string): void {
      if (!canChooseInstrument(state)) return;
      if (!state.catalog.items.some((instrument) => instrument.id === instrumentId)) return;
      if (state.selectedInstrument !== instrumentId) set({ selectedInstrument: instrumentId });
    },

    /** "Reintentar" of the catalog after it could not be read. */
    retryInstruments(): Promise<void> {
      if (state.catalog.status === 'loading' || state.catalog.status === 'ready') return Promise.resolve();
      return loadInstruments();
    },

    /** "Procesar pago de demostración": the chosen instrument's id, once; nothing without a choice. */
    process(): Promise<void> {
      return finish('processing');
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

    /**
     * "Intentar con otro instrumento": a NEW transaction for the same obligation and amount (the normal start,
     * with all its checks on the server), then its checkout. The finished one is never sent again.
     */
    async retryWithAnother(): Promise<void> {
      if (!canRetryWithAnother(state)) return;
      const finished = current()!;
      set({ busy: 'retrying', notice: null });
      try {
        const started = await deps.api.start(finished.charge_id, finished.amount);
        if (disposed) return;
        open(checkoutPath(started.id));
      } catch (error) {
        if (disposed) return;
        if (isApiError(error, 401)) return sessionExpired();
        if (isUnavailable(error)) return unavailable();
        if (isApiError(error, 409, 'DEMO_PAYMENT_IN_PROGRESS')) {
          // Perhaps the resident's own (another tab): its checkout, never a second transaction.
          const items = await refreshObligation(finished.charge_id);
          const own = items?.find((item) => item.charge_id === finished.charge_id)?.in_progress_transaction_id;
          if (own) return open(checkoutPath(own, { resumed: true }));
          set({ busy: null, notice: { tone: 'error', text: error.message } });
          return;
        }
        const text = isApiError(error, 422) ? amountErrorOf(error) ?? INVALID_MESSAGE
          : isApiError(error, 409) ? error.message
            : isApiError(error, 404) ? OBLIGATION_GONE : messageOf(error, START_ERROR);
        set({ busy: null, notice: { tone: 'error', text } });
        void refreshObligation(finished.charge_id); // its balance may have changed
      }
    },

    /** The page is left: no more timers or renders. */
    dispose(): void {
      disposed = true;
      stopPolling();
      if (delayTimer !== null) clearTimer(delayTimer);
      delayTimer = null;
    }
  };
}

export type DemoCheckoutFlow = ReturnType<typeof createDemoCheckoutFlow>;
