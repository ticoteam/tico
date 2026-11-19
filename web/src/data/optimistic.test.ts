import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { clearHeld, connectionChanged, heldCount } from './held';
import { NeedsConnection, quietRoot, write } from './optimistic';
import { queryClient } from './query-client';

afterEach(() => {
  clearHeld();
  queryClient.clear();
});

interface Row {
  id: string;
  title: string;
}
interface List {
  tasks: Row[];
}
const key = ['tasks', 'list', { status: 'open' }];
const rename = (title: string) => (d: List) => ({ ...d, tasks: d.tasks.map((t) => ({ ...t, title })) });
const titles = () => queryClient.getQueryData<List>(key)?.tasks.map((t) => t.title);

describe('write', () => {
  it('shows the change at once and puts the row back when the server refuses it', async () => {
    queryClient.setQueryData<List>(key, { tasks: [{ id: 't1', title: 'Plan' }] });
    const refused = new ApiError(422, 'validation', 'Title is required');
    const sent = write({ key, optimistic: rename(''), send: () => Promise.reject(refused) });
    expect(titles()).toEqual(['']);
    await expect(sent).rejects.toBe(refused);
    expect(titles()).toEqual(['Plan']);
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(false); // put back, not read again
  });

  it('leaves nothing made up when the key had not been read before the refused change', async () => {
    const runs = ['maintenance', 'runs'];
    const sent = write({ key: runs, optimistic: (cur: unknown[] | undefined) => [{ id: 'tmp:1' }, ...(cur ?? [])], send: () => Promise.reject(new ApiError(403, 'forbidden', 'No')) });
    expect(queryClient.getQueryData(runs)).toEqual([{ id: 'tmp:1' }]);
    await expect(sent).rejects.toBeInstanceOf(ApiError);
    expect(queryClient.getQueryData(runs)).toBeUndefined();
  });

  it('reads the key again when the stream changed it meanwhile, rather than putting back a stale copy', async () => {
    queryClient.setQueryData<List>(key, { tasks: [{ id: 't1', title: 'Plan' }] });
    let fail!: (e: unknown) => void;
    const sent = write({ key, optimistic: rename('Mine'), send: () => new Promise((_, rej) => (fail = rej)) });
    queryClient.setQueryData<List>(key, { tasks: [{ id: 't1', title: 'Theirs' }] });
    fail(new ApiError(409, 'task_changed', 'Changed'));
    await expect(sent).rejects.toBeInstanceOf(ApiError);
    expect(titles()).toEqual(['Theirs']);
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
  });

  it('reads an area with no live topic again after its own write', async () => {
    quietRoot('goals');
    queryClient.setQueryData(['goals', 'list'], { goals: [] });
    await write({ key: ['goals', 'list'], optimistic: (d: object) => d, send: () => Promise.resolve({}) });
    expect(queryClient.getQueryState(['goals', 'list'])?.isInvalidated).toBe(true);
  });

  it('refuses a change that cannot wait while offline, and shows nothing', async () => {
    connectionChanged('offline');
    const approve = vi.fn(() => Promise.resolve({}));
    await expect(write({ key: ['needs'], optimistic: () => ({ approved: true }), send: approve })).rejects.toBeInstanceOf(
      NeedsConnection,
    );
    expect(queryClient.getQueryData(['needs'])).toBeUndefined();
    expect(approve).not.toHaveBeenCalled();
  });

  it('holds a change while offline and sends it, once, when the connection is back', async () => {
    queryClient.setQueryData<List>(key, { tasks: [{ id: 't1', title: 'Plan' }] });
    connectionChanged('offline');
    const send = vi.fn((_key: string) => Promise.resolve({ ok: true }));
    const sent = write({ key, optimistic: rename('Offline edit'), send, offline: 'hold' });
    expect(titles()).toEqual(['Offline edit']);
    expect(heldCount()).toBe(1);
    expect(send).not.toHaveBeenCalled();

    connectionChanged('live');
    await expect(sent).resolves.toEqual({ ok: true });
    expect(send).toHaveBeenCalledTimes(1);
    expect(heldCount()).toBe(0);
    expect(titles()).toEqual(['Offline edit']);
  });

  it('holds a change whose send never reached the server, and resends it with the same Idempotency-Key', async () => {
    queryClient.setQueryData<List>(key, { tasks: [{ id: 't1', title: 'Plan' }] });
    const send = vi
      .fn((_key: string) => Promise.resolve({ ok: true }))
      .mockImplementationOnce(() => Promise.reject(new ApiError(0, 'network', 'The server did not answer', true)));
    const sent = write({ key, optimistic: rename('Edit'), send, offline: 'hold' });
    await vi.waitFor(() => expect(heldCount()).toBe(1));
    expect(titles()).toEqual(['Edit']);

    connectionChanged('live');
    await sent;
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1]?.[0]).toBe(send.mock.calls[0]?.[0]);
  });
});
