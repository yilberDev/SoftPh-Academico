import { ApiError } from './errors';
import { serverApiGet } from './server';
import type { BlockList, TowerList, UnitList } from './structure';

/** Server-side only (SSR): read-only calls for the structure pages (HU-013). */

// Database ids are positive BIGINTs; anything else never reaches the API path.
const ID_PATTERN = /^[1-9]\d{0,18}$/;

export function isValidId(value: string | undefined): value is string {
  return value !== undefined && ID_PATTERN.test(value);
}

export function listBlocks(request: Request): Promise<BlockList> {
  return serverApiGet<BlockList>('/blocks', request);
}

export function listTowers(blockId: string, request: Request): Promise<TowerList> {
  return serverApiGet<TowerList>(`/blocks/${blockId}/towers`, request);
}

export function listUnits(towerId: string, request: Request): Promise<UnitList> {
  return serverApiGet<UnitList>(`/towers/${towerId}/units`, request);
}

/** Result of loading a page: the data, or why it could not be shown. */
export type PageLoad<T> = { data: T } | { notFound: true } | { unauthenticated: true };

export async function loadPage<T>(load: () => Promise<T>): Promise<PageLoad<T>> {
  try {
    return { data: await load() };
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
      return { notFound: true };
    }
    if (error instanceof ApiError && error.status === 401) {
      // The session expired between the middleware check and this call.
      return { unauthenticated: true };
    }
    throw error;
  }
}
