import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { Conversation, timelineQuery } from '@/conversation';
import { configQuery, queryClient } from '@/data';
import { groupsQuery } from '@/features/team/exports';
import { ItemHead } from '@/pagekinds';
import { Browse } from './Browse';
import { ChatSheet } from './ChatSheet';
import { Toasts } from '@/kit/Toast';
import { LearnedPage } from './LearnedPage';
import { MenuThreads } from './MenuThreads';
import { MessagePick } from './MessagePick';
import type { Thread, ThreadList } from './model';
import { archivedQuery, browseQuery, chatQuery, learnedQuery, membersQuery, renameThread, threadKeys, threadQuery, viewersQuery } from './queries';
import { loadThreads } from './routing';
import { TeamThread } from './TeamThread';
import { ThreadHead } from './ThreadHead';
import { ThreadsIndex } from './ThreadPane';
import { ThreadsPage } from './ThreadsPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// jsdom lays nothing out: give every element a box so the virtual lists have a window to fill.
const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
});

type Reply = { status: number; body: unknown };
const calls: { url: string; method: string; body: unknown }[] = [];
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
/** `reply` answers what the test is about; anything else gets an empty list, and a thread's calls none. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const r =
      (await reply(String(url), method)) ??
      (/\/threads\/[^/]+\/calls/.test(String(url))
        ? ok({ available: true, calls: [], next_scheduled: null, next_before: null, providers: ['tico'] })
        : ok({ items: [], counts: [] }));
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const all = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0]!.endsWith(path));
const sent = (method: string, path: string) => all(method, path)[0];
const asked = () => calls.map((c) => c.url);

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const thread = (extra: Partial<Thread> = {}): Thread => ({
  id: 't1',
  title: 'Launch plan',
  audience: 'members',
  last_seq: 2,
  last_unread_seq: 0,
  last_message_at: ago(2),
  pinned_for_everyone: false,
  root_id: 't1',
  me: { can_manage: true, can_post: true, following: true, notify: 'live' },
  members: { members: [{ actor: 'human:me' }, { actor: 'human:ana' }, { actor: 'bot:qa' }] },
  ...extra,
});
/** A row of the menu's list: its name, its age in minutes, its unread count. */
const row = (id: string, title: string, min: number, unread = 0): Thread => ({
  id,
  title,
  audience: 'members',
  last_message_at: ago(min),
  last_seq: 5,
  last_unread_seq: 0,
  pinned_for_everyone: false,
  root_id: id,
  me: { unread, can_manage: true },
});
const seeded = (): ThreadList => ({ pages: [{ rows: [row('t1', 'Launch plan', 2, 3), row('t2', 'Hiring', 30)], next: null }], pageParams: [null] });
const msg = (id: string, sender: string, body: string, conv = 't1') => ({
  id,
  seq: Number(id.slice(1)),
  thread_id: conv,
  conversation_id: conv,
  sender_id: sender,
  body,
  created_at: ago(30 - Number(id.slice(1))),
});
function seedThread(t: Thread, messages: ReturnType<typeof msg>[], next: string | null = null) {
  queryClient.setQueryData(threadKeys.view(t.id), t);
  queryClient.setQueryData(timelineQuery(t.id).queryKey, { pages: [{ messages, next_cursor: next, read_marks: {} }], pageParams: [null] } as never);
}
/** The menu's list and catch-up, as the Threads page finds them. */
function seedList(rows: Thread[] = [thread()]) {
  queryClient.setQueryData(threadKeys.list, { pages: [{ rows, next: null }], pageParams: [null] });
  queryClient.setQueryData(threadKeys.catchUp, { count: 0, items: [] });
}
const people = (actors: { id: string; kind: string; name: string; slug?: string; team?: string }[]) =>
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  people([
    { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
    { id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' },
    { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops' },
  ]);
});
afterEach(() => vi.unstubAllGlobals());

describe('A thread @s157', () => {
  it('shows its messages under its name and sends from the box', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/t1/messages') ? ok(msg('m3', 'human:me', 'On it')) : undefined));
    seedList();
    seedThread(thread(), [msg('m1', 'bot:qa', 'The build is ready for review.'), msg('m2', 'human:ana', 'Looking now.')]);
    show(<ThreadsPage pane="thread" thread="t1" />, '/threads/t1');
    expect(await screen.findByText('The build is ready for review.')).toBeTruthy();
    expect(screen.getByText('Looking now.')).toBeTruthy();
    const box = screen.getByRole('textbox', { name: 'Message Launch plan' });
    fireEvent.change(box, { target: { value: 'On it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent('POST', '/api/v2/threads/t1/messages')?.body).toMatchObject({ text: 'On it' }));
  });

  it('has no box where the person may only read', async () => {
    serve();
    seedList();
    seedThread(thread({ me: { can_manage: false, can_post: false } }), [msg('m1', 'bot:qa', 'Closed for now.')]);
    show(<ThreadsPage pane="thread" thread="t1" />, '/threads/t1');
    expect(await screen.findByText('Closed for now.')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Message Launch plan' })).toBeNull();
  });
});

describe('A link to a reply @4918', () => {
  it('opens a team thread\'s sub-thread as itself, at the message it names', async () => {
    const sub = thread({ id: 's2', title: 'Launch reply', audience: 'home', home: { actor: 'group:g1' }, parent_id: 'h1', root_id: 'h1' });
    seedThread(sub, [msg('m1', 'bot:qa', 'In the sub-thread', 's2')]);
    show(<ThreadsPage pane="thread" thread="s2" message="m1" />, '/threads/s2?m=m1');
    expect(await screen.findByText('In the sub-thread')).toBeTruthy();
  });
});

describe('A thread from outside @4882', () => {
  const outside = () => {
    const t = thread({ id: 's1', title: 'Slack: Acme', root_id: 's1', members: { members: [{ actor: 'human:me' }, { actor: 'slack:U1' }, { actor: 'bot:qa' }] } });
    seedList([t]);
    seedThread(t, [msg('m1', 'slack:U1', 'Is the order shipped?', 's1')]);
    return show(<ThreadsPage pane="thread" thread="s1" />, '/threads/s1');
  };
  const say = async (words: string) => {
    fireEvent.change(await screen.findByRole('textbox', { name: 'Message QA' }), { target: { value: words } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  };
  const posts = () => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/threads/dm/messages'));

  it('sends again once when the DM changed as the server wrote, and a refusal keeps the words with its reason @4882-S1 @4882-S3', async () => {
    let n = 0;
    serve((url, method) => {
      if (method !== 'POST' || !url.endsWith('/threads/dm/messages')) return undefined;
      n += 1;
      if (n === 1) return { status: 409, body: { error: { code: 'dm_changed', detail: 'Your chat with bot:qa changed; send it again', retryable: true } } };
      if (n === 2) return ok({ ...msg('m5', 'human:me', 'First', 'd5'), conversation_id: 'd5' });
      return { status: 403, body: { error: { code: 'forbidden', detail: 'You can see bot:qa but not message them' } } };
    });
    const router = outside();
    await say('First');
    await waitFor(() => expect(new URLSearchParams(router.state.location.searchStr).get('chat')).toBe('thread:d5'));
    expect(posts()).toHaveLength(2);
    await say('Second');
    expect(await screen.findByText('You can see bot:qa but not message them')).toBeTruthy();
    expect((screen.getByRole('textbox', { name: 'Message QA' }) as HTMLTextAreaElement).value).toBe('Second');
  });

  it('opens no chat when the person moved to another thread before the send came back @4882-S8', async () => {
    let answer!: (r: Reply) => void;
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/dm/messages') ? new Promise<Reply>((r) => (answer = r)) : undefined));
    const router = outside();
    await say('Slow words');
    await waitFor(() => expect(answer).toBeTypeOf('function'));
    await act(() => router.navigate({ to: '/threads/t1' as never }));
    await act(async () => answer(ok({ ...msg('m6', 'human:me', 'Slow words', 'd6'), conversation_id: 'd6' })));
    expect(new URLSearchParams(router.state.location.searchStr).get('chat')).toBeNull();
  });

  it('says a held message was held, clears the box and opens no chat @4882-S2', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/dm/messages') ? ok({ hold: { id: 'h1' } }) : undefined));
    const router = outside();
    await say('Held words');
    expect(await screen.findByText('Held for a check')).toBeTruthy();
    expect((screen.getByRole('textbox', { name: 'Message QA' }) as HTMLTextAreaElement).value).toBe('');
    expect(new URLSearchParams(router.state.location.searchStr).get('chat')).toBeNull();
  });

  it('has a box only to its agent, sent to the DM with exactly it, and the thread it went to opens beside', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/threads/dm/messages') ? ok({ ...msg('m5', 'human:me', 'Answer them for me'), conversation_id: 'd5', thread_id: 'd5' }) : undefined,
    );
    const outside = thread({ id: 's1', title: 'Slack: Acme', root_id: 's1', members: { members: [{ actor: 'human:me' }, { actor: 'slack:U1' }, { actor: 'bot:qa' }] } });
    seedList([outside]);
    seedThread(outside, [msg('m1', 'slack:U1', 'Is the order shipped?', 's1')]);
    const router = show(<ThreadsPage pane="thread" thread="s1" />, '/threads/s1');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Message QA' }), { target: { value: 'Answer them for me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent('POST', '/api/v2/threads/dm/messages')).toBeTruthy());
    expect(sent('POST', '/api/v2/threads/dm/messages')?.body).toEqual({
      text: 'Answer them for me',
      to_dm: 'bot:qa',
      refs: { from_page: { page: 'thread', label: 'Thread', item: { kind: 'thread', id: 's1' } } },
    });
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/threads/s1/messages'))).toBe(false);
    await waitFor(() => expect(new URLSearchParams(router.state.location.searchStr).get('chat')).toBe('thread:d5'));
  });
});

describe('Threads, no thread open @s156', () => {
  it('says no thread is open; its list is the menu\'s', async () => {
    serve();
    seedList();
    show(<ThreadsIndex />, '/threads');
    expect(await screen.findByRole('heading', { name: 'No thread open' })).toBeTruthy();
    expect(asked().some((url) => url.includes('/timeline') || url.includes('/calls'))).toBe(false);
  });
});

describe('A chat with an agent @s605', () => {
  it('is the DM named for the agent, with a box to write to it', async () => {
    serve();
    const dm = thread({ id: 't4', title: '', audience: 'personal', root_id: 't4', members: { members: [{ actor: 'human:me' }, { actor: 'bot:qa' }] } });
    seedList([dm]);
    seedThread(dm, [msg('m1', 'bot:qa', 'Morning. Two tests failed overnight.', 't4')]);
    show(<ThreadsPage pane="thread" thread="t4" />, '/threads/t4');
    expect(await screen.findByText('Morning. Two tests failed overnight.')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Message QA' })).toBeTruthy();
  });
});

describe('The second chat @s225 @s226', () => {
  it('opens an agent chat beside the page: its DM, the agent named in the head, and a box', async () => {
    const dm = thread({ id: 't3', title: '', audience: 'personal', root_id: 't3', members: { members: [{ actor: 'human:me' }, { actor: 'bot:qa' }] } });
    serve((url) => (url.includes('/threads/dm?') ? ok(dm) : undefined));
    queryClient.setQueryData(timelineQuery('t3').queryKey, { pages: [{ messages: [msg('m1', 'bot:qa', 'Ask me about the tests.', 't3')], next_cursor: null, read_marks: {} }], pageParams: [null] } as never);
    show(<ChatSheet chat="agent:qa" />, '/tasks?chat=agent%3Aqa');
    expect(await screen.findByText('Ask me about the tests.')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('/api/v2/threads/dm?with=bot%3Aqa'))).toBe(true);
    expect(screen.getByRole('textbox', { name: 'Message QA' })).toBeTruthy();
    // The head says this is the agent's main chat, not a task's.
    expect(screen.getByText('Main chat')).toBeTruthy();
  });
});

describe('The Agent bar reads the member-private DM @4857-S28', () => {
  it('asks for the private DM, never an older pair chat', async () => {
    const dm = thread({ id: 't9', title: '', audience: 'personal', root_id: 't9', members: { members: [{ actor: 'human:me' }, { actor: 'bot:qa' }] } });
    serve((url) => (url.includes('/threads/dm?') ? ok(dm) : undefined));
    show(<ChatSheet chat="agent:qa" dm />, '/tasks');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/api/v2/threads/dm?with=bot%3Aqa&private=true'))).toBe(true));
  });
});

describe('The Agent bar that did not load @4857-S16', () => {
  it('keeps its head, the agent named and linked, where the frame puts its fold control, with Try again', async () => {
    serve((url) => (url.includes('/threads/dm?') ? { status: 500, body: { error: { code: 'boom', detail: 'Broke' } } } : undefined));
    queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
    show(<ChatSheet chat="agent:qa" />, '/tasks');
    expect(await screen.findByText('This chat did not load')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'QA' }).closest('header')?.className).toContain('side-head');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('Message @s158', () => {
  const pick = () => show(<MessagePick open onOpenChange={() => undefined} />, '/threads');

  it('one name goes to your DM with them, on their page; nothing is sent', async () => {
    serve((url) => (url.includes('/api/v2/groups') ? ok([]) : undefined));
    const router = pick();
    fireEvent.change(await screen.findByRole('combobox', { name: 'To' }), { target: { value: 'ana' } });
    fireEvent.click(screen.getByRole('option', { name: /Ana Ruiz/ }));
    expect(screen.getByText('Ana Ruiz', { selector: '.to-chip' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'To' }), { key: 'Enter' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/ana'));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('a team goes to its thread', async () => {
    serve((url) =>
      url.includes('/api/v2/groups')
        ? ok([{ id: 'g1', name: 'Growth', parent: '', people: [], bots: [] }])
        : url.includes('/threads/home/') ? ok(thread({ id: 'h1', title: 'Growth', audience: 'home', home: { actor: 'group:g1' } })) : undefined,
    );
    const router = pick();
    fireEvent.change(await screen.findByRole('combobox', { name: 'To' }), { target: { value: 'gro' } });
    fireEvent.click(await screen.findByRole('option', { name: 'Growth' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/h1'));
    expect(sent('GET', '/api/v2/threads/home/group%3Ag1')).toBeTruthy();
  });

  it('several names go to the one group DM for that set, found or made with no message', async () => {
    serve((url, method) =>
      url.includes('/api/v2/groups') ? ok([]) : method === 'POST' && url.endsWith('/threads/dm') ? ok(thread({ id: 'g7', title: 'Ana, QA', audience: 'personal' })) : undefined,
    );
    const router = pick();
    const to = await screen.findByRole('combobox', { name: 'To' });
    fireEvent.change(to, { target: { value: 'ana' } });
    fireEvent.click(screen.getByRole('option', { name: /Ana Ruiz/ }));
    fireEvent.change(to, { target: { value: 'qa' } });
    fireEvent.click(screen.getByRole('option', { name: /QA/ }));
    // with the chips named, the one row is the set
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Ana, QA']);
    fireEvent.click(screen.getByRole('option', { name: 'Ana, QA' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/g7'));
    expect(sent('POST', '/api/v2/threads/dm')?.body).toEqual({ members: ['human:ana', 'bot:qa'], title: 'Ana, QA' });
  });
});

describe('The menu\'s Threads list @s156', () => {
  beforeEach(() => people([]));

  it('shows a pending Threads read and lets the viewer recover if it fails', async () => {
    let finish!: (reply: Reply) => void;
    const first = new Promise<Reply>((resolve) => { finish = resolve; });
    let reads = 0;
    serve((url) =>
      url.startsWith('/api/v2/threads?sort=') ? (++reads === 1 ? first : ok({ threads: [row('t1', 'Launch plan', 2)] })) : undefined,
    );
    show(<MenuThreads asks={new Set()} />, '/threads');
    expect(await screen.findByRole('status', { name: 'Loading threads' })).toBeTruthy();
    finish({ status: 500, body: {} });
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByTitle('Launch plan')).toBeTruthy();
    expect(reads).toBe(2);
  });

  it('lists every conversation but your 1:1 DMs, newest first: bold when new, its age or the Dot, no counts, no tasks', async () => {
    serve(() => ok());
    const task = { ...row('k1', 'Fix the build', 1, 2), task_id: 'k1' };
    const dm = { ...row('d1', 'Chat with Ana', 3), audience: 'personal' as const, dm_with: [{ actor: 'human:ana' }] };
    const group = { ...row('d2', '', 4), audience: 'personal' as const, dm_with: [{ actor: 'human:ana' }, { actor: 'human:jo' }], dm_with_count: 2 };
    const agentHome = { ...row('h1', 'QA Owner', 5), audience: 'home' as const, home: { actor: 'bot:qa' } };
    const teamHome = { ...row('h2', 'Growth', 6), audience: 'home' as const, home: { actor: 'group:g1' } };
    queryClient.setQueryData(threadKeys.list, {
      pages: [{ rows: [row('t1', 'Launch plan', 2, 3), row('t2', 'Hiring', 30), task, dm, group, agentHome, teamHome], next: null }],
      pageParams: [null],
    });
    show(<MenuThreads asks={new Set(['t2'])} />, '/threads/t2');
    const launch = await screen.findByTitle('Launch plan');
    expect(launch.getAttribute('href')).toBe('/threads/t1');
    expect(launch.className).toContain('is-new');
    expect(launch.querySelector('.dot')).toBeNull();
    expect(launch.textContent).toBe('Launch plan2m');
    const hiring = screen.getByTitle('Hiring');
    expect(hiring.className).not.toContain('is-new');
    expect(hiring.getAttribute('aria-current')).toBe('page');
    // the Dot stands in for the age
    expect(hiring.querySelector('.dot')?.getAttribute('aria-label')).toBe('Asks you');
    expect(hiring.querySelector('time')).toBeNull();
    expect(screen.queryByTitle('Fix the build')).toBeNull();
    // your DM with one teammate and an agent's own room are in Team; a group DM and a team's thread are here
    expect(screen.queryByTitle('Ana')).toBeNull();
    expect(screen.queryByTitle('QA Owner')).toBeNull();
    expect(screen.getByTitle('ana, jo')).toBeTruthy();
    expect(screen.getByTitle('Growth').getAttribute('href')).toBe('/threads/h2');
    expect([...document.querySelectorAll('.tree-row')].map((r) => r.getAttribute('title'))).toEqual(['Launch plan', 'ana, jo', 'Growth', 'Hiring']);
    expect(screen.queryByRole('link', { name: 'Mail' })).toBeNull();
    expect(document.querySelector('.count')).toBeNull();
  });

  it('sets mail and texts among them, each with its source mark, opening its mailbox or its texts', async () => {
    serve((url) => {
      if (url.includes('/mail/mailboxes'))
        return ok({ mailboxes: [{ address: 'orders@fernhill.example', connection: 'workspace', may: { organize: true } }, { address: 'off@x', connection: 'none' }] });
      if (url.includes('/mail/messages'))
        return ok({ messages: [{ msg_id: 'l1', thread_id: 'mt1', mailbox: 'orders@fernhill.example', epoch: Math.floor(Date.now() / 1000) - 600, from_header: 'Ruth Ames <ruth@cedarmill.example>', subject: 'Cedar Mill: 2027 price terms', labels: ['UNREAD'] }] });
      if (url.includes('/inbound/messages')) return ok({ messages: [{ message_id: 's1', received_at: ago(20), from: '+15035550142' }], next: null });
      return ok();
    });
    queryClient.setQueryData(threadKeys.list, seeded());
    show(<MenuThreads asks={new Set()} />, '/threads');
    const mail = await screen.findByTitle('Ruth Ames: Cedar Mill: 2027 price terms');
    expect(mail.getAttribute('href')).toBe('/threads/mail?box=orders%40fernhill.example&open=mt1');
    expect(mail.className).toContain('is-new');
    expect(mail.querySelector('[aria-label="Mail"]')).toBeTruthy();
    const text = await screen.findByTitle('+15035550142');
    expect(text.querySelector('[aria-label="Text"]')).toBeTruthy();
    expect([...document.querySelectorAll('.tree-row')].map((r) => r.getAttribute('title'))).toEqual([
      'Launch plan',
      'Ruth Ames: Cedar Mill: 2027 price terms',
      '+15035550142',
      'Hiring',
    ]);
    // a text has no archive yet; mail and threads do
    expect(text.closest('.cr')?.querySelector('.cr-arch')).toBeNull();
    expect(mail.closest('.cr')?.querySelector('.cr-arch')).toBeTruthy();
  });

  it('Archive is only where it is the viewer\'s own: a mail row archives in its mailbox; a Tico thread has none', async () => {
    const letter = { msg_id: 'l1', thread_id: 'mt1', mailbox: 'orders@fernhill.example', epoch: Math.floor(Date.now() / 1000) - 600, from_header: 'Ruth Ames <r@x>', subject: 'Terms' };
    serve((url) =>
      url.includes('/mail/mailboxes')
        ? ok({ mailboxes: [{ address: 'orders@fernhill.example', connection: 'workspace', may: { organize: true } }] })
        : url.includes('/mail/messages?')
          // once archived, the inbox no longer holds it
          ? ok({ messages: calls.some((c) => c.url.includes('/organize')) ? [] : [letter] })
          : url.includes('/threads/archived')
            ? ok({ threads: [{ ...row('t9', 'Old launch', 90), archived_at: ago(80) }], next_cursor: null })
            : ok(),
    );
    queryClient.setQueryData(threadKeys.list, seeded());
    show(<MenuThreads asks={new Set()} />, '/threads');
    const hiring = await screen.findByTitle('Hiring');
    // a thread's archive is everyone's on the server: no verb on its row
    expect(hiring.closest('.cr')?.querySelector('.cr-arch')).toBeNull();
    const mail = await screen.findByTitle('Ruth Ames: Terms');
    fireEvent.click(mail.closest('.cr')!.querySelector<HTMLButtonElement>('.cr-arch')!);
    await waitFor(() => expect(screen.queryByTitle('Ruth Ames: Terms')).toBeNull());
    expect(sent('POST', '/mail/messages/organize')?.body).toEqual({ mailbox: 'orders@fernhill.example', action: 'archive', ids: ['l1'] });
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    // archived threads fold at the foot
    fireEvent.click(await screen.findByRole('button', { name: /Archived/, expanded: false }));
    expect((await screen.findByTitle('Old launch')).getAttribute('href')).toBe('/threads/t9');
  });

  it('a swipe to the left on a touch screen shows Archive on a mail row; a tap puts the row back', async () => {
    serve((url) =>
      url.includes('/mail/mailboxes')
        ? ok({ mailboxes: [{ address: 'orders@fernhill.example', connection: 'workspace' }] })
        : url.includes('/mail/messages?')
          ? ok({ messages: [{ msg_id: 'l1', thread_id: 'mt1', mailbox: 'orders@fernhill.example', epoch: Math.floor(Date.now() / 1000) - 600, from_header: 'Ruth Ames <r@x>', subject: 'Terms' }] })
          : ok(),
    );
    // jsdom has no PointerEvent: one that carries the pointer's kind and place
    class Pointer extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? '';
      }
    }
    vi.stubGlobal('PointerEvent', Pointer);
    queryClient.setQueryData(threadKeys.list, seeded());
    show(<MenuThreads asks={new Set()} />, '/threads');
    const mailRow = (await screen.findByTitle('Ruth Ames: Terms')).closest('.cr')!;
    fireEvent.pointerDown(mailRow, { pointerType: 'touch', clientX: 200, clientY: 10 });
    fireEvent.pointerMove(mailRow, { pointerType: 'touch', clientX: 120, clientY: 12 });
    expect(mailRow.className).toContain('is-swiped');
    expect(within(mailRow as HTMLElement).getByRole('button', { name: 'Archive' }).className).toBe('cr-swipe');
    fireEvent.pointerDown(mailRow, { pointerType: 'touch', clientX: 60, clientY: 10 });
    fireEvent.click(screen.getByTitle('Ruth Ames: Terms'));
    expect(mailRow.className).not.toContain('is-swiped');
  });

  it('shows a rename at once and takes it back when the server refuses it', async () => {
    let refuse!: () => void;
    serve((_url, method) => (method === 'PATCH' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : ok()));
    queryClient.setQueryData(threadKeys.list, seeded());
    show(<MenuThreads asks={new Set()} />, '/threads');
    await screen.findByTitle('Hiring');
    let done!: Promise<unknown>;
    act(() => {
      done = renameThread('t2', 'Hiring plan').catch(() => 'refused');
    });
    expect(await screen.findByTitle('Hiring plan')).toBeTruthy();
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => {
      refuse();
      expect(await done).toBe('refused');
    });
    expect(await screen.findByTitle('Hiring')).toBeTruthy();
    expect(screen.queryByTitle('Hiring plan')).toBeNull();
  });
});

describe('Opening a thread', () => {
  it('starts loading messages while the header is in flight and does not wait for the menu list', async () => {
    let openHeader!: (r: Reply) => void;
    let openMessages!: (r: Reply) => void;
    let openList!: (r: Reply) => void;
    const header = new Promise<Reply>((resolve) => { openHeader = resolve; });
    const messages = new Promise<Reply>((resolve) => { openMessages = resolve; });
    const list = new Promise<Reply>((resolve) => { openList = resolve; });
    const view = '/api/v2/threads/t1';
    const timeline = `${view}/timeline?flat=1&limit=50`;
    serve((url) => (url === view ? header : url === timeline ? messages : list));
    const loaded = loadThreads(queryClient, { thread: 't1' });
    await waitFor(() => expect(asked()).toEqual(expect.arrayContaining([view, timeline])));
    openHeader(ok(row('t1', 'Launch plan', 0)));
    await loaded;
    expect(queryClient.getQueryState(threadKeys.list)?.fetchStatus).toBe('fetching');

    const read = queryClient.ensureInfiniteQueryData(timelineQuery('t1'));
    openMessages(ok({ messages: [], next_cursor: null }));
    openList(ok({ threads: [], next_cursor: null }));
    await expect(read).resolves.toMatchObject({ pages: [{ messages: [] }] });
    expect(asked().filter((u) => u === timeline)).toHaveLength(1);
  });

  it('preloads the header on intent and waits until navigation to read messages', async () => {
    queryClient.setQueryData(threadKeys.list, seeded());
    serve(() => ok(row('t1', 'Launch plan', 0)));
    await loadThreads(queryClient, { thread: 't1', preload: true });
    expect(asked()).toEqual(['/api/v2/threads/t1']);
    expect(queryClient.getQueryState(timelineQuery('t1').queryKey)).toBeUndefined();
  });

  it('uses the cached header when the same thread opens in the side chat', async () => {
    const t = row('t1', 'Launch plan', 0);
    serve(() => ok(t));
    await queryClient.fetchQuery(threadQuery(t.id));
    await expect(queryClient.fetchQuery(chatQuery(`thread:${t.id}`))).resolves.toEqual(t);
    expect(asked()).toEqual(['/api/v2/threads/t1']);
  });
});

describe('A team\'s thread', () => {
  it('opens the team\'s Home and sets its teammates\' updates among its messages as cards', async () => {
    const home = { ...row('h1', 'Growth', 5), audience: 'home', home: { actor: 'group:g1' }, me: { can_post: true } };
    const ups = [
      { id: 'u1', bot: 'gm', kind: 'weekly', headline: 'Orders 1,284. Cedar beds sold out.', body: 'Orders 1,284. Cedar beds sold out.', replies: 2, created_at: ago(30) },
      { id: 'u2', bot: 'ops', kind: 'daily', body: 'Not this team', created_at: ago(20) },
    ];
    serve((url) => {
      if (url.includes('/api/v2/groups')) return ok([{ id: 'g1', name: 'Growth', parent: '', people: [], bots: ['gm'] }]);
      if (url.includes('/threads/home/')) return ok(home);
      if (url.includes('/api/v2/updates')) return ok({ updates: ups, unread: 1, next_before: null });
      if (url.includes('/timeline')) return ok({ messages: [{ id: 'm1', seq: 1, sender_id: 'human:jo', body: 'Proofs are in.', created_at: ago(40) }, { id: 'm2', seq: 2, sender_id: 'human:jo', body: 'Page 6 fixed.', created_at: ago(10) }], next_cursor: null, read_marks: {} });
      return ok();
    });
    people([
      { id: 'bot:gm', kind: 'agent', name: 'Growth Manager', slug: 'gm', team: 'Growth' },
      { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops', team: 'Engineering' },
      { id: 'human:jo', kind: 'person', name: 'Jonas Lind', team: 'Growth' },
    ]);
    show(<TeamThread who="team:Growth" />, '/team/team%3AGrowth');
    const card = await screen.findByText('Orders 1,284. Cedar beds sold out.');
    expect(card.closest('a')?.getAttribute('href')).toBe('/updates?open=u1');
    expect(card.closest('.kcard')?.textContent).toContain('Weekly update');
    expect(screen.queryByText('Not this team')).toBeNull();
    expect(asked().some((u) => u.includes('/api/v2/threads/home/group%3Ag1'))).toBe(true);
    // the update sits between the two messages, by time
    const order = [...document.querySelectorAll('.msg')].map((m) => m.textContent ?? '');
    expect(order.findIndex((t) => t.includes('Proofs'))).toBeLessThan(order.findIndex((t) => t.includes('Orders')));
    expect(order.findIndex((t) => t.includes('Orders'))).toBeLessThan(order.findIndex((t) => t.includes('Page 6')));
  });
});

describe('A thread head @s157', () => {
  it('puts Archive before ⋯, names the task by code and stage, and has no Add people or Mute', async () => {
    serve((url) =>
      url.includes('/api/v2/tasks/k1') ? ok({ task: { id: 'k1', title: 'Fix it', label: 'E604', number: 604, status: 'open', stage: { name: 'Build' } }, comments: [], events: [], children: [] }) : undefined,
    );
    queryClient.setQueryData(threadKeys.view('t1'), thread({ task_id: 'k1' }));
    show(<ThreadHead id="t1" />, '/threads/t1');
    const chip = await screen.findByRole('link', { name: /E604/ });
    await waitFor(() => expect(chip.textContent).toBe('E604Build'));
    expect(screen.getByRole('button', { name: 'Archive' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Add people/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('menuitem', { name: 'Settings' });
    expect(screen.queryByRole('menuitem', { name: /Mute/ })).toBeNull();
  });

  it('a manager archives a team thread from its head; a DM or group DM has no Archive', async () => {
    people([]);
    const team = { ...row('h1', 'Growth', 2), audience: 'home' as const, home: { actor: 'group:g1' }, me: { can_manage: true, can_post: true } };
    const group = { ...row('d2', '', 2), audience: 'personal' as const, dm_with: [{ actor: 'human:ana' }, { actor: 'human:jo' }], me: { can_manage: true, can_post: true } };
    serve((url) => ok(url.endsWith('/threads/h1') ? team : url.endsWith('/threads/d2') ? group : {}));
    show(<ThreadHead id="h1" />, '/threads/h1');
    expect(await screen.findByRole('button', { name: 'Archive' })).toBeTruthy();
    document.body.innerHTML = '';
    show(<ThreadHead id="d2" />, '/threads/d2');
    expect(await screen.findByRole('heading', { name: 'ana, jo' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Archive' })).toBeNull();
  });

  it('offers Join while a call is on in the thread', async () => {
    const live = { id: 'c1', state: 'live', provider: 'tico', thread_id: 't1', participants: [], bots: [], started_at: ago(3) };
    serve((url) => {
      if (url.includes('/threads/t1/calls')) return ok({ available: true, calls: [live], next_scheduled: null, next_before: null, providers: ['tico'] });
      if (url.includes('/calls/c1')) return ok(live);
      if (url.endsWith('/threads/t1')) return ok({ ...row('t1', 'Launch plan', 2), me: { can_post: true }, members: { members: [{ actor: 'human:me' }, { actor: 'human:ana' }] } });
      return ok();
    });
    show(<ThreadHead id="t1" />, '/threads/t1');
    const join = await screen.findByRole('button', { name: 'Join call' });
    await waitFor(() => expect((join as HTMLButtonElement).disabled).toBe(false));
  });

  it('turns the title into a field, saves on Enter and keeps the old name on Escape', async () => {
    const renamed = vi.fn();
    show(<ItemHead title="Hiring" onRename={renamed} renameLabel="Thread name" />, '/threads/t2');
    fireEvent.click(await screen.findByRole('heading', { name: 'Hiring' }));
    const field = screen.getByRole('textbox', { name: 'Thread name' });
    fireEvent.change(field, { target: { value: 'Hiring plan' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(renamed).toHaveBeenCalledExactlyOnceWith('Hiring plan');
    fireEvent.click(screen.getByRole('heading', { name: 'Hiring' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Thread name' }), { key: 'Escape' });
    expect(renamed).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('heading', { name: 'Hiring' })).toBeTruthy();
  });
});

describe('Chat outline @s219', () => {
  it("lists the chat's prompts from the server; a row closes it and pages back to reach that message", async () => {
    serve((url) => {
      if (url.includes('/outline')) return ok({ prompts: [{ id: 'm1', created_at: ago(29), text: 'First ask' }] });
      if (url.includes('/timeline') && url.includes('cursor=c1')) return ok({ messages: [msg('m1', 'human:me', 'First ask')], next_cursor: null, read_marks: {} });
      return undefined;
    });
    seedThread(thread(), [msg('m5', 'human:me', 'Later ask'), msg('m6', 'bot:qa', 'Later answer')], 'c1');
    show(
      <>
        <ThreadHead id="t1" />
        <Conversation id="t1" variant="page" />
      </>,
      '/threads/t1',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Outline' }));
    const dialog = await screen.findByRole('dialog', { name: 'Outline' });
    fireEvent.click(await within(dialog).findByRole('button', { name: /First ask/ }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Outline' })).toBeNull());
    await waitFor(() => expect(calls.some((c) => c.url.includes('/api/v2/threads/t1/timeline') && c.url.includes('cursor=c1'))).toBe(true));
  });
});

describe('People in a thread @s177', () => {
  it('opens from the faces under the title; someone being taken out stays, busy, until the server answers', async () => {
    let refuse!: () => void;
    serve((_url, method) => (method === 'DELETE' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : undefined));
    queryClient.setQueryData(threadKeys.view('t1'), thread());
    queryClient.setQueryData(membersQuery('t1').queryKey, {
      pages: [{ members: [{ actor: 'human:me', role: 'owner' }, { actor: 'human:ana', role: 'member' }, { actor: 'bot:qa', role: 'member' }], next_cursor: null }],
      pageParams: [null],
    });
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'People' }));
    const dialog = await screen.findByRole('dialog', { name: 'People' });
    expect(within(dialog).getByText('Owner')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Remove You' })).toBeNull();

    const remove = within(dialog).getByRole('button', { name: 'Remove Ana Ruiz' });
    fireEvent.click(remove);
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    expect(sent('DELETE', '/api/v2/threads/t1/members/human%3Aana')).toBeTruthy();
    expect(within(dialog).getByText('Ana Ruiz')).toBeTruthy();
    expect(remove.getAttribute('aria-busy')).toBe('true');
    await act(async () => refuse());
    expect(within(dialog).getByText('Ana Ruiz')).toBeTruthy();
    await waitFor(() => expect(remove.getAttribute('aria-busy')).toBeNull());
  });
});

describe('Thread settings @s178', () => {
  beforeEach(() => queryClient.setQueryData(groupsQuery.queryKey, [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: [], people: [] }]));
  const settings = async (t: Thread) => {
    serve();
    queryClient.setQueryData(threadKeys.view('t1'), t);
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
    return screen.findByRole('dialog', { name: 'Settings' });
  };

  it('a manager sees every row, saves Pin for everyone, and is asked before deleting; Tico may change how it notifies but not open it to everyone', async () => {
    const dialog = await settings(thread());
    expect(within(dialog).getByText('Notify me')).toBeTruthy();
    expect(within(dialog).getByText('Who can read')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Pin for everyone' }));
    await waitFor(() => expect(all('PATCH', '/api/v2/threads/t1').at(-1)?.body).toEqual({ pinned_for_everyone: true }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete thread' }));
    expect(await screen.findByRole('alertdialog', { name: 'Delete this thread?' })).toBeTruthy();

    expect((await run('threads.notify', { thread: 't1', notify: 'mentions' }, { via: 'tico' })).ok).toBe(true);
    expect(all('PUT', '/api/v2/threads/t1/follow').at(-1)?.body).toEqual({ notify: 'mentions' });
    const before = all('PATCH', '/api/v2/threads/t1').length;
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'company' }, { via: 'tico' })).ok).toBe(false);
    expect((await run('threads.delete', { thread: 't1' }, { via: 'tico' })).ok).toBe(false);
    expect(all('PATCH', '/api/v2/threads/t1')).toHaveLength(before);
    expect(all('DELETE', '/api/v2/threads/t1')).toHaveLength(0);
  });

  it('leaves out what only a manager may change', async () => {
    const dialog = await settings(thread({ me: { can_manage: false, can_post: true } }));
    expect(within(dialog).getByText('Notify me')).toBeTruthy();
    expect(within(dialog).queryByText('Who can read')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Delete thread' })).toBeNull();
  });

  it('lets a team be chosen as its reader', async () => {
    const dialog = await settings(thread());
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Who can read' }));
    const o = await screen.findByRole('option', { name: 'Design' });
    fireEvent.pointerDown(o, { pointerType: 'mouse' });
    fireEvent.click(o);
    await waitFor(() => expect(all('PATCH', '/api/v2/threads/t1').at(-1)?.body).toEqual({ audience: 'team', audience_group_id: 'g1' }));
  });

  it('a team reader needs the team, and Tico may not open it wider', async () => {
    serve();
    show(<span />, '/threads/t1');
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team' }, { via: 'click' })).ok).toBe(false);
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team', team: 'g1' }, { via: 'tico' })).ok).toBe(false);
    expect(all('PATCH', '/api/v2/threads/t1')).toHaveLength(0);
  });
});

describe('Browse threads @s176', () => {
  it('joins a thread: it leaves the list at once and the follow is sent', async () => {
    serve(() => ok());
    queryClient.setQueryData(browseQuery('').queryKey, {
      pages: [{ threads: [{ ...thread({ id: 't7', title: 'Hiring', audience: 'company' }), member_count: 4 }], next_cursor: null }],
      pageParams: [null],
    });
    show(<Browse onClose={() => {}} />, '/threads');
    const dialog = await screen.findByRole('dialog', { name: 'Browse threads' });
    expect(within(dialog).getByText('Hiring')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join' }));
    await waitFor(() => expect(sent('PUT', '/api/v2/threads/t7/follow')).toBeTruthy());
  });

  it('says so when there is nothing to join', async () => {
    serve(() => ok({ threads: [], next_cursor: null }));
    show(<Browse onClose={() => {}} />, '/threads');
    expect(await screen.findByRole('heading', { name: 'No threads to join' })).toBeTruthy();
  });

  it('opens a thread from its name, to read it before joining', async () => {
    serve((url) => (url.includes('/threads/browse') ? ok({ threads: [{ ...thread({ id: 't5', title: 'Hiring' }), member_count: 3 }], next_cursor: null }) : undefined));
    const onClose = vi.fn();
    const router = show(<Browse onClose={onClose} />, '/threads');
    const dialog = await screen.findByRole('dialog', { name: 'Browse threads' });
    fireEvent.click(await within(dialog).findByRole('link', { name: 'Hiring' }));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/t5'));
    expect(all('POST', '/api/v2/threads/t5/join')).toHaveLength(0);
  });

  it('lists archived threads under Archived; Move back takes one out at once and sends the unarchive', async () => {
    serve();
    queryClient.setQueryData(archivedQuery.queryKey, {
      pages: [{ threads: [{ ...thread({ id: 't9', title: 'Old launch' }), member_count: 2 }], next_cursor: null }],
      pageParams: [null],
    });
    show(<Browse onClose={() => {}} />, '/threads');
    const dialog = await screen.findByRole('dialog', { name: 'Browse threads' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archived' }));
    expect(await within(dialog).findByText('Old launch')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move back' }));
    await waitFor(() => expect(within(dialog).queryByText('Old launch')).toBeNull());
    await waitFor(() => expect(all('PATCH', '/api/v2/threads/t9').at(-1)?.body).toEqual({ archived: false }));
  });
});

describe('Learned @s213', () => {
  beforeEach(() => serve(() => ok()));

  it('says so when nothing is learned yet', async () => {
    queryClient.setQueryData(learnedQuery.queryKey, { personal: [], company: null });
    queryClient.setQueryData(viewersQuery.queryKey, { bots: [], include_learned: false });
    show(<LearnedPage />, '/learned');
    expect(await screen.findByRole('heading', { name: 'Nothing learned yet' })).toBeTruthy();
  });

  it('lists lines by section, finds them by words, and asks before deleting one', async () => {
    queryClient.setQueryData(learnedQuery.queryKey, {
      personal: [
        { id: 'e1', layer: 'personal', text: 'Wants one message each morning.', created_at: ago(60), taught_in_chat: true },
        { id: 'e2', layer: 'personal', text: 'Prefers a table to a paragraph.', created_at: ago(90), taught_in_chat: false },
      ],
      company: null,
    });
    queryClient.setQueryData(viewersQuery.queryKey, { bots: [{ bot: 'qa', slug: 'qa', name: 'QA' }], include_learned: false });
    show(<LearnedPage />, '/learned');
    expect(await screen.findByText('Wants one message each morning.')).toBeTruthy();
    expect(screen.getByText('QA', { selector: '.lr-text' })).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'table' } });
    await waitFor(() => expect(screen.queryByText('Wants one message each morning.')).toBeNull());
    fireEvent.click(within(screen.getByText('Prefers a table to a paragraph.').closest('.lr-row')!).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    expect(await screen.findByRole('alertdialog', { name: 'Delete this line?' })).toBeTruthy();
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it("names the company's section after the company, and adds a reader from the head's ⋯", async () => {
    queryClient.setQueryData(configQuery.queryKey, { company_name: 'Fernhill', app_name: 'Tico' } as never);
    queryClient.setQueryData(learnedQuery.queryKey, {
      personal: [],
      company: { entries: [{ id: 'c1', layer: 'company', text: 'Ships on Tuesdays.', created_at: ago(60) }] },
    });
    queryClient.setQueryData(viewersQuery.queryKey, { bots: [], include_learned: false });
    show(<LearnedPage />, '/learned');
    expect(await screen.findByText('Fernhill')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'More' })[0]!);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add an agent' }));
    expect(await screen.findByRole('dialog', { name: 'Add an agent' })).toBeTruthy();
  });
});
