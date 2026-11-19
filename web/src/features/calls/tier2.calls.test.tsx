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
import { eventsQuery, granolaQuery, importersQuery, itemsQuery, linkedTasksQuery, liveCallsQuery, meetingQuery, meetingSettingsQuery, meetingsQuery, sourcesQuery, type MeetingMore, type Review } from './queries';

/* Tier 2 checks: the 3-day view, List's Shared / Pending / Dismissed filters (the old Meetings tabs), and the ⋯'s
   Log a meeting, Calendar settings, Meeting sources, Connect Granola and the owner's importers. */

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
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const events = (list: CalendarOccurrence[]): CalendarEvents => ({ calendars: [], errors: [], from: '', to: '', month_counts: [], occurrences: list });

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
  const router = createRouter({ routeTree: root.addChildren([cal]), history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

let answer: (url: string, init?: RequestInit) => Response | undefined = () => undefined;
const sent: { url: string; method: string; body: unknown }[] = [];
const writes = () => sent.filter((r) => r.method !== 'GET');
const me = (role: 'person' | 'owner') => queryClient.setQueryData(['me'], { id: 'human:me', email: 'me@example.com', role, slug: null });

beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => undefined;
  me('person');
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  queryClient.setQueryData(granolaQuery.queryKey, { connected: false, needs_signin: false, syncing: false, imported_count: 0, skipped: 0 } as never);
  queryClient.setQueryData(sourcesQuery.queryKey, { sources: [] } as never);
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return answer(String(url), init) ?? json(events([]));
  });
});
afterEach(() => vi.unstubAllGlobals());

const more = async () => {
  const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
  fireEvent.click(within(head).getByRole('button', { name: 'More' }));
};
const pick = async (name: string, option: string | RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};

describe('3 days @s977', () => {
  it('draws three days from the date, says the range, and steps three days at a time @s977', async () => {
    const r = rangeOf('3day', parseDay(DAY) as Date);
    const o: CalendarOccurrence = {
      all_day: false,
      attendees: [],
      busy_only: false,
      calendar: { id: 'c1', name: 'Work', ref: 'cal:1', type: 'tico' },
      can: { edit: true, join: false },
      end: at(59),
      event_id: null,
      home: 'tico',
      kind: 'event',
      links: { attempt: null, automation: null, meeting: null, task: null, thread: null },
      meeting_url: null,
      occurrence_id: 'o1',
      open_url: null,
      recurring: false,
      response: null,
      start: at(58),
      status: 'confirmed',
      time_zone: null,
      title: 'Board prep',
    };
    queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, events([o]));
    const router = show(`/calendar?view=3day&date=${DAY}`);
    expect(await screen.findByText('Nov 18 to Nov 20')).toBeTruthy();
    expect(document.querySelectorAll('.cv-dh')).toHaveLength(3);
    expect(screen.getByText('Board prep')).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Next 3 days' }));
    await waitFor(() => expect((router.state.location.search as { date?: string }).date).toBe('2026-11-21'));
    expect(await screen.findByText('Nov 21 to Nov 23')).toBeTruthy();
    expect(await screen.findByText('Nothing these 3 days')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- the old Meetings tabs, now List's filter */

const meeting = (p: Partial<MeetingMore> = {}): MeetingMore => ({ id: 'mt1', title: 'Weekly sync', review_state: 'live', started: at(-20), can_edit: true, version: 3, private: false, ...p });
function seedList(review: Review, m: MeetingMore) {
  queryClient.setQueryData(meetingsQuery(review).queryKey, { meetings: [m], count: 1, pending_count: m.review_state === 'pending' ? 1 : 0 });
  queryClient.setQueryData(meetingQuery('mt1').queryKey, m);
  queryClient.setQueryData(itemsQuery('mt1').queryKey, { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } });
  queryClient.setQueryData(linkedTasksQuery('mt1').queryKey, { next_cursor: null, tasks: [] });
}

describe('Meetings in List', () => {
  it('Shared: All meetings lists the shared meetings under Past, and one opens seen by the Team @s993', async () => {
    seedList('live', meeting());
    queryClient.setQueryData(meetingsQuery('pending').queryKey, { meetings: [], count: 0, pending_count: 0 });
    const router = show('/calendar?view=list&review=pending');
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'All meetings' }));
    await waitFor(() => expect((router.state.location.search as { review?: string }).review).toBeUndefined());
    expect(await screen.findByRole('button', { name: /Past/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Weekly sync' }));
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByText('Team')).toBeTruthy();
  });

  it('Pending: counts what waits, opens it as only yours, and Share sends approve, which Tico may not @s994', async () => {
    const m = meeting({ review_state: 'pending' });
    seedList('pending', m);
    answer = (url, init) => (url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST' ? json({ ...m, review_state: 'live' }) : undefined);
    show('/calendar?view=list&review=pending');
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    expect((await screen.findByRole('menuitem', { name: /Pending/ })).textContent).toContain('1');
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    fireEvent.click(await screen.findByRole('link', { name: 'Weekly sync' }));
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByText('Only you see this')).toBeTruthy();
    expect(await run('calls.review', { meeting: 'mt1', action: 'approve' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(writes()).toHaveLength(0);
    fireEvent.click(within(panel).getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ method: 'POST', body: { action: 'approve' } })]));
  });

  it('Dismissed: lists what was put away, and Restore brings it back to Pending @s995', async () => {
    const m = meeting({ review_state: 'dismissed' });
    seedList('dismissed', m);
    answer = (url, init) => (url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST' ? json({ ...m, review_state: 'pending' }) : undefined);
    show('/calendar?view=list&review=dismissed');
    expect(await screen.findByRole('button', { name: /Dismissed/ })).toBeTruthy();
    fireEvent.click(await screen.findByRole('link', { name: 'Weekly sync' }));
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: expect.stringMatching(/\/api\/v2\/meetings\/mt1\/review$/), body: { action: 'restore' } })]));
  });
});

/* ---------------------------------------------------------------- the ⋯ on List */

describe("List's ⋯", () => {
  it('Log a meeting needs notes or a transcript, files it shared and opens it @s1013', async () => {
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/import') && init?.method === 'POST'
        ? json({ id: 'mt9' })
        : url.endsWith('/api/v2/meetings/mt9')
          ? json(meeting({ id: 'mt9', title: 'Supplier call' }))
          : undefined;
    const router = show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Log a meeting' }));
    const dialog = await screen.findByRole('dialog', { name: 'Log a meeting' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Title' }), { target: { value: 'Supplier call' } });
    const log = within(dialog).getByRole('button', { name: 'Log meeting' }) as HTMLButtonElement;
    expect(log.disabled).toBe(true);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Notes' }), { target: { value: 'Agreed on new terms.' } });
    fireEvent.click(log);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ url: expect.stringMatching(/\/api\/v2\/meetings\/import$/), body: { title: 'Supplier call', notes: 'Agreed on new terms.', source: 'manual', review: 'live' } });
    await waitFor(() => expect((router.state.location.search as { panel?: string }).panel).toBe('meeting:mt9'));
  });

  it('Calendar settings: holds imports for review; sharing at once is not open to Tico @s1014', async () => {
    queryClient.setQueryData(meetingSettingsQuery.queryKey, { auto_share: null, review_default: 'review' } as never);
    answer = (url, init) => (url.endsWith('/api/v2/meetings/settings') && init?.method === 'POST' ? json({ auto_share: false, review_default: 'review' }) : undefined);
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Calendar settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Calendar settings' });
    expect(within(dialog).queryByRole('combobox', { name: 'Company default' })).toBeNull();
    await pick('Your imported meetings', 'Review before sharing');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ method: 'POST', body: { auto_share: false } })]));
    expect(await run('calls.meetingSettings', { mine: 'auto' }, { via: 'tico' })).toMatchObject({ ok: false });
  });

  it('Meeting sources: the person’s Granola and each source with its state; Sync now brings notes in @s1015', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, {
      connected: true,
      needs_signin: false,
      syncing: false,
      email: 'me@example.com',
      last_synced_at: at(-2),
      imported_count: 4,
      skipped: 0,
    } as never);
    queryClient.setQueryData(sourcesQuery.queryKey, { sources: [{ id: 'close', name: 'Close', status: 'delayed', last_import: at(-30), error: 'Slow to answer' }] } as never);
    answer = (url, init) => (url.includes('/api/v2/meetings/granola/sync') && init?.method === 'POST' ? json({ imported: 0 }) : undefined);
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Meeting sources/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Meeting sources' });
    expect(within(dialog).getByText('4 notes')).toBeTruthy();
    expect(within(dialog).getByText('Delayed')).toBeTruthy();
    expect(within(dialog).getByText('Slow to answer')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ method: 'POST', url: expect.stringMatching(/\/api\/v2\/meetings\/granola\/sync\?now=true$/) })]));
  });

  it('Connect Granola shows the sign-in code, never caches it, and is the person’s own @s1016', async () => {
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/granola/connect') && init?.method === 'POST'
        ? json({ user_code: 'WXYZ-4321', verification_uri: 'https://app.granola.ai/device', interval: 30, expires_in: 600 })
        : undefined;
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Connect Granola' }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Granola' });
    expect((await within(dialog).findByLabelText('Granola code')).textContent).toBe('WXYZ-4321');
    expect(within(dialog).getByRole('link', { name: 'Open Granola' }).getAttribute('href')).toBe('https://app.granola.ai/device');
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('WXYZ-4321');
    expect(await run('calls.connectGranola', {}, { via: 'tico' })).toMatchObject({ ok: false });
    expect(await run('calls.disconnectGranola', {}, { via: 'tico' })).toMatchObject({ ok: false });
  });

  it('the owner turns an importer on with a computer and sees its credentials file; others may not @s1017', async () => {
    me('owner');
    const zoom = { source: 'zoom', name: 'Zoom', status: 'off', enabled: false, runner_id: '', setup: { file: '~/.tico/zoom.env', keys: ['ZOOM_ACCOUNT_ID'], doc: 'docs/meetings.md' } };
    const computers = [{ id: 'pc1', label: 'Office Mac', online: true }];
    queryClient.setQueryData(importersQuery.queryKey, { importers: [zoom], computers } as never);
    // What the server holds once it is on: the write's re-read gets it back.
    answer = (url, init) =>
      url.endsWith('/api/v2/meeting-importers/zoom') && init?.method === 'POST'
        ? json({ source: 'zoom', enabled: true })
        : url.endsWith('/api/v2/meeting-importers')
          ? json({ importers: [{ ...zoom, enabled: true, runner_id: 'pc1', status: 'waiting' }], computers })
          : url.endsWith('/api/v2/meetings/sources')
            ? json({ sources: [] })
            : undefined;
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Meeting sources/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Meeting sources' });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Zoom' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: expect.stringMatching(/\/api\/v2\/meeting-importers\/zoom$/), body: { enabled: true, runner_id: 'pc1' } })]));
    expect(await within(dialog).findByText('~/.tico/zoom.env')).toBeTruthy();
    me('person');
    expect(await run('calls.setImporter', { source: 'zoom', on: false }, { via: 'click' })).toMatchObject({ ok: false });
  });
});
