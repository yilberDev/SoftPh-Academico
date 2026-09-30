import { serverApiGet } from './server';

export interface HealthStatus {
  status: 'ok';
  database: 'ok' | 'unavailable';
}

export function getHealth(request?: Request): Promise<HealthStatus> {
  return serverApiGet<HealthStatus>('/health', request);
}
