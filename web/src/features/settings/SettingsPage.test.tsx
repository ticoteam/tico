import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { agentsQuery } from '@/features/team/routing';
import { Toasts } from '@/kit/Toast';
import { Agents } from './Agents';
import { run } from '@/actions/run';
import {
  accessQuery,
  agentUsageQuery,
  billingQuery,
  computersQuery,
  historyQuery,
  limitsQuery,
  modelsQuery,
  screeningQuery,
  sleepQuery,
  subscriptionsQuery,
  usageQuery,
  type AccessView,
} from './queries';
import { loadSection } from './main';
import { SettingsPage } from './SettingsPage';
import { UsagePage } from './Usage';

// jsdom has no layout: the list draws every row, and End stands for reaching its end.
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: <T,>({ items, getKey, renderItem, label, onReachEnd }: { items: readonly T[]; getKey: (t: T) => string; renderItem: (t: T, i: number) => ReactNode; label?: string; onReachEnd?: () => void }) => (
    <div role="list" aria-label={label}>
      {items.map((t, i) => (
        <div key={getKey(t)}>{renderItem(t, i)}</div>
      ))}
      <button type="button" onClick={onReachEnd}>
        End
      </button>
    </div>
  ),
}));
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

/** `load`: the route's loader; what it throws goes to the router's error component (the app's is the shell's PageFault). */
function show(node: ReactNode, at: string, load?: () => Promise<unknown>) {
  const root = createRootRoute({
    loader: load,
    errorComponent: () => <p>The router’s fault page</p>,
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

const ACCESS: AccessView = {
  owner: { email: 'owner@example.test', person: 'p1' },
  people: [
    { id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true },
    { id: 'p2', name: 'Ben Sample', email: 'ben@example.test', left: false, owner: false, role: 'member', sign_in: false },
  ],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: { bot_reports_direct: true },
  you: 'owner',
  invites: {},
  invite_email: true,
};

const FIG = { runs: 0, input_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 0, est_cost_usd: 0, subscription_equiv_usd: 0, low_estimate_runs: 0, unpriced_runs: 0 };
const NO_USAGE_CHOICES = { departments: [], dimensions: {}, labels: {} };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  // An observer's queued notification must run before jsdom tears down the React window.
  notifyManager.setScheduler(queueMicrotask);
});
beforeEach(() => {
  sent = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings', () => {
  it('lists the sections as links and marks the open one @s1065', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const nav = await screen.findByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'People' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Billing' }).getAttribute('href')).toBe('/settings/billing');
  });

  it('a person row opens that person in the address, with their role to change @s1073', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const row = await screen.findByTitle('Ben Sample');
    expect(row.getAttribute('href')).toBe('/settings/people?open=p2');
  });

  it('an opened person shows their role and what can be done to them', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" open="p2" />, '/settings/people?open=p2');
    expect(await screen.findByRole('button', { name: 'Send invite again' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy();
    expect(screen.getByTitle('Ben Sample').getAttribute('href')).toBe('/settings/people');
  });

  it('a switch shows at once and is taken back when the server refuses @s1072', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'PUT' && url.endsWith('/api/v2/access/rules'))
        return new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners only' } } })));
      if (url.startsWith('/api/v2/access')) return { status: 200, body: ACCESS };
      return { status: 200, body: { pages: {} } };
    });
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="general" />, '/settings/general');
    const sw = await screen.findByRole('switch', { name: 'Reports to Tico support' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Reports to Tico support' }).getAttribute('aria-checked')).toBe('false'));
    expect(sent[0]).toMatchObject({ url: '/api/v2/access/rules', method: 'PUT', body: { bot_reports_direct: false } });
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => refuse());
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Reports to Tico support' }).getAttribute('aria-checked')).toBe('true'));
    // The short words first, the server's reason second.
    const reason = await screen.findByText('Owners only');
    expect(reason.parentElement?.textContent).toBe("Couldn't save: Owners only");
  });

  it('says when a section is for owners and admins only', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners and admins' } } }));
    show(<SettingsPage section="billing" />, '/settings/billing', () => loadSection(queryClient, 'billing'));
    expect(await screen.findByText('Owners and admins only.')).toBeTruthy();
    // The section list stays beside it.
    expect(screen.getByRole('link', { name: 'General' })).toBeTruthy();
  });

  it('a section that does not load goes to the router’s fault page', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'down', detail: 'down' } } }));
    show(<SettingsPage section="history" />, '/settings/history', () => loadSection(queryClient, 'history'));
    expect(await screen.findByText('The router’s fault page')).toBeTruthy();
  });

  it('says so when there are no API tokens', async () => {
    serve((url) => (url.endsWith('/api/v2/me/tokens') ? { status: 200, body: { tokens: [], can_create: true } } : { status: 200, body: { people: [] } }));
    show(<SettingsPage section="account" />, '/settings/account');
    expect(await screen.findByRole('heading', { name: 'No tokens' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Make token' })).toBeTruthy();
  });
});

describe('Usage', () => {
  it('an agent row opens that agent in the address @s1066 @s1086', async () => {
    serve(() => ({ status: 200, body: { from: '', to: '', totals: FIG, rows: [] } }));
    const one = { ...FIG, bot: '0190-builder', name: 'Builder', runs: 4, est_cost_usd: 2.5 };
    queryClient.setQueryData(agentsQuery.queryKey, [{ id: '0190-builder', slug: 'builder', display_name: 'Builder', resolved_model_label: 'Example model' }]);
    queryClient.setQueryData(usageQuery('7d', 'bot').queryKey, { from: '2026-11-13', to: '2026-11-19', choices: NO_USAGE_CHOICES, totals: one, rows: [one] });
    queryClient.setQueryData(usageQuery('7d', 'day').queryKey, { from: '2026-11-13', to: '2026-11-19', choices: NO_USAGE_CHOICES, totals: one, rows: [] });
    show(<UsagePage search={{}} />, '/company/usage');
    const row = await screen.findByTitle('Builder');
    expect(row.getAttribute('href')).toBe('/company/usage?open=0190-builder');
    expect(within(row).getByText('Example model')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Usage' })).toBeTruthy();
  });

  it('a limit row says when an agent is paused @s1087', async () => {
    serve(() => ({ status: 200, body: {} }));
    const none = { daily_usd: null, hourly_usd: null, per_30m_usd: null, weekly_usd: null };
    const lim = {
      ...none,
      blocked: 'monthly' as const,
      blocked_by: null,
      monthly_usd: 10,
      month_spent: 10,
      day_spent: null,
      hour_spent: null,
      week_spent: null,
      percent: 100,
      may_edit: true,
      own_monthly_usd: 10,
      own_daily_usd: null,
      own_hourly_usd: null,
      own_per_30m_usd: null,
      own_weekly_usd: null,
      source: {},
    };
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: { builder: lim },
      default: { ...none, monthly_usd: 50, count_subscription: false },
      may_edit_default: true,
      pools: null,
    });
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    const row = await screen.findByTitle('builder');
    expect(within(row).getByText('Paused')).toBeTruthy();
    expect(row.getAttribute('href')).toBe('/company/usage?view=limits&open=builder');
  });
});

describe('A model sign-in @s746', () => {
  const CODE = 'WDJB-MJHT';
  const PAGE = `https://auth.example/device?user_code=${CODE}`;

  it('keeps the provider’s code and page in the dialog only, never in the query cache', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/model-subscriptions/connect')
        ? { status: 200, body: { id: 'l1', runner_id: 'r1', kind: 'codex_login', state: 'starting' } }
        : url.endsWith('/api/v2/computers/r1/logins/l1')
          ? { status: 200, body: { id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE, lines: [`Enter ${CODE}`] } }
          : { status: 404, body: { detail: 'not_found' } },
    );
    const kind = { connect: false, help: '', kind: 'codex_login' as const, label: 'Codex', owned: false, runtime: 'codex' as const, secret_label: '' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [kind], subscriptions: [] });
    queryClient.setQueryData(computersQuery.queryKey, []);
    show(<Agents />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Codex/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Codex' });
    expect((await within(dialog).findByRole('link', { name: 'Open page' })).getAttribute('href')).toBe(PAGE);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });
});

describe('Settings › Account › API tokens', () => {
  const LIST = { tokens: [{ id: 't1', label: 'Script', created_at: '2026-11-01T00:00:00Z', last_used_at: null, revoked_at: null }], can_create: true };

  it('a new token shows once with its shell line, and is kept nowhere else @s726', async () => {
    const SECRET = 'tico_sample_secret_1234';
    serve((url, method) =>
      url.endsWith('/api/v2/me/tokens')
        ? method === 'POST'
          ? { status: 200, body: { id: 't2', token: SECRET, label: 'CI', expires_at: null } }
          : { status: 200, body: LIST }
        : { status: 200, body: { people: [] } },
    );
    show(<SettingsPage section="account" />, '/settings/account');
    fireEvent.click(await screen.findByRole('button', { name: 'Make token' }));
    const make = await screen.findByRole('dialog', { name: 'Make a token' });
    fireEvent.change(within(make).getByLabelText('Name'), { target: { value: 'CI' } });
    fireEvent.click(within(make).getByRole('button', { name: 'Make' }));
    const shown = await screen.findByRole('dialog', { name: 'Your new token' });
    expect(within(shown).getByLabelText('Token').textContent).toBe(SECRET);
    expect(within(shown).getByLabelText('Shell line').textContent).toBe(`export HUB_API_URL=${window.location.origin} HUB_TOKEN=${SECRET}`);
    expect(sent[0]).toMatchObject({ url: '/api/v2/me/tokens', method: 'POST', body: { label: 'CI' } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(SECRET);
  });

  it('Revoke asks first, and only Revoke in the question sends it @s706', async () => {
    serve((url) => (url.endsWith('/api/v2/me/tokens') ? { status: 200, body: LIST } : { status: 200, body: { people: [] } }));
    show(<SettingsPage section="account" />, '/settings/account');
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Revoke Script?' });
    expect(sent).toHaveLength(0);
    fireEvent.click(within(ask).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/me/tokens/t1/revoke', method: 'POST' }));
  });
});

describe('Settings › People › the owner', () => {
  const MINE: AccessView = { ...ACCESS, people: ACCESS.people.map((p) => (p.id === 'p2' ? { ...p, sign_in: true } : p)) };
  const held = (url: string, method: string): Reply =>
    method === 'POST' ? { status: 409, body: { error: { code: 'version_conflict', detail: 'Changed' } } } : url.startsWith('/api/v2/access') ? { status: 200, body: MINE } : { status: 200, body: {} };

  it('Make owner needs the email typed, keeps no admin unless chosen, and sends the hand-over @s753', async () => {
    serve(held);
    queryClient.setQueryData(accessQuery.queryKey, MINE);
    show(<SettingsPage section="people" open="p2" />, '/settings/people?open=p2');
    fireEvent.click(await screen.findByRole('button', { name: 'Make owner' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Make Ben Sample the owner?' });
    const go = within(ask).getByRole('button', { name: 'Make owner' });
    expect(go.hasAttribute('disabled')).toBe(true);
    expect(within(ask).getByRole('checkbox', { name: 'Keep Ana Test as an admin' }).getAttribute('aria-checked')).toBe('false');
    fireEvent.change(within(ask).getByLabelText('Type ben@example.test to confirm'), { target: { value: 'ben@example.test' } });
    fireEvent.click(go);
    await waitFor(() =>
      expect(sent[0]).toMatchObject({ url: '/api/v2/access/owner', method: 'POST', body: { person: 'p2', previous_owner_bot_admin: false, confirm: true } }),
    );
    expect(await run('settings.makeOwner', { person: 'p2', keepAdmin: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Change email needs the new address twice and sends both @s752', async () => {
    serve(held);
    queryClient.setQueryData(accessQuery.queryKey, MINE);
    show(<SettingsPage section="people" open="p1" />, '/settings/people?open=p1');
    fireEvent.click(await screen.findByRole('button', { name: 'Change email' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Change your sign-in email?' });
    const go = within(ask).getByRole('button', { name: 'Change email' });
    fireEvent.change(within(ask).getByLabelText('New sign-in email'), { target: { value: 'new@example.test' } });
    expect(go.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(ask).getByLabelText('Type it again'), { target: { value: 'new@example.test' } });
    fireEvent.click(go);
    await waitFor(() =>
      expect(sent[0]).toMatchObject({
        url: '/api/v2/access/owner/email',
        method: 'POST',
        body: { email: 'new@example.test', confirm_email: 'new@example.test', confirm: true },
      }),
    );
  });
});

describe('Settings › Computers › when idle', () => {
  it('the computers-sleep switch sends at once @s705', async () => {
    serve(() => ({ status: 404, body: { detail: 'not_found' } }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(computersQuery.queryKey, []);
    queryClient.setQueryData(sleepQuery.queryKey, { hosted: true, managed: false, hours: 2, runners: false });
    show(<SettingsPage section="computers" />, '/settings/computers');
    const sw = await screen.findByRole('switch', { name: "Agents' computers sleep too" });
    fireEvent.click(sw);
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/sleep', method: 'POST', body: { runners: true } }));
  });
});

describe('Settings › Agents › a subscription', () => {
  it('Edit renames it and sends only the name when the holder is unchanged @s747', async () => {
    serve((url, method) => (method === 'POST' ? { status: 200, body: {} } : url.startsWith('/api/v2/access') ? { status: 200, body: ACCESS } : { status: 404, body: { detail: 'not_found' } }));
    const kind = { connect: true, help: '', kind: 'claude_login' as const, label: 'Claude', owned: true, runtime: 'claude' as const, secret_label: '' };
    const plan = { id: 's1', kind: 'claude_login', name: 'Plan One', label: 'Claude', status: 'working', in_use_on: [], owner_id: 'human:p1', owner_name: 'Ana Test' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [kind], subscriptions: [plan] } as never);
    queryClient.setQueryData(computersQuery.queryKey, []);
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<Agents open="s1" />, '/settings/agents?open=s1');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const edit = await screen.findByRole('dialog', { name: 'Edit Plan One' });
    fireEvent.change(within(edit).getByLabelText('Name'), { target: { value: 'Plan Two' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/model-subscriptions/s1', method: 'POST', body: { name: 'Plan Two' } }));
    expect((sent[0]?.body as Record<string, unknown>).owner_id).toBeUndefined();
  });
});

describe('Settings › Security', () => {
  it('a detector switch sends at once; Tico may turn one on but not off, nor open Tico support @s1079', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(screeningQuery.queryKey, { modes: { webhook: 'hold', mail: 'warn' }, layers: { patterns: true, model: true }, sources: [] });
    show(<SettingsPage section="security" />, '/settings/security');
    fireEvent.click(await screen.findByRole('switch', { name: 'Patterns' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/screening', method: 'PUT', body: { patterns: false } }));
    expect(await run('settings.setDetector', { detector: 'model', on: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.setScreening', { door: 'webhook', mode: 'off' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.setSupport', { on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(1);
  });

  it('When caught sets every door but mail, which has its own row @s1079', async () => {
    const SCREEN = { modes: { webhook: 'warn', idea: 'warn', mail: 'warn' }, layers: { patterns: true, model: true }, sources: [] };
    serve(() => ({ status: 200, body: SCREEN }));
    queryClient.setQueryData(screeningQuery.queryKey, SCREEN as never);
    show(<SettingsPage section="security" />, '/settings/security');
    fireEvent.click(within(await screen.findByRole('group', { name: 'When caught' })).getByRole('button', { name: 'Hold' }));
    await waitFor(() => expect(sent.map((x) => x.body)).toEqual([{ webhook: 'hold' }, { idea: 'hold' }]));
    expect(within(screen.getByRole('group', { name: 'In mail' })).queryByRole('button', { name: 'Hold' })).toBeNull();
  });
});

describe('Settings › Models @s1076 @s710', () => {
  it('When a plan runs out has each computer’s Gateway switch, which sends at once', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm1', label: 'Sample Model', provider: 'anthropic' }] } as never);
    queryClient.setQueryData(computersQuery.queryKey, [{ id: 'r1', label: 'Studio', online: true, gateway_fallback: false }] as never);
    show(<SettingsPage section="models" />, '/settings/models');
    const out = await screen.findByRole('region', { name: 'When a plan runs out' });
    fireEvent.click(within(out).getByRole('switch', { name: 'Studio: use Tico Gateway' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/computers/r1/gateway-fallback', body: { on: true } }));
    expect(screen.queryByRole('region', { name: 'Anthropic' })).toBeNull();
  });
});

describe('Settings › Billing and History', () => {
  it('Billing shows the balance, the credit left and where the plan changes @s1081', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(billingQuery.queryKey, {
      state: 'ok',
      month: '2026-11',
      balance_micros: 12_000_000,
      min_balance_micros: 0,
      cloud: { plan: 'Team', billing_url: 'https://billing.example.test' },
      credits: [{ label: 'Welcome credit', amount_micros: 20_000_000, left_micros: 12_000_000, expires: null, current: true }],
      usage: [{ period: '2026-11', total_micros: 8_000_000 }],
      statement: { opening_micros: 0, closing_micros: 0, debits_by_product: {} },
      months: ['2026-11'],
    });
    show(<SettingsPage section="billing" />, '/settings/billing');
    expect(await screen.findByTitle('Welcome credit')).toBeTruthy();
    expect(screen.getByText('Team')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Change plan' }).getAttribute('href')).toBe('https://billing.example.test');
  });

  it('History is one list; People and Agents narrow it in the address @s1080', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(historyQuery.queryKey, {
      items: [
        { id: 'h1', at: '2026-11-18T10:00:00Z', actor: 'human:p2', kind: 'task', subject: 'Plan the launch', result: 'Done' },
        { id: 'h2', at: '2026-11-18T09:00:00Z', actor: 'bot:builder', kind: 'run', subject: 'Built it', result: '' },
      ],
      names: { 'human:p2': 'Ben Sample', 'bot:builder': 'Builder' },
    });
    const router = show(<SettingsPage section="history" who="people" />, '/settings/history?who=people');
    expect(await screen.findByTitle('Ben Sample, Plan the launch: Done')).toBeTruthy();
    expect(screen.queryByTitle('Builder, Built it')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Agents' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ who: 'agents' }));
  });
});

describe('Usage › an agent, opened', () => {
  it('shows its spend, the share from cache, its tokens by kind and its tasks @s1093', async () => {
    serve(() => ({ status: 200, body: { from: '', to: '', totals: FIG, rows: [] } }));
    const one = { ...FIG, bot: 'builder', name: 'Builder', runs: 4, est_cost_usd: 2.5, input_tokens: 10, cached_tokens: 90 };
    queryClient.setQueryData(usageQuery('7d', 'bot').queryKey, { from: '2026-11-13', to: '2026-11-19', choices: NO_USAGE_CHOICES, totals: one, rows: [one] });
    queryClient.setQueryData(usageQuery('7d', 'day').queryKey, { from: '2026-11-13', to: '2026-11-19', choices: NO_USAGE_CHOICES, totals: one, rows: [] });
    queryClient.setQueryData(agentUsageQuery('7d', 'builder').queryKey, {
      totals: one,
      tasks: [{ ...FIG, value: '12', name: 'Plan the launch', runs: 3, est_cost_usd: 2, share: 0.8 }],
      models: [],
    });
    show(<UsagePage search={{ open: 'builder' }} />, '/company/usage?open=builder');
    expect(await screen.findByRole('img', { name: 'Tokens by kind' })).toBeTruthy();
    expect(screen.getByText('90%').parentElement?.textContent).toBe('90% from cache');
    expect(screen.getByTitle('Plan the launch')).toBeTruthy();
  });
});

describe('Usage › Set limit', () => {
  it('an agent’s limit opens its dialog and saves dollars a month; Tico cannot change it @s1114', async () => {
    serve(() => ({ status: 200, body: {} }));
    const none = { daily_usd: null, hourly_usd: null, per_30m_usd: null, weekly_usd: null };
    const lim = { ...none, blocked: null, blocked_by: null, monthly_usd: 10, month_spent: 2, day_spent: null, hour_spent: null, week_spent: null, percent: 20, may_edit: true, own_monthly_usd: 10, own_daily_usd: null, own_hourly_usd: null, own_per_30m_usd: null, own_weekly_usd: null, source: {} };
    queryClient.setQueryData(limitsQuery.queryKey, { bots: { builder: lim }, default: { ...none, monthly_usd: 50, count_subscription: false }, may_edit_default: true, pools: null });
    show(<UsagePage search={{ view: 'limits', open: 'builder' }} />, '/company/usage?view=limits&open=builder');
    fireEvent.click(await screen.findByRole('button', { name: /^Limit\$/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Monthly limit for builder' });
    fireEvent.change(within(dlg).getByLabelText('Dollars a month'), { target: { value: '25' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/usage/limits/builder', method: 'PUT', body: { monthly_usd: 25 } }));
    expect(await run('usage.setLimit', { agent: 'builder', monthly: 500 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});
