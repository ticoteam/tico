import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CalendarEvents } from '@/api/schema';
import { ActionsRoot } from '@/actions';
import './actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { CalendarPage } from './CalendarPage';
import { calendarSearch, parseDay } from './model';
import { granolaQuery, itemsQuery, liveCallsQuery, meetingQuery, meetingsQuery, sourcesQuery, type MeetingMore } from './queries';

/* Tier 2 gaps: Pending's bulk Share N / Dismiss N and a row's own Share & send, Team or Private and an agent at
   sharing; Log a meeting's source, recording link, agent and outside names; Meeting sources' Close link, Remove's
   confirm and the "needs a look" dot; and the first Granola sync once it connects. */

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
const events = (): CalendarEvents => ({ calendars: [], errors: [], from: '', to: '', month_counts: [], occurrences: [] });

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
  const tools = createRoute({ getParentRoute: () => root, path: '/library/tools', component: () => null });
  const router = createRouter({ routeTree: root.addChildren([cal, tools]), history: createMemoryHistory({ initialEntries: [path] }) });
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
const AGENT = { id: 'bot:scout', kind: 'agent', name: 'Scout' };

beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => undefined;
  queryClient.setQueryData(['me'], { id: 'human:me', email: 'me@example.com', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [AGENT], byId: { [AGENT.id]: AGENT } });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  queryClient.setQueryData(granolaQuery.queryKey, { connected: false, needs_signin: false, syncing: false, imported_count: 0, skipped: 0 } as never);
  queryClient.setQueryData(sourcesQuery.queryKey, { sources: [] } as never);
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return answer(String(url), init) ?? json(events());
  });
});
afterEach(() => vi.unstubAllGlobals());

const meeting = (p: Partial<MeetingMore> = {}): MeetingMore => ({ id: 'mt1', title: 'Weekly sync', review_state: 'pending', started: at(-20), can_edit: true, version: 3, private: false, ...p });
function seedPending(...ms: MeetingMore[]) {
  queryClient.setQueryData(meetingsQuery('pending').queryKey, { meetings: ms, count: ms.length, pending_count: ms.length });
  for (const m of ms) {
    queryClient.setQueryData(meetingQuery(m.id).queryKey, m);
    queryClient.setQueryData(itemsQuery(m.id).queryKey, { id: m.id, can_send: true, can_push: true, sections: { doc: [], task: [], feature: [] } });
  }
}
const pick = async (name: string, option: string | RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};
const more = async () => {
  const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
  fireEvent.click(within(head).getByRole('button', { name: 'More' }));
};

describe('Pending @s994', () => {
  it("a row's ⋯ shares and sends it to Tico, or dismisses it alone @s994", async () => {
    seedPending(meeting());
    answer = (url, init) =>
      url.endsWith('/api/v2/meetings/mt1/review') && init?.method === 'POST'
        ? json(meeting({ review_state: 'live' }))
        : url.includes('/api/v2/meetings?review=pending')
          ? json({ meetings: [meeting()], count: 1, pending_count: 1 })
          : undefined;
    show('/calendar?view=list&review=pending');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Weekly sync' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Share & send' }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ body: { action: 'approve', send_to: 'auto' } })]));
    fireEvent.click(await screen.findByRole('button', { name: 'More for Weekly sync' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Dismiss' }));
    await waitFor(() => expect(writes()[1]).toMatchObject({ url: expect.stringMatching(/\/meetings\/mt1\/review$/), body: { action: 'dismiss' } }));
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
