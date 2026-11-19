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
import {
  eventsQuery,
  granolaQuery,
  importersQuery,
  itemsQuery,
  liveCallsQuery,
  meetingQuery,
  meetingSettingsQuery,
  meetingsQuery,
  sourcesQuery,
  transcriptQuery,
  upcomingCallsQuery,
  type Meeting,
  type MeetingMore,
  type Review,
} from './queries';

/* Meetings in List: Shared / Pending / Dismissed (the old Meetings tabs), a meeting's panel (share, private, send,
   transcript and chat), booked calls, and the ⋯'s Log a meeting, Calendar settings, Meeting sources, Connect Granola
   and the owner's importers; then the Calendar checks against the old app. */

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
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const events = (list: CalendarOccurrence[]): CalendarEvents => ({ calendars: [], errors: [], from: '', to: '', month_counts: [], occurrences: list });

function seed(view: 'day' | 'week', list: CalendarOccurrence[]) {
  const r = rangeOf(view, parseDay(DAY) as Date);
  queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, events(list));
}

function show(path: string, basepath = '/') {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const cal = createRoute({ getParentRoute: () => root, path: '/calendar', validateSearch: calendarSearch, component: CalendarPage });
  const tools = createRoute({ getParentRoute: () => root, path: '/library/tools', component: () => null });
  const routines = createRoute({ getParentRoute: () => root, path: '/company/routines', component: () => <p>Routines</p> });
  const router = createRouter({ basepath, routeTree: root.addChildren([cal, tools, routines]), history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

let answer: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined = () => undefined;
// What the server holds for the meeting: each write invalidates the meeting's reads, and they read this back.
let stored: (url: string) => Response | undefined = () => undefined;
const sent: { url: string; method: string; body: unknown }[] = [];
const writes = () => sent.filter((r) => r.method !== 'GET');
const AGENT = { id: 'bot:scout', kind: 'agent', name: 'Scout' };
const ANA = { id: 'human:ana', kind: 'person', name: 'Ana Reyes' };
const CALL = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0';
const me = (role: 'person' | 'owner') => queryClient.setQueryData(['me'], { id: 'human:me', email: 'me@example.com', role, slug: null });

beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => undefined;
  stored = () => undefined;
  me('person');
  queryClient.setQueryData(['directory'], { actors: [ANA, AGENT], byId: { [ANA.id]: ANA, [AGENT.id]: AGENT } });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  queryClient.setQueryData(granolaQuery.queryKey, { connected: false, needs_signin: false, syncing: false, imported_count: 0, skipped: 0 } as never);
  queryClient.setQueryData(sourcesQuery.queryKey, { sources: [] } as never);
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return (await answer(String(url), init)) ?? stored(String(url)) ?? json(events([]));
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

/* ---------------------------------------------------------------- the old Meetings tabs, now List's filter */

const meeting = (p: Partial<MeetingMore> = {}): MeetingMore => ({ id: 'mt1', title: 'Weekly sync', review_state: 'live', started: at(-20), can_edit: true, version: 3, private: false, ...p });
function seedList(review: Review, m: MeetingMore) {
  queryClient.setQueryData(meetingsQuery(review).queryKey, { meetings: [m], count: 1, pending_count: m.review_state === 'pending' ? 1 : 0 });
  queryClient.setQueryData(meetingQuery('mt1').queryKey, m);
  queryClient.setQueryData(itemsQuery('mt1').queryKey, { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } });
}
function seedMeeting(m: MeetingMore) {
  const items = { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } };
  queryClient.setQueryData(meetingQuery('mt1').queryKey, m);
  queryClient.setQueryData(itemsQuery('mt1').queryKey, items);
  queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [m], count: 1, pending_count: 0 });
  stored = (url) =>
    url.endsWith('/api/v2/meetings/mt1')
      ? json(queryClient.getQueryData(meetingQuery('mt1').queryKey) ?? m)
      : url.endsWith('/meetings/mt1/items')
        ? json(items)
        : url.includes('/api/v2/meetings?')
          ? json({ meetings: [], count: 0, pending_count: 0 })
          : undefined;
}
const openMeeting = async () => {
  show('/calendar?view=list&panel=meeting%3Amt1');
  return screen.findByRole('complementary', { name: 'Weekly sync' });
};

const seedPending = (m: MeetingMore) => seedList('pending', { ...m, review_state: 'pending' });

describe('Meetings in List', () => {
  it('copies a meeting link that includes the app base path', async () => {
    seedMeeting(meeting());
    const writeText = vi.fn().mockResolvedValue(undefined);
    const previous = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    try {
      show('/next/calendar?view=list&panel=meeting%3Amt1', '/next');
      const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
      fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy link' }));
      expect(writeText).toHaveBeenCalledWith(`${location.origin}/next/calendar?view=list&panel=meeting%3Amt1`);
    } finally {
      if (previous) Object.defineProperty(navigator, 'clipboard', previous);
      else Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

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
    expect(within(panel).getByRole('button', { name: 'Share' })).toBeTruthy();
    expect(await run('calls.review', { meeting: 'mt1', action: 'approve' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(writes()).toHaveLength(0);
    fireEvent.click(within(panel).getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ method: 'POST', body: { action: 'approve', private: false, send_to: 'auto' } })]));
  });

  it('a pending meeting asks for no tasks: the server lists them only once it is shared @s994', async () => {
    const m = meeting({ review_state: 'pending' });
    seedList('pending', m);
    queryClient.removeQueries({ queryKey: itemsQuery('mt1').queryKey });
    show('/calendar?view=list&review=pending&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    expect(within(panel).getByRole('button', { name: 'Share' })).toBeTruthy();
    expect(sent.filter((r) => r.url.includes('/meetings/mt1/items'))).toEqual([]);
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

describe('Pending @s994', () => {
  it('a pending row waits on you; its panel dismisses it @s994', async () => {
    seedPending(meeting());
    answer = (url, init) => (url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST' ? json(meeting({ review_state: 'dismissed' })) : undefined);
    show('/calendar?view=list&review=pending');
    const row = (await screen.findByRole('link', { name: 'Weekly sync' })).closest('.cl-row') as HTMLElement;
    expect(within(row).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    fireEvent.click(within(row).getByRole('link', { name: 'Weekly sync' }));
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ body: { action: 'dismiss' } })]));
  });

  it('sharing from its panel says Team or Private and which agent files its tasks @s994', async () => {
    seedPending(meeting());
    answer = (url, init) => (url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST' ? json(meeting({ review_state: 'live', private: true })) : undefined);
    show('/calendar?view=list&review=pending&panel=meeting:mt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    await pick('Who sees it once shared', 'Private');
    await pick('Agent that files its tasks', 'Send to Scout');
    fireEvent.click(within(panel).getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ body: { action: 'approve', private: true, send_to: 'scout' } })]));
  });
});

describe('Log a meeting @s1013', () => {
  it('files a transcript with its source, recording link, an outside guest and an agent to send it to @s1013', async () => {
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/import') && init?.method === 'POST' ? json({ id: 'mt9' }) : url.endsWith('/api/v2/meetings/mt9') ? json(meeting({ id: 'mt9', review_state: 'live' })) : undefined;
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Log a meeting' }));
    const dialog = await screen.findByRole('dialog', { name: 'Log a meeting' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Transcript' }), { target: { value: 'Ana: hello' } });
    await pick('Source', 'Zoom');
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Recording link' }), { target: { value: 'https://rec.example.com/1' } });
    const who = within(dialog).getByRole('textbox', { name: 'Who' });
    fireEvent.change(who, { target: { value: 'ana@example.com' } });
    fireEvent.keyDown(who, { key: 'Enter' });
    expect(within(dialog).getByRole('button', { name: 'Remove ana@example.com' })).toBeTruthy();
    await pick('Send to an agent', 'Scout');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Log meeting' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]!.body).toMatchObject({
      source: 'zoom',
      transcript: 'Ana: hello',
      media_url: 'https://rec.example.com/1',
      participants: ['ana@example.com'],
      send_to: 'scout',
    });
  });
});

describe('Meeting sources @s1015', () => {
  it('the ⋯ says a source needs a look, Close opens its tool page, and Remove asks first @s1015', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, { connected: true, needs_signin: false, syncing: false, imported_count: 2, skipped: 0 } as never);
    queryClient.setQueryData(sourcesQuery.queryKey, { sources: [{ id: 'close', name: 'Close', status: 'error', error: 'Key refused' }] } as never);
    answer = (url, init) => (url.endsWith('/api/v2/meetings/granola/connect') && init?.method === 'DELETE' ? json({ ok: true }) : undefined);
    const router = show('/calendar?view=list');
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    expect(head.querySelector('.call-more.is-trouble')).toBeTruthy();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Meeting sources.*Needs a look/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Meeting sources' });
    expect(within(dialog).getByRole('link', { name: 'Manage' }).getAttribute('href')).toBe('/library/tools?tool=close-crm');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Remove Granola?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }));
    expect(writes()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Remove Granola?' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ method: 'DELETE', url: expect.stringMatching(/\/granola\/connect$/) })]));
    fireEvent.click(within(dialog).getByRole('link', { name: 'Manage' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/tools'));
  });
});

describe('Connect Granola @s1016', () => {
  it('brings the first notes in as soon as Granola connects @s1016', async () => {
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/granola/connect') && init?.method === 'POST'
        ? json({ user_code: 'ABCD-1234', verification_uri: 'https://app.granola.ai/device', interval: 0.1, expires_in: 600 })
        : url.endsWith('/api/v2/meetings/granola/connect/status')
          ? json({ state: 'connected' })
          : url.includes('/api/v2/meetings/granola/sync')
            ? json({ imported: 3 })
            : undefined;
    show('/calendar?view=list');
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Connect Granola' }));
    await waitFor(() => expect(writes().map((w) => w.url)).toContainEqual(expect.stringMatching(/\/api\/v2\/meetings\/granola\/sync\?now=true$/)));
  });
});

describe('Meeting details', () => {
  it('the ⋯ makes a shared meeting private and back to the team; Tico may not open it to the team @s1012', async () => {
    seedMeeting(meeting());
    answer = (url, init) => (url.endsWith('/meetings/mt1/edit') && init?.method === 'POST' ? json(meeting({ private: true, version: 4 })) : undefined);
    const panel = await openMeeting();
    expect(within(panel).getByText('Team')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Make private' }));
    await waitFor(() => expect(within(panel).getByText('Private')).toBeTruthy());
    expect(writes()).toEqual([expect.objectContaining({ url: expect.stringMatching(/\/api\/v2\/meetings\/mt1\/edit$/), body: { private: true, version: 3 } })]);
    expect(await run('calls.setPrivate', { meeting: 'mt1', private: false }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(await run('calls.deleteMeeting', { meeting: 'mt1' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(writes()).toHaveLength(1);
  });

  it('sends a meeting to an agent, and deletes it after a confirm @s1012', async () => {
    seedMeeting(meeting());
    answer = (url) =>
      url.endsWith('/api/meetings/mt1/send') ? json({ id: 'mt1', bot: 'scout', task: 't9', pending: false }) : url.endsWith('/meetings/mt1/delete') ? json({ ok: true }) : undefined;
    const panel = await openMeeting();
    expect(await run('calls.sendMeeting', { meeting: 'mt1', agent: 'bot:scout' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.find((r) => r.url.endsWith('/api/meetings/mt1/send'))).toMatchObject({ method: 'POST', body: { bot_id: 'scout', instructions: '' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Delete Weekly sync?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent.find((r) => r.url.endsWith('/meetings/mt1/delete'))).toMatchObject({ method: 'POST' }));
  });
});

describe('Schedule or change a call', () => {
  it('New event books a voice call for later: Start now and Schedule @s1018', async () => {
    queryClient.setQueryData(['config'], { features: { tico_meet: true } });
    answer = (url, init) =>
      url.includes('/api/v2/threads/dm?with=human%3Aana')
        ? json({ id: 'th1' })
        : url.endsWith('/api/v2/threads/th1/calls') && init?.method === 'POST'
          ? json({ id: CALL, thread_id: 'th1', state: 'scheduled' })
          : undefined;
    show(`/calendar?view=week&date=${DAY}`);
    fireEvent.click(await screen.findByRole('button', { name: 'New event' }));
    const dialog = await screen.findByRole('dialog', { name: 'New event' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Voice call' }));
    await screen.findByRole('dialog', { name: 'New call' });
    expect(within(dialog).getByRole('button', { name: 'Start now' })).toBeTruthy();
    expect((within(dialog).getByRole('button', { name: 'Schedule' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Title' }), { target: { value: 'Price check' } });
    fireEvent.change(within(dialog).getByLabelText('Who'), { target: { value: 'Ana' } });
    fireEvent.keyDown(within(dialog).getByLabelText('Who'), { key: 'Enter' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Schedule' }));
    await waitFor(() => expect(sent.find((r) => r.url.endsWith('/threads/th1/calls'))).toBeTruthy());
    const body = sent.find((r) => r.url.endsWith('/threads/th1/calls'))?.body as Record<string, string>;
    expect(body).toMatchObject({ provider: 'tico', title: 'Price check' });
    expect(Date.parse(body.ends_at as string) - Date.parse(body.starts_at as string)).toBe(30 * 60_000);
  });

  it('keeps start and schedule off when the server has no Tico Meet', async () => {
    queryClient.setQueryData(['config'], { features: { tico_meet: false } });
    show(`/calendar?view=week&date=${DAY}`);
    fireEvent.click(await screen.findByRole('button', { name: 'New event' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'New event' })).getByRole('button', { name: 'Voice call' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'New call' }));
    fireEvent.change(dialog.getByLabelText('Who'), { target: { value: 'Ana' } });
    fireEvent.keyDown(dialog.getByLabelText('Who'), { key: 'Enter' });
    expect(dialog.getByRole('button', { name: `Remove ${ANA.name}` })).toBeTruthy();
    expect(dialog.getByText('Tico Meet is not set up on this server')).toBeTruthy();
    for (const name of ['Start now', 'Schedule']) {
      expect((dialog.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("moves a booked call's time from its panel; Tico may move it but not cancel it @s1018", async () => {
    const r = rangeOf('week', parseDay(DAY) as Date);
    const o: CalendarOccurrence = {
      all_day: false,
      attendees: [],
      busy_only: false,
      calendar: { id: 'c1', name: 'Work', ref: 'cal:1', type: 'tico' },
      can: { edit: true, join: true },
      end: at(11),
      event_id: null,
      home: 'tico',
      kind: 'event',
      links: { attempt: null, automation: null, meeting: null, task: null, thread: null },
      meeting_url: `https://app.example.com/call/${CALL}`,
      occurrence_id: 'o1',
      open_url: null,
      recurring: false,
      response: null,
      start: at(10),
      status: 'confirmed',
      time_zone: null,
      title: 'Price check',
    };
    queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, events([o]));
    queryClient.setQueryData(upcomingCallsQuery.queryKey, {
      calls: [{ can_edit: true, id: CALL, people: [], scheduled_start: at(10), scheduled_end: at(11), thread_id: 'th1', title: 'Price check' }],
    });
    answer = (url, init) => (url.endsWith(`/api/v2/calls/${CALL}`) && init?.method === 'PATCH' ? json({ id: CALL, state: 'scheduled' }) : undefined);
    show(`/calendar?view=week&date=${DAY}&panel=event%3Ao1`);
    const panel = await screen.findByRole('complementary', { name: 'Price check' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Change time' }));
    const dialog = await screen.findByRole('dialog', { name: 'Change time' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.find((x) => x.method === 'PATCH')).toBeTruthy());
    expect(sent.find((x) => x.method === 'PATCH')?.body).toEqual({ starts_at: at(10), ends_at: at(11) });
  });
});

describe('Transcript and chat column', () => {
  const said = meeting({
    live_meeting_id: 'lm1',
    turns: [
      { speaker: 'Ana', start_ms: 0, text: 'Prices go up in May.' },
      { speaker: 'Ben', start_ms: 65_000, text: 'Then we tell customers in April, before May.' },
    ],
  });

  it('searches the transcript, marking each hit, and copies it @s1045', async () => {
    seedMeeting(said);
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const panel = await openMeeting();
    fireEvent.click(within(panel).getByRole('tab', { name: 'Transcript' }));
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Search the transcript' }), { target: { value: 'may' } });
    expect(panel.querySelectorAll('mark')).toHaveLength(2);
    expect(within(panel).getByText('1/2')).toBeTruthy();
    fireEvent.keyDown(within(panel).getByRole('textbox', { name: 'Search the transcript' }), { key: 'Enter' });
    expect(within(panel).getByText('2/2')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Copy transcript' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Ana [0:00]: Prices go up in May.\nBen [1:05]: Then we tell customers in April, before May.'));
  });

  it('shows the call’s chat beside the transcript @s1045', async () => {
    seedMeeting(said);
    queryClient.setQueryData(transcriptQuery('lm1').queryKey, { chunks: [], chat: [{ actor: 'human:ana', role: 'user', at_ms: 30_000, text: 'Link to the price sheet?' }] } as never);
    const panel = await openMeeting();
    fireEvent.click(within(panel).getByRole('tab', { name: 'Chat' }));
    expect(within(panel).getByText('Link to the price sheet?')).toBeTruthy();
    expect(within(panel).getByText('Ana Reyes')).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Copy chat' })).toBeTruthy();
  });
});

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
  const bare = (p: Partial<Meeting>): Meeting => ({ id: 'mt1', title: 'Weekly sync', review_state: 'pending', started: at(9), ...p });
  beforeEach(() => {
    queryClient.setQueryData(itemsQuery('mt1').queryKey, { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } });
    queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [], count: 0, pending_count: 1 });
  });

  it('a pending meeting is the person’s alone until Share sends it to the company @s1012', async () => {
    queryClient.setQueryData(meetingQuery('mt1').queryKey, bare({}));
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST'
        ? json(bare({ review_state: 'live' }))
        : url.endsWith('/api/v2/meetings/mt1/items')
          ? json({ id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } })
          : url.includes('/api/v2/meetings')
            ? json(url.endsWith('/mt1') ? bare({ review_state: 'live' }) : { meetings: [], count: 0, pending_count: 0 })
            : undefined;
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(within(panel).queryByRole('button', { name: 'Share' })).toBeNull());
    expect(sent.find((r) => r.url.endsWith('/meetings/mt1/review'))).toMatchObject({ method: 'POST', body: { action: 'approve' } });
  });

  it('shows what was said, each turn with its speaker and time @s1045', async () => {
    queryClient.setQueryData(
      meetingQuery('mt1').queryKey,
      bare({
        review_state: 'live',
        turns: [
          { speaker: 'Ana', start_ms: 0, text: 'Prices go up in May.' },
          { speaker: 'Ben', start_ms: 65_000, text: 'Then we tell customers in April.' },
        ],
      }),
    );
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    fireEvent.click(within(panel).getByRole('tab', { name: 'Transcript' }));
    expect(within(panel).getByText('Then we tell customers in April.')).toBeTruthy();
    expect(within(panel).getByText('Ben')).toBeTruthy();
    expect(within(panel).getByText('1:05')).toBeTruthy();
  });

  it('a transcript with no turns shows its text a line at a time', async () => {
    queryClient.setQueryData(meetingQuery('mt1').queryKey, bare({ review_state: 'live', transcript_readable: 'First line\nSecond line' }));
    show('/calendar?view=list&panel=meeting%3Amt1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly sync' });
    fireEvent.click(within(panel).getByRole('tab', { name: 'Transcript' }));
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
