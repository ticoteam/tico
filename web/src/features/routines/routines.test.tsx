import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { ListeningPage } from './ListeningPage';
import { validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
import { dueQuery, hooksQuery, orgRulesQuery, routinesQuery, stageRulesQuery, watchesQuery, type StageRules } from './queries';
import { runLink } from './RoutinePanel';
import { RoutinesPage } from './RoutinesPage';
import { RulesPage } from './RulesPage';
import { StageRules as StageRulesList } from './StageRules';
import type { Hook, OrgRule, Routine, StageRule } from './types';
import { cronWords, orgParts, stageParts } from './words';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const r = await reply(String(url), init?.method ?? 'GET');
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });

type Sent = { url: string; method: string; body: unknown };
/** serve(), keeping every write the page sends. */
function record(reply: (url: string, method: string) => Reply | Promise<Reply>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const ok = (body: unknown): Reply => ({ status: 200, body });
const pick = async (name: string | RegExp, option: string | RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};

/** The page under a route at its own address, with the action layer bound to that router. */
function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: at.split('?')[0] ?? '/',
    component: () => (
      <>
        {node}
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
}
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.closest('a'))?.getAttribute('href') ?? '');

const routine = (over: Partial<Routine> = {}) =>
  ({
    id: 'r1',
    name: 'Morning numbers',
    enabled: true,
    paused: false,
    can_edit: true,
    can_pause: true,
    owner_id: 'bot:growth',
    trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC' },
    steps: [{ id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the numbers.' }],
    last_run: { id: 'run1', status: 'ok', started_at: '2026-11-19T07:00:00Z' },
    ...over,
  }) as unknown as Routine;

describe('words', () => {
  it('says a schedule, a stage rule and a company rule in plain words', () => {
    expect(cronWords('0 7 * * *')).toBe('every day at 7 AM');
    const r = { event: 'enters', timer: null, cond: {}, jev: 'Is the spec approved?', then: 'block', stage_names: ['Review'] } as unknown as StageRule;
    expect(stageParts(r)).toEqual(['Enters Review', 'the decision model decides', 'Block']);
    expect(orgParts({ text: 'Never share a card number', if_broken: 'warn', reviewer: null })).toEqual(['Never share a card number', 'Warn']);
  });

  it('links a run to the task or thread it made', () => {
    expect(runLink({ steps: [{ outputs: { turn: { task: 't9' } } }] })).toBe('/task/t9');
    expect(runLink({ steps: [{ outputs: { thread: 'th2' } }] })).toBe('/threads/th2');
    expect(runLink({ steps: null })).toBeUndefined();
  });

  it('reads an id that looks like a number back as text, and drops what it does not know', () => {
    expect(validateListeningSearch({ watch: 12, tab: 'sources', match: 7 })).toEqual({ watch: '12', tab: 'sources', match: '7' });
    expect(validateRoutinesSearch({ tab: 'nope', panel: 'routine:5' })).toEqual({ panel: 'routine:5' });
    expect(validateRulesSearch({ tab: 'stages', who: 'group:3', panel: 'x' })).toEqual({ tab: 'stages', who: 'group:3' });
  });
});

describe('Routines', () => {
  it('lists each routine under what starts it, opening it in the panel', async () => {
    serve(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, [routine()]);
    show(<RoutinesPage search={{}} />, '/company/routines');
    const row = await screen.findByTitle('Morning numbers');
    expect(href(row)).toBe('/company/routines?panel=routine:r1');
    expect(screen.getByRole('region', { name: 'Scheduled' })).toBeTruthy();
    expect(within(row).getByText('Every day at 7 AM')).toBeTruthy();
  });

  it('pauses at once and takes it back when refused', async () => {
    let refuse: (r: Reply) => void = () => {};
    serve((_url, method) => (method === 'PATCH' ? new Promise<Reply>((ok) => (refuse = ok)) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [routine()]);
    show(<RoutinesPage search={{ panel: 'routine:r1' }} />, '/company/routines?panel=routine:r1');
    const panel = await screen.findByRole('complementary', { name: 'Morning numbers' });
    fireEvent.click(within(panel).getByRole('switch', { name: 'On' }));
    await waitFor(() => expect(within(panel).getByRole('switch', { name: 'Paused' })).toBeTruthy());
    expect(screen.getByTitle('Morning numbers').className).toContain('is-off');
    refuse({ status: 403, body: { detail: 'forbidden' } });
    await waitFor(() => expect(within(panel).getByRole('switch', { name: 'On' })).toBeTruthy());
  });

  it('says so when there are none, and when they fail to load', async () => {
    serve(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show(<RoutinesPage search={{}} />, '/company/routines');
    expect(await screen.findByText('No routines yet')).toBeTruthy();
  });

  it('shows the fault with Try again', async () => {
    serve((url) => (url.includes('/automations') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(<RoutinesPage search={{}} />, '/company/routines');
    expect(await screen.findByText('Routines did not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('A routine: test run, versions, edit', () => {
  const openMenu = async (item: string) => {
    const panel = await screen.findByRole('complementary', { name: 'Morning numbers' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: item }));
  };

  it('runs it with sample input for its trigger, and says when the filters drop it', async () => {
    let answer: unknown = { run: { id: 'run9', status: 'ok', steps: null } };
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r1/run') ? ok(answer) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ trigger: { type: 'event', name: 'task.created' } })]);
    show(<RoutinesPage search={{ panel: 'routine:r1' }} />, '/company/routines?panel=routine:r1');
    await openMenu('Test run');
    const dialog = await screen.findByRole('dialog', { name: 'Test run' });
    answer = { run: null, dropped: true };
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ input: { event: { name: 'task.created' } } });
    expect(await within(dialog).findByText("Dropped by the trigger's filters")).toBeTruthy();
  });

  it('lists its versions and proposals; restores one and discards a proposal', async () => {
    const versions = [
      { n: 1, actor: 'human:me', created_at: '2026-11-01T09:00:00Z', flow: {}, status: 'active' },
      { n: 2, actor: 'human:me', created_at: '2026-11-02T09:00:00Z', flow: {}, status: 'active' },
      { n: 3, actor: 'bot:growth', created_at: '2026-11-03T09:00:00Z', flow: {}, status: 'pending', summary: 'Post at 8 instead' },
    ];
    const sent = record((url, method) =>
      url.endsWith('/automations/r1/versions') && method === 'GET' ? ok({ next: null, versions }) : url.includes('/restore') ? ok(routine({ version: 3 } as Partial<Routine>)) : method === 'DELETE' ? ok({}) : nothing(),
    );
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ version: 2 } as Partial<Routine>)]);
    show(<RoutinesPage search={{ panel: 'routine:r1' }} />, '/company/routines?panel=routine:r1');
    await openMenu('Versions');
    const current = await screen.findByTitle(/^v2 /);
    expect(within(current).getByText('Current')).toBeTruthy();
    expect(screen.getByTitle('v3 Post at 8 instead')).toBeTruthy();
    fireEvent.click(within(screen.getByTitle(/^v1 /)).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'POST' && x.url.endsWith('/automations/r1/versions/1/restore'))).toBe(true));
    fireEvent.click(within(screen.getByTitle('v3 Post at 8 instead')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'DELETE' && x.url.endsWith('/automations/r1/versions/3'))).toBe(true));
  });

  it('changes the instruction and keeps the steps the editor does not draw', async () => {
    const filter = { id: 'f1', type: 'filter', rules: [{ when: [{ field: 'task.pipeline', value: 'p1' }] }] };
    const sent = record((_url, method) => (method === 'PATCH' ? ok(routine()) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ steps: [filter, { id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the numbers.' }] })]);
    show(<RoutinesPage search={{ panel: 'routine:r1' }} />, '/company/routines?panel=routine:r1');
    await openMenu('Edit');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Instruction' }), { target: { value: 'Post the totals.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]!.body as { steps: unknown[] }).steps).toEqual([filter, { id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the totals.' }]);
  });
});

describe('A webhook: payload and signing', () => {
  const hook: Hook = { id: 'h1', name: 'Shop orders', url: 'https://in.example/hooks/h1', signed: false, automations: [] };
  const calls = { deliveries: [{ delivery: 'd1', received_at: '2026-11-19T07:00:00Z', outcome: 'accepted', tasks: 1, stored: true }] };

  it('opens a kept call’s headers and body', async () => {
    record((url) =>
      url.endsWith('/triggers/h1/deliveries/d1')
        ? ok({ delivery: { received_at: '2026-11-19T07:00:00Z', type: 'order.paid', bytes: 7, expired: false, headers: { 'content-type': 'application/json' }, body: '{"a":1}' } })
        : url.endsWith('/triggers/h1/deliveries')
          ? ok(calls)
          : nothing(),
    );
    queryClient.setQueryData(hooksQuery.queryKey, [hook]);
    show(<RoutinesPage search={{ tab: 'webhooks', panel: 'hook:h1' }} />, '/company/routines?tab=webhooks&panel=hook:h1');
    fireEvent.click(await screen.findByRole('button', { name: 'Payload' }));
    const dialog = await screen.findByRole('dialog', { name: 'Payload' });
    const body = await within(dialog).findByRole('region', { name: 'Body' });
    expect(body.querySelector('pre')?.textContent).toBe('{\n  "a": 1\n}');
    expect(within(dialog).getByRole('region', { name: 'Headers' }).textContent).toContain('content-type: application/json');
  });

  it('turns signing on and shows the made secret once, never in the cache', async () => {
    const secret = 'whsec_made_up_7f3a';
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/triggers/h1/signing')
        ? ok({ trigger: { id: 'h1', signed: true, preset: 'hmac', signing_secret: secret, replayed: false } })
        : url.endsWith('/api/v2/triggers')
          ? ok({ triggers: [{ ...hook, signed: true, preset: 'hmac' }] })
          : url.endsWith('/deliveries')
            ? ok(calls)
            : nothing(),
    );
    queryClient.setQueryData(hooksQuery.queryKey, [hook]);
    show(<RoutinesPage search={{ tab: 'webhooks', panel: 'hook:h1' }} />, '/company/routines?tab=webhooks&panel=hook:h1');
    const panel = await screen.findByRole('complementary', { name: 'Shop orders' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Signing' }));
    const dialog = await screen.findByRole('dialog', { name: 'Signing' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ preset: 'hmac' });
    expect(await within(panel).findByText(secret)).toBeTruthy();
    const cached = JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data));
    expect(cached).not.toContain(secret);
    expect(window.location.href).not.toContain(secret);
  });
});

describe('A webhook: the address shown once', () => {
  const made = 'https://in.example/hooks/h7/made_up_secret_91c2';
  const rotated = 'https://in.example/hooks/h7/made_up_secret_rotated_4b0e';
  /** The page reading its search from the router, so the action's move to the panel lands. */
  const Live = () => <RoutinesPage search={useSearch({ strict: false })} />;
  const inCache = (secret: string) =>
    JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data])).includes(secret);

  it('shows the new address and a new secret in the panel only, never in the cache or the address', async () => {
    record((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/triggers')
        ? ok({ trigger: { id: 'h7', name: 'Shipping', url: made } })
        : method === 'POST' && url.endsWith('/triggers/h7/rotate')
          ? ok({ trigger: { id: 'h7', url: rotated } })
          : url.endsWith('/deliveries')
            ? ok({ deliveries: [] })
            : nothing(),
    );
    queryClient.setQueryData(hooksQuery.queryKey, []);
    show(<Live />, '/company/routines?tab=webhooks');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add webhook' }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'Add webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Shipping' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    const panel = await screen.findByRole('complementary', { name: 'Shipping' });
    expect(await within(panel).findByText(made)).toBeTruthy();
    expect(inCache(made)).toBe(false);
    expect(window.location.href).not.toContain('made_up_secret');

    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New secret' }));
    expect(await within(panel).findByText(rotated)).toBeTruthy();
    expect(inCache(rotated)).toBe(false);
    expect(inCache('made_up_secret')).toBe(false);
  });
});

describe('Rules: due dates and documents', () => {
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

  it('opens a due date from its row, with its history, and marks it satisfied', async () => {
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/rule-due/d1/satisfied') ? ok({}) : url.endsWith('/rule-due/d1') ? ok(detail) : url.includes('/rules/due') ? ok({ due: [] }) : nothing(),
    );
    queryClient.setQueryData(dueQuery.queryKey, [{ id: 'd1', due_on: '2026-12-01', status: 'due_soon', owner: 'human:me', rule: { id: 'o1', text: 'File the yearly report' } }]);
    show(<RulesPage search={{ tab: 'due', panel: 'due:d1' }} />, '/company/rules?tab=due&panel=due:d1');
    expect(href(within(await screen.findByTitle('File the yearly report')).getByRole('link'))).toBe('/company/rules?tab=due&panel=due:d1');
    const panel = await screen.findByRole('complementary', { name: 'Due date' });
    expect(await within(panel).findByText('Dec 1, 2026')).toBeTruthy();
    expect(within(panel).getByTitle(/Marked by/)).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Mark satisfied' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({});
  });

  it('adds a due date to a rule', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/rules/o1/due') ? ok({ ...detail, id: 'd2' }) : nothing()));
    queryClient.setQueryData(dueQuery.queryKey, []);
    queryClient.setQueryData<OrgRule[]>(orgRulesQuery('active').queryKey, [
      { id: 'o1', kind: 'external', text: 'File the yearly report', status: 'active', version: 1, can_edit: true },
    ]);
    show(<RulesPage search={{ tab: 'due', panel: 'due:new' }} />, '/company/rules?tab=due&panel=due:new');
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

  it('sends a file to be read for outside rules and links the reading task', async () => {
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/rules/read')
        ? ok({ task_id: 't5', existing: false })
        : url.includes('/files?')
          ? ok({ files: [{ id: 'f1', title: 'Vendor terms' }, { id: 'f2', title: 'Logo', library_kind: 'image' }] })
          : url.includes('/rule-sources')
            ? ok({ sources: [{ id: 'src1', name: 'Made Up Supply', kind: 'vendor' }] })
            : url.includes('/rules/due')
              ? ok({ due: [] })
              : nothing(),
    );
    queryClient.setQueryData(dueQuery.queryKey, []);
    show(<RulesPage search={{ tab: 'due' }} />, '/company/rules?tab=due');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add document' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add document' });
    const box = within(dialog).getByRole('combobox', { name: 'File' });
    box.focus();
    fireEvent.input(box, { target: { value: 'vendor' }, inputType: 'insertText' });
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

describe('Rules', () => {
  const stage = { id: 'sr1', name: '', pipeline_id: 'p1', pipeline_name: 'Engineering', stages: ['s2'], stage_names: ['Review'], scope: 'stage', event: 'enters', timer: null, cond: {}, jev: 'Is it ready?', then: 'block', status: 'active', revision: 1, can_edit: true } as StageRule;

  it('groups stage rules by pipeline; a row opens its rule', async () => {
    serve(nothing);
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, { items: [stage], built_in: [], may_add: true });
    show(<RulesPage search={{ tab: 'stages' }} />, '/company/rules?tab=stages');
    const row = await screen.findByTitle('Enters Review, the decision model decides, Block');
    expect(href(within(row).getByRole('link'))).toBe('/company/rules?tab=stages&panel=stage:sr1');
    expect(screen.getByRole('region', { name: 'Engineering' })).toBeTruthy();
    expect(within(row).getByTitle('Decision model')).toBeTruthy();
  });

  it('removes a rule only after asking, and puts it back when refused', async () => {
    let refuse: (r: Reply) => void = () => {};
    const shown: StageRules = { items: [stage], built_in: [], may_add: true };
    serve((url, method) => (method === 'POST' ? new Promise<Reply>((ok) => (refuse = ok)) : url.includes('/stage-rules') ? { status: 200, body: shown } : nothing()));
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, { items: [stage], built_in: [], may_add: true });
    show(<RulesPage search={{ tab: 'stages' }} />, '/company/rules?tab=stages');
    const row = await screen.findByTitle('Enters Review, the decision model decides, Block');
    fireEvent.click(within(row).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    const ask = await screen.findByRole('alertdialog');
    expect(screen.getByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.queryByTitle('Enters Review, the decision model decides, Block')).toBeNull());
    refuse({ status: 409, body: { detail: 'conflict' } });
    expect(await screen.findByTitle('Enters Review, the decision model decides, Block')).toBeTruthy();
  });

  it('says so when nothing is suggested', async () => {
    serve((url) => (url.includes('status=suggested') ? { status: 200, body: url.includes('stage-rules') ? { items: [], built_in: [], may_add: true } : { rules: [] } } : nothing()));
    show(<RulesPage search={{ tab: 'suggested' }} />, '/company/rules?tab=suggested');
    expect(await screen.findByText('No suggestions')).toBeTruthy();
  });
});

describe('StageRules', () => {
  const base = { name: '', pipeline_id: 'p1', pipeline_name: 'Engineering', stage_names: ['Review'], timer: null, cond: {}, then: 'block', status: 'active', revision: 1, can_edit: true };
  const entering = { ...base, id: 'sr1', stages: ['s2'], scope: 'stage', event: 'enters', jev: 'Is it ready?' } as StageRule;
  const wide = { ...base, id: 'sr2', stages: [], stage_names: [], scope: 'pipeline', event: 'timer', timer: { kind: 'in_stage', hours: 24 }, jev: '', then: 'tell_owner' } as StageRule;
  const elsewhere = { ...base, id: 'sr3', pipeline_id: 'p2', stages: ['s9'], scope: 'stage', event: 'enters', jev: '' } as StageRule;
  const rules: StageRules = { items: [entering, wide, elsewhere], built_in: [], may_add: true };
  const leave = { requirements: [{ kind: 'page', settings: { document: 'Spec' } }, { kind: 'pr_merged' }], by_hand: true };

  it('lists the stage’s rules by moment, and what it takes to leave', async () => {
    serve(nothing);
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, rules);
    show(<StageRulesList pipeline="p1" stage="s2" leave={leave} />, '/tasks');
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
    const sent: unknown[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const post = init?.method === 'POST' && String(url).includes('/api/v2/stage-rules');
      if (post) sent.push(JSON.parse(String(init.body)));
      const body = post ? { ...wide, id: 'sr9' } : String(url).includes('/stage-rules') ? rules : { detail: 'not_found' };
      return new Response(JSON.stringify(body), { status: post || String(url).includes('/stage-rules') ? 200 : 404, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData<StageRules>(stageRulesQuery('active').queryKey, rules);
    show(<StageRulesList pipeline="p1" stage="s2" />, '/tasks');
    const here = await screen.findByRole('region', { name: 'While here' });
    fireEvent.click(within(here).getByRole('button', { name: 'Add rule' }));
    expect(within(here).queryByRole('combobox', { name: 'Pipeline' })).toBeNull();
    const [, save] = within(here).getAllByRole('button', { name: 'Add rule' });
    fireEvent.click(save!);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ pipeline: 'p1', stages: ['s2'], event: 'timer', then: 'tell_owner' });
    await waitFor(() => expect(within(here).queryByRole('textbox', { name: 'Why' })).toBeNull());
    expect(screen.getByRole('region', { name: 'While here' })).toBeTruthy();
  });
});

describe('Listening', () => {
  it('lists each watch, opening it', async () => {
    serve(nothing);
    queryClient.setQueryData(watchesQuery.queryKey, [routine({ id: 'w1', name: 'Mentions of us', trigger: { type: 'listening', sources: [{ id: 'a', kind: 'search' }], limits: [{ count: 10, per: 'day' }] } })]);
    show(<ListeningPage search={{}} />, '/company/listening');
    const row = await screen.findByTitle('Mentions of us');
    expect(href(row)).toBe('/company/listening?watch=w1');
    expect(within(row).getByText('1 source')).toBeTruthy();
    expect(within(row).getByText('10 a day')).toBeTruthy();
  });

  it('says so when there are none', async () => {
    serve(nothing);
    queryClient.setQueryData(watchesQuery.queryKey, []);
    show(<ListeningPage search={{}} />, '/company/listening');
    expect(await screen.findByText('No watches yet')).toBeTruthy();
  });
});
