import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Browse } from './Browse';
import { LearnedPage } from './LearnedPage';
import { MailPage } from './MailPage';
import type { Thread } from './model';
import { browseQuery, learnedQuery, mailboxesQuery, membersQuery, threadKeys, viewersQuery } from './queries';
import { ThreadHead } from './ThreadHead';
import { UpdatesPage } from './UpdatesPage';

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
const calls: { url: string; method: string }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method });
    // The head's call control: no calls in the thread.
    const r = /\/threads\/[^/]+\/calls/.test(String(url))
      ? ok({ available: true, calls: [], next_scheduled: null, next_before: null, providers: ['tico'] })
      : await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown = {}): Reply => ({ status: 200, body });

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
  last_seq: 3,
  last_unread_seq: 0,
  pinned_for_everyone: false,
  root_id: 't1',
  me: { can_manage: true, can_post: true, following: true, notify: 'live' },
  members: { members: [{ actor: 'human:me' }, { actor: 'human:ana' }, { actor: 'bot:qa' }] },
  ...extra,
});

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], {
    actors: [
      { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
      { id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' },
      { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops' },
    ],
    byId: {
      'human:ana': { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
      'bot:qa': { id: 'bot:qa', kind: 'agent', name: 'QA', slug: 'qa' },
      'bot:ops': { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops' },
    },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('People in a thread @s177', () => {
  it('opens from the faces under the title, takes someone out at once and puts them back when refused', async () => {
    let refuse!: () => void;
    serve((_url, method) => (method === 'DELETE' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : ok()));
    queryClient.setQueryData(threadKeys.view('t1'), thread());
    queryClient.setQueryData(membersQuery('t1').queryKey, {
      pages: [
        {
          members: [
            { actor: 'human:me', role: 'owner' },
            { actor: 'human:ana', role: 'member' },
            { actor: 'bot:qa', role: 'member' },
          ],
          next_cursor: null,
        },
      ],
      pageParams: [null],
    });
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'People' }));
    const dialog = await screen.findByRole('dialog', { name: 'People' });
    expect(within(dialog).getByText('Owner')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Remove You' })).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove Ana Ruiz' }));
    await waitFor(() => expect(within(dialog).queryByText('Ana Ruiz')).toBeNull());
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/api/v2/threads/t1/members/human%3Aana'))).toBe(true);
    await act(async () => refuse());
    expect(await within(dialog).findByText('Ana Ruiz')).toBeTruthy();
  });
});

describe('Thread settings', () => {
  it('shows the manager rows and asks before deleting', async () => {
    serve(() => ok());
    queryClient.setQueryData(threadKeys.view('t1'), thread());
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(within(dialog).getByText('Notify me')).toBeTruthy();
    expect(within(dialog).getByText('Who can read')).toBeTruthy();
    expect(within(dialog).getByRole('switch', { name: 'Pin for everyone' })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete thread' }));
    expect(await screen.findByRole('alertdialog', { name: 'Delete this thread?' })).toBeTruthy();
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('leaves out what only a manager may change', async () => {
    serve(() => ok());
    queryClient.setQueryData(threadKeys.view('t1'), thread({ me: { can_manage: false, can_post: true } }));
    show(<ThreadHead id="t1" />, '/threads/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(within(dialog).getByText('Notify me')).toBeTruthy();
    expect(within(dialog).queryByText('Who can read')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Delete thread' })).toBeNull();
  });
});

describe('Browse threads', () => {
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
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.endsWith('/api/v2/threads/t7/follow'))).toBe(true));
  });

  it('says so when there is nothing to join', async () => {
    serve(() => ok({ threads: [], next_cursor: null }));
    show(<Browse onClose={() => {}} />, '/threads');
    expect(await screen.findByRole('heading', { name: 'No threads to join' })).toBeTruthy();
  });
});

describe('Updates archive', () => {
  it('is its own page under Updates, listing only archived updates', async () => {
    const up = (id: string, body: string, archived: boolean) => ({ id, bot: 'ops', kind: 'daily', day: '2026-01-02', body, read: true, archived, created_at: ago(5) });
    serve((url) =>
      url.includes('archive=true') ? ok({ updates: [up('u1', 'Old plan', true), up('u2', 'Moved back', false)], unread: 0, next_before: null }) : ok(),
    );
    show(<UpdatesPage tab="archive" />, '/updates/archive');
    expect(await screen.findByRole('heading', { name: 'Archive' })).toBeTruthy();
    expect((await screen.findByTitle('Old plan')).getAttribute('href')).toBe('/updates/archive?open=u1');
    expect(screen.queryByTitle('Moved back')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark read' })).toBeNull();
  });
});

describe('Mail instructions', () => {
  it('shows a row for them only when the mailbox has them, and reads them in the item pane', async () => {
    serve((url) =>
      url.includes('/mail/agent') ? ok({ mailbox: 'help@example.test', bot: 'support', instructions: 'Answer **within a day**.' }) : ok({ messages: [] }),
    );
    queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test', agent_instructions: true }]);
    show(<MailPage box="help@example.test" view="instructions" />, '/threads/mail?box=help%40example.test&view=instructions');
    expect((await screen.findAllByTitle('Instructions')).find((el) => el.tagName === 'A')?.getAttribute('href')).toBe('/threads/mail?box=help%40example.test&view=instructions');
    expect(await screen.findByText('within a day')).toBeTruthy();
  });
});

describe('Learned @s213', () => {
  it('says so when nothing is learned yet', async () => {
    serve(() => ok());
    queryClient.setQueryData(learnedQuery.queryKey, { personal: [], company: null });
    queryClient.setQueryData(viewersQuery.queryKey, { bots: [], include_learned: false });
    show(<LearnedPage />, '/learned');
    expect(await screen.findByRole('heading', { name: 'Nothing learned yet' })).toBeTruthy();
  });

  it('lists lines by section, finds them by words, and asks before deleting one', async () => {
    serve(() => ok());
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
});
