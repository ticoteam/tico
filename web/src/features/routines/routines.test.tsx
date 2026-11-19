import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { ListeningPage } from './ListeningPage';
import { validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
import { routinesQuery, stageRulesQuery, watchesQuery, type StageRules } from './queries';
import { runLink } from './RoutinePanel';
import { RoutinesPage } from './RoutinesPage';
import { RulesPage } from './RulesPage';
import type { Routine, StageRule } from './types';
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
