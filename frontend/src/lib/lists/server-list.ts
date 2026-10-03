import { ApiError } from '../api/errors.ts';

/**
 * Server-side list controller (pure, tested with node --test). Shared by the
 * residents list (HU-006) and the payments summary (HU-004).
 *
 * Search, filters, sort and pagination are done by the backend: this only keeps
 * the chosen options, asks for the page and decides what to show. A newer
 * request always wins over an older answer; typed text is searched after a
 * pause (debounce) or right away when another option changes.
 */

export type SortOrder = 'asc' | 'desc';

export interface ListQuery<S extends string> {
  page: number;
  pageSize: number;
  search: string;
  sortBy: S;
  sortOrder: SortOrder;
}

export interface PageResult<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export type ListState<Q, P> =
  | { kind: 'loading'; query: Q }
  | { kind: 'results'; query: Q; page: P }
  | { kind: 'empty'; query: Q }
  | { kind: 'no-results'; query: Q }
  | { kind: 'error'; query: Q; message: string };

export interface ServerListConfig<Q> {
  toQueryString: (query: Q) => string;
  /** Any search or filter applied (then "no results" instead of "empty"). */
  isFiltered: (query: Q) => boolean;
  pageSizes: readonly number[];
  defaultPageSize: number;
  pageMax: number;
  searchMaxLength: number;
  debounceMs: number;
  forbiddenMessage: string;
  connectionError: string;
}

export interface ServerListDeps<Q, P> {
  load: (queryString: string) => Promise<P>;
  render: (state: ListState<Q, P>) => void;
  /** Keeps the options in the page URL (history.replaceState). */
  remember: (queryString: string) => void;
  /** Used when the session ended (401): back to the login page. */
  navigate: (path: string) => void;
  schedule?: (callback: () => void, ms: number) => () => void;
}

function defaultSchedule(callback: () => void, ms: number): () => void {
  const timer = setTimeout(callback, ms);
  return () => clearTimeout(timer);
}

/** Clicking a column: same column toggles the direction; another column starts with `firstOrder`. */
export function sortedBy<S extends string, Q extends ListQuery<S>>(query: Q, field: S, firstOrder: SortOrder = 'asc'): Q {
  const other: SortOrder = firstOrder === 'asc' ? 'desc' : 'asc';
  const sortOrder: SortOrder = query.sortBy === field && query.sortOrder === firstOrder ? other : firstOrder;
  return { ...query, sortBy: field, sortOrder, page: 1 };
}

export function createServerList<S extends string, Q extends ListQuery<S>, P extends PageResult<unknown>>(
  initial: Q,
  config: ServerListConfig<Q>,
  deps: ServerListDeps<Q, P>
) {
  const schedule = deps.schedule ?? defaultSchedule;
  let query = initial;
  let latest = 0;
  let cancelSearch: (() => void) | null = null;

  async function fetchPage(next: Q): Promise<void> {
    query = next;
    const request = ++latest;
    const queryString = config.toQueryString(query);
    deps.remember(queryString);
    deps.render({ kind: 'loading', query });
    let page: P;
    try {
      page = await deps.load(queryString);
    } catch (error) {
      if (request !== latest) return; // a newer request is on its way
      if (error instanceof ApiError && error.status === 401) {
        deps.navigate('/login');
        return;
      }
      const message =
        error instanceof ApiError && error.status === 403
          ? config.forbiddenMessage
          : error instanceof ApiError && error.status === 422
            ? error.message
            : config.connectionError;
      deps.render({ kind: 'error', query, message });
      return;
    }
    if (request !== latest) return; // an older answer never replaces a newer one
    if (page.total === 0) {
      deps.render({ kind: config.isFiltered(query) ? 'no-results' : 'empty', query });
    } else if (page.items.length === 0 && query.page > page.total_pages) {
      // The page no longer exists (fewer rows now): go to the last one.
      await fetchPage({ ...query, page: page.total_pages });
    } else {
      deps.render({ kind: 'results', query, page });
    }
  }

  /** Text typed but not searched yet: any other action searches it right away. */
  let draft: string | null = null;

  function withDraft(next: Q): Q {
    cancelSearch?.();
    cancelSearch = null;
    const typed = draft;
    draft = null;
    return typed !== null && typed.trim() !== next.search.trim() ? { ...next, search: typed, page: 1 } : next;
  }

  return {
    get query(): Q {
      return query;
    },
    start: () => fetchPage(query),
    /** Debounced: one request after the user stops typing. */
    search(text: string): void {
      cancelSearch?.();
      draft = text.slice(0, config.searchMaxLength);
      cancelSearch = schedule(() => {
        cancelSearch = null;
        const next = withDraft(query);
        if (next !== query) void fetchPage(next);
      }, config.debounceMs);
    },
    /** Filters other than the search (status, resident, dates): back to page 1. */
    update(changes: Partial<Q>): Promise<void> {
      return fetchPage(withDraft({ ...query, ...changes, page: 1 }));
    },
    sort(field: S, firstOrder: SortOrder = 'asc'): Promise<void> {
      return fetchPage(withDraft(sortedBy<S, Q>(query, field, firstOrder)));
    },
    goToPage(page: number): Promise<void> {
      return fetchPage(withDraft({ ...query, page: Math.min(Math.max(1, page), config.pageMax) }));
    },
    setPageSize(pageSize: number): Promise<void> {
      const size = config.pageSizes.includes(pageSize) ? pageSize : config.defaultPageSize;
      return fetchPage(withDraft({ ...query, pageSize: size, page: 1 }));
    },
    /** Same options again: retry after an error, or show the latest data. */
    refresh: () => fetchPage(withDraft(query))
  };
}
