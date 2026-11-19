import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { ItemHead } from '@/pagekinds';
import type { Thread, ThreadList } from './model';
import { renameThread, threadKeys } from './queries';
import { ThreadHead } from './ThreadHead';
import { ThreadsPage } from './ThreadsPage';
import { UpdatesPage } from './UpdatesPage';

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
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const r = await reply(String(url), init?.method ?? 'GET');
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const at = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const thread = (id: string, title: string, min: number, unread = 0): Thread => ({
  id,
  title,
  audience: 'members',
  last_message_at: at(min),
  last_seq: 5,
  last_unread_seq: 0,
  pinned_for_everyone: false,
  root_id: id,
  me: { unread, can_manage: true },
});
const seeded = (): ThreadList => ({ pages: [{ rows: [thread('t1', 'Launch plan', 2, 3), thread('t2', 'Hiring', 30)], next: null }], pageParams: [null] });

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('Threads @s156', () => {
  it('lists threads newest first, each row opening its own address, under Catch up', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(threadKeys.list, seeded());
    queryClient.setQueryData(threadKeys.catchUp, { count: 2, items: [] });
    show(<ThreadsPage pane="catch-up" />, '/threads');
    const launch = await screen.findByTitle('Launch plan');
    expect(launch.getAttribute('href')).toBe('/threads/t1');
    expect(launch.className).toContain('is-unread');
    expect(screen.getByTitle('Hiring').getAttribute('href')).toBe('/threads/t2');
    const catchUp = screen.getAllByTitle('Catch up').find((el) => el.tagName === 'A');
    expect(catchUp?.getAttribute('href')).toBe('/threads/catch-up');
    expect(catchUp?.textContent).toContain('2');
    expect(screen.getByRole('button', { name: 'New message' })).toBeTruthy();
  });

  it('shows a rename at once and takes it back when the server refuses it', async () => {
    let refuse!: () => void;
    serve((_url, method) =>
      method === 'PATCH' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : { status: 200, body: {} },
    );
    queryClient.setQueryData(threadKeys.list, seeded());
    queryClient.setQueryData(threadKeys.catchUp, { count: 0, items: [] });
    show(<ThreadsPage pane="catch-up" />, '/threads');
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

  it('finds threads with the server search, keeping the words in each row address', async () => {
    const asked: string[] = [];
    serve((url) => {
      asked.push(url);
      return url.includes('/threads/search')
        ? { status: 200, body: { hits: [{ thread_id: 't9', message_id: 'm1', title: 'Pricing', excerpt: 'the launch price' }], next_cursor: null } }
        : { status: 200, body: {} };
    });
    queryClient.setQueryData(threadKeys.list, seeded());
    queryClient.setQueryData(threadKeys.catchUp, { count: 0, items: [] });
    show(<ThreadsPage pane="catch-up" q="launch price" />, '/threads?q=launch%20price');
    const hit = await screen.findByTitle('Pricing');
    expect(hit.getAttribute('href')).toBe('/threads/t9?q=launch%20price');
    expect(asked.some((u) => u.includes('/api/v2/threads/search?q=launch+price'))).toBe(true);
    expect(screen.queryByTitle('Hiring')).toBeNull();
  });

  it('returns to the filtered list from a thread opened through search', async () => {
    serve((url) => ({
      status: 200,
      body: url.includes('/timeline') ? { messages: [], next_cursor: null } : {},
    }));
    queryClient.setQueryData(threadKeys.list, seeded());
    show(<ThreadsPage pane="thread" thread="t1" q="launch price" open />, '/threads/t1?q=launch%20price');
    const back = await screen.findByRole<HTMLAnchorElement>('link', { name: 'Back to Threads' });
    const url = new URL(back.href);
    expect(url.pathname).toBe('/threads');
    expect(url.searchParams.get('q')).toBe('launch price');
  });

  it('says so when there are no threads yet', async () => {
    serve(() => ({ status: 200, body: { count: 0, items: [] } }));
    queryClient.setQueryData(threadKeys.list, { pages: [{ rows: [], next: null }], pageParams: [null] });
    show(<ThreadsPage pane="catch-up" />, '/threads');
    expect(await screen.findByRole('heading', { name: 'No threads yet' })).toBeTruthy();
  });
});

describe('A thread head', () => {
  it('offers Join while a call is on in the thread', async () => {
    const live = { id: 'c1', state: 'live', provider: 'tico', thread_id: 't1', participants: [], bots: [], started_at: at(3) };
    serve((url) => {
      if (url.includes('/threads/t1/calls')) return { status: 200, body: { available: true, calls: [live], next_scheduled: null, next_before: null, providers: ['tico'] } };
      if (url.includes('/calls/c1')) return { status: 200, body: live };
      if (url.endsWith('/threads/t1')) return { status: 200, body: { ...thread('t1', 'Launch plan', 2), me: { can_post: true }, members: { members: [{ actor: 'human:me' }, { actor: 'human:ana' }] } } };
      return { status: 200, body: {} };
    });
    show(<ThreadHead id="t1" />, '/threads/t1');
    const join = await screen.findByRole('button', { name: 'Join call' });
    await waitFor(() => expect((join as HTMLButtonElement).disabled).toBe(false));
  });
});

describe('Updates @s972', () => {
  it('says so when there are none', async () => {
    serve(() => ({ status: 200, body: { updates: [], unread: 0, next_before: null } }));
    show(<UpdatesPage />, '/updates');
    expect(await screen.findByRole('heading', { name: 'No updates yet' })).toBeTruthy();
  });

  it('opens an update from its row', async () => {
    const u = { id: 'u1', bot: 'ops', kind: 'daily', day: '2026-01-02', body: 'Shipped the launch page\n\nMore words.', read: false, created_at: at(5) };
    serve(() => ({ status: 200, body: { updates: [u], unread: 1, next_before: null } }));
    show(<UpdatesPage />, '/updates');
    const rowLink = await screen.findByTitle('Shipped the launch page');
    expect(rowLink.getAttribute('href')).toBe('/updates?open=u1');
  });
});

describe('A head renamed in place', () => {
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
