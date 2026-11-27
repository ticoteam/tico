// The one way the app talks to the server: same-origin cookie, JSON in and out, an Idempotency-Key on every write,
// retries through a restart, and refusals as typed errors. Only src/api, src/data, src/live and features' queries.ts may call it (lint).
import type { ApiOperations, Problem } from '@/api/schema';
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
  /** Pass the same key to resend a write that may have landed; a new one is made otherwise. A write given its key is
      also sent again through a restart or a dropped connection (RETRY_FOR_MS); one without is sent once. */
  idempotencyKey?: string;
  /** Send it once, retries or not: the caller has its own (the live stream's probe). */
  once?: boolean;
}

// A restart (an update, a deploy) answers 503 "Tico is starting" from the proxy for about 30 s, and a dropped
// connection answers nothing. A read, and a write whose caller passed its Idempotency-Key (the server keeps the first
// answer under it, so a resend lands once), are sent again until RETRY_FOR_MS has passed; a write without one is not,
// since it may have landed. Reads also retry gateway errors (502 and 504). Waits grow from RETRY_FIRST_MS to RETRY_MAX_MS,
// or follow the answer's Retry-After.
export const RETRY_FOR_MS = 60_000;
const RETRY_FIRST_MS = 500;
const RETRY_MAX_MS = 5_000;
const STARTING = /^Tico is starting/;
const STOPPED = /^This Tico company is stopped/;

async function request<T>(method: Method, path: ApiPath, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  let payload: BodyInit | undefined;
  if (body instanceof FormData || body instanceof Blob) payload = body;
  else if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
    payload = JSON.stringify(body);
  }
  if (method !== 'GET') headers.set('Idempotency-Key', opts.idempotencyKey ?? newId());
  const resend = !opts.once && (method === 'GET' || opts.idempotencyKey !== undefined);
  const until = Date.now() + RETRY_FOR_MS;

  for (let wait = RETRY_FIRST_MS; ; wait = Math.min(wait * 2, RETRY_MAX_MS)) {
    // Null when nothing answered (a drop, the server down with no proxy holding it).
    let res: Response | null;
    try {
      res = await fetch(path, { method, headers, body: payload ?? null, credentials: 'same-origin', signal: opts.signal ?? null });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      res = null;
    }
    if (res?.ok) {
      if (res.status === 204) return undefined as T;
      const type = res.headers.get('Content-Type') ?? '';
      return (type.includes('json') ? await res.json() : await res.text()) as T;
    }
    const failed = res === null ? new ApiError(0, 'network', 'The server did not answer', true) : await toError(res);
    // Offline by the browser's word: the write's own holding (data/held.ts) takes over at once.
    // A missing file needs storage to be restored. Let the viewer show the failure while other reads keep retrying through a restart.
    const storageRead = method === 'GET' && failed.code === 'blob_storage';
    const gatewayRead = method === 'GET' && (failed.status === 502 || failed.status === 504);
    const again = failed.status === 0 ? navigator.onLine !== false : (failed.status === 503 || gatewayRead) && failed.retryable && !storageRead;
    const pause = Math.min((res && retryAfter(res)) ?? wait, RETRY_MAX_MS * 2);
    if (!resend || !again || Date.now() + pause > until) throw gaveUp(failed, resend && again);
    await sleep(pause, opts.signal);
  }
}

/** The last failure, said plainly when it was a restart or a drop that outlasted the retries. */
function gaveUp(err: ApiError, retried: boolean): ApiError {
  if (!retried) return err;
  if (err.code === 'starting') return new ApiError(503, 'starting', 'Tico is restarting and did not come back in time. Try again in a minute.', true);
  if (err.status === 0) return new ApiError(0, 'network', 'The server did not answer for a minute. Check the connection and try again.', true);
  return err;
}

function retryAfter(res: Response): number | null {
  const s = Number(res.headers.get('Retry-After'));
  return Number.isFinite(s) && s > 0 ? s * 1000 : null;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const done = () => {
      signal?.removeEventListener('abort', stop);
      resolve();
    };
    const timer = setTimeout(done, ms);
    function stop() {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    }
    signal?.addEventListener('abort', stop, { once: true });
  });
}

/** A read made outside `api` (a file's own bytes) that got no answer: nothing answered, or the answer broke off on the
    way (fetch, or reading its body, threw). Failed as `api`'s own reads fail (status 0, read again by itself); a
    caller's abort stays an abort. */
export async function answered<T>(read: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  try {
    return await read;
  } catch (err) {
    if (signal?.aborted || (err instanceof DOMException && err.name === 'AbortError')) throw err;
    throw new ApiError(0, 'network', 'The server did not answer', true);
  }
}

/** A failed answer as an ApiError: its Problem, or the proxies' own plain answers (a restart, a stopped company). */
export async function toError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => '');
  let problem: Problem['error'] | undefined;
  try {
    problem = (JSON.parse(text) as Partial<Problem>).error;
  } catch {
    // not JSON: a proxy or a crash page
  }
  if (!problem?.code) {
    // The proxies' own plain answers (backend/caddy_assets.py, setup/cell.py HOLD_BODY): a restart, or a stopped company.
    if (res.status === 503 && STARTING.test(text)) return new ApiError(503, 'starting', text.trim(), true);
    if (res.status === 503 && STOPPED.test(text)) return new ApiError(503, 'stopped', text.trim(), false);
    return new ApiError(res.status, `http_${res.status}`, res.statusText, res.status >= 500);
  }
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

/* Typed operations. `operation('PATCH /api/v2/groups/{gid}', { path: { gid }, body: { lead } }, { idempotencyKey })` is `api.patch` with
   the OpenAPI v2 spec's types checked by tsc: a path or method the API does not have, a missing or wrong path or query
   parameter, a body field it does not take, or a use of a field its answer does not have fails `npm run typecheck`.
   The types are ApiOperations in schema.d.ts (scripts/gen_ui_types.py, from backend/openapi_v2.py). The call goes
   through `request`, so the caller still owns its Idempotency-Key, its abort signal and `once`, and retries are the
   same; caching, optimistic writes and the live stream's invalidation stay with src/data (`write`, query keys).
   New calls use it, and a feature moves to it when it is next touched (the Team chart's groups were first:
   features/company/queries.ts). `api.get<T>` stays for a route outside the spec. */

export type OperationName = keyof ApiOperations;
type Empty = Record<never, never>;
type PathInput<O extends ApiOperations[OperationName]> = [O['path']] extends [never] ? { path?: never } : { path: O['path'] };
type QueryInput<O extends ApiOperations[OperationName]> = [O['query']] extends [never]
  ? { query?: never }
  : Empty extends O['query']
    ? { query?: O['query'] }
    : { query: O['query'] };
type BodyInput<O extends ApiOperations[OperationName]> = [O['body']] extends [never] ? { body?: never } : { body: O['body'] };
/** What one operation takes: its path parameters, its query and its body, each only where the spec has one. */
export type OperationInput<K extends OperationName> = PathInput<ApiOperations[K]> & QueryInput<ApiOperations[K]> & BodyInput<ApiOperations[K]>;
/** What one operation answers (`void` for an empty answer, `string` for a stream or file). */
export type OperationAnswer<K extends OperationName> = ApiOperations[K]['response'];
type OperationArgs<K extends OperationName> =
  Empty extends OperationInput<K> ? [input?: OperationInput<K>, opts?: RequestOptions] : [input: OperationInput<K>, opts?: RequestOptions];

/** The URL for one call: each `{name}` from `path`, encoded, then the query's set values in the order given (a list
    as the key repeated; undefined and null left out). */
function operationUrl(template: string, path: Record<string, string | number> = {}, query: Record<string, unknown> = {}): ApiPath {
  const filled = template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = path[name];
    if (value === undefined) throw new Error(`${template} needs ${name}`);
    return encodeURIComponent(String(value));
  });
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    for (const one of Array.isArray(value) ? value : [value]) if (one !== undefined && one !== null) search.append(key, String(one));
  }
  const text = search.toString();
  return (text ? `${filled}?${text}` : filled) as ApiPath;
}

/** One typed call by its "METHOD /path" in the OpenAPI v2 spec. */
export function operation<K extends OperationName>(name: K, ...[input, opts]: OperationArgs<K>): Promise<OperationAnswer<K>> {
  const space = name.indexOf(' ');
  const method = name.slice(0, space) as Method;
  const given = (input ?? {}) as { path?: Record<string, string | number>; query?: Record<string, unknown>; body?: unknown };
  return request<OperationAnswer<K>>(method, operationUrl(name.slice(space + 1), given.path, given.query), given.body, opts);
}
