import { ApiError } from '../api/errors.ts';
import type {
  PaymentConcept,
  PaymentMethod,
  PaymentPayload,
  PaymentStatus,
  RegisteredPayment
} from '../api/payments.ts';
import type { Resident, ResidentPage } from '../api/residents.ts';

/**
 * HU-004: payment form (pure, tested with node --test).
 *
 * Only the format is checked here (required fields, number, date) so the
 * administrator gets immediate feedback; every business rule (who can pay, the
 * apartment, FIFO, credit, dates) is decided by the backend.
 */

export const METHOD_LABELS: Record<PaymentMethod, string> = { CASH: 'Efectivo', CARD: 'Tarjeta', TRANSFER: 'Transferencia' };
export const CONCEPT_LABELS: Record<PaymentConcept, string> = {
  MONTHLY_FEE: 'Mensualidad',
  LATE_FEE: 'Mora',
  PENALTY: 'Penalización',
  OTHER: 'Otro'
};
export const STATUS_LABELS: Record<PaymentStatus, string> = {
  APPLIED: 'Aplicado',
  PARTIALLY_APPLIED: 'Aplicado parcialmente',
  CREDIT: 'Saldo a favor'
};
export const METHODS = Object.keys(METHOD_LABELS) as PaymentMethod[];
export const CONCEPTS = Object.keys(CONCEPT_LABELS) as PaymentConcept[];

export const DESCRIPTION_MAX_LENGTH = 200;
/** HU-008: optional data shown in the detail of the payment (same limits as the server). */
export const REFERENCE_MAX_LENGTH = 100;
export const NOTES_MAX_LENGTH = 500;
export const AMOUNT_MAX = '9999999999.99';
export const RESIDENT_SEARCH_DEBOUNCE_MS = 300;
export const RESIDENT_SEARCH_PAGE_SIZE = 8;

export const SUCCESS_MESSAGE = 'Pago registrado correctamente.';
export const EMAIL_FAILED_WARNING = 'El pago fue registrado, pero no fue posible enviar el recibo al correo.';
export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para registrar pagos.';

// Always 2 decimals: the same representation as DECIMAL(12,2) and the PDF receipt.
const COP = new Intl.NumberFormat('es-CO', {
  style: 'currency',
  currency: 'COP',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
});

/** "$ 150.000,00" — display only; the amount itself always stays a decimal string. */
export function formatCop(amount: string): string {
  return COP.format(Number(amount));
}

/** Today's date in Bogotá as YYYY-MM-DD (the date the administrator sees). */
export function bogotaToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Bogota',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(now);
}

/** "1 de octubre de 2026" for a YYYY-MM-DD date (no time zone involved). */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(year, month - 1, day))
  );
}

/**
 * Amount typed by the administrator → "150000.50". Digits, optionally a comma or a
 * dot and 1-2 decimals. No thousands separators: "150.000" is rejected instead of
 * being read as 150 (never guessed, never rounded).
 */
export function parseAmount(text: string): { value: string } | { error: string } {
  const raw = text.replace(/\s/g, '').replace(/^\$/, '');
  if (!raw) return { error: 'Ingresa el monto.' };
  const match = /^(\d+)(?:[.,](\d{1,2}))?$/.exec(raw);
  if (!match) {
    return { error: 'Escribe el monto solo con números, sin puntos de miles (p. ej., 150000 o 150000,50).' };
  }
  const units = match[1].replace(/^0+(?=\d)/, '');
  const value = match[2] ? `${units}.${match[2].padEnd(2, '0')}` : units;
  if (/^0+(\.0+)?$/.test(value)) return { error: 'El monto debe ser mayor que cero.' };
  if (units.length > 10 || (units.length === 10 && Number(value) > Number(AMOUNT_MAX))) {
    return { error: `El monto no puede superar ${formatCop(AMOUNT_MAX)}.` };
  }
  return { value };
}

/** A real calendar date written as YYYY-MM-DD (2026-02-30 is not). */
export function isIsoDate(text: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return false;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export function showsDescription(concept: string): boolean {
  return concept === 'OTHER';
}

export type PaymentField =
  | 'resident_id'
  | 'amount'
  | 'method'
  | 'concept'
  | 'concept_description'
  | 'payment_date'
  | 'transaction_reference'
  | 'notes';
export const FIELDS: PaymentField[] = [
  'resident_id',
  'amount',
  'method',
  'concept',
  'concept_description',
  'payment_date',
  'transaction_reference',
  'notes'
];
export type FieldErrors = Partial<Record<PaymentField, string>>;

export interface PaymentFormValues {
  residentId: number | null;
  amount: string;
  method: string;
  concept: string;
  description: string;
  paymentDate: string;
  /** Optional (HU-008): empty means none. */
  transactionReference: string;
  notes: string;
}

export function validatePaymentForm(
  values: PaymentFormValues,
  today: string
): { errors: FieldErrors; payload: PaymentPayload | null } {
  const errors: FieldErrors = {};
  if (values.residentId === null) errors.resident_id = 'Selecciona el residente.';
  const amount = parseAmount(values.amount);
  if ('error' in amount) errors.amount = amount.error;
  if (!(METHODS as string[]).includes(values.method)) errors.method = 'Selecciona el método de pago.';
  if (!(CONCEPTS as string[]).includes(values.concept)) errors.concept = 'Selecciona el concepto del pago.';
  const description = values.description.trim().replace(/\s+/g, ' ');
  if (showsDescription(values.concept)) {
    if (!description) errors.concept_description = 'Describe el concepto del pago.';
    else if (description.length > DESCRIPTION_MAX_LENGTH) {
      errors.concept_description = `La descripción no puede superar ${DESCRIPTION_MAX_LENGTH} caracteres.`;
    }
  }
  if (!values.paymentDate) errors.payment_date = 'Selecciona la fecha del pago.';
  else if (!isIsoDate(values.paymentDate)) {
    errors.payment_date = 'La fecha del pago no es válida.';
  } else if (values.paymentDate > today) errors.payment_date = 'La fecha del pago no puede ser posterior a hoy.';
  // Optional: one line for the reference; the notes keep their line breaks.
  const reference = values.transactionReference.trim().replace(/\s+/g, ' ');
  if (reference.length > REFERENCE_MAX_LENGTH) {
    errors.transaction_reference = `La referencia de transacción no puede superar ${REFERENCE_MAX_LENGTH} caracteres.`;
  }
  const notes = values.notes.replace(/\r\n?/g, '\n').trim();
  if (notes.length > NOTES_MAX_LENGTH) errors.notes = `Las notas no pueden superar ${NOTES_MAX_LENGTH} caracteres.`;

  if (Object.keys(errors).length > 0 || 'error' in amount) return { errors, payload: null };
  return {
    errors,
    payload: {
      resident_id: values.residentId!,
      amount: amount.value,
      method: values.method as PaymentMethod,
      concept: values.concept as PaymentConcept,
      // Only for "Otro"; never sent with another concept.
      concept_description: showsDescription(values.concept) ? description : null,
      payment_date: values.paymentDate,
      transaction_reference: reference || null,
      notes: notes || null
    }
  };
}

// --- Confirmation (criterion 2.1) -------------------------------------------------------------

export interface Confirmation {
  title: string;
  lines: { label: string; value: string }[];
  /** Separate warning: the payment is registered even if the email failed. */
  warning: string | null;
}

export function confirmationOf(payment: RegisteredPayment): Confirmation {
  const lines = [
    { label: 'Recibo', value: payment.receipt_number },
    { label: 'Residente', value: `${payment.resident.full_name} · ${payment.unit.label}` },
    { label: 'Monto', value: formatCop(payment.amount) },
    { label: 'Valor aplicado', value: formatCop(payment.applied_amount) }
  ];
  if (payment.transaction_reference) lines.push({ label: 'Referencia de transacción', value: payment.transaction_reference });
  if (Number(payment.credit_amount) > 0) lines.push({ label: 'Saldo a favor', value: formatCop(payment.credit_amount) });
  lines.push(
    { label: 'Estado del pago', value: STATUS_LABELS[payment.status] },
    { label: 'Saldo pendiente de la unidad', value: formatCop(payment.account.pending_balance) },
    { label: 'Saldo a favor de la unidad', value: formatCop(payment.account.credit_balance) }
  );
  if (payment.notes) lines.push({ label: 'Notas', value: payment.notes });
  return {
    title: SUCCESS_MESSAGE,
    lines,
    warning: payment.receipt_email_status === 'SENT' ? null : EMAIL_FAILED_WARNING
  };
}

// --- Submit flow -----------------------------------------------------------------------------------

export type SubmitState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'done'; payment: RegisteredPayment; confirmation: Confirmation }
  | { kind: 'invalid'; errors: FieldErrors }
  | { kind: 'error'; message: string; errors: FieldErrors };

export function createPaymentFlow(deps: {
  register: (payload: PaymentPayload) => Promise<RegisteredPayment>;
  render: (state: SubmitState) => void;
  navigate: (path: string) => void;
  /** Invalidation: the summary is queried again after a payment (criterion 2.1). */
  onRegistered: (payment: RegisteredPayment) => void;
}) {
  let busy = false;
  return {
    async submit(values: PaymentFormValues, today: string): Promise<void> {
      if (busy) return; // no double submission
      const { errors, payload } = validatePaymentForm(values, today);
      if (!payload) {
        deps.render({ kind: 'invalid', errors });
        return;
      }
      busy = true;
      deps.render({ kind: 'submitting' });
      try {
        const payment = await deps.register(payload);
        deps.render({ kind: 'done', payment, confirmation: confirmationOf(payment) });
        deps.onRegistered(payment);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          deps.navigate('/login');
          return;
        }
        if (!(error instanceof ApiError)) {
          deps.render({ kind: 'error', message: CONNECTION_ERROR, errors: {} });
          return;
        }
        const fieldErrors: FieldErrors = {};
        for (const detail of error.details) {
          if (detail.field && (FIELDS as string[]).includes(detail.field)) {
            fieldErrors[detail.field as PaymentField] ??= detail.message;
          }
        }
        const message = error.status === 403 && error.code === 'FORBIDDEN' ? FORBIDDEN_MESSAGE : error.message;
        deps.render({ kind: 'error', message, errors: fieldErrors });
      } finally {
        busy = false;
      }
    }
  };
}

// --- Resident picker (uses the HU-006 list: registered residents only) -------------------------------

export type PickerState =
  | { kind: 'idle' }
  | { kind: 'searching'; text: string }
  | { kind: 'results'; text: string; residents: Resident[] }
  | { kind: 'none'; text: string }
  | { kind: 'error'; message: string }
  | { kind: 'selected'; resident: Resident };

export function residentSearchQuery(text: string): string {
  const params = new URLSearchParams({ page_size: String(RESIDENT_SEARCH_PAGE_SIZE), search: text.trim() });
  return params.toString();
}

export function createResidentPicker(deps: {
  load: (queryString: string) => Promise<ResidentPage>;
  render: (state: PickerState) => void;
  navigate: (path: string) => void;
  schedule?: (callback: () => void, ms: number) => () => void;
}) {
  const schedule =
    deps.schedule ??
    ((callback: () => void, ms: number) => {
      const timer = setTimeout(callback, ms);
      return () => clearTimeout(timer);
    });
  let cancel: (() => void) | null = null;
  let latest = 0;
  let selected: Resident | null = null;

  async function run(text: string): Promise<void> {
    const request = ++latest;
    deps.render({ kind: 'searching', text });
    try {
      const page = await deps.load(residentSearchQuery(text));
      if (request !== latest) return;
      deps.render(page.items.length ? { kind: 'results', text, residents: page.items } : { kind: 'none', text });
    } catch (error) {
      if (request !== latest) return;
      if (error instanceof ApiError && error.status === 401) {
        deps.navigate('/login');
        return;
      }
      deps.render({ kind: 'error', message: error instanceof ApiError ? error.message : CONNECTION_ERROR });
    }
  }

  return {
    get selected(): Resident | null {
      return selected;
    },
    /** Debounced search by name or document; typing again clears the selection. */
    search(text: string): void {
      cancel?.();
      selected = null;
      latest++; // an answer for older text is ignored
      if (!text.trim()) {
        cancel = null;
        deps.render({ kind: 'idle' });
        return;
      }
      cancel = schedule(() => {
        cancel = null;
        void run(text);
      }, RESIDENT_SEARCH_DEBOUNCE_MS);
    },
    select(resident: Resident): void {
      cancel?.();
      cancel = null;
      latest++;
      selected = resident;
      deps.render({ kind: 'selected', resident });
    },
    clear(): void {
      cancel?.();
      cancel = null;
      latest++;
      selected = null;
      deps.render({ kind: 'idle' });
    }
  };
}
