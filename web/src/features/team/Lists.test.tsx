// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, type AnyRouter } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { needed } from './LastRun';
import { fileName, fileOpen, lineOf, rowOf } from './model';
import { agentsQuery, groupsQuery, type AgentFile, type AgentRow, type Group, type LastStop, type TaskTab } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let reads: string[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined) {
  sent = [];
  reads = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method === 'GET') reads.push(u);
    else sent.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = reply(u, method) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return { status: 200, body: groups };
  if (url.includes('/api/employees')) return { status: 200, body: roster };
  if (url.includes('/api/runs')) return { status: 200, body: [] };
  if (url.includes('/api/v2/tasks')) return { status: 200, body: { tasks: [] } };
  return { status: 200, body: {} };
};

let router: AnyRouter;
function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const actors = [ana, pat];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: 'human:p1', bots: ['0190-ana'], people: ['p1'] }];
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3 } as AgentRow];

const defaults: TaskTab[] = [
  { builtin: 'needs', name: 'Needs you', show_count: true, conditions: [{ field: 'needs', value: 'you' }] },
  { builtin: 'done', name: 'Done recently', show_count: true },
];
const launch: TaskTab = { name: 'Launch', show_count: true, conditions: [{ field: 'label', value: 'launch' }] };
const layout = (tabs: TaskTab[], canEdit = true) => ({ status: 200, body: { bot: '0190-ana', tabs, widgets: [], saved: true, can_edit: canEdit } });
const task = (id: string, title: string) => ({ id, number: null, title, status: 'open', pipeline_id: null, updated_at: '2026-11-18T10:00:00Z' });

/** Ana's page, her lists as `tabs`, the Launch list (c2) showing two of its five tasks; a PUT answers `put` when given. */
function lists(tabs: TaskTab[], put?: (body: { tabs: TaskTab[] }) => Reply, canEdit = true) {
  let now = tabs;
  serve((url, method) => {
    if (url.endsWith('/bots/ana/task-tabs') && method === 'PUT') {
      const body = sent.at(-1)?.body as { tabs: TaskTab[] };
      const r = put?.(body) ?? layout(body.tabs);
      if (r.status === 200) now = body.tabs;
      return r;
    }
    if (url.endsWith('/bots/ana/task-tabs')) return layout(now, canEdit);
    if (url.includes('/task-tabs/tasks?tab=c2'))
      return { status: 200, body: { tabs: [{ key: 'c2', name: 'Launch', count: 5, ids: ['t2', 't1'] }], tasks: [task('t1', 'Draft the post'), task('t2', 'Pick the date')] } };
    if (url.includes('/task-tabs/tasks')) return { status: 200, body: { tabs: [], tasks: [] } };
    return undefined;
  });
  show(<TeamWho who="ana" />, '/team/ana');
}

const putBody = () => sent.find((s) => s.method === 'PUT')?.body as { tabs: TaskTab[] } | undefined;

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
});
afterEach(() => vi.unstubAllGlobals());

describe('Its own lists @s611 @s653', () => {
  it('writes a row the way the server stores it, and back', () => {
    expect(rowOf({ field: 'status', value: 'open,done' })).toEqual({ field: 'status', value: ['open', 'done'] });
    expect(rowOf({ field: 'created_within', value: '7' })).toEqual({ field: 'created_within', value: 7 });
    expect(rowOf({ field: 'due', value: 'overdue' })).toEqual({ field: 'due', value: 'overdue' });
    expect(rowOf({ field: 'due', value: '3' })).toEqual({ field: 'due', value: 3 });
    expect(lineOf({ field: 'status', value: ['open', 'closed'] })).toEqual({ field: 'status', value: 'open,closed' });
  });

  it('shows each of its own lists as a section, newest first, with the count; the defaults are not repeated', async () => {
    lists([...defaults, launch]);
    const section = await screen.findByRole('region', { name: 'Launch' });
    await waitFor(() => expect(section.querySelectorAll('.row')).toHaveLength(2));
    expect(section.querySelectorAll('.row-title')[0]?.textContent).toBe('Pick the date');
    expect(within(section).getByText('3 more')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Needs you' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Done recently' })).toBeNull();
  });

  it('"N more" reads the next page from its next and shows it under the first', async () => {
    const tabs: TaskTab[] = [{ name: 'Launch', show_count: true, conditions: [{ field: 'label', value: 'launch' }] }];
    serve((url) => {
      if (url.endsWith('/bots/ana/task-tabs')) return layout(tabs);
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

  it('has no list controls for someone who may not change them', async () => {
    lists([...defaults, launch], undefined, false);
    const section = await screen.findByRole('region', { name: 'Launch' });
    expect(within(section).queryByRole('button', { name: 'Launch options' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('menuitem', { name: 'Dream' });
    expect(screen.queryByRole('menuitem', { name: 'New list' })).toBeNull();
  });

  it('adds a list from the page’s More menu, after the ones there', async () => {
    lists([...defaults, launch]);
    await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New list' }));
    const dialog = await screen.findByRole('dialog', { name: 'New list' });
    expect(within(dialog).getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Mine' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody()?.tabs.map((t) => t.name)).toEqual(['Needs you', 'Done recently', 'Launch', 'Mine']));
    const body = putBody();
    expect(body?.tabs[3]).toEqual({ name: 'Mine', show_count: true, conditions: [{ field: 'assigned_to', value: 'this_bot' }] });
    // A default keeps its own filter; Done recently is sent without one, and the widgets are left as they are.
    expect(body?.tabs[0]?.conditions).toEqual([{ field: 'needs', value: 'you' }]);
    expect(body?.tabs[1]).toEqual({ builtin: 'done', name: 'Done recently', show_count: true });
    expect(body && 'widgets' in body).toBe(false);
    expect(await screen.findByRole('region', { name: 'Mine' })).toBeTruthy();
  });

  it('renames a list and changes its filter', async () => {
    lists([...defaults, launch]);
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(within(section).getByRole('button', { name: 'Launch options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit list' });
    expect((within(dialog).getByLabelText('Row 1 value') as HTMLInputElement).value).toBe('launch');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Launch week' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add row' }));
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Done' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(putBody()?.tabs[2]).toEqual({
        name: 'Launch week',
        show_count: true,
        conditions: [
          { field: 'label', value: 'launch' },
          { field: 'status', value: ['open', 'done'] },
        ],
      }),
    );
    expect(await screen.findByRole('region', { name: 'Launch week' })).toBeTruthy();
  });

  it('removes a list', async () => {
    lists([...defaults, launch]);
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(within(section).getByRole('button', { name: 'Launch options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    await waitFor(() => expect(putBody()?.tabs.map((t) => t.name)).toEqual(['Needs you', 'Done recently']));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Launch' })).toBeNull());
  });

  it('puts a removed list back when the server refuses', async () => {
    lists([...defaults, launch], () => ({ status: 403, body: { error: { code: 'forbidden', detail: 'Only the bot or a person who manages it changes its sidebar' } } }));
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(within(section).getByRole('button', { name: 'Launch options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    await waitFor(() => expect(putBody()).toBeTruthy());
    expect(await screen.findByRole('region', { name: 'Launch' })).toBeTruthy();
  });

  it('shows a Pipeline row from an older layout and saves only once it is removed', async () => {
    lists([...defaults, { name: 'Launch', show_count: true, conditions: [{ field: 'pipeline', value: ['p1'] }] }]);
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(within(section).getByRole('button', { name: 'Launch options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit list' });
    expect(within(dialog).getByText('Pipeline')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove row 1' }));
    expect(within(dialog).getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false);
  });
});

const file = (over: Partial<AgentFile>): AgentFile =>
  ({
    id: 'f1',
    bot: '0190-ana',
    kind: 'document',
    library_kind: 'document',
    locator: 'stored',
    title: 'notes/plan.md',
    name: 'plan.md',
    open: { type: 'tico', url: '/api/v2/files/f1' },
    last_activity_at: '2026-11-18T10:00:00Z',
    scope: 'bot',
    state: 'ok',
    synced: true,
    version: 1,
    working: false,
    ...over,
  }) as AgentFile;

describe('Files @s614', () => {
  it('names a stored file by its own name and opens it over the page; a link opens at its provider over https', () => {
    expect(fileName(file({}))).toBe('plan.md');
    expect(fileOpen(file({}))).toEqual({ href: '/api/v2/files/f1', view: true });
    expect(fileOpen(file({ locator: 'remote_link', open: { type: 'external', url: 'https://example.com/a' } }))).toEqual({ href: 'https://example.com/a', view: false });
    expect(fileOpen(file({ locator: 'remote_link', open: { type: 'external', url: 'https://docs.google.com/d/1' } }))?.view).toBe(true);
    expect(fileOpen(file({ locator: 'remote_link', open: { type: 'external', url: 'http://example.com/a' } }))).toBeNull();
    expect(fileOpen(file({ open: null }))).toBeNull();
  });

  it('shows no Files section when it has none', async () => {
    serve((url) => (url.includes('/bots/ana/files') ? { status: 200, body: { bot: '0190-ana', files: [], total: 0, has_more: false, next_cursor: null, can_manage: true } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    await waitFor(() => expect(queryClient.getQueryData(['team', 'files', 'ana'])).toBeTruthy());
    expect(await screen.findByRole('heading', { name: 'Nothing yet' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Files' })).toBeNull();
  });

  it('shows the three newest, then all of them; a plain click opens a stored file over the page', async () => {
    const files = ['a', 'b', 'c', 'd', 'e'].map((x, i) => file({ id: `f${i}`, title: `${x}.md`, name: `${x}.md`, open: { type: 'tico', url: `/api/v2/files/f${i}` } }));
    files[4] = file({ id: 'f4', locator: 'remote_link', title: 'Brief', name: null, open: { type: 'external', url: 'https://example.com/brief' } });
    serve((url) => (url.includes('/bots/ana/files') ? { status: 200, body: { bot: '0190-ana', files, total: 5, has_more: false, next_cursor: null, can_manage: true } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Files' });
    expect(section.querySelectorAll('.row')).toHaveLength(3);
    const first = within(section).getByTitle('a.md');
    expect(first.getAttribute('href')).toBe('/api/v2/files/f0');
    expect(fireEvent.click(first)).toBe(false);
    expect(fireEvent.click(first, { metaKey: true })).toBe(true);
    fireEvent.click(within(section).getByRole('button', { name: '2 more' }));
    expect(section.querySelectorAll('.row')).toHaveLength(5);
    const link = within(section).getByTitle('Brief');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(fireEvent.click(link)).toBe(true);
    fireEvent.click(within(section).getByRole('button', { name: 'Less' }));
    expect(section.querySelectorAll('.row')).toHaveLength(3);
  });

  it('searches its files on the server', async () => {
    serve((url) => {
      if (!url.includes('/bots/ana/files')) return undefined;
      const list = url.includes('q=') ? [file({ id: 'f9', title: 'note-9.md', name: 'note-9.md' })] : ['a', 'b', 'c', 'd'].map((x, i) => file({ id: `f${i}`, title: `${x}.md`, name: `${x}.md` }));
      return ok({ bot: '0190-ana', files: list, total: list.length, has_more: false, next_cursor: null });
    });
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Files' });
    fireEvent.click(within(section).getByRole('button', { name: 'Search files' }));
    fireEvent.change(within(section).getByRole('textbox', { name: 'Search files' }), { target: { value: 'notes' } });
    expect(await within(section).findByTitle('note-9.md')).toBeTruthy();
    expect(reads.some((u) => u.includes('/bots/ana/files') && u.includes('q=notes'))).toBe(true);
  });
});

const stopped: LastStop = {
  kind: 'stopped',
  tone: 'alert',
  needs_person: true,
  action: 'review',
  at: '2026-11-18T10:00:00Z',
  reason: 'The run ended with an error before it finished.',
  text: 'Last run stopped partway.',
};

describe('Last run @s639', () => {
  it('says whether anything is needed in the old words', () => {
    expect(needed({ ...stopped, needs_person: false, tone: 'soft', action: null }, true)).toBe('No. Nothing was lost.');
    expect(needed(stopped, true)).toBe('Yes: say whether to try it again or let it go.');
    expect(needed(stopped, false)).toBe('Yes: a person who manages this agent decides whether to try it again.');
    expect(needed({ ...stopped, action: null }, true)).toBe('Yes, as the line above says.');
  });

  it('shows nothing when the last run finished', async () => {
    serve((url) => (url.includes('/api/v2/status?bot=ana') ? { status: 200, body: { bot: '0190-ana', status: { state: 'idle', last_stop: null } } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    await waitFor(() => expect(queryClient.getQueryData(['team', 'status', 'ana'])).toBeNull());
    await screen.findByRole('heading', { name: 'Ana' });
    expect(screen.queryByText('Stopped')).toBeNull();
    expect(screen.queryByText('Last run')).toBeNull();
  });

  it('opens what happened from the Stopped pill, and Review opens the stopped run in Settings', async () => {
    serve((url) => (url.includes('/api/v2/status?bot=ana') ? { status: 200, body: { bot: '0190-ana', status: { state: 'crashed', last_stop: stopped } } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Stopped' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('Last run stopped partway.')).toBeTruthy();
    expect(within(dialog).getByText('The run ended with an error before it finished.')).toBeTruthy();
    expect(within(dialog).getByText('Yes: say whether to try it again or let it go.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/agents'));
    expect(router.state.location.search).toEqual({ open: 'ana' });
  });

  it('a soft stop is a grey Last run with nothing to do', async () => {
    const soft: LastStop = { kind: 'never_started', tone: 'soft', needs_person: false, action: null, text: 'Last run didn’t start in time.' };
    serve((url) => (url.includes('/api/v2/status?bot=ana') ? { status: 200, body: { bot: '0190-ana', status: { state: 'idle', last_stop: soft } } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Last run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('No. Nothing was lost.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Review' })).toBeNull();
  });

  it('opens from the head’s More too, where a narrow page hides the pill', async () => {
    const early = { tone: 'soft', needs_person: false, reason: 'It ran out of time', text: 'Stopped early' };
    serve((url) => (url.includes('/api/v2/status') ? ok({ status: { state: 'idle', last_stop: early } }) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByRole('button', { name: /Last run/ })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'More' })[0]!);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Last run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('Is anything needed?')).toBeTruthy();
    expect(within(dialog).getByText('No. Nothing was lost.')).toBeTruthy();
  });

  it('tells someone who does not manage the agent that a manager decides, with no Review', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0]!, can_manage: false }]);
    serve((url) => (url.includes('/api/v2/status?bot=ana') ? ok({ status: { state: 'crashed', last_stop: stopped } }) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Stopped' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('The run ended with an error before it finished.')).toBeTruthy();
    expect(within(dialog).getByText('Yes: a person who manages this agent decides whether to try it again.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Review' })).toBeNull();
  });
});

describe('Phone heads', () => {
  it('keeps an agent’s Settings on a phone', async () => {
    serve(() => undefined);
    show(<TeamWho who="ana" />, '/team/ana');
    expect((await screen.findByRole('button', { name: 'Settings' })).hasAttribute('data-phone')).toBe(true);
  });
});
