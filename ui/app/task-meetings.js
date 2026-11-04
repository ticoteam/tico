'use strict';

// Re-read on task redraw so a meeting's current visibility decides whether its link appears.
function taskMeetingsAdopt(d, task) {
  const slot = $('[data-task-meetings]', d);
  if (!slot) return;
  d.tmEl = slot;
  const rows = [];
  const load = async cursor => {
    const query = new URLSearchParams({limit: '20'});
    if (cursor) query.set('cursor', cursor);
    const result = await get(`/v2/tasks/${encodeURIComponent(task.id)}/meetings?${query}`);
    if (d.tmEl !== slot || !slot.isConnected) return;
    rows.push(...(result.meetings || []));
    slot.hidden = !rows.length;
    slot.innerHTML = `<h3 class="rail-h">Meetings</h3><ul class="task-meetings-list">${rows.map(m =>
      `<li><span class="title">${esc(m.title)}</span><time>${esc(String(m.started_at || '').slice(0, 10))}</time><a href="#/meetings?meeting=${encodeURIComponent(m.meeting_id)}">Open</a></li>`).join('')}</ul>${result.next_cursor ? '<button class="linkish" type="button" data-meetings-more>More</button>' : ''}`;
    const more = $('[data-meetings-more]', slot);
    if (more) more.onclick = async () => {
      more.disabled = true;
      try { await load(result.next_cursor); }
      catch (e) { more.disabled = false; toast(e.message, true); }
    };
  };
  load().catch(() => {});
}

// A link changes no task fields, so the task list's identical-row suppression cannot refresh this section.
liveOn('tasks', event => {
  for (const dialog of document.querySelectorAll('dialog[open][data-task]')) {
    const id = dialog.dataset.task;
    if (!event.bulk && String(event.id) !== id) continue;
    liveSoon('task-meetings:' + id, () => {
      if (dialog.open && dialog.dataset.task === id) taskMeetingsAdopt(dialog, {id});
    }, 200);
  }
});
