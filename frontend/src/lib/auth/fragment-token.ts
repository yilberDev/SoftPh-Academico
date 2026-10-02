/**
 * One-time tokens that arrive in the URL fragment (#token=...): account
 * activation (HU-019) and password reset (HU-016). Pure module.
 *
 * The page reads the token once, removes it from the address bar right away
 * and keeps it only in memory; it is sent to the backend only in POST bodies.
 */

// 32 random bytes as URL-safe base64 (the backend's generator).
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

/** The token of "#token=..." if it looks like one; null otherwise. */
export function readToken(hash: string): string | null {
  // Only a fragment ("#..."): a token in a query string is never accepted.
  if (!hash.startsWith('#')) {
    return null;
  }
  const token = new URLSearchParams(hash.slice(1)).get('token');
  return token !== null && TOKEN_PATTERN.test(token) ? token : null;
}
