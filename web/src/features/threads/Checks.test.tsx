import { InfiniteQueryObserver, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, defaultParseSearch, defaultStringifySearch, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { timelineQuery } from '@/conversation';
import { configQuery, queryClient } from '@/data';
import { groupsQuery } from '@/features/team/exports';
import { Browse } from './Browse';
import { ChatSheet } from './ChatSheet';
import { LearnedPage } from './LearnedPage';
import { MailPage, MessagingPage } from './MailPage';
import type { Thread } from './model';
import { learnedQuery, mailboxesQuery, threadKeys, updateListQuery, updateQuery, viewersQuery } from './queries';
import { ThreadHead } from './ThreadHead';
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
const all = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0]!.endsWith(path));
const sent = (method: string, path: string) => all(method, path)[0];

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
  it('shows the first mailbox when an old link points to one no longer available', async () => {
    serve((url) => (url.includes('/mail/messages') ? ok({ messages: [letter()] }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
    show(<MailPage box="old@example.test" open="old-thread" />, '/threads/mail?box=old%40example.test&open=old-thread');
    const row = await screen.findByTitle('Sam Lee: Invoice question');
    expect(row.closest('main')?.classList.contains('is-list')).toBe(true);
    expect(all('GET', '/mail/messages').map((c) => new URL(c.url, location.origin).searchParams.get('mailbox'))).toEqual(['help@example.test']);
    expect(all('GET', '/mail/threads/old-thread')).toEqual([]);
  });

  it('offers a pick between mailboxes under the filter, even from an empty one, and picking one reads it', async () => {
    serve((url) => (url.includes('/mail/messages') ? ok({ messages: [] }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }, { address: 'sales@example.test' }]);
    const router = show(<MailPage />, '/threads/mail');
    const filter = await screen.findByRole('button', { name: 'Mailbox' });
    expect(filter.hasAttribute('data-phone')).toBe(true);
    fireEvent.click(filter);
    expect(await screen.findByRole('menuitem', { name: 'sales@example.test' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'No mail' })).toBeTruthy();
    expect((await run('mail.pickBox', { box: 'sales@example.test' }, { via: 'click' })).ok).toBe(true);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ box: 'sales@example.test' }));
  });

  it('with no mailbox yet, says so and offers Connect mail @s159', async () => {
    serve();
    queryClient.setQueryData(mailboxesQuery.queryKey, []);
    show(<MailPage />, '/threads/mail');
    expect(await screen.findByRole('heading', { name: 'No mail yet' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect mail' })).toBeTruthy();
  });

  it('a mailbox nothing can read yet says so and offers Connect mail, asking for no mail @s159', async () => {
    serve();
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test', connection: 'none' }]);
    show(<MailPage />, '/threads/mail');
    expect(await screen.findByRole('heading', { name: 'Mail is not connected' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect mail' })).toBeTruthy();
    expect(all('GET', '/mail/messages')).toEqual([]);
  });

  it('a not_connected refusal from the server is the same state, not a fault @s159', async () => {
    serve((url) => (url.includes('/mail/messages') ? { status: 403, body: { error: { code: 'not_connected', detail: 'No grant' } } } : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
    show(<MailPage />, '/threads/mail');
    expect(await screen.findByRole('heading', { name: 'Mail is not connected' })).toBeTruthy();
    expect(screen.queryByText('Threads did not load')).toBeNull();
  });
});

const box = { address: 'help@example.test', agent_bot: 'qa', may: { send: true, organize: true } };
const openMail = (extra: Record<string, unknown> = {}) =>
  serve((url, method) =>
    method === 'POST' && url.endsWith('/mail/drafts')
      ? ok({ draft: 'd1', thread: 'th1' })
      : method === 'POST'
        ? ok({})
        : url.includes('/mail/threads/th1')
          ? ok({ messages: [letter({ body: 'Can I change the size?', attachments: [{ name: 'corner.jpg', size: 2_200_000 }], ...extra })] })
          : url.includes('/mail/messages')
            ? ok({ messages: [letter()] })
            : undefined,
  );

describe('An open mail @s159', () => {
  it('keeps an undated letter readable in the list and in the open thread', async () => {
    const undated = letter({ epoch: 0, date: '', body: 'Can I change the size?' });
    serve((url) => (url.includes('/mail/messages') || url.includes('/mail/threads/') ? ok({ messages: [undated] }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [box]);
    show(<MailPage box={box.address} open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    expect(await screen.findByTitle('Sam Lee: Invoice question')).toBeTruthy();
    expect(await screen.findByText('Can I change the size?')).toBeTruthy();
  });

  it('heads with the sender, their address, the agent and Archive; letters show the full date and their files', async () => {
    openMail({ epoch: Date.UTC(2026, 10, 18, 17, 21) / 1000 });
    queryClient.setQueryData(mailboxesQuery.queryKey, [box]);
    show(<MailPage box="help@example.test" open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    expect(await screen.findByText('Can I change the size?')).toBeTruthy();
    expect(screen.getByText('sam@example.test', { selector: '.lt-addr' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Chat with QA' }).getAttribute('href')).toContain('chat=agent%3Aqa');
    expect(screen.getByText(/^Wed, Nov 18, \d+:21 [AP]M$/)).toBeTruthy();
    expect(screen.getByText('corner.jpg')).toBeTruthy();
    expect(screen.getByText('2.1 MB')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(sent('POST', '/api/v2/mail/messages/organize')?.body).toEqual({ mailbox: 'help@example.test', action: 'archive', ids: ['l1'] }));
  });

  it('replies from the box: drafted in the thread, then sent through the send gate', async () => {
    openMail();
    queryClient.setQueryData(mailboxesQuery.queryKey, [box]);
    show(<MailPage box="help@example.test" open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    const reply = await screen.findByRole('textbox', { name: 'Reply to Sam Lee' });
    expect(screen.queryByRole('button', { name: 'Attach a file' })).toBeNull();
    fireEvent.change(reply, { target: { value: 'Yes, done.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent('POST', '/api/v2/email/sends')?.body).toEqual({ mailbox: 'help@example.test', draft_id: 'd1', thread_id: 'th1' }));
    expect(sent('POST', '/api/v2/mail/drafts')?.body).toEqual({ mailbox: 'help@example.test', body: 'Yes, done.', reply_to: 'th1' });
  });

  it('names the sender the mail API replies to in a thread with multiple senders', async () => {
    serve((url) =>
      url.includes('/mail/threads/th1')
        ? ok({
            messages: [
              letter(),
              letter({ msg_id: 'l2', from_addr: 'mara@example.test', from_header: 'Mara <mara@example.test>' }),
              letter({ msg_id: 'l3', from_addr: 'Help@example.test', from_header: 'Help <Help@example.test>' }),
            ],
          })
        : undefined,
    );
    queryClient.setQueryData(mailboxesQuery.queryKey, [box]);
    show(<MailPage box={box.address} open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    expect(await screen.findByRole('textbox', { name: 'Reply to Mara' })).toBeTruthy();
  });

  it('returns to the selected mailbox and search when closing a letter from its back link', async () => {
    openMail();
    const selected = 'sales@example.test';
    const words = '1203';
    const reading = { box: selected, q: words, open: 'th1' };
    queryClient.setQueryData(mailboxesQuery.queryKey, [box, { address: selected }]);
    const router = show(<MailPage box={selected} open="th1" q={words} />, `/threads/mail${defaultStringifySearch(reading)}`);
    const letterLink = (await screen.findByTitle('Sam Lee: Invoice question')).getAttribute('href')!;
    expect(defaultParseSearch(new URL(letterLink, location.origin).search)).toEqual(reading);
    fireEvent.click(await screen.findByRole('link', { name: 'Back to Mail' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ box: selected, q: words }));
  });

  it('has no reply box or Archive where the person may only read', async () => {
    openMail();
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ ...box, may: { send: false, organize: false } }]);
    show(<MailPage box="help@example.test" open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    expect(await screen.findByText('Can I change the size?')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: /Reply to/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Archive' })).toBeNull();
  });
});

describe('Mail: search @s159', () => {
  const found = [letter({ labels: ['INBOX', 'UNREAD', 'IMPORTANT'], has_attachment: true })];
  it('asks the server for the words searched, and marks an attachment with an icon, not pills @s159', async () => {
    serve((url) => (url.includes('/mail/messages') ? ok({ messages: found, next_cursor: null }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
    show(<MailPage box="help@example.test" q="invoice" />, '/threads/mail?box=help%40example.test&q=invoice');
    const row = await screen.findByTitle('Sam Lee: Invoice question');
    const asked = new URL(calls.find((c) => c.url.includes('/mail/messages'))!.url, 'http://x');
    expect(asked.searchParams.get('q')).toBe('invoice');
    expect(within(row).getByRole('img', { name: 'Attachment' })).toBeTruthy();
    expect(within(row).queryByText('Important')).toBeNull();
    // An opened mail keeps the words it was found by.
    expect(row.getAttribute('href')).toBe('/threads/mail?box=help%40example.test&q=invoice&open=th1');
    expect((screen.getByRole('textbox', { name: 'Find mail' }) as HTMLInputElement).value).toBe('invoice');
  });

  it('searches through the address, and says nothing matches when a narrowing finds nothing @s159', async () => {
    serve((url) => (url.includes('/mail/messages') ? ok({ messages: url.includes('q=nothing') ? [] : found, next_cursor: null }) : undefined));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
    const router = show(<MailPage box="help@example.test" q="nothing" />, '/threads/mail?box=help%40example.test&q=nothing');
    expect(await screen.findByRole('heading', { name: 'No matches' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Find mail' })).toBeTruthy();
    expect((await run('mail.find', { box: 'help@example.test', q: 'refund' }, { via: 'tico' })).ok).toBe(true);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ box: 'help@example.test', q: 'refund' }));
  });
});

describe('Messaging @s160', () => {
  it('reads every part of a text when the server pages its body', async () => {
    const contact = '+15550199';
    serve((url) => {
      const path = new URL(url, location.origin);
      if (path.pathname.endsWith('/inbound/messages/x1')) {
        return path.searchParams.get('offset') === '13'
          ? ok({ text: 'Second part.', next_offset: null })
          : ok({ text: 'First part.  ', next_offset: 13 });
      }
      if (path.pathname.endsWith('/inbound/messages')) {
        return ok({ messages: [{ message_id: 'x1', from: contact, received_at: ago(1), preview: 'First part.' }] });
      }
    });
    show(<MessagingPage open={contact} />, '/threads/messaging?open=%2B15550199');
    expect(await screen.findByText('First part. Second part.')).toBeTruthy();
    expect(all('GET', '/inbound/messages/x1')).toHaveLength(2);
  });

  it('finds a contact outside the loaded page', async () => {
    const older = '+15550199';
    const recent = Array.from({ length: 50 }, (_, i) => ({
      message_id: `recent-${i}`,
      from: `+155502${i.toString().padStart(2, '0')}`,
      received_at: ago(i),
    }));
    serve((url) => {
      if (!url.includes('/api/v2/inbound/messages?')) return;
      return new URL(url, location.origin).searchParams.get('from') === '0199'
        ? ok({ messages: [{ message_id: 'older', from: older, received_at: ago(60) }], next: null })
        : ok({ messages: recent, next: 50 });
    });
    show(<MessagingPage />, '/threads/messaging');
    expect(await screen.findByTitle(recent[0]!.from)).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox', { name: 'Find a conversation' }), { target: { value: '0199' } });
    expect(await screen.findByTitle(older)).toBeTruthy();
  });

  it('lists one row per contact and reads their texts in full, a break at each day', async () => {
    const text = (id: string, from: string, min: number, preview: string) => ({ message_id: id, received_at: ago(min), from, preview });
    serve((url) =>
      url.includes('/inbound/messages/x1')
        ? ok({ text: 'Is Friday fine for the visit?', message_id: 'x1' })
        : url.includes('/inbound/messages/x2')
          ? ok({ text: 'Also, bring the long ladder.', message_id: 'x2' })
          : url.includes('/inbound/messages')
            ? ok({ messages: [text('x2', '+1 555 0100', 4, 'Also'), text('x3', '+1 555 0199', 6, 'Hello'), text('x1', '+1 555 0100', 3000, 'Is Friday')], next: null })
            : undefined,
    );
    show(<MessagingPage open="+1 555 0100" />, '/threads/messaging?open=%2B1+555+0100');
    expect((await screen.findByTitle('+1 555 0199')).getAttribute('href')).toBe('/threads/messaging?open=%2B1+555+0199');
    expect(screen.getAllByTitle('+1 555 0100').find((el) => el.tagName === 'A')).toBeTruthy();
    expect(await screen.findByText('Is Friday fine for the visit?')).toBeTruthy();
    expect(await screen.findByText('Also, bring the long ladder.')).toBeTruthy();
    expect(screen.getByText('Today')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('from=%2B1+555+0100'))).toBe(true);
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
  it('finishes loading the page when the menu stops watching the same feed', async () => {
    const page = { updates: [], unread: 0, next_before: null };
    let answer!: (response: Response) => void;
    vi.stubGlobal('fetch', () => new Promise<Response>((resolve) => (answer = resolve)));
    const options = updateListQuery('all');
    const menu = new InfiniteQueryObserver(queryClient, options);
    const stop = menu.subscribe(() => {});
    const loading = queryClient.ensureInfiniteQueryData(options);
    stop();
    answer(new Response(JSON.stringify(page), { headers: { 'Content-Type': 'application/json' } }));
    await expect(loading).resolves.toMatchObject({ pages: [page] });
  });

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
  it('keeps an already-read update unread after choosing Mark unread', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Shipped the page');
    serve((url, method) => {
      if (url.endsWith('/updates/read') && method === 'POST') {
        u.read = (calls.at(-1)?.body as { read: boolean }).read;
        return ok({});
      }
      if (url.endsWith('/updates/u1')) return ok({ update: u });
      if (url.includes('/updates?')) return ok({ updates: [u], unread: u.read ? 0 : 1 });
    });
    show(<UpdatesPage open="u1" />, '/updates?open=u1');
    await screen.findByRole('heading', { name: 'Shipped the page' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Mark unread' }));
    await waitFor(() => expect(queryClient.getQueryData(updateQuery('u1').queryKey)?.read).toBe(false));
    await waitFor(() => expect(queryClient.getQueryState(updateQuery('u1').queryKey)?.fetchStatus).toBe('idle'));
    expect(all('POST', '/updates/read').map((c) => c.body)).toEqual([{ ids: ['u1'], read: false }]);
  });

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

describe('Learned @s213', () => {
  it("names the company's section after the company, and adds a reader from the head's ⋯", async () => {
    serve();
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

describe('Threads head and Catch up @s156 @s179', () => {
  it('counts Catch up on the Threads tab, writes with a pencil, and Mark read is the filled button', async () => {
    serve();
    queryClient.setQueryData(threadKeys.catchUp, { count: 25, items: [{ thread: thread(), messages: [msg('m1', 'bot:qa', 'Ready.')], new_count: 1 }] });
    show(<ThreadsPage pane="catch-up" open />, '/threads/catch-up');
    expect(await screen.findByText('24 more')).toBeTruthy();
    expect(screen.getByRole('link', { name: /^Threads/ }).textContent).toContain('25');
    expect(screen.getByRole('button', { name: 'New message' }).textContent).toBe('');
    expect(screen.getByRole('button', { name: 'Mark read' }).className).toContain('fill');
  });

  it('offers New message when there are no threads yet', async () => {
    serve();
    queryClient.setQueryData(threadKeys.list, { pages: [{ rows: [], next: null }], pageParams: [null] });
    const router = show(<ThreadsPage pane="catch-up" />, '/threads');
    expect(await screen.findByRole('heading', { name: 'No threads yet' })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'New message' }).find((b) => b.textContent === 'New message')!);
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/new'));
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
});

describe('Thread settings @s178', () => {
  beforeEach(() => queryClient.setQueryData(groupsQuery.queryKey, [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: [], people: [] }]));

  it('lets a team be chosen as its reader @s178', async () => {
    serve();
    queryClient.setQueryData(threadKeys.view('t1'), thread());
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Who can read' }));
    const o = await screen.findByRole('option', { name: 'Design' });
    fireEvent.pointerDown(o, { pointerType: 'mouse' });
    fireEvent.click(o);
    await waitFor(() => expect(all('PATCH', '/api/v2/threads/t1').at(-1)?.body).toEqual({ audience: 'team', audience_group_id: 'g1' }));
  });

  it('a team reader needs the team, and Tico may not open it wider @s178', async () => {
    serve();
    show(<span />, '/threads/t1');
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team' }, { via: 'click' })).ok).toBe(false);
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team', team: 'g1' }, { via: 'tico' })).ok).toBe(false);
    expect(all('PATCH', '/api/v2/threads/t1')).toHaveLength(0);
  });
});

describe('Browse threads @s176', () => {
  it('opens a thread from its name, to read it before joining @s176', async () => {
    serve((url) => (url.includes('/threads/browse') ? ok({ threads: [{ ...thread({ id: 't5', title: 'Hiring' }), member_count: 3 }], next_cursor: null }) : undefined));
    const onClose = vi.fn();
    const router = show(<Browse onClose={onClose} />, '/threads');
    const dialog = await screen.findByRole('dialog', { name: 'Browse threads' });
    fireEvent.click(await within(dialog).findByRole('link', { name: 'Hiring' }));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/t5'));
    expect(all('POST', '/api/v2/threads/t5/join')).toHaveLength(0);
  });
});

describe('Updates: search, weeks and an opened update @s972 @s985 @s992', () => {
  it('looks past pages without a match and reports no matches when the feed is exhausted', async () => {
    const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(35);
    const recent = Array.from({ length: 50 }, (_, i) => update(`recent-${i}`, 'daily', '2026-01-07', `Recent note ${i}`));
    serve((url) => {
      if (!url.startsWith('/api/v2/updates?')) return;
      const cursor = new URL(url, location.origin).searchParams.get('before');
      if (cursor === 'second') return ok({ updates: [update('older', 'daily', '2026-01-05', 'Winter inventory')], unread: 0, next_before: null });
      if (cursor === 'first') return ok({ updates: [update('middle', 'daily', '2026-01-06', 'A middle note')], unread: 0, next_before: 'second' });
      return ok({ updates: recent, unread: 0, next_before: 'first' });
    });
    try {
      show(<UpdatesPage />, '/updates');
      expect(await screen.findByRole('feed', { name: 'Updates' })).toBeTruthy();
      expect(all('GET', '/api/v2/updates')).toHaveLength(1);
      const search = screen.getByRole('textbox', { name: 'Find an update' });
      fireEvent.change(search, { target: { value: 'winter' } });
      expect(screen.queryByRole('heading', { name: 'No matches' })).toBeNull();
      expect(await screen.findByTitle('Winter inventory')).toBeTruthy();
      fireEvent.change(search, { target: { value: 'spring' } });
      expect(await screen.findByRole('heading', { name: 'No matches' })).toBeTruthy();
    } finally {
      height.mockRestore();
    }
  });

  it('finds updates by their words, and Weekly reads a section a week', async () => {
    serve((url) =>
      url.includes('/api/v2/updates')
        ? ok({ updates: [update('u1', 'weekly', '2026-01-07', 'Cash is up'), update('u2', 'weekly', '2026-01-05', 'Ads cost less')], unread: 0, next_before: null })
        : undefined,
    );
    show(<UpdatesPage tab="weekly" />, '/updates/weekly');
    const feed = await screen.findByRole('feed', { name: 'Updates' });
    await within(feed).findByTitle('Cash is up');
    expect(within(feed).getByText('Mon, Jan 5')).toBeTruthy();
    expect(within(feed).queryByText('Wed, Jan 7')).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: 'Find an update' }), { target: { value: 'ads' } });
    await waitFor(() => expect(within(feed).queryByTitle('Cash is up')).toBeNull());
    expect(within(feed).getByTitle('Ads cost less')).toBeTruthy();
  });

  it('reads its body as written, headings kept, and dates it in full', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Cash summary\nThe report is ready.\n\n## What changed\n\nRefunds doubled.', { created_at: '2026-01-02T15:00:00.000Z' });
    serve((url) => (url.includes('/api/v2/updates/u1') ? ok({ update: u }) : url.includes('/api/v2/updates?') ? ok({ updates: [u], unread: 0, next_before: null }) : undefined));
    show(<UpdatesPage open="u1" />, '/updates?open=u1');
    expect(await screen.findByRole('heading', { name: 'What changed' })).toBeTruthy();
    expect(screen.getByText('The report is ready.')).toBeTruthy();
    expect(screen.getByText(/^Fri, Jan 2, \d+:00 [AP]M$/)).toBeTruthy();
  });
});
