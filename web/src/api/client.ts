// The one way the app talks to the server: same-origin cookie, JSON in and out, an Idempotency-Key on every write,
// and refusals as typed errors. Only src/api, src/data, src/live and features' queries.ts may call it (lint).
import type { Problem } from '@/api/schema';
import { newId } from '@/lib/ids';

/** A refusal or failure. `code` is the server's Problem code, `network` when nothing answered, `http_<status>` otherwise. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  /** On a 401 with built-in sign-in: the login path. */
  readonly signIn: string | null;
  readonly extra: Record<string, unknown>;

  constructor(status: number, code: string, detail: string, retryable = false, signIn: string | null = null, extra: Record<string, unknown> = {}) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.signIn = signIn;
    this.extra = extra;
  }
}

export type ApiPath = `/api/${string}`;
type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export interface RequestOptions {
  signal?: AbortSignal;
  /** Pass the same key to resend a write that may have landed; a new one is made otherwise. */
  idempotencyKey?: string;
}

async function request<T>(method: Method, path: ApiPath, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  let payload: BodyInit | undefined;
  if (body instanceof FormData || body instanceof Blob) payload = body;
  else if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
    payload = JSON.stringify(body);
  }
  if (method !== 'GET') headers.set('Idempotency-Key', opts.idempotencyKey ?? newId());

  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload ?? null, credentials: 'same-origin', signal: opts.signal ?? null });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(0, 'network', 'The server did not answer', true);
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get('Content-Type') ?? '';
  return (type.includes('json') ? await res.json() : await res.text()) as T;
}

async function toError(res: Response): Promise<ApiError> {
  let problem: Problem['error'] | undefined;
  try {
    problem = ((await res.json()) as Partial<Problem>).error;
  } catch {
    // not JSON: a proxy or a crash page
  }
  if (!problem?.code) return new ApiError(res.status, `http_${res.status}`, res.statusText, res.status >= 500);
  const { code, detail, retryable, sign_in, ...extra } = problem;
  return new ApiError(res.status, code, detail, retryable ?? false, sign_in ?? null, extra);
}

export const api = {
  get: <T>(path: ApiPath, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
  post: <T>(path: ApiPath, body?: unknown, opts?: RequestOptions) => request<T>('POST', path, body, opts),
  put: <T>(path: ApiPath, body?: unknown, opts?: RequestOptions) => request<T>('PUT', path, body, opts),
  patch: <T>(path: ApiPath, body?: unknown, opts?: RequestOptions) => request<T>('PATCH', path, body, opts),
  /** `body` only for the few routes that take one on DELETE (the server answers 422 without it). */
  delete: <T>(path: ApiPath, opts?: RequestOptions, body?: unknown) => request<T>('DELETE', path, body, opts),
};
