// Tier 2 checks for the team track: one short test per parity row, through the page's own controls.
// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, type AnyRouter } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { AddTeammate } from './AddTeammate';
import { agentsQuery, computersQuery, groupsQuery, modelsQuery, peopleQuery, type AgentRow, type Computer, type Group, type LastStop, type TaskTab } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

// The screen's viewer is a stand-in: nothing connects anywhere.
const fake = vi.hoisted(() => ({ viewers: [] as (EventTarget & { viewOnly: boolean })[] }));
vi.mock('@novnc/novnc', () => ({
  default: class extends EventTarget {
    viewOnly = false;
    scaleViewport = false;
    resizeSession = true;
    background = '';
    focusOnClick = true;
    constructor() {
      super();
      fake.viewers.push(this);
    }
    disconnect() {}
    focus() {}
  },
}));
class Socket extends EventTarget {
  constructor(public url: string) {
    super();
  }
  close() {}
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
function serve(reply: (url: string, method: string, body: unknown) => Reply | undefined = () => undefined) {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (method !== 'GET') sent.push({ url: u, method, body });
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
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const posted = (part: string) => sent.find((s) => s.method !== 'GET' && s.url.includes(part));

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const actors = [ana, pat];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: 'human:p1', bots: ['0190-ana'], people: ['p1'] }];
const roster: AgentRow[] = [
  { id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, model: 'm-one', harness: 'h1', machine: { runner_id: 'r1', label: 'Studio', generation: 2 } } as AgentRow,
];
const studio = { id: 'r1', label: 'Studio', online: true, bots: ['0190-ana'], screen: 'control', screen_available: true, may_manage: true } as Computer;

beforeEach(() => {
  fake.viewers.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, { people: [{ id: 'p1', name: 'Pat Doe' }], seats: [] });
  queryClient.setQueryData(computersQuery.queryKey, [studio, { ...studio, id: 'r2', label: 'Annex', bots: [] }]);
  queryClient.setQueryData(modelsQuery.queryKey, {
    models: [
      { id: 'm-one', label: 'Model One', harnesses: ['h1'] },
      { id: 'm-two', label: 'Model Two', harnesses: ['h1'] },
    ],
    harnesses: [{ id: 'h1', name: 'Harness One' }],
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ---------------------------------------------------------------- its own lists */

const defaults: TaskTab[] = [
  { builtin: 'needs', name: 'Needs you', show_count: true, conditions: [{ field: 'needs', value: 'you' }] },
  { builtin: 'done', name: 'Done recently', show_count: true },
];
const launch: TaskTab = { name: 'Launch', show_count: true, conditions: [{ field: 'label', value: 'launch' }] };
const task = (id: string, title: string) => ({ id, number: null, title, status: 'open', pipeline_id: null, updated_at: '2026-11-18T10:00:00Z' });
function lists() {
  let now = [...defaults, launch];
  serve((url, method, body) => {
    if (url.endsWith('/bots/ana/task-tabs') && method === 'PUT') {
      now = (body as { tabs: TaskTab[] }).tabs;
      return ok({ bot: '0190-ana', tabs: now, widgets: [], saved: true, can_edit: true });
    }
    if (url.endsWith('/bots/ana/task-tabs')) return ok({ bot: '0190-ana', tabs: now, widgets: [], saved: true, can_edit: true });
    if (url.includes('/task-tabs/tasks?tab=c2'))
      return ok({ tabs: [{ key: 'c2', name: 'Launch', count: 5, ids: ['t2', 't1'] }], tasks: [task('t1', 'Draft the post'), task('t2', 'Pick the date')] });
    if (url.includes('/task-tabs/tasks')) return ok({ tabs: [], tasks: [] });
    return undefined;
  });
  show(<TeamWho who="ana" />, '/team/ana');
}

describe('Agent: custom task-list tabs @s611', () => {
  it('shows its own list as a section of its page, with the tasks the filter finds and the count', async () => {
    lists();
    const section = await screen.findByRole('region', { name: 'Launch' });
    expect(await within(section).findByText('Draft the post')).toBeTruthy();
    expect(within(section).getByText('Pick the date')).toBeTruthy();
    expect(within(section).getByText('3 more')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Done recently' })).toBeNull();
  });
});

describe('Task-list filter editor @s653', () => {
  it('renames a list and adds a filter row, saved into the layout in place', async () => {
    lists();
    const section = await screen.findByRole('region', { name: 'Launch' });
    fireEvent.click(within(section).getByRole('button', { name: 'Launch options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit list' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Launch week' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add row' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted('/bots/ana/task-tabs')).toBeTruthy());
    const tabs = (posted('/bots/ana/task-tabs')?.body as { tabs: TaskTab[] }).tabs;
    expect(tabs.map((t) => t.name)).toEqual(['Needs you', 'Done recently', 'Launch week']);
    expect(tabs[2]?.conditions).toHaveLength(2);
    expect(tabs[2]?.conditions?.[0]).toEqual({ field: 'label', value: 'launch' });
  });
});

/* ---------------------------------------------------------------- files */

describe('Agent: Files widget @s614', () => {
  it('lists the three newest files, then the rest on N more', async () => {
    const file = (i: number) => ({
      id: `f${i}`,
      title: `note-${i}.md`,
      name: `note-${i}.md`,
      locator: 'stored',
      last_activity_at: '2026-11-18T10:00:00Z',
      open: { type: 'tico', url: `/api/v2/files/f${i}` },
    });
    serve((url) => (url.includes('/bots/ana/files') ? ok({ bot: '0190-ana', files: [0, 1, 2, 3].map(file), total: 4, has_more: false, next_cursor: null }) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Files' });
    expect(section.querySelectorAll('.row')).toHaveLength(3);
    fireEvent.click(within(section).getByRole('button', { name: '1 more' }));
    expect(section.querySelectorAll('.row')).toHaveLength(4);
    expect(within(section).getByTitle('note-3.md').getAttribute('href')).toBe('/api/v2/files/f3');
  });
});

/* ---------------------------------------------------------------- learnings */

describe('Learnings panel: Decisions @s622', () => {
  it("lists its decisions log on its Settings, newest first", async () => {
    const log = ['# Decisions', '- 2026-10-02 Keep the digest short', '- 2026-11-14 Ask before moving a date'].join('\n');
    serve((url) => (url.includes('/bots/ana/memory') ? ok({ documents: { 'memory/decisions.md': log } }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: 'Decisions' });
    const rows = section.querySelectorAll('.row');
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain('Ask before moving a date');
  });
});

describe('Learnings panel: Dream @s623', () => {
  it('starts a dream from More, and shows its questions to answer', async () => {
    let started = false;
    serve((url, method) => {
      if (!url.includes('/bots/ana/dream')) return undefined;
      if (method === 'POST') started = true;
      const asking = { auto: false, closed_at: null, doing: null, reason: null, started_at: '2026-11-19T09:00:00Z', status: 'open', summary: null, task: 't9' };
      return ok({ dream: started ? { ...asking, state: 'asking', questions: { mine: true, requirement_id: 'r1', task_id: 't9', text: '1. Keep the old notes?' } } : null });
    });
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Dream' }));
    const section = await screen.findByRole('region', { name: 'Dream' });
    expect(await within(section).findByText('A few questions')).toBeTruthy();
    expect(within(section).getByLabelText('Your answer')).toBeTruthy();
    expect(posted('/api/v2/bots/ana/dream')?.method).toBe('POST');
  });
});

/* ---------------------------------------------------------------- external agents */

describe('External agents @s632 @s722 @s739', () => {
  it('are one Coming soon line in Add teammate that cannot be pressed', async () => {
    serve();
    show(<AddTeammate step="pick" team="" onStep={() => {}} onClose={() => {}} />, '/team/design');
    const line = (await screen.findByText('External agent')).closest('.add-person');
    expect(line?.getAttribute('aria-disabled')).toBe('true');
    expect(within(line as HTMLElement).getByText('Coming soon')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /External agent/ })).toBeNull();
  });
});

/* ---------------------------------------------------------------- last run */

describe('Last run ("Is anything needed?") @s639', () => {
  it('tells someone who does not manage the agent that a manager decides, with no Review', async () => {
    const stop: LastStop = { tone: 'alert', needs_person: true, action: 'review', at: '2026-11-18T10:00:00Z', reason: 'It hit an error.', text: 'Last run stopped partway.' };
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0]!, can_manage: false }]);
    serve((url) => (url.includes('/api/v2/status?bot=ana') ? ok({ status: { state: 'crashed', last_stop: stop } }) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Stopped' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('It hit an error.')).toBeTruthy();
    expect(within(dialog).getByText('Yes: a person who manages this agent decides whether to try it again.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Review' })).toBeNull();
  });
});

/* ---------------------------------------------------------------- copies */

describe('Copies of an agent', () => {
  const copy: Actor = { id: 'bot:0191-anb', kind: 'agent', name: 'Ana', slug: 'ana-pat' };
  const mine = { id: '0191-anb', slug: 'ana-pat', owner_id: 'p1', status: 'active', revision: 4, profile: { name: 'doer', label: 'Doer', summary: 'Does the work' } };
  const family = (branches: unknown[]) => ok({ original: '0190-ana', shared: true, branches });
  beforeEach(() => {
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [...actors, copy], byId: { ...byId, [copy.id]: copy, 'ana-pat': copy } });
    queryClient.setQueryData(agentsQuery.queryKey, [...roster, { id: '0191-anb', slug: 'ana-pat', display_name: 'Ana', revision: 4, owner_id: 'p1' } as AgentRow]);
  });

  it('opens my copy on its Settings: what it follows, its status and profile @s649', async () => {
    serve((url) => (url.includes('/bots/ana-pat/branches') ? family([mine]) : undefined));
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    const follows = await screen.findByRole('link', { name: /Ana/ });
    expect(follows.getAttribute('href')).toBe('/team/ana');
    expect(await screen.findByRole('button', { name: 'Copy: Your copy' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Status' }).textContent).toContain('Active');
    expect(screen.getByRole('combobox', { name: 'Profile' }).textContent).toContain('Does the work');
  });

  it('makes a planned copy when I have no computer for it, then opens it on Settings @s650', async () => {
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, owner_id: 'p2' }]);
    serve((url, method) => {
      if (url.includes('/bots/ana/branches')) return family([]);
      if (method === 'POST' && url.includes('/bots/ana/copies')) return { status: 201, body: { ...mine, status: 'planned' } };
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    // with no copy yet, Make my copy is in ⋯ (the pick beside the name comes with the first copy)
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Make my copy' }));
    const dialog = await screen.findByRole('dialog', { name: 'Make my copy' });
    expect(within(dialog).getByText(/You have no computer yet/)).toBeTruthy();
    expect(within(dialog).queryByRole('combobox', { name: 'Computer' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make copy' }));
    await waitFor(() => expect(posted('/bots/ana/copies')?.body).toEqual({}));
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/ana-pat'));
    expect(router.state.location.search).toEqual({ tab: 'settings' });
  });
});

/* ---------------------------------------------------------------- its computer */

describe('Computer screen @s685', () => {
  it('watches the screen first, view only', async () => {
    vi.stubGlobal('WebSocket', Socket);
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    serve((url, method, body) =>
      method === 'POST' && url.endsWith('/api/v2/computers/r1/screen/sessions')
        ? ok({ session_id: 's1', ticket: 'tk-1', control: (body as { control: boolean }).control, ws_path: '/api/v2/runners/r1/screen/ws?ticket=tk-1', width: 1440, height: 900 })
        : undefined,
    );
    show(<TeamWho who="ana" tab="screen" />, '/team/ana?tab=screen');
    await waitFor(() => expect(fake.viewers).toHaveLength(1));
    expect(posted('/screen/sessions')?.body).toEqual({ control: false });
    expect(fake.viewers[0]?.viewOnly).toBe(true);
    act(() => void fake.viewers[0]?.dispatchEvent(new Event('connect')));
    expect(await screen.findByText('View only')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Control' }).getAttribute('aria-pressed')).toBe('false');
  });
});

describe('Computer performance @s686', () => {
  it("says how it is doing in one status line, CPU and memory among it, on the computer's page", async () => {
    serve((url) =>
      url.includes('/computers/r1/performance')
        ? ok({ points: [[1, 10, 12, 40, 41, 0.5, 0, 0, 0], [2, 30, 35, 50, 52, 1.25, 0, 0, 1]] })
        : undefined,
    );
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    expect(await screen.findByText('Online')).toBeTruthy();
    expect(await screen.findByText('CPU 30%')).toBeTruthy();
    expect(screen.getByText('Memory 50%')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- model, move, archive */

describe('Move agent, and Change model settings @s715', () => {
  it('changes the model as a transition against its generation and revision', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? ok({ id: 'tr1' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Model/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Model' });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Model Two' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ kind: 'model', model: 'm-two', expected_generation: 2, expected_revision: 3 }));
  });

  it('uses the new configuration revision when applying a model and compact size together', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/compact-at')) return ok({ revision: 4 });
      if (method === 'POST' && url.endsWith('/transitions')) return ok({ id: 'tr1' });
    });
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Model/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Model' }));
    fireEvent.click(dialog.getByRole('radio', { name: 'Model Two' }));
    fireEvent.click(dialog.getByRole('button', { name: 'More' }));
    fireEvent.click(dialog.getByRole('combobox', { name: 'Compact' }));
    const size = await screen.findByRole('option', { name: '400K' });
    fireEvent.pointerDown(size, { pointerType: 'mouse' });
    fireEvent.click(size);
    fireEvent.click(dialog.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ model: 'm-two', expected_revision: 4 }));
    expect(sent[0]?.url).toMatch(/\/compact-at$/);
    expect(sent[0]?.body).toEqual({ compact_at: 400000, expected_revision: 3 });
  });

  it('moves it to another computer the same way, on pick', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? ok({ id: 'tr2' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('combobox', { name: 'Computer' }));
    const o = await screen.findByRole('option', { name: 'Annex' });
    fireEvent.pointerDown(o, { pointerType: 'mouse' });
    fireEvent.click(o);
    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ kind: 'machine', runner_id: 'r2', expected_generation: 2, expected_revision: 3 }));
  });
});

describe('Archive agent @s716', () => {
  it('asks first, then archives it against its revision and leaves its page', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.includes('/archive')) return ok({});
      if (url.endsWith('/api/v2/bots/ana')) return ok({ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3 });
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive Ana' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Archive Ana?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(posted('/bots/ana/archive')?.body).toEqual({ successor: null, expected_revision: 3 }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
  });
});
