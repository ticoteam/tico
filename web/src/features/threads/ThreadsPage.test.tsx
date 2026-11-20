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
import { MenuThreads } from './MenuThreads';
import { TeamThread } from './TeamThread';
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

describe('The menu\'s Threads list @s156', () => {
  it('lists conversations newest first, bold when new, the Dot where one asks you, no counts, no tasks', async () => {
    serve(() => ({ status: 200, body: {} }));
    const task = { ...thread('k1', 'Fix the build', 1, 2), task_id: 'k1' };
    queryClient.setQueryData(threadKeys.list, { pages: [{ rows: [thread('t1', 'Launch plan', 2, 3), thread('t2', 'Hiring', 30), task], next: null }], pageParams: [null] });
    show(<MenuThreads asks={new Set(['t2'])} />, '/threads/t2');
    const launch = await screen.findByTitle('Launch plan');
    expect(launch.getAttribute('href')).toBe('/threads/t1');
    expect(launch.className).toContain('is-new');
    expect(launch.querySelector('.dot')).toBeNull();
    expect(launch.textContent).toBe('Launch plan');
    const hiring = screen.getByTitle('Hiring');
    expect(hiring.className).not.toContain('is-new');
    expect(hiring.getAttribute('aria-current')).toBe('page');
    expect(hiring.querySelector('.dot')?.getAttribute('aria-label')).toBe('Asks you');
    expect(screen.queryByTitle('Fix the build')).toBeNull();
    expect(screen.getByRole('link', { name: 'Mail' }).getAttribute('href')).toBe('/threads/mail');
    expect(screen.getByRole('link', { name: 'Messaging' }).getAttribute('href')).toBe('/threads/messaging');
  });

  it('shows a rename at once and takes it back when the server refuses it', async () => {
    let refuse!: () => void;
    serve((_url, method) =>
      method === 'PATCH' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : { status: 200, body: {} },
    );
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

describe('A team\'s thread', () => {
  it('opens the team\'s Home and sets its teammates\' updates among its messages as cards', async () => {
    const asked: string[] = [];
    const home = { ...thread('h1', 'Growth', 5), audience: 'home', home: { actor: 'group:g1' }, me: { can_post: true } };
    const ups = [
      { id: 'u1', bot: 'gm', kind: 'weekly', headline: 'Orders 1,284. Cedar beds sold out.', body: 'Orders 1,284. Cedar beds sold out.', replies: 2, created_at: at(30) },
      { id: 'u2', bot: 'ops', kind: 'daily', body: 'Not this team', created_at: at(20) },
    ];
    serve((url) => {
      asked.push(url);
      if (url.includes('/api/v2/groups')) return { status: 200, body: [{ id: 'g1', name: 'Growth', parent: '', people: [], bots: ['gm'] }] };
      if (url.includes('/threads/home/')) return { status: 200, body: home };
      if (url.includes('/api/v2/updates')) return { status: 200, body: { updates: ups, unread: 1, next_before: null } };
      if (url.includes('/timeline')) return { status: 200, body: { messages: [{ id: 'm1', seq: 1, sender_id: 'human:jo', body: 'Proofs are in.', created_at: at(40) }, { id: 'm2', seq: 2, sender_id: 'human:jo', body: 'Page 6 fixed.', created_at: at(10) }], next_cursor: null, read_marks: {} } };
      return { status: 200, body: {} };
    });
    const actors = [
      { id: 'bot:gm', kind: 'agent', name: 'Growth Manager', slug: 'gm', team: 'Growth' },
      { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops', team: 'Engineering' },
      { id: 'human:jo', kind: 'person', name: 'Jonas Lind', team: 'Growth' },
    ];
    queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
    show(<TeamThread who="team:Growth" />, '/team/team%3AGrowth');
    const card = await screen.findByText('Orders 1,284. Cedar beds sold out.');
    expect(card.closest('a')?.getAttribute('href')).toBe('/updates?open=u1');
    expect(card.closest('.kcard')?.textContent).toContain('Weekly update');
    expect(screen.queryByText('Not this team')).toBeNull();
    expect(asked.some((u) => u.includes('/api/v2/threads/home/group%3Ag1'))).toBe(true);
    // the update sits between the two messages, by time
    const order = [...document.querySelectorAll('.msg')].map((m) => m.textContent ?? '');
    expect(order.findIndex((t) => t.includes('Proofs'))).toBeLessThan(order.findIndex((t) => t.includes('Orders')));
    expect(order.findIndex((t) => t.includes('Orders'))).toBeLessThan(order.findIndex((t) => t.includes('Page 6')));
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
