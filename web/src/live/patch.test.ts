import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

describe('tasks', () => {
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
        task: task('t2', { status: 'done', version: 2 }),
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
