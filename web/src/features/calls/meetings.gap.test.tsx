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
import { eventsQuery, itemsQuery, linkedTasksQuery, liveCallsQuery, meetingQuery, meetingsQuery, transcriptQuery, upcomingCallsQuery, type MeetingMore } from './queries';

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
const CALL = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0';

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

let answer: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined = () => undefined;
// What the server holds for the meeting: each write invalidates the meeting's reads, and they read this back.
let stored: (url: string) => Response | undefined = () => undefined;
const sent: { url: string; method: string; body: unknown }[] = [];
const writes = () => sent.filter((r) => r.method !== 'GET');
beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => undefined;
  stored = () => undefined;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const ana = { id: 'human:ana', kind: 'person', name: 'Ana Reyes' };
  const scout = { id: 'bot:scout', kind: 'agent', name: 'Scout' };
  queryClient.setQueryData(['directory'], { actors: [ana, scout], byId: { 'human:ana': ana, 'bot:scout': scout } });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return (await answer(String(url), init)) ?? stored(String(url)) ?? json(events([]));
  });
});
afterEach(() => vi.unstubAllGlobals());

const meeting = (p: Partial<MeetingMore> = {}): MeetingMore => ({ id: 'mt1', title: 'Weekly sync', review_state: 'live', started: at(9), can_edit: true, version: 3, private: false, ...p });
function seedMeeting(m: MeetingMore, tasks: { task_id: string; title: string }[] = []) {
  const items = { id: 'mt1', can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } };
  const links = { next_cursor: null, tasks: tasks.map((t) => ({ ...t, created_by: 'human:me', kind: 'linked' as const, number: 7, status: 'open' })) };
  queryClient.setQueryData(meetingQuery('mt1').queryKey, m);
  queryClient.setQueryData(itemsQuery('mt1').queryKey, items);
  queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [m], count: 1, pending_count: 0 });
  queryClient.setQueryData(linkedTasksQuery('mt1').queryKey, links);
  stored = (url) =>
    url.endsWith('/api/v2/meetings/mt1')
      ? json(queryClient.getQueryData(meetingQuery('mt1').queryKey) ?? m)
      : url.endsWith('/meetings/mt1/items')
        ? json(items)
        : url.includes('/meetings/mt1/tasks')
          ? json(queryClient.getQueryData(linkedTasksQuery('mt1').queryKey) ?? links)
          : url.includes('/api/v2/meetings?')
            ? json({ meetings: [], count: 0, pending_count: 0 })
            : undefined;
}
const openMeeting = async () => {
  show('/calendar?view=list&panel=meeting%3Amt1');
  return screen.findByRole('complementary', { name: 'Weekly sync' });
};

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

  it('links a task by its number and unlinks it @s1012', async () => {
    seedMeeting(meeting(), [{ task_id: 'tk1', title: 'Draft the price note' }]);
    answer = (url, init) =>
      url.endsWith('/meetings/mt1/tasks') && init?.method === 'POST'
        ? json({ next_cursor: null, results: [{ status: 200 }], tasks: [{ task_id: 'tk2', title: 'Tell customers', created_by: 'human:me', kind: 'linked', number: 12, status: 'open' }] })
        : url.endsWith('/meetings/mt1/tasks/tk1') && init?.method === 'DELETE'
          ? json({ ok: true })
          : undefined;
    const panel = await openMeeting();
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Link a task' }), { target: { value: '#12' } });
    fireEvent.submit(within(panel).getByRole('textbox', { name: 'Link a task' }).closest('form') as HTMLFormElement);
    await waitFor(() => expect(within(panel).getByText('Tell customers')).toBeTruthy());
    expect(sent.find((r) => r.url.endsWith('/meetings/mt1/tasks') && r.method === 'POST')?.body).toEqual({ task_ids: ['12'] });
    expect(await run('calls.unlinkTask', { meeting: 'mt1', task: 'tk1' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.find((r) => r.method === 'DELETE')?.url).toMatch(/\/api\/v2\/meetings\/mt1\/tasks\/tk1$/);
  });
});

describe('Schedule or change a call', () => {
  it('New event books a voice call for later: Start now and Schedule @s1018', async () => {
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
