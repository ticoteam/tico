import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { agentsQuery } from '@/features/team/exports';
import { Toasts } from '@/kit/Toast';
import { Computers } from './Agents';
import {
  askQuery,
  botAccessQuery,
  botMailQuery,
  botsQuery,
  changelogQuery,
  computersQuery,
  contactTypesQuery,
  feedsQuery,
  healthQuery,
  hostedQuery,
  labelsQuery,
  limitsQuery,
  modelsQuery,
  operateQuery,
  roadmapQuery,
  stoppedRunsQuery,
  subscriptionsQuery,
} from './queries';
import { SettingsPage } from './SettingsPage';
import { UsagePage } from './Usage';

/* The tier 2 gaps of Settings, closed: one or two tests a row, each tagged with it. Plain sample rows only. */

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let read: string[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') read.push(String(url));
    else sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method) ?? { status: 200, body: {} };
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

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const ACTORS = [
  { id: 'bot:a1', kind: 'agent', name: 'Alpha Agent', slug: 'alpha' },
  { id: 'bot:b2', kind: 'agent', name: 'Beta Agent', slug: 'beta' },
  { id: 'human:p1', kind: 'person', name: 'Ana Test' },
];
const NONE = { daily_usd: null, hourly_usd: null, monthly_usd: null, per_30m_usd: null, weekly_usd: null };
const ROAD = { labels: ['feature'], cors_origins: ['https://site.example.test'], action_limit: 10, suggestion_limit: 20, bot_public_approval: true, suggestions_bot: null };
const RULES = { mode: 'on', budget: 10, criteria: ['Spending', 'Hiring'], own: false };
const LIST = { can_add: false, can_review: false, current_version: '0.0.2', drafts: [] };
const EVERY = { everyone: true, people: [], teams: [], bots: [] };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  read = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
  queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [] });
  queryClient.setQueryData(agentsQuery.queryKey, [{ id: 'a1', slug: 'alpha', harness: 'claude', model: 'm', revision: 1, can_manage: true }] as never);
  queryClient.setQueryData(modelsQuery.queryKey, { models: [], enabled_providers: [], harnesses: [], default: {} });
  queryClient.setQueryData(limitsQuery.queryKey, { bots: {}, default: { ...NONE, count_subscription: false }, may_edit_default: true, pools: null });
  queryClient.setQueryData(botsQuery.queryKey, []);
  queryClient.setQueryData(roadmapQuery.queryKey, ROAD);
  queryClient.setQueryData(labelsQuery.queryKey, [{ id: '1', key: 'feature', label: 'Feature' }] as never);
});
afterEach(() => vi.unstubAllGlobals());

// ---------------------------------------------------------------- Tasks

describe('Settings › Tasks @s1077', () => {
  it('what agents may ask: Screen asks saves your own rules, Everyone saves the team default @s1077', async () => {
    const ASK = { settings: RULES, team: { ...RULES, own: true }, may_edit_team: true };
    serve((url, method) => (method === 'GET' && url === '/api/v2/ask-ingest/settings' ? ok(ASK) : url === '/api/v2/roadmaps/settings' ? ok(ROAD) : undefined));
    queryClient.setQueryData(askQuery.queryKey, ASK);
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const ask = await screen.findByRole('region', { name: 'What agents may ask' });
    fireEvent.click(within(ask).getByRole('switch', { name: 'Screen asks' }));
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({ url: '/api/v2/ask-ingest/settings', method: 'PUT', body: { mode: 'off', budget: 10, criteria: ['Spending', 'Hiring'], team: false } }),
    );
    fireEvent.click(within(ask).getByRole('button', { name: 'Everyone' }));
    fireEvent.change(within(ask).getByRole('spinbutton', { name: 'Tighten after' }), { target: { value: '25' } });
    fireEvent.click(within(ask).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: 'PUT', body: { budget: 25, team: true } }));
    const lines = screen.getByRole('region', { name: /Ask me about/ });
    expect(within(lines).getByTitle('Spending')).toBeTruthy();
  });

  it('roadmap websites, receiver and limits save; Tico may take a website off but not add one @s1077', async () => {
    serve((url) => (url === '/api/v2/roadmaps/settings' ? ok(ROAD) : undefined));
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const road = await screen.findByRole('region', { name: /Public roadmap/ });
    expect(within(road).getByText('https://site.example.test')).toBeTruthy();
    await pick(within(road).getByRole('combobox', { name: 'Suggestions go to' }), /^Beta Agent$/);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/roadmaps/settings', body: { suggestions_bot: 'b2' } }));
    fireEvent.change(within(road).getByRole('spinbutton', { name: 'Visitor requests a day' }), { target: { value: '50' } });
    fireEvent.click(within(road).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ body: { action_limit: 50 } }));
    expect(await run('settings.roadmapSites', { origins: ['https://site.example.test', 'https://new.example.test'] }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(await run('settings.roadmapSites', { origins: [] }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/roadmaps/settings', body: { cors_origins: [] } });
  });
});

// ---------------------------------------------------------------- Health

describe('Settings › Health @s1082', () => {
  const HEALTH = {
    checked: new Date().toISOString(),
    attention: 1,
    checks: [
      { id: 'db', label: 'Database', status: 'ok', summary: 'Answers quickly' },
      { id: 'mail', label: 'Mail', status: 'bad', summary: 'Nothing received' },
    ],
    stuck: [{ bot: 'alpha', name: 'Alpha Agent', reason: 'no_computer', why: 'No computer is online' }],
  };

  it('splits Needs a look from Fine with the checked time, lists a stuck agent, and Check now reads again @s1082', async () => {
    serve((url) => (url === '/api/v2/health' ? ok(HEALTH) : undefined));
    queryClient.setQueryData(healthQuery.queryKey, HEALTH as never);
    show(<SettingsPage section="health" />, '/settings/health');
    const bad = await screen.findByRole('region', { name: 'Needs a look' });
    expect(within(bad).getByTitle('Mail')).toBeTruthy();
    expect(within(bad).getByText(/^Checked /)).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Fine' })).getByTitle('Database')).toBeTruthy();
    const stuck = screen.getByRole('region', { name: 'Stuck agents' });
    expect(within(stuck).getByRole('link', { name: /Alpha Agent/ }).getAttribute('href')).toBe('/team/alpha');
    read = [];
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(read).toContain('/api/v2/health'));
  });

  it('a Tico bug finding is reported to Tico support; Setup check shows what fails @s1082', async () => {
    serve((url) => {
      if (url === '/api/v2/health/findings') return ok({ findings: [{ key: 'bug:1', kind: 'bug', title: 'A page failed to load', occurrences: 2 }] });
      if (url === '/api/v2/support/setup-check') return ok({ checks: [{ name: 'Address', status: 'bad', detail: 'Not reachable', fix: 'Open port 443' }] });
      return undefined;
    });
    queryClient.setQueryData(healthQuery.queryKey, { ...HEALTH, stuck: [] } as never);
    show(<SettingsPage section="health" />, '/settings/health');
    const found = await screen.findByRole('region', { name: 'Findings' });
    fireEvent.click(within(found).getByRole('button', { name: 'Report' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/health/findings/bug%3A1/report', method: 'POST' }));
    fireEvent.click(within(found).getByRole('switch', { name: 'Report Tico bugs without asking' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/access/rules', method: 'PUT', body: { health_monitor_reports: true } }));
    expect(await run('settings.reportRule', { on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    fireEvent.click(screen.getByRole('button', { name: 'Setup check' }));
    const setup = await screen.findByRole('region', { name: 'Setup check' });
    expect(within(setup).getByText('Not reachable Fix: Open port 443')).toBeTruthy();
    expect(within(setup).getByText('1 of 1 checks fail')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- Changelog

describe('Settings › Changelog @s1068', () => {
  const entry = (id: string, version: string, unread = false) => ({ id, version, title: `Change ${id}`, area: 'tasks', bullets: [], kind: 'product', shipped_at: '2026-03-04T00:00:00Z', unread });

  it('marks a version with unread updates New and reads them; each version has its day @s1068', async () => {
    serve();
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, unread_count: 1, next_offset: null, entries: [entry('e1', '0.0.2', true), entry('e2', '0.0.1')] } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    const newest = await screen.findByRole('region', { name: '0.0.2' });
    expect(within(newest).getByText('New')).toBeTruthy();
    expect(within(newest).getByText('Mar 4')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: '0.0.1' })).queryByText('New')).toBeNull();
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/changelog/read', method: 'POST', body: { ids: ['e1'] } }));
  });

  it('Load older reads the next page and adds its versions @s1068', async () => {
    serve((url) => (url.includes('offset=60') ? ok({ ...LIST, unread_count: 0, next_offset: null, entries: [entry('e9', '0.0.0')] }) : undefined));
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, unread_count: 0, next_offset: 60, entries: [entry('e1', '0.0.2')] } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    fireEvent.click(await screen.findByRole('button', { name: 'Load older' }));
    expect(await screen.findByRole('region', { name: '0.0.0' })).toBeTruthy();
    expect(read.some((u) => u.includes('/api/v2/changelog?kind=product&limit=60&offset=60'))).toBe(true);
    expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull();
  });
});

// ---------------------------------------------------------------- Contacts

describe('Settings › Contacts @s1078', () => {
  it('lists where we operate and removes a place; an inbox switch adds every sender @s1078', async () => {
    serve((url, method) =>
      method === 'GET' && url === '/api/v2/entities/self'
        ? ok({ id: 'us', name: 'Sample Co', operates_in: [] })
        : method === 'GET' && url === '/api/v2/mailboxes'
          ? ok({ admin: false, mine: { id: 'mb1', address: 'ana@example.test', kind: 'personal', feeds_directory: true } })
          : undefined,
    );
    queryClient.setQueryData(contactTypesQuery.queryKey, [] as never);
    queryClient.setQueryData(operateQuery.queryKey, { id: 'us', name: 'Sample Co', operates_in: [{ id: 'pl1', name: 'Oregon', link_id: 'ln1' }] } as never);
    queryClient.setQueryData(feedsQuery.queryKey, [{ id: 'mb1', address: 'ana@example.test', kind: 'personal', feeds_directory: false }]);
    show(<SettingsPage section="contacts" />, '/settings/contacts');
    const where = await screen.findByRole('region', { name: /Where we operate/ });
    fireEvent.click(within(where).getByRole('button', { name: 'Remove Oregon' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/entities/unlink', method: 'POST', body: { link_id: 'ln1' } }));
    expect(within(where).queryByTitle('Oregon')).toBeNull();
    fireEvent.click(screen.getByRole('switch', { name: 'ana@example.test' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/mailboxes/mb1/contacts', method: 'PATCH', body: { on: true } }));
  });
});

// ---------------------------------------------------------------- Stopped runs

describe('Settings › Agents › stopped runs @s1110', () => {
  it('a decision already saved keeps its buttons off; Details shows the request, the saved answer and the tool calls @s1110', async () => {
    serve();
    queryClient.setQueryData(botsQuery.queryKey, [{ id: 'a1', slug: 'alpha', display_name: 'Alpha Agent', state: 'active', owner_id: 'human:p1', bot_owners: [] }]);
    queryClient.setQueryData(botAccessQuery('a1').queryKey, { bot: 'a1', see: EVERY, read: EVERY, write: EVERY, revision: 2 });
    queryClient.setQueryData(botMailQuery('a1').queryKey, { outbound_send: false, company_send: true } as never);
    queryClient.setQueryData(stoppedRunsQuery('a1').queryKey, [
      {
        id: 'j1',
        run_id: 'r1',
        stopped: 'read',
        request: 'Sum up the week',
        run: { final_text: 'Half a summary', started_at: '2026-03-04T10:00:00Z' },
        tools: [{ tool: 'search', summary: 'looked up notes' }],
        pending_decision: { decision: 'resume' },
      },
    ] as never);
    show(<SettingsPage section="agents" open="alpha" />, '/settings/agents?open=alpha');
    const line = (await screen.findByText('Stopped runs')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Review' }));
    const dlg = await screen.findByRole('dialog', { name: 'Stopped runs' });
    expect(within(dlg).getByText(/^Stopped .*before changing anything\.$/)).toBeTruthy();
    expect((within(dlg).getByRole('button', { name: 'Try again' }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dlg).getByRole('button', { name: 'Dismiss' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(dlg).getByRole('status').textContent).toContain('Tico will try again');
    fireEvent.click(within(dlg).getByRole('button', { name: 'Details' }));
    expect(within(dlg).getByText('Sum up the week')).toBeTruthy();
    expect(within(dlg).getByText('Half a summary')).toBeTruthy();
    expect(within(dlg).getByText('Tool calls (1)')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- Usage, Computers

describe('Usage › Limits, clear the automatic queue @s1115', () => {
  it('can be limited to one team @s1115', async () => {
    serve();
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: {},
      default: { ...NONE, count_subscription: false },
      may_edit_default: true,
      pools: { company: null, groups: { g1: { name: 'Growth', parent: null, monthly_usd: null, month_spent: 0, percent: 0, blocked: null } } },
    } as never);
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    fireEvent.click(await screen.findByRole('button', { name: 'Clear queue' }));
    const dlg = await screen.findByRole('alertdialog');
    await pick(within(dlg).getByRole('combobox', { name: 'Applies to' }), /Growth/);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Clear queue' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/usage/queues/purge', method: 'POST', body: { scope: 'group:g1' } }));
  });
});

describe('Settings › Computers, hosted servers @s704', () => {
  it('a start held for credit links to Tico support @s704', async () => {
    serve((url) =>
      url === '/api/v2/hosted-servers/h1/start' ? ok({ state: 'paused', message: 'Out of credit', credit_url: 'https://help.example.test/credit' }) : undefined,
    );
    queryClient.setQueryData(computersQuery.queryKey, [] as never);
    queryClient.setQueryData(hostedQuery.queryKey, { state: 'ok', servers: [{ server_id: 'h1', kind: 'tico', power: 'stopped' }] });
    show(<Computers />, '/settings/computers');
    const line = (await screen.findByText('Tico server')).closest('.row') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Start' }));
    const link = await within(line).findByRole('link', { name: 'Tico support' });
    expect(link.getAttribute('href')).toBe('https://help.example.test/credit');
    expect(within(line).getByText('Out of credit')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- Search settings

describe('Search settings @s1130', () => {
  const HITS = {
    settings: [
      { key: 'a', name: 'Public roadmap labels', who: 'admin', card: '#/settings/tasks/settings-roadmaps', where: 'Tasks' },
      { key: 'b', name: 'Theme', who: 'person', card: '#/settings/general', where: 'General' },
    ],
  };

  it('arrow keys move through the results @s1130', async () => {
    serve((url) => (url.startsWith('/api/v2/settings/catalog') ? ok(HITS) : undefined));
    show(<SettingsPage section="general" />, '/settings/general');
    const box = await screen.findByRole('textbox', { name: 'Search settings' });
    fireEvent.change(box, { target: { value: 'the' } });
    const first = await screen.findByRole('link', { name: 'Public roadmap labels' });
    expect(first.getAttribute('href')).toBe('/settings/tasks');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(first.getAttribute('aria-current')).toBe('true');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(screen.getByRole('link', { name: 'Theme' }).getAttribute('aria-current')).toBe('true');
    expect(first.getAttribute('aria-current')).toBeNull();
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(first.getAttribute('aria-current')).toBe('true');
  });

  it('a result opens the setting itself: its part of the section takes the focus @s1130', async () => {
    serve((url) => (url.startsWith('/api/v2/settings/catalog') ? ok(HITS) : undefined));
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const box = await screen.findByRole('textbox', { name: 'Search settings' });
    fireEvent.change(box, { target: { value: 'road' } });
    await screen.findByRole('link', { name: 'Public roadmap labels' });
    fireEvent.keyDown(box, { key: 'Enter' });
    const road = await screen.findByRole('region', { name: /Public roadmap/ });
    await waitFor(() => expect(road.contains(document.activeElement)).toBe(true));
  });
});

// ---------------------------------------------------------------- what Tico may do

describe('Settings, what Tico may change', () => {
  it('Tico may stop an agent mailing on its own, but not start it @s1111', async () => {
    serve((url) => (url === '/api/v2/bots/a1/mail-settings' ? ok({ outbound_send: false, company_send: true }) : undefined));
    queryClient.setQueryData(botMailQuery('a1').queryKey, { outbound_send: true, company_send: true } as never);
    expect(await run('settings.agentMail', { agent: 'a1', on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.agentMail', { agent: 'a1', on: false }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/a1/mail-settings', body: { outbound_send: false } });
  });

  it('Tico may take a roadmap label off, but not put one on @s1077', async () => {
    serve((url) => (url === '/api/v2/roadmaps/settings' ? ok(ROAD) : undefined));
    expect(await run('settings.roadmapLabels', { labels: ['feature', 'idea'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.roadmapLabels', { labels: [] }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/roadmaps/settings', body: { labels: [] } });
  });

  it('a product update cannot be taken back, so only the person publishes one @s1120', async () => {
    serve();
    expect(await run('settings.productUpdate', { title: 'New', bullets: ['A line'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });
});
