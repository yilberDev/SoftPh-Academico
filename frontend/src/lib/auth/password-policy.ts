/**
 * Format checks of a new password (the HU-001 policy, checked again by the
 * backend): 12-128 characters, not only spaces, and the same confirmation.
 * Used by account activation (HU-019) and password reset (HU-016). Pure module.
 */

export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

export type NewPasswordErrors = Partial<Record<'password' | 'password_confirmation', string>>;

export function validateNewPassword(password: string, confirmation: string): NewPasswordErrors {
  const errors: NewPasswordErrors = {};
  if (!password) {
    errors.password = 'Crea una contraseña.';
  } else if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    errors.password = `La contraseña debe tener entre ${PASSWORD_MIN_LENGTH} y ${PASSWORD_MAX_LENGTH} caracteres.`;
  } else if (!password.trim()) {
    errors.password = 'La contraseña no puede estar formada solo por espacios.';
  }
  if (!confirmation) {
    errors.password_confirmation = 'Confirma tu contraseña.';
  } else if (confirmation !== password) {
    errors.password_confirmation = 'La confirmación no coincide con la contraseña.';
  }
  return errors;
}
