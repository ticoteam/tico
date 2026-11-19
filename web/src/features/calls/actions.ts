// What a person (or Tico) can do on the Calendar, in a meeting's panel and on a call.
import type { Call, MeetingImporterList } from '@/api/schema';
import { defineAction, s, type Ctx } from '@/actions';
import { meQuery } from '@/data';
import { callWindowControls, desktop, isCallWindow } from '@/desktop';
import { VIEWS } from './model';
import {
  addItem,
  addToCall,
  callKeys,
  changeEvent,
  connectGranola,
  createEvent,
  disconnectGranola,
  dismissItem,
  editItem,
  editNote,
  endCall,
  importersQuery,
  joinCall,
  logMeeting,
  makeTask,
  meetKeys,
  removeEvent,
  reviewMeeting,
  setImporter,
  setMeetingSettings,
  startCall,
  startInThread,
  syncGranola,
  type MeetingItem,
  type MeetingItems,
  cancelCall,
  deleteMeeting,
  linkTask,
  moveCall,
  scheduleCall,
  sendMeeting,
  setMeetingPrivate,
  startTitled,
  unlinkTask,
} from './queries';
import { decline } from './ring';
import { claim, hear, join, leave, pickDevice, prime, setMuted, toggleMute } from './session';
import { clearStage, itemKey, keepItem, makeItemTask, setStage, takeOutAgent } from './queries';
import { callState, hideChat, setCaptions, shareScreen } from './session';

const id = (what: string) => s.string({ min: 1, max: 200, what });
const day = s.string({ pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a day, 2026-11-18' });
const when = s.string({ pattern: /^\d{4}-\d{2}-\d{2}T/, what: 'an ISO time, 2026-11-18T10:30:00Z' });
const person = s.string({ min: 3, max: 200, pattern: /^(human|bot):[^\s]+$/, what: 'human:<id> or bot:<id>' });
const guest = s.string({ min: 3, max: 200, pattern: /^((human|bot|team):[^\s]+|[^\s@]+@[^\s@]+)$/, what: 'human:<id>, bot:<id>, team:<id> or an email' });
const section = s.oneOf(['doc', 'task', 'feature'] as const);

type Search = Record<string, unknown>;
const toCalendar = ({ router }: Ctx, search: (prev: Search) => Search) => router.navigate({ to: '/calendar', search });
/** This window is the desktop app's small call window. */
const smallWindow = ({ router }: Ctx) => isCallWindow(router.state.location.pathname, router.state.location.searchStr);
// The small window is its call's page already: a navigation there would drop ?window=1 and draw the whole app in it.
const openCall = (ctx: Ctx, room: string) => (smallWindow(ctx) ? undefined : ctx.router.navigate({ to: '/call/$room', params: { room } }));
const PAGE = { kind: 'page', id: '/calendar' } as const;
const SOURCES = { kind: 'dialog', id: 'meeting-sources' } as const;

/* ---------------------------------------------------------------- the calendar */

const showInput = s.object({
  view: s.optional(s.oneOf(VIEWS)),
  date: s.optional(day),
  q: s.optional(s.string({ max: 200, what: 'words to find in List' })),
  review: s.optional(s.oneOf(['pending', 'dismissed', 'shared'] as const)),
});
const showRun = (ctx: Ctx, i: { view?: string; date?: string; q?: string; review?: string }) =>
  toCalendar(ctx, (prev) => {
    const next: Search = { ...prev, view: i.view ?? (i.q || i.review ? 'list' : prev.view), date: i.date ?? prev.date };
    if (i.q !== undefined) next.q = i.q || undefined;
    if (i.review !== undefined) next.review = i.review;
    return next;
  });

export const show = defineAction({
  id: 'calls.show',
  title: 'Show the calendar',
  kind: 'view',
  input: showInput,
  run: showRun,
  show: showRun,
  did: ({ view, q }) => (q ? `Searched meetings for ${q}` : view ? `Opened Calendar, ${view === '3day' ? '3 days' : view}` : 'Opened Calendar'),
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

export const editMeetingItem = defineAction({
  id: 'calls.editItem',
  title: 'Reword a meeting item',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), item: id('an item id'), text: s.string({ min: 1, max: 2000 }) }),
  run: (ctx, { meeting, item, text }) => editItem(meeting, itemOf(ctx, meeting, item), text),
  did: () => 'Changed it',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const editNotes = defineAction({
  id: 'calls.editNote',
  title: 'Write notes on a meeting',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), note: s.string({ max: 20000 }) }),
  run: (_ctx, { meeting, note }) => editNote(meeting, note),
  did: () => 'Saved the notes',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const review = defineAction({
  id: 'calls.review',
  title: 'Share or put away a meeting',
  kind: 'change',
  input: s.object({ meeting: id('a meeting id'), action: s.oneOf(['approve', 'dismiss', 'restore'] as const) }),
  // Sharing shows a meeting only the person saw to the whole company: their own press.
  can: (ctx, { action }) => !(ctx.via === 'tico' && action === 'approve'),
  run: (_ctx, { meeting, action }) => reviewMeeting(meeting, action),
  did: ({ action }) => (action === 'approve' ? 'Shared the meeting' : action === 'dismiss' ? 'Put the meeting away' : 'Brought the meeting back'),
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

/** A tab opened inside the press (a pop-up blocker lets that one through); it goes to the Meet once the server answers. */
function blankTab(): Window | null {
  try {
    const tab = window.open('', '_blank');
    if (tab) tab.opener = null;
    return tab;
  } catch {
    return null;
  }
}
function sendTab(tab: Window | null, url: string | null | undefined): void {
  if (!url) return void tab?.close();
  if (tab && !tab.closed) tab.location.href = url;
  else window.open(url, '_blank', 'noopener');
}
/** A Google Meet: the server notes the join and answers its link, which opens in the tab the press made. */
async function intoMeet(tab: Window | null, run: () => Promise<{ url?: string | null; call?: Call }>, fallback?: string | null): Promise<void> {
  try {
    const r = await run();
    sendTab(tab, r.url || r.call?.join_url || fallback);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

/** Start a call in a thread: a voice call rings its people and brings in the agents picked; a Google Meet takes people only. */
export const startInThreadAction = defineAction({
  id: 'calls.startIn',
  title: 'Start a call in a thread',
  kind: 'change',
  tico: false,
  input: s.object({ thread: id('a thread id'), meet: s.optional(s.boolean()), agents: s.optional(s.array(person, { max: 50 })) }),
  run: async (ctx, { thread, meet, agents = [] }) => {
    if (meet) {
      const tab = blankTab();
      let call: Call | undefined;
      await intoMeet(tab, async () => {
        call = await startInThread(thread, 'google_meet', []);
        return { call };
      });
      return call;
    }
    prime(null);
    const bots = agents.filter((a) => a.startsWith('bot:')).map((a) => a.slice(4));
    const call = await startInThread(thread, 'tico', bots).catch((e: unknown) => {
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

export const endCallAction = defineAction({
  id: 'calls.end',
  title: 'End a call for everyone',
  kind: 'change',
  input: s.object({ id: id('a call id') }),
  // For everyone at once: a started call by its starter only (calls.may_end), for Tico as for a click.
  can: (ctx, { id }) => {
    const c = ctx.queryClient.getQueryData<Call>(callKeys.one(id));
    const me = ctx.queryClient.getQueryData(meQuery.queryKey)?.id;
    return !c || c.state === 'scheduled' || !me || c.connector === me;
  },
  run: (_ctx, { id }) => endCall(id),
  did: () => 'Ended the call',
});

export const joinCallAction = defineAction({
  id: 'calls.join',
  title: 'Join a call',
  kind: 'change',
  tico: false,
  input: s.object({ id: id('a call id') }),
  run: async (ctx, { id }) => {
    const known = ctx.queryClient.getQueryData<Call>(callKeys.one(id));
    if (known?.provider === 'google_meet') return intoMeet(blankTab(), () => joinCall(id), known.join_url);
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

/** The phone's lip and the bar press this: mute if on, unmute if off. */
export const toggleMuteAction = defineAction({
  id: 'calls.toggleMute',
  title: 'Mute or unmute',
  kind: 'change',
  tico: false,
  input: s.object({}),
  run: () => toggleMute(),
});

export const addPeople = defineAction({
  id: 'calls.add',
  title: 'Add to a call',
  kind: 'change',
  input: s.object({ id: id('a call id'), with: s.array(person, { max: 50 }) }),
  run: (_ctx, i) => addToCall(i.id, i.with),
  did: ({ with: w }) => (w.length === 1 ? 'Added one to the call' : `Added ${w.length} to the call`),
  target: ({ id }) => ({ kind: 'page', id: `/call/${id}` }),
});

export const declineAction = defineAction({
  id: 'calls.decline',
  title: 'Decline a call',
  kind: 'change',
  input: s.object({ id: id('a call id') }),
  run: (_ctx, { id }) => decline(id),
  did: () => 'Declined the call',
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

/* ---------------------------------------------------------------- the desktop app's small call window (platforms.md 4.6)
   Moving the call into it is the shell's `shell.openCallWindow`. These two live in the small window itself. */

const NO_WINDOW = 'not_here: only the desktop app’s small call window';

export const keepOnTop = defineAction({
  id: 'calls.keepOnTop',
  title: 'Keep the call window on top',
  kind: 'view',
  tico: false,
  input: s.object({ on: s.boolean() }),
  can: (ctx) => !!callWindowControls() && smallWindow(ctx),
  run: async (_ctx, { on }) => {
    const w = callWindowControls();
    if (!w) throw new Error(NO_WINDOW);
    await w.setOnTop(on);
  },
  did: ({ on }) => (on ? 'Kept the call on top' : 'Let the call go behind'),
});

/** The small window closes and the main window takes the call again: the room moves, so this one leaves it first and
    the main window joins on hearing 'return'. */
export const backToApp = defineAction({
  id: 'calls.backToApp',
  title: 'Move the call back to the app',
  kind: 'view',
  tico: false,
  input: s.object({}),
  can: (ctx) => !!desktop() && smallWindow(ctx),
  run: async () => {
    const t = desktop();
    if (!t) throw new Error(NO_WINDOW);
    await leave();
    // 'return': the main window takes the call; a plain close() would end as 'closed' (left)
    await t.callWindow.close('return');
  },
  did: () => 'Moved the call back to the app',
});

/* ---------------------------------------------------------------- meetings brought in: logged, Granola, settings */

export const logMeetingAction = defineAction({
  id: 'calls.logMeeting',
  title: 'Log a meeting',
  kind: 'change',
  input: s.object({
    title: s.optional(s.string({ max: 300, what: 'a title' })),
    start: s.optional(when),
    with: s.optional(s.array(s.string({ min: 1, max: 200, what: 'a name or an email' }), { max: 100 })),
    notes: s.optional(s.string({ max: 200_000, what: 'notes, in markdown' })),
    transcript: s.optional(s.string({ max: 2_000_000, what: 'a transcript: Name: text, WebVTT, SRT or JSON' })),
    private: s.optional(s.boolean()),
  }),
  run: async (ctx, i) => {
    if (!i.notes?.trim() && !i.transcript?.trim()) throw new Error('empty: add notes or a transcript');
    const m = await logMeeting({
      title: i.title?.trim() || undefined,
      started_at: i.start,
      participants: i.with?.length ? i.with : undefined,
      notes: i.notes?.trim() || undefined,
      transcript: i.transcript?.trim() ? i.transcript : undefined,
      private: i.private || undefined,
    });
    if (m.id) await toCalendar(ctx, (prev) => ({ ...prev, view: 'list', panel: `meeting:${m.id}` }));
    return m;
  },
  did: ({ title }) => (title ? `Logged ${title}` : 'Logged a meeting'),
  target: (_i, m) => (m?.id ? { kind: 'meeting', id: m.id } : PAGE),
});

const share = s.oneOf(['default', 'review', 'auto'] as const);
export const meetingSettingsAction = defineAction({
  id: 'calls.meetingSettings',
  title: 'Change meeting settings',
  kind: 'change',
  input: s.object({ mine: share, team: s.optional(s.oneOf(['review', 'auto'] as const)) }),
  // Sharing at once shows every meeting brought in to the company without a look first: the person's own press.
  can: (ctx, { mine, team }) => !(ctx.via === 'tico' && (mine === 'auto' || team === 'auto')),
  run: (_ctx, { mine, team }) => setMeetingSettings({ auto_share: mine === 'default' ? null : mine === 'auto', ...(team ? { review_default: team } : {}) }),
  did: ({ mine }) => (mine === 'auto' ? 'Set meetings to share at once' : mine === 'review' ? 'Set meetings to wait for review' : 'Set meetings to the company default'),
});

// Signing in to Granola is the person's own: the code it gives must reach only their screen.
export const connectGranolaAction = defineAction({
  id: 'calls.connectGranola',
  title: 'Connect Granola',
  kind: 'change',
  tico: false,
  input: s.object({}),
  run: () => connectGranola(),
  did: () => 'Started connecting Granola',
});

/** Turn a meeting importer on or off, or move it to another computer. Left out, the computer stays, or is the first online one. */
export const setImporterAction = defineAction({
  id: 'calls.setImporter',
  title: 'Turn a meeting importer on or off',
  kind: 'change',
  input: s.object({
    source: id('an importer: zoom, google-meet or granola'),
    on: s.boolean(),
    computer: s.optional(id('a computer id from the importers list')),
  }),
  can: ({ queryClient }) => queryClient.getQueryData(meQuery.queryKey)?.role === 'owner',
  run: async ({ queryClient }, { source, on, computer }) => {
    const d = queryClient.getQueryData<MeetingImporterList>(importersQuery.queryKey) ?? (await queryClient.fetchQuery(importersQuery));
    const now = d.importers.find((i) => i.source === source);
    if (!now) throw new Error('not_found: no such importer');
    const runner = computer ?? (now.runner_id || (d.computers.find((c) => c.online) ?? d.computers[0])?.id || '');
    if (on && !runner) throw new Error('no_computer: add a computer first');
    return setImporter(source, { enabled: on, runner_id: runner });
  },
  did: ({ on }) => (on ? 'Turned the importer on' : 'Turned the importer off'),
  target: () => SOURCES,
});

export const syncGranolaAction = defineAction({
  id: 'calls.syncGranola',
  title: 'Sync Granola now',
  kind: 'change',
  input: s.object({}),
  run: () => syncGranola(),
  did: () => 'Asked Granola for new notes',
});

export const disconnectGranolaAction = defineAction({
  id: 'calls.disconnectGranola',
  title: 'Remove Granola',
  kind: 'change',
  input: s.object({}),
  // Only a person can connect it again (above), so removing it is the person's own press too.
  tico: false,
  run: () => disconnectGranola(),
  did: () => 'Removed Granola',
});

/* ---------------------------------------------------------------- a meeting's details: who sees it, its agent, its tasks */

const meetingId = id('a meeting id');

export const setPrivateAction = defineAction({
  id: 'calls.setPrivate',
  title: 'Make a meeting private or the team’s',
  kind: 'change',
  input: s.object({ meeting: meetingId, private: s.boolean() }),
  // Opening a meeting to the whole company is the person's own press; keeping it private is not.
  can: (ctx, i) => !(ctx.via === 'tico' && !i.private),
  run: (_ctx, i) => setMeetingPrivate(i.meeting, i.private),
  did: (i) => (i.private ? 'Made the meeting private' : 'Showed the meeting to the team'),
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const sendMeetingAction = defineAction({
  id: 'calls.sendMeeting',
  title: 'Send a meeting to an agent',
  kind: 'change',
  input: s.object({ meeting: meetingId, agent: s.string({ min: 5, max: 200, pattern: /^bot:[^\s]+$/, what: 'bot:<id>' }) }),
  run: (_ctx, { meeting, agent }) => sendMeeting(meeting, agent.slice(4)),
  did: () => 'Sent the meeting to the agent',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const deleteMeetingAction = defineAction({
  id: 'calls.deleteMeeting',
  title: 'Delete a meeting',
  kind: 'change',
  // It cannot be undone from the app: the person's own press.
  tico: false,
  input: s.object({ meeting: meetingId }),
  run: async (ctx, { meeting }) => {
    await deleteMeeting(meeting);
    await toCalendar(ctx, ({ panel: _, ...rest }) => rest);
  },
  did: () => 'Deleted the meeting',
  target: () => PAGE,
});

/** A task's number ("#123", "123"), its id, or its link (".../task/<id>"). */
const taskRef = (raw: string): string => {
  const t = raw.trim();
  const linked = /\/task\/([^/?#]+)/.exec(t);
  return linked ? decodeURIComponent(linked[1] as string) : t.replace(/^#/, '');
};

export const linkTaskAction = defineAction({
  id: 'calls.linkTask',
  title: 'Link a task to a meeting',
  kind: 'change',
  input: s.object({ meeting: meetingId, task: s.string({ min: 1, max: 500, what: 'a task: #123, its id or its link' }) }),
  run: (_ctx, { meeting, task }) => linkTask(meeting, taskRef(task)),
  did: () => 'Linked the task',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

export const unlinkTaskAction = defineAction({
  id: 'calls.unlinkTask',
  title: 'Unlink a task from a meeting',
  kind: 'change',
  input: s.object({ meeting: meetingId, task: id('a task id') }),
  run: (_ctx, { meeting, task }) => unlinkTask(meeting, task),
  did: () => 'Unlinked the task',
  target: ({ meeting }) => ({ kind: 'meeting', id: meeting }),
});

/* ---------------------------------------------------------------- booked calls */

const callWith = s.array(person, { max: 50 });

/** A voice call now, from New call: its title kept. The microphone opens on the person's own press. */
export const startNowAction = defineAction({
  id: 'calls.startNow',
  title: 'Start a voice call now',
  kind: 'change',
  tico: false,
  input: s.object({ with: callWith, title: s.optional(s.string({ max: 200 })) }),
  run: async (ctx, i) => {
    prime(null);
    const call = await startTitled(i.with, i.title?.trim() ?? '').catch((e: unknown) => {
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

export const scheduleAction = defineAction({
  id: 'calls.schedule',
  title: 'Book a voice call',
  kind: 'change',
  input: s.object({
    with: callWith,
    title: s.optional(s.string({ max: 200 })),
    start: when,
    minutes: s.number({ int: true, min: 5, max: 1440 }),
  }),
  run: (_ctx, i) => {
    if (!i.with.length) throw new Error('empty: pick who the call is with');
    const at = new Date(i.start);
    if (at.getTime() < Date.now() - 60_000) throw new Error('past: pick a time from now on');
    return scheduleCall(i.with, i.title?.trim() ?? '', at, i.minutes);
  },
  did: ({ title }) => (title?.trim() ? `Booked ${title.trim()}` : 'Booked the call'),
  target: () => PAGE,
});

export const moveCallAction = defineAction({
  id: 'calls.moveCall',
  title: 'Change a booked call’s time',
  kind: 'change',
  input: s.object({ id: id('a call id'), start: when, minutes: s.number({ int: true, min: 5, max: 1440 }) }),
  run: (_ctx, i) => moveCall(i.id, new Date(i.start), i.minutes),
  did: () => 'Changed the call’s time',
  target: () => PAGE,
});

export const cancelCallAction = defineAction({
  id: 'calls.cancel',
  title: 'Cancel a booked call',
  kind: 'change',
  input: s.object({ id: id('a call id') }),
  run: async (ctx, { id }) => {
    await cancelCall(id);
    await toCalendar(ctx, ({ panel: _, ...rest }) => rest);
  },
  did: () => 'Canceled the call',
  target: () => PAGE,
});

/* ---------------------------------------------------------------- on the call: show, agents, captions, items after it */

const cachedCall = (ctx: Ctx, id: string) => ctx.queryClient.getQueryData<Call>(callKeys.one(id));
const myId = (ctx: Ctx) => ctx.queryClient.getQueryData(meQuery.queryKey)?.id;

export const shareAction = defineAction({
  id: 'calls.share',
  title: 'Share my screen on the call, or stop',
  kind: 'change',
  // It sends what is on the person's own screen to everyone in the call, from the browser's picker: their own press.
  tico: false,
  input: s.object({ id: id('a call id'), on: s.boolean() }),
  run: async (ctx, { id, on }) => {
    if (!on) return stopShowing(ctx, id);
    if (!(await shareScreen(true))) return;
    await setStage(id, { kind: 'screen' }).catch(async (e: unknown) => {
      await shareScreen(false);
      throw e;
    });
  },
  did: ({ on }) => (on ? 'Shared the screen' : 'Stopped sharing'),
});

export const showFileAction = defineAction({
  id: 'calls.showFile',
  title: 'Show a Tico file to everyone on the call',
  kind: 'change',
  input: s.object({ id: id('a call id'), file: id('a file id') }),
  run: async (_ctx, { id, file }) => {
    await setStage(id, { kind: 'file', file_id: file });
    // one thing at a time: a file in place of my screen
    await shareScreen(false);
  },
  did: () => 'Showed the file',
  target: ({ id }) => ({ kind: 'page', id: `/call/${id}` }),
});

/** My screen stops, then what I showed (or, for the starter, what anyone showed) leaves the stage. */
async function stopShowing(ctx: Ctx, id: string): Promise<void> {
  await shareScreen(false);
  const c = cachedCall(ctx, id);
  const me = callState().me ?? myId(ctx);
  if (c?.stage && (c.stage.by === me || c.connector === me)) await clearStage(id);
}

export const stopShowAction = defineAction({
  id: 'calls.stopShow',
  title: 'Stop showing on the call',
  kind: 'change',
  input: s.object({ id: id('a call id') }),
  run: (ctx, { id }) => stopShowing(ctx, id),
  did: () => 'Stopped showing',
});

export const captionsAction = defineAction({
  id: 'calls.captions',
  title: 'Show or hide captions in the call bar',
  kind: 'view',
  input: s.object({ on: s.boolean() }),
  run: (_ctx, { on }) => setCaptions(on),
  did: ({ on }) => (on ? 'Captions on' : 'Captions off'),
});

export const takeOutAction = defineAction({
  id: 'calls.takeOut',
  title: 'Take an agent out of the call',
  kind: 'change',
  input: s.object({ id: id('a call id'), agent: id('the agent’s id in the call') }),
  run: (_ctx, { id, agent }) => takeOutAgent(id, agent),
  did: () => 'Took the agent out',
  target: ({ id }) => ({ kind: 'page', id: `/call/${id}` }),
});

const item = { id: id('a call id'), owner: s.string({ max: 200 }), title: s.string({ min: 1, max: 2000 }) };

export const makeItemTaskAction = defineAction({
  id: 'calls.makeItemTask',
  title: 'Make a task from a call’s action item',
  kind: 'change',
  input: s.object(item),
  run: async (ctx, i) => {
    const task = await makeItemTask(cachedCall(ctx, i.id)?.meeting_id ?? null, i.title, i.owner || myId(ctx) || '');
    keepItem(i.id, itemKey(i), task.id);
    return task;
  },
  did: (i) => `Made ${i.title}`,
});

export const hideItemAction = defineAction({
  id: 'calls.hideItem',
  title: 'Hide a call’s action item',
  kind: 'view',
  input: s.object(item),
  run: (_ctx, i) => keepItem(i.id, itemKey(i), ''),
  did: () => 'Hid the item',
});

export const hideChatAction = defineAction({
  id: 'calls.hideChat',
  title: 'Close the call’s chat beside the page',
  kind: 'view',
  input: s.object({}),
  run: () => hideChat(),
  did: () => 'Closed the chat',
});
