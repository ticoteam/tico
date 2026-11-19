import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CalendarEvents, CalendarOccurrence } from '@/api/schema';
import { ActionsRoot, run } from '@/actions';
import './actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { CalendarPage } from './CalendarPage';
import { calendarSearch, parseDay, rangeOf } from './model';
import { eventsQuery, itemsQuery, liveCallsQuery, meetingQuery, meetingsQuery, type Meeting } from './queries';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => null,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const DAY = '2026-11-18';
const at = (h: number) => new Date((parseDay(DAY) as Date).getTime() + h * 3_600_000).toISOString();
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
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function seed(view: 'day' | 'week', list: CalendarOccurrence[]) {
  const r = rangeOf(view, parseDay(DAY) as Date);
  queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, events(list));
}

function show(path: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const cal = createRoute({ getParentRoute: () => root, path: '/calendar', validateSearch: calendarSearch, component: CalendarPage });
  const routines = createRoute({ getParentRoute: () => root, path: '/company/routines', component: () => <p>Routines</p> });
  const router = createRouter({ routeTree: root.addChildren([cal, routines]), history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

// Each test answers the requests it cares about; the rest answer an empty range.
let answer: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined = () => undefined;
const sent: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => undefined;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return (await answer(String(url), init)) ?? json(events([]));
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('Calendar, checked against the old app', () => {
  it('Day: one day of hours, its event from start to end, stepping a day at a time @s976', async () => {
    seed('day', [occ({})]);
    show(`/calendar?view=day&date=${DAY}`);
    const ev = await waitFor(() => screen.getAllByTitle(/^Pricing review,/).find((el) => el.classList.contains('ev')) as HTMLElement);
    expect(ev.getAttribute('href')).toContain('panel=event%3Ao1');
    expect(screen.getByLabelText('Previous day').getAttribute('href')).toContain('date=2026-11-17');
    expect(screen.getByLabelText('Next day').getAttribute('href')).toContain('date=2026-11-19');
  });

  it('shows the loading bars until the range is read, never "Nothing this week"', async () => {
    answer = () => new Promise<Response>(() => {});
    show(`/calendar?view=week&date=${DAY}`);
    expect(await screen.findByRole('status', { name: 'Loading' })).toBeTruthy();
    expect(screen.queryByText('Nothing this week')).toBeNull();
  });

  it("a List the server refuses is the page's fault, with Try again", async () => {
    let fail = true;
    answer = (url) => (url.includes('/api/v2/meetings') ? (fail ? json({ error: { code: 'boom' } }, 500) : json({ meetings: [{ id: 'mt1', title: 'Weekly sync', review_state: 'live', started: at(9) }], count: 1, pending_count: 0 })) : undefined);
    show('/calendar?view=list');
    expect(await screen.findByText('Calendar did not load')).toBeTruthy();
    expect(screen.queryByText('No meetings or calls yet')).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Weekly sync')).toBeTruthy();
  });

  it("a routine's run links to its routine @s983", async () => {
    seed('week', [occ({ kind: 'run', title: 'Morning brief', links: { attempt: null, automation: 'au1', meeting: null, task: null, thread: null } })]);
    show(`/calendar?view=week&date=${DAY}&panel=event%3Ao1`);
    const panel = await screen.findByRole('complementary', { name: 'Morning brief' });
    const link = within(panel).getByRole('link', { name: 'Routine' });
    expect(link.getAttribute('href')).toBe('/company/routines?panel=routine%3Aau1');
  });
});

describe('A meeting beside List', () => {
  const meeting = (p: Partial<Meeting>): Meeting => ({ id: 'mt1', title: 'Weekly sync', review_state: 'pending', started: at(9), ...p });
  beforeEach(() => {
    queryClient.setQueryData(itemsQuery('mt1').queryKey, { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } });
    queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [], count: 0, pending_count: 1 });
  });

  it('a pending meeting is the person’s alone until Share sends it to the company @s1012', async () => {
    queryClient.setQueryData(meetingQuery('mt1').queryKey, meeting({}));
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST'
        ? json(meeting({ review_state: 'live' }))
        : url.endsWith('/api/v2/meetings/mt1/items')
          ? json({ id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } })
          : url.includes('/api/v2/meetings')
            ? json(url.endsWith('/mt1') ? meeting({ review_state: 'live' }) : { meetings: [], count: 0, pending_count: 0 })
            : undefined;
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByText('Only you see this')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(within(panel).queryByText('Only you see this')).toBeNull());
    expect(sent.find((r) => r.url.endsWith('/meetings/mt1/review'))).toMatchObject({ method: 'POST', body: { action: 'approve' } });
  });

  it('shows what was said, each turn with its speaker and time @s1045', async () => {
    queryClient.setQueryData(
      meetingQuery('mt1').queryKey,
      meeting({
        review_state: 'live',
        turns: [
          { speaker: 'Ana', start_ms: 0, text: 'Prices go up in May.' },
          { speaker: 'Ben', start_ms: 65_000, text: 'Then we tell customers in April.' },
        ],
      }),
    );
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByRole('heading', { name: 'Transcript' })).toBeTruthy();
    expect(within(panel).getByText('Then we tell customers in April.')).toBeTruthy();
    expect(within(panel).getByText('Ben')).toBeTruthy();
    expect(within(panel).getByText('1:05')).toBeTruthy();
  });

  it('a transcript with no turns shows its text a line at a time', async () => {
    queryClient.setQueryData(meetingQuery('mt1').queryKey, meeting({ review_state: 'live', transcript_readable: 'First line\nSecond line' }));
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByText('Second line')).toBeTruthy();
  });
});

describe('What Tico may do in Calendar', () => {
  it('leaves sharing a meeting with the company to the person', async () => {
    show(`/calendar?view=week&date=${DAY}`);
    await screen.findByText('Nov 15 to Nov 21');
    const tico = { via: 'tico' } as const;
    expect(await run('calls.review', { meeting: 'mt1', action: 'approve' }, tico)).toMatchObject({ ok: false });
    expect(sent.filter((r) => r.method !== 'GET')).toEqual([]);
  });

  it('may still put a meeting away and bring agents into a call', async () => {
    answer = (url) => (url.endsWith('/meetings/mt1/review') ? json({ id: 'mt1', title: 'Weekly sync', review_state: 'dismissed' }) : url.includes('/calls/call1') ? json({ id: 'call1' }) : undefined);
    show(`/calendar?view=week&date=${DAY}`);
    await screen.findByText('Nov 15 to Nov 21');
    expect(await run('calls.review', { meeting: 'mt1', action: 'dismiss' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('calls.add', { id: 'call1', with: ['bot:gm'] }, { via: 'tico' })).toMatchObject({ ok: true });
  });
});
