/**
 * HU-010: DEMO_PAYMENTS_ENABLED read by the frontend with the backend's own rule. The table is the
 * one the backend's Settings (pydantic) gives for each value, checked against it on 2026-10-09.
 * Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { FALSE_VALUES, INVALID_FLAG_WARNING, TRUE_VALUES, parseDemoPaymentsFlag } from '../src/lib/resident/demo-payment-flag.ts';

const ENABLED = { enabled: true, valid: true };
const DISABLED = { enabled: false, valid: true };
const INVALID = { enabled: false, valid: false };

describe('DEMO_PAYMENTS_ENABLED', () => {
  it('not set: disabled (the default)', () => {
    assert.deepEqual(parseDemoPaymentsFlag(undefined), DISABLED);
  });

  it('the values the backend accepts as true, in any case', () => {
    for (const value of ['true', 'TRUE', 'True', 'tRuE', '1', 'yes', 'YES', 'Yes', 'y', 'Y', 'on', 'ON', 'On', 't', 'T']) {
      assert.deepEqual(parseDemoPaymentsFlag(value), ENABLED, value);
    }
  });

  it('the values the backend accepts as false, in any case', () => {
    for (const value of ['false', 'FALSE', 'False', '0', 'no', 'NO', 'n', 'N', 'off', 'OFF', 'f', 'F']) {
      assert.deepEqual(parseDemoPaymentsFlag(value), DISABLED, value);
    }
  });

  it('anything else is invalid and fails closed (never enabled), as the backend refuses it', () => {
    for (const value of ['', ' ', ' true', 'true ', '2', '-1', '1.0', '0.0', 'enabled', 'disabled', 'si', 'sí', 'yes please',
      'nope', 'truee', 'null', 'undefined']) {
      assert.deepEqual(parseDemoPaymentsFlag(value), INVALID, JSON.stringify(value));
    }
    assert.match(INVALID_FLAG_WARNING, /queda deshabilitado/);
  });

  it('exactly the backend\'s two sets, without overlap', () => {
    assert.deepEqual([...TRUE_VALUES].sort(), ['1', 'on', 't', 'true', 'y', 'yes']);
    assert.deepEqual([...FALSE_VALUES].sort(), ['0', 'f', 'false', 'n', 'no', 'off']);
    assert.ok(TRUE_VALUES.every((value) => !FALSE_VALUES.includes(value)));
  });
});
