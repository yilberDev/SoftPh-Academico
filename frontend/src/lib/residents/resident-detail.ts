import { ApiError } from '../api/errors.ts';
import type { ResidentDetail, ResidentUpdatePayload } from '../api/residents.ts';
import { NAME_MAX_LENGTH, NAME_MIN_LENGTH } from './invitation-form.ts';

/**
 * HU-015: detail, update and deletion of one registered resident (pure, tested with node --test).
 *
 * The administrator may correct the registration data (full name, document, phone); the email,
 * the account status and the apartment are shown, never edited. "Cancelar" discards the draft
 * without any request (CU-015 7c). Deleting asks for confirmation first; cancelling it sends
 * nothing (criterion 6). Every change carries the updated_at the administrator saw: if someone
 * changed the resident meanwhile (409 VERSION_CONFLICT) the detail is read again and nothing is
 * overwritten silently. Format checks here only improve the experience: the backend decides.
 */

export const LIST_PATH = '/admin/residents';
/** Where a successful deletion leads: the list (HU-006), which says so (criterion 5.2). */
export const DELETED_PATH = `${LIST_PATH}?deleted=1`;

export const UPDATED_MESSAGE = 'Los datos del residente se actualizaron. El listado de residentes ya muestra la información nueva.';
export const NO_CHANGES_MESSAGE = 'No había cambios que guardar.';
export const EDIT_CANCELLED_MESSAGE = 'Edición cancelada: no se guardó ningún cambio.';
export const DELETE_CANCELLED_MESSAGE = 'Eliminación cancelada: el residente se conserva sin cambios.';
export const DELETED_MESSAGE = 'El residente fue eliminado del listado de residentes registrados.';
export const VERSION_CONFLICT_MESSAGE =
  'Otra persona modificó este residente. Se cargó su información actual: revísala antes de volver a intentarlo.';
export const NOT_FOUND_MESSAGE = 'Este residente ya no está registrado.';
export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo. No se guardó ningún cambio.';
export const CHECK_FIELDS_MESSAGE = 'Revisa los datos marcados.';

// The same rules and messages as the backend (app/schemas/user.py).
const FULL_NAME_PATTERN = /^\p{L}+(?:[ '-]\p{L}+)*$/u;
const DOCUMENT_PATTERN = /^[A-Z0-9]{5,20}$/;
const PHONE_PATTERN = /^\+?\d{7,15}$/;
export const DOCUMENT_MAX_LENGTH = 20;
export const PHONE_MAX_LENGTH = 20;

export type ResidentField = 'full_name' | 'document_number' | 'phone';
export type FieldErrors = Partial<Record<ResidentField, string>>;
const FIELDS: readonly ResidentField[] = ['full_name', 'document_number', 'phone'];

export interface ResidentFormValues {
  fullName: string;
  documentNumber: string;
  phone: string;
}

export function formValuesOf(resident: ResidentDetail): ResidentFormValues {
  return { fullName: resident.full_name, documentNumber: resident.document_number ?? '', phone: resident.phone ?? '' };
}

/** Format checks only. Returns the errors per field and, if there are none, the normalized data. */
export function validateResidentForm(values: ResidentFormValues): {
  errors: FieldErrors;
  data: Omit<ResidentUpdatePayload, 'updated_at'> | null;
} {
  const errors: FieldErrors = {};
  const fullName = values.fullName.split(/\s+/).filter(Boolean).join(' ');
  const document = values.documentNumber.trim().toUpperCase();
  const phone = values.phone.replace(/[\s-]/g, '');

  if (!fullName) {
    errors.full_name = 'El nombre completo es obligatorio.';
  } else if (fullName.length < NAME_MIN_LENGTH || fullName.length > NAME_MAX_LENGTH) {
    errors.full_name = `El nombre completo debe tener entre ${NAME_MIN_LENGTH} y ${NAME_MAX_LENGTH} caracteres.`;
  } else if (!FULL_NAME_PATTERN.test(fullName)) {
    errors.full_name = 'El nombre completo solo puede contener letras, espacios, apóstrofos y guiones.';
  }
  if (!document) {
    errors.document_number = 'El número de documento es obligatorio.';
  } else if (!DOCUMENT_PATTERN.test(document)) {
    errors.document_number = 'El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos.';
  }
  if (phone && !PHONE_PATTERN.test(phone)) {
    errors.phone = 'El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional.';
  }
  if (Object.keys(errors).length > 0) return { errors, data: null };
  return { errors, data: { full_name: fullName, document_number: document, phone: phone || null } };
}

function sameData(resident: ResidentDetail, data: Omit<ResidentUpdatePayload, 'updated_at'>): boolean {
  return (
    resident.full_name === data.full_name &&
    (resident.document_number ?? null) === data.document_number &&
    (resident.phone ?? null) === data.phone
  );
}

export interface Notice {
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

export interface ResidentDetailState {
  resident: ResidentDetail;
  /** The edit form is open; values is its draft (the detail above keeps the stored data). */
  editing: boolean;
  values: ResidentFormValues;
  busy: 'save' | 'delete' | null;
  /** The deletion waits for the administrator's confirmation (criterion 4.2). */
  confirmingDelete: boolean;
  /** The resident is no longer registered (404): nothing can be changed any more. */
  gone: boolean;
  notice: Notice | null;
  errors: FieldErrors;
}

export interface ResidentDetailDeps {
  update: (id: number, payload: ResidentUpdatePayload) => Promise<ResidentDetail>;
  remove: (id: number, updatedAt: string) => Promise<void>;
  reload: (id: number) => Promise<ResidentDetail>;
  render: (state: ResidentDetailState) => void;
  navigate: (path: string) => void;
}

export function createResidentDetail(initial: ResidentDetail, deps: ResidentDetailDeps) {
  let state: ResidentDetailState = {
    resident: initial,
    editing: false,
    values: formValuesOf(initial),
    busy: null,
    confirmingDelete: false,
    gone: false,
    notice: null,
    errors: {}
  };

  function set(changes: Partial<ResidentDetailState>): void {
    state = { ...state, ...changes };
    deps.render(state);
  }

  /** 409 VERSION_CONFLICT: the current data is read again; the form closes, nothing is overwritten. */
  async function reloadAfterConflict(): Promise<void> {
    const notice: Notice = { tone: 'warning', text: VERSION_CONFLICT_MESSAGE };
    try {
      const resident = await deps.reload(state.resident.id);
      set({ resident, values: formValuesOf(resident), editing: false, busy: null, confirmingDelete: false, notice,
            errors: {} });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return gone();
      set({ busy: null, confirmingDelete: false, notice });
    }
  }

  function gone(): void {
    set({ gone: true, editing: false, busy: null, confirmingDelete: false, errors: {},
          notice: { tone: 'error', text: NOT_FOUND_MESSAGE } });
  }

  async function handleError(error: unknown): Promise<void> {
    if (!(error instanceof ApiError)) {
      set({ busy: null, confirmingDelete: false, notice: { tone: 'error', text: CONNECTION_ERROR } });
      return;
    }
    if (error.status === 401) {
      deps.navigate('/login');
      return;
    }
    if (error.status === 404) return gone();
    if (error.status === 409 && error.code === 'VERSION_CONFLICT') return reloadAfterConflict();
    // 422 and 409 DOCUMENT_ALREADY_REGISTERED: each message next to its field (criterion 2.2).
    const errors: FieldErrors = {};
    for (const detail of error.details) {
      const field = detail.field as ResidentField | undefined;
      if (field && FIELDS.includes(field) && !errors[field]) errors[field] = detail.message;
    }
    if (Object.keys(errors).length > 0) {
      set({ busy: null, confirmingDelete: false, errors, notice: { tone: 'error', text: CHECK_FIELDS_MESSAGE } });
      return;
    }
    set({ busy: null, confirmingDelete: false, notice: { tone: 'error', text: error.message } });
  }

  return {
    get state(): ResidentDetailState {
      return state;
    },
    /** Opens the form with the stored data (criterion 1.1). */
    startEdit(): void {
      if (state.busy || state.gone || state.editing) return;
      set({ editing: true, values: formValuesOf(state.resident), notice: null, errors: {}, confirmingDelete: false });
    },
    /** "Cancelar": the draft is discarded and nothing is sent (CU-015 7c). */
    cancelEdit(): void {
      if (state.busy || !state.editing) return;
      set({ editing: false, values: formValuesOf(state.resident), errors: {},
            notice: { tone: 'info', text: EDIT_CANCELLED_MESSAGE } });
    },
    /** Validates, then sends one PATCH with the token read (never twice while it is on its way). */
    async save(values: ResidentFormValues): Promise<void> {
      if (state.busy || state.gone || !state.editing) return;
      const checked = validateResidentForm(values);
      if (!checked.data) {
        set({ values, errors: checked.errors, notice: { tone: 'error', text: CHECK_FIELDS_MESSAGE } });
        return;
      }
      if (sameData(state.resident, checked.data)) {
        set({ editing: false, values: formValuesOf(state.resident), errors: {},
              notice: { tone: 'info', text: NO_CHANGES_MESSAGE } });
        return;
      }
      set({ values, busy: 'save', notice: null, errors: {} });
      try {
        const resident = await deps.update(state.resident.id, { ...checked.data, updated_at: state.resident.updated_at });
        // Criterion 3: the confirmation, and the detail shows the data as the server stored it.
        set({ resident, editing: false, values: formValuesOf(resident), busy: null,
              notice: { tone: 'success', text: UPDATED_MESSAGE } });
      } catch (error) {
        await handleError(error);
      }
    },
    /** First step: asks for confirmation (nothing is sent yet). */
    askDelete(): void {
      if (state.busy || state.gone || state.editing) return;
      set({ confirmingDelete: true, notice: null, errors: {} });
    },
    /** Criterion 6: the record stays as it was; no request. */
    cancelDelete(): void {
      if (state.busy || !state.confirmingDelete) return;
      set({ confirmingDelete: false, notice: { tone: 'info', text: DELETE_CANCELLED_MESSAGE } });
    },
    /** Second step: one DELETE; on success the list (criterion 5.2). */
    async confirmDelete(): Promise<void> {
      if (state.busy || !state.confirmingDelete || state.gone) return;
      set({ busy: 'delete' });
      try {
        await deps.remove(state.resident.id, state.resident.updated_at);
        deps.navigate(DELETED_PATH); // busy stays: nothing else can be sent while leaving the page
      } catch (error) {
        await handleError(error);
      }
    }
  };
}

/** The list page says the resident was deleted when it comes from a deletion (?deleted=1). */
export function deletedNotice(params: URLSearchParams): string | null {
  return params.get('deleted') === '1' ? DELETED_MESSAGE : null;
}

/** Link from a row of the list to the resident's detail. */
export function residentPath(id: number): string {
  return `${LIST_PATH}/${id}`;
}
