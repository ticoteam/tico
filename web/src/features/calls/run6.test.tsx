import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CalendarEvents } from '@/api/schema';
import { ActionsRoot } from '@/actions';
import './actions';
import { queryClient } from '@/data';
import { CalendarPage } from './CalendarPage';
import { calendarSearch, parseDay, rangeOf } from './model';
import { eventsQuery, liveCallsQuery, meetingsQuery } from './queries';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => null,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));

const DAY = '2026-11-18';
const none: CalendarEvents = { calendars: [], errors: [], from: '', to: '', month_counts: [], occurrences: [] };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});
beforeEach(() => {
  queryClient.clear();
  document.documentElement.dataset.layout = 'cards';
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(liveCallsQuery.queryKey, { calls: [] });
  queryClient.setQueryData(meetingsQuery().queryKey, { meetings: [], count: 0, pending_count: 0 });
  const r = rangeOf('week', parseDay(DAY) as Date);
  queryClient.setQueryData(eventsQuery(r.from, r.to).queryKey, none);
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify(none), { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => {
  delete document.documentElement.dataset.layout;
  vi.unstubAllGlobals();
});

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
    </QueryClientProvider>,
  );
}

/** The head's controls a phone draws besides its ⋯: in the actions only those marked `data-phone` (pagekinds.css hides
    the rest). */
const controls = (head: HTMLElement) =>
  [...head.querySelectorAll<HTMLElement>('a, button')]
    .filter((el) => el.getAttribute('aria-label') !== 'More')
    .filter((el) => {
      const kept = [...head.querySelectorAll('.pk-acts > *')].find((c) => c.contains(el));
      return !kept || kept.hasAttribute('data-phone');
    })
    .map((el) => el.getAttribute('aria-label') ?? el.textContent);

describe('Calendar head on a phone', () => {
  it('keeps two controls in Week: Previous and Next; New event is in the ⋯', async () => {
    show(`/calendar?view=week&date=${DAY}`);
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    expect(controls(head)).toEqual(['Previous week', 'Next week']);
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New event' }));
    expect(await screen.findByRole('dialog', { name: 'New event' })).toBeTruthy();
  });

  it('keeps New event in List, where nothing steps through dates', async () => {
    show('/calendar?view=list');
    const head = (await screen.findByRole('heading', { name: 'Calendar' })).closest('header') as HTMLElement;
    const kept = within(head).getByRole('button', { name: 'New event' });
    expect(kept.hasAttribute('data-phone')).toBe(true);
    expect(controls(head).length).toBeLessThanOrEqual(2);
  });
});
