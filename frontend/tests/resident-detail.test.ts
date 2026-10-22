/**
 * HU-015: detail, update and deletion of a registered resident (administrator). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { ResidentDetail, ResidentUpdatePayload } from '../src/lib/api/residents.ts';
import {
  CHECK_FIELDS_MESSAGE,
  CONNECTION_ERROR,
  DELETED_MESSAGE,
  DELETED_PATH,
  DELETE_CANCELLED_MESSAGE,
  EDIT_CANCELLED_MESSAGE,
  NOT_FOUND_MESSAGE,
  NO_CHANGES_MESSAGE,
  UPDATED_MESSAGE,
  VERSION_CONFLICT_MESSAGE,
  createResidentDetail,
  deletedNotice,
  formValuesOf,
  residentPath,
  validateResidentForm,
  type ResidentDetailState,
  type ResidentFormValues
} from '../src/lib/residents/resident-detail.ts';

function resident(overrides: Partial<ResidentDetail> = {}): ResidentDetail {
  return {
    id: 7,
    full_name: 'Laura Gómez',
    document_number: '1012345678',
    phone: '+573001234567',
    email: 'laura@example.com',
    unit: { id: 3, label: 'B1-TO2-APTO-104' },
    status: 'ACTIVE',
    updated_at: '2026-10-22T15:00:00.123456Z',
    ...overrides
  };
}

function apiError(status: number, code: string, message = 'Mensaje del servidor.', details: ApiError['details'] = []) {
  return new ApiError(status, { error: { code, message, details } });
}

function values(overrides: Partial<ResidentFormValues> = {}): ResidentFormValues {
  return { fullName: 'Laura Gómez Ruiz', documentNumber: '1012345678', phone: '+573001234567', ...overrides };
}

function setup(options: {
  update?: (id: number, payload: ResidentUpdatePayload) => Promise<ResidentDetail>;
  remove?: (id: number, updatedAt: string) => Promise<void>;
  reload?: (id: number) => Promise<ResidentDetail>;
} = {}) {
  const calls = { update: [] as [number, ResidentUpdatePayload][], remove: [] as [number, string][], reload: 0 };
  const renders: ResidentDetailState[] = [];
  const navigations: string[] = [];
  const controller = createResidentDetail(resident(), {
    update: async (id, payload) => {
      calls.update.push([id, payload]);
      return options.update ? options.update(id, payload) : resident({ ...payload, updated_at: '2026-10-22T16:00:00Z' });
    },
    remove: async (id, updatedAt) => {
      calls.remove.push([id, updatedAt]);
      return options.remove ? options.remove(id, updatedAt) : undefined;
    },
    reload: async (id) => {
      calls.reload += 1;
      return options.reload ? options.reload(id) : resident({ full_name: 'Laura Gómez Pérez', updated_at: '2026-10-22T17:00:00Z' });
    },
    render: (state) => renders.push(state),
    navigate: (path) => navigations.push(path)
  });
  return { controller, calls, renders, navigations };
}

describe('resident form checks (the same rules as the backend)', () => {
  it('normalizes like the server: spaces of the name, uppercase document, phone without spaces or hyphens', () => {
    assert.deepEqual(validateResidentForm({ fullName: '  Laura   Gómez ', documentNumber: ' ab123456 ', phone: '300 123-4567' }), {
      errors: {},
      data: { full_name: 'Laura Gómez', document_number: 'AB123456', phone: '3001234567' }
    });
  });

  it('the phone is optional: empty is null', () => {
    assert.equal(validateResidentForm(values({ phone: '  ' })).data?.phone, null);
  });

  it('incomplete or invalid data: one message per field, nothing to send', () => {
    assert.deepEqual(validateResidentForm({ fullName: '', documentNumber: '', phone: '12' }), {
      errors: {
        full_name: 'El nombre completo es obligatorio.',
        document_number: 'El número de documento es obligatorio.',
        phone: 'El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional.'
      },
      data: null
    });
    assert.equal(validateResidentForm(values({ fullName: 'Lu' })).errors.full_name,
      'El nombre completo debe tener entre 3 y 150 caracteres.');
    assert.equal(validateResidentForm(values({ fullName: 'Laura 2' })).errors.full_name,
      'El nombre completo solo puede contener letras, espacios, apóstrofos y guiones.');
    assert.equal(validateResidentForm(values({ fullName: "María-José O'Neil" })).errors.full_name, undefined);
    assert.equal(validateResidentForm(values({ documentNumber: '12-34' })).errors.document_number,
      'El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos.');
  });

  it('the form starts from the stored data', () => {
    assert.deepEqual(formValuesOf(resident({ phone: null })), {
      fullName: 'Laura Gómez', documentNumber: '1012345678', phone: ''
    });
  });
});

describe('update (criteria 1, 2 and 3)', () => {
  it('opens the form with the stored data; "Cancelar" discards the draft and sends nothing (CU-015 7c)', () => {
    const { controller, calls } = setup();

    controller.startEdit();
    assert.equal(controller.state.editing, true);
    assert.deepEqual(controller.state.values, formValuesOf(resident()));
    controller.cancelEdit();

    assert.equal(controller.state.editing, false);
    assert.deepEqual(controller.state.values, formValuesOf(resident()));
    assert.deepEqual(controller.state.notice, { tone: 'info', text: EDIT_CANCELLED_MESSAGE });
    assert.deepEqual(calls, { update: [], remove: [], reload: 0 });
  });

  it('sends the normalized data with the token read, then confirms and shows what the server stored', async () => {
    const { controller, calls } = setup();
    controller.startEdit();

    await controller.save(values({ fullName: ' Laura  Gómez Ruiz ', phone: '' }));

    assert.deepEqual(calls.update, [[7, {
      full_name: 'Laura Gómez Ruiz', document_number: '1012345678', phone: null, updated_at: '2026-10-22T15:00:00.123456Z'
    }]]);
    assert.equal(controller.state.editing, false);
    assert.equal(controller.state.resident.full_name, 'Laura Gómez Ruiz');
    assert.equal(controller.state.resident.updated_at, '2026-10-22T16:00:00Z'); // the next change uses the new token
    assert.deepEqual(controller.state.notice, { tone: 'success', text: UPDATED_MESSAGE });
  });

  it('invalid data is not sent: the messages go next to their fields and the draft stays', async () => {
    const { controller, calls } = setup();
    controller.startEdit();

    await controller.save(values({ documentNumber: '' }));

    assert.deepEqual(calls.update, []);
    assert.equal(controller.state.editing, true);
    assert.deepEqual(controller.state.errors, { document_number: 'El número de documento es obligatorio.' });
    assert.deepEqual(controller.state.notice, { tone: 'error', text: CHECK_FIELDS_MESSAGE });
    assert.equal(controller.state.values.documentNumber, '');
  });

  it('nothing changed: no request, the form closes', async () => {
    const { controller, calls } = setup();
    controller.startEdit();

    await controller.save(formValuesOf(resident()));

    assert.deepEqual(calls.update, []);
    assert.equal(controller.state.editing, false);
    assert.deepEqual(controller.state.notice, { tone: 'info', text: NO_CHANGES_MESSAGE });
  });

  it('one PATCH while it is on its way: a second save does nothing; the controls are busy', async () => {
    let resolve!: (value: ResidentDetail) => void;
    const { controller, calls, renders } = setup({ update: () => new Promise((done) => (resolve = done)) });
    controller.startEdit();

    const first = controller.save(values());
    await controller.save(values({ fullName: 'Otra Persona' }));
    assert.equal(renders.at(-1)?.busy, 'save');
    controller.cancelEdit();
    controller.askDelete();
    resolve(resident({ full_name: 'Laura Gómez Ruiz' }));
    await first;

    assert.equal(calls.update.length, 1);
    assert.equal(controller.state.busy, null);
    assert.equal(controller.state.confirmingDelete, false);
  });

  it('server checks: 422 and the duplicated document go next to their fields; the draft stays', async () => {
    const duplicated = apiError(409, 'DOCUMENT_ALREADY_REGISTERED', 'El número de documento ya está registrado.',
      [{ field: 'document_number', message: 'El número de documento ya está registrado.' }]);
    const { controller } = setup({ update: async () => { throw duplicated; } });
    controller.startEdit();

    await controller.save(values({ documentNumber: 'AB123456' }));

    assert.equal(controller.state.editing, true);
    assert.deepEqual(controller.state.errors, { document_number: 'El número de documento ya está registrado.' });
    assert.equal(controller.state.values.documentNumber, 'AB123456');
    assert.equal(controller.state.resident.document_number, '1012345678'); // the stored data did not change
  });

  it('a 422 without a known field shows its message', async () => {
    const invalid = apiError(422, 'VALIDATION_ERROR', 'Los datos enviados no son válidos.',
      [{ field: 'updated_at', message: 'El valor no es válido.' }]);
    const { controller } = setup({ update: async () => { throw invalid; } });
    controller.startEdit();

    await controller.save(values());

    assert.deepEqual(controller.state.notice, { tone: 'error', text: 'Los datos enviados no son válidos.' });
  });

  it('409 VERSION_CONFLICT: the current data is read again and the form closes; nothing is overwritten', async () => {
    const { controller, calls } = setup({ update: async () => { throw apiError(409, 'VERSION_CONFLICT'); } });
    controller.startEdit();

    await controller.save(values());

    assert.equal(calls.reload, 1);
    assert.equal(controller.state.editing, false);
    assert.equal(controller.state.resident.full_name, 'Laura Gómez Pérez');
    assert.equal(controller.state.resident.updated_at, '2026-10-22T17:00:00Z');
    assert.deepEqual(controller.state.notice, { tone: 'warning', text: VERSION_CONFLICT_MESSAGE });
  });

  it('404: the resident is no longer registered; nothing else can be changed', async () => {
    const { controller, calls } = setup({ update: async () => { throw apiError(404, 'RESIDENT_NOT_FOUND'); } });
    controller.startEdit();

    await controller.save(values());
    controller.startEdit();
    controller.askDelete();

    assert.equal(controller.state.gone, true);
    assert.equal(controller.state.editing, false);
    assert.equal(controller.state.confirmingDelete, false);
    assert.deepEqual(controller.state.notice, { tone: 'error', text: NOT_FOUND_MESSAGE });
    assert.equal(calls.update.length, 1);
  });

  it('401: back to the login page', async () => {
    const { controller, navigations } = setup({ update: async () => { throw apiError(401, 'NOT_AUTHENTICATED'); } });
    controller.startEdit();

    await controller.save(values());

    assert.deepEqual(navigations, ['/login']);
  });

  it('no connection: the previous data stays and the message says nothing was saved (CU-015 9a)', async () => {
    const { controller } = setup({ update: async () => { throw new TypeError('Failed to fetch'); } });
    controller.startEdit();

    await controller.save(values());

    assert.equal(controller.state.resident.full_name, 'Laura Gómez');
    assert.equal(controller.state.busy, null);
    assert.deepEqual(controller.state.notice, { tone: 'error', text: CONNECTION_ERROR });
  });
});

describe('deletion (criteria 4, 5 and 6)', () => {
  it('asks for confirmation first: nothing is sent yet (criterion 4.2)', () => {
    const { controller, calls } = setup();

    controller.askDelete();

    assert.equal(controller.state.confirmingDelete, true);
    assert.deepEqual(calls.remove, []);
  });

  it('cancelling keeps the resident as it was and sends nothing (criterion 6)', () => {
    const { controller, calls } = setup();
    controller.askDelete();

    controller.cancelDelete();

    assert.equal(controller.state.confirmingDelete, false);
    assert.deepEqual(controller.state.notice, { tone: 'info', text: DELETE_CANCELLED_MESSAGE });
    assert.deepEqual(calls.remove, []);
    assert.deepEqual(controller.state.resident, resident());
  });

  it('confirming sends one DELETE with the token, then the list says so (criterion 5)', async () => {
    const { controller, calls, navigations } = setup();
    controller.askDelete();

    await controller.confirmDelete();
    await controller.confirmDelete(); // a double click

    assert.deepEqual(calls.remove, [[7, '2026-10-22T15:00:00.123456Z']]);
    assert.deepEqual(navigations, [DELETED_PATH]);
    assert.equal(controller.state.busy, 'delete'); // nothing else can be sent while leaving
  });

  it('confirming without asking first does nothing; the deletion is not offered while editing', async () => {
    const { controller, calls } = setup();

    await controller.confirmDelete();
    controller.startEdit();
    controller.askDelete();

    assert.deepEqual(calls.remove, []);
    assert.equal(controller.state.confirmingDelete, false);
  });

  it('409 VERSION_CONFLICT: the dialog closes and the current data is shown; nothing was deleted', async () => {
    const { controller, calls } = setup({ remove: async () => { throw apiError(409, 'VERSION_CONFLICT'); } });
    controller.askDelete();

    await controller.confirmDelete();

    assert.equal(calls.reload, 1);
    assert.equal(controller.state.confirmingDelete, false);
    assert.equal(controller.state.busy, null);
    assert.deepEqual(controller.state.notice, { tone: 'warning', text: VERSION_CONFLICT_MESSAGE });
  });

  it('another reason given by the server is shown as it says it', async () => {
    const reason = apiError(409, 'CONFLICT', 'La operación entra en conflicto con el estado actual.');
    const { controller } = setup({ remove: async () => { throw reason; } });
    controller.askDelete();

    await controller.confirmDelete();

    assert.deepEqual(controller.state.notice, { tone: 'error', text: 'La operación entra en conflicto con el estado actual.' });
    assert.equal(controller.state.gone, false);
  });

  it('blocked by the apartment (CU-015 7b): the reasons of the server, the dialog closes, the resident stays', async () => {
    const message = 'No se puede eliminar al residente porque su unidad residencial B1-TO2-APTO-104 tiene saldo ' +
      'pendiente por pagar. Resuelve esa condición e inténtalo de nuevo.';
    const blocked = apiError(409, 'RESIDENT_DELETION_BLOCKED', message,
      [{ field: 'pending_balance', message: 'su unidad residencial B1-TO2-APTO-104 tiene saldo pendiente por pagar' }]);
    const { controller, calls, navigations } = setup({ remove: async () => { throw blocked; } });
    controller.askDelete();

    await controller.confirmDelete();

    assert.equal(calls.remove.length, 1);
    assert.deepEqual(navigations, []);
    assert.equal(controller.state.confirmingDelete, false);
    assert.equal(controller.state.busy, null);
    assert.equal(controller.state.gone, false);
    assert.deepEqual(controller.state.errors, {}); // a condition of the apartment, not a field of the form
    assert.deepEqual(controller.state.notice, { tone: 'error', text: message });
    assert.deepEqual(controller.state.resident, resident());
  });

  it('already deleted by someone else (404): it says so', async () => {
    const { controller } = setup({ remove: async () => { throw apiError(404, 'RESIDENT_NOT_FOUND'); } });
    controller.askDelete();

    await controller.confirmDelete();

    assert.equal(controller.state.gone, true);
    assert.deepEqual(controller.state.notice, { tone: 'error', text: NOT_FOUND_MESSAGE });
  });
});

describe('list links', () => {
  it('each row leads to the detail; the list says when it comes from a deletion', () => {
    assert.equal(residentPath(7), '/admin/residents/7');
    assert.equal(deletedNotice(new URLSearchParams('deleted=1')), DELETED_MESSAGE);
    assert.equal(deletedNotice(new URLSearchParams('deleted=yes&search=Laura')), null);
    assert.equal(deletedNotice(new URLSearchParams('')), null);
  });
});
