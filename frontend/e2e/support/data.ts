import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

/**
 * E2E data on the *_test database, through backend/tests/e2e/seed.py (the backend
 * services, never SQL from here). The passwords are generated for each run in
 * global-setup.ts, live only in this process environment and are never printed.
 */

const BACKEND = fileURLToPath(new URL('../../../backend', import.meta.url));

export interface Seed {
  today: string; // Bogotá, YYYY-MM-DD
  admin_email: string;
  resident_email: string;
  resident_name: string;
  blocks: Record<string, number>;
  towers: Record<string, number>;
  units: Record<string, number>;
  charges: { history: number; debt: number };
  laura_id: number;
  /** Pedro's feedback (HU-009) and one stored before HU-009 (category and urgency null). Laura has none. */
  feedback: { pedro: number; historical: number };
}

function seed(action: 'migrate' | 'reset' | 'clean'): string {
  return execFileSync('.venv/bin/python', ['-m', 'tests.e2e.seed', action], {
    cwd: BACKEND,
    env: process.env,
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe']
  });
}

export const migrateDatabase = (): void => void seed('migrate');
export const cleanDatabase = (): void => void seed('clean');

/** Empties the *_test database and creates the scenario (seed.py); ids and labels only. */
export function resetDatabase(): Seed {
  const lines = seed('reset').trim().split('\n');
  return JSON.parse(lines[lines.length - 1]) as Seed;
}

export function adminPassword(): string {
  return required('E2E_ADMIN_PASSWORD');
}

export function residentPassword(): string {
  return required('E2E_RESIDENT_PASSWORD');
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set (global-setup.ts generates it).`);
  return value;
}

// --- Calendar dates (Bogotá "today" from the backend) --------------------------------------------

export function addDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** "2026-10" of a date; with `months`, that many months later. */
export function periodOf(isoDate: string, months = 0): string {
  const [year, month] = isoDate.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1 + months, 1));
  return date.toISOString().slice(0, 7);
}
