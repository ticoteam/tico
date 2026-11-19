// Every server call the calendar, meetings and calls make, and every query they read.
import { queryOptions, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Assistant, CalendarEvents, CalendarOccurrence, Call, CallJoin, CallLive, CallNotes, EventChange, EventCreate, ThreadView } from '@/api/schema';
import { queryClient, quietRoot, write } from '@/data';

const enc = encodeURIComponent;

/* ---------------------------------------------------------------- keys */

export const calKeys = {
  all: ['calendar'] as const,
  events: ['calendar', 'events'] as const,
  range: (from: string, to: string) => ['calendar', 'events', from, to] as const,
};
export const meetKeys = {
  list: ['meetings', 'list'] as const,
  one: (id: string) => ['meetings', id] as const,
  items: (id: string) => ['meetings', id, 'items'] as const,
  live: (id: string) => ['meetings', id, 'live'] as const,
};
export const callKeys = {
  live: ['calls', 'live'] as const,
  one: (id: string) => ['calls', id] as const,
  notes: (id: string) => ['calls', id, 'notes'] as const,
};

// The calendar topic refetches ['calendar'] and a followed meeting refetches ['meetings', id]; the list of past meetings has
// no topic of its own, so it is read again after the person's own writes and on reconnect. A call has no topic a feature
// may hear yet, so the call page reads it again on a timer while it is on.
quietRoot('meetings');

/* ---------------------------------------------------------------- calendar */

/** Everything in [from, to): meetings, due dates, routines' runs and time away. The server takes 62 days at most. */
export const eventsQuery = (from: Date, to: Date) =>
  queryOptions({
    queryKey: calKeys.range(from.toISOString(), to.toISOString()),
    queryFn: ({ signal }) =>
      api.get<CalendarEvents>(`/api/v2/calendar/events?from=${enc(from.toISOString())}&to=${enc(to.toISOString())}&kinds=event,run,due,away`, { signal }),
  });

/** Every range on screen or in the cache, so a change shows wherever the event is drawn. */
const shownRanges = (): QueryKey[] => {
  const keys = queryClient.getQueriesData({ queryKey: calKeys.events }).map(([k]) => k);
  return keys.length ? keys : [calKeys.events];
};
const mapEvents = (d: CalendarEvents | undefined, f: (list: CalendarOccurrence[]) => CalendarOccurrence[]) => d && { ...d, occurrences: f(d.occurrences) };

/** What the event dialog sends: the fields a person sets. */
export interface EventFields {
  title: string;
  start: Date;
  minutes: number;
  attendees: string[];
  meet: boolean;
}
const body = (f: EventFields): EventCreate => ({
  title: f.title,
  start: f.start.toISOString(),
  end: new Date(f.start.getTime() + f.minutes * 60_000).toISOString(),
  attendees: f.attendees,
  add_meet: f.meet,
  time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
});

const draft = (id: string, f: EventFields): CalendarOccurrence => ({
  all_day: false,
  attendees: f.attendees.map((ref) => ({ ref })),
  busy_only: false,
  calendar: { id: '', name: '', ref: '', type: 'tico' },
  can: { edit: false, join: false },
  start: f.start.toISOString(),
  end: new Date(f.start.getTime() + f.minutes * 60_000).toISOString(),
  event_id: null,
  home: 'tico',
  kind: 'event',
  links: { attempt: null, automation: null, meeting: null, task: null, thread: null },
  meeting_url: null,
  occurrence_id: id,
  open_url: null,
  recurring: false,
  response: null,
  status: 'confirmed',
  time_zone: null,
  title: f.title,
});

const fresh = () => void queryClient.invalidateQueries({ queryKey: calKeys.all });

export async function createEvent(f: EventFields): Promise<CalendarOccurrence> {
  const tmp = `tmp:${Date.now()}`;
  const made = await write({
    key: shownRanges(),
    optimistic: (d: CalendarEvents | undefined) => mapEvents(d, (l) => [...l, draft(tmp, f)]),
    send: (idempotencyKey) => api.post<CalendarOccurrence>('/api/v2/calendar/events', body(f), { idempotencyKey }),
    settle: (d: CalendarEvents | undefined, o: CalendarOccurrence) =>
      mapEvents(d, (l) => l.filter((x) => x.occurrence_id !== o.occurrence_id).map((x) => (x.occurrence_id === tmp ? o : x))),
  });
  fresh();
  return made;
}

export async function changeEvent(id: string, f: EventFields): Promise<CalendarOccurrence> {
  const change: EventChange = { ...body(f), scope: 'this' };
  const made = await write({
    key: shownRanges(),
    optimistic: (d: CalendarEvents | undefined) =>
      mapEvents(d, (l) => l.map((x) => (x.occurrence_id === id ? { ...x, title: f.title, start: change.start as string, end: change.end as string } : x))),
    send: (idempotencyKey) => api.patch<CalendarOccurrence>(`/api/v2/calendar/events/${enc(id)}`, change, { idempotencyKey }),
  });
  fresh();
  return made;
}

export async function removeEvent(id: string): Promise<void> {
  await write({
    key: shownRanges(),
    optimistic: (d: CalendarEvents | undefined) => mapEvents(d, (l) => l.filter((x) => x.occurrence_id !== id)),
    send: (idempotencyKey) => api.delete<void>(`/api/v2/calendar/events/${enc(id)}?scope=this`, { idempotencyKey }),
  });
  fresh();
}

/* ---------------------------------------------------------------- meetings */

/** A meeting as GET /api/v2/meetings answers (the schema names only its id, title and review state). */
export interface Meeting {
  id: string;
  title: string;
  review_state: 'pending' | 'live' | 'dismissed';
  started?: string | null;
  created?: string | null;
  duration_ms?: number | null;
  notes?: string | null;
  preview?: string | null;
  participants?: { name?: string | null; email?: string | null; person_id?: string | null }[];
  outcome?: { summary?: string | string[] | null; tasks?: { id: string; title: string; status?: string }[] } | null;
  live_meeting_id?: string | null;
  can_edit?: boolean;
  media_url?: string | null;
  turns?: { speaker?: string | null; start_ms?: number | null; text: string }[];
}
export interface MeetingList {
  meetings: Meeting[];
  count: number;
  pending_count: number;
}
export interface MeetingItem {
  id: string;
  status: string;
  text: string;
  section: 'doc' | 'task' | 'feature';
  version?: number;
  proposed_task_id?: string | null;
  result_ref?: string | null;
}
export interface MeetingItems {
  id: string;
  can_send: boolean;
  can_push: boolean;
  sections: { doc: MeetingItem[]; task: MeetingItem[]; feature: MeetingItem[] };
}
/** A live meeting's transcript so far (GET /api/v2/live-meetings/{id}). */
export interface LiveTranscript {
  chunks: { seq: number; revision?: number; speaker?: string | null; text: string; start_ms?: number | null }[];
}

export const meetingsQuery = queryOptions({
  queryKey: meetKeys.list,
  queryFn: ({ signal }) => api.get<MeetingList>('/api/v2/meetings', { signal }),
});

export const meetingQuery = (id: string) =>
  queryOptions({ queryKey: meetKeys.one(id), queryFn: ({ signal }) => api.get<Meeting>(`/api/v2/meetings/${enc(id)}`, { signal }) });

export const itemsQuery = (id: string) =>
  queryOptions({ queryKey: meetKeys.items(id), queryFn: ({ signal }) => api.get<MeetingItems>(`/api/v2/meetings/${enc(id)}/items`, { signal }) });

export const transcriptQuery = (meetingId: string) =>
  queryOptions({
    queryKey: meetKeys.live(meetingId),
    queryFn: ({ signal }) => api.get<LiveTranscript>(`/api/v2/live-meetings/${enc(meetingId)}`, { signal }),
  });

const mapSection = (d: MeetingItems | undefined, section: MeetingItem['section'], f: (l: MeetingItem[]) => MeetingItem[]) =>
  d && { ...d, sections: { ...d.sections, [section]: f(d.sections[section]) } };

/** An agenda item or a note (doc), a task to make (task). */
export function addItem(meeting: string, section: MeetingItem['section'], text: string): Promise<MeetingItem> {
  const tmp = `tmp:${Date.now()}`;
  return write({
    key: meetKeys.items(meeting),
    optimistic: (d: MeetingItems | undefined) => mapSection(d, section, (l) => [...l, { id: tmp, status: 'proposed', text, section }]),
    send: (idempotencyKey) => api.post<MeetingItem>(`/api/v2/meetings/${enc(meeting)}/items`, { section, text }, { idempotencyKey }),
    settle: (d: MeetingItems | undefined, it: MeetingItem) => mapSection(d, section, (l) => l.map((x) => (x.id === tmp ? it : x))),
  });
}

export function dismissItem(meeting: string, item: MeetingItem): Promise<MeetingItem> {
  return write({
    key: meetKeys.items(meeting),
    optimistic: (d: MeetingItems | undefined) => mapSection(d, item.section, (l) => l.filter((x) => x.id !== item.id)),
    send: (idempotencyKey) =>
      api.post<MeetingItem>(`/api/v2/meetings/${enc(meeting)}/items/${enc(item.id)}`, { status: 'dismissed', version: item.version }, { idempotencyKey }),
  });
}

/** Send a task item on: it becomes a task. */
export function makeTask(meeting: string, item: MeetingItem): Promise<MeetingItem> {
  return write({
    key: meetKeys.items(meeting),
    optimistic: (d: MeetingItems | undefined) => mapSection(d, item.section, (l) => l.map((x) => (x.id === item.id ? { ...x, status: 'pushed' } : x))),
    send: (idempotencyKey) => api.post<MeetingItem>(`/api/v2/meetings/${enc(meeting)}/items/${enc(item.id)}/push`, {}, { idempotencyKey }),
  });
}

/* ---------------------------------------------------------------- calls */

export const liveCallsQuery = queryOptions({
  queryKey: callKeys.live,
  queryFn: ({ signal }) => api.get<CallLive>('/api/v2/calls/live', { signal }),
});

export const callQuery = (id: string) =>
  queryOptions({ queryKey: callKeys.one(id), queryFn: ({ signal }) => api.get<Call>(`/api/v2/calls/${enc(id)}`, { signal }) });

export const notesQuery = (id: string) =>
  queryOptions({ queryKey: callKeys.notes(id), queryFn: ({ signal }) => api.get<CallNotes>(`/api/v2/calls/${enc(id)}/notes`, { signal }) });

/**
 * The thread a call with these actors lives in: Tico's own room when nobody is named, the DM with one, a new group
 * with several. Then the call itself, with the agents in it.
 */
export async function startCall(withWho: readonly string[]): Promise<Call> {
  let thread: string;
  let bots = withWho.filter((a) => a.startsWith('bot:')).map((a) => a.slice(4));
  if (!withWho.length) {
    const tico = await api.get<Assistant>('/api/v2/assistant');
    if (!tico.room_id) throw new Error('Tico is not on yet');
    thread = tico.room_id;
    bots = [tico.bot];
  } else if (withWho.length === 1) {
    thread = (await api.get<ThreadView>(`/api/v2/threads/dm?with=${enc(withWho[0] as string)}&create=true`)).id;
  } else {
    thread = (await api.post<ThreadView>('/api/v2/threads/dm', { members: withWho })).id;
  }
  const call = await api.post<Call>(`/api/v2/threads/${enc(thread)}/calls`, { provider: 'tico', bots });
  queryClient.setQueryData(callKeys.one(call.id), call);
  return call;
}

export const joinCall = (id: string) => api.post<CallJoin>(`/api/v2/calls/${enc(id)}/join`, {});

export async function leaveCall(id: string): Promise<void> {
  const call = await api.post<Call>(`/api/v2/calls/${enc(id)}/leave`, {});
  queryClient.setQueryData(callKeys.one(id), call);
  void queryClient.invalidateQueries({ queryKey: callKeys.live });
}
