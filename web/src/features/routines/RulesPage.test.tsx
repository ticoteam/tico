import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { pipelinesQuery } from '@/features/tasks/exports';
import { validateRulesSearch } from './load';
import {
  constitutionQuery,
  dueQuery,
  holdsQuery,
  orgRulesQuery,
  reachQuery,
  routinesQuery,
  ruleGroupsQuery,
  ruleHistoryQuery,
  ruleSourcesQuery,
  stageRulesQuery,
  teamsQuery,
  type StageRules,
} from './queries';
import { RulesPage } from './RulesPage';
import { StageRules as StageRulesList } from './StageRules';
import type { OrgRule, Reach, Routine, StageRule } from './types';

/* The Rules page, tab by tab, and a stage's rules. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const people = (actors: { id: string; kind: string; name: string }[]) =>
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  people([
    { id: 'bot:rel', kind: 'agent', name: 'Release' },
    { id: 'human:ann', kind: 'person', name: 'Ann Lee' },
  ]);
  queryClient.setQueryData(pipelinesQuery.queryKey, [] as never);
  queryClient.setQueryData(holdsQuery.queryKey, []);
  queryClient.setQueryData(teamsQuery.queryKey, [{ id: 'g1', name: 'Engineering', bots: ['rel'], people: [] }]);
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
/** Answers every call with `reply`, keeping each write the page sends, and each read's address in `reads`. */
function record(reply: (url: string, method: string) => Reply | Promise<Reply> = nothing) {
  const sent = Object.assign([] as Sent[], { reads: [] as string[] });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') sent.reads.push(String(url));
    else sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const pick = async (name: string | RegExp, option: string | RegExp, scope: HTMLElement = document.body) => {
  fireEvent.click(within(scope).getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.querySelector('a') ?? el.closest('a'))?.getAttribute('href') ?? '');

/** The Rules page at its own address reading its search from the router, or `node` at `at`, with the action layer bound. */
function show(at: string, node?: ReactNode) {
  const Live = () => <RulesPage search={useSearch({ strict: false })} />;
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: node ? (at.split('?')[0] ?? '/') : '/company/rules',
    validateSearch: validateRulesSearch,
    component: () => (
      <>
        {node ?? <Live />}
        <ActionsRoot dot={() => null} fold={() => {}} />
      </>
    ),
  });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const org = (over: Partial<OrgRule> = {}): OrgRule => ({ id: 'o1', kind: 'internal', text: 'Never merge on red', status: 'active', version: 1, can_edit: true, who: { everyone: true }, if_broken: 'block', ...over });
const reach: Reach = {
  defaults: { mode: 'nearby', book: 'nearby', chats_per_day: 40, tasks_per_day: 25 },
  settings: [{ party: 'bot:rel', mode: 'open' }],
  effective: { '*': {}, 'bot:rel': { mode: 'open', from: { mode: 'bot:rel' } } },
  may_edit: ['*', 'bot:rel'],
};
const company = (rules: OrgRule[]) => {
  queryClient.setQueryData(orgRulesQuery('active').queryKey, rules);
  queryClient.setQueryData(reachQuery.queryKey, reach);
};
const routine = (over: Partial<Routine>) => ({ enabled: true, owner_id: 'bot:growth', steps: [], ...over }) as unknown as Routine;
const stage = { id: 'sr1', name: '', pipeline_id: 'p1', pipeline_name: 'Engineering', stages: ['s2'], stage_names: ['Review'], scope: 'stage', event: 'enters', timer: null, cond: {}, jev: 'Is it ready?', then: 'block', status: 'active', revision: 1, can_edit: true } as StageRule;
const stageRules = (items: StageRule[], status: 'active' | 'suggested' = 'active') =>
  queryClient.setQueryData<StageRules>(stageRulesQuery(status).queryKey, { items, built_in: [], may_add: true });

describe('Rules: the page and Company', () => {
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

  it('one teammate: its own rules, then what it takes from its team and from Everyone @s551', async () => {
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

  it('Add rule: the rule, several teammates as Who, Ask first to the manager by default, then it opens @s546', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules') ? ok(org({ id: 'o5', text: 'Ask before a refund', if_broken: 'escalate' })) : nothing()));
    company([]);
    const router = show('/company/rules');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add rule' }))[0]!);
    const panel = await screen.findByRole('complementary', { name: 'New rule' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Rule' }), { target: { value: 'Ask before a refund' } });
    await pick('Who', 'Engineering', panel);
    await pick('Who', 'Ann Lee', panel);
    fireEvent.click(within(within(panel).getByRole('group', { name: 'If broken' })).getByRole('button', { name: 'Ask first' }));
    expect(within(panel).getByRole('combobox', { name: 'Ask' }).textContent).toContain('Manager');
    fireEvent.click(within(panel).getByRole('button', { name: 'Add rule' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ kind: 'internal', text: 'Ask before a refund', if_broken: 'escalate', reviewer: null, who: { groups: ['g1'], people: ['ann'] } });
    await waitFor(() => expect((router.state.location.search as { panel?: string }).panel).toBe('org:o5'));
  });

  it('a company rule shows its due dates and quote; an edit keeps every Who @s548', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules/o3') ? ok(org({ id: 'o3' })) : nothing()));
    company([
      org({
        id: 'o3',
        text: 'Ship only from main',
        who: { groups: [{ id: 'g1' }], people: [{ id: 'ann' }] },
        cite: { file_id: 'f1', section: 'Section 2', quote: 'All releases leave from main.' },
        due: [{ id: 'd1', due_on: '2026-12-01', status: 'upcoming', repeats: 'yearly', task_days: 7 }],
        version: 4,
      }),
    ]);
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

describe('Rules: checked exactly, history, the constitution and the clerk', () => {
  it('a rule with a typed check says Checked exactly, then its holds and its versions, each continuing on More', async () => {
    const sent = record((url) =>
      url.includes('/history?versions_cursor=')
        ? ok({ versions: [{ id: 'a1', created_at: '2026-11-19T10:00:00Z', actor: 'bot:rel', before: null, after: { status: 'suggested', version: 1 } }], versions_next: null, holds: [], holds_next: null })
        : nothing(),
    );
    company([org({ id: 'o7', text: 'Refunds over $100 go to Ben first', exact_check: { kind: 'max_usd', action: 'refund', field: 'amount_usd', max_usd: 100 } })]);
    queryClient.setQueryData(ruleHistoryQuery('o7').queryKey, {
      versions: [
        { id: 'a2', created_at: '2026-11-20T10:00:00Z', actor: 'human:ann', before: { text: 'Refunds go to Ben', status: 'active', version: 1 }, after: { text: 'Refunds over $100 go to Ben first', status: 'active', version: 2 } },
      ],
      versions_next: '2026-11-20T10:00:00Z|a2',
      holds: [{ id: 'h1', state: 'held', created_at: '2026-11-20T11:00:00Z', author: 'bot:rel', rules: [{ id: 'o7' }], to: ['Ann Lee'] }],
      holds_next: null,
    });
    show('/company/rules?panel=org:o7');
    const panel = await screen.findByRole('complementary', { name: 'Rule' });
    expect(within(panel).getByRole('heading', { name: 'Checked exactly' })).toBeTruthy();
    expect(within(panel).getByText('refund: amount_usd at most $100')).toBeTruthy();
    const days = within(panel).getByRole('region', { name: 'Last 30 days' });
    expect(within(days).getByTitle('Ann Lee')).toBeTruthy();
    expect(within(days).queryByRole('button', { name: 'More' })).toBeNull();
    const versions = within(panel).getByRole('region', { name: 'Versions' });
    expect(within(versions).getAllByText(/Changed words|Suggested/).map((e) => e.textContent)).toEqual(['Changed words']);
    fireEvent.click(within(versions).getByRole('button', { name: 'More' }));
    await waitFor(() => expect(within(versions).getAllByText(/Changed words|Suggested/).map((e) => e.textContent)).toEqual(['Changed words', 'Suggested']));
    expect(sent.reads.some((u) => u.endsWith('/api/v2/rules/o7/history?versions_cursor=2026-11-20T10%3A00%3A00Z%7Ca2'))).toBe(true);
    expect(within(versions).queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('the constitution heads Company and opens its doc; Make this a rule sends the words and links the task', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/rules/make') ? ok({ task_id: 't9', owner: 'bot:rules-owner' }) : nothing()));
    company([]);
    queryClient.setQueryData(constitutionQuery.queryKey, () => ({ id: 'doc-1', title: 'Constitution', locked: true }));
    show('/company/rules');
    expect(href(await screen.findByTitle('Constitution'))).toBe('/library/doc-1');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Make this a rule' }));
    const dialog = await screen.findByRole('dialog', { name: 'Make this a rule' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Rule' }), { target: { value: '  Refunds over $100 go to Ben first ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ text: 'Refunds over $100 go to Ben first' });
    const done = await screen.findByRole('dialog', { name: 'Make this a rule' });
    expect(href(await within(done).findByRole('link', { name: 'Open task' }))).toBe('/task/t9');
  });
});

describe('Rules: Outside, Due and documents', () => {
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

  it('Due: late first, then the next 30 days, then later; a row names who gets its task, and the tab reads only its own data @s520', async () => {
    people([{ id: 'bot:fin', kind: 'agent', name: 'Finance Owner' }]);
    const day = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);
    const due = [
      { id: 'd3', due_on: day(200), status: 'upcoming', owner: 'human:me', rule: { id: 'o3', text: 'The supply contract ends' } },
      { id: 'd1', due_on: day(-3), status: 'overdue', owner: 'bot:fin', task_days: 14, rule: { id: 'o1', text: 'Renew the domain' } },
      { id: 'd2', due_on: day(10), status: 'due_soon', owner: 'human:me', rule: { id: 'o2', text: 'File the sales tax' } },
    ];
    const sent = record((u) => (u.includes('/api/v2/rules/due') ? ok({ due }) : nothing()));
    show('/company/rules?tab=due');
    const late = within(await screen.findByRole('region', { name: 'Late' })).getByTitle('Renew the domain, 14 days before, Finance Owner gets a task');
    expect(within(late).getByText('Late')).toBeTruthy();
    expect(href(late)).toBe('/company/rules?tab=due&panel=due:d1');
    expect(within(screen.getByRole('region', { name: 'Next 30 days' })).getByTitle('File the sales tax')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Later' })).getByTitle('The supply contract ends')).toBeTruthy();
    expect(sent.reads.filter((u) => u.includes('/api/v2/'))).toEqual([expect.stringContaining('/api/v2/rules/due')]);
  });

  const detail = {
    id: 'd1',
    due_on: '2026-12-01',
    repeats: 'yearly',
    status: 'due_soon',
    owner: 'human:me',
    rule: { id: 'o1', text: 'File the yearly report' },
    can_edit: true,
    can_satisfy: true,
    open_days: ['2026-12-01'],
    task_days: null,
    chain: [{ id: 'd1', satisfied: [{ due_on: '2025-12-01', satisfied_by: 'human:me', via: 'person' }] }],
  };

  it('opens a due date from its row, with its history, and marks it satisfied @s549', async () => {
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/rule-due/d1/satisfied') ? ok({}) : url.endsWith('/rule-due/d1') ? ok(detail) : url.includes('/rules/due') ? ok({ due: [] }) : nothing(),
    );
    queryClient.setQueryData(dueQuery.queryKey, [{ id: 'd1', due_on: '2026-12-01', status: 'due_soon', owner: 'human:me', rule: { id: 'o1', text: 'File the yearly report' } }]);
    show('/company/rules?tab=due&panel=due:d1');
    expect(href(within(await screen.findByTitle('File the yearly report')).getByRole('link'))).toBe('/company/rules?tab=due&panel=due:d1');
    const panel = await screen.findByRole('complementary', { name: 'Due date' });
    expect(await within(panel).findByText('Dec 1, 2026')).toBeTruthy();
    expect(within(panel).getByTitle(/Marked by/)).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Mark satisfied' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({});
  });

  it('adds a due date to a rule @s550', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/rules/o1/due') ? ok({ ...detail, id: 'd2' }) : nothing()));
    queryClient.setQueryData(dueQuery.queryKey, []);
    queryClient.setQueryData<OrgRule[]>(orgRulesQuery('active').queryKey, [{ id: 'o1', kind: 'external', text: 'File the yearly report', status: 'active', version: 1, can_edit: true }]);
    show('/company/rules?tab=due&panel=due:new');
    await screen.findByRole('complementary', { name: 'New due date' });
    await pick(/Rule/, 'File the yearly report');
    const next = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 15);
    fireEvent.click(screen.getByRole('button', { name: /Due/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Next month' }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`${next.toLocaleString('en-US', { month: 'long' })} 15$`) }));
    await pick(/Routine/, 'A task before it');
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const day = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-15`;
    expect(sent[0]!.body).toEqual({ first_due: day, repeats: 'yearly', owner: 'human:me', task_days: 14 });
  });

  it('sends a file to be read for outside rules, finding older files as words are typed, and links the reading task @s547', async () => {
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/rules/read')
        ? ok({ task_id: 't5', existing: false })
        : url.includes('/files/search')
          ? ok({ results: [{ id: 'f9', title: 'Old vendor contract' }] })
          : url.includes('/files?')
            ? ok({ files: [{ id: 'f1', title: 'Vendor terms' }, { id: 'f2', title: 'Logo', library_kind: 'image' }] })
            : url.includes('/rule-sources')
              ? ok({ sources: [{ id: 'src1', name: 'Made Up Supply', kind: 'vendor' }] })
              : url.includes('/rules/due')
                ? ok({ due: [] })
                : nothing(),
    );
    queryClient.setQueryData(dueQuery.queryKey, []);
    show('/company/rules?tab=due');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add document' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add document' });
    const box = within(dialog).getByRole('combobox', { name: 'File' });
    box.focus();
    fireEvent.input(box, { target: { value: 'vendor' }, inputType: 'insertText' });
    expect(await screen.findByRole('option', { name: /Old vendor contract/ })).toBeTruthy();
    fireEvent.click(await screen.findByRole('option', { name: /Vendor terms/ }));
    await pick(/From/, 'Made Up Supply');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Read it' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ file_id: 'f1', note: '', source_id: 'src1' });
    const done = await screen.findByRole('dialog', { name: 'Add document' });
    expect(await within(done).findByText('Reading Vendor terms.')).toBeTruthy();
    expect(href(within(done).getByRole('link', { name: 'Open task' }))).toBe('/task/t5');
  });
});

describe('Rules: Wakes, Routines on tasks and Built in', () => {
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
});

describe('Rules: Suggested', () => {
  it('Add takes a suggestion in, asking who follows one with no Who; Set aside skips one @s524', async () => {
    const sent = record((url, method) => (method === 'POST' && /\/rules\/o\d\/(accept|skip)$/.test(url) ? ok({}) : nothing()));
    queryClient.setQueryData(orgRulesQuery('suggested').queryKey, [
      org({ id: 'o7', status: 'suggested', version: 2, text: 'Refunds wait for an OK', who: {} }),
      org({ id: 'o8', status: 'suggested', version: 3, text: 'Refunds over $80 wait for an OK', suggested_by: 'bot:support' }),
      org({ id: 'o9', status: 'suggested', version: 2, text: 'Post nothing after 10 PM', if_broken: 'warn' }),
    ]);
    stageRules([], 'suggested');
    show('/company/rules?tab=suggested');
    fireEvent.click(within(await screen.findByTitle('Refunds wait for an OK')).getByRole('button', { name: 'Add' }));
    const dialog = await screen.findByRole('dialog', { name: 'Who follows it?' });
    expect((within(dialog).getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
    await pick('Who', 'Engineering', dialog);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ url: expect.stringContaining('/rules/o7/accept'), body: { version: 2, who: { groups: ['g1'] } } });

    fireEvent.click(within(screen.getByTitle('Refunds over $80 wait for an OK')).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toMatchObject({ method: 'POST', body: { version: 3 } });
    expect(sent[1]!.url).toContain('/api/v2/rules/o8/accept');
    fireEvent.click(within(screen.getByTitle('Post nothing after 10 PM')).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Set aside' }));
    await waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]!.url).toContain('/api/v2/rules/o9/skip');
  });

  it('a clerk’s draft is not added here: Open task goes to its task, where its OK adds it', async () => {
    const sent = record();
    queryClient.setQueryData(orgRulesQuery('suggested').queryKey, [
      org({ id: 'o6', status: 'suggested', text: 'Refunds over $100 go to Ben first', bound: true, clerk_task: 't5' }),
      // a clerk's draft on a task this reader cannot open: no Add, and nothing names the task
      org({ id: 'o5', status: 'suggested', text: 'Mail only the partner domains', bound: true, clerk_task: null }),
    ]);
    stageRules([], 'suggested');
    const router = show('/company/rules?tab=suggested');
    const row = await screen.findByTitle('Refunds over $100 go to Ben first');
    expect(within(row).queryByRole('button', { name: 'Add' })).toBeNull();
    const hidden = screen.getByTitle('Mail only the partner domains');
    expect(within(hidden).queryByRole('button', { name: 'Add' })).toBeNull();
    expect(within(hidden).queryByRole('button', { name: 'Open task' })).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: 'Open task' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/task/t5'));
    expect(sent).toHaveLength(0);
  });

  it('a suggested change to a stage rule says what it changes @s524', async () => {
    record();
    stageRules([{ ...stage, name: 'Review gate', jev: '' }]);
    stageRules([{ ...stage, id: 'sr9', name: 'Review gate v2', jev: '', status: 'suggested', suggests: 'sr1', reason: 'Tests first' } as StageRule], 'suggested');
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

  it('says so when nothing is suggested', async () => {
    record((url) => (url.includes('status=suggested') ? ok(url.includes('stage-rules') ? { items: [], built_in: [], may_add: true } : { rules: [] }) : nothing()));
    show('/company/rules?tab=suggested');
    expect(await screen.findByText('No suggestions')).toBeTruthy();
  });
});

describe('Rules: pipelines and stages', () => {
  it('groups stage rules by pipeline; a row opens its rule, keeping the search @s519', async () => {
    record();
    stageRules([stage]);
    show('/company/rules?tab=stages&q=review');
    const row = await screen.findByTitle('Enters Review, the decision model decides, Block');
    expect(href(within(row).getByRole('link'))).toBe('/company/rules?tab=stages&q=review&panel=stage:sr1');
    expect(screen.getByRole('region', { name: 'Engineering' })).toBeTruthy();
    expect(within(row).getByTitle('Decision model')).toBeTruthy();
  });

  it('one rule open: when, check and then, its pipeline, and Edit @s548', async () => {
    record();
    stageRules([stage]);
    show('/company/rules?tab=stages&panel=stage:sr1');
    const panel = await screen.findByRole('complementary', { name: 'Enters Review' });
    expect(within(panel).getByText('the decision model decides')).toBeTruthy();
    expect(within(panel).getByText('Block')).toBeTruthy();
    expect(within(panel).getByText('Engineering')).toBeTruthy();
    expect(within(panel).getByText('Is it ready?')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Edit' }));
    expect(await screen.findByRole('combobox', { name: 'When' })).toBeTruthy();
  });

  it('removes a rule only after asking, and keeps it while the removal is out and when it is refused', async () => {
    let refuse: (r: Reply) => void = () => {};
    let sent = false;
    const removal = () => {
      sent = true;
      return new Promise<Reply>((ok) => (refuse = ok));
    };
    record((url, method) => (method === 'POST' ? removal() : url.includes('/stage-rules') ? ok({ items: [stage], built_in: [], may_add: true }) : nothing()));
    stageRules([stage]);
    show('/company/rules?tab=stages');
    const row = await screen.findByTitle('Enters Review, the decision model decides, Block');
    fireEvent.click(within(row).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    const ask = await screen.findByRole('alertdialog');
    expect(screen.getByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(sent).toBe(true));
    expect(screen.getByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
    refuse({ status: 409, body: { detail: 'conflict' } });
    expect(await screen.findByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
  });
});

describe('A stage’s rules', () => {
  const base = { name: '', pipeline_id: 'p1', pipeline_name: 'Engineering', stage_names: ['Review'], timer: null, cond: {}, then: 'block', status: 'active', revision: 1, can_edit: true };
  const wide = { ...base, id: 'sr2', stages: [], stage_names: [], scope: 'pipeline', event: 'timer', timer: { kind: 'in_stage', hours: 24 }, jev: '', then: 'tell_owner' } as StageRule;
  const elsewhere = { ...base, id: 'sr3', pipeline_id: 'p2', stages: ['s9'], scope: 'stage', event: 'enters', jev: '' } as StageRule;
  const rules: StageRules = { items: [stage, wide, elsewhere], built_in: [], may_add: true };
  const leave = { requirements: [{ kind: 'page', settings: { document: 'Spec' } }, { kind: 'pr_merged' }], by_hand: true };

  it('lists the stage’s rules by moment, and what it takes to leave', async () => {
    record();
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, rules);
    show('/tasks', <StageRulesList pipeline="p1" stage="s2" leave={leave} />);
    const enter = await screen.findByRole('region', { name: 'On entering' });
    expect(within(enter).getByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
    const here = screen.getByRole('region', { name: 'While here' });
    expect(within(here).getAllByRole('button', { name: 'More' })).toHaveLength(1);
    const out = screen.getByRole('region', { name: 'To leave' });
    expect(within(out).getByTitle('Approve Spec')).toBeTruthy();
    expect(within(within(out).getByTitle('PR merged')).getByTitle('Built in')).toBeTruthy();
    expect(within(out).getByTitle('OK from anyone')).toBeTruthy();
    expect(within(out).queryByRole('button', { name: 'Add rule' })).toBeNull();
    expect(screen.getAllByRole('button', { name: /^On entering|^While here/ })).toHaveLength(2);
  });

  it('adds a rule with its pipeline and stage preset, and stays on the page', async () => {
    const sent = record((url, method) => (method === 'POST' && url.includes('/api/v2/stage-rules') ? ok({ ...wide, id: 'sr9' }) : url.includes('/stage-rules') ? ok(rules) : nothing()));
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, rules);
    show('/tasks', <StageRulesList pipeline="p1" stage="s2" />);
    const here = await screen.findByRole('region', { name: 'While here' });
    fireEvent.click(within(here).getByRole('button', { name: 'Add rule' }));
    expect(within(here).queryByRole('combobox', { name: 'Pipeline' })).toBeNull();
    const [, save] = within(here).getAllByRole('button', { name: 'Add rule' });
    fireEvent.click(save!);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ pipeline: 'p1', stages: ['s2'], event: 'timer', then: 'tell_owner' });
    await waitFor(() => expect(within(here).queryByRole('textbox', { name: 'Why' })).toBeNull());
    expect(screen.getByRole('region', { name: 'While here' })).toBeTruthy();
  });
});
