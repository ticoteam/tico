// The one way the app talks to the server: same-origin cookie, JSON in and out, an Idempotency-Key on every write,
// retries through a restart, and refusals as typed errors. Only src/api, src/data, src/live and features' queries.ts may call it (lint).
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
  /** Pass the same key to resend a write that may have landed; a new one is made otherwise. A write given its key is
      also sent again through a restart or a dropped connection (RETRY_FOR_MS); one without is sent once. */
  idempotencyKey?: string;
  /** Send it once, retries or not: the caller has its own (the live stream's probe). */
  once?: boolean;
}

// A restart (an update, a deploy) answers 503 "Tico is starting" from the proxy for about 30 s, and a dropped
// connection answers nothing. A read, and a write whose caller passed its Idempotency-Key (the server keeps the first
// answer under it, so a resend lands once), are sent again until RETRY_FOR_MS has passed; a write without one is not,
// since it may have landed. Waits grow from RETRY_FIRST_MS to RETRY_MAX_MS, or follow the answer's Retry-After.
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
    const again = failed.status === 0 ? navigator.onLine !== false : failed.status === 503 && failed.retryable;
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

async function toError(res: Response): Promise<ApiError> {
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
