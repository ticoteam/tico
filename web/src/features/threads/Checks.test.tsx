import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { timelineQuery } from '@/conversation';
import { queryClient } from '@/data';
import { ChatSheet } from './ChatSheet';
import { LearnedPage } from './LearnedPage';
import { MailPage, MessagingPage } from './MailPage';
import type { Thread } from './model';
import { learnedQuery, mailboxesQuery, threadKeys, viewersQuery } from './queries';
import { ThreadsPage } from './ThreadsPage';
import { UpdatesPage } from './UpdatesPage';

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
const ok = (body: unknown): Reply => ({ status: 200, body });
/** `reply` answers what the test is about; anything else (calls, feedback, ...) gets an empty list. */
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const r =
      reply(String(url), method) ??
      (/\/threads\/[^/]+\/calls/.test(String(url))
        ? ok({ available: true, calls: [], next_scheduled: null, next_before: null, providers: ['tico'] })
        : ok({ items: [], counts: [] }));
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const sent = (method: string, path: string) => calls.find((c) => c.method === method && c.url.split('?')[0]!.endsWith(path));

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
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
const msg = (id: string, sender: string, body: string, conv = 't1') => ({
  id,
  seq: Number(id.slice(1)),
  thread_id: conv,
  conversation_id: conv,
  sender_id: sender,
  body,
  created_at: ago(1),
});
function seedThread(t: Thread, messages: ReturnType<typeof msg>[]) {
  queryClient.setQueryData(threadKeys.view(t.id), t);
  queryClient.setQueryData(timelineQuery(t.id).queryKey, { pages: [{ messages, next_cursor: null, read_marks: {} }], pageParams: [null] } as never);
}

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
    { id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' },
    { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  queryClient.setQueryData(threadKeys.list, { pages: [{ rows: [thread()], next: null }], pageParams: [null] });
  queryClient.setQueryData(threadKeys.catchUp, { count: 0, items: [] });
});
afterEach(() => vi.unstubAllGlobals());

describe('A thread @s157', () => {
  it('shows its messages under its name and sends from the box', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/t1/messages') ? ok(msg('m3', 'human:me', 'On it')) : undefined));
    seedThread(thread(), [msg('m1', 'bot:qa', 'The build is ready for review.'), msg('m2', 'human:ana', 'Looking now.')]);
    show(<ThreadsPage pane="thread" thread="t1" open />, '/threads/t1');
    expect(await screen.findByText('The build is ready for review.')).toBeTruthy();
    expect(screen.getByText('Looking now.')).toBeTruthy();
    const box = screen.getByRole('textbox', { name: 'Message Launch plan' });
    fireEvent.change(box, { target: { value: 'On it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent('POST', '/api/v2/threads/t1/messages')?.body).toMatchObject({ text: 'On it' }));
  });

  it('has no box where the person may only read', async () => {
    serve();
    seedThread(thread({ me: { can_manage: false, can_post: false } }), [msg('m1', 'bot:qa', 'Closed for now.')]);
    show(<ThreadsPage pane="thread" thread="t1" open />, '/threads/t1');
    expect(await screen.findByText('Closed for now.')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Message Launch plan' })).toBeNull();
  });
});

describe('A chat with an agent @s605', () => {
  it('is the DM named for the agent, with a box to write to it', async () => {
    serve();
    const dm = thread({ id: 't4', title: '', audience: 'personal', root_id: 't4', members: { members: [{ actor: 'human:me' }, { actor: 'bot:qa' }] } });
    queryClient.setQueryData(threadKeys.list, { pages: [{ rows: [dm], next: null }], pageParams: [null] });
    seedThread(dm, [msg('m1', 'bot:qa', 'Morning. Two tests failed overnight.', 't4')]);
    show(<ThreadsPage pane="thread" thread="t4" open />, '/threads/t4');
    expect(await screen.findByText('Morning. Two tests failed overnight.')).toBeTruthy();
    expect(screen.getAllByTitle('QA').find((el) => el.tagName === 'A')?.getAttribute('href')).toBe('/threads/t4');
    expect(screen.getByRole('textbox', { name: 'Message QA' })).toBeTruthy();
  });
});

describe('New message @s158', () => {
  it('picks a teammate, sends the first message and lands in the new thread', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/dm') ? ok(thread({ id: 't5', title: '' })) : undefined));
    const router = show(<ThreadsPage pane="new" open />, '/threads/new');
    fireEvent.change(await screen.findByRole('textbox', { name: 'To' }), { target: { value: 'ana' } });
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Suggestions' })).getByRole('option', { name: /Ana Ruiz/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Ana Ruiz' }), { target: { value: 'Lunch at noon?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/t5'));
    expect(sent('POST', '/api/v2/threads/dm')?.body).toEqual({ members: ['human:ana'], body: 'Lunch at noon?' });
  });

  it('keeps the words in the box when a rule holds the message', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/threads/dm') ? ok({ held: true }) : undefined));
    const router = show(<ThreadsPage pane="new" open />, '/threads/new');
    fireEvent.change(await screen.findByRole('textbox', { name: 'To' }), { target: { value: 'ana' } });
    fireEvent.click(screen.getByRole('option', { name: /Ana Ruiz/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Ana Ruiz' }), { target: { value: 'Lunch at noon?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent('POST', '/api/v2/threads/dm')).toBeTruthy());
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Message Ana Ruiz' }) as HTMLTextAreaElement).value).toBe('Lunch at noon?'));
    expect(router.state.location.pathname).toBe('/threads/new');
  });

  it('offers a named thread that matches the words', async () => {
    serve();
    show(<ThreadsPage pane="new" open />, '/threads/new');
    fireEvent.change(await screen.findByRole('textbox', { name: 'To' }), { target: { value: 'launch' } });
    expect(screen.getByRole('option', { name: /Launch plan/ }).getAttribute('href')).toBe('/threads/t1');
  });
});

describe('Catch up @s179', () => {
  it('shows what is new, thread by thread, and Mark read reads every one', async () => {
    serve();
    queryClient.setQueryData(threadKeys.catchUp, {
      count: 2,
      items: [{ thread: thread(), messages: [msg('m1', 'bot:qa', 'Ready for review.'), msg('m2', 'human:ana', 'Looking now.')], new_count: 2 }],
    });
    show(<ThreadsPage pane="catch-up" open />, '/threads/catch-up');
    const group = await screen.findByRole('region', { name: 'Launch plan' });
    expect(within(group).getByText('Ready for review.')).toBeTruthy();
    expect(within(group).getByRole('link', { name: /Launch plan/ }).getAttribute('href')).toBe('/threads/t1');
    fireEvent.click(screen.getByRole('button', { name: 'Mark read' }));
    await waitFor(() => expect(sent('POST', '/api/v2/threads/t1/read-all')).toBeTruthy());
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
  });
});

const letter = (extra: Record<string, unknown> = {}) => ({
  msg_id: 'l1',
  thread_id: 'th1',
  mailbox: 'help@example.test',
  epoch: Math.floor(Date.now() / 1000) - 600,
  from_header: 'Sam Lee <sam@example.test>',
  from_addr: 'sam@example.test',
  to: ['help@example.test'],
  subject: 'Invoice question',
  snippet: 'Hi, a question',
  ...extra,
});

describe('Mail @s159', () => {
  it('lists a row per conversation and reads the open one in full', async () => {
    serve((url) =>
      url.includes('/mail/messages')
        ? ok({ messages: [letter()] })
        : url.includes('/mail/threads/th1')
          ? ok({ messages: [letter({ body: 'Hi, is the March invoice paid?' })] })
          : undefined,
    );
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
    show(<MailPage box="help@example.test" open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    const row = await screen.findByTitle('Sam Lee: Invoice question');
    expect(row.getAttribute('href')).toBe('/threads/mail?box=help%40example.test&open=th1');
    expect(await screen.findByText('Hi, is the March invoice paid?')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Invoice question' })).toBeTruthy();
  });
});

describe('Mailboxes @s1203', () => {
  it('offers a pick between mailboxes, even from an empty one, and picking one reads it', async () => {
    serve((url) => (url.includes('/mail/messages') ? ok({ messages: [] }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }, { address: 'sales@example.test' }]);
    const router = show(<MailPage />, '/threads/mail');
    expect(await screen.findByRole('combobox', { name: 'Mailbox' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'No mail' })).toBeTruthy();
    expect((await run('mail.pickBox', { box: 'sales@example.test' }, { via: 'click' })).ok).toBe(true);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ box: 'sales@example.test' }));
  });
});

describe('Messaging @s160', () => {
  it('lists the texts the company received and reads the open one', async () => {
    serve((url) =>
      url.includes('/inbound/messages/x1')
        ? ok({ text: 'Is Friday fine for the visit?', message_id: 'x1' })
        : url.includes('/inbound/messages')
          ? ok({ messages: [{ message_id: 'x1', received_at: ago(4), from: '+1 555 0100', preview: 'Is Friday fine' }], next: null })
          : undefined,
    );
    show(<MessagingPage open="x1" />, '/threads/messaging?open=x1');
    expect((await screen.findByTitle('+1 555 0100: Is Friday fine')).getAttribute('href')).toBe('/threads/messaging?open=x1');
    expect(await screen.findByText('Is Friday fine for the visit?')).toBeTruthy();
  });
});

const update = (id: string, kind: string, day: string, body: string, extra: Record<string, unknown> = {}) => ({
  id,
  bot: 'ops',
  kind,
  day,
  body,
  read: true,
  created_at: `${day}T15:00:00.000Z`,
  ...extra,
});

describe('Updates tabs @s984 @s985', () => {
  it('Daily and Weekly read only their kind', async () => {
    serve((url) => (url.includes('/api/v2/updates') ? ok({ updates: [update('u1', 'daily', '2026-01-02', 'Shipped the page')], unread: 0, next_before: null }) : undefined));
    show(<UpdatesPage tab="daily" />, '/updates/daily');
    expect(await screen.findByTitle('Shipped the page')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('/api/v2/updates?kind=daily'))).toBe(true);
    expect(screen.getByRole('link', { name: 'Daily' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Weekly' }).getAttribute('href')).toBe('/updates/weekly');
  });

  it('Weekly asks for weekly ones', async () => {
    serve((url) => (url.includes('/api/v2/updates') ? ok({ updates: [update('u2', 'weekly', '2026-01-02', 'A good week')], unread: 0, next_before: null }) : undefined));
    show(<UpdatesPage tab="weekly" />, '/updates/weekly');
    expect((await screen.findByTitle('A good week')).getAttribute('href')).toBe('/updates/weekly?open=u2');
    expect(calls.some((c) => c.url.includes('/api/v2/updates?kind=weekly'))).toBe(true);
  });
});

describe('Updates, one feed @s986 @s987', () => {
  it('puts every kind in one feed, a section a day, newest day first', async () => {
    serve((url) =>
      url.includes('/api/v2/updates')
        ? ok({
            updates: [
              update('u1', 'daily', '2026-01-01', 'Fixed the login bug'),
              update('u2', 'weekly', '2026-01-02', 'A good week', { created_at: '2026-01-02T18:00:00.000Z' }),
              update('u3', 'daily', '2026-01-02', 'Shipped the page'),
            ],
            unread: 0,
            next_before: null,
          })
        : undefined,
    );
    show(<UpdatesPage />, '/updates');
    const feed = await screen.findByRole('feed', { name: 'Updates' });
    await within(feed).findByTitle('A good week');
    const order = [...feed.querySelectorAll('[title]')].map((el) => el.getAttribute('title'));
    expect(order.filter((t) => t !== null && ['A good week', 'Shipped the page', 'Fixed the login bug'].includes(t))).toEqual(['A good week', 'Shipped the page', 'Fixed the login bug']);
    expect(within(feed).getByText('Fri, Jan 2')).toBeTruthy();
    expect(within(feed).getByText('Thu, Jan 1')).toBeTruthy();
  });
});

describe('An update, open @s992', () => {
  it('reads in full, is marked read on opening, and its writer is a press away', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Shipped the page\n\nThe launch page is live.', { read: false });
    serve((url) =>
      url.includes('/api/v2/updates/u1') ? ok({ update: u }) : url.includes('/api/v2/updates?') ? ok({ updates: [u], unread: 1, next_before: null }) : undefined,
    );
    show(<UpdatesPage open="u1" />, '/updates?open=u1');
    expect(await screen.findByText('The launch page is live.')).toBeTruthy();
    await waitFor(() => expect(sent('POST', '/api/v2/updates/read')?.body).toMatchObject({ ids: ['u1'] }));
    expect(screen.getByRole('link', { name: 'Chat with Ops' }).getAttribute('href')).toContain('chat=agent%3Aops');
  });
});

describe('Learned: what readers see @s213', () => {
  it('turns on that agents reading the chat also read what Tico learned', async () => {
    serve((url, method) => (method === 'PUT' && url.endsWith('/viewers/learned') ? ok({ bots: [{ bot: 'qa', slug: 'qa', name: 'QA' }], include_learned: true }) : undefined));
    queryClient.setQueryData(learnedQuery.queryKey, { personal: [], company: null });
    queryClient.setQueryData(viewersQuery.queryKey, { bots: [{ bot: 'qa', slug: 'qa', name: 'QA' }], include_learned: false });
    show(<LearnedPage />, '/learned');
    fireEvent.click(await screen.findByRole('switch', { name: 'Include what Tico learned' }));
    await waitFor(() => expect(sent('PUT', '/api/v2/assistant/viewers/learned')?.body).toEqual({ include_learned: true }));
  });
});
