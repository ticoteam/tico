// What a person (or Tico) can do on the Calendar, in a meeting's panel and on a call.
import { defineAction, s, type Ctx } from '@/actions';
import { VIEWS } from './model';
import { addItem, changeEvent, createEvent, dismissItem, makeTask, meetKeys, removeEvent, startCall, type MeetingItem, type MeetingItems } from './queries';
import { claim, hear, join, leave, pickDevice, prime, setMuted } from './session';

const id = (what: string) => s.string({ min: 1, max: 200, what });
const day = s.string({ pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a day, 2026-11-18' });
const when = s.string({ pattern: /^\d{4}-\d{2}-\d{2}T/, what: 'an ISO time, 2026-11-18T10:30:00Z' });
const person = s.string({ min: 3, max: 200, pattern: /^(human|bot):[^\s]+$/, what: 'human:<id> or bot:<slug>' });
const guest = s.string({ min: 3, max: 200, pattern: /^((human|bot|team):[^\s]+|[^\s@]+@[^\s@]+)$/, what: 'human:<id>, bot:<slug>, team:<id> or an email' });
const section = s.oneOf(['doc', 'task', 'feature'] as const);

type Search = Record<string, unknown>;
const toCalendar = ({ router }: Ctx, search: (prev: Search) => Search) => router.navigate({ to: '/calendar', search });
const openCall = ({ router }: Ctx, room: string) => router.navigate({ to: '/call/$room', params: { room } });
const PAGE = { kind: 'page', id: '/calendar' } as const;

/* ---------------------------------------------------------------- the calendar */

export const show = defineAction({
  id: 'calls.show',
  title: 'Show the calendar',
  kind: 'view',
  input: s.object({ view: s.optional(s.oneOf(VIEWS)), date: s.optional(day) }),
  run: (ctx, { view, date }) => toCalendar(ctx, (prev) => ({ ...prev, view: view ?? prev.view, date: date ?? prev.date })),
  show: (ctx, { view, date }) => toCalendar(ctx, (prev) => ({ ...prev, view: view ?? prev.view, date: date ?? prev.date })),
  did: ({ view }) => (view ? `Opened Calendar, ${view}` : 'Opened Calendar'),
  target: () => PAGE,
});

export const openEvent = defineAction({
  id: 'calls.openEvent',
  title: 'Open an event',
  kind: 'view',
  input: s.object({ id: id('an occurrence id') }),
  run: (ctx, { id }) => toCalendar(ctx, (prev) => ({ ...prev, panel: `event:${id}` })),
  did: () => 'Opened the event',
  target: ({ id }) => ({ kind: 'event', id }),
});

export const openMeeting = defineAction({
  id: 'calls.openMeeting',
  title: 'Open a meeting',
  kind: 'view',
  input: s.object({ id: id('a meeting id') }),
  run: (ctx, { id }) => toCalendar(ctx, (prev) => ({ ...prev, view: 'list', panel: `meeting:${id}` })),
  did: () => 'Opened the meeting',
  target: ({ id }) => ({ kind: 'meeting', id }),
});

export const closePanel = defineAction({
  id: 'calls.closePanel',
  title: 'Close the event',
  kind: 'view',
  input: s.object({}),
  run: (ctx) => toCalendar(ctx, ({ panel: _, ...rest }) => rest),
  target: () => PAGE,
});

const eventInput = {
  title: s.string({ min: 1, max: 300 }),
  start: when,
  minutes: s.number({ int: true, min: 5, max: 1440 }),
  with: s.optional(s.array(guest, { max: 100 })),
  meet: s.optional(s.boolean()),
};
const fields = (i: { title: string; start: string; minutes: number; with?: string[]; meet?: boolean }) => ({
  title: i.title,
  start: new Date(i.start),
  minutes: i.minutes,
  attendees: i.with ?? [],
  meet: !!i.meet,
});

export const newEvent = defineAction({
  id: 'calls.createEvent',
  title: 'Add an event',
  kind: 'change',
  input: s.object(eventInput),
  run: async (ctx, input) => {
    const o = await createEvent(fields(input));
    await toCalendar(ctx, (prev) => ({ ...prev, panel: `event:${o.occurrence_id}` }));
    return o;
  },
  did: ({ title }) => `Added ${title}`,
  target: (_i, o) => (o ? { kind: 'event', id: o.occurrence_id } : PAGE),
});

export const editEvent = defineAction({
  id: 'calls.changeEvent',
  title: 'Change an event',
  kind: 'change',
  input: s.object({ id: id('an occurrence id'), ...eventInput }),
  run: (_ctx, { id, ...input }) => changeEvent(id, fields(input)),
  did: ({ title }) => `Changed ${title}`,
  target: ({ id }) => ({ kind: 'event', id }),
});

export const deleteEvent = defineAction({
  id: 'calls.removeEvent',
  title: 'Remove an event',
  kind: 'change',
  input: s.object({ id: id('an occurrence id') }),
  run: async (ctx, { id }) => {
    await removeEvent(id);
    await toCalendar(ctx, ({ panel: _, ...rest }) => rest);
  },
  did: () => 'Removed the event',
  target: () => PAGE,
});

/* ---------------------------------------------------------------- meetings */

const itemOf = ({ queryClient }: Ctx, meeting: string, item: string): MeetingItem => {
  const d = queryClient.getQueryData<MeetingItems>(meetKeys.items(meeting));
  const found = d && [...d.sections.doc, ...d.sections.task, ...d.sections.feature].find((x) => x.id === item);
  if (!found) throw new Error('not_found: that item is not on screen');
  return found;
};

export const addMeetingItem = defineAction({
  id: 'calls.addItem',
  title: 'Add to a meeting',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), section, text: s.string({ min: 1, max: 2000 }) }),
  run: (_ctx, { meeting, section, text }) => addItem(meeting, section, text),
  did: ({ section }) => (section === 'task' ? 'Added a task to make' : 'Added to the agenda'),
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const dismissMeetingItem = defineAction({
  id: 'calls.dismissItem',
  title: 'Hide a meeting item',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), item: id('an item id') }),
  run: (ctx, { meeting, item }) => dismissItem(meeting, itemOf(ctx, meeting, item)),
  did: () => 'Hid it',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const makeMeetingTask = defineAction({
  id: 'calls.makeTask',
  title: 'Make a task from a meeting',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), item: id('an item id') }),
  run: (ctx, { meeting, item }) => makeTask(meeting, itemOf(ctx, meeting, item)),
  did: () => 'Made the task',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

/* ---------------------------------------------------------------- calls */

// The microphone opens only from the person's own press: these are never Tico's. prime() runs before the first await,
// so it is still inside the press when the browser asks.
export const startCallAction = defineAction({
  id: 'calls.start',
  title: 'Start a call',
  kind: 'change',
  tico: false,
  input: s.object({ with: s.array(person, { max: 50 }) }),
  run: async (ctx, input) => {
    prime(null);
    // No call came of it: let the microphone go again.
    const call = await startCall(input.with).catch((e: unknown) => {
      void leave();
      throw e;
    });
    claim(call.id);
    await openCall(ctx, call.id);
    await join(call.id);
    return call;
  },
  did: () => 'Started the call',
});

export const joinCallAction = defineAction({
  id: 'calls.join',
  title: 'Join a call',
  kind: 'change',
  tico: false,
  input: s.object({ id: id('a call id') }),
  run: async (ctx, { id }) => {
    prime(id);
    await openCall(ctx, id);
    await join(id);
  },
  did: () => 'Joined the call',
});

export const openCallAction = defineAction({
  id: 'calls.open',
  title: 'Open a call',
  kind: 'view',
  input: s.object({ id: id('a call id') }),
  run: (ctx, { id }) => openCall(ctx, id),
  show: (ctx, { id }) => openCall(ctx, id),
  did: () => 'Opened the call',
  target: ({ id }) => ({ kind: 'page', id: `/call/${id}` }),
});

export const mute = defineAction({
  id: 'calls.mute',
  title: 'Mute',
  kind: 'change',
  tico: false,
  input: s.object({ on: s.boolean() }),
  run: (_ctx, { on }) => setMuted(on),
  did: ({ on }) => (on ? 'Muted' : 'Unmuted'),
});

export const device = defineAction({
  id: 'calls.device',
  title: 'Pick a microphone',
  kind: 'change',
  tico: false,
  input: s.object({ id: id('a device id') }),
  run: (_ctx, { id }) => pickDevice(id),
});

export const hearAction = defineAction({
  id: 'calls.hear',
  title: 'Turn on the call’s sound',
  kind: 'change',
  tico: false,
  input: s.object({}),
  run: () => hear(),
});

export const leaveAction = defineAction({
  id: 'calls.leave',
  title: 'Leave the call',
  kind: 'change',
  input: s.object({}),
  run: () => leave(),
  did: () => 'Left the call',
});
