'use strict';

function taskMeetingsRender(d) {
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
function taskMeetingsPatch(rows, changes) {
  const byId = new Map(rows.map(row => [row.meeting_id, row]));
  for (const [id, meeting] of changes) {
    if (meeting) byId.set(id, meeting);
    else byId.delete(id);
  }
  return [...byId.values()];
}
async function taskMeetingsLoad(d, cursor = null) {
  const slot = d.tmEl, reading = d.tmRead;
  if (!reading || reading.loading) return;
  reading.loading = true;
  reading.changes.clear();
  taskMeetingsRender(d);
  // A recovery read retains the number of pages the person expanded.
  const target = cursor ? 0 : Math.max(20, reading.rows.length);
  let rows = cursor ? reading.rows : [], next = cursor;
  try {
    do {
      const query = new URLSearchParams({limit: '20'});
      if (next) query.set('cursor', next);
      const result = await get(`/v2/tasks/${encodeURIComponent(slot.dataset.task)}/meetings?${query}`);
      if (d.tmRead !== reading || !slot.isConnected) return;
      rows = taskMeetingsPatch([...rows, ...(result.meetings || [])], reading.changes);
      next = result.next_cursor;
    } while (!cursor && next && rows.length < target);
    reading.rows = rows; reading.next = next;
  } catch (error) {
    if (d.tmRead === reading && [403, 404].includes(error.status)) {
      reading.rows = []; reading.next = null;
    } else throw error;
  } finally {
    reading.loading = false;
    if (d.tmRead === reading) taskMeetingsRender(d);
  }
}
// Keep expanded rows when the task dialog redraws for an unrelated task change.
function taskMeetingsAdopt(d, task) {
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

liveOn('tasks', event => {
  if (event.kind !== 'meetings' && !event.kinds?.includes('meetings')) return;
  for (const dialog of document.querySelectorAll('dialog[open][data-task]')) {
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
  for (const dialog of document.querySelectorAll('dialog[open][data-task]')) {
    if (dialog.tmRead) { dialog.tmRead.changes.clear(); taskMeetingsLoad(dialog).catch(() => {}); }
  }
});
