import { ApiError } from '../api/errors.ts';
import type { BlockList, TowerList, UnitList } from '../api/structure.ts';
import { CONNECTION_ERROR } from './charge-format.ts';

/**
 * Obligations extension: Bloque → Torre → Apartamento selectors (pure, tested
 * with node --test), shared by the new obligation form and the list filters.
 *
 * Uses the HU-013 endpoints as they are (active items only). Choosing a level
 * clears the levels below it, and an answer for an older choice never replaces
 * the newer one.
 */

export interface PickerOption {
  id: number;
  text: string;
}

export interface UnitSelection {
  blockId: number | null;
  towerId: number | null;
  unitId: number | null;
}

export const NO_SELECTION: UnitSelection = { blockId: null, towerId: null, unitId: null };

export interface UnitPickerState extends UnitSelection {
  blocks: PickerOption[];
  towers: PickerOption[];
  units: PickerOption[];
  loading: 'blocks' | 'towers' | 'units' | null;
  error: string | null;
}

export const LOAD_ERROR = 'No fue posible cargar los bloques, torres y apartamentos. Intenta de nuevo.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para consultar las unidades residenciales.';

export interface UnitPickerDeps {
  loadBlocks: () => Promise<BlockList>;
  loadTowers: (blockId: number) => Promise<TowerList>;
  loadUnits: (towerId: number) => Promise<UnitList>;
  render: (state: UnitPickerState) => void;
  navigate: (path: string) => void;
}

export function createUnitPicker(deps: UnitPickerDeps) {
  let state: UnitPickerState = { ...NO_SELECTION, blocks: [], towers: [], units: [], loading: null, error: null };
  let latest = 0;

  function set(next: Partial<UnitPickerState>): void {
    state = { ...state, ...next };
    deps.render(state);
  }

  async function load<T>(level: 'blocks' | 'towers' | 'units', request: () => Promise<T>): Promise<T | null> {
    const id = ++latest;
    set({ loading: level, error: null });
    try {
      const result = await request();
      return id === latest ? result : null;
    } catch (error) {
      if (id !== latest) return null;
      if (error instanceof ApiError && error.status === 401) {
        deps.navigate('/login');
        return null;
      }
      set({
        loading: null,
        error: error instanceof ApiError && error.status === 403 ? FORBIDDEN_MESSAGE : error instanceof ApiError ? LOAD_ERROR : CONNECTION_ERROR
      });
      return null;
    }
  }

  async function chooseBlock(blockId: number | null, towerId: number | null = null, unitId: number | null = null): Promise<void> {
    set({ blockId, towerId: null, unitId: null, towers: [], units: [] });
    if (blockId === null) {
      latest++;
      set({ loading: null, error: null });
      return;
    }
    const list = await load('towers', () => deps.loadTowers(blockId));
    if (!list) return;
    const towers = list.items.map((tower) => ({ id: tower.id, text: tower.name }));
    set({ towers, loading: null });
    if (towerId !== null && towers.some((tower) => tower.id === towerId)) await chooseTower(towerId, unitId);
  }

  async function chooseTower(towerId: number | null, unitId: number | null = null): Promise<void> {
    set({ towerId, unitId: null, units: [] });
    if (towerId === null) {
      latest++;
      set({ loading: null, error: null });
      return;
    }
    const list = await load('units', () => deps.loadUnits(towerId));
    if (!list) return;
    const units = list.items.map((unit) => ({ id: unit.id, text: unit.label }));
    set({ units, loading: null, unitId: unitId !== null && units.some((unit) => unit.id === unitId) ? unitId : null });
  }

  return {
    get state(): UnitPickerState {
      return state;
    },
    /** Loads the blocks and, when given (page URL), restores a previous choice that still exists. */
    async start(initial: UnitSelection = NO_SELECTION): Promise<void> {
      const list = await load('blocks', deps.loadBlocks);
      if (!list) return;
      const blocks = list.items.map((block) => ({ id: block.id, text: block.name }));
      set({ blocks, loading: null });
      if (initial.blockId !== null && blocks.some((block) => block.id === initial.blockId)) {
        await chooseBlock(initial.blockId, initial.towerId, initial.unitId);
      }
    },
    selectBlock: (blockId: number | null): Promise<void> => chooseBlock(blockId),
    selectTower: (towerId: number | null): Promise<void> => chooseTower(towerId),
    selectUnit(unitId: number | null): void {
      set({ unitId: unitId !== null && state.units.some((unit) => unit.id === unitId) ? unitId : null });
    },
    /** Back to no selection (the blocks stay loaded). */
    clear(): void {
      latest++;
      set({ ...NO_SELECTION, towers: [], units: [], loading: null, error: null });
    },
    /** Draws the current state again (e.g. after the form was enabled or disabled). */
    redraw(): void {
      deps.render(state);
    },
    selection(): UnitSelection {
      return { blockId: state.blockId, towerId: state.towerId, unitId: state.unitId };
    },
    unitLabel(): string | null {
      return state.units.find((unit) => unit.id === state.unitId)?.text ?? null;
    }
  };
}

/** A select value ("" or a positive id) → id or null. */
export function idOf(value: string): number | null {
  return /^[1-9]\d{0,15}$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
}
