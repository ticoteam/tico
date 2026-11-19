// Tier 2 gaps closed on the threads pages: a team as a thread's reader, and reading a thread before joining it.
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { groupsQuery } from '@/features/team/exports';
import { Browse } from './Browse';
import type { Thread } from './model';
import { threadKeys } from './queries';
import { ThreadHead } from './ThreadHead';

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
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Call['body']) : undefined;
    calls.push({ url: String(url), method, body });
    const r = reply(String(url), method) ?? ok({ items: [], counts: [] });
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

const thread = (extra: Partial<Thread> = {}): Thread => ({
  id: 't1',
  title: 'Launch plan',
  audience: 'members',
  last_seq: 2,
  last_unread_seq: 0,
  last_message_at: new Date().toISOString(),
  pinned_for_everyone: false,
  root_id: 't1',
  me: { can_manage: true, can_post: true, following: true, notify: 'live' },
  members: { members: [{ actor: 'human:me' }] },
  ...extra,
});

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(groupsQuery.queryKey, [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: [], people: [] }]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Thread settings @s178', () => {
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
    await waitFor(() => expect(sent('PATCH', '/api/v2/threads/t1').at(-1)?.body).toEqual({ audience: 'team', audience_group_id: 'g1' }));
  });

  it('a team reader needs the team, and Tico may not open it wider @s178', async () => {
    serve();
    show(<span />, '/threads/t1');
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team' }, { via: 'click' })).ok).toBe(false);
    expect((await run('threads.whoCanRead', { thread: 't1', audience: 'team', team: 'g1' }, { via: 'tico' })).ok).toBe(false);
    expect(sent('PATCH', '/api/v2/threads/t1')).toHaveLength(0);
  });
});

describe('Browse threads @s176', () => {
  it("opens a thread from its name, to read it before joining @s176", async () => {
    serve((url) => (url.includes('/threads/browse') ? ok({ threads: [{ ...thread({ id: 't5', title: 'Hiring' }), member_count: 3 }], next_cursor: null }) : undefined));
    const onClose = vi.fn();
    const router = show(<Browse onClose={onClose} />, '/threads');
    const dialog = await screen.findByRole('dialog', { name: 'Browse threads' });
    fireEvent.click(await within(dialog).findByRole('link', { name: 'Hiring' }));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/t5'));
    expect(sent('POST', '/api/v2/threads/t5/join')).toHaveLength(0);
  });
});
