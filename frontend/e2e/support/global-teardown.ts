import { cleanDatabase } from './data';

/** Leaves the *_test database empty, as the integration tests expect it. */
export default async function globalTeardown(): Promise<void> {
  cleanDatabase();
}
