// What the routes and the shell use before a page's code loads: the calendar's address, the call reads and the call
// state. No components here: the first load imports this file, and a page comes through exports.ts with its route.
export { calendarSearch, rangeOf, type CalendarSearch, type CalView } from './model';
export { eventsQuery, meetingsQuery, callQuery, liveCallsQuery, ringingQuery } from './queries';
export { isActive as isCallActive, useCall } from './session';
/** The action the phone's call lip and any mute key press: `run(TOGGLE_MUTE, {}, { via: 'click' })`. */
export const TOGGLE_MUTE = 'calls.toggleMute';
