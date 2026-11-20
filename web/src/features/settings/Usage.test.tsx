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
import { agentUsageQuery, limitsQuery, taskCapsQuery, usageQuery } from './queries';
import { loadUsage } from './routing';
import { UsagePage } from './Usage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let read: string[] = [];
/** Anything `reply` leaves (undefined) answers 200 with {}. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') read.push(String(url));
    else sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
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

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const NO_USAGE_CHOICES = { departments: [], dimensions: {}, labels: {} };
const FIG = { runs: 0, input_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 0, est_cost_usd: 0, subscription_equiv_usd: 0, low_estimate_runs: 0, unpriced_runs: 0 };
const NONE = { daily_usd: null, hourly_usd: null, per_30m_usd: null, weekly_usd: null };
const LIM = {
  ...NONE,
  blocked: null,
  blocked_by: null,
  monthly_usd: 10,
  month_spent: 2,
  day_spent: null,
  hour_spent: null,
  week_spent: null,
  percent: 20,
  may_edit: true,
  own_monthly_usd: 10,
  own_daily_usd: null,
  own_hourly_usd: null,
  own_per_30m_usd: null,
  own_weekly_usd: null,
  source: {},
};
const pool = (o: Record<string, unknown>) => ({ monthly_usd: null, month_spent: 0, percent: 0, blocked: null, spent: {}, ...o });
const POOLS = {
  company: pool({ monthly_usd: 500, month_spent: 100, percent: 20 }),
  groups: {
    g1: pool({ name: 'Product', parent: null, monthly_usd: 200, month_spent: 50, percent: 25 }),
    g2: pool({ name: 'Design', parent: 'g1', month_spent: 20 }),
  },
};
const CAPS = { caps: [{ sender: 'bot:builder', receiver: 'person:p1', per_day: 5, count: 2, closed: 0, updated_at: '', updated_by: '' }], proposals: [] };
const AGENT = { id: 'builder', slug: 'builder', display_name: 'Builder', team: 'g2', revision: 7 };
const DIRECTORY = {
  actors: [
    { id: 'human:p1', name: 'Ana Sample', kind: 'person' },
    { id: 'bot:builder', name: 'Builder', kind: 'agent' },
  ],
  byId: { 'human:p1': { id: 'human:p1', name: 'Ana Sample', kind: 'person' }, 'bot:builder': { id: 'bot:builder', name: 'Builder', kind: 'agent' } },
};

function seedLimits() {
  queryClient.setQueryData(limitsQuery.queryKey, { bots: { builder: LIM }, default: { ...NONE, monthly_usd: 50, count_subscription: false }, may_edit_default: true, pools: POOLS });
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  read = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

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
    serve();
    const lim = { ...LIM, blocked: 'monthly' as const, month_spent: 10, percent: 100 };
    queryClient.setQueryData(limitsQuery.queryKey, { bots: { builder: lim }, default: { ...NONE, monthly_usd: 50, count_subscription: false }, may_edit_default: true, pools: null });
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    const row = await screen.findByTitle('builder');
    expect(within(row).getByText('Paused')).toBeTruthy();
    expect(row.getAttribute('href')).toBe('/company/usage?view=limits&open=builder');
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
    serve();
    queryClient.setQueryData(limitsQuery.queryKey, { bots: { builder: LIM }, default: { ...NONE, monthly_usd: 50, count_subscription: false }, may_edit_default: true, pools: null });
    show(<UsagePage search={{ view: 'limits', open: 'builder' }} />, '/company/usage?view=limits&open=builder');
    fireEvent.click(await screen.findByRole('button', { name: /^Limit\$/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Monthly limit for builder' });
    fireEvent.change(within(dlg).getByLabelText('Dollars a month'), { target: { value: '25' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/usage/limits/builder', method: 'PUT', body: { monthly_usd: 25 } }));
    expect(await run('usage.setLimit', { agent: 'builder', monthly: 500 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Usage › Spend: a filter', () => {
  it('the filter narrows the read by a team or a model, nothing else @s1086', async () => {
    serve(() => ok({ from: '', to: '', totals: FIG, rows: [], departments: ['Eng', 'Ops'], dimensions: { model: ['haiku', 'opus'], harness: ['claude', 'codex'] }, dimension_labels: {} }));
    show(<UsagePage search={{}} />, '/company/usage');
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    expect(screen.queryByRole('combobox', { name: 'Harness' })).toBeNull();
    await pick(await screen.findByRole('combobox', { name: 'Model' }), /^haiku$/);
    await waitFor(() => expect(read.some((u) => u.includes('model=haiku') && u.includes('group=bot'))).toBe(true));
    expect(read.filter((u) => u.includes('group=bot') && !u.includes('model=')).length).toBe(1);
    expect(await screen.findByRole('button', { name: 'Filter, 1 on', hidden: true })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Custom' })).toBeNull();
  });
});

describe('Loading Usage', () => {
  it('starts the chart and the selected model grouping together', async () => {
    let releaseDay!: () => void;
    const day = new Promise<void>((resolve) => { releaseDay = resolve; });
    serve(async (url) => {
      if (url.includes('group=day')) await day;
      return ok({ from: '', to: '', totals: FIG, rows: [] });
    });
    await loadUsage(queryClient, { by: 'model' });
    expect(read.map((url) => new URLSearchParams(url.split('?')[1]).get('group'))).toEqual(['day', 'model']);
    expect(queryClient.getQueryData(usageQuery('7d', 'model').queryKey)?.totals).toEqual(FIG);
    const chart = queryClient.ensureQueryData(usageQuery('7d', 'day'));
    releaseDay();
    await chart;
  });
});

describe('Usage › Limits: company, teams, Add limit, the panel', () => {
  it('shows the company and the team tree, and Add limit sets a team’s @s1087 @s312', async () => {
    serve();
    seedLimits();
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    // the company row is named for the company (t-limits)
    expect((await screen.findByTitle('Sample Co')).getAttribute('href')).toContain('open=pool');
    expect(screen.getByTitle('Product')).toBeTruthy();
    const design = screen.getByTitle('Design').querySelector('.row-title') as HTMLElement;
    expect(design.style.getPropertyValue('--depth')).toBe('1');
    fireEvent.click(screen.getByRole('button', { name: 'Add limit' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add limit' });
    await pick(within(dlg).getByRole('combobox', { name: 'For' }), /^Design/);
    fireEvent.change(within(dlg).getByLabelText('Dollars a month'), { target: { value: '40' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/usage/pools/groups/g2', method: 'PUT', body: { monthly_usd: 40 } }));
    expect(read.some((u) => u.startsWith('/api/v2/usage?'))).toBe(false);
  });

  it('Tico may lower a limit but never raise or remove one @s313', async () => {
    serve();
    seedLimits();
    expect(await run('usage.setPoolLimit', { scope: 'company', monthly: 900 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setPoolLimit', { scope: 'company' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setDefaultLimit', { monthly: 100 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setLimit', { agent: 'builder', monthly: 20 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setPoolLimit', { scope: 'company', monthly: 300 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('usage.setLimit', { agent: 'builder', monthly: 5 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('usage.setDefaultLimit', { monthly: 25 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.map((s) => [s.url, s.body])).toEqual([
      ['/api/v2/usage/pools/company', { monthly_usd: 300 }],
      ['/api/v2/usage/limits/builder', { monthly_usd: 5 }],
      [expect.stringContaining('/api/v2/usage/limits'), expect.objectContaining({ monthly_usd: 25 })],
    ]);
  });

  it('Count plan runs is a switch; Tico may turn it on, never off @s1087', async () => {
    serve();
    seedLimits();
    expect(await run('usage.countPlanRuns', { on: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.countPlanRuns', { on: true }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.map((x) => [x.url, x.body])).toEqual([['/api/v2/usage/limits', { count_subscription: true }]]);
  });

  it('an opened agent shows where its money went, its models and its task caps @s368 @s1087', async () => {
    serve((url, method) => (method === 'GET' && url === '/api/v2/task-caps' ? ok(CAPS) : ok({})));
    seedLimits();
    queryClient.setQueryData(['directory'], DIRECTORY);
    queryClient.setQueryData(agentsQuery.queryKey, [AGENT] as never);
    queryClient.setQueryData(agentUsageQuery('month', 'builder').queryKey, {
      totals: { ...FIG, runs: 9 },
      tasks: [{ ...FIG, value: '12', name: 'Plan the launch', runs: 3, est_cost_usd: 2 }],
      models: [{ ...FIG, value: 'opus-5', name: 'opus-5', runs: 9, est_cost_usd: 4 }],
    });
    queryClient.setQueryData(taskCapsQuery.queryKey, CAPS as never);
    show(<UsagePage search={{ view: 'limits', open: 'builder' }} />, '/company/usage?view=limits&open=builder');
    expect(await screen.findByText('Went to')).toBeTruthy();
    expect(screen.getByTitle('Plan the launch')).toBeTruthy();
    expect(screen.getByTitle('opus-5')).toBeTruthy();
    const cap = screen.getByTitle('To Ana Sample');
    expect(within(cap).getByText('2 of 5')).toBeTruthy();
    fireEvent.click(within(cap).getByRole('button', { name: 'Change' }));
    const dlg = await screen.findByRole('dialog', { name: 'Tasks a day from Builder' });
    fireEvent.change(within(dlg).getByLabelText('Tasks a day'), { target: { value: '3' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/task-caps', method: 'PUT', body: { sender: 'bot:builder', receiver: 'person:p1', per_day: 3 } }));
    fireEvent.click(within(screen.getByTitle('To Ana Sample')).getByRole('button', { name: 'Change' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Tasks a day from Builder' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(sent[1]).toMatchObject({ url: '/api/v2/task-caps?sender=bot%3Abuilder&receiver=person%3Ap1', method: 'DELETE' }));
  });

  it('Tico may add or lower a task cap, never raise one @s368', async () => {
    serve();
    queryClient.setQueryData(agentsQuery.queryKey, [AGENT] as never);
    queryClient.setQueryData(taskCapsQuery.queryKey, CAPS as never);
    expect(await run('usage.setTaskCap', { sender: 'bot:builder', receiver: 'person:p1', perDay: 10 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setTaskCap', { sender: 'bot:builder', receiver: 'person:p1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('usage.setTaskCap', { sender: 'bot:builder', receiver: 'person:p1', perDay: 1 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent).toHaveLength(1);
  });

  it('a team opened shows its teams, and Remove limit takes its limit off @s1087', async () => {
    serve();
    seedLimits();
    queryClient.setQueryData(agentsQuery.queryKey, [AGENT] as never);
    show(<UsagePage search={{ view: 'limits', open: 'pool:g1' }} />, '/company/usage?view=limits&open=pool:g1');
    const panel = await screen.findByRole('complementary', { name: 'Product' });
    expect(within(panel).getByTitle('Design')).toBeTruthy();
    expect(within(panel).getByTitle('Builder')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove limit' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/usage/pools/groups/g1', method: 'PUT', body: { monthly_usd: null } }));
  });
});

describe('Usage › Limits, clear the automatic queue @s1115', () => {
  beforeEach(() =>
    queryClient.setQueryData(limitsQuery.queryKey, { bots: {}, default: { ...NONE, monthly_usd: null, count_subscription: false }, may_edit_default: true, pools: null }),
  );

  it('asks first, then clears; Tico cannot', async () => {
    serve((url) => (url === '/api/v2/usage/queues/purge' ? ok({ cancelled: 3, kept_people: 1, still_running: 0 }) : undefined));
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    fireEvent.click(await screen.findByRole('button', { name: 'Clear queue' }));
    const dlg = await screen.findByRole('alertdialog', { name: 'Clear the automatic queue?' });
    expect(sent).toHaveLength(0);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Clear queue' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/usage/queues/purge', method: 'POST', body: { scope: 'company' } }));
    sent = [];
    expect(await run('usage.clearQueue', { scope: 'company' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });

  it('can be limited to one team @s1115', async () => {
    serve();
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: {},
      default: { ...NONE, monthly_usd: null, count_subscription: false },
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
