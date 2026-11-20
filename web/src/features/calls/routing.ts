import type { QueryClient } from '@tanstack/react-query';
import { addDays, calendarFeeds, panelOf, parseDay, rangeOf, type CalendarSearch } from './model';
import { eventsQuery, findEventsQuery, liveCallsQuery, meetingQuery, meetingsQuery } from './queries';

// What the routes and the shell use before a page's code loads: the calendar's address, the call reads and the call
// state. No components here: the first load imports this file, and a page comes through exports.ts with its route.
export { calendarSearch, rangeOf, type CalendarSearch, type CalView } from './model';
export { eventsQuery, meetingsQuery, callQuery, liveCallsQuery, ringingQuery } from './queries';
export { isActive as isCallActive, useCall } from './session';
/** The action the phone's call lip and any mute key press: `run(TOGGLE_MUTE, {}, { via: 'click' })`. */
export const TOGGLE_MUTE = 'calls.toggleMute';

export function loadCalendar(client: QueryClient, search: CalendarSearch) {
  const feeds = calendarFeeds(search);
  if (feeds.list) void client.prefetchQuery(meetingsQuery(search.review ?? 'live', search.q ?? ''));
  if (feeds.range) {
    const view = search.view ?? 'week';
    const day = (feeds.list ? null : parseDay(search.date)) ?? addDays(new Date(), 0);
    const { from, to } = rangeOf(view, day);
    void client.prefetchQuery(eventsQuery(from, to));
  }
  if (feeds.found && search.q) void client.prefetchQuery(findEventsQuery(search.q));
  if (feeds.live) void client.prefetchQuery(liveCallsQuery);
  const panel = panelOf(search.panel);
  if (panel?.kind === 'meeting') void client.prefetchQuery(meetingQuery(panel.id));
}
