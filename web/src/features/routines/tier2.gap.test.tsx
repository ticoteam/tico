import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch, type AnyRouter } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { pipelinesQuery } from '@/features/tasks/exports';
import { ListeningPage } from './ListeningPage';
import { validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
import {
  deliveriesQuery,
  dueQuery,
  holdsQuery,
  hooksQuery,
  orgRulesQuery,
  reachQuery,
  routinesQuery,
  ruleSourcesQuery,
  stageRulesQuery,
  teamsQuery,
  versionsQuery,
  watchesQuery,
  watchQuery,
  type StageRules,
} from './queries';
import { RoutinesPage } from './RoutinesPage';
import { RulesPage } from './RulesPage';
import type { Hook, OrgRule, Reach, Routine, StageRule } from './types';

/* The gap wave on Rules, Routines and Listening: each test carries the row it closes. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const actors = [
  { id: 'bot:rel', kind: 'agent', name: 'Release' },
  { id: 'human:ann', kind: 'person', name: 'Ann Lee' },
];
beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  queryClient.setQueryData(pipelinesQuery.queryKey, [] as never);
  queryClient.setQueryData(holdsQuery.queryKey, []);
  queryClient.setQueryData(teamsQuery.queryKey, [{ id: 'g1', name: 'Engineering', bots: ['rel'], people: [] }]);
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
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
const pick = async (name: string | RegExp, option: string | RegExp, within_: HTMLElement = document.body) => {
  fireEvent.click(within(within_).getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};
const menu = async (scope: HTMLElement, item: string) => {
  fireEvent.click(within(scope).getByRole('button', { name: 'More' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: item }));
};

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
const reach: Reach = { defaults: { mode: 'nearby', book: 'nearby', chats_per_day: 40, tasks_per_day: 25 }, settings: [], effective: { '*': {} }, may_edit: ['*'] };
const routine = (over: Partial<Routine> = {}) =>
  ({
    id: 'r1',
    name: 'Morning numbers',
    enabled: true,
    can_edit: true,
    can_pause: true,
    owner_id: 'bot:rel',
    version: 2,
    trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC' },
    steps: [{ id: 's1', type: 'bot_turn', bot: 'bot:rel', prompt: 'Post the numbers.' }],
    ...over,
  }) as unknown as Routine;

describe('Rules: adding and editing a rule', () => {
  it('an outside rule names its From, a new one made first, so the server takes it @s518', async () => {
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/rule-sources')
        ? ok({ source: { id: 'src9', name: 'Tax office', kind: 'government' } })
        : method === 'POST' && url.endsWith('/api/v2/rules')
          ? ok(org({ id: 'x5', kind: 'external', text: 'File by Jan 31' }))
          : nothing(),
    );
    queryClient.setQueryData(orgRulesQuery('active').queryKey, []);
    queryClient.setQueryData(dueQuery.queryKey, []);
    queryClient.setQueryData(ruleSourcesQuery.queryKey, [{ id: 'src1', name: 'Made Up Supply', kind: 'vendor' }]);
    show('/company/rules?tab=outside');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add rule' }))[0]!);
    const panel = await screen.findByRole('complementary', { name: 'New outside rule' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Rule' }), { target: { value: 'File by Jan 31' } });
    expect((within(panel).getByRole('button', { name: 'Add rule' }) as HTMLButtonElement).disabled).toBe(true);
    await pick('From', 'New…', panel);
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Tax office' } });
    fireEvent.click(within(within(panel).getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'Government' }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Add rule' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[0]).toMatchObject({ url: expect.stringContaining('/api/v2/rule-sources'), body: { name: 'Tax office', kind: 'government' } });
    expect(sent[1]!.body).toMatchObject({ kind: 'external', text: 'File by Jan 31', source_id: 'src9', who: { everyone: true } });
  });

  it('Who takes several teammates, Ask first defaults to the manager @s546', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules') ? ok(org({ id: 'o5' })) : nothing()));
    queryClient.setQueryData(orgRulesQuery('active').queryKey, []);
    queryClient.setQueryData(reachQuery.queryKey, reach);
    show('/company/rules?panel=org:new');
    const panel = await screen.findByRole('complementary', { name: 'New rule' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Rule' }), { target: { value: 'Ask before a refund' } });
    await pick('Who', 'Engineering', panel);
    await pick('Who', 'Ann Lee', panel);
    fireEvent.click(within(within(panel).getByRole('group', { name: 'If broken' })).getByRole('button', { name: 'Ask first' }));
    expect(within(panel).getByRole('combobox', { name: 'Ask' }).textContent).toContain('Manager');
    fireEvent.click(within(panel).getByRole('button', { name: 'Add rule' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ kind: 'internal', text: 'Ask before a refund', if_broken: 'escalate', reviewer: null, who: { groups: ['g1'], people: ['ann'] } });
  });

  it('a company rule shows its due dates and quote; an edit keeps every Who @s548', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules/o3') ? ok(org({ id: 'o3' })) : nothing()));
    const rule = org({
      id: 'o3',
      text: 'Ship only from main',
      who: { groups: [{ id: 'g1' }], people: [{ id: 'ann' }] },
      cite: { file_id: 'f1', section: 'Section 2', quote: 'All releases leave from main.' },
      due: [{ id: 'd1', due_on: '2026-12-01', status: 'upcoming', repeats: 'yearly', task_days: 7 }],
      version: 4,
    });
    queryClient.setQueryData(orgRulesQuery('active').queryKey, [rule]);
    queryClient.setQueryData(reachQuery.queryKey, reach);
    const router = show('/company/rules?panel=org:o3');
    const panel = await screen.findByRole('complementary', { name: 'Rule' });
    expect(within(panel).getByText('Engineering, Ann Lee')).toBeTruthy();
    expect(within(panel).getByText(/All releases leave from main/)).toBeTruthy();
    expect(within(panel).getByTitle('Every year, task 7 days before')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Add date' }));
    await waitFor(() => expect((router.state.location.search as { panel?: string }).panel).toBe('due:new:o3'));
    await router.navigate({ to: '/company/rules', search: { panel: 'org:o3' } } as never);
    fireEvent.click(within(await screen.findByRole('complementary', { name: 'Rule' })).getByRole('button', { name: 'Edit' }));
    const editor = await screen.findByRole('complementary', { name: 'Rule' });
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Rule' }), { target: { value: 'Ship only from main, always' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ version: 4, text: 'Ship only from main, always', who: { groups: ['g1'], people: ['ann'] } });
    expect(sent[0]!.body).not.toHaveProperty('with');
    expect(sent[0]!.body).not.toHaveProperty('description');
  });
});

describe('Rules: suggestions, search and filters', () => {
  it('a suggestion with no Who asks who follows it @s524', async () => {
    const sent = record((url, method) => (method === 'POST' && /\/rules\/o\d\/accept$/.test(url) ? ok({}) : nothing()));
    queryClient.setQueryData(orgRulesQuery('suggested').queryKey, [
      org({ id: 'o7', status: 'suggested', version: 2, text: 'Refunds wait for an OK', who: {} }),
      org({ id: 'o8', status: 'suggested', version: 3, text: 'Post nothing after 10 PM' }),
      org({ id: 'o9', status: 'suggested', version: 1, text: 'Reply within a day' }),
    ]);
    queryClient.setQueryData<StageRules>(stageRulesQuery('suggested').queryKey, { items: [], built_in: [], may_add: true });
    show('/company/rules?tab=suggested');
    fireEvent.click(within(await screen.findByTitle('Refunds wait for an OK')).getByRole('button', { name: 'Add' }));
    const dialog = await screen.findByRole('dialog', { name: 'Who follows it?' });
    expect((within(dialog).getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
    await pick('Who', 'Engineering', dialog);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ url: expect.stringContaining('/rules/o7/accept'), body: { version: 2, who: { groups: ['g1'] } } });
  });

  it('a suggested change to a stage rule says what it changes @s524', async () => {
    record();
    const base = { pipeline_id: 'p1', pipeline_name: 'Engineering', stages: ['s2'], stage_names: ['Review'], scope: 'stage', event: 'enters', timer: null, cond: {}, jev: '', then: 'block', revision: 1, can_edit: true };
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, { items: [{ ...base, id: 'sr1', name: 'Review gate', status: 'active' } as StageRule], built_in: [], may_add: true });
    queryClient.setQueryData<StageRules>(stageRulesQuery('suggested').queryKey, {
      items: [{ ...base, id: 'sr9', name: 'Review gate v2', status: 'suggested', suggests: 'sr1', reason: 'Tests first' } as StageRule],
      built_in: [],
      may_add: true,
    });
    queryClient.setQueryData(orgRulesQuery('suggested').queryKey, []);
    show('/company/rules?tab=suggested');
    const row = await screen.findByTitle(/^Enters Review/);
    expect(within(row).getByRole('button', { name: 'Accept change' })).toBeTruthy();
    fireEvent.click(within(row).getByRole('link'));
    const changes = await screen.findByRole('region', { name: 'Suggested change' });
    expect(within(changes).getByText('Review gate')).toBeTruthy();
    expect(within(changes).getByText('Review gate v2')).toBeTruthy();
    expect(within(changes).getByText('Tests first')).toBeTruthy();
  });

  it('every tab can be searched, in the address @s562', async () => {
    record();
    queryClient.setQueryData(orgRulesQuery('active').queryKey, [
      org({ id: 'x1', kind: 'external', text: 'File the yearly forms', source: { id: 's1', name: 'Tax office' } }),
      org({ id: 'x2', kind: 'external', text: 'Pay invoices in 30 days', source: { id: 's2', name: 'Made Up Supply' } }),
    ]);
    queryClient.setQueryData(dueQuery.queryKey, []);
    queryClient.setQueryData(ruleSourcesQuery.queryKey, [
      { id: 's1', name: 'Tax office' },
      { id: 's2', name: 'Made Up Supply' },
    ]);
    show('/company/rules?tab=outside&q=invoices');
    expect(await screen.findByTitle('Pay invoices in 30 days, Stop')).toBeTruthy();
    expect(screen.queryByTitle('File the yearly forms, Stop')).toBeNull();
    expect(validateRulesSearch({ tab: 'outside', q: 'invoices' })).toEqual({ tab: 'outside', q: 'invoices' });
  });
});

describe('Routines', () => {
  it('an edit keeps its trigger’s filters and zone @s564', async () => {
    const filters = [{ field: 'task.pipeline', op: 'is', value: 'p1' }];
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r1') ? ok(routine()) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC', filters } as Routine['trigger'] })]);
    show('/company/routines?panel=routine:r1');
    await menu(await screen.findByRole('complementary', { name: 'Morning numbers' }), 'Edit');
    await pick('Every', 'Weekdays');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]!.body as { trigger: unknown }).trigger).toEqual({ type: 'schedule', cron: '0 7 * * 1-5', tz: 'UTC', filters });
  });

  it('a due routine’s test run uses its next due day and names its due date @s566', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r2/run') ? ok({ run: null }) : nothing()));
    const due = routine({ id: 'r2', name: 'Yearly filing', trigger: { type: 'due', first_due: '2026-01-31', repeats: 'yearly', open_days: 14, rule_due_id: 'rd1' } as Routine['trigger'], next_due_at: '2027-01-31' });
    queryClient.setQueryData(routinesQuery.queryKey, [due]);
    show('/company/routines?panel=routine:r2');
    await menu(await screen.findByRole('complementary', { name: 'Yearly filing' }), 'Test run');
    const dialog = await screen.findByRole('dialog', { name: 'Test run' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ input: { due: { due_on: '2027-01-31', first_due: '2026-01-31', repeats: 'yearly', open_days: 14, rule_due_id: 'rd1' } } });
  });

  it('a proposed version shows what it changes with View diff @s567', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [routine()]);
    queryClient.setQueryData(versionsQuery('r1').queryKey, [
      { n: 3, actor: 'bot:rel', created_at: '2026-11-18T07:00:00Z', status: 'pending', flow: { steps: [{ id: 's1', type: 'bot_turn', bot: 'bot:rel', prompt: 'Post the numbers and the trend.' }] } },
      { n: 2, actor: 'human:me', created_at: '2026-11-17T07:00:00Z', status: 'active' },
    ]);
    show('/company/routines?panel=routine:r1');
    await menu(await screen.findByRole('complementary', { name: 'Morning numbers' }), 'Versions');
    fireEvent.click(await screen.findByRole('button', { name: 'View diff' }));
    const diff = await screen.findByLabelText('Diff');
    expect(within(diff).getByText('- Agent turn bot:rel: Post the numbers.')).toBeTruthy();
    expect(within(diff).getByText('+ Agent turn bot:rel: Post the numbers and the trend.')).toBeTruthy();
  });

});

describe('Webhooks', () => {
  const hook: Hook = { id: 'h1', name: 'Shop orders', url: 'https://in.example/hooks/h1', signed: false, received_24h: 5, refused_24h: 2, hourly: 60, keep_days: 7, unheard: 'log', type_from: '', automations: [] };

  it('one webhook: renamed, New secret asked first @s536', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/triggers/h1') ? ok({ trigger: { ...hook, name: 'Store orders' } }) : nothing()));
    queryClient.setQueryData(hooksQuery.queryKey, [hook]);
    queryClient.setQueryData(deliveriesQuery('h1').queryKey, [{ delivery: 'd1', received_at: '2026-11-18T07:00:00Z', outcome: 'accepted', tasks: 1, stored: true }] as never);
    show('/company/routines?tab=webhooks&panel=hook:h1');
    const panel = await screen.findByRole('complementary', { name: 'Shop orders' });
    expect(within(panel).getByText('5 calls, 2 refused')).toBeTruthy();

    await menu(panel, 'Edit');
    const dialog = await screen.findByRole('dialog', { name: 'Edit webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Store orders' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ name: 'Store orders' });

    await menu(panel, 'New secret');
    expect(await screen.findByRole('alertdialog', { name: 'Make a new address?' })).toBeTruthy();
    expect(sent).toHaveLength(1);
  });

  it('Add webhook asks how it is signed, as the old app did @s577', async () => {
    const sent = record((url, method) =>
      method !== 'POST'
        ? url.endsWith('/deliveries')
          ? ok({ deliveries: [] })
          : nothing()
        : url.endsWith('/api/v2/triggers')
          ? ok({ trigger: { id: 'h7', url: 'https://in.example/hooks/h7/made_up_secret' } })
          : url.endsWith('/triggers/h7/signing')
            ? ok({ trigger: { id: 'h7', signed: true, preset: 'github' } })
            : url.endsWith('/triggers/h7')
              ? ok({ trigger: { id: 'h7', name: 'Repo events' } })
              : nothing(),
    );
    queryClient.setQueryData(hooksQuery.queryKey, []);
    show('/company/routines?tab=webhooks');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add webhook' }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'Add webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Repo events' } });
    await pick('Signing', 'GitHub', dialog);
    fireEvent.change(within(dialog).getByLabelText('Signing secret'), { target: { value: 'typed-made-up' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[0]!.body).toEqual({ name: 'Repo events' });
    expect(sent[1]!.body).toEqual({ preset: 'github', secret: 'typed-made-up' });
  });
});

describe('Listening', () => {
  const watch = (over: Partial<Routine> = {}) =>
    routine({
      id: 'w1',
      name: 'Mentions of us',
      owner_id: 'human:me',
      trigger: { type: 'listening', sources: [{ id: 'a', kind: 'search', site: 'reddit.com', target: 'raised bed' }], rules: [], limits: [{ count: 10, per: 'day' }], at: '08:00', tz: 'UTC' } as Routine['trigger'],
      ...over,
    });

  it('the watches are searched in the address, and one not reading says so @s516', async () => {
    record();
    queryClient.setQueryData(watchesQuery.queryKey, [watch({ listening: { dot: 'hollow' } }), watch({ id: 'w2', name: 'Seed talk', trigger: { type: 'listening', sources: [], limits: [] } as Routine['trigger'] })]);
    show('/company/listening?q=raised');
    const row = await screen.findByTitle('Mentions of us');
    expect(within(row).getByText('Not reading')).toBeTruthy();
    expect(screen.queryByTitle('Seed talk')).toBeNull();
    expect(validateListeningSearch({ q: 'raised' })).toEqual({ q: 'raised' });
  });

  it('one watch: renamed, its rules changed, and a GitHub source added; a push source is not Tico’s @s540', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/w1') ? ok(watch()) : nothing()));
    queryClient.setQueryData(watchQuery('w1').queryKey, watch());
    show('/company/listening?watch=w1&tab=sources');
    await screen.findByRole('region', { name: 'Sources' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit watch' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Brand mentions' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add rule' }));
    const rule = within(dialog).getByRole('group', { name: 'Rule 1' });
    fireEvent.change(within(rule).getByRole('textbox', { name: 'Tag' }), { target: { value: 'asks' } });
    fireEvent.change(within(rule).getByRole('textbox', { name: 'Sentence' }), { target: { value: 'Someone asks for a kit' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({
      name: 'Brand mentions',
      trigger: { type: 'listening', rules: [{ do: 'keep', tag: 'asks', text: 'Someone asks for a kit' }], limits: [{ count: 10, per: 'day' }] },
    });

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit watch' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    const add = await screen.findByRole('dialog', { name: 'Add source' });
    fireEvent.click(within(within(add).getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'GitHub' }));
    fireEvent.change(within(add).getByRole('textbox', { name: 'Repository' }), { target: { value: 'https://github.com/acme/widgets' } });
    await pick('Follow', 'Releases', add);
    fireEvent.click(within(add).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    const sources = (sent[1]!.body as { trigger: { sources: unknown[] } }).trigger.sources;
    expect(sources[1]).toMatchObject({ kind: 'github', site: 'github', target: 'acme/widgets', follow: 'releases' });
    expect((await run('listening.addSource', { watch: 'w1', kind: 'push', site: 'Support form' }, { via: 'tico' })).ok).toBe(false);
  });
});

describe('Add document', () => {
  it('finds an older file in Files as words are typed @s547', async () => {
    record((url) =>
      url.includes('/files/search')
        ? ok({ results: [{ id: 'f9', title: 'Old vendor contract' }] })
        : url.includes('/files?')
          ? ok({ files: [{ id: 'f1', title: 'Vendor terms' }] })
          : url.includes('/rule-sources')
            ? ok({ sources: [] })
            : nothing(),
    );
    queryClient.setQueryData(dueQuery.queryKey, []);
    show('/company/rules?tab=due');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add document' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add document' });
    const box = within(dialog).getByRole('combobox', { name: 'File' });
    box.focus();
    fireEvent.input(box, { target: { value: 'contract' }, inputType: 'insertText' });
    expect(await screen.findByRole('option', { name: /Old vendor contract/ }, { timeout: 2000 })).toBeTruthy();
  });
});
