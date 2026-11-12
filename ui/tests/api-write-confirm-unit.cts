// No browser: a JSON write's success counts as saved only when its answer is a JSON object or array, as an upload's does.
// A proxy or sign-in page answered with 200, or a cut-off answer, ends unconfirmed and keeps its request id, so the same
// submission retried in the tab carries the same Idempotency-Key. Only a write declared `noContent` reads an empty
// success as saved; a redirect is refused and goes through the signed-out check.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/api.js'), 'utf8');
// From the message helpers to the write path's end: the rest of the file listens to the page as it loads.
const start = source.indexOf('const apiErrorRaw'), end = source.indexOf('let LAST_PRESS');
assert.ok(start > 0 && end > start, 'the write path is where this test reads it');

interface Call { method: string; key: string; redirect: string | undefined }
type Answer = () => Response | Promise<Response>;
const calls: Call[] = [];
let answers: Answer[] = [];
let signedOut = false;
const context = vm.createContext({
  API: '/api', SESSION: {out: false, seen: false}, window: {}, performance, AbortController, Response, URLSearchParams,
  setTimeout: (run: () => void) => { run(); return 0; }, clearTimeout: () => {},
  crypto: {randomUUID: (() => { let n = 0; return () => `id-${++n}`; })()},
  sessionCheck: () => false, cloudAccessLocked: () => {},
  sessionProbe: async () => signedOut,
  signedOutError: () => Object.assign(new Error('Signed out'), {signedOut: true}),
  fetch: async (_url: string, init: {method: string; redirect?: string; headers: Record<string, string>}) => {
    calls.push({method: init.method, key: init.headers['Idempotency-Key'] ?? '', redirect: init.redirect});
    const next = answers.shift();
    assert.ok(next, 'every attempt has an answer');
    return next();
  },
});
vm.runInContext(`${source.slice(start, end)};globalThis.writeRequestOnce = writeRequestOnce;globalThis.pendingWrites = pendingWrites;`, context);
const api = context as unknown as {
  writeRequestOnce(method: string, p: string, body?: unknown, operationId?: string, opts?: {noContent?: boolean}): Promise<unknown>;
  pendingWrites: Map<string, string>;
};
// A fetch that refuses a redirect rejects with the page's own TypeError.
const VmTypeError = vm.runInContext('TypeError', context) as TypeErrorConstructor;

const html = () => new Response('<!doctype html><title>Sign in</title>', {status: 200, headers: {'Content-Type': 'text/html'}});
const json = (body: unknown) => () => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
const empty = (status: number) => () => new Response(null, {status});
const outcome = async (run: Promise<unknown>) => run.then(value => ({value, error: null as null | {message: string; unconfirmed?: boolean; signedOut?: boolean}}),
  (error: {message: string; unconfirmed?: boolean; signedOut?: boolean}) => ({value: undefined, error}));
const reset = (next: Answer[]) => { calls.length = 0; answers = next; api.pendingWrites.clear(); signedOut = false; };

void (async () => {
  // AC-1: three 200 HTML pages end unconfirmed with the existing message, and the request id is kept.
  reset([html, html, html]);
  let result = await outcome(api.writeRequestOnce('POST', '/v2/tasks', {title: 'Write the brief'}));
  assert.equal(result.error?.unconfirmed, true, 'a 200 HTML page is not a saved write');
  assert.match(result.error?.message ?? '', /^Could not confirm whether this saved\./);
  assert.equal(calls.length, 3, 'the write is retried before it is reported unconfirmed');
  assert.equal(new Set(calls.map(c => c.key)).size, 1, 'every try carries the same Idempotency-Key');
  assert.equal(api.pendingWrites.size, 1, 'the request id is kept');
  const first = calls[0]!.key;

  // AC-2: the same submission retried in the tab carries the first request's key, and a JSON answer confirms it.
  calls.length = 0; answers = [json({id: 't1'})];
  result = await outcome(api.writeRequestOnce('POST', '/v2/tasks', {title: 'Write the brief'}));
  assert.equal(JSON.stringify(result.value), JSON.stringify({id: 't1'}));
  assert.equal(calls[0]!.key, first, 'the retry reuses the kept request id');
  assert.equal(api.pendingWrites.size, 0, 'a confirmed write releases its request id');

  // AC-3: a cut-off JSON body is the same as a page.
  const cut = () => new Response('{"id": "t1", "tit', {status: 200});
  reset([cut, cut, cut]);
  result = await outcome(api.writeRequestOnce('PATCH', '/v2/tasks/t1', {title: 'x'}));
  assert.equal(result.error?.unconfirmed, true, 'a cut-off answer is not a saved write');
  assert.equal(api.pendingWrites.size, 1);

  // A JSON scalar is not a confirmation either; a later try that answers with JSON is.
  reset([json(null), json({ok: true})]);
  result = await outcome(api.writeRequestOnce('PUT', '/v2/x', {}));
  assert.equal(JSON.stringify(result.value), JSON.stringify({ok: true}));
  assert.equal(calls.length, 2);
  assert.equal(api.pendingWrites.size, 0);

  // AC-4: a redirect is refused (redirect: 'error'), and a signed-out session shows the banner, never a save.
  reset([() => { throw new VmTypeError('Failed to fetch'); }]);
  signedOut = true;
  result = await outcome(api.writeRequestOnce('POST', '/v2/tasks', {title: 'y'}));
  assert.equal(calls[0]!.redirect, 'error', 'a JSON write does not follow redirects');
  assert.equal(result.error?.signedOut, true, 'a sign-in redirect reads as signed out');
  assert.equal(result.value, undefined);

  // AC-5: a write declared noContent reads a 204 with no body as saved and releases its request id.
  reset([empty(204)]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/declared', {}, undefined, {noContent: true}));
  assert.equal(result.error, null, 'a declared 204 write is saved');
  assert.equal(api.pendingWrites.size, 0);
  // Declared or not, a page is still no confirmation.
  reset([html, html, html]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/declared', {}, undefined, {noContent: true}));
  assert.equal(result.error?.unconfirmed, true);

  // AC-6: a write not declared that answers 204 is unconfirmed.
  reset([empty(204), empty(204), empty(204)]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/undeclared', {}));
  assert.equal(result.error?.unconfirmed, true, 'an undeclared empty success is not a saved write');
  assert.equal(api.pendingWrites.size, 1);

  // AC-7: a normal save answered 200 with JSON saves at once, arrays included.
  reset([json([{id: 'a'}])]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/list', {}));
  assert.equal(JSON.stringify(result.value), JSON.stringify([{id: 'a'}]));
  assert.equal(calls.length, 1);
  assert.equal(api.pendingWrites.size, 0);
  // A refusal still reads its JSON, and an HTML refusal still fails with its status, as before.
  reset([() => new Response(JSON.stringify({error: 'No'}), {status: 400, statusText: 'Bad Request'})]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/x', {}));
  assert.equal(result.error?.message, 'No');
  reset([() => new Response('<html>', {status: 400, statusText: 'Bad Request'})]);
  result = await outcome(api.writeRequestOnce('POST', '/v2/x', {}));
  assert.equal(result.error?.message, 'Bad Request');

  console.log('api-write-confirm-unit: ok');
})().catch(error => { console.error(error); process.exit(1); });
