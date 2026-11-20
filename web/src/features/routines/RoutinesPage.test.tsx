import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the router's error component, as the app wires it
import { PageFault } from '@/shell/PageFault';
import { validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
import { deliveriesQuery, heartbeatQuery, hooksQuery, maintenanceQuery, maintenanceRunsQuery, routinesQuery, runsQuery, systemJobsQuery, templatesQuery, versionsQuery } from './queries';
import { RoutinesPage } from './RoutinesPage';
import type { Hook, Maintenance, Routine, StageRule, SystemJob } from './types';
import { cronWords, orgParts, runLink, stageParts } from './words';

/* The Routines page: routines and one routine's panel, the built-in ones, runs, webhooks, the inbox board and
   inbox templates. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const ME = { id: 'human:me', kind: 'person', name: 'Ada Example', slug: 'ada' };
beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: 'ada@company.example', role: 'person', slug: 'ada' });
  queryClient.setQueryData(['directory'], { actors: [ME], byId: { 'human:me': ME } });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
const failed = (): Reply => ({ status: 500, body: { error: { code: 'boom', detail: 'boom' } } });
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
const menu = async (scope: HTMLElement, item: string) => {
  fireEvent.click(within(scope).getByRole('button', { name: 'More' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: item }));
};
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.closest('a'))?.getAttribute('href') ?? '');

/** The page at its own address, reading its search from the router so an action's move lands, with the action layer bound. */
function show(at: string) {
  const Live = () => <RoutinesPage search={useSearch({ strict: false })} />;
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: '/company/routines',
    validateSearch: validateRoutinesSearch,
    component: () => (
      <>
        <Live />
        <ActionsRoot dot={() => null} fold={() => {}} />
      </>
    ),
  });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }), defaultErrorComponent: PageFault });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

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
    ...over,
  }) as unknown as Routine;
const openRoutine = (over: Partial<Routine> = {}) => {
  queryClient.setQueryData(routinesQuery.queryKey, [routine(over)]);
  show(`/company/routines?panel=routine:${over.id ?? 'r1'}`);
  return screen.findByRole('complementary', { name: over.name ?? 'Morning numbers' });
};

describe('words and the address', () => {
  it('says a schedule, a stage rule and a company rule in plain words', () => {
    expect(cronWords('0 7 * * *')).toBe('every day at 7:00 AM');
    expect(cronWords('30 1,9,17 * * *')).toBe('every day at 1:30 AM, 9:30 AM and 5:30 PM');
    expect(cronWords('0 9 * * 4')).toBe('every Thursday at 9:00 AM');
    const r = { event: 'enters', timer: null, cond: {}, jev: 'Is the spec approved?', then: 'block', stage_names: ['Review'] } as unknown as StageRule;
    expect(stageParts(r)).toEqual(['Enters Review', 'the decision model decides', 'Block']);
    expect(orgParts({ text: 'Never share a card number', if_broken: 'warn', reviewer: null })).toEqual(['Never share a card number', 'Warn']);
  });

  it('links a run to the task or thread it made', () => {
    expect(runLink({ steps: [{ outputs: { turn: { task: 't9' } } }] })).toBe('/task/t9');
    expect(runLink({ steps: [{ outputs: { thread: 'th2' } }] })).toBe('/threads/th2');
    expect(runLink({ steps: null })).toBeUndefined();
  });

  it('reads an id that looks like a number back as text, and keeps only the tabs, filters, statuses and ranges it knows @s509', () => {
    expect(validateListeningSearch({ watch: 12, tab: 'sources', match: 7 })).toEqual({ watch: '12', tab: 'sources', match: '7' });
    expect(validateRoutinesSearch({ tab: 'nope', panel: 'routine:5' })).toEqual({ panel: 'routine:5' });
    expect(validateRulesSearch({ tab: 'stages', who: 'group:3', panel: 'x' })).toEqual({ tab: 'stages', who: 'group:3' });
    expect(validateRoutinesSearch({ q: 'numbers', state: 'failed' })).toEqual({ q: 'numbers', state: 'failed' });
    expect(validateRoutinesSearch({ state: 'nope' })).toEqual({});
    expect(validateRoutinesSearch({ tab: 'runs', q: 'x' })).toEqual({ tab: 'runs' });
    expect(validateRoutinesSearch({ tab: 'runs', status: 'failed', range: '7d' })).toEqual({ tab: 'runs', status: 'failed', range: '7d' });
    expect(validateRoutinesSearch({ tab: 'runs', status: 'lost', range: '1y' })).toEqual({ tab: 'runs' });
    expect(validateRoutinesSearch({ tab: 'webhooks', status: 'failed' })).toEqual({ tab: 'webhooks' });
  });
});

describe('Routines', () => {
  it('lists each routine under what starts it, opening it in the panel @s509', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [routine()]);
    show('/company/routines');
    const row = await screen.findByTitle('Morning numbers');
    expect(href(row)).toBe('/company/routines?panel=routine:r1');
    expect(screen.getByRole('region', { name: 'Scheduled' })).toBeTruthy();
    expect(within(row).getByText('Every day at 7:00 AM')).toBeTruthy();
  });

  it('with no routines the head keeps only ⋯, and the empty state holds Add routine @s509', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show('/company/routines');
    expect(await screen.findByText('No routines yet')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Add routine' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Filter' })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Search routines' })).toBeNull();
  });

  it('shows the fault with Try again', async () => {
    record((url) => (url.includes('/automations') ? failed() : nothing()));
    show('/company/routines');
    expect(await screen.findByText('This page did not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('counts a built-in routine as a search match', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ name: 'Old cleanup' })]);
    queryClient.setQueryData(systemJobsQuery.queryKey, []);
    show('/company/routines?q=System%20jobs');
    expect(await screen.findByRole('link', { name: /System jobs/ })).toBeTruthy();
    expect(screen.queryByText('No matches')).toBeNull();
  });

  it('keeps the search and state filter when opening and closing a routine', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [routine({ name: 'Old cleanup', enabled: false }), routine({ id: 'r2' })]);
    const router = show('/company/routines?q=cleanup&state=off');
    const link = await screen.findByRole('link', { name: /Old cleanup/ });
    router.history.push(link.getAttribute('href')!);
    const panel = await screen.findByRole('complementary', { name: 'Old cleanup' });
    expect(router.state.location.search).toMatchObject({ q: 'cleanup', state: 'off', panel: 'routine:r1' });
    expect(screen.queryByTitle('Morning numbers')).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ q: 'cleanup', state: 'off' }));
  });

  it('finds routines by name and filters by state, both in the address @s509', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [
      routine(),
      routine({ id: 'r2', name: 'Weekly invoices', last_run: { status: 'failed' } } as unknown as Partial<Routine>),
      routine({ id: 'r3', name: 'Old cleanup', enabled: false }),
    ]);
    const router = show('/company/routines');
    expect(await screen.findByText('Morning numbers')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Search routines' })).toBeTruthy();

    const r = await run('routines.list', { q: 'invoice' }, { via: 'click' });
    expect(r.ok).toBe(true);
    await waitFor(() => expect(screen.queryByText('Morning numbers')).toBeNull());
    expect(screen.getByText('Weekly invoices')).toBeTruthy();
    expect(router.state.location.search).toMatchObject({ q: 'invoice' });

    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Off' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'invoice', state: 'off' }));
    expect(await screen.findByText('No matches')).toBeTruthy();

    await run('routines.list', { state: 'off' }, { via: 'tico' });
    expect(await screen.findByText('Old cleanup')).toBeTruthy();
    expect(screen.queryByText('Weekly invoices')).toBeNull();
    // the built-in ones step aside while a state is picked
    expect(screen.queryByText('Built in')).toBeNull();
  });

  it('pauses at once and takes it back when refused', async () => {
    let refuse: (r: Reply) => void = () => {};
    record((_url, method) => (method === 'PATCH' ? new Promise<Reply>((ok) => (refuse = ok)) : nothing()));
    const panel = await openRoutine();
    fireEvent.click(within(panel).getByRole('switch', { name: 'On' }));
    await waitFor(() => expect(within(panel).getByRole('switch', { name: 'Paused' })).toBeTruthy());
    expect(screen.getByTitle('Morning numbers').className).toContain('is-off');
    refuse({ status: 403, body: { detail: 'forbidden' } });
    await waitFor(() => expect(within(panel).getByRole('switch', { name: 'On' })).toBeTruthy());
  });
});

describe('One routine', () => {
  it('shows what starts it, what it does, its limit and instruction; Test run needs edit rights @s510', async () => {
    record();
    const p = await openRoutine({ can_edit: false });
    expect(within(p).getByText('Trigger')).toBeTruthy();
    expect(within(p).getByText('Every day at 7:00 AM')).toBeTruthy();
    expect(within(p).getByText('Does')).toBeTruthy();
    expect(within(p).getByText('Limit')).toBeTruthy();
    expect(within(p).getByText('Post the numbers.')).toBeTruthy();
    fireEvent.click(within(p).getByRole('button', { name: 'More' }));
    expect((await screen.findByRole('menuitem', { name: 'Test run' })).getAttribute('aria-disabled')).toBe('true');
  });

  it('a routine that did not load says so in its panel, with Try again @s510', async () => {
    let n = 0;
    record((u) => (u.endsWith('/api/v2/automations/r7') ? (n++, failed()) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show('/company/routines?panel=routine:r7');
    expect(await screen.findByText('This routine did not load')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(n).toBe(2));
  });

  it('says it is on hold, releases its held runs, and keeps Drop from Tico @s510', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r1/held') ? ok(routine()) : nothing()));
    const p = await openRoutine({ hold: { held: 3, dropped: 0, rule: 'flood', since: '2026-11-01T00:00:00Z', task: null } } as unknown as Partial<Routine>);
    expect(within(screen.getByTitle('Morning numbers')).getByText('On hold')).toBeTruthy();
    expect(within(p).getByText('3 held')).toBeTruthy();
    expect(await run('routines.endHold', { routine: 'r1', action: 'drop' }, { via: 'tico' })).toMatchObject({ ok: false });
    fireEvent.click(within(p).getByRole('button', { name: 'Release' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'POST', body: { action: 'release' } });
    await waitFor(() => expect(within(p).queryByText('3 held')).toBeNull());
  });

  it('lists its last runs and runs it now @s528', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r1/run') ? ok({ run: { id: 'run2', status: 'queued', steps: null } }) : nothing()));
    queryClient.setQueryData(runsQuery('r1').queryKey, [{ id: 'run1', automation_id: 'r1', status: 'failed', started_at: '2026-11-18T07:00:00Z', error: 'Ran out of time', steps: null, version: 1 }] as never);
    const p = await openRoutine();
    expect(within(p).getByText('Last runs')).toBeTruthy();
    expect(within(p).getByText('Ran out of time')).toBeTruthy();
    fireEvent.click(within(p).getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.url).toContain('/api/v2/automations/r1/run');
  });

  it('sets its spend limit in dollars each 30 minutes, which Tico may not @s565', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r1') ? ok(routine({ spend_cap: { per_30m: 2.5 } })) : nothing()));
    const p = await openRoutine();
    fireEvent.click(within(p).getByRole('button', { name: 'Change' }));
    fireEvent.change(within(p).getByLabelText('Dollars each 30 minutes'), { target: { value: '2.5' } });
    fireEvent.click(within(p).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PATCH', body: { spend_cap: { per_30m: 2.5 } } });
    expect(await run('routines.setLimit', { routine: 'r1', per30m: 100 }, { via: 'tico' })).toMatchObject({ ok: false });
  });

  it('runs it with sample input for its trigger, and says when the filters drop it', async () => {
    let answer: unknown = { run: { id: 'run9', status: 'ok', steps: null } };
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r1/run') ? ok(answer) : nothing()));
    await menu(await openRoutine({ trigger: { type: 'event', name: 'task.created' } }), 'Test run');
    const dialog = await screen.findByRole('dialog', { name: 'Test run' });
    answer = { run: null, dropped: true };
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ input: { event: { name: 'task.created' } } });
    expect(await within(dialog).findByText("Dropped by the trigger's filters")).toBeTruthy();
  });

  it('a due routine’s test run uses its next due day and names its due date @s566', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r2/run') ? ok({ run: null }) : nothing()));
    const trigger = { type: 'due', first_due: '2026-01-31', repeats: 'yearly', open_days: 14, rule_due_id: 'rd1' } as Routine['trigger'];
    await menu(await openRoutine({ id: 'r2', name: 'Yearly filing', trigger, next_due_at: '2027-01-31' }), 'Test run');
    const dialog = await screen.findByRole('dialog', { name: 'Test run' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ input: { due: { due_on: '2027-01-31', first_due: '2026-01-31', repeats: 'yearly', open_days: 14, rule_due_id: 'rd1' } } });
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
    await menu(await openRoutine({ version: 2 } as Partial<Routine>), 'Versions');
    const current = await screen.findByTitle(/^v2 /);
    expect(within(current).getByText('Current')).toBeTruthy();
    expect(screen.getByTitle('v3 Post at 8 instead')).toBeTruthy();
    fireEvent.click(within(screen.getByTitle(/^v1 /)).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'POST' && x.url.endsWith('/automations/r1/versions/1/restore'))).toBe(true));
    fireEvent.click(within(screen.getByTitle('v3 Post at 8 instead')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'DELETE' && x.url.endsWith('/automations/r1/versions/3'))).toBe(true));
  });

  it('a proposed version shows what it changes with View diff @s567', async () => {
    record();
    queryClient.setQueryData(versionsQuery('r1').queryKey, [
      { n: 3, actor: 'bot:growth', created_at: '2026-11-18T07:00:00Z', status: 'pending', flow: { steps: [{ id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the numbers and the trend.' }] } },
      { n: 2, actor: 'human:me', created_at: '2026-11-17T07:00:00Z', status: 'active' },
    ]);
    await menu(await openRoutine({ version: 2 } as Partial<Routine>), 'Versions');
    fireEvent.click(await screen.findByRole('button', { name: 'View diff' }));
    const diff = await screen.findByLabelText('Diff');
    expect(within(diff).getByText('- Agent turn bot:growth: Post the numbers.')).toBeTruthy();
    expect(within(diff).getByText('+ Agent turn bot:growth: Post the numbers and the trend.')).toBeTruthy();
  });

  it('changes the instruction and keeps the steps the editor does not draw @s563', async () => {
    const filter = { id: 'f1', type: 'filter', rules: [{ when: [{ field: 'task.pipeline', value: 'p1' }] }] };
    const sent = record((_url, method) => (method === 'PATCH' ? ok(routine()) : nothing()));
    await menu(await openRoutine({ steps: [filter, { id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the numbers.' }] }), 'Edit');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Instruction' }), { target: { value: 'Post the totals.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]!.body as { steps: unknown[] }).steps).toEqual([filter, { id: 's1', type: 'bot_turn', bot: 'bot:growth', prompt: 'Post the totals.' }]);
  });

  it('an edit sends the new schedule and keeps its trigger’s filters and zone @s564', async () => {
    const filters = [{ field: 'task.pipeline', op: 'is', value: 'p1' }];
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r1') ? ok(routine()) : nothing()));
    await menu(await openRoutine({ trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC', filters } as Routine['trigger'] }), 'Edit');
    await pick('Every', 'Weekdays');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]!.body as { trigger: unknown }).trigger).toEqual({ type: 'schedule', cron: '0 7 * * 1-5', tz: 'UTC', filters });
  });
});

describe('Built in', () => {
  const PRESETS: SystemJob['presets'] = [
    { key: 'hourly', label: 'Hourly' },
    { key: '4h', label: 'Every 4 hours' },
    { key: 'daily', label: 'Daily at a time' },
  ];
  const builtIn = (panel: string, name: string) => {
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show(`/company/routines?panel=builtin:${panel}`);
    return screen.findByRole('complementary', { name });
  };

  it('Maintenance: turns a step off and runs it now @s511', async () => {
    const sent = record((url, method) => (method !== 'GET' && url.includes('/maintenance') ? ok({}) : nothing()));
    const m: Maintenance = {
      settings: { schedule: 'daily', paused: false },
      steps: [
        { key: 'tidy', name: 'Tidy memory', enabled: true },
        { key: 'core', name: 'Check records', enabled: true, fixed: true },
      ],
      can_change: true,
    };
    queryClient.setQueryData(maintenanceQuery.queryKey, m);
    queryClient.setQueryData(maintenanceRunsQuery.queryKey, []);
    const p = await builtIn('maintenance', 'Maintenance');
    const fixed = within(p).getByRole('switch', { name: 'Check records' }) as HTMLButtonElement;
    expect(fixed.hasAttribute('data-disabled') || fixed.getAttribute('aria-disabled') === 'true' || fixed.disabled).toBe(true);
    fireEvent.click(within(p).getByRole('switch', { name: 'Tidy memory' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PATCH', body: { steps: [{ key: 'tidy', enabled: false }] } });
    fireEvent.click(within(p).getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]!.url).toContain('/api/v2/maintenance/run');
  });

  it('Heartbeat: changes how often it beats @s512', async () => {
    const sent = record((url, method) => (method === 'PUT' && url.endsWith('/heartbeat') ? ok({}) : nothing()));
    queryClient.setQueryData(heartbeatQuery.queryKey, {
      interval_s: 15,
      interval_min: 5,
      interval_max: 60,
      can_change: true,
      last_beat_at: null,
      steps: [{ key: 'wake', name: 'Wake agents', on: true, switch: false, every: 'Every beat', can_change: true, last_ms: 40 }],
      beats: [{ id: 'b1', started_at: '2026-11-18T07:00:00Z', did: 2, failed: 0, total_ms: 120 }],
    } as never);
    const p = await builtIn('heartbeat', 'Heartbeat');
    expect(within(p).getByText('Wake agents')).toBeTruthy();
    expect(within(p).getByText('2 done')).toBeTruthy();
    await pick('Runs', 'Every 30 seconds');
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PUT', body: { interval_s: 30 } });
  });

  it('System jobs: switches one off, runs one now, and shows when a switch-less one last ran @s513', async () => {
    const sent = record((url, method) => (method !== 'GET' && url.includes('/system-routines/') ? ok({}) : nothing()));
    queryClient.setQueryData(systemJobsQuery.queryKey, [
      { key: 'digest', name: 'Daily digest', schedule: 'Daily', on: true, can_change: true, controllable: true },
      { key: 'sweep', name: 'Sweep', what: 'Clears old files', on: true, switch: false, last: { started_at: '2026-11-18T07:00:00Z', ok: true } },
    ] satisfies SystemJob[]);
    const p = await builtIn('system', 'System jobs');
    expect(within(p).queryByRole('switch', { name: 'Sweep' })).toBeNull();
    expect(p.querySelector('[data-target="job:sweep"] .row-age')?.textContent).not.toBe('');
    fireEvent.click(within(p).getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'POST' });
    expect(sent[0]!.url).toContain('/api/v2/system-routines/digest/run');
    fireEvent.click(within(p).getByRole('switch', { name: 'Daily digest' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toMatchObject({ method: 'PUT', body: { on: false } });
  });

  it('System jobs: picks a preset and a daily time, and shows the next run and Last runs @s513', async () => {
    const job: SystemJob = { key: 'kpi_asks', name: 'KPI asks', on: true, can_change: true, switch: true, presets: PRESETS, preset: 'hourly', at: '09:00', next: '2026-11-19T10:00:00Z' };
    const sweep: SystemJob = { key: 'sweep', name: 'Sweep', what: 'Clears old files', on: true, switch: false, last: { started_at: '2026-11-19T07:00:00Z', finished_at: '2026-11-19T07:00:03Z', ok: true } };
    const mail: SystemJob = { key: 'mail', name: 'Mail sync', on: true, switch: false, last: { started_at: '2026-11-19T08:00:00Z', ok: false } };
    const sent = record((url, method) =>
      method === 'PUT' && url.includes('/system-routines/kpi_asks') ? ok({ job: { ...job, preset: 'daily', at: '09:00', schedule: 'Daily at 09:00' } }) : nothing(),
    );
    queryClient.setQueryData(systemJobsQuery.queryKey, [job, sweep, mail]);
    const p = await builtIn('system', 'System jobs');
    expect(within(p).getByText(/Next/)).toBeTruthy();

    await pick('KPI asks schedule', 'Daily at');
    await waitFor(() => expect(sent).toHaveLength(1));
    // its time is kept already, so only the preset goes
    expect(sent[0]).toMatchObject({ method: 'PUT', body: { preset: 'daily' } });
    expect(sent[0]!.body).toEqual({ preset: 'daily' });
    expect(sent[0]!.url).toContain('/api/v2/system-routines/kpi_asks');

    // the time beside Daily at, in 12-hour words, sent alone
    await pick('KPI asks time', '2:30 PM');
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toMatchObject({ method: 'PUT', body: { at: '14:30' } });

    // Last runs: newest first, a failed one says so
    const runs = within(p).getByRole('heading', { name: 'Last runs' }).parentElement!;
    const rows = runs.querySelectorAll('.prun');
    expect([...rows].map((r) => r.querySelector('.row-sub')?.textContent)).toEqual(['Mail sync', 'Sweep']);
    expect(within(rows[0] as HTMLElement).getByText('Failed')).toBeTruthy();
  });

  it('System jobs: a job the person may not change shows its schedule as words @s513', async () => {
    record();
    queryClient.setQueryData(systemJobsQuery.queryKey, [
      { key: 'kpi_asks', name: 'KPI asks', schedule: 'Hourly', on: true, can_change: false, switch: true, presets: PRESETS, preset: 'hourly' },
    ] satisfies SystemJob[]);
    const p = await builtIn('system', 'System jobs');
    expect(within(p).queryByRole('combobox', { name: 'KPI asks schedule' })).toBeNull();
    expect(within(p).getByText('Hourly')).toBeTruthy();
  });
});

describe('Runs', () => {
  const runs = {
    next: null,
    since: '2026-11-18T07:00:00Z',
    runs: [
      { id: 'run1', automation_id: 'r1', automation_name: 'Morning numbers', started_at: '2026-11-19T07:00:00Z', status: 'failed', trigger: 'schedule', owner_id: 'bot:growth', outcome: 'Timed out' },
      { id: 'run2', automation_id: 'sys', automation_name: 'Tidy up', started_at: '2026-11-19T06:00:00Z', status: 'done', trigger: 'system' },
    ],
  };

  it('lists runs across routines, a run opening its routine', async () => {
    record((url) => (url.includes('/automations/runs?') ? ok(runs) : nothing()));
    show('/company/routines?tab=runs');
    const row = await screen.findByTitle('Morning numbers');
    expect(href(row)).toContain('tab=runs');
    expect(href(row)).toContain('panel=routine:r1');
    expect(within(row).getByText('Failed')).toBeTruthy();
    expect(within(row).getByText('Timed out')).toBeTruthy();
    // Tico's own jobs have no page
    expect(screen.getByTitle('Tidy up').tagName).toBe('DIV');
    // the old app's default: the last hour
    expect(screen.getByRole('region', { name: 'Last hour' })).toBeTruthy();
  });

  it('filters by status in the address and says when there are none', async () => {
    const sent = record((url) => (url.includes('/automations/runs?') ? ok(url.includes('status=failed') ? { ...runs, runs: [] } : runs) : nothing()));
    const router = show('/company/routines?tab=runs');
    await screen.findByTitle('Morning numbers');
    fireEvent.click(screen.getByRole('button', { name: 'Filter runs' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Failed' }));
    expect(await screen.findByText('No failed runs in the last hour')).toBeTruthy();
    expect(router.state.location.search).toMatchObject({ tab: 'runs', status: 'failed' });
    expect(sent.reads.some((u) => u.includes('/automations/runs?') && u.includes('status=failed') && u.includes('range=1h'))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByTitle('Morning numbers')).toBeTruthy();
    expect(router.state.location.search).toEqual({ tab: 'runs' });
  });

  it('names the range and the status it looked at when there are none', async () => {
    record((url) => (url.includes('/automations/runs?') ? ok({ next: null, since: '2026-11-19T07:00:00Z', runs: [] }) : nothing()));
    show('/company/routines?tab=runs&status=waiting&range=7d');
    expect(await screen.findByText('No waiting runs in the last 7 days')).toBeTruthy();
  });
});

describe('Webhooks', () => {
  const hook: Hook = { id: 'h1', name: 'Shop orders', url: 'https://in.example/hooks/h1', signed: false, automations: [] };
  const calls = { deliveries: [{ delivery: 'd1', received_at: '2026-11-19T07:00:00Z', outcome: 'accepted', tasks: 0, rules: 1, stored: true }] };
  const openHook = (h: Hook = hook) => {
    queryClient.setQueryData(hooksQuery.queryKey, [h]);
    show('/company/routines?tab=webhooks&panel=hook:h1');
    return screen.findByRole('complementary', { name: h.name });
  };
  const inCache = (secret: string) => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data])).includes(secret);

  it('lists incoming ones with their calls today, unsigned marked, a row opening it; nothing of the outgoing ones is read @s514', async () => {
    const sent = record();
    queryClient.setQueryData(hooksQuery.queryKey, [{ ...hook, received_24h: 3 }]);
    show('/company/routines?tab=webhooks');
    const row = within(await screen.findByRole('region', { name: 'Incoming' })).getByTitle('Shop orders');
    expect(within(row).getByText('Unsigned')).toBeTruthy();
    expect(within(row).getByText('3 in 24 h')).toBeTruthy();
    expect(href(within(row).getByRole('link', { name: 'Shop orders' }))).toBe('/company/routines?tab=webhooks&panel=hook:h1');
    expect(screen.getByRole('button', { name: 'Add webhook' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Outgoing' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(sent.reads.some((u) => u.includes('/api/v2/webhooks'))).toBe(false);
  });

  it('one webhook: how it is signed, its calls with why one was refused, its ⋯, and a rename @s536', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/triggers/h1') ? ok({ trigger: { ...hook, name: 'Store orders' } }) : nothing()));
    queryClient.setQueryData(deliveriesQuery('h1').queryKey, [{ delivery: 'd1', received_at: '2026-11-18T07:00:00Z', outcome: 'refused', reason: 'Bad signature', stored: true }] as never);
    const panel = await openHook({ ...hook, received_24h: 5, refused_24h: 2, hourly: 60, keep_days: 7, unheard: 'log', type_from: '' });
    expect(within(panel).getByText('5 calls, 2 refused')).toBeTruthy();
    expect(within(panel).getByText('No')).toBeTruthy();
    const call = within(panel).getByTitle('Bad signature');
    expect(within(call).getByText('Refused')).toBeTruthy();
    expect(within(call).getByRole('button', { name: 'Payload' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    for (const item of ['New secret', 'Signing', 'Remove']) expect(await screen.findByRole('menuitem', { name: item })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Store orders' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ name: 'Store orders' });
  });

  it('opens a kept call’s headers and body @s576', async () => {
    record((url) =>
      url.endsWith('/triggers/h1/deliveries/d1')
        ? ok({ delivery: { received_at: '2026-11-19T07:00:00Z', type: 'order.paid', bytes: 7, expired: false, headers: { 'content-type': 'application/json' }, body: '{"a":1}' } })
        : url.endsWith('/triggers/h1/deliveries')
          ? ok(calls)
          : nothing(),
    );
    await openHook();
    expect(await screen.findByText('Handled')).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Payload' }));
    const dialog = await screen.findByRole('dialog', { name: 'Payload' });
    const body = await within(dialog).findByRole('region', { name: 'Body' });
    expect(body.querySelector('pre')?.textContent).toBe('{\n  "a": 1\n}');
    expect(within(dialog).getByRole('region', { name: 'Headers' }).textContent).toContain('content-type: application/json');
  });

  it('turns signing on and shows the made secret once, never in the cache @s578', async () => {
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
    const panel = await openHook();
    await menu(panel, 'Signing');
    const dialog = await screen.findByRole('dialog', { name: 'Signing' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toEqual({ preset: 'hmac' });
    expect(await within(panel).findByText(secret)).toBeTruthy();
    expect(inCache(secret)).toBe(false);
    expect(window.location.href).not.toContain(secret);
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

  it('shows the new address and a new secret in the panel only, never in the cache or the address', async () => {
    const made = 'https://in.example/hooks/h7/made_up_secret_91c2';
    const rotated = 'https://in.example/hooks/h7/made_up_secret_rotated_4b0e';
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
    show('/company/routines?tab=webhooks');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add webhook' }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'Add webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Shipping' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    const panel = await screen.findByRole('complementary', { name: 'Shipping' });
    expect(await within(panel).findByText(made)).toBeTruthy();
    expect(inCache(made)).toBe(false);
    expect(window.location.href).not.toContain('made_up_secret');

    await menu(panel, 'New secret');
    // the current address stops working: asked first
    const ask = await screen.findByRole('alertdialog', { name: 'Make a new address?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'New secret' }));
    expect(await within(panel).findByText(rotated)).toBeTruthy();
    expect(inCache(rotated)).toBe(false);
    expect(inCache('made_up_secret')).toBe(false);
  });
});

// A board: one rule, Sort over two destinations.
const DESTS = [
  { id: 'now', name: 'Now', then: [] },
  { id: 'later', name: 'Later', then: [{ do: 'archive', args: {} }] },
];
const SORT = [
  { id: 'd1', type: 'decide', enabled: true, out: 'd1', questions: { to: { type: 'choice', instructions: 'Pick where this message goes.', criteria: { now: 'Now', later: 'Later' } } } },
  {
    id: 'f2',
    type: 'filter',
    enabled: true,
    rules: [
      { when: [{ field: 'd1.to', op: 'is', value: 'now' }], match: 'all', then: [], to: 'now', next: 'end' },
      { when: [{ field: 'd1.to', op: 'is', value: 'later' }], match: 'all', then: [], to: 'later', next: 'end' },
      { when: [], match: 'all', then: [], to: 'later', next: 'end' },
    ],
  },
];
const RULES = {
  id: 'f1',
  type: 'filter',
  enabled: true,
  rules: [{ when: [{ field: 'message.from', op: 'contains', value: '@shop.example' }], match: 'all', then: [{ do: 'label', args: { name: 'Receipts' } }], to: 'later', next: 'end' }],
};
const INBOX = { type: 'message', inbox: 'ada@company.example' };
const boardRoutine = () => routine({ id: 'r9', name: 'Ada inbox', owner_id: 'human:me', trigger: INBOX, steps: [RULES, ...SORT], destinations: DESTS } as unknown as Partial<Routine>);

describe('The inbox board', () => {
  it('shows its parts and saves a rule from its editor @s527 @s568', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r9') ? ok({ ...boardRoutine() }) : nothing()));
    queryClient.setQueryData(routinesQuery.queryKey, [boardRoutine()]);
    show('/company/routines?panel=routine:r9');
    const panel = await screen.findByRole('complementary', { name: 'Ada inbox' });
    expect(within(panel).getByText('Label Receipts · → Later')).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Sort' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'message.from contains @shop.example' }));
    const editor = await screen.findByRole('complementary', { name: 'Rule' });
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Condition 1 value' }), { target: { value: '@store.example' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0]!.body as { steps: { rules?: { when: { value: string }[]; to?: string; then: unknown[] }[] }[]; destinations: unknown[] };
    expect(body.steps[0]!.rules![0]).toMatchObject({ when: [{ field: 'message.from', op: 'contains', value: '@store.example' }], to: 'later', then: [{ do: 'label', args: { name: 'Receipts' } }] });
    expect(body.destinations).toEqual(DESTS);
    expect(await screen.findByRole('complementary', { name: 'Ada inbox' })).toBeTruthy();
  });

  it('has no Edit: a board is edited a part at a time', async () => {
    record();
    queryClient.setQueryData(routinesQuery.queryKey, [boardRoutine(), routine()]);
    const router = show('/company/routines?panel=routine:r9');
    const panel = await screen.findByRole('complementary', { name: 'Ada inbox' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Versions' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Edit' })).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    await router.navigate({ to: '/company/routines', search: { panel: 'routine:r1' } as never });
    const other = await screen.findByRole('complementary', { name: 'Morning numbers' });
    fireEvent.click(within(other).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeTruthy();
  });

  it('keeps one destination at least', async () => {
    record();
    const one = { ...boardRoutine(), destinations: DESTS.slice(0, 1), steps: [{ id: 'f2', type: 'filter', enabled: true, rules: [{ when: [], match: 'all', then: [], to: 'now', next: 'end' }] }] } as unknown as Routine;
    queryClient.setQueryData(routinesQuery.queryKey, [one]);
    show('/company/routines?panel=routine:r9');
    await screen.findByRole('complementary', { name: 'Ada inbox' });
    const r = await run('routines.removeBoardItem', { routine: 'r9', part: 'destination', at: 0 }, { via: 'click' });
    expect(r).toMatchObject({ ok: false });
    expect(r.error).toContain('A board keeps one destination at least');
  });
});

describe('Inbox templates', () => {
  const templates = { templates: [{ id: 'triage', name: 'Triage', destinations: ['Now', 'Later'] }] };
  const filled = { name: 'Triage', owner: 'person:me', enabled: true, trigger: INBOX, steps: SORT, destinations: DESTS };
  const newInbox = async () => {
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show('/company/routines');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New inbox' }));
    return screen.findByRole('dialog', { name: 'New inbox' });
  };

  it('adds a new inbox from a template for me @s569', async () => {
    const sent = record((url, method) =>
      url.endsWith('/automations/templates')
        ? ok(templates)
        : url.includes('/automations/templates/triage?')
          ? ok(filled)
          : method === 'POST' && url.endsWith('/api/v2/automations')
            ? ok({ ...boardRoutine(), id: 'r20', name: 'Triage' })
            : nothing(),
    );
    const dialog = await newInbox();
    expect((within(dialog).getByRole('textbox', { name: 'Inbox' }) as HTMLInputElement).value).toBe('ada@company.example');
    expect(await within(dialog).findByText('Now · Later')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ name: 'Triage', owner: 'person:me', enabled: true, builtin: false, steps: SORT, destinations: DESTS });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New inbox' })).toBeNull());
  });

  it('stays open when the owner cannot hear that inbox', async () => {
    const sent = record((url) =>
      url.endsWith('/automations/templates')
        ? ok(templates)
        : url.includes('/automations/templates/triage?')
          ? { status: 403, body: { error: { code: 'forbidden', detail: 'That owner cannot hear this inbox' } } }
          : nothing(),
    );
    const dialog = await newInbox();
    await within(dialog).findByText('Now · Later');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(false));
    expect(sent).toHaveLength(0);
    expect(screen.getByRole('dialog', { name: 'New inbox' })).toBeTruthy();
  });

  const older = () => routine({ id: 'r8', name: 'Old inbox', owner_id: 'human:me', trigger: INBOX, steps: [{ id: 'l1', type: 'llm', enabled: true, prompt: 'Sort my mail.' }] } as unknown as Partial<Routine>);
  const applyDialog = async () => {
    queryClient.setQueryData(routinesQuery.queryKey, [older()]);
    queryClient.setQueryData(templatesQuery.queryKey, templates.templates);
    show('/company/routines?panel=routine:r8');
    await menu(await screen.findByRole('complementary', { name: 'Old inbox' }), 'Apply template');
    return screen.findByRole('dialog', { name: 'Apply a template' });
  };

  it('moves an older inbox onto a template, keeping its instructions @s570', async () => {
    const sent = record((url, method) =>
      url.includes('/automations/templates/triage?') ? ok(filled) : method === 'PATCH' && url.endsWith('/automations/r8') ? ok(older()) : nothing(),
    );
    const dialog = await applyDialog();
    expect(await within(dialog).findByText('Archive')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0]!.body as { summary: string; steps: { type: string; prompt?: string; choose?: { mode: string } }[] };
    expect(body.summary).toBe('Applied a template');
    expect(body.steps.at(-1)).toMatchObject({ type: 'llm', prompt: 'Sort my mail.', choose: { mode: 'draft' } });
  });

  it("says so when a template doesn't fit", async () => {
    const sent = record((url) => (url.includes('/automations/templates/triage?') ? ok({ ...filled, destinations: [] }) : nothing()));
    const dialog = await applyDialog();
    expect(await within(dialog).findByText("This template doesn't fit this inbox")).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Apply' }).hasAttribute('disabled')).toBe(true);
    expect(sent).toHaveLength(0);
  });
});
