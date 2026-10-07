import { apiFetch } from './client';

/**
 * Physical structure (HU-013): Bloque → Torre → Apartamento.
 *
 * `name` ("TO2") and `label` ("B1-TO2-APTO-104") always come from the backend;
 * the frontend never builds identifiers. `child_label_prefix` is only used for
 * the form preview (prefix + typed number) and is not the official identifier.
 */

export interface BlockRef {
  id: number;
  number: number;
  name: string;
  label: string;
}

export type TowerRef = BlockRef;

export interface Block extends BlockRef {
  version: number;
  active_towers: number;
  active_units: number;
}

export interface Tower extends BlockRef {
  block_id: number;
  version: number;
  active_units: number;
  block: BlockRef;
}

export interface Unit extends BlockRef {
  block_id: number;
  tower_id: number;
  version: number;
  block: BlockRef;
  tower: TowerRef;
}

export interface BlockList {
  items: Block[];
  child_label_prefix: string;
}

export interface TowerList {
  block: Block;
  items: Tower[];
  child_label_prefix: string;
}

export interface UnitList {
  block: BlockRef;
  tower: Tower;
  items: Unit[];
  child_label_prefix: string;
}

/** Browser only. collectionPath: "/blocks", "/blocks/{id}/towers" or "/towers/{id}/units". */
export function createStructureItem(collectionPath: string, number: number, csrfToken: string): Promise<unknown> {
  return apiFetch(collectionPath, { method: 'POST', body: JSON.stringify({ number }), csrfToken });
}

/** Browser only. itemPath: "/blocks/{id}", "/towers/{id}" or "/units/{id}". */
export function renumberStructureItem(
  itemPath: string,
  number: number,
  version: number,
  csrfToken: string
): Promise<unknown> {
  return apiFetch(itemPath, { method: 'PATCH', body: JSON.stringify({ number, version }), csrfToken });
}

/** Browser only. Soft delete; the version the page was rendered with. */
export function deleteStructureItem(itemPath: string, version: number, csrfToken: string): Promise<unknown> {
  return apiFetch(`${itemPath}?version=${encodeURIComponent(version)}`, { method: 'DELETE', csrfToken });
}

/** Browser only. Active blocks, towers of a block and apartments of a tower (the obligations pickers). */
export function getBlocks(): Promise<BlockList> {
  return apiFetch<BlockList>('/blocks', { cache: 'no-store' });
}

export function getTowers(blockId: number): Promise<TowerList> {
  return apiFetch<TowerList>(`/blocks/${blockId}/towers`, { cache: 'no-store' });
}

export function getUnits(towerId: number): Promise<UnitList> {
  return apiFetch<UnitList>(`/towers/${towerId}/units`, { cache: 'no-store' });
}
