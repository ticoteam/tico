import { QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiError } from '@/api/client';
import { clearHeld, heldCount, hold } from '@/data/held';
import { queryClient } from '@/data/query-client';
import { FakeEventSource, frame, opening, task, taskFrame } from './fake-stream';
import { flushFrame } from './frame';
import { follow, resetFollows } from './follow';
import { onTopic, type TaskEvent, type TaskList } from './patch';
import { liveStatus, startLive, stopLive } from './stream';

const offs: (() => void)[] = [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  offs.splice(0).forEach((off) => off());
  stopLive();
  clearHeld();
  resetFollows();
  queryClient.clear();
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function seen(): TaskEvent[][] {
  const batches: TaskEvent[][] = [];
  offs.push(onTopic<TaskEvent>('tasks', (b) => void batches.push(b)));
  return batches;
}

describe('the stream', () => {
  it('opens with compact output and changed-ids Needs you, and resumes from the last change number after a drop', () => {
    startLive();
    const first = FakeEventSource.last;
    expect(first.params.get('needs')).toBe('changes');
    expect(first.params.get('output')).toBe('compact');
    expect(first.params.has('after')).toBe(false);
    first.feed(opening(900) + taskFrame(901, task('t1')) + frame(905, 'cursor', { seq: 905 }) + ': keepalive\n\n');
    expect(liveStatus()).toBe('live');

    first.fail();
    expect(liveStatus()).toBe('connecting');
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.last.params.get('after')).toBe('905');
  });

  it('drops a frame it has already applied, and keeps frames that carry no change number', () => {
    const batches = seen();
    startLive();
    FakeEventSource.last.feed(opening(900) + taskFrame(901, task('t1')) + taskFrame(902, task('t2')));
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    // The server replays from `after`; a proxy or a second tab can still hand back one already seen.
    FakeEventSource.last.feed(
      opening(910, 902) +
        taskFrame(902, task('t2')) +
        taskFrame(903, task('t3')) +
        frame(null, 'tasks', { task_id: 't1', gone: true }),
    );
    flushFrame();
    expect(batches.map((b) => b.map((e) => e.id ?? e.task_id))).toEqual([['t1', 't2', 't3', 't1']]);
  });

  it('on reset, marks everything stale and resumes from the number the server gave', () => {
    queryClient.setQueryData(['tasks', 'list', { status: 'open' }], { tasks: [], cursor: '900.a' });
    startLive();
    FakeEventSource.last.feed(opening(900) + frame(5000, 'reset', { seq: 5000 }));
    expect(queryClient.getQueryState(['tasks', 'list', { status: 'open' }])?.isInvalidated).toBe(true);
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.last.params.get('after')).toBe('5000');
  });

  it('applies a burst as one cache write per frame', () => {
    const key = ['tasks', 'list', { status: 'open' }];
    queryClient.setQueryData<TaskList>(key, { tasks: [task('t1'), task('t2'), task('t3')], cursor: '900.a' });
    const batches = seen();
    let writes = 0;
    offs.push(
      queryClient.getQueryCache().subscribe((e) => {
        if (e.type === 'updated' && e.action.type === 'success' && e.query.queryHash === JSON.stringify(key)) writes++;
      }),
    );
    startLive();
    let text = opening(900);
    for (let n = 1; n <= 30; n++) text += taskFrame(900 + n, task(`t${(n % 3) + 1}`, { version: n + 1, title: `Edit ${n}` }));
    FakeEventSource.last.feed(text);
    expect(writes).toBe(0);
    flushFrame();
    expect(batches).toHaveLength(1);
    expect(batches[0]).toHaveLength(30);
    expect(writes).toBe(1);
    expect(queryClient.getQueryData<TaskList>(key)?.tasks.map((t) => (t as { title?: string }).title)).toEqual([
      'Edit 30',
      'Edit 28',
      'Edit 29',
    ]);
  });

  it('tells a sign-out apart from a dropped network', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ error: { code: 'unauthenticated', detail: 'Sign in again' } }, { status: 401 }),
      ),
    );
    startLive();
    FakeEventSource.last.feed(opening(900));
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    FakeEventSource.last.fail(); // a second failure in a row asks who is signed in
    await vi.waitFor(() => expect(liveStatus()).toBe('signed-out'));
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.all).toHaveLength(2); // nothing reconnects until the person signs in
  });

  it('says offline after ten seconds down, and reconnects at once when the network is back', () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))));
    startLive();
    FakeEventSource.last.feed(opening(900));
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(9_000);
    FakeEventSource.last.fail();
    expect(liveStatus()).toBe('connecting');
    vi.advanceTimersByTime(1_000);
    expect(liveStatus()).toBe('offline');
    const opened = FakeEventSource.all.length;
    window.dispatchEvent(new Event('online'));
    expect(FakeEventSource.all).toHaveLength(opened + 1);
    FakeEventSource.last.feed(opening(905, 900));
    return vi.waitFor(() => expect(liveStatus()).toBe('live'));
  });

  it('ends with expired: asks who is signed in and stops on a 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: { code: 'unauthenticated', detail: 'x' } }, { status: 401 })));
    startLive();
    FakeEventSource.last.feed(opening(900) + 'event: expired\ndata: {}\n\n');
    await vi.waitFor(() => expect(liveStatus()).toBe('signed-out'));
  });
});

describe('who is signed in', () => {
  const me = { id: 'human:a', email: '', role: 'person', slug: null };
  const answer = (actor: string) => vi.fn(async () => Response.json({ actor, email: '', role: 'human' }));

  it('on a 401, drops held changes and what was read, and the person read takes the 401 (the root signs out)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: { code: 'unauthenticated', detail: 'x' } }, { status: 401 })));
    queryClient.setQueryData(['me'], me);
    queryClient.setQueryData(['tasks', 'detail', 't1'], { id: 't1' });
    startLive();
    FakeEventSource.last.feed(opening(900));
    FakeEventSource.last.fail();
    const send = vi.fn(async () => 'sent');
    const held = hold(send);
    vi.advanceTimersByTime(250);
    FakeEventSource.last.fail();
    await expect(held).rejects.toThrow();
    await vi.waitFor(() => expect(liveStatus()).toBe('signed-out'));
    expect(send).not.toHaveBeenCalled();
    expect(heldCount()).toBe(0);
    expect(queryClient.getQueryData(['tasks', 'detail', 't1'])).toBeUndefined();
    const person = queryClient.getQueryState(['me']);
    expect(person?.status).toBe('error');
    expect((person?.error as ApiError).status).toBe(401);
    expect(queryClient.getQueryData(['me'])).toBeUndefined();
  });

  it('back with someone else signed in: held changes never go out under them, and the app opens again', async () => {
    vi.stubGlobal('fetch', answer('human:b'));
    const reload = vi.fn();
    vi.stubGlobal('location', { reload });
    queryClient.setQueryData(['me'], me);
    startLive();
    FakeEventSource.last.feed(opening(900));
    FakeEventSource.last.fail();
    const send = vi.fn(async () => 'sent');
    const held = hold(send);
    vi.advanceTimersByTime(250);
    FakeEventSource.last.feed(opening(905, 900));
    await expect(held).rejects.toThrow();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(send).not.toHaveBeenCalled();
    expect(liveStatus()).not.toBe('live');
  });

  it('back as the same person: goes live and sends what was held', async () => {
    vi.stubGlobal('fetch', answer('human:a'));
    queryClient.setQueryData(['me'], me);
    startLive();
    FakeEventSource.last.feed(opening(900));
    FakeEventSource.last.fail();
    const held = hold(async () => 'sent');
    vi.advanceTimersByTime(250);
    FakeEventSource.last.feed(opening(905, 900));
    await expect(held).resolves.toBe('sent');
    expect(liveStatus()).toBe('live');
  });
});

describe('the first connection', () => {
  it('reads nothing again; a reconnect replays from its number instead', () => {
    const key = ['tasks', 'list', { status: 'open' }];
    const read = vi.fn(async () => ({ tasks: [], cursor: '900.a' }));
    queryClient.setQueryData(key, { tasks: [], cursor: '900.a' });
    offs.push(new QueryObserver(queryClient, { queryKey: key, queryFn: read }).subscribe(() => {}));
    startLive();
    FakeEventSource.last.feed(opening(905));
    expect(liveStatus()).toBe('live');
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(false);
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.last.params.get('after')).toBe('905');
    FakeEventSource.last.feed(opening(910, 905));
    expect(read).not.toHaveBeenCalled();
  });

  it('follows that change while it opens reconnect once it says where it starts, from that number', () => {
    startLive();
    const release = follow({ thread: 'th1' });
    vi.advanceTimersByTime(30);
    expect(FakeEventSource.all).toHaveLength(1); // not closed and opened again before it is ready
    FakeEventSource.last.feed(opening(900));
    vi.advanceTimersByTime(30);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.last.params.get('thread')).toBe('th1');
    expect(FakeEventSource.last.params.get('after')).toBe('900');
    release();
    vi.advanceTimersByTime(30);
  });
});

describe('follows', () => {
  it('narrow the stream to what is on screen, and mark what they fed stale without reading it', () => {
    startLive();
    FakeEventSource.last.feed(opening(900));
    expect(FakeEventSource.last.params.get('topics')).not.toContain('messages');

    const release = follow({ thread: 'th1' });
    const again = follow({ thread: 'th1' });
    vi.advanceTimersByTime(30);
    expect(FakeEventSource.all).toHaveLength(2);
    const narrowed = FakeEventSource.last.params;
    expect(narrowed.get('thread')).toBe('th1');
    expect(narrowed.get('topics')?.split(',')).toEqual(expect.arrayContaining(['messages', 'runs']));
    expect(narrowed.get('after')).toBe('900');

    const key = ['thread', 'th1', 'timeline'];
    const read = vi.fn(async () => ({ messages: [] }));
    queryClient.setQueryData(key, { messages: [] });
    offs.push(new QueryObserver(queryClient, { queryKey: key, queryFn: read }).subscribe(() => {}));
    again();
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(false); // still followed once
    release();
    vi.advanceTimersByTime(30);
    expect(FakeEventSource.last.params.has('thread')).toBe(false);
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
    expect(read).not.toHaveBeenCalled();
  });
});
