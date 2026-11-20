import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { Conversation, timelineQuery } from '@/conversation';
import { queryClient } from '@/data';
import { Browse } from './Browse';
import { MailPage } from './MailPage';
import type { Thread } from './model';
import { archivedQuery, mailboxesQuery, threadKeys } from './queries';
import { ThreadHead } from './ThreadHead';
import { UpdatesPage } from './UpdatesPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
});

type Reply = { status: number; body: unknown };
type Call = { url: string; method: string; body: Record<string, unknown> | undefined };
const calls: Call[] = [];
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
/** `reply` answers what the test is about; anything else gets an empty list. */
function serve(reply: (url: string, method: string, body: Call['body']) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Call['body']) : undefined;
    calls.push({ url: String(url), method, body });
    const r =
      reply(String(url), method, body) ??
      (/\/threads\/[^/]+\/calls/.test(String(url))
        ? ok({ available: true, calls: [], next_scheduled: null, next_before: null, providers: ['tico'] })
        : ok({ items: [], counts: [] }));
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0]!.endsWith(path));

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
  members: { members: [{ actor: 'human:me' }, { actor: 'bot:qa' }] },
  ...extra,
});
const msg = (id: string, sender: string, body: string) => ({
  id,
  seq: Number(id.slice(1)),
  thread_id: 't1',
  conversation_id: 't1',
  sender_id: sender,
  body,
  created_at: ago(30 - Number(id.slice(1))),
});
function seedThread(messages: ReturnType<typeof msg>[], next: string | null = null) {
  queryClient.setQueryData(threadKeys.view('t1'), thread());
  queryClient.setQueryData(timelineQuery('t1').queryKey, { pages: [{ messages, next_cursor: next, read_marks: {} }], pageParams: [null] } as never);
}
beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
    { id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
});
afterEach(() => vi.unstubAllGlobals());

describe('Mail: Instructions tab @s168', () => {
  it('offers no Instructions row for a mailbox without them, and opening them asks the server for that mailbox', async () => {
    serve((url) => (url.includes('/mail/agent') ? ok({ mailbox: 'b@example.test', bot: 'support', instructions: 'Reply the same day.' }) : ok({ messages: [] })));
    queryClient.setQueryData(mailboxesQuery.queryKey, [
      { address: 'a@example.test', agent_instructions: null },
      { address: 'b@example.test', agent_instructions: true },
    ]);
    show(<MailPage box="a@example.test" />, '/threads/mail?box=a%40example.test');
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryAllByTitle('Instructions').filter((el) => el.tagName === 'A')).toHaveLength(0);
    expect(sent('GET', '/api/v2/mail/agent')).toHaveLength(0);

    const r = await run('mail.openInstructions', { box: 'b@example.test' }, { via: 'tico' });
    expect(r.ok).toBe(true);
  });
});

describe('Browse threads @s176', () => {
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
    await waitFor(() => expect(sent('PATCH', '/api/v2/threads/t9').at(-1)?.body).toEqual({ archived: false }));
  });
});

describe('Thread settings @s178', () => {
  it('saves Pin for everyone from the sheet; Tico may change how it notifies but not open it to everyone', async () => {
    serve();
    queryClient.setQueryData(threadKeys.view('t1'), thread());
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Pin for everyone' }));
    await waitFor(() => expect(sent('PATCH', '/api/v2/threads/t1').at(-1)?.body).toEqual({ pinned_for_everyone: true }));

    expect((await run('threads.notify', { thread: 't1', notify: 'mentions' }, { via: 'tico' })).ok).toBe(true);
    expect(sent('PUT', '/api/v2/threads/t1/follow').at(-1)?.body).toEqual({ notify: 'mentions' });

    const before = sent('PATCH', '/api/v2/threads/t1').length;
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'company' }, { via: 'tico' })).ok).toBe(false);
    expect((await run('threads.delete', { thread: 't1' }, { via: 'tico' })).ok).toBe(false);
    expect(sent('PATCH', '/api/v2/threads/t1')).toHaveLength(before);
    expect(sent('DELETE', '/api/v2/threads/t1')).toHaveLength(0);
  });
});

describe('Chat outline @s219', () => {
  it("lists the chat's prompts from the server; a row closes it and pages back to reach that message", async () => {
    serve((url) => {
      if (url.includes('/outline')) return ok({ prompts: [{ id: 'm1', created_at: ago(29), text: 'First ask' }] });
      if (url.includes('/timeline') && url.includes('cursor=c1')) return ok({ messages: [msg('m1', 'human:me', 'First ask')], next_cursor: null, read_marks: {} });
      return undefined;
    });
    seedThread([msg('m5', 'human:me', 'Later ask'), msg('m6', 'bot:qa', 'Later answer')], 'c1');
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

describe('Updates: Archive @s991', () => {
  it('opens from ⋯ on Updates, and an archived update moves back from its own ⋯', async () => {
    const up = { id: 'u1', bot: 'qa', kind: 'daily', day: '2026-01-02', body: 'Old plan', read: true, archived: true, created_at: ago(5) };
    serve((url) => {
      if (url.includes('/api/v2/updates/u1')) return ok({ update: up });
      if (url.includes('/api/v2/updates')) return ok({ updates: url.includes('archive=true') ? [up] : [], unread: 0, next_before: null });
      return undefined;
    });
    const router = show(<UpdatesPage />, '/updates');
    expect((await run('updates.openArchive', {}, { via: 'click' })).ok).toBe(true);
    expect(router.state.location.pathname).toBe('/updates/archive');
  });

  it('moves an open archived update back to Updates', async () => {
    const up = { id: 'u1', bot: 'qa', kind: 'daily', day: '2026-01-02', body: 'Old plan', read: true, archived: true, created_at: ago(5) };
    serve((url) => {
      if (url.includes('/api/v2/updates/u1')) return ok({ update: up });
      if (url.includes('/api/v2/updates/archive')) return ok({});
      return ok({ updates: [up], unread: 0, next_before: null });
    });
    show(<UpdatesPage tab="archive" open="u1" />, '/updates/archive?open=u1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move back to Updates' }));
    await waitFor(() => expect(sent('POST', '/api/v2/updates/archive').at(-1)?.body).toEqual({ ids: ['u1'], archived: false }));
  });
});
