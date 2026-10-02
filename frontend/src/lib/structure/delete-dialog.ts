import { ApiError } from '../api/errors.ts';

/**
 * Delete confirmation dialog of the structure pages (HU-013), without DOM.
 *
 * The backend always validates the current state on DELETE. When it answers
 * that the item cannot be deleted (409 with one of the codes below), the
 * dialog becomes informative: it shows the reason, offers only "Cerrar" and
 * never sends another DELETE from that state. Reopening the dialog starts a
 * new confirmation, and the backend validates again.
 */

/** 409 codes that mean "the current state does not allow deleting it", with the reason shown. */
export const BLOCKED_DELETE_REASONS: ReadonlyMap<string, string> = new Map([
  ['BLOCK_HAS_ACTIVE_TOWERS', 'El bloque tiene torres activas. Elimínalas primero.'],
  ['TOWER_HAS_ACTIVE_UNITS', 'La torre tiene apartamentos activos. Elimínalos primero.'],
  // HU-003: an apartment with a resident or a pending invitation is in use.
  ['UNIT_IN_USE', 'El apartamento tiene un residente o una invitación pendiente.']
]);

export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';

export type DeleteDialogState =
  | { kind: 'closed' }
  | { kind: 'confirm'; label: string; submitting: boolean; error: string | null }
  | { kind: 'blocked'; label: string; reason: string };

export interface DeleteDialogOptions {
  /** Draws the state (the only place that touches the DOM). */
  render: (state: DeleteDialogState) => void;
  /** Called after a successful DELETE (204). */
  onDeleted: () => void;
}

export interface DeleteDialog {
  readonly state: DeleteDialogState;
  /** Opens the confirmation for one item; nothing is sent yet. */
  open(label: string, remove: () => Promise<unknown>): void;
  /** Sends the DELETE, only from the confirmation state and once at a time. */
  confirm(): Promise<void>;
  /** Cancel / Cerrar / Escape. */
  close(): void;
}

/** State after a failed DELETE: informative for the known 409s, otherwise the usual error. */
export function stateAfterDeleteError(label: string, error: unknown): DeleteDialogState {
  if (error instanceof ApiError && error.status === 409) {
    const reason = BLOCKED_DELETE_REASONS.get(error.code);
    if (reason) {
      return { kind: 'blocked', label, reason };
    }
  }
  const message = error instanceof ApiError ? error.message : CONNECTION_ERROR;
  return { kind: 'confirm', label, submitting: false, error: message };
}

export function createDeleteDialog({ render, onDeleted }: DeleteDialogOptions): DeleteDialog {
  let state: DeleteDialogState = { kind: 'closed' };
  let remove: (() => Promise<unknown>) | null = null;
  // Each open() starts a new session; a late answer of a closed one is ignored.
  let session = 0;

  function set(next: DeleteDialogState): void {
    state = next;
    render(state);
  }

  return {
    get state() {
      return state;
    },

    open(label, removeItem) {
      session += 1;
      remove = removeItem;
      set({ kind: 'confirm', label, submitting: false, error: null });
    },

    async confirm() {
      if (state.kind !== 'confirm' || state.submitting || !remove) {
        return;
      }
      const current = session;
      const { label } = state;
      set({ kind: 'confirm', label, submitting: true, error: null });
      try {
        await remove();
      } catch (error) {
        if (current === session && state.kind === 'confirm') {
          set(stateAfterDeleteError(label, error));
        }
        return;
      }
      if (current === session) {
        remove = null;
        set({ kind: 'closed' });
      }
      onDeleted();
    },

    close() {
      if (state.kind === 'closed') {
        return;
      }
      session += 1;
      remove = null;
      set({ kind: 'closed' });
    }
  };
}
