import { DEMO_PAYMENTS_ENABLED } from 'astro:env/server';
import { INVALID_FLAG_WARNING, parseDemoPaymentsFlag } from './demo-payment-flag.ts';

/** Server-side only (SSR): whether the demo payment pages and links are shown (see demo-payment-flag.ts). */
let warned = false;

export function demoPaymentsEnabled(): boolean {
  const flag = parseDemoPaymentsFlag(DEMO_PAYMENTS_ENABLED);
  if (!flag.valid && !warned) {
    warned = true;
    console.warn(INVALID_FLAG_WARNING); // the value itself is never logged
  }
  return flag.enabled;
}
