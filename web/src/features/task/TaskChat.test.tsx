import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the test hands the chat a stream event, as the shell's startLive does
import { dispatch, installPatchers } from '@/live/patch';
import { TaskById, TaskChat } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The conversation itself is tested in its own folder: here, only which one the task's chat opens.
vi.mock('@/conversation', async (actual) => ({
  ...(await actual<Record<string, unknown>>()),
  Conversation: ({ id, title, foot, mentionFirst, view, task, compose, canSend }: { id: string; title: string; foot?: ReactNode; mentionFirst?: readonly string[]; view?: unknown; task?: unknown; compose?: string; canSend?: boolean }) => (
    <section aria-label={`Chat with ${title}`} data-conversation={id} data-mention-first={mentionFirst?.join(' ')} data-view={JSON.stringify(view ?? null)}
      data-send={JSON.stringify(task ?? null)} data-compose={compose ?? ''} data-can-send={String(canSend ?? true)}>
      {foot}
    </section>
  ),
  MessageBones: () => <div role="status" aria-label="Loading messages" />,
}));

/* Made-up rows only. */
const task = {
  id: 't1',
  number: 12,
  title: 'Fix the sign-in page',
  status: 'open',
  pipeline_id: 'p1',
  pipeline: { id: 'p1', name: 'Product' },
  stage_id: 's2',
  stage: { id: 's2', name: 'Review' },
  owner_id: 'bot:a1',
  conversation_id: 'c7',
  status_line: 'up_next',
  version: 1,
  labels: [],
  documents: [],
  to_leave: [],
  links: [],
};

/** What the server holds: the task's detail and the person's Needs you. */
let detail: Record<string, unknown> = task;
/** What the detail adds beside the task: whether the person may message its agents, its sessions. */
let top: Record<string, unknown> = {};
let needs: unknown[] = [];
let snoozed: unknown[] = [];
/** The thread read fails this many times before it answers. */
let roomFails = 0;

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  installPatchers();
});
beforeEach(() => {
  detail = task;
  top = {};
  needs = [];
  snoozed = [];
  roomFails = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'bot:a1', kind: 'agent', name: 'Birch Builder' },
    { id: 'bot:r2', kind: 'agent', name: 'Rowan Reviewer' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  serveRoom({ id: 'c7', task_id: 't1', title: task.title });
});
afterEach(() => vi.unstubAllGlobals());

/** The task's own room (`task_id` is the task), or its owner's main chat (none). */
function serveRoom(room: Record<string, unknown>) {
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    if (u.endsWith('/api/v2/threads/c7') && roomFails > 0 && roomFails--)
      return new Response(JSON.stringify({ error: { code: 'unavailable', detail: 'Try again' } }), { status: 500, headers: { 'Content-Type': 'application/json' } });
    const body = u.includes('/api/v2/tasks/')
      ? { task: detail, runs: [], children: [], comments: [], events: [], messages: [], has_more: false, ...top }
      : u.includes('/api/v2/needs-you/snoozed')
        ? { items: snoozed }
        : u.includes('/api/v2/needs-you')
        ? { items: needs, version: 'v1', count: needs.length }
        : u.includes('/api/v2/threads/c7')
        ? room
        : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('a task’s chat', () => {
  it('names who has the task and opens their conversation on it @s53', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(chat.getAttribute('data-conversation')).toBe('c7');
    // The name goes to the owner's page.
    expect(screen.getByRole('link', { name: 'Birch Builder' }).getAttribute('href')).toBe('/team/a1');
  });

  it('lists who asked, who it is for, who has it and its roles’ people first after an @, each once', async () => {
    detail = { ...task, requester_id: 'human:ana', assignee_id: 'bot:r2', roles: { reviewer: ['human:ben', 'bot:a1'] } };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(chat.getAttribute('data-mention-first')).toBe('human:ana bot:r2 bot:a1 human:ben');
  });

  it('says under the name that this is the task’s own chat, or its owner’s main chat', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    expect((await screen.findByRole('link', { name: 'Fix the sign-in page' })).getAttribute('href')).toBe('/task/t1');
    expect(screen.queryByText('Main chat')).toBeNull();
  });

  it('says its one status line over the message box, and the next one when the stream sends the task again', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(await within(chat).findByText('Up next')).toBeTruthy();
    act(() => dispatch('tasks', [{ seq: 2, id: 't1', kind: 'run', kinds: ['run'], task: { ...task, version: 2, status_line: 'working' } }]));
    expect(await within(chat).findByText('Working')).toBeTruthy();
  });

  it('says "Main chat" when the task was filed in its owner’s main chat', async () => {
    serveRoom({ id: 'c7', task_id: null, title: 'Chat with Birch Builder' });
    show(<TaskChat task="12" />, '/tasks/p1/12');
    await screen.findByText('Main chat');
    expect(screen.queryByRole('link', { name: 'Fix the sign-in page' })).toBeNull();
  });
});

/** A ❓ on the task waiting on `who` (backend/waiting_on.py: a person's wait, `qmark:<mark id>`). */
const asked = (mark: string, who: string, line: string) => ({ state: 'person', who, line, requirement_id: `qmark:${mark}`, since: null, until: null, ask: true });

describe('a question for the person, pinned over the message box', () => {
  it('pins each ❓ to the person with who asked it, and none to someone else', async () => {
    detail = {
      ...task,
      waits: [asked('m1', 'human:me', '❓ Ship the fix on Friday?'), asked('m2', 'human:pat', 'Can Pat check the copy?'), asked('m3', 'human:me', 'Keep the old page?')],
    };
    // m1 a reviewer asked, as Needs you has it (the server's own record of the ❓); m3 it does not list: the owner's.
    needs = [{ kind: 'question', id: 'qmark:m1', mark_id: 'm1', message_id: 'msg1', conversation_id: 'c7', task_id: 't1', from_id: 'bot:r2', line: 'Ship the fix on Friday?', since: null }];
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    await waitFor(() => expect(within(chat).getAllByRole('region', { name: 'Question' })).toHaveLength(2));
    const [first, second] = within(chat).getAllByRole('region', { name: 'Question' });
    expect(within(first!).getByText('Ship the fix on Friday?')).toBeTruthy();
    expect(await within(first!).findByRole('img', { name: 'Rowan Reviewer' })).toBeTruthy();
    expect(within(second!).getByText('Keep the old page?')).toBeTruthy();
    expect(within(second!).getByRole('img', { name: 'Birch Builder' })).toBeTruthy();
    expect(within(chat).queryByText('Can Pat check the copy?')).toBeNull();
    // the pin says it needs them: the status line does not say so again
    expect(within(chat).queryByText('Up next')).toBeNull();
  });

  it('names who asked a ❓ the person snoozed, which still waits on the task but has left their Needs you', async () => {
    detail = { ...task, waits: [asked('m1', 'human:me', 'Ship the fix on Friday?')] };
    snoozed = [{ kind: 'question', id: 'qmark:m1', mark_id: 'm1', message_id: 'msg1', conversation_id: 'c7', task_id: 't1', from_id: 'bot:r2', line: 'Ship the fix on Friday?', since: null, snoozed_until: '2026-12-01T00:00:00Z' }];
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const pin = await screen.findByRole('region', { name: 'Question' });
    expect(await within(pin).findByRole('img', { name: 'Rowan Reviewer' })).toBeTruthy();
  });

  it('goes when the server clears the question (the person replied) and the stream says so', async () => {
    detail = { ...task, waits: [asked('m1', 'human:me', 'Ship the fix on Friday?')] };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    await within(chat).findByRole('region', { name: 'Question' });
    // The reply cleared it (backend/question_marks.py replied): the task's change says `question`, and it is read again.
    detail = { ...task, version: 2, waits: [] };
    act(() => dispatch('tasks', [{ seq: 3, id: 't1', kind: 'question', kinds: ['question'] }]));
    await waitFor(() => expect(within(chat).queryByRole('region', { name: 'Question' })).toBeNull());
    expect(within(chat).getByText('Up next')).toBeTruthy();
  });
});

describe("a task's comments and its agents' sessions", () => {
  const chat = async () => screen.findByRole('region', { name: /^Chat with / });
  const data = (el: HTMLElement, k: string) => JSON.parse(el.getAttribute(`data-${k}`) ?? 'null');

  it('shows the comments, whose box writes a plain comment that names no agent (it wakes nobody)', async () => {
    top = { can_direct: true };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const c = await chat();
    expect(data(c, 'view')).toEqual({ comments: true });
    expect(data(c, 'send')).toEqual({ id: 't1' });
    expect(c.getAttribute('data-compose')).toBe('Add a comment');
  });

  it('keeps the box a plain comment with a ❓ pinned over it: the ❓’s own card answers it', async () => {
    detail = { ...task, waits: [asked('m1', 'human:me', 'Ship the fix on Friday?')] };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const c = await chat();
    await within(c).findByRole('region', { name: 'Question' });
    expect(data(c, 'send')).toEqual({ id: 't1' });
  });

  it('keeps the ❓s for the person over an agent’s session box too', async () => {
    detail = { ...task, waits: [asked('m1', 'human:me', 'Ship the fix on Friday?')] };
    top = { can_direct: true, sessions: [{ agent: 'bot:r2', last_seq: 9 }] };
    show(<TaskChat task="12" />, '/tasks/p1/12?session=bot%3Ar2');
    const c = await screen.findByRole('region', { name: 'Chat with Rowan Reviewer' });
    expect(within(await within(c).findByRole('region', { name: 'Question' })).getByText('Ship the fix on Friday?')).toBeTruthy();
  });

  it("opens the agent's session the address names: its name heads it, the box writes to it, ✕ goes back to the comments", async () => {
    top = { can_direct: true, sessions: [{ agent: 'bot:a1', last_seq: 4 }, { agent: 'bot:r2', last_seq: 9 }] };
    const router = show(<TaskChat task="12" />, '/tasks/p1/12?session=bot%3Ar2');
    const c = await screen.findByRole('region', { name: 'Chat with Rowan Reviewer' });
    expect(data(c, 'view')).toEqual({ session: 'bot:r2' });
    expect(data(c, 'send')).toEqual({ id: 't1', to: 'bot:r2' });
    expect(c.getAttribute('data-can-send')).toBe('true');
    expect(screen.getByRole('link', { name: 'Rowan Reviewer' }).getAttribute('href')).toBe('/team/r2');
    act(() => screen.getByRole('button', { name: 'Back to comments' }).click());
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(data(await screen.findByRole('region', { name: 'Chat with Birch Builder' }), 'view')).toEqual({ comments: true });
  });

  it('shows a session to someone who may not message its agent, with a box that sends nothing', async () => {
    top = { can_direct: false };
    show(<TaskChat task="12" />, '/tasks/p1/12?session=bot%3Ar2');
    const c = await screen.findByRole('region', { name: 'Chat with Rowan Reviewer' });
    expect(c.getAttribute('data-can-send')).toBe('false');
  });

  it('has no comments or sessions for a task filed in its owner’s main chat: that chat, as it is', async () => {
    serveRoom({ id: 'c7', task_id: null, title: 'Chat with Birch Builder' });
    show(<TaskChat task="12" />, '/tasks/p1/12?session=bot%3Ar2');
    const c = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(data(c, 'view')).toBeNull();
    expect(data(c, 'send')).toEqual({ id: 't1' });
  });
});

describe('a task chat whose thread did not read', () => {
  it('says so with Try again in place of the chat, and opens it when the read works', async () => {
    roomFails = 1;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    expect(await screen.findByText('This chat did not load')).toBeTruthy();
    expect(screen.queryByRole('status', { name: 'Loading messages' })).toBeNull();
    act(() => screen.getByRole('button', { name: 'Try again' }).click());
    expect(await screen.findByRole('region', { name: 'Chat with Birch Builder' })).toBeTruthy();
  });
});

describe('a task by its number alone', () => {
  it('shows message bones while the task is read', () => {
    vi.stubGlobal('fetch', () => new Promise(() => undefined));
    show(<TaskChat task="12" />, '/tasks/p1/12');
    return screen.findByRole('status', { name: 'Loading messages' });
  });

  it('takes the address over to the task under its pipeline @s1', async () => {
    const router = show(<TaskById task="12" />, '/task/12');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
  });
});
