// Every server call the calendar, meetings and calls make, and every query they read.
import { queryOptions, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  Assistant,
  CalendarEvents,
  CalendarOccurrence,
  CalendarSearch as CalendarFound,
  Call,
  CallJoin,
  CallList,
  CallLive,
  CallNotes,
  CallRing,
  CallRinging,
  EventChange,
  EventCreate,
  GranolaDevice,
  GranolaDisconnect,
  GranolaSignIn,
  GranolaStatus,
  GranolaSync,
  ImportMeetingBody,
  ImporterConfig,
  MeetingImporter,
  MeetingImporterList,
  MeetingImporterSaved,
  MeetingReviewPreferences,
  MeetingSourceList,
  ReviewSettings,
  ThreadView,
} from '@/api/schema';
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
  shown: (review: Review, q: string) => ['meetings', 'list', review, q] as const,
  one: (id: string) => ['meetings', id] as const,
  items: (id: string) => ['meetings', id, 'items'] as const,
  live: (id: string) => ['meetings', id, 'live'] as const,
};
export const callKeys = {
  live: ['calls', 'live'] as const,
  one: (id: string) => ['calls', id] as const,
  notes: (id: string) => ['calls', id, 'notes'] as const,
  ringing: ['calls', 'ringing'] as const,
  /** A thread's calls. Under `live` on purpose: every `call.*` change reads ['calls', 'live'] again, and this with it. */
  inThread: (thread: string) => ['calls', 'live', 'thread', thread] as const,
};

// The calendar topic refetches ['calendar'] and a followed meeting refetches ['meetings', id]; the list of past meetings has
// no topic of its own, so it is read again after the person's own writes and on reconnect. A call, the live list and the
// ringing list are read again on a `call.*` change in the threads topic (live/patch.ts) and when the room says someone
// came or went (session.ts); its transcript comes on the live meeting's own stream (followTranscript).
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
  /** What the meeting's source wrote. */
  notes?: string | null;
  /** The person's own notes, edited in place. */
  note?: string | null;
  version?: number;
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
/** The person's own notes on a meeting. `version` is the meeting's: a note changed elsewhere since is refused. */
export function editNote(id: string, note: string): Promise<Meeting> {
  const version = queryClient.getQueryData<Meeting>(meetKeys.one(id))?.version;
  return write({
    key: meetKeys.one(id),
    optimistic: (d: Meeting | undefined) => d && { ...d, note },
    send: (idempotencyKey) => api.post<Meeting>(`/api/v2/meetings/${enc(id)}/edit`, { note, version }, { idempotencyKey }),
    settle: (_d: Meeting | undefined, m: Meeting) => m,
  });
}

/** A live meeting's transcript so far (GET /api/v2/live-meetings/{id}); `event_id` is where its stream goes on from. */
export interface LiveTranscript {
  chunks: Chunk[];
  event_id?: number;
}
export interface Chunk {
  seq: number;
  revision?: number;
  speaker?: string | null;
  text: string;
  start_ms?: number | null;
}

/**
 * Which meetings List shows: shared ones (the server's default), the person's own to review, those they put away, or
 * the shared ones someone else filed (`shared`).
 */
export type Review = 'live' | 'pending' | 'dismissed' | 'shared';

export const meetingsQuery = (review: Review = 'live', q = '') =>
  queryOptions({
    queryKey: meetKeys.shown(review, q),
    queryFn: ({ signal }) => api.get<MeetingList>(`/api/v2/meetings?review=${review}${q ? `&q=${enc(q)}` : ''}`, { signal }),
  });

/** Calendar events whose title or people match (List's search). */
export const findEventsQuery = (q: string) =>
  queryOptions({
    queryKey: [...calKeys.all, 'search', q],
    queryFn: ({ signal }) => api.get<CalendarFound>(`/api/v2/calendar/search?q=${enc(q)}`, { signal }),
  });

/** Share a meeting the person kept back, put it away, or bring it back. */
export async function reviewMeeting(id: string, action: 'approve' | 'dismiss' | 'restore'): Promise<Meeting> {
  const m = await write({
    key: meetKeys.one(id),
    optimistic: (d: Meeting | undefined) => d && { ...d, review_state: action === 'dismiss' ? 'dismissed' : action === 'approve' ? 'live' : 'pending' },
    send: (idempotencyKey) => api.post<Meeting>(`/api/v2/meetings/${enc(id)}/review`, { action }, { idempotencyKey }),
    settle: (_d: Meeting | undefined, m: Meeting) => m,
  });
  void queryClient.invalidateQueries({ queryKey: meetKeys.list });
  return m;
}

export const meetingQuery = (id: string) =>
  queryOptions({ queryKey: meetKeys.one(id), queryFn: ({ signal }) => api.get<Meeting>(`/api/v2/meetings/${enc(id)}`, { signal }) });

export const itemsQuery = (id: string) =>
  queryOptions({ queryKey: meetKeys.items(id), queryFn: ({ signal }) => api.get<MeetingItems>(`/api/v2/meetings/${enc(id)}/items`, { signal }) });

export const transcriptQuery = (meetingId: string) =>
  queryOptions({
    queryKey: meetKeys.live(meetingId),
    queryFn: ({ signal }) => api.get<LiveTranscript>(`/api/v2/live-meetings/${enc(meetingId)}`, { signal }),
  });

/**
 * Follow a live meeting's stream from `after`: each line said, new or corrected, goes into the transcript in place (a
 * later revision wins); an agent coming or going, or the meeting's state, reads the call again. Returns the close.
 */
export function followTranscript(meetingId: string, after: number, callId: string): () => void {
  if (typeof EventSource === 'undefined') return () => undefined;
  const source = new EventSource(`/api/v2/live-meetings/${enc(meetingId)}/events?after=${after}`);
  const put = (ev: Event) => {
    let c: Partial<Chunk>;
    try {
      c = JSON.parse(String((ev as MessageEvent).data || '{}')) as Partial<Chunk>;
    } catch {
      return;
    }
    if (typeof c.seq !== 'number') return;
    const chunk: Chunk = {
      seq: c.seq,
      revision: Number(c.revision) || 1,
      speaker: c.speaker ?? null,
      text: String(c.text ?? ''),
      start_ms: c.start_ms ?? null,
    };
    queryClient.setQueryData<LiveTranscript>(meetKeys.live(meetingId), (d) => {
      const list = d?.chunks ?? [];
      const had = list.find((x) => x.seq === chunk.seq);
      if (had && (had.revision ?? 1) > (chunk.revision ?? 1)) return d;
      const next = had ? list.map((x) => (x.seq === chunk.seq ? chunk : x)) : [...list, chunk].sort((a, b) => a.seq - b.seq);
      return { ...d, chunks: next };
    });
  };
  source.addEventListener('meeting.chunk', put);
  source.addEventListener('meeting.chunk_corrected', put);
  for (const type of ['meeting.bot_joined', 'meeting.bot_left', 'meeting.state', 'meeting.finalized']) source.addEventListener(type, () => freshCall(callId));
  source.addEventListener('expired', () => source.close());
  return () => source.close();
}

/** A note or agenda line, reworded. */
export function editItem(meeting: string, item: MeetingItem, text: string): Promise<MeetingItem> {
  return write({
    key: meetKeys.items(meeting),
    optimistic: (d: MeetingItems | undefined) => mapSection(d, item.section, (l) => l.map((x) => (x.id === item.id ? { ...x, text } : x))),
    send: (idempotencyKey) =>
      api.post<MeetingItem>(`/api/v2/meetings/${enc(meeting)}/items/${enc(item.id)}`, { text, version: item.version }, { idempotencyKey }),
    settle: (d: MeetingItems | undefined, it: MeetingItem) => mapSection(d, item.section, (l) => l.map((x) => (x.id === item.id ? { ...x, ...it } : x))),
  });
}

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

/** Calls that ring the person now: rung, not yet in, still on. */
export const ringingQuery = queryOptions({
  queryKey: callKeys.ringing,
  queryFn: ({ signal }) => api.get<CallRinging>('/api/v2/calls/ringing', { signal }),
});

/** Read a call again (and the lists it shows in): someone came, went, or the room closed. */
export function freshCall(id: string): void {
  void queryClient.invalidateQueries({ queryKey: callKeys.one(id), exact: true });
  void queryClient.invalidateQueries({ queryKey: callKeys.live });
}

/** Bring someone into a call that is on: a person is rung, an agent joins (the server takes its id or slug). */
export async function addToCall(id: string, actors: readonly string[]): Promise<void> {
  const agents = actors.filter((a) => a.startsWith('bot:')).map((a) => a.slice(4));
  const people = actors.filter((a) => !a.startsWith('bot:'));
  await Promise.all([
    ...people.map((actor) => api.post<CallRing>(`/api/v2/calls/${enc(id)}/ring`, { actor })),
    ...(agents.length
      ? [api.post<Call>(`/api/v2/calls/${enc(id)}/bots`, { bots: agents }).then((c) => void queryClient.setQueryData(callKeys.one(id), c))]
      : []),
  ]);
  freshCall(id);
}

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

/** A thread's latest call, its soonest booked one, and which kinds of call the person may start there. */
export const threadCallsQuery = (thread: string) =>
  queryOptions({
    queryKey: callKeys.inThread(thread),
    queryFn: async ({ signal }): Promise<CallList> => {
      const d = await api.get<Partial<CallList>>(`/api/v2/threads/${enc(thread)}/calls?limit=1`, { signal });
      // A reply can leave out its lists: read as none.
      return { available: !!d.available, calls: d.calls ?? [], next_before: d.next_before ?? null, next_scheduled: d.next_scheduled ?? null, providers: d.providers ?? [] };
    },
  });

/** A call in this thread: a voice call (Tico Meet) with these agents, or a Google Meet (people only). */
export async function startInThread(thread: string, provider: 'tico' | 'google_meet', bots: readonly string[]): Promise<Call> {
  const call = await api.post<Call>(`/api/v2/threads/${enc(thread)}/calls`, provider === 'tico' && bots.length ? { provider, bots } : { provider });
  queryClient.setQueryData(callKeys.one(call.id), call);
  void queryClient.invalidateQueries({ queryKey: callKeys.live });
  return call;
}

/** End a Google Meet for everyone: whoever started it may. */
export async function endCall(id: string): Promise<Call> {
  const call = await api.post<Call>(`/api/v2/calls/${enc(id)}/end`, {});
  queryClient.setQueryData(callKeys.one(id), call);
  void queryClient.invalidateQueries({ queryKey: callKeys.live });
  return call;
}

export const joinCall = (id: string) => api.post<CallJoin>(`/api/v2/calls/${enc(id)}/join`, {});

export async function leaveCall(id: string): Promise<void> {
  const call = await api.post<Call>(`/api/v2/calls/${enc(id)}/leave`, {});
  queryClient.setQueryData(callKeys.one(id), call);
  void queryClient.invalidateQueries({ queryKey: callKeys.live });
}

/* ---------------------------------------------------------------- meeting sources and settings */

const sourceKeys = {
  granola: ['meetings', 'sources', 'granola'] as const,
  list: ['meetings', 'sources', 'list'] as const,
  importers: ['meetings', 'sources', 'importers'] as const,
  settings: ['meetings', 'settings'] as const,
};

/** Every source and how it is doing: Import, Close and each importer that is on. Anyone may read it. */
export const sourcesQuery = queryOptions({
  queryKey: sourceKeys.list,
  queryFn: ({ signal }) => api.get<MeetingSourceList>('/api/v2/meetings/sources', { signal }),
});

/** Every importer, on or off, with the computer that runs it, and the computers that may. The owner's read only. */
export const importersQuery = queryOptions({
  queryKey: sourceKeys.importers,
  queryFn: ({ signal }) => api.get<MeetingImporterList>('/api/v2/meeting-importers', { signal }),
});

/** An importer as it will be: off, or on with a computer (a new computer starts over at waiting, as the server does). */
function configured(i: MeetingImporter, change: Required<ImporterConfig>, computers: MeetingImporterList['computers']): MeetingImporter {
  const same = i.enabled && i.runner_id === change.runner_id;
  return {
    ...i,
    enabled: change.enabled,
    runner_id: change.runner_id,
    runner_label: computers.find((c) => c.id === change.runner_id)?.label ?? '',
    status: !change.enabled ? 'off' : same ? i.status : 'waiting',
  };
}

/** Turn an importer on or off, or move it to another computer. Its credentials stay in a file on that computer. */
export async function setImporter(source: string, change: Required<ImporterConfig>): Promise<MeetingImporterSaved> {
  const r = await write({
    key: sourceKeys.importers,
    optimistic: (d: MeetingImporterList | undefined) =>
      d && { ...d, importers: d.importers.map((i) => (i.source === source ? configured(i, change, d.computers) : i)) },
    send: (idempotencyKey) => api.post<MeetingImporterSaved>(`/api/v2/meeting-importers/${enc(source)}`, change, { idempotencyKey }),
  });
  void queryClient.invalidateQueries({ queryKey: sourceKeys.list });
  return r;
}

/** The person's own Granola account: connected or not, last sync, how many notes it brought. */
export const granolaQuery = queryOptions({
  queryKey: sourceKeys.granola,
  queryFn: ({ signal }) => api.get<GranolaStatus>('/api/v2/meetings/granola', { signal }),
});

/**
 * Start a Granola sign-in: a short code the person enters in Granola. The code lives on the dialog that shows it, never
 * in the query cache.
 */
export const connectGranola = () => api.post<GranolaDevice>('/api/v2/meetings/granola/connect', {});

/** Where a sign-in stands: `pending` until it connects, is denied or expires. */
export async function pollGranola(): Promise<GranolaSignIn> {
  const r = await api.get<GranolaSignIn>('/api/v2/meetings/granola/connect/status');
  if (r.state !== 'pending') void queryClient.invalidateQueries({ queryKey: sourceKeys.granola });
  return r;
}

export async function disconnectGranola(): Promise<void> {
  await api.delete<GranolaDisconnect>('/api/v2/meetings/granola/connect');
  await queryClient.invalidateQueries({ queryKey: sourceKeys.granola });
}

/** Bring new Granola notes in now. */
export async function syncGranola(): Promise<GranolaSync> {
  const r = await api.post<GranolaSync>('/api/v2/meetings/granola/sync?now=true', {});
  void queryClient.invalidateQueries({ queryKey: sourceKeys.granola });
  void queryClient.invalidateQueries({ queryKey: meetKeys.list });
  return r;
}

/** Whether the person's imported meetings wait for review or are shared at once, and the company's default. */
export const meetingSettingsQuery = queryOptions({
  queryKey: sourceKeys.settings,
  queryFn: ({ signal }) => api.get<MeetingReviewPreferences>('/api/v2/meetings/settings', { signal }),
});

export async function setMeetingSettings(body: ReviewSettings): Promise<MeetingReviewPreferences> {
  const r = await api.post<MeetingReviewPreferences>('/api/v2/meetings/settings', body);
  queryClient.setQueryData(sourceKeys.settings, r);
  return r;
}

/** A meeting logged by hand: notes, a transcript, or both. Shared at once, unless private. */
export interface LoggedMeeting {
  title?: string;
  started_at?: string;
  participants?: string[];
  notes?: string;
  transcript?: string;
  private?: boolean;
}
export async function logMeeting(m: LoggedMeeting): Promise<{ id: string }> {
  const body: ImportMeetingBody = { ...m, source: m.transcript ? 'upload' : 'manual', review: 'live' };
  const r = await api.post<{ id: string }>('/api/v2/meetings/import', body);
  void queryClient.invalidateQueries({ queryKey: meetKeys.list });
  void queryClient.invalidateQueries({ queryKey: calKeys.all });
  return r;
}
