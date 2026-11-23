import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryObserver } from '@tanstack/react-query';
import { act } from '@testing-library/react';
import { queryClient } from '@/data/query-client';
import { keys } from '@/data/keys';
import { FakeEventSource, frame, message, messageFrame, opening, task, taskFrame, AT } from './fake-stream';
import { flushFrame } from './frame';
import { type TaskList, type Timeline, patchTaskList, removeMessage, taskMatches, upsertMessage } from './patch';
import { startLive, stopLive } from './stream';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  startLive();
  FakeEventSource.last.feed(opening(900));
});

afterEach(() => {
  stopLive();
  queryClient.clear();
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const open = ['tasks', 'list', { status: 'open', pipeline: 'p1' }];
const done = ['tasks', 'list', { status: 'done', pipeline: 'p1' }];

it('refreshes only the agent whose run state changed', () => {
  const payer = (bot: string) => [...keys.team.paidBy(), bot, true];
  queryClient.setQueryData(payer('ops'), { last_turn: null });
  queryClient.setQueryData(payer('sales'), { last_turn: null });
  FakeEventSource.last.feed(frame(901, 'runs', { bot_id: 'sales', output: { kind: 'text' } }));
  FakeEventSource.last.feed(frame(902, 'runs', { bot_id: 'ops', state: 'queued' }));
  flushFrame();
  expect(queryClient.getQueryState(payer('ops'))?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(payer('sales'))?.isInvalidated).toBe(false);
});

describe('tasks', () => {
  it('keep a task’s children current: one filed is read again, one that ends or reopens lands in place', async () => {
    const parent = ['tasks', 'detail', 't1'];
    const review = (status: string) => ({ id: 't2', status, role: 'review' });
    // The server's children, read when a read starts; each read answers once released.
    let server: unknown[] = [];
    const reads: (() => void)[] = [];
    const read = () => {
      const kids = [...server];
      return new Promise((done) => reads.push(() => done({ ...task('t1'), children: kids })));
    };
    queryClient.setQueryData(parent, { ...task('t1'), children: [] });
    const seen = new QueryObserver(queryClient, { queryKey: parent, queryFn: read, staleTime: Infinity });
    const stop = seen.subscribe(() => {});
    const children = () => queryClient.getQueryData<{ children: unknown[] }>(parent)?.children;
    try {
      // A review filed: the relation's event, then the task's own update in the same frame, which must not hide it.
      server = [review('open')];
      FakeEventSource.last.feed(taskFrame(901, task('t1', { version: 2 }), 'relation'));
      FakeEventSource.last.feed(taskFrame(902, task('t1', { version: 3 })));
      flushFrame();
      expect(reads).toHaveLength(1);
      // It ends before that read answers: its event names a task no read knows yet, so the children are read again.
      server = [review('done')];
      FakeEventSource.last.feed(taskFrame(903, task('t2', { parent_id: 't1', status: 'done', version: 2 })));
      flushFrame();
      expect(reads).toHaveLength(2);
      await act(() => reads.forEach((go) => go()));
      expect(children()).toEqual([review('done')]);
      // Reopened, and done again: each lands in place.
      FakeEventSource.last.feed(taskFrame(904, task('t2', { parent_id: 't1', status: 'open', version: 3 })));
      flushFrame();
      expect(children()).toEqual([review('open')]);
      FakeEventSource.last.feed(taskFrame(905, task('t2', { parent_id: 't1', status: 'done', version: 4 })));
      flushFrame();
      expect(children()).toEqual([review('done')]);
    } finally {
      stop();
    }
  });

  it('read an open task’s waits again when a paid shot to reconcile comes, when who it waits on moves, and when it goes', async () => {
    const detail = ['tasks', 'detail', 't1'];
    const shot = { state: 'person', who: 'human:p1', line: 'Reconcile 1 shot: Intro', requirement_id: 'paid_render:t1', shots: ['shot_a'] };
    let server: unknown[] = [];
    const reads: (() => void)[] = [];
    const read = () => {
      const waits = [...server];
      return new Promise((done) => reads.push(() => done({ ...task('t1'), waits })));
    };
    queryClient.setQueryData(detail, { ...task('t1'), waits: [] });
    const seen = new QueryObserver(queryClient, { queryKey: detail, queryFn: read, staleTime: Infinity });
    const stop = seen.subscribe(() => {});
    const waits = () => queryClient.getQueryData<{ waits: unknown[] }>(detail)?.waits;
    try {
      server = [shot];
      FakeEventSource.last.feed(taskFrame(901, task('t1', { version: 1 }), 'shots'));
      flushFrame();
      await act(() => reads.splice(0).forEach((go) => go()));
      expect(waits()).toEqual([shot]);
      // another person now has the card: an ordinary update reads the shot's wait again, with who it waits on
      server = [{ ...shot, who: 'human:p2' }];
      FakeEventSource.last.feed(taskFrame(902, task('t1', { version: 2, owner_id: 'human:p2' })));
      flushFrame();
      await act(() => reads.splice(0).forEach((go) => go()));
      expect(waits()).toEqual([{ ...shot, who: 'human:p2' }]);
      server = [];
      FakeEventSource.last.feed(taskFrame(903, task('t1', { version: 2 }), 'shots'));
      flushFrame();
      await act(() => reads.splice(0).forEach((go) => go()));
      expect(waits()).toEqual([]);
    } finally {
      stop();
    }
  });

  it('read a task’s children again when one of them is gone from the reader', () => {
    const parent = ['tasks', 'detail', 't1'];
    queryClient.setQueryData(parent, { ...task('t1'), children: [{ id: 't2', status: 'open', role: 'review' }] });
    FakeEventSource.last.feed(frame(901, 'tasks', { seq: 901, actor: 'human:p1', at: AT, id: 't2', kind: 'delete', kinds: ['delete'], gone: true }));
    flushFrame();
    expect(queryClient.getQueryState(parent)?.isInvalidated).toBe(true);
  });

  it('read a case again when its key date changes: the stream row leaves out its matter @live', () => {
    const detail = ['tasks', 'detail', 't1'];
    queryClient.setQueryData(detail, { ...task('t1'), matter: { dates: [{ id: 'd1', done_at: null }] } });
    FakeEventSource.last.feed(frame(901, 'tasks', { seq: 901, actor: 'human:p1', at: AT, id: 't1', kind: 'obligations', kinds: ['obligations'] }));
    flushFrame();
    expect(queryClient.getQueryState(detail)?.isInvalidated).toBe(true);
  });

  it('read a case again when its key date changes even if a later event of the same frame names only the task @live', () => {
    const detail = ['tasks', 'detail', 't1'];
    queryClient.setQueryData(detail, { ...task('t1'), matter: { dates: [{ id: 'd1', done_at: null }] } });
    FakeEventSource.last.feed(frame(901, 'tasks', { seq: 901, actor: 'human:p1', at: AT, id: 't1', kind: 'obligations', kinds: ['obligations'] }));
    FakeEventSource.last.feed(frame(902, 'tasks', { seq: 902, actor: 'human:p1', at: AT, id: 't1', kind: 'update', kinds: ['update'] }));
    flushFrame();
    expect(queryClient.getQueryState(detail)?.isInvalidated).toBe(true);
  });

  it('start a read of a task’s children again when a review ends while an older one is in flight: it ends Reviewed', async () => {
    const parent = ['tasks', 'detail', 't1'];
    const review = (status: string) => ({ id: 't2', status, role: 'review' });
    let server = [review('open')];
    const reads: (() => void)[] = [];
    const read = () => {
      const kids = [...server];
      return new Promise((done) => reads.push(() => done({ ...task('t1'), children: kids })));
    };
    queryClient.setQueryData(parent, { ...task('t1'), children: [review('open')] });
    const seen = new QueryObserver(queryClient, { queryKey: parent, queryFn: read, staleTime: Infinity });
    const stop = seen.subscribe(() => {});
    try {
      // a read starts (the task changed), and sees the review still open
      void queryClient.invalidateQueries({ queryKey: parent, exact: true });
      expect(reads).toHaveLength(1);
      // the review ends before that read answers: it lands at once, and the read starts again
      server = [review('done')];
      FakeEventSource.last.feed(taskFrame(901, task('t2', { parent_id: 't1', status: 'done', version: 2 })));
      flushFrame();
      expect(queryClient.getQueryData<{ children: unknown[] }>(parent)?.children).toEqual([review('done')]);
      expect(reads).toHaveLength(2);
      await act(() => reads.forEach((go) => go()));
      // the older read is dropped: the pill reads "Reviewed" (features/task reviewOf: no review open, one done)
      expect(queryClient.getQueryData<{ children: unknown[] }>(parent)?.children).toEqual([review('done')]);
    } finally {
      stop();
    }
  });

  it('patch a row in place, keeping every row that did not change', () => {
    const [a, b, c] = [task('t1'), task('t2'), task('t3')];
    queryClient.setQueryData<TaskList>(open, { tasks: [a, b, c], cursor: '900.x' });
    FakeEventSource.last.feed(taskFrame(901, task('t2', { title: 'Renamed', version: 2 })));
    flushFrame();
    const list = queryClient.getQueryData<TaskList<ReturnType<typeof task>>>(open);
    expect(list?.tasks.map((t) => t.id)).toEqual(['t1', 't2', 't3']);
    expect(list?.tasks[0]).toBe(a);
    expect(list?.tasks[2]).toBe(c);
    expect(list?.tasks[1]?.title).toBe('Renamed');
  });

  it('move a task between the lists whose filters it leaves and joins', () => {
    queryClient.setQueryData<TaskList>(open, { tasks: [task('t1'), task('t2')], cursor: '900.x' });
    queryClient.setQueryData<TaskList>(done, { tasks: [], cursor: '900.x' });
    FakeEventSource.last.feed(
      frame(901, 'tasks', {
        seq: 901,
        actor: 'bot:b1',
        at: AT,
        id: 't2',
        kind: 'update',
        kinds: ['update'],
        task: task('t2', { parent_id: 't1', status: 'done', version: 2 }),
        was: { owner: 'bot:b1', status: 'open' },
      }) + frame(902, 'tasks', { seq: 902, actor: 'human:p1', at: AT, id: 't1', kind: 'delete', kinds: ['delete'], gone: true }),
    );
    flushFrame();
    expect(queryClient.getQueryData<TaskList>(open)?.tasks).toEqual([]);
    expect(queryClient.getQueryData<TaskList>(done)?.tasks.map((t) => t.id)).toEqual(['t2']);
  });

  it('skip a change the list was read after, and a version older than the one held', () => {
    const list: TaskList = { tasks: [task('t1', { version: 5 })], cursor: '950.x' };
    const before = patchTaskList(list, [{ seq: 940, id: 't1', task: task('t1', { version: 6, title: 'x' }) }], { status: 'open' });
    expect(before.list).toBe(list);
    const stale = patchTaskList(list, [{ seq: 960, id: 't1', task: task('t1', { version: 4, title: 'x' }) }], { status: 'open' });
    expect(stale.list).toBe(list);
  });

  it('leave filters only the server can answer to the server', () => {
    expect(taskMatches(task('t1'), { status: 'open,done', owner: 'bot:b1' })).toBe(true);
    expect(taskMatches(task('t1', { snoozed: true }), { status: 'open' })).toBe(false);
    expect(taskMatches(task('t1', { snoozed: true }), { status: 'all' })).toBe(true);
    expect(taskMatches(task('t1'), { search: 'plan' })).toBeNull();
    // a team's list: any of its members
    expect(taskMatches(task('t1'), { status: 'open', owners: 'human:p1,bot:b1' })).toBe(true);
    expect(taskMatches(task('t1'), { status: 'open', owners: 'human:p1,human:p2' })).toBe(false);
    expect(taskMatches(task('t1'), { status: 'open', owners: 'b1' })).toBeNull();
    const { list, unsure } = patchTaskList({ tasks: [], cursor: '900.x' }, [{ seq: 901, id: 't9', task: task('t9') }], { search: 'plan' });
    expect(list.tasks).toEqual([]);
    expect(unsure).toBe(true);
  });

  it('match a board read with stage counts as one without, and put a status-only change in place on any list', () => {
    expect(taskMatches(task('t1'), { status: 'open', counts: 'stage' })).toBe(true);
    const held = task('t1');
    const moved = { ...held, status_line: 'working' };
    // A filter only the server answers would read the list again (resetting its pages); a status change does not.
    const { list, unsure } = patchTaskList({ tasks: [held], cursor: '900.x' }, [{ seq: 901, id: 't1', kind: 'run', kinds: ['run'], task: moved }], { waiting: 'human:me' });
    expect(unsure).toBe(false);
    expect(list.tasks).toEqual([moved]);
    const away = patchTaskList({ tasks: [], cursor: '900.x' }, [{ seq: 902, id: 't2', kind: 'status', kinds: ['status'], task: task('t2') }], { waiting: 'human:me' });
    expect(away.unsure).toBe(false);
    expect(away.list.tasks).toEqual([]);
  });

  it('mark every list stale on a bulk change', () => {
    queryClient.setQueryData<TaskList>(open, { tasks: [], cursor: '900.x' });
    FakeEventSource.last.feed(frame(901, 'tasks', { seq: 901, actor: 'human:p1', at: AT, bulk: true }));
    flushFrame();
    expect(queryClient.getQueryState(open)?.isInvalidated).toBe(true);
  });
});

describe('read-again topics', () => {
  it('refreshes the Company Org and Team rosters when an agent joins or leaves the org', () => {
    const rosters = [keys.directory(), keys.groups(), keys.team.groups(), keys.team.agents()];
    for (const key of rosters) queryClient.setQueryData(key, []);
    FakeEventSource.last.feed(frame(901, 'setup', { seq: 901, actor: 'human:p1', at: AT, area: 'org' }));
    flushFrame();
    expect(rosters.map((key) => queryClient.getQueryState(key)?.isInvalidated)).toEqual([true, true, true, true]);
  });

  it('mark the letters list stale on a letters event', () => {
    queryClient.setQueryData(['letters'], []);
    FakeEventSource.last.feed(frame(901, 'letters', { seq: 901, actor: 'human:p1', at: AT }));
    flushFrame();
    expect(queryClient.getQueryState(['letters'])?.isInvalidated).toBe(true);
  });

  describe('a task\'s thread with an open @ of the reader (#4762)', () => {
    const list = (rows: object[]) => ({ pages: [{ rows, next: null }], pageParams: [null] });
    const send = (seq: number, data: object) => FakeEventSource.last.feed(frame(seq, 'threads', { seq, actor: 'human:p1', at: AT, thread_id: 'k1', root_id: 'k1', ...data }));
    const brief = { id: 'k1', title: 'Choose the venue', audience: 'task', root_id: 'k1', parent_id: null, kind: 'task', last_message_at: AT };
    const stale = () => queryClient.getQueryState(keys.threads.list())?.isInvalidated;

    it('reads the list again when the first @ arrives on a thread it never held: activity, then attention', async () => {
      queryClient.setQueryData(keys.threads.list(), list([]));
      send(901, { kind: 'activity', message_id: 'm1', unread: true, unread_count: 1, counters: { last_seq: 1, last_unread_seq: 1 }, thread: brief });
      send(902, { kind: 'attention' });
      flushFrame();
      expect(stale()).toBe(true);
      await vi.runAllTimersAsync();
    });

    it('reads the list again when a marked thread has activity: a reply from elsewhere may have answered it', async () => {
      queryClient.setQueryData(keys.threads.list(), list([{ ...brief, task_id: 'task-1', mentioned: true, me: { unread: 0 } }]));
      send(901, { kind: 'activity', message_id: 'm2', unread: false, unread_count: 0, counters: { last_seq: 2, last_unread_seq: 1 }, thread: brief });
      flushFrame();
      expect(stale()).toBe(true);
      await vi.runAllTimersAsync();
      queryClient.setQueryData(keys.threads.list(), list([{ ...brief, me: { unread: 0 } }]));
      send(902, { kind: 'activity', message_id: 'm3', unread: false, unread_count: 0, counters: { last_seq: 3, last_unread_seq: 1 }, thread: brief });
      flushFrame();
      expect(stale()).toBe(false); // an unmarked thread's activity is patched in place, no read
    });
  });

  it('mark a call and the live and ringing lists stale on a call change in the threads topic', () => {
    for (const k of [['calls', 'c1'], ['calls', 'c1', 'notes'], ['calls', 'live'], ['calls', 'ringing'], ['calls', 'c2']]) queryClient.setQueryData(k, {});
    FakeEventSource.last.feed(frame(901, 'threads', { seq: 901, actor: 'human:p1', at: AT, thread_id: 'th1', kind: 'call.updated', call_id: 'c1' }));
    flushFrame();
    const stale = (k: string[]) => queryClient.getQueryState(k)?.isInvalidated;
    expect([stale(['calls', 'c1']), stale(['calls', 'c1', 'notes']), stale(['calls', 'live']), stale(['calls', 'ringing'])]).toEqual([true, true, true, true]);
    expect(stale(['calls', 'c2'])).toBe(false);
  });
});

describe('messages', () => {
  const key = ['thread', 'th1', 'timeline'];

  it('upsert into the thread by id, in seq order, and never twice', () => {
    const m1 = message('m1', 'th1', 4400);
    queryClient.setQueryData<Timeline>(key, { messages: [m1], next_cursor: null });
    const m2 = message('m2', 'th1', 4411);
    FakeEventSource.last.feed(messageFrame(901, m2) + messageFrame(902, { ...m2, body: 'Edited', revision: 1 }));
    flushFrame();
    const rows = queryClient.getQueryData<Timeline<ReturnType<typeof message>>>(key)?.messages ?? [];
    expect(rows.map((m) => m.id)).toEqual(['m1', 'm2']);
    expect(rows[0]).toBe(m1);
    expect(rows[1]?.body).toBe('Edited');
  });

  it('place a reply after its sibling’s sub-thread, in an infinite timeline', () => {
    const pages = {
      pages: [
        {
          messages: [
            { ...message('m1', 'th1', 10), depth: 0 },
            { ...message('r1', 'sub1', 11), depth: 1 },
          ],
        },
      ],
      pageParams: [null],
    };
    const next = upsertMessage(pages, 'th1', message('m2', 'th1', 12));
    expect('pages' in next && next.pages[0]?.messages.map((m) => m.id)).toEqual(['m1', 'r1', 'm2']);
    expect(upsertMessage(next, 'th1', message('m2', 'th1', 12))).toBe(next);
    expect(removeMessage(next, 'nope')).toBe(next);
  });

  it('mark a deleted message deleted', () => {
    queryClient.setQueryData<Timeline>(key, { messages: [message('m1', 'th1', 4400)] });
    FakeEventSource.last.feed(
      frame(901, 'messages', {
        seq: 901,
        actor: 'human:p1',
        at: AT,
        id: 'm1',
        conversation_id: 'th1',
        thread_id: 'th1',
        root_id: 'th1',
        message_seq: 4400,
        revision: 2,
        deleted: true,
      }),
    );
    flushFrame();
    expect(queryClient.getQueryData<Timeline>(key)?.messages[0]?.deleted).toBe(true);
  });
});
