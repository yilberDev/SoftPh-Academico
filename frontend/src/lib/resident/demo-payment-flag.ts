/**
 * HU-010: DEMO_PAYMENTS_ENABLED as the frontend reads it (pure, tested with node --test).
 *
 * The same variable as the backend's (the shared root .env), read with the same rule as its
 * Settings (pydantic): case-insensitive "true", "1", "yes", "y", "on", "t" enable it, and
 * "false", "0", "no", "n", "off", "f" disable it. Not set: disabled (the default). Any other
 * value, an empty or a padded one included, is invalid: the backend refuses to start with it,
 * and here it fails closed (disabled, never enabled) with a warning. The flag only hides the
 * pages and links; the backend publishes the demo routes only when its own flag is on.
 */

export const TRUE_VALUES: readonly string[] = ['true', '1', 'yes', 'y', 'on', 't'];
export const FALSE_VALUES: readonly string[] = ['false', '0', 'no', 'n', 'off', 'f'];

export const INVALID_FLAG_WARNING =
  'DEMO_PAYMENTS_ENABLED no es un valor booleano válido (true/false, 1/0, yes/no, y/n, on/off, t/f): ' +
  'el pago de demostración queda deshabilitado.';

export interface DemoPaymentsFlag {
  enabled: boolean;
  /** False when the value is not one the backend accepts. */
  valid: boolean;
}

export function parseDemoPaymentsFlag(raw: string | undefined): DemoPaymentsFlag {
  if (raw === undefined) return { enabled: false, valid: true };
  const value = raw.toLowerCase(); // no trimming: the backend rejects " true" too
  if (TRUE_VALUES.includes(value)) return { enabled: true, valid: true };
  if (FALSE_VALUES.includes(value)) return { enabled: false, valid: true };
  return { enabled: false, valid: false };
}
