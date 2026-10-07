/** Standard error body returned by the API (see backend-fastapi skill). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: { field?: string; message: string }[];
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorBody['error']['details'];
  /** The whole error body: a few errors carry more than code, message and details (e.g. a new preview). */
  readonly body: ApiErrorBody | null;

  constructor(status: number, body: ApiErrorBody | null) {
    super(body?.error.message ?? 'Ocurrió un error inesperado. Intenta de nuevo.');
    this.name = 'ApiError';
    this.status = status;
    this.code = body?.error.code ?? 'UNEXPECTED_RESPONSE';
    this.details = body?.error.details ?? [];
    this.body = body;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === 'object' &&
    value !== null &&
    'error' in value &&
    typeof (value as ApiErrorBody).error?.code === 'string'
  );
}

/** Parses a JSON response, throwing ApiError for non-2xx statuses. */
export async function parseResponse<T>(response: Response): Promise<T> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, isApiErrorBody(body) ? body : null);
  }
  return body as T;
}
