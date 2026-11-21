// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { GoalTree } from '@/api/schema';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { goalsTreeQuery } from '@/features/company/exports';
import { AddAgentButton, AddTeammate } from './AddTeammate';
import {
  accessQuery,
  agentQuery,
  agentsQuery,
  changeAgent,
  computersQuery,
  groupsQuery,
  modelsQuery,
  moveAgent,
  moveToTeam,
  peopleQuery,
  type AgentRow,
  type Computer,
  type Group,
} from './queries';
import { loadWho } from './routing';
import { SignIns } from './SignIns';
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
/** `reply` answers what the test is about; every other read a page makes is answered by `fallback`. */
function serve(reply: (url: string, method: string, body: unknown) => Reply | Promise<Reply> | undefined = () => undefined) {
  sent = [];
  reads = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (method === 'GET') reads.push(u);
    else sent.push({ url: u, method, body });
    const r = (await reply(u, method, body)) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return ok(groups);
  if (url.includes('/api/employees')) return ok(roster);
  if (url.includes('/api/runs')) return ok([]);
  if (url.includes('/api/v2/tasks')) return ok({ tasks: [] });
  if (url.includes('/api/v2/pipelines')) return ok({ pipelines: [] });
  return ok();
};
const posted = (part: string) => sent.find((s) => s.url.includes(part));
const tasksOf = (status: string, tasks: unknown[]) => (url: string, method: string) =>
  method === 'GET' && url.includes('/api/v2/tasks') && url.includes(`status=${status}`) ? ok({ tasks }) : undefined;

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
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]));
const link = (title: string) => screen.queryAllByTitle(title).find((el) => el.tagName === 'A');
async function pick(box: HTMLElement, name: string) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

// Ana is a newer agent: her bot id is not her slug, as groups, computers and tasks name her.
const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const bo: Actor = { id: 'bot:0191-bo', kind: 'agent', name: 'Bo', slug: 'bo' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const kim: Actor = { id: 'human:p2', kind: 'person', name: 'Kim Roe' };
const directory = (actors: Actor[]) => ({ actors, byId: Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]])) });
const design: Group = { id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1', 'p2'] };
const led: Group = { ...design, lead: 'human:p1', people: ['p1'] };
const anaRow = { id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, machine: { runner_id: 'r1', label: 'Studio', generation: 2 }, users: [{ id: 'p1', name: 'Pat Doe' }] } as AgentRow;
const studio = { id: 'r1', label: 'Studio', online: true, bots: ['0190-ana'], readiness: { runtimes: { codex: { installed: true, authenticated: 'missing' } } } } as unknown as Computer;
const screenStudio = { id: 'r1', label: 'Studio', online: true, bots: ['0190-ana'], screen: 'control', screen_available: true, may_manage: true } as Computer;
const annex = (c: Computer) => ({ ...c, id: 'r2', label: 'Annex', bots: [] });
const task = (id: string, title: string, more: object = {}) => ({ id, number: null, title, status: 'open', owner_id: ana.id, updated_at: '2026-11-01T00:00:00Z', ...more });

let groups: Group[] = [];
let roster: AgentRow[] = [];
/** Who the company holds, as the page finds it cached; `fallback` serves the same groups and roster. */
function seed(o: { actors?: Actor[]; groups?: Group[]; roster?: AgentRow[]; computers?: Computer[]; models?: unknown; role?: string } = {}) {
  groups = o.groups ?? [design];
  roster = o.roster ?? [anaRow];
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: o.role ?? 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, directory(o.actors ?? [ana, pat, kim]));
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(computersQuery.queryKey, o.computers ?? [studio]);
  queryClient.setQueryData(modelsQuery.queryKey, (o.models ?? { models: [], harnesses: [{ id: 'codex', name: 'Codex', capabilities: { browser_sign_in: true } }] }) as never);
}
/** Ana runs Model One on Studio; Annex is free; Pat leads Design. */
const onModels = () =>
  seed({
    actors: [ana, pat],
    groups: [led],
    roster: [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, model: 'm-one', harness: 'h1', machine: { runner_id: 'r1', label: 'Studio', generation: 2 } } as AgentRow],
    computers: [screenStudio, annex(screenStudio)],
    models: {
      models: [
        { id: 'm-one', label: 'Model One', harnesses: ['h1'] },
        { id: 'm-two', label: 'Model Two', harnesses: ['h1'] },
      ],
      harnesses: [{ id: 'h1', name: 'Harness One' }],
    },
  });
/** Pat, an owner, leads Design with Ana and Bo in it. */
const withBo = () =>
  seed({
    actors: [ana, bo, pat],
    groups: [{ ...led, bots: ['0190-ana', '0191-bo'] }],
    roster: [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, owner_id: 'p1' } as AgentRow, { id: '0191-bo', slug: 'bo', display_name: 'Bo', revision: 1, owner_id: 'p1' } as AgentRow],
    computers: [screenStudio],
    role: 'owner',
  });
/** Studio and Annex run Claude Code, signed in. */
const claude = { ...studio, readiness: { runtimes: { claude: { installed: true, authenticated: 'ready', account: { email: 'ops@example.com' }, weekly: { used_percent: 40 } } } } } as unknown as Computer;
const onClaude = (computers: Computer[] = [claude, annex(claude)]) =>
  seed({ computers, models: { models: [], harnesses: [{ id: 'claude', name: 'Claude Code', capabilities: { browser_sign_in: true, sign_out: true } }] } });

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(peopleQuery.queryKey, {
    people: [
      { id: 'p1', name: 'Pat Doe', slack: 'pat' },
      { id: 'p2', name: 'Kim Roe', email: 'kim@example.com' },
    ],
    seats: [],
  });
  seed();
});
afterEach(() => vi.unstubAllGlobals());

/** Design holds Ana and Pat and the team Brand (Bo); Sales is empty. Nothing else is cached. */
function teams() {
  seed({
    actors: [ana, bo, pat],
    groups: [
      { id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1'] },
      { id: 'g2', name: 'Brand', parent: 'g1', lead: '', bots: ['0191-bo'], people: [] },
      { id: 'g3', name: 'Sales', parent: '', lead: '', bots: [], people: [] },
    ],
    roster: [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', description: 'Writes copy', revision: 3 }, { id: '0191-bo', slug: 'bo', display_name: 'Bo', revision: 1 }],
  });
  for (const q of [peopleQuery, computersQuery, modelsQuery]) queryClient.removeQueries({ queryKey: q.queryKey });
}

describe('Team pages', () => {
  beforeEach(teams);

  it('has no page for a team: its old address goes home, the team shown in the menu, Add teammate kept @s249', async () => {
    serve();
    const router = show(<TeamWho who="design" add="pick" />, '/team/design?add=pick');
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(router.state.location.search).toEqual({ team: 'g1', add: 'pick' });
    expect(screen.queryByRole('heading', { name: 'Design' })).toBeNull();
  });

  it('moves a teammate out at once and puts them back when the server refuses', async () => {
    let refuse!: () => void;
    serve((_url, method) => (method === 'PATCH' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : undefined));
    const teamOf = (bot: string) => queryClient.getQueryData<Group[]>(groupsQuery.queryKey)?.find((g) => g.bots.includes(bot))?.id;
    let done!: Promise<unknown>;
    act(() => {
      done = moveToTeam('agent', '0190-ana', 'g3').catch(() => 'refused');
    });
    await waitFor(() => expect(teamOf('0190-ana')).toBe('g3'));
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => {
      refuse();
      expect(await done).toBe('refused');
    });
    await waitFor(() => expect(teamOf('0190-ana')).toBe('g1'));
  });

  it('shows an agent’s new role at once and takes it back when the server refuses @s615', async () => {
    let refuse!: () => void;
    serve((url, method) =>
      method === 'POST' && url.includes('/definition') ? new Promise<Reply>((r) => (refuse = () => r({ status: 409, body: { detail: 'changed' } }))) : undefined,
    );
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    expect(await screen.findByText('Writes copy')).toBeTruthy();
    expect(link('Ana')).toBeUndefined();
    let done!: Promise<unknown>;
    act(() => {
      done = changeAgent('ana', { description: 'Writes the newsletter' }).catch(() => 'refused');
    });
    expect(await screen.findByText('Writes the newsletter')).toBeTruthy();
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => {
      refuse();
      expect(await done).toBe('refused');
    });
    expect(await screen.findByText('Writes copy')).toBeTruthy();
    expect(screen.queryByText('Writes the newsletter')).toBeNull();
  });

  it('lists the pages it owns and gives it another or takes one back, as Settings › Page owners does @4857', async () => {
    const pages = {
      finance: { bot: 'bot:0190-ana', name: 'Finance', choice: 'default' },
      docs: { bot: 'bot:0190-ana', name: 'Docs', choice: 'bot:0190-ana' },
      growth: { bot: 'bot:gm', name: 'Growth', choice: 'default' },
    };
    let admin = false;
    serve((url, method) => (method === 'GET' && url.includes('/api/v2/page-owners') ? ok({ pages, can_change: admin }) : method === 'PUT' ? ok({}) : undefined));
    // a member reads the pages it owns but changes none (the server's PUT is the owner's or an admin's)
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    expect(await screen.findByText('Finance, Docs')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Pages/ })).toBeNull();
    cleanup();
    queryClient.removeQueries({ queryKey: ['settings', 'page-owners'] });
    admin = true;
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Pages\s*Finance, Docs/ }));
    expect((await screen.findByRole('menuitem', { name: /Finance/ })).getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Growth' }));
    await waitFor(() => expect(posted('/page-owners/growth')).toMatchObject({ method: 'PUT', body: { bot: '0190-ana' } }));
    fireEvent.click(await screen.findByRole('button', { name: /^Pages/ }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Docs/ }));
    await waitFor(() => expect(posted('/page-owners/docs')).toMatchObject({ method: 'PUT', body: { bot: 'default' } }));
  });

  it('links the agent’s computer and instructions as tabs of its own address', async () => {
    serve((url) => (url.includes('/instructions') ? ok({ content: 'Write short.' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    await screen.findByText('Write short.');
    expect(screen.getByTitle('Open the instructions').getAttribute('href')).toBe('/team/ana?tab=instructions');
    expect(screen.getByTitle('Open the computer').getAttribute('href')).toBe('/team/ana?tab=computer');
  });

  it('opens the model picker over Settings at ?tab=model', async () => {
    serve();
    show(<TeamWho who="ana" tab="model" />, '/team/ana?tab=model');
    expect(await screen.findByRole('dialog', { name: 'Model' })).toBeTruthy();
    expect(await screen.findByText('Writes copy')).toBeTruthy();
  });

  it('lists a person’s goals, each opening that goal’s sheet', async () => {
    serve();
    const kpi = { id: 'k1', name: 'Signups', unit: '', latest: { value: 12 }, link: { target: 20 } };
    queryClient.setQueryData(goalsTreeQuery.queryKey, {
      goals: [
        { id: 'g1', title: 'Grow signups', owner_id: pat.id, kpis: [kpi] },
        { id: 'g2', title: 'Ship kits', owner_id: ana.id, kpis: [] },
      ],
      other_kpis: [],
      owners: {},
      proposals: [],
      unaligned: {},
    } as unknown as GoalTree);
    show(<TeamWho who="p1" />, '/team/p1');
    await waitFor(() => expect(link('Grow signups')?.getAttribute('href')).toBe('/company/strategy/goals?panel=goal%3Ag1'));
    expect(screen.getByText('12 of 20')).toBeTruthy();
    expect(link('Ship kits')).toBeUndefined();
  });

  it('says so for an address that names no one', async () => {
    serve();
    show(<TeamWho who="nobody" />, '/team/nobody');
    expect(await screen.findByRole('heading', { name: 'No such teammate' })).toBeTruthy();
  });
});

describe('Loading a teammate', () => {
  beforeEach(teams);
  const read = async (who: string, tab?: Parameters<typeof loadWho>[2]) => {
    serve();
    await loadWho(queryClient, who, tab);
    return reads;
  };

  it('reads what the page it opens on draws first, before it draws', async () => {
    queryClient.removeQueries({ queryKey: agentsQuery.queryKey });
    expect(await read('ana')).toEqual([expect.stringContaining('/api/v2/tasks?owner=bot%3A0190-ana&status=open')]);
    expect(await read('ana', 'settings')).toEqual(['/api/employees']);
    expect(await read('p1', 'slack')).toEqual([expect.stringContaining('/humans/p1/slack')]);
    // a team's address reads nothing more: it goes home, the team shown in the menu
    serve();
    await expect(loadWho(queryClient, 'design', undefined)).rejects.toMatchObject({ options: { to: '/', search: { team: 'g1' } } });
    expect(reads).toEqual([]);
  });

  it('throws a failed read to the router, which draws the fault', async () => {
    serve(() => ({ status: 500, body: { detail: 'down' } }));
    await expect(loadWho(queryClient, 'ana', 'instructions')).rejects.toThrow();
  });

  it('sends a team home when the shell detaches from the in-flight directory read: the one read carries on', async () => {
    queryClient.removeQueries({ queryKey: directoryQuery.queryKey });
    const org = ok({ org_groups: [], people: [], bots: [] });
    let finish: (reply: Reply) => void = () => {};
    const first = new Promise<Reply>((resolve) => {
      finish = resolve;
    });
    serve((url) => (url !== '/api/v2/org' ? undefined : reads.filter((u) => u === '/api/v2/org').length === 1 ? first : org));
    const observer = new QueryObserver(queryClient, directoryQuery);
    const unsubscribe = observer.subscribe(() => {});
    const page = loadWho(queryClient, 'design', undefined);
    unsubscribe();
    finish(org);
    await expect(page).rejects.toMatchObject({ options: { to: '/', search: { team: 'g1' } } });
    expect(reads.filter((u) => u === '/api/v2/org')).toHaveLength(1);
  });
});

describe("An agent's column", () => {
  it('shows what it is working on, then its other open tasks, each opening the task @s6 @s606 @s608', async () => {
    queryClient.setQueryData(directoryQuery.queryKey, directory([{ ...ana, working: true }, pat, kim]));
    serve((url, method) => (url.endsWith('/api/v2/bots/ana') ? ok({ status: { task_id: 't1' } }) : tasksOf('open', [task('t1', 'Write the brief'), task('t2', 'Pick a font')])(url, method)));
    show(<TeamWho who="ana" />, '/team/ana');
    const working = await screen.findByRole('region', { name: /Working/ });
    expect((await within(working).findByText('Write the brief')).closest('a')?.getAttribute('href')).toBe('/task/t1');
    const tasks = screen.getByRole('region', { name: /Tasks/ });
    expect(within(tasks).getByText('Pick a font').closest('a')?.getAttribute('href')).toBe('/task/t2');
    expect(within(tasks).queryByText('Write the brief')).toBeNull();
  });

  it('opens All tasks filtered to it, on the pipeline Tasks opens @s608', async () => {
    serve((url, method) => (url.includes('/api/v2/pipelines') ? ok({ pipelines: [{ id: 'eng', name: 'Engineering', stages: [], revision: 1 }] }) : tasksOf('open', [task('t1', 'Pick a font')])(url, method)));
    show(<TeamWho who="ana" />, '/team/ana');
    const tasks = await screen.findByRole('region', { name: /Tasks/ });
    await waitFor(() => expect(within(tasks).getByRole('link', { name: /All tasks/ }).getAttribute('href')).toBe('/tasks/eng?owner=bot%3A0190-ana'));
  });

  it('shows its figures, and More pauses it against its revision @s600', async () => {
    serve(tasksOf('open', [task('t2', 'Pick a font')]));
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByText('Pick a font')).toBeTruthy();
    expect(screen.getByText('Turns today')).toBeTruthy();
    expect(screen.getByText('Spend this month')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => expect(posted('/api/v2/bots/ana/control')?.body).toMatchObject({ action: 'pause' }));
  });

  it('puts what waits on me first, under Waits on you @s607', async () => {
    serve(tasksOf('open', [task('t3', 'Approve the logo', { turn: { state: 'person', who: pat.id } })]));
    show(<TeamWho who="ana" />, '/team/ana');
    const waits = await screen.findByRole('region', { name: /Waits on you/ });
    expect(within(waits).getByText('Approve the logo')).toBeTruthy();
  });

  it('lists what it waits on from someone else, naming who @s609', async () => {
    serve(tasksOf('open', [task('t1', 'Approve the banner', { turn: { state: 'person', who: kim.id } })]));
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByRole('heading', { name: /Waiting/ })).toBeTruthy();
    expect(screen.getByText('Approve the banner')).toBeTruthy();
    expect(screen.getByText('On Kim Roe')).toBeTruthy();
  });

  it('opens what it finished this week from the Done fold @s610', async () => {
    serve(tasksOf('done', [task('t4', 'Sent the recap', { status: 'done' })]));
    show(<TeamWho who="ana" />, '/team/ana');
    const fold = await screen.findByRole('button', { name: '1 this week' });
    expect(screen.queryByText('Sent the recap')).toBeNull();
    fireEvent.click(fold);
    expect(screen.getByText('Sent the recap').closest('a')?.getAttribute('href')).toBe('/task/t4');
  });

  it('shows its latest update as lines @s613 @s1048', async () => {
    serve((url) => (url.includes('/api/v2/updates') ? ok({ updates: [{ id: 'u1', body: '- Shipped the banner\n- Next: the footer', created_at: '2026-11-18T10:00:00Z' }] }) : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    const update = await screen.findByRole('region', { name: /Latest update/ });
    expect(within(update).getByText('Shipped the banner')).toBeTruthy();
    expect(within(update).getByText('Next: the footer')).toBeTruthy();
  });

  it('turns to Settings from the gear @s619', async () => {
    serve();
    const router = show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'settings' }));
  });

  it('opens Add teammate over any page, onto the team given', async () => {
    serve(tasksOf('open', [task('t1', 'Pick a font')]));
    const router = show(<TeamWho who="ana" />, '/team/ana');
    await screen.findByRole('region', { name: /Tasks/ });
    expect(await run('team.addTeammate', { team: 'g1' }, { via: 'click' })).toMatchObject({ ok: true });
    await waitFor(() => expect(router.state.location.search).toEqual({ add: 'pick', team: 'g1' }));
  });
});

describe("An agent's Settings", () => {
  it('reopens the role editor with the saved role after cancelling a draft', async () => {
    const agent = { ...anaRow, description: 'Writes the brief' };
    serve((url) => (url === '/api/employees' ? ok([agent]) : undefined));
    queryClient.setQueryData(agentsQuery.queryKey, [agent]);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const edit = () => screen.findByRole('button', { name: /^Role/ });
    fireEvent.click(await edit());
    const field = await screen.findByRole<HTMLTextAreaElement>('textbox', { name: 'Role' });
    fireEvent.change(field, { target: { value: 'Unsent role' } });
    expect(field.value).toBe('Unsent role');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Role' })).toBeNull());

    fireEvent.click(await edit());
    expect((await screen.findByRole<HTMLTextAreaElement>('textbox', { name: 'Role' })).value).toBe('Writes the brief');
    expect(sent).toEqual([]);
  });

  it('reopens the model picker with the saved model after cancelling a draft', async () => {
    const agent = { ...anaRow, model: 'standard' };
    const models = { models: [{ id: 'standard', label: 'Standard' }, { id: 'advanced', label: 'Advanced' }], harnesses: [] };
    serve((url) => (url === '/api/employees' ? ok([agent]) : url === '/api/v2/models' ? ok(models) : undefined));
    queryClient.setQueryData(agentsQuery.queryKey, [agent]);
    queryClient.setQueryData(modelsQuery.queryKey, models);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const edit = () => screen.findByRole('button', { name: /^Model/ });
    fireEvent.click(await edit());
    fireEvent.click(await screen.findByRole('radio', { name: 'Advanced' }));
    expect(screen.getByRole('radio', { name: 'Advanced', checked: true })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model' })).toBeNull());

    fireEvent.click(await edit());
    expect(await screen.findByRole('radio', { name: 'Standard', checked: true })).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it('lists its runs and what it learned in one History, newest first @s618 @s638', async () => {
    serve((url) => {
      if (url.includes('/api/runs')) return ok([{ id: 'run1', issue: '12', state: 'done', outcome: 'success', finished_at: '2026-11-17T10:00:00Z' }]);
      if (url.includes('/bots/ana/memory')) return ok({ updates: [{ sha: 'a1', subject: 'Prefers short subject lines', when: '2026-11-18T10:00:00Z' }] });
      return undefined;
    });
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const history = await screen.findByRole('region', { name: 'History' });
    const learned = await within(history).findByText('Prefers short subject lines');
    expect(within(learned.closest('.row')! as HTMLElement).getByText('Learned')).toBeTruthy();
    const ran = within(history).getByText('Task 12');
    expect(ran.closest('a')?.getAttribute('href')).toBe('/task/12');
    expect(learned.compareDocumentPosition(ran) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('changes who it works for @s717', async () => {
    serve();
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /Works for/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Kim Roe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted('/bots/ana/owners')?.body).toEqual({ owners: ['p1', 'p2'], expected_revision: 3 }));
  });

  it('makes a credential for an agent that runs elsewhere, shown once and kept out of the cache @s723', async () => {
    const TOKEN = 'tok-made-up-9';
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...anaRow, agent: { harness: 'hermes', profile: 'ana' } } as AgentRow]);
    serve((url, method) => (method === 'POST' && url.endsWith('/bots/ana/agent-credential') ? ok({ token: TOKEN }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'New credential' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Make it' }));
    expect((await screen.findByText(new RegExp(TOKEN))).textContent).toContain(`--bot ana --token ${TOKEN}`);
    expect(screen.getByRole('button', { name: 'Copy token' })).toBeTruthy();
    expect(cached()).not.toContain(TOKEN);
  });

  it('shows its lines read-only to someone who may not manage it', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...anaRow, can_manage: false } as AgentRow]);
    serve();
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    expect(await screen.findByText('Studio')).toBeTruthy();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Role|Model)/ })).toBeNull();
  });

  it('sets where the conversation compacts from the model picker’s More', async () => {
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm-one', label: 'Model One' }], harnesses: [] } as never);
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...anaRow, model: 'm-one' } as AgentRow]);
    serve((url, method) => (method === 'POST' && url.endsWith('/compact-at') ? ok({ revision: 4 }) : undefined));
    show(<TeamWho who="ana" tab="model" />, '/team/ana?tab=model');
    const dialog = await screen.findByRole('dialog', { name: 'Model' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'More' }));
    await pick(within(dialog).getByRole('combobox', { name: 'Compact' }), '400K');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(posted('/bots/ana/compact-at')?.body).toEqual({ compact_at: 400000, expected_revision: 3 }));
    expect(sent.some((s) => s.url.includes('/transitions'))).toBe(false);
  });
});

describe("An agent's rules @s616", () => {
  const rules = (url: string) =>
    url.endsWith('/bots/ana/rules')
      ? ok({
          settings: [{ text: 'Answer within a day' }],
          internal: { rules: [{ id: 'r1', kind: 'internal', text: 'Ask before spending', status: 'active' }] },
          external: { rules: [{ id: 'r2', kind: 'external', text: 'Keep records a year', status: 'active' }] },
        })
      : undefined;

  it('starts a new rule for this agent on the Rules page', async () => {
    serve(rules);
    const router = show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: /Rules/ });
    fireEvent.click(within(section).getByRole('button', { name: 'New rule' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/company/rules'));
    expect(router.state.location.search).toEqual({ who: 'bot:0190-ana', panel: 'org:new' });
  });

  it('lists its own and the company’s, and opens one from its More, on the tab it lives on; its settings have none', async () => {
    serve(rules);
    const router = show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: /Rules/ });
    const outside = (await within(section).findByText('Keep records a year')).closest('.rule') as HTMLElement;
    const setting = within(section).getByText('Answer within a day').closest('.rule') as HTMLElement;
    expect(within(section).getByText('Ask before spending')).toBeTruthy();
    expect(within(setting).queryByRole('button', { name: 'More' })).toBeNull();
    fireEvent.click(within(outside).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/company/rules'));
    expect(router.state.location.search).toEqual({ tab: 'outside', panel: 'org:r2' });
  });
});

describe("An agent's instructions @s617", () => {
  const INS = '# Role\n\nYou write the weekly note.\n\nKeep it under 200 words.';
  const instructions = (url: string, method: string) =>
    method === 'GET' && url.endsWith('/bots/ana/instructions') ? ok({ content: INS, updated_at: '2026-11-17T10:00:00Z' }) : undefined;
  const toTico = () => posted('/api/v2/assistant/messages')?.body as { text: string } | undefined;

  it('reads the whole instructions, with a way back to Settings', async () => {
    serve((url) => (url.endsWith('/bots/ana/instructions') ? ok({ content: '# Voice\n\nWrite short.\n\n- One idea a line\n- No jargon' }) : undefined));
    show(<TeamWho who="ana" tab="instructions" />, '/team/ana?tab=instructions');
    expect(await screen.findByRole('heading', { name: 'Voice' })).toBeTruthy();
    expect(screen.getByText('Write short.')).toBeTruthy();
    expect(screen.getByText('No jargon')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to Settings' }).getAttribute('href')).toContain('tab=settings');
  });

  it('lets whoever manages it edit the whole text, and sends only the change to Tico', async () => {
    serve(instructions);
    show(<TeamWho who="ana" tab="instructions" />, '/team/ana?tab=instructions');
    expect(await screen.findByText('You write the weekly note.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const box = await screen.findByRole('textbox', { name: 'Instructions' });
    fireEvent.change(box, { target: { value: INS.replace('200 words', '150 words') } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toTico()).toBeTruthy());
    expect(toTico()!.text).toContain('Ana [ana]');
    expect(toTico()!.text).toContain('Replace this:\n\nKeep it under 200 words.\n\nwith this:\n\nKeep it under 150 words.');
    expect(toTico()!.text).not.toContain('You write the weekly note.');
  });

  it('gives anyone who may not manage it a request instead, and neither is open to Tico', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...anaRow, can_manage: false }]);
    serve(instructions);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'Request changes' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'What should change' }), { target: { value: 'Sign every note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(toTico()?.text).toContain('Ask whoever manages Ana [ana]'));
    expect(toTico()!.text).toContain('Sign every note');
    expect(await run('agents.editInstructions', { slug: 'ana', text: 'New' }, { via: 'click' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('agents.requestInstructions', { slug: 'ana', changes: 'x' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe("A person's page", () => {
  const everyone = { everyone: true, people: [], teams: [], bots: [] };
  const manages = (who: string) => (url: string, method: string) =>
    url.includes(`/humans/${who}/access`) && method === 'GET'
      ? ok({ see: everyone, read: everyone, write: everyone, revision: 7, target: `human:${who}`, you: { manage: true, see: true, read: true, write: true } })
      : undefined;

  it('opens what they finished this month from the Done fold, and keeps Settings on a phone @s601', async () => {
    serve(tasksOf('done', [task('t5', 'Signed the lease', { status: 'done', owner_id: kim.id })]));
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: '1 this month' }));
    expect(screen.getByText('Signed the lease')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Settings' }).hasAttribute('data-phone')).toBe(true);
  });

  it('marks only an open task Late; a done one stays grey', async () => {
    const late = (n: number, status: string) => ({ id: `t${n}`, number: n, title: `Task ${n}`, status, pipeline_id: 'eng', stage_id: null, owner_id: kim.id, late: true, turn: null, updated_at: new Date().toISOString() });
    serve((url, method) => tasksOf('open', [late(1, 'open')])(url, method) ?? tasksOf('done', [late(2, 'done')])(url, method));
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: '1 this month' }));
    expect(await screen.findByText('Task 2')).toBeTruthy();
    expect(screen.getAllByText('Late')).toHaveLength(1);
  });

  it('gives them a task: shown at once, sent with them as owner @s654', async () => {
    serve();
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: 'Give Kim Roe a task' }));
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Draft the brief' } });
    fireEvent.click(screen.getByRole('button', { name: 'Give it' }));
    expect(await screen.findByText('Draft the brief')).toBeTruthy();
    await waitFor(() => expect(posted('/api/v2/tasks')?.body).toEqual({ title: 'Draft the brief', owner: 'human:p2' }));
  });

  it('changes who they report to on pick and their GitHub, for whoever manages them; Tico never moves them @s625', async () => {
    serve(manages('p2'));
    show(<TeamWho who="p2" tab="settings" />, '/team/p2?tab=settings');
    expect(await screen.findByText('kim@example.com')).toBeTruthy();
    await pick(await screen.findByRole('combobox', { name: 'Reports to' }), 'Pat Doe');
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/humans/p2'))?.body).toEqual({ reports_to: 'p1' }));
    fireEvent.click(screen.getByRole('button', { name: /GitHub/ }));
    fireEvent.change(await screen.findByLabelText('GitHub login'), { target: { value: 'kimroe' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.filter((s) => s.url.endsWith('/api/v2/humans/p2')).at(-1)?.body).toEqual({ github: 'kimroe' }));
    expect(await run('people.setReportsTo', { person: 'p2', boss: 'p1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('changes who can reach them from Access, against its revision @s655', async () => {
    serve(manages('p2'));
    show(<TeamWho who="p2" tab="settings" />, '/team/p2?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /Access/ }));
    const read = await screen.findByRole('group', { name: 'Can read' });
    fireEvent.click(within(read).getByRole('checkbox', { name: 'Can read: everyone' }));
    fireEvent.click(within(read).getByRole('checkbox', { name: 'Pat Doe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.find((s) => s.method === 'PUT')?.body).toMatchObject({ read: { everyone: false, people: ['p1'] }, revision: 7 }));
    expect(sent.find((s) => s.method === 'PUT')?.url).toContain('/api/v2/humans/p2/access');
  });

  it('makes a password for someone, shown once and kept out of the cache @s751', async () => {
    const PASSWORD = 'pw-made-up-4';
    queryClient.setQueryData(accessQuery.queryKey, { you: 'admin', people: [{ id: 'p2', role: 'member' }] } as never);
    serve((url, method) => (method === 'POST' && url.endsWith('/humans/p2/password') ? ok({ person: 'p2', password: PASSWORD }) : undefined));
    show(<TeamWho who="p2" tab="settings" />, '/team/p2?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'Make a password' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Make it' }));
    expect(await screen.findByText(PASSWORD)).toBeTruthy();
    expect(cached()).not.toContain(PASSWORD);
  });
});

describe("A person's Slack @s626", () => {
  const thread = (n: number, body: string) => ({
    bot: '0190-ana',
    display_name: 'Ana',
    conversation_id: `c${n}`,
    channel: 'D1',
    last_message_at: `2026-11-1${n}T10:00:00Z`,
    messages: [
      { id: `m${n}a`, body: 'Earlier line', sender_id: 'human:p1' },
      { id: `m${n}b`, body, sender_id: 'bot:0190-ana' },
    ],
  });
  const slackOf = (threads: unknown[] | null, status = 200) => (url: string) =>
    url.includes('/api/v2/humans/p1/slack') ? { status, body: status === 200 ? { person: 'p1', threads } : { error: { code: 'forbidden' } } } : undefined;

  it('shows their newest Slack threads in the column, each opening its thread, and All threads opens the rest', async () => {
    serve(slackOf([thread(4, 'Can the price hold?'), thread(3, 'Who gets which offer'), thread(2, 'Three subject lines'), thread(1, 'Kits for Canada')]));
    show(<TeamWho who="p1" />, '/team/p1');
    const slack = await screen.findByRole('region', { name: /Slack/ });
    expect(within(slack).getByText('Can the price hold?').closest('a')?.getAttribute('href')).toBe('/threads/c4');
    expect(within(slack).getByText('Three subject lines')).toBeTruthy();
    expect(within(slack).queryByText('Kits for Canada')).toBeNull();
    expect(within(slack).getAllByText('Ana')).toHaveLength(3);
    expect(within(slack).getByRole('link', { name: /All threads/ }).getAttribute('href')).toBe('/team/p1?tab=slack');
  });

  it('lists every thread one step in', async () => {
    serve(slackOf([thread(4, 'Can the price hold?'), thread(1, 'Kits for Canada')]));
    show(<TeamWho who="p1" tab="slack" />, '/team/p1?tab=slack');
    const all = await screen.findByRole('region', { name: /Threads/ });
    expect(within(all).getByText('Kits for Canada').closest('a')?.getAttribute('href')).toBe('/threads/c1');
  });

  it('leaves the section out for someone who may not read it', async () => {
    serve(slackOf(null, 403));
    show(<TeamWho who="p1" />, '/team/p1');
    expect(await screen.findByRole('region', { name: /Tasks/ })).toBeTruthy();
    await waitFor(() => expect(queryClient.getQueryState(['team', 'slack', 'p1'])?.status).toBe('error'));
    expect(screen.queryByRole('region', { name: /Slack/ })).toBeNull();
  });

  // The router's fault page draws a 403 as no access, without Try again.
  it('hands a refused read one step in to the router as a 403', async () => {
    serve(slackOf(null, 403));
    await expect(loadWho(queryClient, 'p1', 'slack')).rejects.toMatchObject({ status: 403 });
  });

  it('says not connected when they have no Slack and no threads', async () => {
    serve((url) => (url.includes('/humans/p2/slack') ? ok({ person: 'p2', threads: [] }) : undefined));
    show(<TeamWho who="p2" />, '/team/p2');
    const slack = await screen.findByRole('region', { name: /Slack/ });
    expect(within(slack).getByText('Not connected')).toBeTruthy();
  });
});

describe('Add teammate', () => {
  it('offers a new agent, one from a template and inviting a person, then goes to the one picked @s656', async () => {
    serve();
    show(<AddAgentButton team="g1" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Add agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add teammate' });
    expect(within(dialog).getByRole('button', { name: /Invite a person/ })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: /New agent/ })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: /From a template/ }));
    expect(await screen.findByRole('dialog', { name: 'From a template' })).toBeTruthy();
  });

  it('adds a new agent with its role on the team @s633 @s718', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/bots') ? ok({ slug: 'max' }) : undefined));
    show(<AddTeammate step="new" team="g1" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Max' } });
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'Writes the posts' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/groups/g1'))?.body).toEqual({ add: { bots: ['max'] } }));
    expect(sent[0]?.body).toEqual({ display_name: 'Max', description: 'Writes the posts', status: 'active' });
  });

  it('adds an agent from the template picked @s631 @s712', async () => {
    serve((url, method) => {
      if (url.includes('/api/v2/templates')) return ok({ cards: [{ slug: 'writer', name: 'Writer', summary: 'Writes posts' }, { slug: 'scout', name: 'Scout', summary: 'Finds leads' }] });
      if (method === 'POST' && url.endsWith('/api/v2/bots')) return ok({ slug: 'writer' });
      return undefined;
    });
    show(<AddTeammate step="template" team="" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.click(await screen.findByRole('option', { name: /Writer/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
    await waitFor(() => expect(sent[0]?.body).toEqual({ display_name: 'Writer', description: '', status: 'active', template: 'writer' }));
  });

  it('invites a person by email onto the team @s662', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/access/humans') ? ok({ person: 'p9' }) : undefined));
    show(<AddTeammate step="person" team="g1" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'lee@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send invite' }));
    await waitFor(() => expect(sent.map((s) => s.url.replace(/^.*\/api/, '/api'))).toEqual(['/api/v2/access/humans', '/api/v2/access/humans/p9/invite']));
    expect(sent[0]?.body).toEqual({ email: 'lee@example.com', name: '', team: 'g1' });
  });
});

describe('Cloud agents @s629 @s733 @s734 @s735', () => {
  beforeEach(onModels);
  const agents = [
    { id: 'claude', name: 'Claude Code', maker: 'Anthropic', site: 'claude.com', line: 'Codes in your repo', supported: true, connect_id: 'claude' },
    { id: 'devin', name: 'Devin', maker: 'Cognition', site: 'devin.ai', line: 'Works in its own VM', supported: false, connect_id: null },
  ];
  const seat = { id: 's9', kind: 'seat', stage: 'recruiting', title: 'Devin', description: '', reports_to: 'human:p1', team: 'g1', agent: 'devin', agent_name: 'Devin', request_state: 'requested' };
  const cloud = (seats: unknown[] = [], more?: (url: string, method: string) => Reply | undefined) => {
    serve((url, method) => more?.(url, method) ?? (url.endsWith('/api/v2/cloud-agents') ? ok({ agents }) : url.endsWith('/api/v2/seats') ? ok({ seats }) : undefined));
    return show(<AddTeammate step="cloud" team="g1" onStep={() => {}} onClose={() => {}} />, '/team/design');
  };
  const row = async (name: string) => (await screen.findByText(name)).closest('.cloud') as HTMLElement;

  it('is a line in Add teammate that opens its step', async () => {
    serve();
    const onStep = vi.fn();
    show(<AddTeammate step="pick" team="g1" onStep={onStep} onClose={() => {}} />, '/team/design');
    fireEvent.click(await screen.findByRole('button', { name: 'Cloud agents' }));
    expect(onStep).toHaveBeenCalledWith('cloud');
    expect(screen.queryByText('Coming soon')).toBeNull();
  });

  it('adds a supported one under the team lead and opens its page', async () => {
    const router = cloud([], (url, method) => (method === 'POST' && url.includes('/cloud-agents/claude/add') ? ok({ bot: 'b9', slug: 'claude-code', seat: 's1', harness: 'claude', model: 'm' }) : undefined));
    expect(within(await row('Devin')).queryByRole('button', { name: 'Add' })).toBeNull();
    fireEvent.click(within(await row('Claude Code')).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(posted('/cloud-agents/claude/add')?.body).toEqual({ team: 'g1', reports_to: 'human:p1' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/claude-code'));
  });

  it('requests one Tico does not connect with its first task, then opens its Connecting page', async () => {
    const router = cloud([], (url, method) => (method === 'POST' && url.includes('/cloud-agents/devin/request') ? ok(seat) : undefined));
    fireEvent.click(within(await row('Devin')).getByRole('button', { name: 'Request' }));
    const ask = await screen.findByRole('dialog', { name: 'Request Devin' });
    fireEvent.change(within(ask).getByRole('textbox', { name: 'First task' }), { target: { value: '  Port the checkout tests  ' } });
    fireEvent.click(within(ask).getByRole('button', { name: 'Request' }));
    await waitFor(() =>
      expect(posted('/cloud-agents/devin/request')?.body).toEqual({ team: 'g1', reports_to: 'human:p1', first_task: 'Port the checkout tests' }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/seat%3As9'));
  });

  it('requests another agent by the name typed, and Back returns to the list', async () => {
    cloud([], (url, method) => (method === 'POST' && url.includes('/cloud-agents/other/request') ? ok({ ...seat, agent: 'other', title: 'Factory' }) : undefined));
    fireEvent.click(within(await row('Another agent')).getByRole('button', { name: 'Request' }));
    let ask = await screen.findByRole('dialog', { name: 'Another agent' });
    expect(within(ask).getByRole('button', { name: 'Request' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(within(ask).getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog', { name: 'Cloud agents' })).toBeTruthy();
    fireEvent.click(within(await row('Another agent')).getByRole('button', { name: 'Request' }));
    ask = await screen.findByRole('dialog', { name: 'Another agent' });
    fireEvent.change(within(ask).getByRole('textbox', { name: 'Name' }), { target: { value: 'Factory' } });
    fireEvent.click(within(ask).getByRole('button', { name: 'Request' }));
    await waitFor(() => expect(posted('/cloud-agents/other/request')?.body).toEqual({ team: 'g1', reports_to: 'human:p1', first_task: '', name: 'Factory' }));
  });

  it('marks a pending request Requested, and its ⋯ opens the Connecting page', async () => {
    const router = cloud([seat]);
    const devin = await row('Devin');
    expect(await within(devin).findByText('Requested')).toBeTruthy();
    expect(within(devin).queryByRole('button', { name: 'Request' })).toBeNull();
    fireEvent.click(within(devin).getByRole('button', { name: 'More on Devin' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/seat%3As9'));
  });

  it('cancels a pending request from its ⋯', async () => {
    cloud([seat], (url, method) => (method === 'POST' && url.includes('/seats/s9/cancel-request') ? ok({ id: 's9', closed: true, request_state: 'canceled' }) : undefined));
    const devin = await row('Devin');
    expect(await within(devin).findByText('Requested')).toBeTruthy();
    fireEvent.click(within(devin).getByRole('button', { name: 'More on Devin' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel request' }));
    await waitFor(() => expect(posted('/seats/s9/cancel-request')?.method).toBe('POST'));
  });

  it('draws a requested seat’s Connecting page: its steps, its facts and the task waiting @s736', async () => {
    serve((url) =>
      url.endsWith('/api/v2/seats/s9/request')
        ? ok({ seat: { ...seat, request_state: 'building', created_at: '2026-11-11T19:58:00Z' }, agent: agents[1], steps: ['requested', 'building', 'ready'], tasks: [{ id: 't7', title: 'Port the checkout tests', status: 'open' }] })
        : undefined,
    );
    show(<TeamWho who="seat:s9" />, '/team/seat%3As9');
    expect(await screen.findByRole('heading', { name: 'Devin' })).toBeTruthy();
    const steps = screen.getByRole('list', { name: 'Connecting' });
    expect(within(steps).getByText('Building').closest('li')?.getAttribute('aria-current')).toBe('step');
    expect(within(steps).getByText('Requested').closest('li')?.className).toBe('is-done');
    expect(screen.getByRole('link', { name: 'devin.ai' }).getAttribute('href')).toBe('https://devin.ai');
    expect(await screen.findByText('Pat Doe')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Waiting for Devin' }).contains(link('Port the checkout tests')!)).toBe(true);
    expect(link('Port the checkout tests')?.getAttribute('href')).toBe('/task/t7');
  });

  it('cancels from the Connecting page and goes home, its team shown in the menu', async () => {
    serve((url, method) => {
      if (url.endsWith('/api/v2/seats/s9/request')) return ok({ seat, agent: agents[1], steps: ['requested', 'building', 'ready'], tasks: [] });
      if (url.endsWith('/api/v2/seats')) return ok({ seats: [seat] });
      if (method === 'POST' && url.includes('/seats/s9/cancel-request')) return ok({ id: 's9', closed: true, request_state: 'canceled' });
    });
    const router = show(<TeamWho who="seat:s9" />, '/team/seat%3As9');
    expect(await screen.findByRole('heading', { name: 'Devin' })).toBeTruthy();
    fireEvent.click(within(document.querySelector<HTMLElement>('.pk-head')!).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel request' }));
    await waitFor(() => expect(posted('/seats/s9/cancel-request')?.method).toBe('POST'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(router.state.location.search).toEqual({ team: 'g1' });
  });

  it('says so for a request that is gone', async () => {
    serve((url) => (url.endsWith('/api/v2/seats/s0/request') ? { status: 404, body: { code: 'not_found', detail: 'No cloud agent is requested for this seat' } } : undefined));
    show(<TeamWho who="seat:s0" />, '/team/seat%3As0');
    expect(await screen.findByRole('heading', { name: 'No such request' })).toBeTruthy();
  });

});

describe('A computer', () => {
  it('lists its agents and restarts it, asked first, when it is offline @s729', async () => {
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, online: false }]);
    serve();
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    const on = await screen.findByRole('region', { name: /Agents on this computer/ });
    expect(within(on).getByRole('link', { name: 'Ana' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Restart Studio?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(sent.some((s) => s.method === 'POST' && s.url.endsWith('/api/v2/computers/r1/restart'))).toBe(true));
  });

  it('marks each agent stopped while it is offline, and the All agents row sets one model for every agent @s729', async () => {
    queryClient.setQueryData(directoryQuery.queryKey, directory([ana, bo, pat, kim]));
    queryClient.setQueryData(agentsQuery.queryKey, [anaRow, { id: '0191-bo', slug: 'bo', display_name: 'Bo', revision: 5, model: 'm-two', machine: { runner_id: 'r1', label: 'Studio', generation: 2 } } as AgentRow]);
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, online: false, bots: ['0190-ana', '0191-bo'] }]);
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm-one', label: 'Model One' }, { id: 'm-two', label: 'Model Two' }], harnesses: [] } as never);
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? ok({ id: 'tr' }) : undefined));
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    const on = await screen.findByRole('region', { name: /Agents on this computer/ });
    expect(within(on).getAllByText('Stopped')).toHaveLength(2);
    fireEvent.click(within(on).getByRole('button', { name: 'Model for all agents' }));
    const dialog = await screen.findByRole('dialog', { name: 'Model' });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Model One' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    // Neither runs Model One yet: both move.
    await waitFor(() => expect(sent.filter((s) => s.url.includes('/transitions')).map((s) => s.url)).toEqual([
      expect.stringContaining('/bots/ana/transitions'),
      expect.stringContaining('/bots/bo/transitions'),
    ]));
  });

  it("says how it is doing in one status line, CPU and memory among it, on the computer's page @s686", async () => {
    onModels();
    serve((url) => (url.includes('/computers/r1/performance') ? ok({ points: [[1, 10, 12, 40, 41, 0.5, 0, 0, 0], [2, 30, 35, 50, 52, 1.25, 0, 0, 1]] }) : undefined));
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    expect(await screen.findByText('Online')).toBeTruthy();
    expect(await screen.findByText('CPU 30%')).toBeTruthy();
    expect(screen.getByText('Memory 50%')).toBeTruthy();
  });

  it('adds a sign-in for a tool every agent on it uses @s730', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/computers/r1/logins') ? ok({ id: 'l1', runner_id: 'r1', state: 'starting' }) : undefined));
    show(<SignIns c={studio} />, '/team/ana?tab=computer');
    fireEvent.click(await screen.findByRole('button', { name: 'Add a sign-in' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a sign-in' });
    await pick(within(dialog).getByRole('combobox', { name: 'AI tool' }), 'Codex');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(posted('/api/v2/computers/r1/logins')?.body).toEqual({ runtime: 'codex' }));
  });

  it('names the agents a sign-out stops before it signs out @s732', async () => {
    onClaude();
    serve((url) => (url.includes('/logouts/bots') ? ok({ bots: [{ bot: '0190-ana', name: 'Ana' }] }) : undefined));
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    expect(await screen.findByText('40% this week')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of Claude Code' }));
    expect(await screen.findByText('Ana will stop until someone signs in again.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(posted('/computers/r1/logouts')?.body).toEqual({ runtime: 'claude' }));
  });

  it('lets Tico turn a screen off or to view only, never on to control', async () => {
    withBo();
    serve((_url, method) => (method === 'GET' ? undefined : ok({ mode: 'off' })));
    show(<span />, '/team/ana');
    expect((await run('computers.setScreen', { computer: 'r1', mode: 'off' }, { via: 'tico' })).ok).toBe(true);
    expect((await run('computers.setScreen', { computer: 'r1', mode: 'view' }, { via: 'tico' })).ok).toBe(true);
    expect(await run('computers.setScreen', { computer: 'r1', mode: 'control' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Copies of an agent', () => {
  const copy: Actor = { id: 'bot:0191-anb', kind: 'agent', name: 'Ana', slug: 'ana-pat' };
  const family = (branches: unknown[]) => ok({ original: '0190-ana', shared: true, branches });
  const mine = (profile: object) => ({ id: '0191-anb', slug: 'ana-pat', owner_id: 'p1', status: 'active', revision: 4, profile });
  beforeEach(() => {
    onClaude([{ ...claude, owner_id: 'p1' }, { ...annex(claude), owner_id: 'p1' }]);
    queryClient.setQueryData(directoryQuery.queryKey, directory([ana, pat, kim, copy]));
    queryClient.setQueryData(agentsQuery.queryKey, [anaRow, { id: '0191-anb', slug: 'ana-pat', display_name: 'Ana', revision: 4, owner_id: 'p1' } as AgentRow]);
  });

  it('opens my copy on its Settings: what it follows, its status and profile @s649', async () => {
    serve((url) => (url.includes('/bots/ana-pat/branches') ? family([mine({ name: 'doer', label: 'Doer', summary: 'Does the work' })]) : undefined));
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    const follows = await screen.findByRole('link', { name: /Ana/ });
    expect(follows.getAttribute('href')).toBe('/team/ana');
    expect(await screen.findByRole('button', { name: 'Copy: Your copy' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Status' }).textContent).toContain('Active');
    expect(screen.getByRole('combobox', { name: 'Profile' }).textContent).toContain('Does the work');
  });

  it('makes my copy of a shared agent on my computer that does not run it', async () => {
    serve((url, method) => {
      if (url.includes('/bots/ana/branches')) return family([]);
      if (method === 'POST' && url.includes('/bots/ana/copies')) return { status: 201, body: mine({ name: 'custom', label: 'Custom' }) };
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    // with no copy yet, Make my copy is in ⋯ (the pick beside the name comes with the first copy)
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Make my copy' }));
    expect(await screen.findByRole('combobox', { name: 'Computer' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Make copy' }));
    await waitFor(() => expect(posted('/bots/ana/copies')?.body).toEqual({ runner_id: 'r2' }));
  });

  it('makes a planned copy when I have no computer for it, then opens it on Settings @s650', async () => {
    queryClient.setQueryData(computersQuery.queryKey, [{ ...screenStudio, owner_id: 'p2' }]);
    serve((url, method) => {
      if (url.includes('/bots/ana/branches')) return family([]);
      if (method === 'POST' && url.includes('/bots/ana/copies')) return { status: 201, body: { ...mine({ name: 'doer', label: 'Doer' }), status: 'planned' } };
      return undefined;
    });
    const router = show(<TeamWho who="ana" />, '/team/ana');
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

  it('pauses my copy against its revision', async () => {
    serve((url) => (url.includes('/bots/ana-pat/branches') ? family([mine({ name: 'custom', label: 'Custom' })]) : undefined));
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    expect(await screen.findByRole('link', { name: /Ana/ })).toBeTruthy();
    await pick(await screen.findByRole('combobox', { name: 'Status' }), 'Paused');
    await waitFor(() => expect(posted('/bots/ana-pat/definition')?.body).toEqual({ status: 'paused', expected_revision: 4 }));
  });

  it('asks before leaving Custom drops its own wake rules, then sends the change again', async () => {
    let tries = 0;
    serve((url, method) => {
      if (url.includes('/bots/ana-pat/branches')) return family([mine({ name: 'custom', label: 'Custom' })]);
      if (url.includes('/api/v2/profiles')) return ok({ profiles: [{ name: 'custom', label: 'Custom' }, { name: 'doer', label: 'Doer' }] });
      if (method === 'POST' && url.includes('/bots/ana-pat/profile'))
        return tries++ ? ok() : { status: 409, body: { error: { code: 'conflict', detail: 'Wake rules would be deleted' } } };
      return undefined;
    });
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    await pick(await screen.findByRole('combobox', { name: 'Profile' }), 'Doer');
    fireEvent.click(await screen.findByRole('button', { name: 'Leave Custom' }));
    await waitFor(() => expect(sent.filter((s) => s.url.includes('/profile')).map((s) => s.body)).toEqual([
      { profile: 'doer', expected_revision: 4 },
      { profile: 'doer', expected_revision: 4, confirm_drop_rules: true },
    ]));
  });
});

describe('Move agent, and Change model settings @s715', () => {
  beforeEach(onModels);

  it('changes the model as a transition against its generation and revision', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? ok({ id: 'tr1' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Model/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Model' });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Model Two' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ kind: 'model', model: 'm-two', expected_generation: 2, expected_revision: 3 }));
  });

  it('opens on what it runs, with Apply off until a pick differs', async () => {
    serve();
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Model/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Model' }));
    expect(dialog.getByRole('radio', { name: 'Model One' }).getAttribute('aria-checked')).toBe('true');
    expect(dialog.getByRole('button', { name: 'Apply' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(dialog.getByRole('radio', { name: 'Model Two' }));
    expect(dialog.getByRole('radio', { name: 'Model Two' }).getAttribute('aria-checked')).toBe('true');
    expect(dialog.getByRole('button', { name: 'Apply' }).hasAttribute('disabled')).toBe(false);
    fireEvent.click(dialog.getByRole('radio', { name: 'Model One' }));
    expect(dialog.getByRole('button', { name: 'Apply' }).hasAttribute('disabled')).toBe(true);
  });

  it('uses the new configuration revision when applying a model and compact size together', async () => {
    serve((url, method) => (method !== 'POST' ? undefined : url.endsWith('/compact-at') ? ok({ revision: 4 }) : url.endsWith('/transitions') ? ok({ id: 'tr1' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /^Model/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Model' }));
    fireEvent.click(dialog.getByRole('radio', { name: 'Model Two' }));
    fireEvent.click(dialog.getByRole('button', { name: 'More' }));
    await pick(dialog.getByRole('combobox', { name: 'Compact' }), '400K');
    fireEvent.click(dialog.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ model: 'm-two', expected_revision: 4 }));
    expect(sent[0]?.url).toMatch(/\/compact-at$/);
    expect(sent[0]?.body).toEqual({ compact_at: 400000, expected_revision: 3 });
  });

  it('moves it to another computer the same way, on pick', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? ok({ id: 'tr2' }) : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    await pick(await screen.findByRole('combobox', { name: 'Computer' }), 'Annex');
    await waitFor(() => expect(posted('/bots/ana/transitions')?.body).toMatchObject({ kind: 'machine', runner_id: 'r2', expected_generation: 2, expected_revision: 3 }));
  });

  it('a move shows the agent on its new computer at once', async () => {
    serve();
    await act(() => moveAgent('ana', 'r2'));
    expect(sent[0]).toMatchObject({ url: expect.stringContaining('/api/v2/bots/ana/transitions'), body: { kind: 'machine', runner_id: 'r2', expected_generation: 2, expected_revision: 3 } });
    expect(queryClient.getQueryData(agentsQuery.queryKey)?.[0]?.machine?.runner_id).toBe('r2');
  });
});

describe('Archive agent @s716', () => {
  beforeEach(withBo);

  it('from Tico with nothing on screen, reads the revision first instead of sending 1', async () => {
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

  it('asks first, hands its open tasks to the agent picked in "Hand its work to", archives it against its revision and leaves its page', async () => {
    serve((url, method) => (method === 'POST' && url.includes('/archive') ? ok({}) : undefined));
    const router = show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive Ana' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Archive Ana?' });
    expect(within(dialog).getByRole('combobox', { name: 'Hand its work to' }).textContent).toContain('Pat Doe');
    await pick(within(dialog).getByRole('combobox'), 'Bo');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(posted('/bots/ana/archive')?.body).toEqual({ successor: '0191-bo', expected_revision: 3 }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
  });
});
