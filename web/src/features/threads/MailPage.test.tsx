import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, defaultParseSearch, defaultStringifySearch, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { MailPage, MessagingPage } from './MailPage';
import { mailboxesQuery } from './queries';

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

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
});
afterEach(() => vi.unstubAllGlobals());

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

  it('the box talks only to the mailbox\'s agent, about this mail, never to whoever wrote', async () => {
    serve((url, method) =>
      url.includes('/mail/messages')
        ? ok({ messages: [letter()] })
        : url.includes('/mail/threads/th1')
          ? ok({ messages: [letter({ body: 'Hi, is the March invoice paid?' })] })
          : method === 'POST' && url.endsWith('/threads/dm/messages')
            ? ok({ id: 'm9', seq: 9, thread_id: 'd9', conversation_id: 'd9', sender_id: 'human:me', body: 'Is it paid?', created_at: ago(0) })
            : undefined,
    );
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test', agent_bot: 'qa' }]);
    const router = show(<MailPage box="help@example.test" open="th1" />, '/threads/mail?box=help%40example.test&open=th1');
    expect(screen.queryByRole('textbox', { name: /Reply to/ })).toBeNull();
    const box = await screen.findByRole('textbox', { name: 'Message QA' });
    fireEvent.change(box, { target: { value: 'Is it paid?' } });
    await waitFor(() => expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    // to the person's DM with exactly the mailbox's agent, which the server finds as it writes (#4882), with this mail
    // as a pointer, no words of it
    await waitFor(() => expect(sent('POST', '/api/v2/threads/dm/messages')).toBeTruthy());
    expect(sent('POST', '/api/v2/threads/dm/messages')?.body).toEqual({
      text: 'Is it paid?',
      to_dm: 'bot:qa',
      refs: { from_page: { page: 'mail', label: 'Mail', item: { kind: 'mail', id: 'th1' } } },
    });
    expect(all('POST', '/api/v2/email/sends')).toEqual([]);
    expect(all('POST', '/api/v2/mail/drafts')).toEqual([]);
    // the thread it went to opens beside, as the server answered it
    await waitFor(() => expect(new URLSearchParams(router.state.location.searchStr).get('chat')).toBe('thread:d9'));
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

describe('Mail: Instructions @s168', () => {
  it('shows a row for them only when the mailbox has them, and reads them in the item pane', async () => {
    serve((url) => (url.includes('/mail/agent') ? ok({ mailbox: 'help@example.test', bot: 'support', instructions: 'Answer **within a day**.' }) : ok({ messages: [] })));
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test', agent_instructions: true }]);
    show(<MailPage box="help@example.test" view="instructions" />, '/threads/mail?box=help%40example.test&view=instructions');
    // The reading column draws while the list still loads: wait for the row.
    await waitFor(() =>
      expect(screen.getAllByTitle('Instructions').find((el) => el.tagName === 'A')?.getAttribute('href')).toBe('/threads/mail?box=help%40example.test&view=instructions'),
    );
    expect(await screen.findByText('within a day')).toBeTruthy();
  });

  it('offers no Instructions row for a mailbox without them, and opening them asks the server for that mailbox', async () => {
    serve((url) => (url.includes('/mail/agent') ? ok({ mailbox: 'b@example.test', bot: 'support', instructions: 'Reply the same day.' }) : ok({ messages: [] })));
    queryClient.setQueryData(mailboxesQuery.queryKey, [
      { address: 'a@example.test', agent_instructions: null },
      { address: 'b@example.test', agent_instructions: true },
    ]);
    show(<MailPage box="a@example.test" />, '/threads/mail?box=a%40example.test');
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryAllByTitle('Instructions').filter((el) => el.tagName === 'A')).toHaveLength(0);
    expect(all('GET', '/api/v2/mail/agent')).toHaveLength(0);
    expect((await run('mail.openInstructions', { box: 'b@example.test' }, { via: 'tico' })).ok).toBe(true);
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
  it('asks the server for the words searched, and marks an attachment with an icon, not pills', async () => {
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

  it('searches through the address, and says nothing matches when a narrowing finds nothing', async () => {
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
