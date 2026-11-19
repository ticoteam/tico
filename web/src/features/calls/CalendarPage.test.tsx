import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, type AnyRouter } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CalendarEvents, CalendarOccurrence } from '@/api/schema';
import { ActionsRoot, run } from '@/actions';
import './actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { CalendarPage } from './CalendarPage';
import { calendarSearch, parseDay, rangeOf } from './model';
import { eventsQuery, liveCallsQuery, meetingsQuery } from './queries';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => null,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const DAY = '2026-11-18';
const at = (h: number, m = 0) => new Date((parseDay(DAY) as Date).getTime() + (h * 60 + m) * 60_000).toISOString();
const occ = (p: Partial<CalendarOccurrence>): CalendarOccurrence => ({
  all_day: false,
  attendees: [],
  busy_only: false,
  calendar: { id: 'c1', name: 'Work', ref: 'cal:1', type: 'tico' },
  can: { edit: true, join: false },
  end: at(11),
  event_id: null,
  home: 'tico',
  kind: 'event',
  links: { attempt: null, automation: null, meeting: null, task: null, thread: null },
  meeting_url: null,
  occurrence_id: 'o1',
  open_url: null,
  recurring: false,
  response: null,
  start: at(10),
  status: 'confirmed',
  time_zone: null,
  title: 'Pricing review',
  ...p,
});
const events = (list: CalendarOccurrence[]): CalendarEvents => ({ calendars: [], errors: [], from: '', to: '', month_counts: [], occurrences: list });

function seed(view: 'day' | '3day' | 'week' | 'month', list: CalendarOccurrence[]) {
  const r = rangeOf(view, parseDay(DAY) as Date);
  queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, events(list));
}

function show(path: string): AnyRouter {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const cal = createRoute({ getParentRoute: () => root, path: '/calendar', validateSearch: calendarSearch, component: CalendarPage });
  const router = createRouter({ routeTree: root.addChildren([cal]), history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router as unknown as AnyRouter;
}

const fetches: string[] = [];
let refuse: () => void = () => {};
beforeEach(() => {
  queryClient.clear();
  fetches.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [], count: 0, pending_count: 0 });
  // Anything not seeded answers empty; nothing leaves the test.
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    fetches.push(String(url));
    if (init?.method === 'DELETE') {
      await new Promise<void>((r) => (refuse = r));
      return new Response(JSON.stringify({ error: { code: 'forbidden', detail: 'Only the organizer can remove it' } }), { status: 403, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify(events([])), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

const block = (title: string) =>
  screen.getAllByTitle(new RegExp(`^${title},`)).find((el) => el.classList.contains('ev') || el.classList.contains('mi')) as HTMLElement;

describe('Calendar', () => {
  it.each([true, false])('still picks people or Just Tico when the server reports Tico Meet available=%s', async (available) => {
    queryClient.setQueryData(['config'], { features: { tico_meet: available } });
    queryClient.setQueryData(['directory'], { actors: [{ id: 'human:ana', name: 'Ana', kind: 'person' }], byId: {} });
    seed('week', [occ({})]);
    show(`/calendar?view=week&date=${DAY}`);
    fireEvent.click(await screen.findByRole('button', { name: 'Start call' }));
    const picker = within(await screen.findByRole('dialog', { name: 'Start call' }));
    expect(picker.getByRole('checkbox', { name: 'Just Tico', checked: true })).toBeTruthy();
    fireEvent.click(picker.getByRole('checkbox', { name: 'Ana' }));
    expect(picker.getByRole('checkbox', { name: 'Ana', checked: true })).toBeTruthy();
    expect((picker.getByRole('button', { name: 'Start call' }) as HTMLButtonElement).disabled).toBe(!available);
    fireEvent.click(picker.getByRole('checkbox', { name: 'Just Tico', checked: false }));
    expect(picker.getByRole('checkbox', { name: 'Ana', checked: false })).toBeTruthy();
    expect(picker.getByRole('checkbox', { name: 'Just Tico', checked: true })).toBeTruthy();
    const go = picker.getByRole('button', { name: 'Call Tico' }) as HTMLButtonElement;
    expect(go.disabled).toBe(!available);
    if (!available) {
      expect(picker.getByText('Tico Meet is not set up on this server')).toBeTruthy();
      fireEvent.click(go);
      expect(fetches).not.toContain('/api/v2/assistant');
      expect(picker.getByRole('checkbox', { name: 'Just Tico', checked: true })).toBeTruthy();
    }
  });

  it('draws a week with its range, and an event links to its panel @s971 @s978', async () => {
    seed('week', [occ({})]);
    show(`/calendar?view=week&date=${DAY}`);
    await screen.findByText('Nov 15 to Nov 21');
    const ev = block('Pricing review');
    expect(ev.getAttribute('href')).toContain('panel=event%3Ao1');
    expect(ev.getAttribute('href')).toContain(`date=${DAY}`);
    expect(screen.getByLabelText('Next week').getAttribute('href')).toContain('date=2026-11-25');
  });

  it('opens the event beside the grid and closes it again @s983', async () => {
    seed('week', [occ({ recurring: true })]);
    const router = show(`/calendar?view=week&date=${DAY}`);
    fireEvent.click(await waitFor(() => block('Pricing review')));
    const panel = await screen.findByRole('complementary', { name: 'Pricing review' });
    expect(within(panel).getByText('Wed, Nov 18')).toBeTruthy();
    expect(within(panel).getByText('10:00 AM to 11:00 AM')).toBeTruthy();
    expect(router.state.location.search).toMatchObject({ panel: 'event:o1', view: 'week' });
    fireEvent.click(within(panel).getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Pricing review' })).toBeNull());
    expect(router.state.location.search).not.toHaveProperty('panel');
  });

  it('switches views through the address @s979', async () => {
    seed('week', [occ({})]);
    seed('month', [occ({})]);
    const router = show(`/calendar?view=week&date=${DAY}`);
    await screen.findByText('Nov 15 to Nov 21');
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    await screen.findByText('November 2026');
    expect(router.state.location.search).toMatchObject({ view: 'month', date: DAY });
    expect(block('Pricing review').classList.contains('mi')).toBe(true);
  });

  it('3 days stays an address, off the switch (s977 is not drawn): three columns, stepping by three', async () => {
    seed('3day', [occ({})]);
    const router = show(`/calendar?view=3day&date=${DAY}`);
    await screen.findByText('Nov 18 to Nov 20');
    expect(screen.queryByRole('button', { name: '3 days' })).toBeNull();
    expect([...document.querySelectorAll('.cv-dh')].map((h) => h.textContent)).toEqual(['Wed18', 'Thu19', 'Fri20']);
    const next = screen.getByLabelText('Next 3 days');
    expect(next.getAttribute('href')).toContain('date=2026-11-21');
    fireEvent.click(next);
    await screen.findByText('Nov 21 to Nov 23');
    expect(router.state.location.search).toMatchObject({ view: '3day', date: '2026-11-21' });
  });

  it('Week keeps the ⋯: Log a meeting and Calendar settings', async () => {
    seed('week', [occ({})]);
    show(`/calendar?view=week&date=${DAY}`);
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Log a meeting' })).toBeTruthy();
  });

  it('Tico can pick 3 days', async () => {
    seed('week', [occ({})]);
    seed('3day', [occ({})]);
    const router = show(`/calendar?view=week&date=${DAY}`);
    await screen.findByText('Nov 15 to Nov 21');
    expect(await run('calls.show', { view: '3day' }, { via: 'tico' })).toMatchObject({ ok: true });
    await screen.findByText('Nov 18 to Nov 20');
    expect(router.state.location.search).toMatchObject({ view: '3day', date: DAY });
  });

  it('says when a day has nothing on it', async () => {
    seed('day', []);
    show(`/calendar?view=day&date=${DAY}`);
    expect(await screen.findByText('Nothing this day')).toBeTruthy();
  });

  it('asks the server for exactly the range on screen', async () => {
    show(`/calendar?view=day&date=${DAY}`);
    await waitFor(() => expect(fetches.some((u) => u.includes('/api/v2/calendar/events'))).toBe(true));
    const r = rangeOf('day', parseDay(DAY) as Date);
    const u = new URL(fetches.find((x) => x.includes('/calendar/events')) as string, 'http://x');
    expect(u.searchParams.get('from')).toBe(r.from.toISOString());
    expect(u.searchParams.get('to')).toBe(r.to.toISOString());
  });

  it('List shows past meetings; its filter and search go to the server through the address @s980 @s973', async () => {
    queryClient.setQueryData(meetingsQuery().queryKey, {
      meetings: [{ id: 'mt1', title: 'Weekly sync', review_state: 'live', started: at(9) }],
      count: 1,
      pending_count: 0,
    });
    const router = show('/calendar?view=list');
    expect(await screen.findByText('Weekly sync')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pending' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ review: 'pending' }));
    await waitFor(() => expect(fetches.some((u) => u.endsWith('/api/v2/meetings?review=pending'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Find a meeting' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Find a meeting' }), { target: { value: 'sync' } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'sync', review: 'pending' }));
    await waitFor(() => expect(fetches.some((u) => u.endsWith('/api/v2/meetings?review=pending&q=sync'))).toBe(true));
    expect(fetches.some((u) => u.endsWith('/api/v2/calendar/search?q=sync'))).toBe(true);
  });

  it('Shared with me reads review=shared, and says when nothing is shared', async () => {
    queryClient.setQueryData(meetingsQuery('shared').queryKey, { meetings: [], count: 0, pending_count: 0 });
    const router = show('/calendar?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Shared with me' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ review: 'shared' }));
    expect(await screen.findByText('Nothing shared with you')).toBeTruthy();
    queryClient.setQueryData(meetingsQuery('shared').queryKey, {
      meetings: [{ id: 'mt2', title: 'Garden plan', review_state: 'live', started: at(9) }],
      count: 1,
      pending_count: 0,
    });
    expect(await screen.findByText('Garden plan')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Shared with me/ })).toBeTruthy();
  });

  it('a removed event goes at once and comes back when the server refuses', async () => {
    seed('week', [occ({})]);
    show(`/calendar?view=week&date=${DAY}&panel=event%3Ao1`);
    const panel = await screen.findByRole('complementary', { name: 'Pricing review' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    const ask = await screen.findByRole('alertdialog');
    fireEvent.click(within(ask).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.queryAllByTitle(/^Pricing review,/)).toHaveLength(0));
    refuse();
    await waitFor(() => expect(block('Pricing review')).toBeTruthy());
    const reason = await screen.findByText('Only the organizer can remove it');
    expect(reason.parentElement?.textContent).toBe("Couldn't save: Only the organizer can remove it");
  });
});

describe('Calendar on a phone', () => {
  beforeEach(() => {
    document.documentElement.dataset.layout = 'cards';
  });
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('keeps New event filled in the head, the views and the range under it; Start call is in the ⋯', async () => {
    queryClient.setQueryData(['config'], { features: { tico_meet: false } });
    seed('week', [occ({})]);
    seed('month', [occ({})]);
    const router = show(`/calendar?view=week&date=${DAY}`);
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    expect(within(head).getByRole('button', { name: 'New event' }).hasAttribute('data-phone')).toBe(true);
    expect(within(head).queryByRole('button', { name: 'Start call' })).toBeNull();
    expect(screen.getByText('Nov 15 to Nov 21').closest('.cv-rangerow')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ view: 'month' }));
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Start call' }));
    const picker = within(await screen.findByRole('dialog', { name: 'Start call' }));
    expect(picker.getByText('Tico Meet is not set up on this server')).toBeTruthy();
    expect((picker.getByRole('button', { name: 'Call Tico' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Week is a strip of days with the chosen day under it', async () => {
    seed('week', [occ({}), occ({ occurrence_id: 'o2', title: 'Garden walk', start: at(34), end: at(35) })]);
    const router = show(`/calendar?view=week&date=${DAY}`);
    const strip = (await screen.findByLabelText('Thu, Nov 19')).closest('.cv-strip') as HTMLElement;
    expect(strip.querySelector('.cv-sd.is-on')?.getAttribute('aria-label')).toBe('Wed, Nov 18');
    const agenda = document.querySelector('.pk-cal-agenda') as HTMLElement;
    expect(within(agenda).getByText('Pricing review')).toBeTruthy();
    expect(within(agenda).queryByText('Garden walk')).toBeNull();
    fireEvent.click(within(strip).getByLabelText('Thu, Nov 19'));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ date: '2026-11-19' }));
    expect(await within(agenda).findByText('Garden walk')).toBeTruthy();
  });

  it('draws 3 days as the grid, where Week shows the agenda', async () => {
    seed('3day', [occ({})]);
    show(`/calendar?view=3day&date=${DAY}`);
    await waitFor(() => expect(document.querySelectorAll('.cv-dh')).toHaveLength(3));
    expect(document.querySelector('.pk-cal')?.classList.contains('has-agenda')).toBe(false);
    expect(screen.getByText('Nov 18 to Nov 20')).toBeTruthy();
  });

  it('puts List’s filter under the ⋯', async () => {
    show('/calendar?view=list');
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Filter' }));
    expect(await screen.findByRole('menuitem', { name: 'Shared with me' })).toBeTruthy();
  });

  it("List's search sits under the head, and stays while it searches", async () => {
    const router = show('/calendar?view=list');
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    expect(within(head).queryByRole('textbox')).toBeNull();
    const box = screen.getByRole('textbox', { name: 'Find a meeting' });
    fireEvent.change(box, { target: { value: 'sync' } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'sync' }));
    expect(screen.getByRole('textbox', { name: 'Find a meeting' })).toBe(box);
  });
});
