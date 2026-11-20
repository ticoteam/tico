import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, RETRY_FOR_MS } from './client';

// The proxies' answer while the server restarts (backend/caddy_assets.py HOLD_BODY).
const starting = () => new Response('Tico is starting. Try again in a few seconds.', { status: 503, headers: { 'Retry-After': '5' } });
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

let answers: (() => Response | Promise<Response>)[];
let keys: (string | null)[];

beforeEach(() => {
  vi.useFakeTimers();
  keys = [];
  vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
    keys.push(new Headers(init?.headers).get('Idempotency-Key'));
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
