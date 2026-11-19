import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { validateRoutinesSearch } from './load';
import { heartbeatQuery, maintenanceQuery, maintenanceRunsQuery, routinesQuery, runsQuery, systemJobsQuery } from './queries';
import { RoutinesPage } from './RoutinesPage';
import type { Maintenance, Routine, SystemJob } from './types';

/* One routine's panel (hold, limit, runs) and the built-in Maintenance, Heartbeat and System jobs. */

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
const panel = () => screen.findByRole('complementary', { name: 'Morning numbers' });
const openRoutine = (over: Partial<Routine> = {}) => {
  queryClient.setQueryData(routinesQuery.queryKey, [routine(over)]);
  show('/company/routines?panel=routine:r1');
};

describe('One routine', () => {
  it('shows what starts it, what it does, its limit and instruction; Test run needs edit rights @s510', async () => {
    record(nothing);
    openRoutine({ can_edit: false });
    const p = await panel();
    expect(within(p).getByText('Trigger')).toBeTruthy();
    expect(within(p).getByText('Every day at 7 AM')).toBeTruthy();
    expect(within(p).getByText('Does')).toBeTruthy();
    expect(within(p).getByText('Limit')).toBeTruthy();
    expect(within(p).getByText('Post the numbers.')).toBeTruthy();
    fireEvent.click(within(p).getByRole('button', { name: 'More' }));
    expect((await screen.findByRole('menuitem', { name: 'Test run' })).getAttribute('aria-disabled')).toBe('true');
  });

  it('says it is on hold, releases its held runs, and keeps Drop from Tico @s510', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/automations/r1/held') ? ok(routine()) : nothing()));
    openRoutine({ hold: { held: 3, dropped: 0, rule: 'flood', since: '2026-11-01T00:00:00Z', task: null } } as unknown as Partial<Routine>);
    const p = await panel();
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
    openRoutine();
    const p = await panel();
    expect(within(p).getByText('Last runs')).toBeTruthy();
    expect(within(p).getByText('Ran out of time')).toBeTruthy();
    fireEvent.click(within(p).getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.url).toContain('/api/v2/automations/r1/run');
  });

  it('sets its spend limit in dollars each 30 minutes, which Tico may not @s565', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/r1') ? ok(routine({ spend_cap: { per_30m: 2.5 } })) : nothing()));
    openRoutine();
    const p = await panel();
    fireEvent.click(within(p).getByRole('button', { name: 'Change' }));
    fireEvent.change(within(p).getByLabelText('Dollars each 30 minutes'), { target: { value: '2.5' } });
    fireEvent.click(within(p).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PATCH', body: { spend_cap: { per_30m: 2.5 } } });
    expect(await run('routines.setLimit', { routine: 'r1', per30m: 100 }, { via: 'tico' })).toMatchObject({ ok: false });
  });
});

describe('Built in', () => {
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
    queryClient.setQueryData(routinesQuery.queryKey, []);
    queryClient.setQueryData(maintenanceQuery.queryKey, m);
    queryClient.setQueryData(maintenanceRunsQuery.queryKey, []);
    show('/company/routines?panel=builtin:maintenance');
    const p = await screen.findByRole('complementary', { name: 'Maintenance' });
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
    queryClient.setQueryData(routinesQuery.queryKey, []);
    queryClient.setQueryData(heartbeatQuery.queryKey, {
      interval_s: 15,
      interval_min: 5,
      interval_max: 60,
      can_change: true,
      last_beat_at: null,
      steps: [{ key: 'wake', name: 'Wake agents', on: true, switch: false, every: 'Every beat', can_change: true, last_ms: 40 }],
      beats: [{ id: 'b1', started_at: '2026-11-18T07:00:00Z', did: 2, failed: 0, total_ms: 120 }],
    } as never);
    show('/company/routines?panel=builtin:heartbeat');
    const p = await screen.findByRole('complementary', { name: 'Heartbeat' });
    expect(within(p).getByText('Wake agents')).toBeTruthy();
    expect(within(p).getByText('2 done')).toBeTruthy();
    await pick('Runs', 'Every 30 seconds');
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'PUT', body: { interval_s: 30 } });
  });

  it('System jobs: switches one off, runs one now, and shows when a switch-less one last ran @s513', async () => {
    const sent = record((url, method) => (method !== 'GET' && url.includes('/system-routines/') ? ok({}) : nothing()));
    const jobs: SystemJob[] = [
      { key: 'digest', name: 'Daily digest', schedule: 'Daily', on: true, can_change: true, controllable: true },
      { key: 'sweep', name: 'Sweep', what: 'Clears old files', on: true, switch: false, last: { started_at: '2026-11-18T07:00:00Z', ok: true } },
    ];
    queryClient.setQueryData(routinesQuery.queryKey, []);
    queryClient.setQueryData(systemJobsQuery.queryKey, jobs);
    show('/company/routines?panel=builtin:system');
    const p = await screen.findByRole('complementary', { name: 'System jobs' });
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
});
