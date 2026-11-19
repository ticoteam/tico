import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { AddComputer } from './Agents';
import { AddPersonButton } from './People';
import { Account, General, People, Security } from './Basics';
import { SettingsPage } from './SettingsPage';
import { agentsQuery } from '@/features/team/exports';
import {
  accessQuery,
  botAccessQuery,
  botsQuery,
  computersQuery,
  humansQuery,
  limitsQuery,
  modelsQuery,
  screeningQuery,
  subscriptionsQuery,
  type AccessFull,
} from './queries';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method) ?? (String(url) === '/api/v2/access' ? { status: 200, body: ACCESS } : { status: 200, body: {} });
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown): Reply => ({ status: 200, body });

function show(node: ReactNode, at = '/settings') {
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
}

const unfold = async (name: string) => {
  const region = await screen.findByRole('region', { name });
  fireEvent.click(within(region).getAllByRole('button', { expanded: false })[0] as HTMLElement);
  return region;
};

const ACCESS: AccessFull = {
  owner: { email: 'ana@example.test', person: 'p1' },
  people: [
    { id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true },
    { id: 'p2', name: 'Ben Sample', email: 'ben@example.test', left: false, owner: false, role: 'member', sign_in: false },
    { id: 'p3', name: 'Cal Former', email: 'cal@example.test', left: true, owner: false, role: 'member', sign_in: false, removable: true },
  ],
  allowed: ['ben@example.test'],
  allowed_domains: ['other.test'],
  revision: 4,
  rules: { bot_reports_direct: true },
  you: 'owner',
  invites: {},
  invite_email: true,
  home_domain: 'example.test',
  join: 'admin',
  member_bot_limit: 3,
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'ana@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
  queryClient.setQueryData(accessQuery.queryKey, ACCESS);
});
afterEach(() => vi.unstubAllGlobals());

describe('General: icon and repositories', () => {
  const general = () => {
    serve((url) => {
      if (url.startsWith('/api/v2/repositories'))
        return ok({
          github_connected: true,
          github_installed: true,
          new_bot_default: 'own',
          repositories: [
            { full_name: 'acme/web', enabled: true, reachable: true, default_branch: 'main', bot_repo: false, last_seen_at: null, setup_command: null, setup_source: null },
            { full_name: 'acme/api', enabled: false, reachable: true, default_branch: 'main', bot_repo: false, last_seen_at: null, setup_command: null, setup_source: null },
          ],
        });
      return undefined;
    });
    show(<General />);
  };

  it('shows the icon and the Repositories card; Add goes through an action @s1072', async () => {
    general();
    expect(await screen.findByRole('img', { name: 'Team icon' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change' })).toBeTruthy();
    expect(within(await screen.findByRole('region', { name: 'Repositories' })).getByText('acme/web')).toBeTruthy();
    const off = await unfold('Not in use');
    fireEvent.click(within(off).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/repositories/acme/api', method: 'PUT', body: { enabled: true } }));
  });

  it('Tico may not change the icon or make a repository @s1072', async () => {
    serve(() => undefined);
    const file = new File(['x'], 'icon.png', { type: 'image/png' });
    expect(await run('settings.teamIcon', { file }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.createRepo', { name: 'site' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('People: joining, left and adds', () => {
  it('adds a person, and restores someone who left @s1073', async () => {
    serve(() => undefined);
    show(
      <>
        <AddPersonButton />
        <People />
      </>,
      '/settings/people',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Add person' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add a person' });
    fireEvent.change(within(dlg).getByRole('textbox', { name: 'Email' }), { target: { value: 'dee@example.test' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Invite' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/access/humans', method: 'POST', body: { email: 'dee@example.test', name: '' } }));
    const left = await unfold('Left');
    fireEvent.click(within(left).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/access/humans/p3', method: 'POST', body: { left: false } }));
  });

  it('widening who can join, raising the limit and restoring are closed to Tico; tightening is open @s1073', async () => {
    serve(() => undefined);
    expect(await run('settings.setJoining', { allowed: ACCESS.allowed, domains: ['other.test', 'example.test'] }, { via: 'tico' })).toMatchObject({
      ok: false,
      error: 'not_allowed',
    });
    expect(await run('settings.agentLimit', { limit: 5 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.restorePerson', { person: 'p3' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect(await run('settings.setJoining', { allowed: [], domains: [] }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent[0]).toMatchObject({ url: '/api/v2/access/allow', method: 'PUT', body: { allowed: [], allowed_domains: [] } });
  });
});

describe("API tokens: everyone's, and an expiry", () => {
  it("an owner sees another person's token and revokes it after a confirm; Tico cannot @s706", async () => {
    const mine = { id: 'k1', label: 'Laptop', created_at: '2026-11-01T00:00:00Z', last_used_at: null, expires_at: null, revoked_at: null };
    serve((url) => {
      if (url === '/api/v2/me/tokens') return ok({ tokens: [mine], can_create: true });
      if (url === '/api/v2/access/tokens')
        return ok({ tokens: [mine, { ...mine, id: 'k2', label: 'Script', human: 'p2', name: 'Ben Sample', email: 'ben@example.test' }] });
      return undefined;
    });
    queryClient.setQueryData(humansQuery.queryKey, []);
    show(<Account />);
    const all = await unfold("Everyone's tokens");
    expect(within(all).queryByText('Laptop')).toBeNull();
    expect(within(all).getByText('Ben Sample')).toBeTruthy();
    fireEvent.click(within(all).getByRole('button', { name: 'Revoke' }));
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Revoke Script?' })).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/me/tokens/k2/revoke', method: 'POST', body: {} }));
    expect(await run('settings.revokeAnyToken', { token: 'k2' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('a new token takes an expiry in days @s706', async () => {
    serve((url, method) => (url === '/api/v2/me/tokens' && method === 'POST' ? ok({ id: 'k3', token: 'secret-value', label: 'CI', expires_at: null }) : undefined));
    const r = await run('settings.createToken', { label: 'CI', days: 30 }, { via: 'click' });
    expect(r).toMatchObject({ ok: true });
    expect(sent).toContainEqual({ url: '/api/v2/me/tokens', method: 'POST', body: { label: 'CI', expires_in_days: 30 } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('secret-value');
  });
});

describe('Security: caught lately and Tico support signing in', () => {
  const security = () => {
    queryClient.setQueryData(screeningQuery.queryKey, {
      modes: { webhook: 'warn' },
      layers: { patterns: true, model: true },
      sources: [{ id: 'webhook', name: 'Webhooks' }],
      recent: [{ at: '2026-11-18T10:00:00Z', action: 'held', source: 'webhook', score: 0.91, task: 't9' }],
    } as never);
    serve((url, method) => {
      if (url === '/api/v2/support/access' && method === 'GET')
        return ok({ available: true, active: null, last: null, sessions: [], can_start: true, can_full: true });
      if (url === '/api/v2/support/tickets') return ok({ tickets: [{ id: 't1', status: 'open', created_at: '2026-11-18T09:00:00Z' }] });
      if (url.startsWith('/api/v2/support')) return ok({ available: false });
      return undefined;
    });
    show(<Security />);
  };

  it('lists the holds and warnings @s1079', async () => {
    security();
    const screening = await screen.findByRole('region', { name: 'Screening' });
    fireEvent.click(within(screening).getByRole('button', { name: 'Show' }));
    const caught = await screen.findByRole('dialog', { name: 'Held and warned' });
    expect(within(caught).getByText('Held · webhook · 91%')).toBeTruthy();
    expect(within(caught).getByRole('link').getAttribute('href')).toBe('/task/t9');
  });

  it('lets Tico support in against the open request; letting in is closed to Tico, Stop is open @s1124', async () => {
    security();
    const support = await screen.findByRole('region', { name: 'Tico support' });
    fireEvent.click(await within(support).findByRole('switch', { name: 'Tico support may sign in' }));
    const dlg = await screen.findByRole('alertdialog', { name: 'Let Tico support sign in?' });
    fireEvent.click(await within(dlg).findByRole('button', { name: 'Let in' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/support/access', method: 'POST', body: { ticket_id: 't1', tier: 'app', hours: 72 } }));
    expect(await run('settings.letSupportIn', { ticket: 't1', tier: 'full', hours: 1 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.stopSupport', { session: 's1' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent).toContainEqual({ url: '/api/v2/support/access/s1/stop', method: 'POST', body: {} });
  });
});

describe('Agent access: groups and agents', () => {
  it('Private can name a group and another agent, not only people @s1112', async () => {
    const EVERY = { everyone: true, people: [], teams: [], bots: [] };
    const ACTORS = [
      { id: 'bot:a1', kind: 'agent', name: 'Alpha Agent', slug: 'alpha', team: 'Product', hue: 1 },
      { id: 'human:p1', kind: 'person', name: 'Ana Test', hue: 3 },
    ];
    queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [] });
    queryClient.setQueryData(agentsQuery.queryKey, [
      { id: 'a1', slug: 'alpha', harness: 'claude', model: 'model-one', resolved_model_label: 'Model One', revision: 3, can_manage: true, machine: { generation: 7 } },
    ] as never);
    queryClient.setQueryData(modelsQuery.queryKey, { models: [], enabled_providers: [], harnesses: [], default: { model: '' } } as never);
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: {},
      default: { count_subscription: false, daily_usd: null, hourly_usd: null, monthly_usd: null, per_30m_usd: null, weekly_usd: null },
      may_edit_default: true,
      pools: null,
    } as never);
    queryClient.setQueryData(botsQuery.queryKey, [
      { id: 'a1', slug: 'alpha', display_name: 'Alpha Agent', state: 'active', owner_id: 'human:p1', bot_owners: [] },
      { id: 'b2', slug: 'beta', display_name: 'Beta Agent', state: 'active', owner_id: 'human:p1', bot_owners: [] },
    ]);
    queryClient.setQueryData(botAccessQuery('a1').queryKey, {
      bot: 'a1',
      see: EVERY,
      read: EVERY,
      write: EVERY,
      teams: [{ id: 'product', name: 'Product' }],
      revision: 2,
    });
    serve((_url, method) => (method === 'GET' ? { status: 404, body: { detail: 'not_found' } } : ok({})));
    show(<SettingsPage section="agents" open="alpha" />, '/settings/agents?open=alpha');
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
});

describe('Computers: add one', () => {
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
