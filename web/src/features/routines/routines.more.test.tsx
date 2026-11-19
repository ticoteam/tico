import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { validateRoutinesSearch } from './load';
import { endpointsQuery, eventTypesQuery, hooksQuery, routinesQuery, templatesQuery, type Endpoint } from './queries';
import { RoutinesPage } from './RoutinesPage';
import type { Routine } from './types';

/* Runs across routines, the inbox board's editor, inbox templates and outgoing webhooks. */

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
/** Answers every call with `reply`, keeping each write the page sends. */
function record(reply: (url: string, method: string, n: number) => Reply | Promise<Reply>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = await reply(String(url), method, sent.length);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const ok = (body: unknown): Reply => ({ status: 200, body });
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
const refused = (code: string, detail: string, extra: Record<string, unknown> = {}, status = 403): Reply => ({ status, body: { error: { code, detail, ...extra } } });
const writes = (sent: Sent[]) => sent.filter((s) => s.method !== 'GET');

/** The page under a route at its own address, reading its search from the router, with the action layer bound. */
function show(at: string, node?: ReactNode) {
  const Live = () => <RoutinesPage search={useSearch({ strict: false })} />;
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: '/company/routines',
    validateSearch: validateRoutinesSearch,
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
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.closest('a'))?.getAttribute('href') ?? '');
const inCache = (text: string) => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data])).includes(text);

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
    expect(screen.getByRole('region', { name: 'Last 24 hours' })).toBeTruthy();
  });

  it('filters by status in the address and says when there are none', async () => {
    const asked: string[] = [];
    record((url) => {
      if (!url.includes('/automations/runs?')) return nothing();
      asked.push(url);
      return ok(url.includes('status=failed') ? { ...runs, runs: [] } : runs);
    });
    const router = show('/company/routines?tab=runs');
    await screen.findByTitle('Morning numbers');
    fireEvent.click(screen.getByRole('button', { name: 'Filter runs' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Failed' }));
    expect(await screen.findByText('No failed runs')).toBeTruthy();
    expect(router.state.location.search).toMatchObject({ tab: 'runs', status: 'failed' });
    expect(asked.some((u) => u.includes('status=failed') && u.includes('range=24h'))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByTitle('Morning numbers')).toBeTruthy();
    expect(router.state.location.search).toEqual({ tab: 'runs' });
  });

  it('shows the fault with Try again', async () => {
    record((url) => (url.includes('/automations/runs?') ? { status: 500, body: { error: { code: 'boom', detail: 'boom' } } } : nothing()));
    show('/company/routines?tab=runs');
    expect(await screen.findByText('Routines did not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('keeps only known statuses and ranges in the address', () => {
    expect(validateRoutinesSearch({ tab: 'runs', status: 'failed', range: '7d' })).toEqual({ tab: 'runs', status: 'failed', range: '7d' });
    expect(validateRoutinesSearch({ tab: 'runs', status: 'lost', range: '1y' })).toEqual({ tab: 'runs' });
    expect(validateRoutinesSearch({ tab: 'webhooks', status: 'failed' })).toEqual({ tab: 'webhooks' });
  });
});

describe('The inbox board', () => {
  it('shows its parts and saves a rule from its editor', async () => {
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
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    const body = writes(sent)[0]!.body as { steps: { rules?: { when: { value: string }[]; to?: string; then: unknown[] }[] }[]; destinations: unknown[] };
    expect(body.steps[0]!.rules![0]).toMatchObject({ when: [{ field: 'message.from', op: 'contains', value: '@store.example' }], to: 'later', then: [{ do: 'label', args: { name: 'Receipts' } }] });
    expect(body.destinations).toEqual(DESTS);
    expect(await screen.findByRole('complementary', { name: 'Ada inbox' })).toBeTruthy();
  });

  it('keeps one destination at least', async () => {
    record(nothing);
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

  it('adds a new inbox from a template for me', async () => {
    const sent = record((url, method) =>
      url.endsWith('/automations/templates')
        ? ok(templates)
        : url.includes('/automations/templates/triage?')
          ? ok(filled)
          : method === 'POST' && url.endsWith('/api/v2/automations')
            ? ok({ ...boardRoutine(), id: 'r20', name: 'Triage' })
            : nothing(),
    );
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show('/company/routines');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New inbox' }));
    const dialog = await screen.findByRole('dialog', { name: 'New inbox' });
    expect((within(dialog).getByRole('textbox', { name: 'Inbox' }) as HTMLInputElement).value).toBe('ada@company.example');
    expect(await within(dialog).findByText('Now · Later')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]!.body).toMatchObject({ name: 'Triage', owner: 'person:me', enabled: true, builtin: false, steps: SORT, destinations: DESTS });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New inbox' })).toBeNull());
  });

  it('stays open when the owner cannot hear that inbox', async () => {
    const sent = record((url) =>
      url.endsWith('/automations/templates')
        ? ok(templates)
        : url.includes('/automations/templates/triage?')
          ? refused('forbidden', 'That owner cannot hear this inbox')
          : nothing(),
    );
    queryClient.setQueryData(routinesQuery.queryKey, []);
    show('/company/routines');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New inbox' }));
    const dialog = await screen.findByRole('dialog', { name: 'New inbox' });
    await within(dialog).findByText('Now · Later');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(false));
    expect(writes(sent)).toHaveLength(0);
    expect(screen.getByRole('dialog', { name: 'New inbox' })).toBeTruthy();
  });

  const older = () => routine({ id: 'r8', name: 'Old inbox', owner_id: 'human:me', trigger: INBOX, steps: [{ id: 'l1', type: 'llm', enabled: true, prompt: 'Sort my mail.' }] } as unknown as Partial<Routine>);
  const applyDialog = async () => {
    queryClient.setQueryData(routinesQuery.queryKey, [older()]);
    queryClient.setQueryData(templatesQuery.queryKey, templates.templates);
    show('/company/routines?panel=routine:r8');
    const panel = await screen.findByRole('complementary', { name: 'Old inbox' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Apply template' }));
    return screen.findByRole('dialog', { name: 'Apply a template' });
  };

  it('moves an older inbox onto a template, keeping its instructions', async () => {
    const sent = record((url, method) =>
      url.includes('/automations/templates/triage?') ? ok(filled) : method === 'PATCH' && url.endsWith('/automations/r8') ? ok(older()) : nothing(),
    );
    const dialog = await applyDialog();
    expect(await within(dialog).findByText('Archive')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    const body = writes(sent)[0]!.body as { summary: string; steps: { type: string; prompt?: string; choose?: { mode: string } }[] };
    expect(body.summary).toBe('Applied a template');
    expect(body.steps.at(-1)).toMatchObject({ type: 'llm', prompt: 'Sort my mail.', choose: { mode: 'draft' } });
  });

  it("says so when a template doesn't fit", async () => {
    const sent = record((url) => (url.includes('/automations/templates/triage?') ? ok({ ...filled, destinations: [] }) : nothing()));
    const dialog = await applyDialog();
    expect(await within(dialog).findByText("This template doesn't fit this inbox")).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Apply' }).hasAttribute('disabled')).toBe(true);
    expect(writes(sent)).toHaveLength(0);
  });
});

describe('Outgoing webhooks', () => {
  const TYPES = [
    { name: 'task.created', label: 'Task created', group: 'task', description: '', example: {}, version: 1 },
    { name: 'task.done', label: 'Task done', group: 'task', description: '', example: {}, version: 1 },
  ];
  const endpoint = (over: Partial<Endpoint> = {}): Endpoint => ({
    id: 'e1',
    name: 'Order feed',
    url: 'https://hooks.example/orders',
    host: 'hooks.example',
    events: ['task.created'],
    enabled: true,
    can_change: true,
    allow_http: false,
    created_at: '2026-11-01T00:00:00Z',
    created_by: 'human:me',
    disabled_at: null,
    disabled_reason: null,
    filter: {},
    headers: [{ name: 'Authorization', secret: true, set: true }],
    last_7_days: { delivered: 12, failed: 0, pending: 0 },
    old_secret_until: null,
    paused_reason: '',
    rate_per_hour: 600,
    requested_by: null,
    revision: 3,
    updated_at: '2026-11-01T00:00:00Z',
    ...over,
  });
  const seed = (list: Endpoint[]) => {
    queryClient.setQueryData(hooksQuery.queryKey, []);
    queryClient.setQueryData(endpointsQuery.queryKey, list);
    queryClient.setQueryData(eventTypesQuery.queryKey, TYPES);
  };
  const lists = (url: string) =>
    url.endsWith('/api/v2/triggers') ? ok({ triggers: [] }) : url.includes('/api/v2/event-types') ? ok({ items: TYPES }) : null;

  it('adds one and shows its signing secret once, in the dialog only', async () => {
    const secret = 'whsec_made_up_55c1';
    const sent = record((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/webhooks')
        ? ok({ webhook: { ...endpoint({ id: 'e2', name: 'Ship feed', headers: [] }), secret, replayed: false } })
        : url.includes('/api/v2/webhooks?')
          ? ok({ items: [] })
          : (lists(url) ?? nothing()),
    );
    seed([]);
    show('/company/routines?tab=webhooks');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New outgoing webhook' }));
    const dialog = await screen.findByRole('dialog', { name: 'New outgoing webhook' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Ship feed' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Address' }), { target: { value: 'https://hooks.example/ship' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    const shown = await screen.findByRole('dialog', { name: 'Signing secret' });
    expect(within(shown).getByText(secret)).toBeTruthy();
    expect(writes(sent)[0]!.body).toEqual({ name: 'Ship feed', url: 'https://hooks.example/ship', events: ['*'], allow_http: false, headers: [] });
    expect(inCache(secret)).toBe(false);
    fireEvent.click(within(shown).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByText(secret)).toBeNull());
  });

  it('asks before sending saved headers to a new address, then keeps them', async () => {
    const moved = endpoint({ url: 'https://other.example/in', host: 'other.example', revision: 4 });
    const sent = record((url, method, n) =>
      method === 'POST' && url.endsWith('/webhooks/e1/update')
        ? n === 1
          ? refused('destination_change', 'Nothing was saved.', { old: { address: 'https://hooks.example/orders' }, new: { address: 'https://other.example/in' }, credentials: ['Authorization'] }, 409)
          : ok({ webhook: moved })
        : url.includes('/api/v2/webhooks?')
          ? ok({ items: [moved] })
          : (lists(url) ?? nothing()),
    );
    seed([endpoint()]);
    show('/company/routines?tab=webhooks');
    fireEvent.click(await screen.findByRole('button', { name: 'Order feed' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit outgoing webhook' });
    expect((within(dialog).getByLabelText('Header 1 value') as HTMLInputElement).placeholder).toBe('Saved');
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Address' }), { target: { value: 'https://other.example/in' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Send saved headers to the new address?' });
    expect(within(ask).getByText('Authorization would go to https://other.example/in instead of https://hooks.example/orders.')).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Send them' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(2));
    expect(writes(sent)[0]!.body).toEqual({
      name: 'Order feed',
      url: 'https://other.example/in',
      events: ['task.created'],
      allow_http: false,
      headers: [{ name: 'Authorization', secret: true }],
      expected_revision: 3,
    });
    expect(writes(sent)[1]!.body).toMatchObject({ credentials: 'keep', url: 'https://other.example/in' });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  });

  it('keeps secrets and new endpoints from Tico', async () => {
    record((url, method) => (method === 'POST' && url.endsWith('/webhooks/e1/update') ? ok({ webhook: endpoint({ name: 'Orders' }) }) : (lists(url) ?? nothing())));
    seed([endpoint()]);
    show('/company/routines?tab=webhooks');
    await screen.findByRole('button', { name: 'Order feed' });
    const base = { name: 'Orders', url: 'https://hooks.example/orders', events: ['task.created'] };
    expect(await run('webhooks.addEndpoint', base, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('webhooks.updateEndpoint', { endpoint: 'e1', ...base, headers: [{ name: 'Authorization', value: 'made-up', secret: true }] }, { via: 'tico' })).toMatchObject({
      ok: false,
      error: 'not_allowed',
    });
    expect(await run('webhooks.updateEndpoint', { endpoint: 'e1', ...base, credentials: 'keep' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    // moving where the events are sent is the person's own
    expect(await run('webhooks.updateEndpoint', { endpoint: 'e1', ...base, url: 'https://other.example/in' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    // a rename that keeps the saved header as it is stays open to Tico
    expect(await run('webhooks.updateEndpoint', { endpoint: 'e1', ...base, headers: [{ name: 'Authorization', secret: true }] }, { via: 'tico' })).toMatchObject({ ok: true });
  });
});
