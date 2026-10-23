/**
 * Shared class names of the public landing page, so its buttons, cards and focus rings stay consistent
 * (pure strings, tested with node --test). Crisp corners: controls 6 px, cards 8 px, as in the approved design.
 */

export const FOCUS_RING = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700';
export const FOCUS_RING_ON_DARK = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'inverse' | 'onDark';
export type ButtonSize = 'md' | 'lg';

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-semibold transition-colors motion-reduce:transition-none';

const BUTTON_SIZES: Record<ButtonSize, string> = {
  md: 'min-h-11 px-4 text-sm',
  lg: 'min-h-12 px-6 text-[0.9375rem]'
};

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: `bg-teal-700 text-white shadow-sm hover:bg-teal-800 ${FOCUS_RING}`,
  secondary: `border border-slate-200 bg-white text-slate-900 shadow-sm hover:border-slate-300 hover:bg-slate-50 ${FOCUS_RING}`,
  ghost: `text-slate-700 hover:bg-slate-100 hover:text-slate-900 ${FOCUS_RING}`,
  inverse: `bg-white text-teal-800 shadow-md hover:bg-teal-50 ${FOCUS_RING_ON_DARK}`,
  onDark: `text-white underline decoration-white/50 underline-offset-4 hover:decoration-white ${FOCUS_RING_ON_DARK}`
};

/** The classes of a landing button or button-like link. */
export function buttonClasses(variant: ButtonVariant = 'primary', size: ButtonSize = 'md'): string {
  return `${BUTTON_BASE} ${BUTTON_SIZES[size]} ${BUTTON_VARIANTS[variant]}`;
}

/** A white card on the light canvas: micro border and an ultra-subtle shadow. */
export const CARD = 'rounded-lg border border-slate-200 bg-white shadow-[0_1px_2px_0_rgba(15,23,42,0.04)]';

/** The square that holds a line icon (teal on a neutral tint). */
export const ICON_BOX = 'flex shrink-0 items-center justify-center rounded-md bg-slate-100 text-teal-700';

/** The small uppercase label above a title. */
export const EYEBROW = 'text-xs font-bold uppercase tracking-wider text-teal-700';
