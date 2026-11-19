// Calendar, meetings and calls. The only file other code imports from this feature.
export { CalendarPage } from './CalendarPage';
export { CallBar } from './CallBar';
export { CallPage, type CallPageProps } from './CallPage';
export { CallSide, type CallSideProps } from './CallSide';
export { RingCard } from './RingCard';
export { calendarSearch, rangeOf, type CalendarSearch, type CalView } from './model';
export { eventsQuery, meetingsQuery, callQuery, liveCallsQuery, ringingQuery } from './queries';
export { isActive as isCallActive, useCall } from './session';
/** The action the phone's call lip and any mute key press: `run(TOGGLE_MUTE, {}, { via: 'click' })`. */
export const TOGGLE_MUTE = 'calls.toggleMute';
