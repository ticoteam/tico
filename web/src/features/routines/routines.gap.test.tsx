import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { validateRoutinesSearch } from './load';
import { routinesQuery, systemJobsQuery } from './queries';
import { RoutinesPage } from './RoutinesPage';
import type { Routine, SystemJob } from './types';

/* The gaps: System jobs' schedule, next run and Last runs (s513); search and filter on the Routines list (s509). */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
function record(reply: (url: string, method: string) => Reply): Sent[] {
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
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
const pick = async (name: string | RegExp, option: string | RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};

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
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const PRESETS: SystemJob['presets'] = [
  { key: 'hourly', label: 'Hourly' },
  { key: '4h', label: 'Every 4 hours' },
  { key: 'daily', label: 'Daily at a time' },
];

describe('System jobs @s513', () => {
  it('picks a preset and a daily time, and shows the next run and Last runs @s513', async () => {
    const job: SystemJob = { key: 'kpi_asks', name: 'KPI asks', on: true, can_change: true, switch: true, presets: PRESETS, preset: 'hourly', at: '09:00', next: '2026-11-19T10:00:00Z' };
    const sweep: SystemJob = { key: 'sweep', name: 'Sweep', what: 'Clears old files', on: true, switch: false, last: { started_at: '2026-11-19T07:00:00Z', finished_at: '2026-11-19T07:00:03Z', ok: true } };
    const mail: SystemJob = { key: 'mail', name: 'Mail sync', on: true, switch: false, last: { started_at: '2026-11-19T08:00:00Z', ok: false } };
    const sent = record((url, method) =>
      method === 'PUT' && url.includes('/system-routines/kpi_asks') ? ok({ job: { ...job, preset: 'daily', at: '09:00', schedule: 'Daily at 09:00' } }) : nothing(),
    );
    queryClient.setQueryData(routinesQuery.queryKey, []);
    queryClient.setQueryData(systemJobsQuery.queryKey, [job, sweep, mail]);
    show('/company/routines?panel=builtin:system');
    const p = await screen.findByRole('complementary', { name: 'System jobs' });
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

  it('a job the person may not change shows its schedule as words @s513', async () => {
    record(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, []);
    queryClient.setQueryData(systemJobsQuery.queryKey, [
      { key: 'kpi_asks', name: 'KPI asks', schedule: 'Hourly', on: true, can_change: false, switch: true, presets: PRESETS, preset: 'hourly' },
    ] satisfies SystemJob[]);
    show('/company/routines?panel=builtin:system');
    const p = await screen.findByRole('complementary', { name: 'System jobs' });
    expect(within(p).queryByRole('combobox', { name: 'KPI asks schedule' })).toBeNull();
    expect(within(p).getByText('Hourly')).toBeTruthy();
  });
});

const routine = (id: string, name: string, over: Partial<Routine> = {}) =>
  ({ id, name, enabled: true, paused: false, owner_id: 'bot:growth', trigger: { type: 'schedule', cron: '0 7 * * *', tz: 'UTC' }, steps: [], ...over }) as unknown as Routine;

describe('Search and filter the routines @s509', () => {
  it('counts a built-in routine as a search match', async () => {
    record(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, [routine('r1', 'Old cleanup')]);
    queryClient.setQueryData(systemJobsQuery.queryKey, []);
    show('/company/routines?q=System%20jobs');
    expect(await screen.findByRole('link', { name: /System jobs/ })).toBeTruthy();
    expect(screen.queryByText('No matches')).toBeNull();
  });

  it('keeps the search and state filter when opening and closing a routine', async () => {
    record(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, [
      routine('r1', 'Old cleanup', { enabled: false }),
      routine('r2', 'Morning numbers'),
    ]);
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
    record(nothing);
    queryClient.setQueryData(routinesQuery.queryKey, [
      routine('r1', 'Morning numbers'),
      routine('r2', 'Weekly invoices', { last_run: { status: 'failed' } } as Partial<Routine>),
      routine('r3', 'Old cleanup', { enabled: false }),
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

  it('reads a filter from the address and drops one it does not know @s509', () => {
    expect(validateRoutinesSearch({ q: 'numbers', state: 'failed' })).toEqual({ q: 'numbers', state: 'failed' });
    expect(validateRoutinesSearch({ state: 'nope' })).toEqual({});
    expect(validateRoutinesSearch({ tab: 'runs', q: 'x' })).toEqual({ tab: 'runs' });
  });
});
