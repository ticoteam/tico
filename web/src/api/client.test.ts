import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest';
import { api, ApiError, operation, type OperationAnswer, RETRY_FOR_MS } from './client';
import type { Group } from './schema';

// The proxies' answer while the server restarts (backend/caddy_assets.py HOLD_BODY).
const starting = () => new Response('Tico is starting. Try again in a few seconds.', { status: 503, headers: { 'Retry-After': '5' } });
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

let answers: (() => Response | Promise<Response>)[];
let keys: (string | null)[];
let sent: { url: string; method: string; body: unknown }[];

beforeEach(() => {
  vi.useFakeTimers();
  keys = [];
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    keys.push(new Headers(init?.headers).get('Idempotency-Key'));
    sent.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const next = answers.length > 1 ? answers.shift() : answers[0];
    return (next as () => Response)();
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the client through a restart', () => {
  it('sends a read again after "Tico is starting" and answers with what came back', async () => {
    answers = [starting, starting, () => ok({ id: 't1' })];
    const read = api.get<{ id: string }>('/api/v2/tasks/t1');
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(read).resolves.toEqual({ id: 't1' });
    expect(keys).toHaveLength(3);
  });

  it.each([502, 504])('sends a read again after a %i gateway error', async (status) => {
    answers = [() => new Response('Gateway error', { status }), () => ok({ id: 't1' })];
    const read = api.get<{ id: string }>('/api/v2/tasks/t1');
    await Promise.all([expect(read).resolves.toEqual({ id: 't1' }), vi.advanceTimersByTimeAsync(500)]);
    expect(keys).toHaveLength(2);
  });

  it('reports a 502 gateway error after the first keyed write', async () => {
    answers = [() => new Response('Bad Gateway', { status: 502 }), () => ok({})];
    const sent = api.post('/api/v2/tasks', { title: 'New' }, { idempotencyKey: 'k1' });
    await Promise.all([expect(sent).rejects.toMatchObject({ status: 502 }), vi.advanceTimersByTimeAsync(500)]);
    expect(keys).toEqual(['k1']);
  });

  it('sends a keyed write again under the same Idempotency-Key, also after a dropped connection', async () => {
    answers = [
      () => {
        throw new TypeError('Failed to fetch');
      },
      starting,
      () => ok({ done: true }),
    ];
    const sent = api.post('/api/v2/tasks/t1/asks/r1/ok', { shown_hash: 'h' }, { idempotencyKey: 'k1' });
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(sent).resolves.toEqual({ done: true });
    expect(keys).toEqual(['k1', 'k1', 'k1']);
  });

  it('gives up after about a minute of restarts, saying so plainly', async () => {
    answers = [starting];
    const read = api.get('/api/v2/tasks/t1');
    const failed = expect(read).rejects.toMatchObject({ status: 503, code: 'starting', retryable: true });
    await vi.advanceTimersByTimeAsync(RETRY_FOR_MS + 10_000);
    await failed;
    await expect(read).rejects.toThrow(/did not come back in time/);
    expect(keys.length).toBeGreaterThan(5);
    expect(keys.length).toBeLessThanOrEqual(RETRY_FOR_MS / 5_000 + 1);
  });

  it('sends a write without a key once: it may have landed', async () => {
    answers = [starting, () => ok({})];
    const sent = api.post('/api/v2/tasks', { title: 'New' });
    const failed = expect(sent).rejects.toBeInstanceOf(ApiError);
    await vi.advanceTimersByTimeAsync(10_000);
    await failed;
    expect(keys).toHaveLength(1);
  });

  it('does not wait on a stopped company or on a refusal', async () => {
    answers = [() => new Response('This Tico company is stopped.', { status: 503 })];
    await expect(api.get('/api/v2/me')).rejects.toMatchObject({ code: 'stopped', retryable: false });
    answers = [() => new Response(JSON.stringify({ error: { code: 'task_changed', detail: 'Changed' } }), { status: 409 })];
    await expect(api.post('/api/v2/tasks/t1/asks/r1/ok', {}, { idempotencyKey: 'k2' })).rejects.toMatchObject({ code: 'task_changed' });
    expect(keys).toHaveLength(2);
  });

  it('reports an unavailable stored file after the first read', async () => {
    answers = [
      () => new Response(JSON.stringify({ error: { code: 'blob_storage', detail: 'Stored file is unavailable', retryable: true } }), { status: 503 }),
      () => ok({}),
    ];
    await expect(api.get('/api/v2/files/f1/versions/1')).rejects.toMatchObject({ status: 503, code: 'blob_storage' });
    expect(keys).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops waiting when the read is called off', async () => {
    answers = [starting];
    const stop = new AbortController();
    const read = api.get('/api/v2/tasks/t1', { signal: stop.signal });
    const called = expect(read).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    stop.abort();
    await called;
    await vi.advanceTimersByTimeAsync(RETRY_FOR_MS);
    expect(keys).toHaveLength(1);
  });
});

describe('typed operations (operation)', () => {
  const group: Group = { id: 'g 1', name: 'Sales', order: 1, parent: '', people: ['lee'], bots: [], bot_slugs: [] };

  it('fills the path, sends the body and keeps the caller\'s Idempotency-Key through a restart', async () => {
    answers = [starting, () => ok(group)];
    const saved = operation('PATCH /api/v2/groups/{gid}', { path: { gid: 'g 1' }, body: { lead: 'person:lee' } }, { idempotencyKey: 'k1' });
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(saved).resolves.toEqual(group);
    expect(sent).toEqual([
      { url: '/api/v2/groups/g%201', method: 'PATCH', body: { lead: 'person:lee' } },
      { url: '/api/v2/groups/g%201', method: 'PATCH', body: { lead: 'person:lee' } },
    ]);
    expect(keys).toEqual(['k1', 'k1']);
  });

  it('reads with no input, and writes the query from what is set, in order', async () => {
    answers = [() => ok([group])];
    await expect(operation('GET /api/v2/groups')).resolves.toEqual([group]);
    answers = [() => ok({})];
    await operation('GET /api/v2/bots/{bot}/rules', { path: { bot: 'bot:a/b' }, query: { external_cursor: 'c 1', limit: 5, internal_cursor: null } });
    expect(sent.map((s) => s.url)).toEqual(['/api/v2/groups', '/api/v2/bots/bot%3Aa%2Fb/rules?external_cursor=c+1&limit=5']);
    expect(keys).toEqual([null, null]);
  });

  it('sends a route whose body is optional without one', async () => {
    answers = [() => ok({ deleted: true })];
    await operation('DELETE /api/v2/automations/{aid}', { path: { aid: 'a1' } }, { idempotencyKey: 'k3' });
    expect(sent).toEqual([{ url: '/api/v2/automations/a1', method: 'DELETE', body: null }]);
    expect(keys).toEqual(['k3']);
  });

  it('stops when its read is called off', async () => {
    answers = [starting];
    const stop = new AbortController();
    const read = operation('GET /api/v2/groups', {}, { signal: stop.signal });
    const called = expect(read).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    stop.abort();
    await called;
    expect(sent).toHaveLength(1);
  });

  it('refuses at build time a call the API does not have (tsc runs this file)', () => {
    // Each line below is a compile error under `npm run typecheck`; at run time none of them is sent.
    const never = () => {
      // @ts-expect-error: no such path
      void operation('GET /api/v2/groupz', {});
      // @ts-expect-error: the path is PATCH, not PUT
      void operation('PUT /api/v2/groups/{gid}', { path: { gid: 'g1' }, body: {} });
      // @ts-expect-error: the path needs its gid
      void operation('PATCH /api/v2/groups/{gid}', { body: { lead: 'person:lee' } });
      // @ts-expect-error: GroupUpdate has no `leader`
      void operation('PATCH /api/v2/groups/{gid}', { path: { gid: 'g1' }, body: { leader: 'person:lee' } });
      // @ts-expect-error: `limit` is a number
      void operation('GET /api/v2/bots/{bot}/rules', { path: { bot: 'a' }, query: { limit: 'five' } });
      // @ts-expect-error: a read takes no body
      void operation('GET /api/v2/groups', { body: { name: 'x' } });
      // @ts-expect-error: Empty is closed, so no field fits
      void operation('POST /api/v2/kpis/{kid}/archive', { path: { kid: 'k1' }, body: { why: 'old' } });
      // @ts-expect-error: an event stream is read by src/live, not as one JSON answer
      void operation('GET /api/v2/events', {});
      // @ts-expect-error: file bytes are not JSON or text
      void operation('GET /api/v2/files/{fid}/versions/{number}', { path: { fid: 'f1', number: 1 } });
      const either = 'GET /api/v2/groups' as 'GET /api/v2/groups' | 'PATCH /api/v2/groups/{gid}';
      // @ts-expect-error: one call names one operation, or PATCH's input could go out as a GET
      void operation(either, { path: { gid: 'g1' }, body: { lead: '' } });
      void (async () => {
        const groups = await operation('GET /api/v2/groups');
        // @ts-expect-error: the answer is a list of Group, which has no `title`
        void groups[0]?.title;
      })();
    };
    expect(typeof never).toBe('function');
    // A 201 answer is the spec's (which declares no shape for this one), not void.
    expectTypeOf<OperationAnswer<'POST /api/v2/skills/proposals'>>().toBeUnknown();
    // A team chart change answers the whole Group, its bots' labels and its lead included.
    expectTypeOf<OperationAnswer<'PATCH /api/v2/groups/{gid}'>>().toEqualTypeOf<Group>();
    expectTypeOf<Group['lead']>().toEqualTypeOf<string | undefined>();
  });
});
