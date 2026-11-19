// Tier 2 gaps closed on the team pages: one short test per row, through the page's controls or the action layer.
// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, type AnyRouter } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentQuery, agentsQuery, computersQuery, groupsQuery, peopleQuery, type AgentRow, type Computer, type Group, type TaskTab } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
let reads: string[] = [];
function serve(reply: (url: string, method: string, body: unknown) => Reply | undefined = () => undefined) {
  sent = [];
  reads = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (method === 'GET') reads.push(u);
    else sent.push({ url: u, method, body });
    const r = reply(u, method, body) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return ok(groups);
  if (url.includes('/api/employees')) return ok(roster);
  if (url.includes('/api/runs')) return ok([]);
  if (url.includes('/api/v2/tasks')) return ok({ tasks: [] });
  return ok();
};

let router: AnyRouter;
function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const posted = (part: string) => sent.find((s) => s.url.includes(part));
const pick = async (combo: HTMLElement, name: string) => {
  fireEvent.click(combo);
  const o = await screen.findByRole('option', { name });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const bo: Actor = { id: 'bot:0191-bo', kind: 'agent', name: 'Bo', slug: 'bo' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const actors = [ana, bo, pat];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: 'human:p1', bots: ['0190-ana', '0191-bo'], people: ['p1'] }];
const roster: AgentRow[] = [
  { id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, owner_id: 'p1' } as AgentRow,
  { id: '0191-bo', slug: 'bo', display_name: 'Bo', revision: 1, owner_id: 'p1' } as AgentRow,
];
const studio = { id: 'r1', label: 'Studio', online: true, bots: ['0190-ana'], screen: 'control', screen_available: true, may_manage: true } as Computer;

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, [{ id: 'p1', name: 'Pat Doe' }]);
  queryClient.setQueryData(computersQuery.queryKey, [studio]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ---------------------------------------------------------------- archive */

describe('Archive agent @s716', () => {
  it('from Tico with nothing on screen, reads the revision first instead of sending 1 @s716', async () => {
    queryClient.removeQueries({ queryKey: agentsQuery.queryKey });
    queryClient.removeQueries({ queryKey: agentQuery('ana').queryKey });
    serve((url, method) => {
      if (method === 'POST' && url.includes('/archive')) return ok({});
      if (url.endsWith('/api/v2/bots/ana')) return ok({ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 7 });
      return undefined;
    });
    show(<span />, '/team/ana');
    const r = await run('agents.archive', { slug: 'ana' }, { via: 'tico' });
    expect(r.ok).toBe(true);
    expect(posted('/bots/ana/archive')?.body).toEqual({ successor: null, expected_revision: 7 });
  });

  it('hands its open tasks to the agent picked in "Hand its work to" @s716', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/archive') ? ok({}) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive Ana' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Archive Ana?' });
    expect(within(dialog).getByText(/Its open tasks go to Pat Doe/)).toBeTruthy();
    await pick(within(dialog).getByRole('combobox'), 'Bo');
    expect(within(dialog).getByText(/Its open tasks go to Bo/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(posted('/bots/ana/archive')?.body).toEqual({ successor: '0191-bo', expected_revision: 3 }));
  });
});

/* ---------------------------------------------------------------- its own lists */

describe('Agent: custom task-list tabs @s611', () => {
  it('"N more" reads the next page from its next and shows it under the first @s611', async () => {
    const task = (id: string, title: string) => ({ id, number: null, title, status: 'open', pipeline_id: null, updated_at: '2026-11-18T10:00:00Z' });
    const tabs: TaskTab[] = [{ name: 'Launch', show_count: true, conditions: [{ field: 'label', value: 'launch' }] }];
    serve((url) => {
      if (url.endsWith('/bots/ana/task-tabs')) return ok({ bot: '0190-ana', tabs, widgets: [], saved: true, can_edit: true });
      if (url.includes('/task-tabs/tasks?tab=c0&before='))
        return ok({ tabs: [{ key: 'c0', name: 'Launch', count: 3, ids: ['t3'], next: null }], tasks: [task('t3', 'Book the room')] });
      if (url.includes('/task-tabs/tasks?tab=c0'))
        return ok({ tabs: [{ key: 'c0', name: 'Launch', count: 3, ids: ['t1', 't2'], next: '2026-11-01|t2' }], tasks: [task('t1', 'Draft the post'), task('t2', 'Pick the date')] });
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(await within(section).findByRole('button', { name: '1 more' }));
    expect(await within(section).findByText('Book the room')).toBeTruthy();
    expect(reads.some((u) => u.includes('before=2026-11-01%7Ct2'))).toBe(true);
    expect(within(section).queryByText('1 more')).toBeNull();
  });
});

/* ---------------------------------------------------------------- decisions */

describe('Learnings panel: Decisions @s622', () => {
  it('past 20, "N more" opens the rest @s622', async () => {
    const lines = Array.from({ length: 22 }, (_, i) => `- 2026-10-${String(i + 1).padStart(2, '0')} Decision ${i + 1}`);
    serve((url) => (url.includes('/bots/ana/memory') ? ok({ documents: { 'memory/decisions.md': ['# Decisions', ...lines].join('\n') } }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: 'Decisions' });
    expect(section.querySelectorAll('.row')).toHaveLength(20);
    fireEvent.click(within(section).getByRole('button', { name: '2 more' }));
    expect(section.querySelectorAll('.row')).toHaveLength(22);
  });
});

/* ---------------------------------------------------------------- files */

describe('Agent: Files widget @s614', () => {
  const file = (i: number) => ({
    id: `f${i}`,
    title: `note-${i}.md`,
    name: `note-${i}.md`,
    locator: 'stored',
    last_activity_at: '2026-11-18T10:00:00Z',
    open: { type: 'tico', url: `/api/v2/files/f${i}` },
  });
  const files = () =>
    serve((url) => {
      if (!url.includes('/bots/ana/files')) return undefined;
      const narrowed = url.includes('q=') || url.includes('kind=');
      const list = narrowed ? [file(9)] : [0, 1, 2, 3].map(file);
      return ok({ bot: '0190-ana', files: list, total: list.length, has_more: false, next_cursor: null });
    });

  it('searches its files on the server @s614', async () => {
    files();
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Files' });
    fireEvent.click(within(section).getByRole('button', { name: 'Search files' }));
    fireEvent.change(within(section).getByRole('textbox', { name: 'Search files' }), { target: { value: 'notes' } });
    expect(await within(section).findByTitle('note-9.md')).toBeTruthy();
    expect(reads.some((u) => u.includes('/bots/ana/files') && u.includes('q=notes'))).toBe(true);
  });

  it('narrows its files to one kind @s614', async () => {
    files();
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Files' });
    fireEvent.click(within(section).getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Images' }));
    expect(await within(section).findByTitle('note-9.md')).toBeTruthy();
    expect(reads.some((u) => u.includes('/bots/ana/files') && u.includes('kind=image'))).toBe(true);
  });
});

/* ---------------------------------------------------------------- the screen's gate */

describe('computers.setScreen for Tico', () => {
  it('lets Tico turn a screen off or to view only, never on to control', async () => {
    serve((_url, method) => (method === 'POST' || method === 'PUT' || method === 'PATCH' ? ok({ mode: 'off' }) : undefined));
    show(<span />, '/team/ana');
    expect((await run('computers.setScreen', { computer: 'r1', mode: 'off' }, { via: 'tico' })).ok).toBe(true);
    expect((await run('computers.setScreen', { computer: 'r1', mode: 'view' }, { via: 'tico' })).ok).toBe(true);
    expect(await run('computers.setScreen', { computer: 'r1', mode: 'control' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});
