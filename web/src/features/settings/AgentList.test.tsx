import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { agentsQuery } from '@/features/team/exports';
import { Toasts } from '@/kit/Toast';
import { botsQuery, limitsQuery, modelsQuery, providersQuery, subscriptionsQuery } from './queries';
import { SettingsPage, hitHref } from './SettingsPage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

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
  it('checking agents shows how many, and Change model moves each one on its own harness', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByText('2 agents')).toBeTruthy();
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

  it('the search narrows the list by name, team or model', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Search agents or models' }), { target: { value: 'growth' } });
    expect(await screen.findByText('1 agent')).toBeTruthy();
    expect(screen.queryByTitle('Alpha Agent')).toBeNull();
    expect(screen.getByTitle('Beta Agent')).toBeTruthy();
  });

  it('a retired agent can be brought back by its owner', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: /Retired/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/c3/restore', method: 'POST' }));
  });
});

describe('Settings actions', () => {
  it('person-only changes are refused when Tico asks, and nothing is sent', async () => {
    serve(() => ({ status: 200, body: {} }));
    show(<SettingsPage section="agents" />, '/settings/agents');
    await screen.findByText('2 agents');
    const asks: [string, unknown][] = [
      ['settings.makeOwner', { person: 'p2', keepAdmin: true }],
      ['settings.changeEmail', { email: 'new@example.test', again: 'new@example.test' }],
      ['settings.removePerson', { person: 'p2' }],
      ['settings.agentOwners', { agent: 'a1', add: ['p2'], remove: [] }],
      ['settings.agentMail', { agent: 'a1', on: true }],
    ];
    for (const [id, input] of asks) expect(await run(id, input, { via: 'tico' })).toEqual({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });

  it('the default model shows at once and goes back when the server refuses', async () => {
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

  it('offers only the settings the reader may change', async () => {
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
