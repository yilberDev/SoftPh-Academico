/**
 * HU-003: invitation form logic (format checks and result texts).
 * Run with `npm test` (node:test, native TypeScript in Node 24; no extra dependencies).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { Invitation } from '../src/lib/api/invitations.ts';
import {
  DELIVERY_LABELS,
  formatBogota,
  invitationOutcome,
  parseInvitationParam,
  resultPath,
  validateInvitationForm,
  type InvitationFormValues
} from '../src/lib/residents/invitation-form.ts';

const VALID: InvitationFormValues = {
  email: 'residente@example.com',
  fullName: 'Laura Gómez',
  unitId: '15',
  validityDays: '7'
};

function invitation(overrides: Partial<Invitation> = {}): Invitation {
  return {
    id: 12,
    email: 'residente@example.com',
    full_name: 'Laura Gómez',
    unit: { id: 15, label: 'B1-TO2-APTO-104' },
    status: 'PENDING',
    delivery_status: 'SENT',
    send_attempts: 1,
    validity_days: 7,
    created_at: '2026-10-01T20:05:00Z',
    expires_at: '2026-10-08T20:05:00Z',
    ...overrides
  };
}

describe('validateInvitationForm', () => {
  it('builds the payload sent to the API (unit_id, never the label)', () => {
    assert.deepEqual(
      validateInvitationForm({ ...VALID, email: '  residente@example.com ', fullName: '  Laura   Gómez ' }),
      {
        errors: {},
        payload: { email: 'residente@example.com', full_name: 'Laura Gómez', unit_id: 15, validity_days: 7 }
      }
    );
  });

  it('requires every field', () => {
    const { errors, payload } = validateInvitationForm({ email: '', fullName: ' ', unitId: '', validityDays: '' });
    assert.equal(payload, null);
    assert.deepEqual(Object.keys(errors).sort(), ['email', 'full_name', 'unit_id', 'validity_days']);
    assert.equal(errors.email, 'Ingresa el correo electrónico del residente.');
    assert.equal(errors.full_name, 'Ingresa el nombre completo del residente.');
    assert.equal(errors.unit_id, 'Selecciona un apartamento disponible.');
  });

  for (const email of ['sin-arroba', 'a@b', 'a @example.com', `${'a'.repeat(250)}@example.com`]) {
    it(`rejects the email "${email.slice(0, 20)}"`, () => {
      assert.equal(validateInvitationForm({ ...VALID, email }).errors.email, 'Ingresa un correo electrónico válido.');
    });
  }

  for (const fullName of ['Al', 'A'.repeat(151)]) {
    it(`rejects a name of ${fullName.length} characters`, () => {
      assert.equal(
        validateInvitationForm({ ...VALID, fullName }).errors.full_name,
        'El nombre completo debe tener entre 3 y 150 caracteres.'
      );
    });
  }

  for (const validityDays of ['1', '30']) {
    it(`accepts a validity of ${validityDays} days`, () => {
      assert.equal(validateInvitationForm({ ...VALID, validityDays }).payload?.validity_days, Number(validityDays));
    });
  }

  for (const validityDays of ['0', '31', '-1', '7.5', 'siete', '']) {
    it(`rejects a validity of "${validityDays}"`, () => {
      assert.equal(
        validateInvitationForm({ ...VALID, validityDays }).errors.validity_days,
        'La vigencia debe estar entre 1 y 30 días.'
      );
    });
  }

  for (const unitId of ['0', 'abc', '1.5', '-2']) {
    it(`rejects the unit "${unitId}"`, () => {
      assert.equal(validateInvitationForm({ ...VALID, unitId }).errors.unit_id, 'Selecciona un apartamento disponible.');
    });
  }
});

describe('invitationOutcome (CA-7)', () => {
  it('sent: confirmation with the email and the expiry', () => {
    assert.deepEqual(invitationOutcome(invitation()), {
      tone: 'success',
      title: 'Invitación enviada a residente@example.com',
      detail: 'B1-TO2-APTO-104. Vence el 8 de octubre de 2026, 3:05 p. m. (hora de Colombia).',
      canResend: false
    });
  });

  it('failed: the invitation exists but the email was not sent, and it can be resent', () => {
    const outcome = invitationOutcome(invitation({ delivery_status: 'FAILED' }));
    assert.equal(outcome.tone, 'warning');
    assert.equal(outcome.title, 'La invitación se creó, pero el correo no pudo enviarse.');
    assert.equal(outcome.canResend, true);
  });

  it('unconfirmed delivery can also be resent', () => {
    const outcome = invitationOutcome(invitation({ delivery_status: 'PENDING' }));
    assert.equal(outcome.title, 'La invitación se creó, pero no se pudo confirmar el envío del correo.');
    assert.equal(outcome.canResend, true);
  });

  it('resend results', () => {
    assert.equal(invitationOutcome(invitation(), true).title, 'Invitación reenviada a residente@example.com');
    assert.equal(
      invitationOutcome(invitation({ delivery_status: 'FAILED' }), true).title,
      'La invitación sigue vigente, pero el correo no pudo enviarse.'
    );
  });
});

describe('dates and navigation', () => {
  it('formats the expiry in Bogotá time', () => {
    assert.equal(formatBogota('2026-10-08T20:05:00Z'), '8 de octubre de 2026, 3:05 p. m. (hora de Colombia)');
    assert.equal(formatBogota('2026-10-09T04:30:00Z'), '8 de octubre de 2026, 11:30 p. m. (hora de Colombia)');
  });

  it('labels every delivery status', () => {
    assert.deepEqual(DELIVERY_LABELS, { SENT: 'Enviado', FAILED: 'No enviado', PENDING: 'Sin confirmar' });
  });

  it('puts only the invitation id in the URL', () => {
    assert.equal(resultPath(12), '/admin/residents/invite?invitation=12');
    assert.equal(resultPath(12, true), '/admin/residents/invite?invitation=12&resent=1');
  });

  it('parses the id safely', () => {
    assert.equal(parseInvitationParam('12'), 12);
    for (const value of [null, '', '0', '-1', '1.5', 'abc', '12abc', '9'.repeat(20)]) {
      assert.equal(parseInvitationParam(value), null, String(value));
    }
  });
});
