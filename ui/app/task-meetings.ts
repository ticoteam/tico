'use strict';

type TmMeeting = import('../types/api').TaskMeeting;
type TmPage = import('../types/api').TaskMeetings;
// The meetings a task's dialog lists: the rows read so far, the cursor of the next page, and the live changes that arrived
// while a read was out (taskMeetingsPatch applies them on top of the rows). opening is the dialog opening it was read for.
interface TmReading {
  rows: TmMeeting[];
  next: string | null;
  changes: Map<string, TmMeeting | null | undefined>;
  loading: boolean;
  opening?: unknown;
}
// The task dialog with its meetings slot (tmEl), the slot's reading (tmRead) and the opening counter the page keeps on it.
interface TmDialog extends TaskDialog {
  tmEl?: HTMLElement | null;
  tmRead?: TmReading | null;
  taskOpening?: unknown;
}
// A live event about a task: meeting_links lists the changed links when the server sends them.
interface TmLiveEvent {
  id?: unknown;
  kind?: string;
  kinds?: string[];
  meeting_links?: {meeting_id: string; meeting?: TmMeeting | null}[] | null;
}

function taskMeetingsRender(d: TmDialog) {
  const slot = d.tmEl, reading = d.tmRead;
  if (!slot?.isConnected || !reading) return;
  slot.hidden = !reading.rows.length;
  if (!$('.task-meetings-list', slot)) slot.innerHTML = '<h3 class="rail-h">Meetings</h3><ul class="task-meetings-list"></ul>';
  const list = $('.task-meetings-list', slot);
  const held = new Map([...list.children].map(row => [row.dataset.meetingId, row]));
  reading.rows.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  for (const meeting of reading.rows) {
    let row = held.get(meeting.meeting_id);
    held.delete(meeting.meeting_id);
    if (!row) { row = document.createElement('li'); row.dataset.meetingId = meeting.meeting_id; }
    const html = `<span class="title">${esc(meeting.title)}</span><time>${esc(String(meeting.started_at || '').slice(0, 10))}</time><a href="#/meetings?meeting=${encodeURIComponent(meeting.meeting_id)}">Open</a>`;
    if (row.innerHTML !== html) row.innerHTML = html;
    list.append(row);
  }
  for (const row of held.values()) row.remove();
  let more = $('[data-meetings-more]', slot);
  if (!reading.next) more?.remove();
  else {
    if (!more) { more = document.createElement('button'); more.type = 'button'; more.className = 'linkish'; more.dataset.meetingsMore = ''; more.textContent = 'More'; slot.append(more); }
    more.disabled = !!reading.loading;
    more.onclick = () => taskMeetingsLoad(d, reading.next).catch(error => toast(error.message, true));
  }
}
function taskMeetingsPatch(rows: TmMeeting[], changes: Map<string, TmMeeting | null | undefined>): TmMeeting[] {
  const byId = new Map(rows.map((row): [string, TmMeeting] => [row.meeting_id, row]));
  for (const [id, meeting] of changes) {
    if (meeting) byId.set(id, meeting);
    else byId.delete(id);
  }
  return [...byId.values()];
}
async function taskMeetingsLoad(d: TmDialog, cursor: string | null = null) {
  const slot = d.tmEl, reading = d.tmRead;
  if (!reading || reading.loading) return;
  if (!slot) throw new Error('The task meetings slot is missing.');
  reading.loading = true;
  reading.changes.clear();
  taskMeetingsRender(d);
  // A recovery read retains the number of pages the person expanded.
  const target = cursor ? 0 : Math.max(20, reading.rows.length);
  let rows: TmMeeting[] = cursor ? reading.rows : [], next = cursor;
  try {
    do {
      const query = new URLSearchParams({limit: '20'});
      if (next) query.set('cursor', next);
      const result = await get<TmPage>(`/v2/tasks/${encodeURIComponent(String(slot.dataset.task))}/meetings?${query}`);
      if (d.tmRead !== reading || !slot.isConnected) return;
      rows = taskMeetingsPatch([...rows, ...(result.meetings || [])], reading.changes);
      next = result.next_cursor;
    } while (!cursor && next && rows.length < target);
    reading.rows = rows; reading.next = next;
  } catch (error) {
    const status = errorStatus(error);
    if (d.tmRead === reading && (status === 403 || status === 404)) {
      reading.rows = []; reading.next = null;
    } else throw error;
  } finally {
    reading.loading = false;
    if (d.tmRead === reading) taskMeetingsRender(d);
  }
}
// Keep expanded rows when the task dialog redraws for an unrelated task change.
function taskMeetingsAdopt(d: TmDialog, task: {id: unknown}) {
  let slot = $('[data-task-meetings]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tmEl?.dataset.task === id && d.tmRead) {
    if (d.tmEl !== slot) slot.replaceWith(d.tmEl);
    if (d.tmRead.opening !== d.taskOpening) {
      d.tmRead = {...d.tmRead, opening: d.taskOpening, changes: new Map(), loading: false};
      taskMeetingsLoad(d).catch(() => {});
    }
    return;
  }
  d.tmEl = slot; slot.dataset.task = id;
  d.tmRead = {rows: [], next: null, changes: new Map(), loading: false, opening: d.taskOpening};
  taskMeetingsLoad(d).catch(() => {});
}

liveOn('tasks', (event: TmLiveEvent) => {
  if (event.kind !== 'meetings' && !event.kinds?.includes('meetings')) return;
  for (const dialog of document.querySelectorAll<TmDialog>('dialog[open][data-task]')) {
    if (String(event.id) !== dialog.dataset.task || !dialog.tmRead) continue;
    if (!Array.isArray(event.meeting_links)) {
      liveSoon('task-meetings:' + dialog.id, () => { if (dialog.open) taskMeetingsLoad(dialog).catch(() => {}); }, 200);
      continue;
    }
    const reading = dialog.tmRead;
    for (const change of event.meeting_links) reading.changes.set(change.meeting_id, change.meeting);
    reading.rows = taskMeetingsPatch(reading.rows, reading.changes);
    taskMeetingsRender(dialog);
  }
});
liveOn('reset', () => {
  for (const dialog of document.querySelectorAll<TmDialog>('dialog[open][data-task]')) {
    if (dialog.tmRead) { dialog.tmRead.changes.clear(); taskMeetingsLoad(dialog).catch(() => {}); }
  }
});
