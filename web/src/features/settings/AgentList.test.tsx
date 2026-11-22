import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { agentsQuery, computersQuery as teamComputers } from '@/features/team/exports';
import { Toasts } from '@/kit/Toast';
import { dragTo } from '@/test/drag';
import { AddComputer, Computers } from './Agents';
import {
  botAccessQuery,
  botMailQuery,
  botsQuery,
  computersQuery,
  hostedQuery,
  limitsQuery,
  modelsQuery,
  providersQuery,
  stoppedRunsQuery,
  subscriptionsQuery,
} from './queries';
import { SettingsPage, hitHref } from './SettingsPage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
/** Anything `reply` leaves (undefined) answers 200 with {}. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = (await reply(String(url), method)) ?? ok({});
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

const ok = (body: unknown): Reply => ({ status: 200, body });

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const ACTORS = [
  { id: 'bot:a1', kind: 'agent', name: 'Alpha Agent', slug: 'alpha', team: 'Product', hue: 1 },
  { id: 'bot:b2', kind: 'agent', name: 'Beta Agent', slug: 'beta', team: 'Growth', hue: 2 },
  { id: 'human:p1', kind: 'person', name: 'Ana Test', hue: 3 },
];
const ROWS = [
  { id: 'a1', slug: 'alpha', harness: 'claude', model: 'model-one', resolved_model_label: 'Model One', revision: 3, can_manage: true, machine: { generation: 7 } },
  { id: 'b2', slug: 'beta', harness: 'codex', model: 'model-one', resolved_model_label: 'Model One', revision: 5, can_manage: true, machine: { generation: 2 } },
];
const MODELS = {
  models: [
    { id: 'model-one', provider: 'anthropic', label: 'Model One', harnesses: ['claude'] },
    { id: 'model-two', provider: 'anthropic', label: 'Model Two', harnesses: ['claude'], efforts: ['low', 'high'] },
  ],
  enabled_providers: ['anthropic'],
  harnesses: [{ id: 'claude', name: 'Claude Code' }],
  default: { model: 'model-one' },
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
  queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [] });
  queryClient.setQueryData(agentsQuery.queryKey, ROWS);
  queryClient.setQueryData(modelsQuery.queryKey, MODELS);
  queryClient.setQueryData(limitsQuery.queryKey, { bots: {}, default: { count_subscription: false, daily_usd: null, hourly_usd: null, monthly_usd: null, per_30m_usd: null, weekly_usd: null }, may_edit_default: true, pools: null });
  queryClient.setQueryData(botsQuery.queryKey, [{ id: 'c3', slug: 'gamma', display_name: 'Gamma Agent', state: 'archived', owner_id: 'p1' }]);
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings › Agents', () => {
  it('checking agents shows how many, and Change model moves each one on its own harness @s713', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByRole('checkbox', { name: 'Check Alpha Agent' })).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Alpha Agent' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Beta Agent' }));
    expect(await screen.findByText('2 checked')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change model' })).toBeTruthy();

    const r = await run('settings.changeModels', { agents: ['alpha', 'beta'], model: 'model-two', effort: 'high' }, { via: 'click' });
    expect(r).toMatchObject({ ok: true, did: 'Changed 2' });
    const moves = sent.filter((s) => s.url.endsWith('/transitions'));
    expect(moves.map((m) => m.url).sort()).toEqual(['/api/v2/bots/alpha/transitions', '/api/v2/bots/beta/transitions']);
    // Beta's own harness cannot run the model: it moves to one that can.
    expect(moves.find((m) => m.url.includes('beta'))?.body).toMatchObject({ kind: 'model', model: 'model-two', effort: 'high', harness: 'claude' });
    expect(moves.find((m) => m.url.includes('alpha'))?.body).toMatchObject({ harness: 'claude', expected_generation: 7, expected_revision: 3 });
  });

  it('the search narrows the list by name, team or model @s1074', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Search agents, models or computers' }), { target: { value: 'growth' } });
    expect(await screen.findByText('1 agent')).toBeTruthy();
    expect(screen.queryByTitle('Alpha Agent')).toBeNull();
    expect(screen.getByTitle('Beta Agent')).toBeTruthy();
  });

  it("one row's model opens the team's model picker, and Add agent opens Add teammate", async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: "Alpha Agent's model" }));
    expect(await screen.findByRole('radiogroup', { name: 'Model' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
    expect(await screen.findByRole('button', { name: /New agent/ })).toBeTruthy();
  });

  it('a row reads its model, effort and where it compacts, and its tasks follow it unless set', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [
      { ...ROWS[0], resolved_model_label: 'Claude Model One', resolved_effort: 'high', compact_at: 400000 } as never,
      { ...ROWS[1], task_model: { model: 'model-two', reasoning_effort: 'low' } } as never,
    ]);
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect((await screen.findByRole('button', { name: "Alpha Agent's model" })).textContent).toBe('Model One, High, 400K');
    expect(screen.getByRole('button', { name: "Alpha Agent's tasks" }).textContent).toBe('Same');
    expect(screen.getByRole('button', { name: "Beta Agent's tasks" }).textContent).toBe('Model Two, Low');
    // Grouped by team: each team is a section.
    expect(screen.getByRole('button', { name: /Product/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Growth/ })).toBeTruthy();
  });

  it('by computer, every computer is a group; checked agents move to one at once', async () => {
    queryClient.setQueryData(teamComputers.queryKey, [
      { id: 'r1', label: 'Shared One', online: true },
      { id: 'r2', label: 'Spare Two', online: false },
    ]);
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...ROWS[0], machine: { runner_id: 'r1', label: 'Shared One', generation: 7 } }, ROWS[1]] as never);
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Computer' }));
    expect(await screen.findByRole('button', { name: /Spare Two/ })).toBeTruthy();
    expect(screen.getByText('No agents yet')).toBeTruthy();
    expect(screen.getByRole('button', { name: /No computer/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Alpha Agent' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Beta Agent' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Move' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Spare Two/ }));
    await waitFor(() => expect(sent.filter((s) => s.url.endsWith('/transitions'))).toHaveLength(2));
    for (const m of sent) expect(m.body).toMatchObject({ kind: 'machine', runner_id: 'r2' });
  });

  it('a limit is changed where it stands', async () => {
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: { alpha: { monthly_usd: 120, own_monthly_usd: 120, percent: 10, month_spent: 12, blocked: null, may_edit: true } as never },
      default: { count_subscription: false, daily_usd: null, hourly_usd: null, monthly_usd: null, per_30m_usd: null, weekly_usd: null },
      may_edit_default: true,
      pools: null,
    });
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    const lim = await screen.findByRole('button', { name: "Alpha Agent's limit" });
    expect(lim.textContent).toBe('$120');
    fireEvent.click(lim);
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'Dollars a month' }), { target: { value: '80' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/usage/limits/alpha', method: 'PUT', body: { monthly_usd: 80 } }));
  });

  it('the head box checks every agent shown; a search says how many it found', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Check all' }));
    expect(await screen.findByText('2 checked')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check all' }));
    expect(screen.queryByText('2 checked')).toBeNull();
    expect(screen.queryByText('2 agents')).toBeNull();
    fireEvent.change(screen.getByLabelText('Search agents, models or computers'), { target: { value: 'alpha' } });
    expect(await screen.findByText('1 agent')).toBeTruthy();
  });

  it("what an agent's tasks run on goes to the server with its revision; empty is the agent's own", async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    await screen.findByRole('checkbox', { name: 'Check Alpha Agent' });
    expect(await run('settings.taskModel', { agent: 'alpha', model: 'model-two', effort: 'low' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({
      url: '/api/v2/bots/a1/task-model',
      body: { task_model: { model: 'model-two', harness: 'claude', effort: 'low' }, expected_revision: 3 },
    });
    await run('settings.taskModel', { agent: 'beta', model: '' }, { via: 'click' });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/b2/task-model', body: { task_model: null, expected_revision: 5 } });
  });

  it('folded, Subscriptions and keys shows a chip for each kind and a count of keys @s709', async () => {
    const sub = (id: string, kind: string, status = 'working') => ({ id, kind, name: `Plan ${id}`, label: 'sample', status, in_use_on: [] }) as never;
    queryClient.setQueryData(subscriptionsQuery.queryKey, {
      kinds: [],
      subscriptions: [sub('s1', 'claude_login'), sub('s2', 'claude_login'), sub('s3', 'codex_login', 'needs_reconnect'), sub('s4', 'anthropic_key'), sub('s5', 'openai_key')],
    });
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByText('Plan s3 expired')).toBeTruthy();
    expect(screen.getByText('Claude').closest('.fs-chip')?.textContent).toBe('Claude2');
    expect(screen.getByText('2 keys')).toBeTruthy();
    expect(screen.queryByText('ChatGPT')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Subscriptions and keys/ }));
    expect(await screen.findByTitle('Plan s1')).toBeTruthy();
    expect(screen.queryByText('2 keys')).toBeNull();
  });

  it('a plan the provider refused for its billing says so, folded and in its row @s709', async () => {
    const past = { id: 's1', kind: 'claude_login', name: 'brooke@acme.example', label: 'Claude plan', status: 'working', problem: 'Payment past due', in_use_on: [] };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [past as never] });
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByText('brooke@acme.example: Payment past due')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Subscriptions and keys/ }));
    const row = await screen.findByTitle('brooke@acme.example');
    expect(row.className).toContain('is-bad');
    expect(row.textContent).toContain('Payment past due');
  });

  it('a retired agent can be brought back by its owner @s693', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: /Retired/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/c3/restore', method: 'POST' }));
  });

  it('groups the agents by team, and by computer when asked @n:se:settings-fleet', async () => {
    serve();
    queryClient.setQueryData(teamComputers.queryKey, [{ id: 'r1', label: 'Shared One', online: true }] as never);
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...ROWS[0], machine: { runner_id: 'r1', label: 'Shared One', generation: 1 } }, ROWS[1]] as never);
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByRole('region', { name: /Product/ })).toBeTruthy();
    expect(screen.getByRole('region', { name: /Growth/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Computer' }));
    expect(await screen.findByRole('region', { name: /Shared One/ })).toBeTruthy();
    expect(screen.getByRole('region', { name: /No computer/ })).toBeTruthy();
  });
});

const MACHINES = [
  { id: 'r1', label: 'Shared One', online: true, platform: 'linux', version: '0.0.9', screen_available: true, screen: 'view', bots: ['a1'] },
  { id: 'r2', label: 'Spare Two', online: false },
];
/** A small server: the computers and agents read back as written (a write re-reads its list). */
const byComputer = () => {
  const machines = MACHINES.map((m) => ({ ...m }));
  const agents = [{ ...ROWS[0], machine: { runner_id: 'r1', label: 'Shared One', generation: 7 } }, ROWS[1]];
  queryClient.setQueryData(teamComputers.queryKey, machines);
  queryClient.setQueryData(computersQuery.queryKey, [{ id: 'r1', label: 'Shared One', online: true, gateway_fallback: false }]);
  queryClient.setQueryData(agentsQuery.queryKey, agents as never);
  serve((url, method) => {
    const label = /^\/api\/v2\/computers\/r1\/label$/.test(url) && method === 'POST';
    if (label) machines[0]!.label = (sent.at(-1)?.body as { label: string }).label;
    if (url === '/api/v2/operations') return { status: 200, body: { computers: machines, machines } };
    if (url === '/api/employees') return { status: 200, body: agents };
    return { status: 200, body: {} };
  });
};

describe('Settings › Agents › a computer', () => {
  it("a computer's group head opens its panel", async () => {
    byComputer();
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Computer' }));
    const open = await screen.findByRole('link', { name: 'Open Shared One' });
    expect(open.getAttribute('href')).toBe('/settings/agents?open=computer%3Ar1');
    expect(open.textContent).toBe('Online');
    // "No computer" is not a computer: nothing to open.
    expect(screen.queryByRole('link', { name: /Open No computer/ })).toBeNull();
  });

  it('the panel shows its facts and agents; Restart asks, then restarts; the screen setting saves', async () => {
    byComputer();
    const router = show(<SettingsPage section="agents" open="computer:r1" />, '/settings/agents?open=computer%3Ar1');
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    expect(within(panel).getByText('Online')).toBeTruthy();
    expect(within(panel).getByText('linux')).toBeTruthy();
    expect(within(panel).getByRole('link', { name: /Alpha Agent/ }).getAttribute('href')).toBe('/team/alpha');
    expect(within(panel).queryByText('Beta Agent')).toBeNull();
    // Opened from a link, the list is grouped by computer.
    expect(screen.getByRole('link', { name: 'Open Spare Two' })).toBeTruthy();

    fireEvent.click(within(panel).getByRole('button', { name: 'Control' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/computers/r1/screen', body: { mode: 'control' } }));

    fireEvent.click(within(panel).getByRole('button', { name: 'Restart' }));
    const ask = await screen.findByRole('dialog', { name: 'Restart Shared One?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/computers/r1/restart', method: 'POST' }));

    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it('rename and remove change both lists at once', async () => {
    byComputer();
    show(<SettingsPage section="agents" open="computer:r1" />, '/settings/agents?open=computer%3Ar1');
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Rename' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'Name' }), { target: { value: 'Shared Three' } });
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Rename computer' })).getByRole('button', { name: 'Rename' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/computers/r1/label', body: { label: 'Shared Three' } }));
    for (const key of [teamComputers.queryKey, computersQuery.queryKey])
      await waitFor(() => expect(queryClient.getQueryData<{ id: string; label: string }[]>(key)?.[0]?.label).toBe('Shared Three'));

    fireEvent.click(within(await screen.findByRole('complementary', { name: 'Shared Three' })).getByRole('button', { name: 'Remove' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/computers/r1/revoke', method: 'POST' }));
  });

  it('Settings › Computers opens the same panel @s1075', async () => {
    byComputer();
    show(<SettingsPage section="computers" open="r1" />, '/settings/computers?open=r1');
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    expect(within(panel).getByRole('switch', { name: 'Fall back to Tico Gateway' })).toBeTruthy();
  });
});

describe('Settings › Computers', () => {
  it('external agents are one dimmed Coming soon line with nothing to press @s703', async () => {
    serve();
    queryClient.setQueryData(computersQuery.queryKey, [{ id: 'r1', label: 'Studio', online: true }] as never);
    show(<Computers />, '/settings/computers');
    const section = await screen.findByRole('region', { name: 'External agents' });
    expect(within(section).getByText('Coming soon')).toBeTruthy();
    expect(within(section).getByText('Agents made and run outside Tico').closest('.row')?.getAttribute('aria-disabled')).toBe('true');
    expect(within(section).queryByRole('button', { name: /pair|add|connect/i })).toBeNull();
    expect(within(section).queryByRole('link')).toBeNull();
  });

  it('a stopped hosted server starts for an owner, says why when held with a link to Tico support, and Tico cannot start it @s704', async () => {
    serve((url) =>
      url === '/api/v2/hosted-servers/h1/start' ? ok({ state: 'paused', message: 'Out of credit', credit_url: 'https://help.example.test/credit' }) : undefined,
    );
    queryClient.setQueryData(computersQuery.queryKey, [] as never);
    queryClient.setQueryData(hostedQuery.queryKey, { state: 'ok', servers: [{ server_id: 'h1', kind: 'tico', power: 'stopped' }] });
    show(<Computers />, '/settings/computers');
    const line = (await screen.findByText('Tico server')).closest('.row') as HTMLElement;
    expect(within(line).getByText('Stopped')).toBeTruthy();
    fireEvent.click(within(line).getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/hosted-servers/h1/start', method: 'POST' }));
    expect(await within(line).findByText('Out of credit')).toBeTruthy();
    expect(within(line).getByRole('link', { name: 'Tico support' }).getAttribute('href')).toBe('https://help.example.test/credit');
    sent = [];
    expect(await run('settings.startServer', { server: 'h1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });

  it('Add computer makes a one-time code in the dialog; Tico cannot @s728', async () => {
    queryClient.setQueryData(computersQuery.queryKey, []);
    serve((url, method) => (url === '/api/v2/enrollments' && method === 'POST' ? ok({ code: 'JOIN-4821' }) : undefined));
    show(<AddComputer />, '/settings/computers');
    fireEvent.click(await screen.findByRole('button', { name: 'Add computer' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add a computer' });
    fireEvent.change(within(dlg).getByRole('textbox', { name: 'Name' }), { target: { value: 'Studio' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Get code' }));
    expect((await within(dlg).findAllByText(/JOIN-4821/)).length).toBeGreaterThan(0);
    expect(sent).toContainEqual({ url: '/api/v2/enrollments', method: 'POST', body: { operator: 'p1' } });
    expect(await run('settings.addComputer', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Settings › Agents › drag', () => {
  const group = (id: string) => document.querySelector(`section[data-target="group:computer:${id}"]`) as Element;
  const row = (slug: string) => document.querySelector(`[data-target="agent:${slug}"]`) as Element;

  it("a row dropped on another computer's group moves there; checked rows go with it", async () => {
    byComputer();
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Computer' }));
    await screen.findByRole('link', { name: 'Open Spare Two' });
    dragTo(row('beta'), group('r2').querySelector('.stage-head'));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ url: '/api/v2/bots/beta/transitions', body: { kind: 'machine', runner_id: 'r2' } });
    expect(document.querySelector('[data-drop], .is-drag')).toBeNull();

    sent = [];
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Alpha Agent' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check Beta Agent' }));
    dragTo(row('alpha'), group('r2'));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent.map((x) => x.url).sort()).toEqual(['/api/v2/bots/alpha/transitions', '/api/v2/bots/beta/transitions']);
  });

  it('a drop on "No computer", on its own group, or a drag from a control or a touch moves nothing', async () => {
    byComputer();
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Computer' }));
    await screen.findByRole('link', { name: 'Open Spare Two' });
    const alpha = row('alpha');
    expect(alpha.getAttribute('draggable')).toBe('true');
    dragTo(alpha, group(''));
    dragTo(alpha, group('r1'));
    dragTo(alpha, group('r2'), { press: within(alpha as HTMLElement).getByRole('checkbox') });
    dragTo(alpha, group('r2'), { pointerType: 'touch' });
    await new Promise((r) => setTimeout(r, 20));
    expect(sent).toEqual([]);
  });

  it('grouped by team, rows do not drag', async () => {
    byComputer();
    show(<SettingsPage section="agents" />, '/settings/agents');
    await screen.findByRole('link', { name: 'Alpha Agent' });
    expect(row('alpha').getAttribute('draggable')).toBeNull();
  });
});

describe('Settings › Agents on a phone', () => {
  it("a row's ⋯ reaches its model, tasks and limit", async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Alpha Agent' }));
    expect(await screen.findByRole('menuitem', { name: /Model/ })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: /Tasks/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /Limit/ }));
    expect(await screen.findByRole('spinbutton', { name: 'Dollars a month' })).toBeTruthy();
  });
});

describe('Settings actions', () => {
  it('person-only changes are refused when Tico asks, and nothing is sent', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    await screen.findByRole('checkbox', { name: 'Check Alpha Agent' });
    const asks: [string, unknown][] = [
      ['settings.makeOwner', { person: 'p2', keepAdmin: true }],
      ['settings.changeEmail', { email: 'new@example.test', again: 'new@example.test' }],
      ['settings.removePerson', { person: 'p2' }],
      ['settings.agentOwners', { agent: 'a1', add: ['p2'], remove: [] }],
      ['settings.agentMail', { agent: 'a1', on: true }],
      ['settings.saveDirectory', { source: 'scim', interval: 60, massLeave: 10, groups: [], orgUnits: [], domains: [] }],
      ['settings.scimToken', {}],
      ['settings.syncDirectory', { plan: 'h1', confirm: true }],
    ];
    for (const [id, input] of asks) expect(await run(id, input, { via: 'tico' })).toEqual({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });

  it('the default model shows at once and goes back when the server refuses @s711 @s1076', async () => {
    let refuse!: () => void;
    serve((url, method) =>
      method === 'PUT' && url === '/api/v2/providers'
        ? new Promise<Reply>((r) => (refuse = () => r({ status: 409, body: { error: { code: 'version_conflict', detail: 'Changed' } } })))
        : { status: 200, body: {} },
    );
    queryClient.setQueryData(providersQuery.queryKey, { enabled: ['anthropic'], default: { model: 'model-one' }, revision: 4, meeting_model: '' });
    show(<SettingsPage section="models" />, '/settings/models');
    await screen.findByText('Defaults');
    const done = run('settings.defaultModel', { model: 'model-two' }, { via: 'click' });
    await waitFor(() => expect(queryClient.getQueryData<{ default: { model: string } }>(providersQuery.queryKey)?.default.model).toBe('model-two'));
    expect(sent[0]).toMatchObject({ url: '/api/v2/providers', method: 'PUT', body: { enabled: ['anthropic'], model: 'model-two', expected_revision: 4 } });
    expect((sent[0]?.body as Record<string, unknown>).meeting_model).toBeUndefined();
    refuse();
    expect((await done).ok).toBe(false);
  });
});

describe('Settings search', () => {
  it('opens each hit in its section here, or leaves it out', () => {
    expect(hitHref('#/settings/bots/settings-bots')).toBe('/settings/agents');
    expect(hitHref('https://tico.example.test/#/settings/devices/x')).toBe('/settings/computers');
    expect(hitHref('#/usage')).toBe('/company/usage');
    expect(hitHref('#/bot/sam/settings')).toBe('/team/sam?tab=settings');
    expect(hitHref('#/settings/inboxes/x')).toBeNull();
  });

  it('offers only the settings the reader may change @s1130', async () => {
    queryClient.setQueryData(['me'], { id: 'human:p2', email: 'ben@example.test', role: 'person', slug: null });
    serve((url) => {
      if (url.startsWith('/api/v2/settings/catalog'))
        return {
          status: 200,
          body: {
            settings: [
              { key: 'a', name: 'Company name', who: 'owner', card: '#/settings/general/x' },
              { key: 'b', name: 'Who can sign in', who: 'admin', card: '#/settings/people/x' },
              { key: 'c', name: 'Browser alerts', who: 'person', card: '#/settings/general/y' },
            ],
          },
        };
      if (url.startsWith('/api/v2/access')) return { status: 403, body: { error: { code: 'forbidden', detail: 'Admins' } } };
      return { status: 200, body: {} };
    });
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Search settings' }), { target: { value: 'alerts' } });
    const hit = await screen.findByRole('link', { name: 'Browser alerts' });
    expect(hit.getAttribute('href')).toBe('/settings/general');
    expect(screen.queryByRole('link', { name: 'Company name' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Who can sign in' })).toBeNull();
  });
});

describe('Settings › Agents › an opened agent', () => {
  const EVERY = { everyone: true, people: [], teams: [], bots: [] };
  const PEOPLE = [...ACTORS, { id: 'human:p2', kind: 'person', name: 'Ben Sample', hue: 4 }];
  const agent = (id: string, slug: string, name: string) => ({ id, slug, display_name: name, state: 'active', owner_id: 'human:p1', bot_owners: [] });
  /** Alpha opened; `teams` are the groups its access may name, `stopped` its stopped runs. */
  const open = ({ teams, stopped = [] }: { teams?: { id: string; name: string }[]; stopped?: unknown[] } = {}) => {
    queryClient.setQueryData(['directory'], { actors: PEOPLE, byId: Object.fromEntries(PEOPLE.map((a) => [a.id, a])) });
    queryClient.setQueryData(botsQuery.queryKey, [agent('a1', 'alpha', 'Alpha Agent'), agent('b2', 'beta', 'Beta Agent')]);
    queryClient.setQueryData(botAccessQuery('a1').queryKey, { bot: 'a1', see: EVERY, read: EVERY, write: EVERY, revision: 2, ...(teams ? { teams } : {}) });
    queryClient.setQueryData(botMailQuery('a1').queryKey, { outbound_send: false, company_send: true } as never);
    queryClient.setQueryData(stoppedRunsQuery('a1').queryKey, stopped as never);
    serve((_url, method) => (method === 'GET' ? { status: 404, body: { detail: 'not_found' } } : { status: 200, body: {} }));
    show(<SettingsPage section="agents" open="alpha" />, '/settings/agents?open=alpha');
  };

  it('Access: Private with one person sends the same people for see, read and write; Tico cannot @s1112', async () => {
    open();
    const line = (await screen.findByText('Open')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Edit' }));
    const dlg = await screen.findByRole('dialog', { name: 'Who can use Alpha Agent' });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Private' }));
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Ben Sample' }));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    const only = { everyone: false, people: ['p2'], teams: [], bots: [] };
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/bots/a1/access', method: 'PUT', body: { see: only, read: only, write: only, revision: 2 } }));
    expect(await run('settings.agentAccess', { agent: 'a1', see: EVERY, read: EVERY, write: EVERY }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Owners: the maker stays, a checked person is added @s1113', async () => {
    open();
    const line = (await screen.findByText('Owners')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Edit' }));
    const dlg = await screen.findByRole('dialog', { name: 'Who owns Alpha Agent' });
    expect(within(dlg).getByRole('checkbox', { name: 'Ana Test' }).hasAttribute('data-disabled')).toBe(true);
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Ben Sample' }));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/bots/a1/co-owners', method: 'POST', body: { add: ['p2'], remove: [] } }));
  });

  it('Access: Private can name a group and another agent, not only people @s1112', async () => {
    open({ teams: [{ id: 'product', name: 'Product' }] });
    const line = (await screen.findByText('Open')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Edit' }));
    const dlg = await screen.findByRole('dialog', { name: 'Who can use Alpha Agent' });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Private' }));
    expect(within(dlg).queryByRole('checkbox', { name: 'Alpha Agent' })).toBeNull();
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Product' }));
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Beta Agent' }));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    const only = { everyone: false, people: [], teams: ['product'], bots: ['b2'] };
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/bots/a1/access', method: 'PUT', body: { see: only, read: only, write: only } }));
  });

  it('mail: the switch lets it send without asking; Tico may stop it mailing on its own, but not start it @s1111', async () => {
    open();
    fireEvent.click(await screen.findByRole('switch', { name: 'Send mail without asking' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/a1/mail-settings', method: 'POST', body: { outbound_send: true } }));
    sent = [];
    expect(await run('settings.agentMail', { agent: 'a1', on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
    expect(await run('settings.agentMail', { agent: 'a1', on: false }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/a1/mail-settings', body: { outbound_send: false } });
  });

  it('stopped runs: Review opens them, Try again reconciles with the note; Tico cannot @s1110', async () => {
    open({ stopped: [{ id: 'j1', run_id: 'r9', stopped: 'changed', reason: 'The computer restarted', task: { title: 'Send the weekly note' } }] });
    const line = (await screen.findByText('Stopped runs')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Review' }));
    const dlg = await screen.findByRole('dialog', { name: 'Stopped runs' });
    expect(within(dlg).getByText('Send the weekly note')).toBeTruthy();
    expect(within(dlg).getByText('Trying again could repeat work that already finished.')).toBeTruthy();
    fireEvent.change(within(dlg).getByLabelText('Note for the agent'), { target: { value: 'Nothing was sent' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/jobs/j1/reconcile', method: 'POST', body: { run_id: 'r9', decision: 'resume' } }));
    expect((sent.at(-1)?.body as { note: string }).note).toContain('Details: Nothing was sent');
    sent = [];
    expect(await run('settings.stoppedRun', { agent: 'a1', job: 'j1', run: 'r9', decision: 'dismiss' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(sent).toHaveLength(0);
  });

  it('stopped runs: a decision already saved keeps its buttons off and says Saved @s1110', async () => {
    open({ stopped: [{ id: 'j1', run_id: 'r1', stopped: 'read', task: { title: 'Sum up the week' }, pending_decision: { decision: 'resume' } }] });
    const line = (await screen.findByText('Stopped runs')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Review' }));
    const dlg = await screen.findByRole('dialog', { name: 'Stopped runs' });
    expect(within(dlg).getByText('Sum up the week')).toBeTruthy();
    expect((within(dlg).getByRole('button', { name: 'Try again' }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dlg).getByRole('button', { name: 'Dismiss' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(dlg).getByText('Saved')).toBeTruthy();
  });
});
