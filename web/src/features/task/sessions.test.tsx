import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { liveText, stepRun } from '@/conversation/queries';
import { keys, queryClient } from '@/data';
import { CardsContext } from '@/kit/Popover';
// eslint-disable-next-line no-restricted-imports -- the test hands the page stream frames, as the shell's startLive does
import { dispatch, installPatchers } from '@/live/patch';
import { sessionRunQuery, type TaskData } from './queries';
import { Sessions, SessionRun, snapshotRun, useTaskLive, useTaskReadMark } from './sessions';

/* The Sessions list on a task's Details, the read mark it counts new from, and an agent's run over its session box. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn() }));

const t = {
  id: 't1',
  number: 12,
  pipeline_id: 'p1',
  conversation_id: 'c7',
  owner_id: 'bot:a1',
  sessions: [
    { agent: 'bot:a1', last_seq: 4 },
    { agent: 'bot:r2', last_seq: 9 },
  ],
} as unknown as TaskData;

/* A thread's read mark is sent once per page load (conversation markRead): a test that marks one reads its own thread. */
const on = (thread: string) => ({ ...t, conversation_id: thread }) as TaskData;
const commentsOf = (thread: string) => ['thread', thread, 'timeline', { comments: true }];
const comments = commentsOf('c7');
const page = (mark: number, seqs: number[] = [], thread = 'c7') => ({
  pages: [{ messages: seqs.map((seq) => ({ id: `m${seq}`, seq, thread_id: thread, sender_id: 'human:ana', body: 'x' })), next_cursor: null, read_marks: { [thread]: mark } }],
  pageParams: [null],
});

let calls: { url: string; method: string; body: unknown }[] = [];
let run: unknown = null;
/** The task's thread: its own (`task_id` is the task), or its owner's main chat. */
let room: Record<string, unknown> | null = null;

function show(node: ReactNode, at: string, cards = false) {
  const root = createRootRoute({ component: () => <CardsContext value={cards}>{node}</CardsContext> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  // the cache tells React at once (the app waits a tick), so fake timers below hold only the page's own wait
  notifyManager.setScheduler((cb) => cb());
  installPatchers();
});
beforeEach(() => {
  calls = [];
  run = null;
  room = null;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'bot:a1', kind: 'agent', name: 'Birch Builder' },
    { id: 'bot:r2', kind: 'agent', name: 'Rowan Reviewer' },
    { id: 'bot:q3', kind: 'agent', name: 'Quill QA' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const thread = /^\/api\/v2\/threads\/([^/?]+)$/.exec(url)?.[1];
    const body = url.includes('/execution') ? { execution: run } : thread ? (room ?? { id: thread, task_id: 't1' }) : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('Sessions', () => {
  it('lists one row per agent, each opening its session with the address’s other words kept, new past the mark the page opened with', async () => {
    queryClient.setQueryData(comments, page(5));
    show(<Page t={t} />, '/tasks/p1?task=12&list=p1&task.tab=details&task.ask=qmark%3Ak1');
    const list = await screen.findByRole('region', { name: 'Sessions' });
    const builder = within(list).getByRole('link', { name: 'Birch Builder' });
    const reviewer = within(list).getByRole('link', { name: 'Rowan Reviewer' });
    expect(builder.getAttribute('href')).toBe('/tasks/p1/12/details?ask=qmark%3Ak1&session=bot%3Aa1');
    // a1's newest (4) is older than the mark (5); r2's (9) is past it
    expect(builder.className).not.toContain('is-unread');
    expect(reviewer.className).toContain('is-unread');
    // the page marks the task read: the rows keep counting from the mark it opened with
    await waitFor(() => expect(calls.some((c) => c.url === '/api/v2/threads/c7/read')).toBe(true));
    expect(within(list).getByRole('link', { name: 'Rowan Reviewer' }).className).toContain('is-unread');
  });

  it('opened again from what it holds, counts nothing it already marked read as new', async () => {
    queryClient.setQueryData(commentsOf('c8'), page(5, [], 'c8'));
    const first = show(<Page t={on('c8')} />, '/tasks/p1/12/details');
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toEqual({ url: '/api/v2/threads/c8/read', method: 'POST', body: { seq: 9 } }));
    // the held comments carry the new mark: the next visit opens with it
    expect(queryClient.getQueryData<ReturnType<typeof page>>(commentsOf('c8'))?.pages[0]?.read_marks.c8).toBe(9);
    first.unmount();
    show(<Page t={on('c8')} />, '/tasks/p1/12/details');
    const list = await screen.findByRole('region', { name: 'Sessions' });
    expect(within(list).getByRole('link', { name: 'Rowan Reviewer' }).className).not.toContain('is-unread');
  });

  it('adds an agent at work on the thread before the task read lists it, and on a phone opens the chat card', async () => {
    queryClient.setQueryData(comments, page(5));
    queryClient.setQueryData(['thread', 'c7', 'live'], [{ run: 'r1', bot: 'bot:q3', parts: [], typing: null, doing: 'think', ended: false }]);
    show(<Sessions t={t} />, '/tasks/p1/12/details', true);
    const qa = await screen.findByRole('link', { name: 'Quill QA' });
    expect(qa.getAttribute('href')).toBe('/tasks/p1/12/chat?session=bot%3Aq3');
  });

  it('has none for a task filed in its owner’s main chat, even with an agent at work there', async () => {
    room = { id: 'c7', task_id: null };
    queryClient.setQueryData(['thread', 'c7', 'live'], [{ run: 'r1', bot: 'bot:q3', parts: [], typing: null, doing: 'think', ended: false }]);
    show(<Sessions t={t} />, '/tasks/p1/12/details');
    await waitFor(() => expect(calls.some((c) => c.url === '/api/v2/threads/c7')).toBe(true));
    await act(async () => {});
    expect(screen.queryByRole('region', { name: 'Sessions' })).toBeNull();
  });

  it('shows nothing for a task no agent has a session on', async () => {
    show(<Sessions t={{ ...t, sessions: [] }} />, '/tasks/p1/12/details');
    await act(async () => {});
    expect(screen.queryByRole('region', { name: 'Sessions' })).toBeNull();
  });

  it('reads the task’s sessions again once per burst of its thread’s messages and runs, from the task page on any of its tabs', async () => {
    const detail = keys.tasks.detail('t1');
    queryClient.setQueryData(detail, t);
    // the task page open on a document: no Details, no Sessions list
    show(<Live t={t} />, '/tasks/p1/12/spec');
    await act(async () => {});
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const spy = vi.spyOn(queryClient, 'invalidateQueries');
      act(() => {
        dispatch('messages', [{ id: 'm20', conversation_id: 'c7', message: { id: 'm20', seq: 20, thread_id: 'c7', session_agent_id: 'bot:q3', body: 'x' } }]);
        dispatch('runs', [{ run_id: 'a1', conversation_id: 'c7', bot_id: 'q3', state: 'running' }]);
      });
      act(() => dispatch('messages', [{ id: 'm21', conversation_id: 'c7', message: { id: 'm21', seq: 21, thread_id: 'c7', body: 'y' } }]));
      const reads = () => spy.mock.calls.filter(([f]) => JSON.stringify(f?.queryKey) === JSON.stringify(detail));
      expect(reads()).toHaveLength(0);
      act(() => vi.advanceTimersByTime(1000));
      expect(reads()).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

/** The task page's own subscription (TaskView): its sessions and its agents' runs kept current. */
function Live({ t, own = true }: { t: TaskData; own?: boolean }) {
  useTaskLive(t, own);
  return null;
}

function Marks({ t, own }: { t: TaskData; own: boolean }) {
  useTaskReadMark(t, own);
  return null;
}

/** The task page as the list reads it: it keeps and advances the read mark, Sessions shows the rows. */
const Page = ({ t }: { t: TaskData }) => (
  <>
    <Marks t={t} own />
    <Sessions t={t} />
  </>
);

describe('one read mark per task', () => {
  it('marks the task read up to its newest message, a comment’s or a session’s, once it kept the mark it opened with', async () => {
    queryClient.setQueryData(commentsOf('c9'), page(3, [2, 6], 'c9'));
    show(<Marks t={on('c9')} own />, '/tasks/p1/12');
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toEqual({ url: '/api/v2/threads/c9/read', method: 'POST', body: { seq: 9 } }));
  });

  it('marks a task opened in a tab behind once that tab comes forward, and not after the page has gone', async () => {
    let seen: DocumentVisibilityState = 'hidden';
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => seen });
    try {
      queryClient.setQueryData(commentsOf('c10'), page(3, [2, 6], 'c10'));
      const view = show(<Marks t={on('c10')} own />, '/tasks/p1/12');
      await act(async () => {});
      expect(calls.some((c) => c.method === 'POST')).toBe(false);
      seen = 'visible';
      act(() => void document.dispatchEvent(new Event('visibilitychange')));
      expect(calls.filter((c) => c.method === 'POST')).toEqual([{ url: '/api/v2/threads/c10/read', method: 'POST', body: { seq: 9 } }]);
      // gone: the tab coming forward again marks nothing (another task's thread, nothing marked there yet)
      view.unmount();
      queryClient.setQueryData(commentsOf('c11'), page(3, [2, 6], 'c11'));
      seen = 'hidden';
      const other = show(<Marks t={on('c11')} own />, '/tasks/p1/12');
      await act(async () => {});
      other.unmount();
      seen = 'visible';
      act(() => void document.dispatchEvent(new Event('visibilitychange')));
      expect(calls.some((c) => c.url === '/api/v2/threads/c11/read')).toBe(false);
    } finally {
      // back to jsdom's own (always visible)
      delete (document as unknown as Record<string, unknown>).visibilityState;
    }
  });

  it('marks nothing on a task filed in its owner’s main chat', async () => {
    queryClient.setQueryData(['thread', 'c7', 'timeline'], page(3, [2, 6]));
    show(<Marks t={t} own={false} />, '/tasks/p1/12');
    await act(async () => {});
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });
});

describe("an agent's run over its session box", () => {
  it('says a run that waits or stopped, and nothing for one that finished', async () => {
    run = { state: 'uncertain', label: 'Stopped — saved for later', bot_id: 'r2' };
    show(<SessionRun t={t} agent="bot:r2" />, '/tasks/p1/12');
    expect(await screen.findByText('Stopped — saved for later')).toBeTruthy();
    expect(calls[0]?.url).toBe('/api/v2/tasks/t1/execution?agent=bot%3Ar2');
  });

  it('draws a run under way when the view opens as its reply so far, and reads it again on each of its job changes', async () => {
    run = { state: 'running', label: 'Working', bot_id: 'r2', attempt_id: 'a9', text: 'Half way through the ledger', parts: [{ kind: 'reply', text: 'Half way through the ledger' }] };
    show(
      <>
        <Live t={t} />
        <SessionRun t={t} agent="bot:r2" />
      </>,
      '/tasks/p1/12',
    );
    const live = () => queryClient.getQueryData<{ run: string; bot: string; parts: string[]; typing: string | null }[]>(['thread', 'c7', 'live']) ?? [];
    await waitFor(() => expect(live()).toEqual([expect.objectContaining({ run: 'a9', bot: 'bot:r2', parts: [], typing: 'Half way through the ledger' })]));
    expect(screen.queryByText('Working')).toBeNull();
    // another agent's change reads nothing; its own job's does, queued ones too
    run = { state: 'queued', label: 'Saved — queued', bot_id: 'r2', attempt_id: 'a9' };
    act(() => dispatch('runs', [{ conversation_id: 'c7', bot_id: 'q3', state: 'queued' }]));
    await act(async () => {});
    expect(calls.filter((c) => c.url.includes('/execution'))).toHaveLength(1);
    act(() => dispatch('runs', [{ conversation_id: 'c7', bot_id: 'r2', state: 'queued' }]));
    expect(await screen.findByText('Saved — queued')).toBeTruthy();
    // the snapshot's run is no longer under way: its row goes
    expect(live()).toEqual([]);
  });

  it('carries on from the reply so far: the stream’s next words join it, and the message that ends it takes its place once', () => {
    const seeded = snapshotRun(
      {
        state: 'running',
        label: 'Working',
        bot_id: 'r2',
        attempt_id: 'a9',
        text: 'Read the ledger.\n\nHalf way',
        parts: [
          { kind: 'reply', text: 'Read the ledger.' },
          { kind: 'tool', text: 'Reading Finance' },
          { kind: 'reply', text: 'Half way' },
        ],
      },
      'bot:r2',
    );
    let runs = [seeded];
    runs = stepRun(runs, { run_id: 'a9', output: { kind: 'delta', payload: { text: ' through the ledger' } } });
    expect(liveText(runs[0]!)).toBe('Read the ledger.\n\nHalf way through the ledger');
    runs = stepRun(runs, { run_id: 'a9', output: { kind: 'message', payload: { text: 'Half way through the ledger.' } } });
    expect(liveText(runs[0]!)).toBe('Read the ledger.\n\nHalf way through the ledger.');
    // a snapshot taken at a tool call: at work, its label said
    expect(snapshotRun({ state: 'running', label: 'Working', bot_id: 'r2', attempt_id: 'a9', parts: [{ kind: 'tool', text: 'Reading Finance' }] }, 'bot:r2')).toEqual(
      expect.objectContaining({ parts: [], typing: null, doing: 'work', label: 'Reading Finance' }),
    );
  });

  it('never brings back a run that ended while its session was closed', async () => {
    run = { state: 'running', label: 'Working', bot_id: 'r2', attempt_id: 'a9', text: 'Half way', parts: [{ kind: 'reply', text: 'Half way' }] };
    function Switch() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <Live t={t} />
          <button type="button" onClick={() => setOpen(!open)}>
            Switch
          </button>
          {open && <SessionRun t={t} agent="bot:r2" />}
        </>
      );
    }
    show(<Switch />, '/tasks/p1/12');
    const live = () => queryClient.getQueryData<{ run: string }[]>(['thread', 'c7', 'live']) ?? [];
    await waitFor(() => expect(live().map((r) => r.run)).toEqual(['a9']));
    // another agent's session opens; meanwhile this agent's run ends and its reply is posted
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }));
    run = { state: 'completed', label: 'Done', bot_id: 'r2', attempt_id: 'a9' };
    act(() => {
      dispatch('runs', [{ run_id: 'a9', conversation_id: 'c7', bot_id: 'r2', state: 'completed' }]);
      dispatch('messages', [{ id: 'm30', conversation_id: 'c7', message: { id: 'm30', seq: 30, thread_id: 'c7', session_agent_id: 'bot:r2', body: 'Done', refs: { turn_id: 'a9' } } }]);
    });
    expect(live()).toEqual([]);
    // the closed session's run is read afresh when it opens, never drawn from before
    expect(queryClient.getQueryState(sessionRunQuery('t1', 'bot:r2').queryKey)).toBeUndefined();
    const reads = calls.filter((c) => c.url.includes('/execution')).length;
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }));
    // not even while it is read
    expect(live()).toEqual([]);
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/execution'))).toHaveLength(reads + 1));
    await act(async () => {});
    expect(live()).toEqual([]);
  });

  it('says nothing while its reply is being written', async () => {
    run = { state: 'running', label: 'Working', bot_id: 'r2' };
    queryClient.setQueryData(['thread', 'c7', 'live'], [{ run: 'r1', bot: 'bot:r2', parts: [], typing: null, doing: 'think', ended: false }]);
    show(<SessionRun t={t} agent="bot:r2" />, '/tasks/p1/12');
    await waitFor(() => expect(calls.length).toBe(1));
    expect(screen.queryByText('Working')).toBeNull();
  });
});
