import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch, type AnyRouter } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { pipelinesQuery } from '@/features/tasks/exports';
import { ListeningPage } from './ListeningPage';
import { validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
import {
  deliveriesQuery,
  dueQuery,
  endpointsQuery,
  holdsQuery,
  hooksQuery,
  orgRulesQuery,
  reachQuery,
  routinesQuery,
  ruleGroupsQuery,
  stageRulesQuery,
  teamsQuery,
  watchesQuery,
  watchQuery,
  type StageRules,
} from './queries';
import { RoutinesPage } from './RoutinesPage';
import { RulesPage } from './RulesPage';
import type { Hook, OrgRule, Reach, Routine, StageRule } from './types';

/* Tier 2 parity checks for Rules, Routines and Listening: one small test per row whose main behavior no other test
   carries. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(pipelinesQuery.queryKey, [] as never);
  queryClient.setQueryData(holdsQuery.queryKey, []);
  queryClient.setQueryData(teamsQuery.queryKey, []);
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
function record(reply: (url: string, method: string) => Reply = nothing): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const ok = (body: unknown): Reply => ({ status: 200, body });
function nothing(): Reply {
  return { status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } };
}
const pick = async (name: string | RegExp, option: string | RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.querySelector('a') ?? el.closest('a'))?.getAttribute('href') ?? '');

/** The three pages at their own addresses, each reading its search from the router, so an action's move lands. */
function show(at: string): AnyRouter {
  const root = createRootRoute({ component: Outlet });
  const tail = <ActionsRoot dot={() => null} fold={() => {}} />;
  const Rules = () => <RulesPage search={useSearch({ strict: false })} />;
  const Routines = () => <RoutinesPage search={useSearch({ strict: false })} />;
  const Listening = () => <ListeningPage search={useSearch({ strict: false })} />;
  const pages = [
    createRoute({ getParentRoute: () => root, path: '/company/rules', validateSearch: validateRulesSearch, component: () => (<><Rules />{tail}</>) }),
    createRoute({ getParentRoute: () => root, path: '/company/routines', validateSearch: validateRoutinesSearch, component: () => (<><Routines />{tail}</>) }),
    createRoute({ getParentRoute: () => root, path: '/company/listening', validateSearch: validateListeningSearch, component: () => (<><Listening />{tail}</>) }),
  ];
  const router = createRouter({ routeTree: root.addChildren(pages), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router as unknown as AnyRouter;
}

const org = (over: Partial<OrgRule> = {}): OrgRule => ({ id: 'o1', kind: 'internal', text: 'Never merge on red', status: 'active', version: 1, can_edit: true, who: { everyone: true }, if_broken: 'block', ...over });
const reach: Reach = {
  defaults: { mode: 'nearby', book: 'nearby', chats_per_day: 40, tasks_per_day: 25 },
  settings: [{ party: 'bot:rel', mode: 'open' }],
  effective: { '*': {}, 'bot:rel': { mode: 'open', from: { mode: 'bot:rel' } } },
  may_edit: ['*', 'bot:rel'],
};
const team = { id: 'g1', name: 'Engineering', bots: ['rel'], people: [] };
const routine = (over: Partial<Routine> = {}) =>
  ({
    id: 'r1',
    name: 'Morning numbers',
    enabled: true,
    can_edit: true,
    can_pause: true,
    owner_id: 'bot:growth',
    trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC' },
    steps: [{ id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the numbers.' }],
    ...over,
  }) as unknown as Routine;
const company = (rules: OrgRule[]) => {
  queryClient.setQueryData(orgRulesQuery('active').queryKey, rules);
  queryClient.setQueryData(reachQuery.queryKey, reach);
  queryClient.setQueryData(teamsQuery.queryKey, [team]);
};

describe('Rules', () => {
  it('is one page of eight tabs as links, with Add rule and a ⋯ for a due date or a document @s508', async () => {
    record();
    company([]);
    show('/company/rules');
    expect(href(await screen.findByRole('link', { name: 'Pipelines and stages' }))).toBe('/company/rules?tab=stages');
    for (const t of ['Company', 'Built in', 'Outside', 'Due', 'Wakes', 'Routines on tasks', 'Suggested']) expect(screen.getByRole('link', { name: t })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Add due date' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Add document' })).toBeTruthy();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Add rule' })[0]!);
    expect(await screen.findByRole('complementary', { name: 'New rule' })).toBeTruthy();
  });

  it('Company: who on the left, their reach and limits, their rules and the exceptions; a reach change is sent @s517', async () => {
    const sent = record((url, method) => (method === 'PUT' && url.includes('/reach/settings/') ? ok({}) : nothing()));
    company([org()]);
    show('/company/rules');
    const who = await screen.findByRole('navigation', { name: 'Whose rules' });
    expect(within(who).getByTitle('Everyone')).toBeTruthy();
    expect(within(who).getByTitle('Engineering')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Rules' })).getByTitle('Never merge on red, Stop')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Exceptions' })).getByTitle(/Reach, Anyone$/)).toBeTruthy();
    fireEvent.click(within(screen.getByRole('group', { name: 'Reach' })).getByRole('button', { name: 'Listed' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PUT', body: { mode: 'closed' } });
    expect(sent[0]!.url).toContain('/api/v2/reach/settings/*');
  });

  it("one teammate: its own rules, then what it takes from its team and from Everyone @s551", async () => {
    record();
    company([org(), org({ id: 'o2', text: 'Ship only from main', who: { groups: [{ id: 'g1' }] } }), org({ id: 'o3', text: 'Release before noon', who: { bots: [{ id: 'rel' }] } })]);
    show('/company/rules?who=bot:rel');
    expect(within(await screen.findByRole('region', { name: 'Rules' })).getByTitle('Release before noon, Stop')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'From Engineering' })).getByTitle('Ship only from main, Stop')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'From Everyone' })).getByTitle('Never merge on red, Stop')).toBeTruthy();
  });

  it('on a phone, whose rules is picked from one select, kept in the address @s562', async () => {
    record();
    company([org()]);
    const router = show('/company/rules');
    await screen.findByRole('combobox', { name: 'Whose rules' });
    await pick('Whose rules', 'Engineering');
    await waitFor(() => expect((router.state.location.search as { who?: string }).who).toBe('group:g1'));
  });

  it('Outside: rules under who they come from; a row opens the rule with its From @s518', async () => {
    record();
    queryClient.setQueryData(orgRulesQuery('active').queryKey, [org({ id: 'x1', kind: 'external', text: 'File the yearly forms by Jan 31', source: { id: 's1', name: 'Tax office' } })]);
    queryClient.setQueryData(dueQuery.queryKey, []);
    show('/company/rules?tab=outside');
    const row = within(await screen.findByRole('region', { name: 'Tax office' })).getByTitle('File the yearly forms by Jan 31, Stop');
    expect(href(row)).toBe('/company/rules?tab=outside&panel=org:x1');
    fireEvent.click(within(row).getByRole('link'));
    const panel = await screen.findByRole('complementary', { name: 'Outside rule' });
    expect(within(panel).getByText('Tax office')).toBeTruthy();
  });

  it('Due: late first, then the next 30 days, then later; a late one says so @s520', async () => {
    record();
    const day = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);
    queryClient.setQueryData(dueQuery.queryKey, [
      { id: 'd3', due_on: day(200), status: 'upcoming', owner: 'human:me', rule: { id: 'o3', text: 'The supply contract ends' } },
      { id: 'd1', due_on: day(-3), status: 'overdue', owner: 'human:me', task_days: 14, rule: { id: 'o1', text: 'Renew the domain' } },
      { id: 'd2', due_on: day(10), status: 'due_soon', owner: 'human:me', rule: { id: 'o2', text: 'File the sales tax' } },
    ]);
    show('/company/rules?tab=due');
    const late = within(await screen.findByRole('region', { name: 'Late' })).getByTitle('Renew the domain, 14 days before');
    expect(within(late).getByText('Late')).toBeTruthy();
    expect(href(late)).toBe('/company/rules?tab=due&panel=due:d1');
    expect(within(screen.getByRole('region', { name: 'Next 30 days' })).getByTitle('File the sales tax')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Later' })).getByTitle('The supply contract ends')).toBeTruthy();
  });

  it('Wakes: what wakes an agent, each opening its routine on Routines @s521', async () => {
    record();
    queryClient.setQueryData(ruleGroupsQuery.queryKey, { wakes: [{ id: 'r2', name: 'Wake support', enabled: true }], automations: [], built_in: [] });
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ id: 'r2', name: 'Wake support', trigger: { type: 'event', name: 'message.created' } as Routine['trigger'] })]);
    show('/company/rules?tab=wakes');
    const row = await screen.findByTitle(/, Wake /);
    expect(href(row)).toBe('/company/routines?panel=routine:r2');
  });

  it('Routines on tasks: under the pipeline its filter names, off ones marked, opening the routine @s522', async () => {
    record();
    queryClient.setQueryData(pipelinesQuery.queryKey, [{ id: 'p1', name: 'Content', stages: [] }] as never);
    queryClient.setQueryData(ruleGroupsQuery.queryKey, { wakes: [], automations: [{ id: 'r3', name: 'Price check', enabled: false }], built_in: [] });
    const filter = { id: 'f1', type: 'filter', rules: [{ when: [{ field: 'task.pipeline', value: 'p1' }] }] };
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ id: 'r3', name: 'Price check', enabled: false, trigger: { type: 'event', name: 'task.step_changed' } as Routine['trigger'], steps: [filter] as never })]);
    show('/company/rules?tab=on-tasks');
    const row = within(await screen.findByRole('region', { name: 'Content' })).getByTitle(/Run Price check$/);
    expect(row.className).toContain('is-off');
    expect(href(row)).toBe('/company/routines?panel=routine:r3');
  });

  it('Built in: what always applies, with nothing to change @s523', async () => {
    record();
    queryClient.setQueryData(ruleGroupsQuery.queryKey, { wakes: [], automations: [], built_in: [{ name: 'A task is never deleted', what: 'It is archived' }] });
    show('/company/rules?tab=built-in');
    const row = within(await screen.findByRole('region', { name: 'Always on' })).getByTitle('A task is never deleted, It is archived');
    expect(within(row).queryByRole('link')).toBeNull();
    expect(within(row).queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('Suggested: Add takes a suggestion in, Set aside skips one @s524', async () => {
    const sent = record((url, method) => (method === 'POST' && /\/rules\/o\d\/(accept|skip)$/.test(url) ? ok({}) : nothing()));
    queryClient.setQueryData(orgRulesQuery('suggested').queryKey, [
      org({ id: 'o8', status: 'suggested', version: 3, text: 'Refunds over $80 wait for an OK', suggested_by: 'bot:support' }),
      org({ id: 'o9', status: 'suggested', version: 2, text: 'Post nothing after 10 PM', if_broken: 'warn' }),
    ]);
    queryClient.setQueryData<StageRules>(stageRulesQuery('suggested').queryKey, { items: [], built_in: [], may_add: true });
    show('/company/rules?tab=suggested');
    const first = await screen.findByTitle('Refunds over $80 wait for an OK');
    fireEvent.click(within(first).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'POST', body: { version: 3 } });
    expect(sent[0]!.url).toContain('/api/v2/rules/o8/accept');
    fireEvent.click(within(screen.getByTitle('Post nothing after 10 PM')).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Set aside' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]!.url).toContain('/api/v2/rules/o9/skip');
  });

  it('Add rule: the rule, who follows it and what happens if broken, then it opens @s546', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules') ? ok(org({ id: 'o5', text: 'Ask before a refund', if_broken: 'warn' })) : nothing()));
    company([]);
    const router = show('/company/rules');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add rule' }))[0]!);
    const panel = await screen.findByRole('complementary', { name: 'New rule' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Rule' }), { target: { value: 'Ask before a refund' } });
    fireEvent.click(within(within(panel).getByRole('group', { name: 'If broken' })).getByRole('button', { name: 'Warn' }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Add rule' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ kind: 'internal', text: 'Ask before a refund', if_broken: 'warn', reviewer: null, who: { everyone: true } });
    await waitFor(() => expect((router.state.location.search as { panel?: string }).panel).toBe('org:o5'));
  });

  it('one rule open: when, check and then, its pipeline, and Edit @s548', async () => {
    record();
    const stage = { id: 'sr1', name: '', pipeline_id: 'p1', pipeline_name: 'Engineering', stages: ['s2'], stage_names: ['Review'], scope: 'stage', event: 'enters', timer: null, cond: {}, jev: 'Is it ready?', then: 'block', status: 'active', revision: 1, can_edit: true } as StageRule;
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, { items: [stage], built_in: [], may_add: true });
    show('/company/rules?tab=stages&panel=stage:sr1');
    const panel = await screen.findByRole('complementary', { name: 'Enters Review' });
    expect(within(panel).getByText('the decision model decides')).toBeTruthy();
    expect(within(panel).getByText('Block')).toBeTruthy();
    expect(within(panel).getByText('Engineering')).toBeTruthy();
    expect(within(panel).getByText('Is it ready?')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Edit' }));
    expect(await screen.findByRole('combobox', { name: 'When' })).toBeTruthy();
  });
});

describe('Routines', () => {
  it('Webhooks: incoming ones with their calls today, unsigned marked, a row opening it @s514', async () => {
    record();
    const hook: Hook = { id: 'h1', name: 'Shop orders', url: 'https://in.example/hooks/h1', signed: false, received_24h: 3, automations: [] };
    queryClient.setQueryData(hooksQuery.queryKey, [hook]);
    queryClient.setQueryData(endpointsQuery.queryKey, []);
    show('/company/routines?tab=webhooks');
    const row = within(await screen.findByRole('region', { name: 'Incoming' })).getByTitle('Shop orders');
    expect(within(row).getByText('Unsigned')).toBeTruthy();
    expect(within(row).getByText('3 in 24 h')).toBeTruthy();
    expect(href(within(row).getByRole('link', { name: 'Shop orders' }))).toBe('/company/routines?tab=webhooks&panel=hook:h1');
    expect(screen.getByRole('button', { name: 'Add webhook' })).toBeTruthy();
  });

  it('one webhook: how it is signed, its last calls with why one was refused, and its ⋯ @s536', async () => {
    record();
    const hook: Hook = { id: 'h1', name: 'Shop orders', url: 'https://in.example/hooks/h1', signed: false, automations: [] };
    queryClient.setQueryData(hooksQuery.queryKey, [hook]);
    queryClient.setQueryData(endpointsQuery.queryKey, []);
    queryClient.setQueryData(deliveriesQuery('h1').queryKey, [{ delivery: 'd1', received_at: '2026-11-18T07:00:00Z', outcome: 'refused', reason: 'Bad signature', stored: true }] as never);
    show('/company/routines?tab=webhooks&panel=hook:h1');
    const panel = await screen.findByRole('complementary', { name: 'Shop orders' });
    expect(within(panel).getByText('No')).toBeTruthy();
    const call = within(panel).getByTitle('Bad signature');
    expect(within(call).getByText('Refused')).toBeTruthy();
    expect(within(call).getByRole('button', { name: 'Payload' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    for (const item of ['New secret', 'Signing', 'Remove']) expect(await screen.findByRole('menuitem', { name: item })).toBeTruthy();
  });

  it('trigger editor: a new schedule is sent as the routine’s trigger @s564', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r1') ? ok(routine()) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [routine()]);
    show('/company/routines?panel=routine:r1');
    const panel = await screen.findByRole('complementary', { name: 'Morning numbers' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    await pick('Every', 'Weekdays');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]!.body as { trigger: unknown }).trigger).toEqual({ type: 'schedule', cron: '0 7 * * 1-5', tz: 'UTC' });
  });
});

describe('Listening', () => {
  const watch = (over: Partial<Routine> = {}) =>
    routine({ id: 'w1', name: 'Mentions of us', owner_id: 'human:me', trigger: { type: 'listening', sources: [{ id: 'a', kind: 'search', site: 'reddit.com', target: 'raised bed' }], limits: [{ count: 10, per: 'day' }] } as Routine['trigger'], ...over });

  it('adds a watch, off and with no sources, and opens its sources; a paused one is marked @s516', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/automations') ? ok(watch({ id: 'w9', name: 'Seed talk', enabled: false })) : nothing()));
    queryClient.setQueryData(watchesQuery.queryKey, [watch({ enabled: false })]);
    const router = show('/company/listening');
    expect((await screen.findByTitle('Mentions of us')).className).toContain('is-off');
    fireEvent.click(screen.getByRole('button', { name: 'Add watch' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add watch' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Seed talk' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ name: 'Seed talk', enabled: false, trigger: { type: 'listening', sources: [] } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ watch: 'w9', tab: 'sources' }));
  });

  it('one watch: its sources, and Add source puts one in its trigger @s540', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/w1') ? ok(watch()) : nothing()));
    queryClient.setQueryData(watchQuery('w1').queryKey, watch());
    show('/company/listening?watch=w1&tab=sources');
    expect(within(await screen.findByRole('region', { name: 'Sources' })).getByTitle('raised bed')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Matches' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add source' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Words' }), { target: { value: 'seed kit' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Site' }), { target: { value: 'reddit.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const sources = (sent[0]!.body as { trigger: { sources: { kind: string; target: string; site?: string }[] } }).trigger.sources;
    expect(sources).toHaveLength(2);
    expect(sources[1]).toMatchObject({ kind: 'search', target: 'seed kit', site: 'reddit.com' });
  });
});
