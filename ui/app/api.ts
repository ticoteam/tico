/* ui/app/api.ts — get/post/put/form requests, API errors, toast
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** Per-write options: `timeout` caps the whole write in milliseconds; the callbacks hear each retry and each answer. */
interface ApiWriteOptions {
  timeout?: number | undefined;
  keepalive?: boolean | undefined;
  onRetry?: ((attempt: number, status: number) => void) | undefined;
  onResponse?: ((status: number) => void) | undefined;
  /** The route answers 204 or an empty body by design, so an empty success counts as saved (writeRequestOnce). */
  noContent?: boolean | undefined;
}

/** A tagged list the page keeps: the ETag the server sent with it, and the body it came with. */
interface ApiTagged {
  tag: string;
  text: string;
}

/** One GET's answer: the response, its body text, and the kept copy a 304 answers from (when there was one). */
interface ApiRawGet {
  r: Response;
  text: string;
  kept?: ApiTagged | undefined;
}

/** A GET started before the page asks for it (getPrefetch), and when. */
interface ApiPrefetch {
  at: number;
  raw: Promise<ApiRawGet | null>;
}

// The parsed JSON of an answer when the caller names no type. It is any, as these helpers have always returned it: typing
// it unknown breaks the converted callers that read its fields (ui/app/tools.ts, docs-page.ts and others, about 50 strict
// findings). A caller that wants a type passes it to get<T>() or types its variable. This is the one `any` in this file.
// biome-ignore lint/suspicious/noExplicitAny: the untyped answer is any by contract (see the comment above)
type UntypedJson = any;

const bytes = (n: number): string => n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(0)} KB` : n < 1073741824 ? `${(n/1048576).toFixed(1)} MB` : `${(n/1073741824).toFixed(1)} GB`;
// A task cap's 409 (redesign CONTRACT §1) says why in `message`; `error` is only its code. The task engine's refusal is
// flat: `error` is its rule and `detail` the one line a person reads.
const apiErrorRaw = (j: ApiFailureBody, r: Response): string => (j.error === 'task_cap' && j.message) || (j.error && typeof j.error === 'object' ? j.error.detail : '') || (typeof j.error === 'string' && typeof j.detail === 'string' && j.detail) || (typeof j.error === 'string' ? j.error : '') || (Array.isArray(j.detail) ? j.detail.map(e => `${e.loc?.slice(1).join('.') || 'Request'}: ${e.msg}`).join('; ') : '') || r.statusText;
// A refused setup step's reference (backend/setup_failures.py), after the message, so what a person sees or sends as a
// screenshot matches the row the server kept.
const apiErrorRef = (j: ApiFailureBody): string => {
  const ref = j.error && typeof j.error === 'object' ? j.error['ref'] : '';
  return typeof ref === 'string' && /^sf-[0-9a-f]{8}$/.test(ref) ? ` · ${ref}` : '';
};
const apiError = (j: ApiFailureBody, r: Response): string => {
  const raw = apiErrorRaw(j, r) + apiErrorRef(j);
  return String(raw).replace(/\. Rewrite the title\/body to fix these writing errors and retry\. Do not ask the human to waive formatting rules\./g, '. Please edit the title or details and try again.')
    .replace(/start the title with a verb \(it starts "([^"]+)"\)/g, 'Start the title with an action, such as “Email” or “Review”');
};
// The message is what a person reads; `body` is the one extra fact a refusal carries, such as the
// task a duplicate feature request already has (`backend/store.py`, Problem.extra).
const apiFailure = (j: ApiFailureBody, r: Response): ApiFailure => Object.assign(new Error(apiError(j, r)), {body: j, status: r.status});
// Signed out, by any sign-in (session.js): the built-in login page, or the banner. A locked Tico Cloud company's 402
// `locked` shows Pay to continue at once (cloud-access.ts).
const signInRedirect = (r: Response, j: ApiFailureBody): boolean => {
  const error = typeof j?.error === 'object' ? j.error : null;
  // The 402's `cloud` is the company's cloud access state as the server sends it. cloudAccessLocked spreads it over
  // its own state with defaults, so a partial object is fine; it is not checked against the type here.
  if (r.status === 402 && error?.code === 'locked') cloudAccessLocked(error.cloud as CloudAccessState | undefined);
  return r.status === 401 && sessionCheck(r, j);
};
// A list the server tags (tasks, labels, routines) is asked for again with its tag: unchanged, the answer is a 304
// with no body and the copy kept here is read again, so the server neither loads nor sends it. A few dozen at most.
const GET_TAGGED = new Map<string, ApiTagged>();
// Two readers of the same path at the same moment (the bot page asks for its goals twice, a poll lands while a page
// loads) share one request; each parses its own copy. A write forgets them all, so a read that follows a write never
// gets an answer from before it. `fresh` never joins a read that is already on its way (later readers join it).
const GET_INFLIGHT = new Map<string, Promise<ApiRawGet>>();
// What startup asks for ahead of the page that will need it (getPrefetch): read once, within a few seconds.
const GET_PREFETCHED = new Map<string, ApiPrefetch>();
const getForget = () => { GET_INFLIGHT.clear(); GET_PREFETCHED.clear(); };
// A read that must come from the server's rows, not a copy kept here: its tag goes, so no 304 can answer it.
const getForgetTagged = (test: (p: string) => boolean) => { for (const p of [...GET_TAGGED.keys()]) if (test(p)) GET_TAGGED.delete(p); };
const getRaw = (p: string): Promise<ApiRawGet> => {
  const kept = GET_TAGGED.get(p);
  return fetch(API + p, {cache:'no-store', headers: kept ? {'If-None-Match': kept.tag} : {}}).then(async r => {
    window.TicoObservability?.response(r.status);
    if (r.ok) SESSION.seen = true;
    const text = r.status === 304 ? '' : await r.text().catch(() => '');
    if (r.ok) {
      const tag = r.headers.get('ETag');
      if (r.status !== 304) GET_TAGGED.delete(p);
      if (tag && r.status !== 304) {
        GET_TAGGED.set(p, {tag, text});
        // The map holds at most 40 entries: when a 41st arrives, the oldest goes. (The key is undefined only when the map is empty.)
        if (GET_TAGGED.size > 40) { const oldest = GET_TAGGED.keys().next().value; if (oldest !== undefined) GET_TAGGED.delete(oldest); }
      }
    }
    return {r, text, kept};
  }, error => { void sessionProbe(); throw error; });   // offline, or a proxy's login on another origin
};
const getPrefetch = (p: string) => { if (!GET_PREFETCHED.has(p)) GET_PREFETCHED.set(p, {at: Date.now(), raw: getRaw(p).catch(() => null)}); };
/**
 * A GET of an API path (under /api). Resolves to the parsed JSON; a failed answer throws its apiFailure.
 * `fresh` skips any read already on its way or prefetched.
 */
const get = async <T = UntypedJson>(p: string, {fresh = false}: {fresh?: boolean} = {}): Promise<T> => {
  if (SESSION.out) throw signedOutError();
  let res: ApiRawGet | null | undefined = null;
  const pre = !fresh && GET_PREFETCHED.get(p);
  if (pre) { GET_PREFETCHED.delete(p); if (Date.now() - pre.at < 5000) res = await pre.raw; }
  if (!res) {
    let raw = !fresh && GET_INFLIGHT.get(p);
    if (!raw) {
      raw = getRaw(p);
      GET_INFLIGHT.set(p, raw);
      const done = () => { if (GET_INFLIGHT.get(p) === raw) GET_INFLIGHT.delete(p); };
      raw.then(done, done);
    }
    res = await raw;
  }
  const {r, text, kept} = res;
  if (r.status === 304 && kept) return JSON.parse(kept.text);
  // The parsed body is the server's JSON: signInRedirect and apiFailure read its fields with checks, so it is cast here.
  let j: unknown; try { j = JSON.parse(text); } catch { j = {}; }
  if (!r.ok) { const failure = failureBody(j); signInRedirect(r, failure); throw apiFailure(failure, r); }
  return j as T;
};
// The one cast to a failure body: the answers read through it are checked by apiError, apiFailure and signInRedirect.
const failureBody = (value: unknown): ApiFailureBody => value as ApiFailureBody;
const pendingWrites = new Map<string, string>();
const newRequestId = (): string => globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
  const n = globalThis.crypto?.getRandomValues?.(new Uint8Array(1))[0] ?? Math.floor(Math.random() * 256);
  return ((c === 'x' ? n : (n & 3) | 8) & 15).toString(16);
});
// Every JSON write goes through one path so a PUT carries the same Idempotency-Key and the same
// retry rules as a POST; the method is part of the signature so the two never share a request id.
// `opts.timeout` caps the whole write, retries included: a stalled connection ends as {timedOut, unconfirmed}
// instead of waiting for the browser to give up, and the request id is kept so a retry is never saved twice.
// `opts.onRetry` hears each retry (attempt, status), so a slow send can say it is trying again.
// The error names the write's signature, so the caller can forget the kept request id once the person moves on.
// A success counts as saved only when its body is a JSON object or array, as an upload's does (formRequestOnce): a
// proxy or sign-in page, or a cut-off answer, is retried with the same request id and ends unconfirmed. Only a write
// declared `opts.noContent` counts an empty success; no UI write is declared today (no JSON write's route answers 204).
// A redirect is refused, so a sign-in redirect goes through the signed-out check below.
const writeConfirmation = (text: string | null): object | null => {
  try { const value: unknown = text ? JSON.parse(text) : null; return value && typeof value === 'object' ? value : null; }
  catch { return null; }
};
const writeTimedOut = (ms: number | undefined, signature: string) => Object.assign(new Error(`No answer from the server in ${Math.round(Number(ms) / 1000)} seconds.`), {unconfirmed: true, timedOut: true, signature});
/**
 * One attempt chain for a JSON write: retries reuse the same Idempotency-Key, and `opts.timeout` bounds all of them.
 * `operationId` is a request id to reuse, as the caller kept it.
 */
const writeRequestOnce = async (method: string, p: string, body: unknown = {}, operationId?: string, opts: ApiWriteOptions = {}): Promise<UntypedJson> => {
  if (SESSION.out) throw signedOutError(true);
  // A bot's read-only view (backend/app_view.py, view-only.ts) sends no write: the server would refuse it anyway.
  if (typeof S !== 'undefined' && S.me?.read_only_view) throw Object.assign(new Error('Read-only view'), {status: 403});
  getForget();
  const payload = JSON.stringify(body), signature = method + ' ' + p + '\n' + payload;
  const key = operationId || pendingWrites.get(signature) || newRequestId();
  pendingWrites.set(signature, key);
  const deadline = opts.timeout == null ? Infinity : performance.now() + opts.timeout;
  for (let attempt = 0; attempt < 3; attempt++) {
    const left = deadline - performance.now();
    if (left <= 0) throw writeTimedOut(opts.timeout, signature);
    const abort = new AbortController(), timer = left < Infinity ? setTimeout(() => abort.abort(), left) : 0;
    let status = 0;
    try {
      const r = await fetch(API + p, {method, redirect:'error', headers:{'Content-Type':'application/json', 'Idempotency-Key':key}, body:payload, signal:abort.signal, keepalive:!!opts.keepalive});
      status = r.status;
      opts.onResponse?.(status);
      window.TicoObservability?.response(r.status);
      if (r.ok) SESSION.seen = true;
      // The JSON answer: apiError and signInRedirect read its fields with checks, so it is cast to a failure body here.
      const text = await r.text().catch((error: unknown) => { if (abort.signal.aborted) throw error; return null; });
      const confirmation = writeConfirmation(text), j = failureBody(confirmation ?? {});
      if ([429,502,503,504].includes(r.status)) throw new TypeError(apiError(j, r));
      if (r.ok && !confirmation && !(opts.noContent && text === '')) throw new TypeError('The answer to this write was not valid JSON');
      pendingWrites.delete(signature);
      if (!r.ok) { signInRedirect(r, j); throw apiFailure(j, r); }
      return j;
    } catch (error) {
      if (abort.signal.aborted) throw writeTimedOut(opts.timeout, signature);
      if (!(error instanceof TypeError)) throw error;
      // A proxy that signed the person out answered before Tico saw the write, so nothing was saved.
      // The probe has its own timeout, but this write may have only milliseconds left. Bound the wait by the
      // same abort signal as the request; the shared probe can still finish for its other callers.
      let onAbort: () => void = () => {};
      const expired = new Promise<never>((_, reject) => {
        onAbort = () => reject(writeTimedOut(opts.timeout, signature));
        abort.signal.addEventListener('abort', onAbort, {once: true});
      });
      let signedOut: boolean;
      try { signedOut = await Promise.race([sessionProbe(), expired]); }
      finally { abort.signal.removeEventListener('abort', onAbort); }
      if (abort.signal.aborted || performance.now() >= deadline) throw writeTimedOut(opts.timeout, signature);
      if (signedOut) throw signedOutError(true);
      if (attempt === 2) throw Object.assign(new Error('Could not confirm whether this saved. Retry the same submission in this tab; it will use the same request ID.'), {unconfirmed: true, status, signature});
      opts.onRetry?.(attempt + 1, status);
      await new Promise(resolve => setTimeout(resolve, Math.min(500 * (attempt + 1), Math.max(0, deadline - performance.now()))));
    } finally { clearTimeout(timer); }
  }
};
// The button just pressed shows the write is under way until it settles (styles/skeleton.css), so a slow server
// never looks like an ignored click. Only a press in the last moment counts; a later background save marks nothing.
let LAST_PRESS: {el: Element; at: number} | null = null;
document.addEventListener('click', e => {
  const el = eventElement(e)?.closest('button, [role="button"], input[type="submit"]');
  LAST_PRESS = el ? {el, at: performance.now()} : null;
}, true);
document.addEventListener('submit', e => { if (e.submitter) LAST_PRESS = {el: e.submitter, at: performance.now()}; }, true);
const pressPending = (): (() => void) => {
  const press = LAST_PRESS;
  if (!press || performance.now() - press.at > 400 || !press.el.isConnected || press.el.classList.contains('is-pending')) return () => {};
  const el = press.el;
  el.classList.add('is-pending'); el.setAttribute('aria-busy', 'true');
  return () => { el.classList.remove('is-pending'); el.removeAttribute('aria-busy'); };
};
const writeRequest = async (method: string, p: string, body?: unknown, operationId?: string, opts?: ApiWriteOptions): Promise<UntypedJson> => { const done = pressPending(); try { return await writeRequestOnce(method, p, body, operationId, opts); } finally { done(); } };
const postRequest = (p: string, body: unknown = {}, operationId?: string, opts?: ApiWriteOptions) => writeRequest('POST', p, body, operationId, opts);
const patchRequest = (p: string, body: unknown = {}, operationId?: string) => writeRequest('PATCH', p, body, operationId);
const putRequest = (p: string, body: unknown = {}, operationId?: string) => writeRequest('PUT', p, body, operationId);
/**
 * A POST of an API path (under /api): one JSON write, traced when diagnostics are on.
 * `operationId` is a request id to reuse, as the caller kept it.
 */
const post = (p: string, body: unknown = {}, operationId?: string, opts?: ApiWriteOptions): Promise<UntypedJson> => window.TicoObservability
  ? window.TicoObservability.run(p, () => postRequest(p, body, operationId, opts)) : postRequest(p, body, operationId, opts);
const patch = (p: string, body: unknown = {}, operationId?: string): Promise<UntypedJson> => window.TicoObservability
  ? window.TicoObservability.run(p, () => patchRequest(p, body, operationId)) : patchRequest(p, body, operationId);
const put = (p: string, body: unknown = {}, operationId?: string): Promise<UntypedJson> => window.TicoObservability
  ? window.TicoObservability.run(p, () => putRequest(p, body, operationId)) : putRequest(p, body, operationId);
// WebKit (Safari and the macOS app's web view) can send a file read from disk as an empty or cut-short body
// while a service worker controls the page, which ours does. The same bytes copied into memory send in full.
// Above the cap a file goes as picked, so a large video never sits in the page twice over.
const UPLOAD_COPY_MAX = 100_000_000;
const hexDigest = (buffer: ArrayBuffer): string => Array.from(new Uint8Array(buffer), b => b.toString(16).padStart(2, '0')).join('');
/**
 * The body with each file copied into memory (see UPLOAD_COPY_MAX).
 * `digests` receives each copy's SHA-256 when given.
 */
async function memoryBody(body: Blob | FormData, digests: Map<Blob, string> | null): Promise<Blob | FormData> {
  const copy = async (file: Blob): Promise<Blob> => {
    if (file.size > UPLOAD_COPY_MAX) return file;
    const bytes = await file.arrayBuffer();
    // Only a File has a name and a lastModified; for a plain Blob both were undefined, and the File below gets the same defaults.
    const made = file instanceof File
      ? new File([bytes], file.name, {type: file.type, lastModified: file.lastModified})
      : new File([bytes], '', {type: file.type});
    // One read serves both the copy and the retry signature's digest.
    if (digests) digests.set(made, hexDigest(await crypto.subtle.digest('SHA-256', bytes)));
    return made;
  };
  if (body instanceof Blob) return copy(body);
  const copied = new FormData();
  for (const [name, value] of body.entries()) copied.append(name, value instanceof File ? await copy(value) : value);
  return copied;
}
// Keep multipart and binary retries stable across newly constructed bodies.
/**
 * One multipart or binary POST, retried with the same Idempotency-Key until its answer is confirmed.
 */
const formRequestOnce = async (url: string, body: Blob | FormData, opts: ApiWriteOptions = {}): Promise<Response> => {
  if (SESSION.out) throw signedOutError(true);
  getForget();
  const digests = S.me?.cloud ? new Map<Blob, string>() : null;
  body = await memoryBody(body, digests);
  if (!digests) return fetch(url, {method:'POST', body});
  const fields: unknown[][] = [];
  for (const [name, value] of body instanceof Blob ? [['file', body]] : body.entries()) {
    if (value instanceof Blob) {
      const digest = digests.get(value) ?? hexDigest(await crypto.subtle.digest('SHA-256', await value.arrayBuffer()));
      fields.push([name, (value instanceof File && value.name) || '', value.type, value.size, digest]);
    } else fields.push([name, value]);
  }
  const signature = url + '\n' + JSON.stringify(fields), key = pendingWrites.get(signature) || newRequestId();
  pendingWrites.set(signature, key);
  for (let attempt = 0; attempt < 3; attempt++) {
    let status = 0;
    try {
      const response = await fetch(url, {method:'POST', body, redirect:'error', headers:{'Idempotency-Key':key}});
      status = response.status;
      opts.onResponse?.(status);
      window.TicoObservability?.response(response.status);
      if (response.status === 401) sessionCheck(response, await response.clone().json().catch(() => ({})));
      if ([429,502,503,504].includes(response.status)) throw new TypeError('Upload temporarily unavailable');
      if (response.ok) {
        const confirmation: unknown = await response.clone().json().catch(() => null);
        if (!confirmation || typeof confirmation !== 'object') throw new TypeError('Upload confirmation was not valid JSON');
      }
      pendingWrites.delete(signature);
      return response;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      if (await sessionProbe()) throw signedOutError(true);
      if (attempt === 2) throw Object.assign(new Error('Could not confirm whether this upload saved. Keep the files and retry the same submission in this tab.'), {unconfirmed: true, status});
      opts.onRetry?.(attempt + 1, status);
      await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  // Each attempt above returns or throws; this line only satisfies the type checker.
  throw new Error('Could not confirm whether this upload saved.');
};
const formRequest = async (url: string, body: Blob | FormData, opts?: ApiWriteOptions): Promise<Response> => { const done = pressPending(); try { return await formRequestOnce(url, body, opts); } finally { done(); } };
const formFetch = (url: string, body: Blob | FormData, opts?: ApiWriteOptions): Promise<Response> => window.TicoObservability
  ? window.TicoObservability.run(url.startsWith(API) ? url.slice(API.length) : '', () => formRequest(url, body, opts)) : formRequest(url, body, opts);
// `opts` (writeRequestOnce): the timeout applies to a message without files; an upload keeps no time limit, since a
// large file on a slow line can rightly take minutes, and only reports its retries.
async function cloudCompose(path: string, body: Record<string, unknown>, files: File[] = [], opts: ApiWriteOptions & {operationId?: string | undefined} = {}): Promise<UntypedJson> {
  if (!files.length) return post(path, body, opts.operationId, opts);
  const fd = new FormData();
  // FormData.append turns a non-string value into its text, as String does; an object goes as JSON.
  for (const [key, value] of Object.entries(body)) fd.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
  files.forEach(file => { fd.append('files', file, file.name); });
  const response = await formFetch(API + path.replace('/v2/', '/v2/uploads/'), fd, {onRetry: opts.onRetry, onResponse: opts.onResponse});
  // The JSON answer: apiFailure reads its fields with checks, so it is cast to a failure body here.
  const result = failureBody(await response.json().catch(() => ({})));
  if (!response.ok) {
    const error = apiFailure(result, response);
    error.message ||= 'The server did not confirm this upload.';
    throw error;
  }
  if (!result.task && !result.message) throw new Error('The server did not confirm this upload.');
  return result;
}
const toast = (msg: string, isErr?: boolean, action?: ToastAction | null, opts: ToastOptions = {}): void => {
  if (isErr && SESSION.out && !opts.keepWhenSignedOut) return;   // the signed-out banner says why every request fails (session.js)
  // A bot's read-only view (backend/app_view.py) clicks nothing: a toast there is only a person-only part of the page
  // refusing the bot, which a person never sees, and it would sit in the bot's screenshot.
  if (typeof S !== 'undefined' && S.me?.read_only_view) return;
  // An error with nothing else to press offers Report this: Support opens with the error attached (ui/support.js).
  if (isErr && !action && msg && typeof window.talkToUs === 'function' && window.supportOn?.(true)) action = {label: 'Report this', run: () => window.talkToUs?.({kind: 'toast', title: String(msg)})};
  const t = document.createElement('div'); t.className = 'toast' + (isErr ? ' err' : ''); t.textContent = msg;
  if (action) {
    // `action` is a parameter that was reassigned above, so the handler below reads this constant (the same value).
    const chosen = action;
    const button = document.createElement('button'); button.className = 'linkish'; button.textContent = action.label;
    button.onclick = async () => { button.disabled = true; try { await chosen.run(); t.remove(); } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; } };
    t.append(' ', button);
  }
  // `host`: an open modal dialog, so a button on the toast is not behind it (the page outside a modal takes no clicks).
  (opts.host?.isConnected ? opts.host : document.body).appendChild(t); setTimeout(() => t.remove(), action ? 8000 : isErr ? 8000 : 3500);
};
