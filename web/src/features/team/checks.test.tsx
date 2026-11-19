// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { AddAgentButton, AddTeammate } from './AddTeammate';
import { accessQuery, agentsQuery, computersQuery, groupsQuery, modelsQuery, peopleQuery, type AgentRow, type Computer, type Group } from './queries';
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
function serve(reply: (url: string, method: string) => Reply | undefined) {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = reply(u, method) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return { status: 200, body: groups };
  if (url.includes('/api/employees')) return { status: 200, body: roster };
  if (url.includes('/api/runs')) return { status: 200, body: [] };
  if (url.includes('/api/v2/tasks')) return { status: 200, body: { tasks: [] } };
  return { status: 200, body: {} };
};
const tasksOf = (status: string, tasks: unknown[]) => (url: string, method: string) =>
  method === 'GET' && url.includes('/api/v2/tasks') && url.includes(`status=${status}`) ? { status: 200, body: { tasks } } : undefined;

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]));
async function pick(box: HTMLElement, name: string) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const kim: Actor = { id: 'human:p2', kind: 'person', name: 'Kim Roe' };
const directory = (actors: Actor[]) => ({ actors, byId: Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]])) });
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1', 'p2'] }];
const roster: AgentRow[] = [
  { id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, machine: { runner_id: 'r1', label: 'Studio', generation: 2 }, users: [{ id: 'p1', name: 'Pat Doe' }] } as AgentRow,
];
const studio = {
  id: 'r1',
  label: 'Studio',
  online: true,
  bots: ['0190-ana'],
  readiness: { runtimes: { codex: { installed: true, authenticated: 'missing' } } },
} as unknown as Computer;
const task = (id: string, title: string, more: object = {}) => ({ id, number: null, title, status: 'open', owner_id: ana.id, updated_at: '2026-11-01T00:00:00Z', ...more });
const everyone = { everyone: true, people: [], teams: [], bots: [] };
const manages = (who: string) => (url: string, method: string) =>
  url.includes(`/humans/${who}/access`) && method === 'GET'
    ? { status: 200, body: { see: everyone, read: everyone, write: everyone, revision: 7, target: `human:${who}`, you: { manage: true, see: true, read: true, write: true } } }
    : undefined;

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, directory([ana, pat, kim]));
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, [
    { id: 'p1', name: 'Pat Doe' },
    { id: 'p2', name: 'Kim Roe', email: 'kim@example.com' },
  ]);
  queryClient.setQueryData(computersQuery.queryKey, [studio]);
  queryClient.setQueryData(modelsQuery.queryKey, { models: [], harnesses: [{ id: 'codex', name: 'Codex', capabilities: { browser_sign_in: true } }] });
});
afterEach(() => vi.unstubAllGlobals());

describe("An agent's column", () => {
  it('shows what it is working on, then its other open tasks, each opening the task @s6 @s606 @s608', async () => {
    queryClient.setQueryData(directoryQuery.queryKey, directory([{ ...ana, working: true }, pat, kim]));
    serve((url, method) =>
      url.endsWith('/api/v2/bots/ana') ? { status: 200, body: { status: { task_id: 't1' } } } : tasksOf('open', [task('t1', 'Write the brief'), task('t2', 'Pick a font')])(url, method),
    );
    show(<TeamWho who="ana" />, '/team/ana');
    const working = await screen.findByRole('region', { name: /Working/ });
    expect((await within(working).findByText('Write the brief')).closest('a')?.getAttribute('href')).toBe('/task/t1');
    const tasks = screen.getByRole('region', { name: /Tasks/ });
    expect(within(tasks).getByText('Pick a font').closest('a')?.getAttribute('href')).toBe('/task/t2');
    expect(within(tasks).queryByText('Write the brief')).toBeNull();
  });

  it('shows its figures, and More pauses it against its revision @s600', async () => {
    serve(tasksOf('open', [task('t2', 'Pick a font')]));
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByText('Pick a font')).toBeTruthy();
    expect(screen.getByText('Turns today')).toBeTruthy();
    expect(screen.getByText('Spend this month')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/bots/ana/control'))?.body).toMatchObject({ action: 'pause' }));
  });

  it('puts what waits on me first, under Waits on you @s607', async () => {
    serve(tasksOf('open', [task('t3', 'Approve the logo', { turn: { state: 'person', who: pat.id } })]));
    show(<TeamWho who="ana" />, '/team/ana');
    const waits = await screen.findByRole('region', { name: /Waits on you/ });
    expect(within(waits).getByText('Approve the logo')).toBeTruthy();
  });

  it('opens what it finished this week from the Done fold @s610', async () => {
    serve((url, method) => tasksOf('done', [task('t4', 'Sent the recap', { status: 'done' })])(url, method));
    show(<TeamWho who="ana" />, '/team/ana');
    const fold = await screen.findByRole('button', { name: '1 this week' });
    expect(screen.queryByText('Sent the recap')).toBeNull();
    fireEvent.click(fold);
    expect(screen.getByText('Sent the recap').closest('a')?.getAttribute('href')).toBe('/task/t4');
  });

  it('shows its latest update as lines @s613 @s1048', async () => {
    serve((url) =>
      url.includes('/api/v2/updates') ? { status: 200, body: { updates: [{ id: 'u1', body: '- Shipped the banner\n- Next: the footer', created_at: '2026-11-18T10:00:00Z' }] } } : undefined,
    );
    show(<TeamWho who="ana" />, '/team/ana');
    const update = await screen.findByRole('region', { name: /Latest update/ });
    expect(within(update).getByText('Shipped the banner')).toBeTruthy();
    expect(within(update).getByText('Next: the footer')).toBeTruthy();
  });

  it('turns to Settings from the gear @s619', async () => {
    serve(() => undefined);
    const router = show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'settings' }));
  });
});

describe("An agent's Settings", () => {
  it('lists its rules, its own and the company’s @s616', async () => {
    serve((url) =>
      url.endsWith('/bots/ana/rules')
        ? { status: 200, body: { settings: [{ text: 'Answer within a day' }], internal: { rules: [{ id: 'r1', kind: 'internal', text: 'Ask before spending', status: 'active' }] } } }
        : undefined,
    );
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const rules = await screen.findByRole('region', { name: /Rules/ });
    expect(await within(rules).findByText('Answer within a day')).toBeTruthy();
    expect(within(rules).getByText('Ask before spending')).toBeTruthy();
  });

  it('lists its runs and what it learned in one History, newest first @s618 @s638', async () => {
    serve((url) => {
      if (url.includes('/api/runs')) return { status: 200, body: [{ id: 'run1', issue: '12', state: 'done', outcome: 'success', finished_at: '2026-11-17T10:00:00Z' }] };
      if (url.includes('/bots/ana/memory')) return { status: 200, body: { updates: [{ sha: 'a1', subject: 'Prefers short subject lines', when: '2026-11-18T10:00:00Z' }] } };
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

  it('reads the whole instructions, with a way back to Settings @s617', async () => {
    serve((url) => (url.endsWith('/bots/ana/instructions') ? { status: 200, body: { content: '# Voice\n\nWrite short.\n\n- One idea a line\n- No jargon' } } : undefined));
    show(<TeamWho who="ana" tab="instructions" />, '/team/ana?tab=instructions');
    expect(await screen.findByRole('heading', { name: 'Voice' })).toBeTruthy();
    expect(screen.getByText('Write short.')).toBeTruthy();
    expect(screen.getByText('No jargon')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to Settings' }).getAttribute('href')).toContain('tab=settings');
  });

  it('makes a credential for an agent that runs elsewhere, shown once and kept out of the cache @s723', async () => {
    const TOKEN = 'tok-made-up-9';
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0], agent: { harness: 'hermes', profile: 'ana' } } as AgentRow]);
    serve((url, method) => (method === 'POST' && url.endsWith('/bots/ana/agent-credential') ? { status: 200, body: { token: TOKEN } } : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'New credential' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Make it' }));
    expect((await screen.findByText(new RegExp(TOKEN))).textContent).toContain(`--bot ana --token ${TOKEN}`);
    expect(screen.getByRole('button', { name: 'Copy token' })).toBeTruthy();
    expect(cached()).not.toContain(TOKEN);
  });

});

describe("A person's page", () => {
  it('opens what they finished this month from the Done fold, and keeps Settings on a phone @s601', async () => {
    serve(tasksOf('done', [task('t5', 'Signed the lease', { status: 'done', owner_id: kim.id })]));
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: '1 this month' }));
    expect(screen.getByText('Signed the lease')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Settings' }).hasAttribute('data-phone')).toBe(true);
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

  it('changes who can reach them from Access @s655', async () => {
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
    serve((url, method) => (method === 'POST' && url.endsWith('/humans/p2/password') ? { status: 200, body: { person: 'p2', password: PASSWORD } } : undefined));
    show(<TeamWho who="p2" tab="settings" />, '/team/p2?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'Make a password' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Make it' }));
    expect(await screen.findByText(PASSWORD)).toBeTruthy();
    expect(cached()).not.toContain(PASSWORD);
  });
});

describe('Add teammate', () => {
  it('offers a new agent, one from a template and inviting a person, then goes to the one picked @s656', async () => {
    serve(() => undefined);
    show(<AddAgentButton team="g1" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Add agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add teammate' });
    expect(within(dialog).getByRole('button', { name: /Invite a person/ })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: /New agent/ })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: /From a template/ }));
    expect(await screen.findByRole('dialog', { name: 'From a template' })).toBeTruthy();
  });

  it('adds a new agent with its role on the team @s633 @s718', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/bots') ? { status: 200, body: { slug: 'max' } } : undefined));
    show(<AddTeammate step="new" team="g1" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Max' } });
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'Writes the posts' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/groups/g1'))?.body).toEqual({ add: { bots: ['max'] } }));
    expect(sent[0]?.body).toEqual({ display_name: 'Max', description: 'Writes the posts', status: 'active' });
  });

  it('adds an agent from the template picked @s631 @s712', async () => {
    serve((url, method) => {
      if (url.includes('/api/v2/templates')) return { status: 200, body: { cards: [{ slug: 'writer', name: 'Writer', summary: 'Writes posts' }, { slug: 'scout', name: 'Scout', summary: 'Finds leads' }] } };
      if (method === 'POST' && url.endsWith('/api/v2/bots')) return { status: 200, body: { slug: 'writer' } };
      return undefined;
    });
    show(<AddTeammate step="template" team="" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.click(await screen.findByRole('option', { name: /Writer/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
    await waitFor(() => expect(sent[0]?.body).toEqual({ display_name: 'Writer', description: '', status: 'active', template: 'writer' }));
  });

  it('invites a person by email onto the team @s662', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/access/humans') ? { status: 200, body: { person: 'p9' } } : undefined));
    show(<AddTeammate step="person" team="g1" onStep={() => {}} onClose={() => {}} />, '/team/design');
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'lee@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send invite' }));
    await waitFor(() => expect(sent.map((s) => s.url.replace(/^.*\/api/, '/api'))).toEqual(['/api/v2/access/humans', '/api/v2/access/humans/p9/invite']));
    expect(sent[0]?.body).toEqual({ email: 'lee@example.com', name: '', team: 'g1' });
  });
});

describe('A team', () => {
  it('has Teammates and Rules tabs, the month’s spend, the mock’s pills and its open seats', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0], status: 'paused' } as AgentRow]);
    serve((url) =>
      url.includes('/api/humans')
        ? { status: 200, body: { seats: [{ id: 's1', title: 'Security Owner', team: 'g1' }, { id: 's2', title: 'Elsewhere', team: 'g9' }] } }
        : url.includes('/api/v2/usage')
          ? { status: 200, body: { totals: { est_cost_usd: 782 } } }
          : undefined,
    );
    show(<TeamWho who="design" />, '/team/design');
    expect((await screen.findByRole('link', { name: 'Rules' })).getAttribute('href')).toBe('/company/rules?who=group%3Ag1');
    expect(screen.getByRole('link', { name: 'Teammates' })).toBeTruthy();
    expect(await screen.findByText('$782')).toBeTruthy();
    expect(await screen.findByText('Security Owner')).toBeTruthy();
    expect(screen.queryByText('Elsewhere')).toBeNull();
    expect(screen.getByText('Stopped')).toBeTruthy();
  });
});

describe('Picks save on select', () => {
  it('shows an agent’s lines read-only to someone who may not manage it', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0], can_manage: false } as AgentRow]);
    serve(() => undefined);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    expect(await screen.findByText('Studio')).toBeTruthy();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Role|Model)/ })).toBeNull();
  });

  it('sets where the conversation compacts from the model picker’s More', async () => {
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm-one', label: 'Model One' }], harnesses: [] } as never);
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0], model: 'm-one' } as AgentRow]);
    serve((url, method) => (method === 'POST' && url.endsWith('/compact-at') ? { status: 200, body: {} } : undefined));
    show(<TeamWho who="ana" tab="model" />, '/team/ana?tab=model');
    const dialog = await screen.findByRole('dialog', { name: 'Model' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'More' }));
    await pick(within(dialog).getByRole('combobox', { name: 'Compact' }), '400K');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/bots/ana/compact-at'))?.body).toEqual({ compact_at: 400000, expected_revision: 3 }));
    expect(sent.some((s) => s.url.includes('/transitions'))).toBe(false);
  });
});

describe('A computer', () => {
  it('lists its agents and restarts it, asked first, when it is offline @s729', async () => {
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, online: false }]);
    serve(() => undefined);
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    const on = await screen.findByRole('region', { name: /Agents on this computer/ });
    expect(within(on).getByRole('link', { name: 'Ana' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Restart Studio?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(sent.some((s) => s.method === 'POST' && s.url.endsWith('/api/v2/computers/r1/restart'))).toBe(true));
  });

  it('marks each agent stopped while it is offline, and the All agents row sets one model for every agent @s729', async () => {
    const bo: Actor = { id: 'bot:0191-bo', kind: 'agent', name: 'Bo', slug: 'bo' };
    queryClient.setQueryData(directoryQuery.queryKey, directory([ana, bo, pat, kim]));
    queryClient.setQueryData(agentsQuery.queryKey, [...roster, { id: '0191-bo', slug: 'bo', display_name: 'Bo', revision: 5, model: 'm-two', machine: { runner_id: 'r1', label: 'Studio', generation: 2 } } as AgentRow]);
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, online: false, bots: ['0190-ana', '0191-bo'] }]);
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm-one', label: 'Model One' }, { id: 'm-two', label: 'Model Two' }], harnesses: [] } as never);
    serve((url, method) => (method === 'POST' && url.includes('/transitions') ? { status: 200, body: { id: 'tr' } } : undefined));
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

  it('adds a sign-in for a tool every agent on it uses @s730', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/api/v2/computers/r1/logins') ? { status: 200, body: { id: 'l1', runner_id: 'r1', state: 'starting' } } : undefined));
    show(<SignIns c={studio} />, '/team/ana?tab=computer');
    fireEvent.click(await screen.findByRole('button', { name: 'Add a sign-in' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a sign-in' });
    await pick(within(dialog).getByRole('combobox', { name: 'AI tool' }), 'Codex');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/computers/r1/logins'))?.body).toEqual({ runtime: 'codex' }));
  });
});
