import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useLocation, useSearch } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { agentsQuery, computersQuery as teamComputers } from '@/features/team/exports';
import { Toasts } from '@/kit/Toast';
import { dragTo } from '@/test/drag';
import { AddComputer, Agents, Computers } from './Agents';
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
  type Subscription,
} from './queries';
import { SettingsPage, hitHref } from './SettingsPage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// Vitest skips CSS imports, so read the stylesheet for the cards layout test.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url;

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
/** Anything `reply` leaves (undefined) answers 200 with {} or the default idle view. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = (await reply(String(url), method)) ?? ok(url === '/api/v2/sleep' ? { sleep: { hosted: false } } : {});
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

});

describe('Settings › Providers', () => {
  const claude = { kind: 'claude_token' as const, label: 'Claude plan', runtime: 'claude' as const, secret_label: 'Setup token', help: '', owned: true, connect: true };

  it('adds Claude from the kinds the server lists and offers a setup token when Connect is offline', async () => {
    const catalog = {
      kinds: [claude, { ...claude, kind: 'toString', label: 'Unknown plan' }],
      subscriptions: [],
    };
    queryClient.setQueryData([...subscriptionsQuery.queryKey], catalog);
    let save!: (reply: Reply) => void;
    const pending = new Promise<Reply>((resolve) => { save = resolve; });
    serve((url, method) => {
      if (method === 'GET') return url === '/api/v2/model-subscriptions' ? ok(catalog) : undefined;
      if (url.endsWith('/connect')) return { status: 409, body: { error: { code: 'offline', detail: 'No computer can sign in' } } };
      return pending;
    });
    show(<SettingsPage section="providers" />, '/settings/providers');
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    expect(screen.queryByRole('menuitem', { name: /Unknown plan/ })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Claude plan/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Paste instead' }));
    expect(sent).toEqual([{ url: '/api/v2/model-subscriptions/connect', method: 'POST', body: { kind: 'claude_token' } }]);
    fireEvent.change(await screen.findByLabelText('Setup token'), { target: { value: 'sk-ant-test-token' } });
    const submit = screen.getByRole<HTMLButtonElement>('button', { name: 'Connect' });
    fireEvent.click(submit);
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    await waitFor(() => expect(sent.at(-1)).toEqual({
      url: '/api/v2/model-subscriptions',
      method: 'POST',
      body: { kind: 'claude_token', secret: 'sk-ant-test-token' },
    }));
    expect(sent).toHaveLength(2);
    save(ok({ id: 'plan-1', name: 'Work plan' }));
    expect(await screen.findByText('Work plan connected')).toBeTruthy();
  });

  const plan: Subscription = {
    id: 'plan-1', kind: 'claude_login', label: 'Claude plan', name: 'Work plan',
    in_use_on: [], computers: [], owner_id: 'human:p1', owner_name: 'Ana Test', added_by: 'human:p1', added_by_name: 'Ana Test',
    status: 'needs_reconnect', status_at: null, status_detail: '', expired: false, runtime: 'claude', preview: '', usage: null,
    order: null, revision: 1, created_at: '', updated_at: '',
  };
  const restart = { status: 409, body: { error: { code: 'confirm_restart', detail: '2 running runs will stop and start again', running: 2 } } };

  it('reconnects the selected plan only after confirming the restart', async () => {
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [plan] });
    const url = '/api/v2/model-subscriptions/connect';
    serve((path) => {
      if (path !== url) return;
      if ((sent.at(-1)?.body as { confirm?: boolean } | undefined)?.confirm) {
        return ok({ id: 'login-1', runner_id: 'r1', state: 'waiting', url: 'https://example.test/sign-in' });
      }
      return restart;
    });
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Restart runs?' });
    expect(ask.textContent).toContain(restart.body.error.detail);
    expect(sent).toEqual([{ url, method: 'POST', body: { subscription: plan.id } }]);
    fireEvent.click(within(ask).getByRole('button', { name: 'Reconnect' }));
    expect(await screen.findByRole('link', { name: 'Open Claude' })).toBeTruthy();
    expect(sent.at(-1)).toEqual({ url, method: 'POST', body: { subscription: plan.id, confirm: true } });
  });

  it('pasting during Reconnect updates the same plan and waits for restart approval', async () => {
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [plan] });
    const url = `/api/v2/model-subscriptions/${plan.id}`;
    let save!: (reply: Reply) => void;
    const pending = new Promise<Reply>((resolve) => { save = resolve; });
    serve((path, method) => {
      if (path.endsWith('/connect')) return { status: 409, body: { error: { code: 'offline', detail: 'No computer can sign in' } } };
      if (path === url && method === 'POST') return sent.filter((s) => s.url === url).length > 1 ? pending : restart;
      if (path === '/api/v2/model-subscriptions') return ok({ kinds: [], subscriptions: [plan] });
    });
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Paste instead' }));
    fireEvent.change(await screen.findByLabelText('Setup token'), { target: { value: 'sk-ant-replacement-token' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reconnect' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Restart runs?' });
    expect(sent.at(-1)).toEqual({ url, method: 'POST', body: { secret: 'sk-ant-replacement-token' } });
    const approve = within(ask).getByRole<HTMLButtonElement>('button', { name: 'Reconnect' });
    fireEvent.click(approve);
    expect(approve.disabled).toBe(true);
    fireEvent.click(approve);
    await waitFor(() => expect(sent.at(-1)).toEqual({ url, method: 'POST', body: { secret: 'sk-ant-replacement-token', confirm: true } }));
    expect(sent.filter((s) => s.url === url)).toHaveLength(2);
    save(ok(plan));
    expect(await screen.findByText('Work plan connected')).toBeTruthy();
  });

  it.each(['offline', 'runner_version'])('carries restart approval into paste when sign-in returns %s', async (code) => {
    const catalog = { kinds: [], subscriptions: [plan] };
    queryClient.setQueryData(subscriptionsQuery.queryKey, catalog);
    serve((url, method) => {
      if (url.endsWith('/connect')) {
        const approved = (sent.at(-1)?.body as { confirm?: boolean }).confirm;
        return approved ? { status: 409, body: { error: { code, detail: 'No computer can sign in' } } } : restart;
      }
      if (method === 'POST') return ok(plan);
      return ok(catalog);
    });
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Restart runs?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'Reconnect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Paste instead' }));
    fireEvent.change(await screen.findByLabelText('Setup token'), { target: { value: 'sk-ant-replacement-token' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reconnect' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({
      url: `/api/v2/model-subscriptions/${plan.id}`, body: { secret: 'sk-ant-replacement-token', confirm: true },
    }));
  });

  it('warns before opening sign-in on someone else’s computer and waits for cancellation before paste', async () => {
    const catalog = { kinds: [], subscriptions: [plan] };
    const login = { id: 'login-1', runner_id: 'r1', state: 'waiting', url: 'https://example.test/sign-in' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, catalog);
    let finishCancel!: (reply: Reply) => void;
    const pending = new Promise<Reply>((resolve) => { finishCancel = resolve; });
    serve((url) => {
      if (url.endsWith('/connect')) return ok({ ...login, computer: 'Acme Mac', not_yours: true });
      if (url.endsWith('/cancel')) return sent.filter((s) => s.url === url).length === 1
        ? pending : ok({ ...login, state: 'canceled' });
      if (url.endsWith('/logins/login-1')) return ok(login);
      if (url === '/api/v2/model-subscriptions') return ok(catalog);
    });
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    expect(await screen.findByRole('link', { name: 'Open Claude' })).toBeTruthy();
    expect(screen.getByRole('dialog', { description: /credential passes through someone else's computer.*Acme Mac/i })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Paste instead' }));
    expect(screen.queryByLabelText('Setup token')).toBeNull();
    finishCancel({ status: 409, body: { error: { code: 'state', detail: 'The sign-in is finishing' } } });
    expect(await screen.findByText('The sign-in is finishing')).toBeTruthy();
    expect(screen.queryByLabelText('Setup token')).toBeNull();
    expect(sent.map(({ url }) => url)).toEqual(['/api/v2/model-subscriptions/connect', '/api/v2/computers/r1/logins/login-1/cancel']);
    fireEvent.click(screen.getByRole('button', { name: 'Paste instead' }));
    expect(await screen.findByLabelText('Setup token')).toBeTruthy();
  });

  it('follows a sign-in that queues after the computer wake deadline until the server ends it', async () => {
    vi.useFakeTimers();
    try {
      queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [plan] });
      let finishStart!: (reply: Reply) => void;
      const pending = new Promise<Reply>((resolve) => { finishStart = resolve; });
      let expired = false;
      serve((url) => {
        if (url.endsWith('/connect')) return pending;
        if (url.endsWith('/logins/login-1')) return ok({ state: expired ? 'expired' : 'waiting', url: 'https://example.test/sign-in' });
      });
      show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
      await act(() => vi.advanceTimersByTimeAsync(0));
      fireEvent.click(screen.getByRole('button', { name: 'More for Work plan' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Reconnect' }));
      await act(() => vi.advanceTimersByTimeAsync(5 * 60_000));
      expect(sent).toHaveLength(1);
      expect(screen.queryByRole('button', { name: 'Paste instead' })).toBeNull();
      await act(async () => { finishStart(ok({ id: 'login-1', runner_id: 'r1', state: 'waiting' })); });
      expect(screen.getByRole('link', { name: 'Open Claude' })).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Paste instead' })).toBeNull();
      expired = true;
      await act(() => vi.advanceTimersByTimeAsync(2000));
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Paste instead' })); });
      expect(sent).toHaveLength(1);
      expect(screen.getByLabelText('Setup token')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels a sign-in queued after the dialog was dismissed', async () => {
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [plan] });
    let finishStart!: (reply: Reply) => void;
    const pending = new Promise<Reply>((resolve) => { finishStart = resolve; });
    const connect = '/api/v2/model-subscriptions/connect';
    const cancel = '/api/v2/computers/r1/logins/login-1/cancel';
    serve((url) => {
      if (url === connect) return pending;
      if (url === cancel) return ok({ state: 'canceled' });
    });
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    await waitFor(() => expect(sent.map(({ url }) => url)).toEqual([connect]));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Reconnect Claude plan' })).toBeNull();
    await act(async () => { finishStart(ok({ id: 'login-1', runner_id: 'r1', state: 'requested' })); });
    expect(sent.map(({ url }) => url)).toEqual([connect, cancel]);
  });

  it.each(['member', 'admin'])('shows Add and Reconnect according to the %s role', async (role) => {
    queryClient.setQueryData(['me'], { id: 'human:p2', email: 'ben@example.test', role: 'person', slug: null });
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [claude], subscriptions: [{ ...plan, can_remove: true }] });
    serve((url) => url === '/api/v2/access'
      ? role === 'admin' ? ok({ you: role }) : { status: 403, body: { error: { code: 'forbidden' } } }
      : undefined);
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    expect(await screen.findByTitle(plan.name)).toBeTruthy();
    if (role === 'admin') {
      expect(await screen.findByRole('button', { name: 'Add' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'More for Work plan' }));
      expect(await screen.findByRole('menuitem', { name: 'Reconnect' })).toBeTruthy();
    } else {
      expect(screen.queryByRole('button', { name: 'Add' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'More for Work plan' }));
      expect(await screen.findByRole('menuitem', { name: 'Remove' })).toBeTruthy();
      expect(screen.getAllByRole('menuitem')).toHaveLength(1);
    }
  });

  it('does not offer a paste write when another sign-in is already in progress', async () => {
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [plan] });
    serve((url) => url.endsWith('/connect')
      ? { status: 409, body: { error: { code: 'state', detail: 'Someone else is connecting' } } } : undefined);
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Work plan' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reconnect' }));
    expect(await screen.findByText('Someone else is connecting')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Paste instead' })).toBeNull();
  });

  it('replaces a company key in place after restart confirmation', async () => {
    const key = { ...plan, id: 'key-1', name: 'Company key', kind: 'anthropic_key' as const, owner_id: '', status: 'working' as const };
    const catalog = { kinds: [], subscriptions: [key] };
    const path = '/api/v2/model-subscriptions/key-1';
    queryClient.setQueryData(subscriptionsQuery.queryKey, catalog);
    serve((url, method) => {
      if (url === path && method === 'POST') return sent.length === 1 ? restart : ok(key);
      if (url === '/api/v2/model-subscriptions') return ok(catalog);
    });
    show(<SettingsPage section="providers" />, '/settings/providers');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Company key' }));
    expect(screen.queryByRole('menuitem', { name: 'Check use' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Replace key' }));
    const dialog = await screen.findByRole('dialog', { name: 'Replace key for Company key' });
    fireEvent.change(within(dialog).getByLabelText('API key'), { target: { value: 'made-up-api-key' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace key' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Restart runs?' });
    expect(sent).toEqual([{ url: path, method: 'POST', body: { secret: 'made-up-api-key' } }]);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Replace key' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(sent.at(-1)).toEqual({ url: path, method: 'POST', body: { secret: 'made-up-api-key', confirm: true } });
  });

  it('groups providers by owner and counts each visible agent once per pool @s709', async () => {
    const shared = { id: 'a1', name: 'Alpha Agent', fallback: false };
    const rows: Subscription[] = [
      { ...plan, id: 'other', name: 'Other plan', owner_id: 'human:p2', owner_name: 'Ben Sample' },
      { ...plan, id: 'legacy', name: 'Legacy plan', owner_id: '', owner_name: '' },
      { ...plan, id: 'company', kind: 'anthropic_key', name: 'Company key', owner_id: '', owner_name: '' },
      { ...plan, name: 'My plan', agents: [shared] },
      { ...plan, id: 'mine2', name: 'My other plan', agents: [shared] },
    ];
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: rows });
    serve((url) => url === '/api/v2/model-subscriptions' ? ok({ kinds: [], subscriptions: rows }) : undefined);
    show(<SettingsPage section="providers" />, '/settings/providers');
    const mine = await screen.findByRole('region', { name: 'Your pool' });
    const other = screen.getByRole('region', { name: "Ben Sample's pool" });
    const legacy = screen.getByRole('region', { name: 'No owner' });
    const company = screen.getByRole('region', { name: 'Company keys' });
    expect(within(mine).getByText('2 plans')).toBeTruthy();
    expect(within(mine).getByText('Used by 1 agent')).toBeTruthy();
    expect(within(mine).getAllByText('On 1 agent')).toHaveLength(2);
    const fold = within(other).getByRole('button', { name: "Ben Sample's pool" });
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(within(other).queryByText('Other plan')).toBeNull();
    fireEvent.click(fold);
    const otherPlan = within(other).getByText('Other plan');
    expect(otherPlan.closest('a')).toBeNull();
    fireEvent.click(fold);
    expect(within(other).queryByText('Other plan')).toBeNull();
    expect(within(legacy).getByText('Added by Ana Test')).toBeTruthy();
    expect(within(company).getByText('Company key')).toBeTruthy();
    expect(mine.compareDocumentPosition(other)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(other.compareDocumentPosition(legacy)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(legacy.compareDocumentPosition(company)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('identifies fallback agents in the expanded provider row and counts them', async () => {
    const catalog = { kinds: [], subscriptions: [{ ...plan, agents: [
      { id: 'a1', name: 'Alpha Agent', fallback: false },
      { id: 'b2', name: 'Beta Agent', fallback: true },
    ] }] };
    queryClient.setQueryData(subscriptionsQuery.queryKey, catalog);
    serve((url) => url === '/api/v2/model-subscriptions' ? ok(catalog) : undefined);
    show(<SettingsPage section="providers" open={plan.id} />, '/settings/providers?open=plan-1');
    const pool = within(await screen.findByRole('region', { name: 'Your pool' }));
    expect(pool.getByText('On 2 agents')).toBeTruthy();
    const [fallback] = pool.getAllByText('Fallback');
    expect(fallback?.parentElement?.textContent).toContain('Beta Agent');
  });

  it('selects one badge per row by severity before rotation', async () => {
    const usage: NonNullable<Subscription['usage']> = { used_pct: 100, window: 'week', cost_usd_month: null, resets_at: '2099-07-01T00:00:00Z' };
    const base = { ...plan, status: 'working' as const };
    const rows = [
      { ...base, name: 'Payment', problem: 'Payment past due', expired: true, usage },
      { ...base, id: 'expired', name: 'Expired', expired: true, usage },
      { ...base, id: 'limited', name: 'Limited', available: false, usage: { ...usage, quota_hold_until: usage.resets_at! } },
      { ...base, id: 'busy', name: 'Busy plan', available: false, usage: { ...usage, capacity_until: usage.resets_at! } },
      { ...base, id: 'near', name: 'Near', usage: { ...usage, used_pct: 96, near: true }, in_use_on: ['r1'] },
      { ...base, id: 'live', name: 'Live', order: 1, in_use_on: ['r1'], available: true, usage },
      { ...base, id: 'ready', name: 'Ready plan', order: 2, usage: { ...usage, used_pct: 100.4, resets_at: null } },
      { ...base, id: 'next', name: 'Spare', order: 3, next_in_rotation: true },
    ];
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: rows });
    serve((url) => url === '/api/v2/model-subscriptions' ? ok({ kinds: [], subscriptions: rows }) : undefined);
    show(<SettingsPage section="providers" />, '/settings/providers');
    await screen.findByRole('region', { name: 'Your pool' });
    for (const [row, words] of [['Payment', 'Problem'], ['Expired', 'Reconnect needed'], ['Limited', 'At limit until'], ['Busy plan', 'Busy until'], ['Near', 'Near limit'], ['Live', 'In use'], ['Spare', 'Next'], ['Ready plan', 'Ready']] as const) {
      const badges = screen.getByTitle(row).querySelectorAll('.pill');
      expect(badges).toHaveLength(1);
      expect(badges[0]?.textContent).toContain(words);
    }
    expect(screen.getByText('Problem').getAttribute('title')).toBe('Payment past due');
    expect(within(screen.getByTitle('Busy plan')).getByText(/Busy until/).classList.contains('pill-flag')).toBe(false);
    expect(within(screen.getByTitle('Live')).getByText(/100%/)).toBeTruthy();
    expect(within(screen.getByTitle('Expired')).queryByText(/100%/)).toBeNull();
  });

  it('shows the empty state inside Your pool and holds Add while the key is saving', async () => {
    const catalog = { subscriptions: [], kinds: [{ kind: 'anthropic_key' as const, runtime: 'claude' as const, label: 'Anthropic API key', secret_label: 'API key', connect: false, help: '', owned: false }] };
    let finish!: (reply: Reply) => void;
    const pending = new Promise<Reply>((resolve) => { finish = resolve; });
    queryClient.setQueryData(subscriptionsQuery.queryKey, catalog);
    serve((url, method) => url === '/api/v2/model-subscriptions' ? method === 'POST' ? pending : ok(catalog) : undefined);
    show(<SettingsPage section="providers" />, '/settings/providers');
    const pool = await screen.findByRole('region', { name: 'Your pool' });
    expect(within(pool).getByText('Add a Claude or ChatGPT plan, or an API key.')).toBeTruthy();
    expect(within(pool).queryByText('Used by 0 agents')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Anthropic key/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Add API key' }));
    fireEvent.change(dialog.getByLabelText('API key'), { target: { value: 'made-up-api-key' } });
    const add = dialog.getByRole<HTMLButtonElement>('button', { name: 'Add' });
    fireEvent.click(add);
    fireEvent.click(add);
    expect(add.disabled).toBe(true);
    expect(add.getAttribute('aria-busy')).toBe('true');
    expect(sent).toHaveLength(1);
    finish(ok({ id: 'key' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

});

describe('Settings › Agents and Computers', () => {
  it('counts a restored agent when its owner returns to the warm Computers list @s693', async () => {
    function Page() {
      const path = useLocation({ select: (where) => where.pathname });
      const { open } = useSearch({ strict: false }) as { open?: string };
      return path === '/settings/computers' ? <Computers open={open} /> : <Agents />;
    }

    let restored = false;
    const gamma = { id: 'c3', slug: 'gamma', display_name: 'Gamma Agent', machine: { runner_id: 'r1' } };
    serve((url, method) => {
      if (url === '/api/v2/bots/c3/restore' && method === 'POST') {
        restored = true;
        return ok({ status: 'active', restored: true });
      }
      if (url === '/api/v2/bots?include_archived=1') return ok([{ ...gamma, state: restored ? 'active' : 'archived', owner_id: 'p1' }]);
      if (url === '/api/employees') return ok(restored ? [...ROWS, gamma] : ROWS);
      if (url === '/api/v2/operations') return ok({ computers: [{ ...MACHINES[0], bots: restored ? ['a1', 'c3'] : ['a1'] }] });
      return undefined;
    });
    const router = show(<Page />, '/settings/computers');
    const initial = await screen.findByRole('link', { name: /^Shared One/ });
    expect(within(initial).getByText('1 agent')).toBeTruthy();
    void router.navigate({ to: '/settings/$section', params: { section: 'agents' } });
    fireEvent.click(await screen.findByRole('button', { name: /Retired/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(queryClient.getQueryData(agentsQuery.queryKey)).toContainEqual(gamma));
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/c3/restore', method: 'POST' });
    void router.navigate({ to: '/settings/$section', params: { section: 'computers' }, search: { open: 'computer:r1' } });
    const row = await screen.findByRole('link', { name: /^Shared One/ });
    expect(await within(row).findByText('2 agents')).toBeTruthy();
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    expect(within(panel).getByRole('link', { name: /Gamma Agent/ })).toBeTruthy();
  });

  it('refreshes the computer assignments once after a partially successful bulk move settles', async () => {
    const agents = [...ROWS, { id: 'c3', slug: 'gamma', machine: { runner_id: 'r1' } }];
    queryClient.setQueryData(agentsQuery.queryKey, agents);
    queryClient.setQueryData(teamComputers.queryKey, MACHINES);
    let reads = 0;
    let refuse!: (reply: Reply) => void;
    const last = new Promise<Reply>((resolve) => { refuse = resolve; });
    serve((url) => {
      if (url === '/api/v2/operations') {
        reads++;
        return ok({ computers: [{ ...MACHINES[0], bots: ['c3'] }, { ...MACHINES[1], bots: ['a1', 'b2'] }] });
      }
      if (url === '/api/employees') return ok(agents.map((row) => row.slug === 'gamma' ? row : { ...row, machine: { runner_id: 'r2' } }));
      if (url === '/api/v2/bots/gamma/transitions') return last;
      if (url.endsWith('/transitions')) return ok({ id: 'tr1' });
      return undefined;
    });
    show(<SettingsPage section="agents" />, '/settings/agents');
    await screen.findByRole('checkbox', { name: 'Check Alpha Agent' });
    let move!: ReturnType<typeof run>;
    try {
      await act(async () => {
        move = run('settings.moveAgents', { agents: ['alpha', 'beta', 'gamma'], computer: 'r2' }, { via: 'click' });
      });
      expect(sent.filter((request) => request.url.endsWith('/transitions'))).toHaveLength(3);
      expect(reads).toBe(0);
    } finally {
      refuse({ status: 403, body: { detail: 'Move refused' } });
      await act(async () => { await move; });
    }
    expect(await move).toMatchObject({ ok: true, out: { moved: 2, failed: ['gamma'] } });
    expect(reads).toBe(1);
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
  { id: 'r1', label: 'Shared One', cloud_machine: { shared: true }, online: true, platform: 'linux', version: '0.0.9', screen_available: true, screen: 'view', bots: ['a1'], gateway_fallback: false },
  { id: 'r2', label: 'Spare Two', online: false, gateway_fallback: false },
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
    if (url === '/api/v2/sleep') return ok({ sleep: { hosted: false } });
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

  it('Settings › Computers opens the same panel with its Gateway setting on the first read @s1075', async () => {
    let reads = 0;
    serve((url) => {
      if (url === '/api/v2/operations') {
        reads++;
        return ok({ computers: [{ ...MACHINES[0], gateway_fallback: true }] });
      }
      return undefined;
    });
    show(<SettingsPage section="computers" open="r1" />, '/settings/computers?open=r1');
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    expect(within(panel).getByText('Cloud shared')).toBeTruthy();
    const gateway = within(panel).getByRole('switch', { name: 'Fall back to Tico Gateway', checked: true });
    expect(reads).toBe(1);
    fireEvent.click(gateway);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/computers/r1/gateway-fallback', body: { on: false } }));
  });
});

describe('Settings › Computers', () => {
  it('lists each active computer once with its reported kind, state, version and agent count @s1075', async () => {
    const computers = [
      { id: 'r1', label: 'Shared One', cloud_machine: { shared: true }, online: true, version: '0.0.9', bots: ['a1', 'b2'] },
      { id: 'r2', label: 'Build Cloud', cloud_machine: { shared: false }, online: true, restarting: '2026-11-22T20:00:00Z', version: '0.0.8', bots: ['d4'] },
      { id: 'r3', label: 'Studio', online: false, version: '0.0.7', bots: [] },
      { id: 'r4', label: 'Revoked', online: false, revoked_at: '2026-11-22T20:00:00Z' },
    ];
    queryClient.setQueryData(['directory'], { actors: [], byId: {} });
    queryClient.setQueryData(agentsQuery.queryKey, [...ROWS, { id: 'd4', slug: 'delta' }]);
    serve((url) => {
      if (url === '/api/v2/operations') return ok({ computers });
      if (url === '/api/v2/sleep') return ok({ sleep: { hosted: true, hours: 0 } });
      return undefined;
    });
    show(<SettingsPage section="computers" />, '/settings/computers');
    const shared = await screen.findByRole('link', { name: /^Shared One/ });
    const cloud = screen.getByRole('link', { name: /^Build Cloud/ });
    const own = screen.getByRole('link', { name: /^Studio/ });
    for (const [row, facts] of [
      [shared, ['Cloud shared', 'Online', '0.0.9', '2 agents']],
      [cloud, ['Cloud', 'Restarting', '0.0.8', '1 agent']],
      [own, ['Your own', 'Offline', '0.0.7', '0 agents']],
    ] as const) {
      for (const fact of facts) expect(within(row).getByText(fact)).toBeTruthy();
    }
    expect(screen.getAllByRole('link').filter((link) => link.getAttribute('href')?.startsWith('/settings/computers?open='))).toHaveLength(3);
    expect(screen.queryByRole('region', { name: 'External agents' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Add computer' })).toBeTruthy();
    expect(await screen.findByRole('region', { name: 'When idle' })).toBeTruthy();
  });

  it('opens the selected machine’s existing panel from its row and closes it again', async () => {
    function Page() {
      const { open } = useSearch({ strict: false }) as { open?: string };
      return <SettingsPage section="computers" open={open} />;
    }
    byComputer();
    const router = show(<Page />, '/settings/computers');
    const row = await screen.findByRole('link', { name: /^Spare Two/ });
    expect(row.getAttribute('aria-expanded')).toBe('false');
    expect(row.getAttribute('data-target')).toBe('computer:r2');
    fireEvent.click(row);
    const panel = await screen.findByRole('complementary', { name: 'Spare Two' });
    expect(router.state.location.search).toEqual({ open: 'computer:r2' });
    expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(within(panel).getByText('Spare Two').getAttribute('data-target')).toBe('computer:r2');
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Spare Two' })).toBeNull());
    expect(row.getAttribute('aria-expanded')).toBe('false');
    expect(row.getAttribute('data-target')).toBe('computer:r2');
  });

  it('wraps the computer row in cards mode even when the list is wide', async () => {
    vi.stubGlobal('innerWidth', 640);
    document.documentElement.dataset.layout = 'cards';
    const style = document.createElement('style');
    style.textContent = fs.readFileSync(new URL('./settings.css', here), 'utf8');
    document.head.append(style);
    try {
      byComputer();
      show(<SettingsPage section="computers" />, '/settings/computers');
      const row = await screen.findByRole('link', { name: /^Shared One/ });
      expect(getComputedStyle(within(row).getByText('Online')).gridRow).toBe('1');
      expect(getComputedStyle(within(row).getByText('Shared One')).gridColumn).toBe('1 / 3');
    } finally {
      style.remove();
      delete document.documentElement.dataset.layout;
    }
  });

  it('refreshes both counts after a move while the computers list is closed', async () => {
    function Page() {
      const path = useLocation({ select: (where) => where.pathname });
      return path === '/settings/computers' ? <SettingsPage section="computers" /> : <span>Agent settings</span>;
    }
    let moved = false;
    let reads = 0;
    const agents = [{ ...ROWS[0]!, machine: { runner_id: 'r1', generation: 7 } }, ROWS[1]!];
    queryClient.setQueryData(teamComputers.queryKey, [
      { ...MACHINES[0]!, bots: ['a1', 'b2'] },
      { ...MACHINES[1]!, bots: [] },
    ]);
    queryClient.setQueryData(agentsQuery.queryKey, agents);
    serve((url, method) => {
      if (url === '/api/v2/bots/alpha/transitions' && method === 'POST') {
        moved = true;
        return ok({ id: 'tr1' });
      }
      if (url === '/api/v2/operations') {
        reads++;
        return ok({
          computers: [
            { ...MACHINES[0], bots: moved ? ['b2'] : ['a1', 'b2'] },
            { ...MACHINES[1], bots: moved ? ['a1'] : [] },
          ],
        });
      }
      if (url === '/api/employees') return ok(moved ? [{ ...agents[0], machine: { runner_id: 'r2', generation: 8 } }, agents[1]] : agents);
      return undefined;
    });
    const router = show(<Page />, '/team/alpha');
    await screen.findByText('Agent settings');
    expect(await run('agents.move', { slug: 'alpha', computer: 'r2' }, { via: 'click' })).toMatchObject({ ok: true });
    await router.navigate({ to: '/settings/$section', params: { section: 'computers' } });
    const from = await screen.findByRole('link', { name: /^Shared One/ });
    const to = screen.getByRole('link', { name: /^Spare Two/ });
    expect(await within(from).findByText('1 agent')).toBeTruthy();
    expect(await within(to).findByText('1 agent')).toBeTruthy();
    expect(reads).toBe(1);
  });

  it('counts only the assigned agents the operator can see in the roster', async () => {
    queryClient.setQueryData(['me'], { id: 'human:p1', role: 'person' });
    queryClient.setQueryData(teamComputers.queryKey, [
      { ...MACHINES[0]!, owner_id: 'p1', may_manage: true, bots: ['a1', 'hidden'] },
    ]);
    // A see-only agent has no machine in the roster.
    queryClient.setQueryData(agentsQuery.queryKey, [{ id: 'a1', slug: 'alpha', display_name: 'Alpha Agent' }]);
    serve((url) => url === '/api/v2/access' ? { status: 403, body: { detail: 'Admins only' } } : undefined);
    show(<SettingsPage section="computers" open="r1" />, '/settings/computers?open=r1');
    expect(within(await screen.findByRole('link', { name: /^Shared One/ })).getByText('1 agent')).toBeTruthy();
    const panel = screen.getByRole('complementary', { name: 'Shared One' });
    expect(within(panel).getAllByRole('link')).toHaveLength(1);
    expect(within(panel).getByRole('link', { name: /Alpha Agent/ })).toBeTruthy();
  });

  it('an operator can restart or remove their empty computer but cannot rename it or change Gateway fallback', async () => {
    queryClient.setQueryData(['me'], { id: 'human:p1', role: 'person' });
    queryClient.setQueryData(teamComputers.queryKey, [{ ...MACHINES[1]!, owner_id: 'p1', may_manage: true, bots: [] }]);
    serve((url) => url === '/api/v2/access' ? { status: 403, body: { detail: 'Admins only' } } : undefined);
    show(<SettingsPage section="computers" open="r2" />, '/settings/computers?open=r2');
    const panel = within(await screen.findByRole('complementary', { name: 'Spare Two' }));
    expect(panel.getByRole<HTMLButtonElement>('button', { name: 'Restart' }).disabled).toBe(false);
    expect(panel.getByRole<HTMLButtonElement>('button', { name: 'Remove' }).disabled).toBe(false);
    expect(panel.queryByRole('button', { name: 'Rename' })).toBeNull();
    expect(panel.getByRole('switch', { name: 'Fall back to Tico Gateway' }).getAttribute('aria-disabled')).toBe('true');
  });

  it('hides the last computer and its panel while removal is pending, and restores both if refused', async () => {
    let finish!: (reply: Reply) => void;
    const removal = new Promise<Reply>((resolve) => { finish = resolve; });
    queryClient.setQueryData(teamComputers.queryKey, [MACHINES[1]!]);
    serve((url, method) => url === '/api/v2/computers/r2/revoke' && method === 'POST' ? removal : undefined);
    show(<SettingsPage section="computers" open="r2" />, '/settings/computers?open=r2');
    const panel = await screen.findByRole('complementary', { name: 'Spare Two' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
    try {
      expect(await screen.findByText('No computers yet')).toBeTruthy();
      expect(screen.queryByRole('link', { name: /^Spare Two/ })).toBeNull();
      expect(screen.queryByRole('complementary', { name: 'Spare Two' })).toBeNull();
    } finally {
      finish({ status: 403, body: { detail: 'Removal refused' } });
    }
    expect(await screen.findByRole('link', { name: /^Spare Two/ })).toBeTruthy();
    expect(screen.getByRole('complementary', { name: 'Spare Two' })).toBeTruthy();
  });

  it.each([false, true])('clears the removed computer’s selection and preserves a new selection (%s)', async (selectAnother) => {
    function Page() {
      const { open } = useSearch({ strict: false }) as { open?: string };
      return <SettingsPage section="computers" open={open} />;
    }

    let finish!: (reply: Reply) => void;
    const removal = new Promise<Reply>((resolve) => { finish = resolve; });
    queryClient.setQueryData(teamComputers.queryKey, MACHINES);
    serve((url) => url === '/api/v2/computers/r1/revoke' ? removal : undefined);
    const router = show(<Page />, '/settings/computers?open=r1');
    const panel = await screen.findByRole('complementary', { name: 'Shared One' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
    try {
      await waitFor(() => expect(screen.queryByRole('link', { name: /^Shared One/ })).toBeNull());
      if (selectAnother) {
        fireEvent.click(screen.getByRole('link', { name: /^Spare Two/ }));
        await screen.findByRole('complementary', { name: 'Spare Two' });
      }
    } finally {
      await act(async () => { finish(ok({})); });
    }
    expect(router.state.location.search).toEqual(selectAnother ? { open: 'computer:r2' } : {});
    if (selectAnother) expect(screen.getByRole('complementary', { name: 'Spare Two' })).toBeTruthy();
  });

  it('a stopped hosted server starts for an owner, says why when held with a link to Tico support, and Tico cannot start it @s704', async () => {
    serve((url) =>
      url === '/api/v2/hosted-servers/h1/start' ? ok({ state: 'paused', message: 'Out of credit', credit_url: 'https://help.example.test/credit' }) : undefined,
    );
    queryClient.setQueryData(teamComputers.queryKey, []);
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
    show(<SettingsPage section="agents" />, '/settings/agents');
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
    expect(hitHref('#/settings/models/settings-subs')).toBe('/settings/providers');
    expect(hitHref('#/settings/models/settings-defaults')).toBe('/settings/agents');
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
