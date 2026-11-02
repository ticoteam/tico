/* ui/app/operations-due.js — Due dates on a location's or equipment's panel: list, add, mark done, reopen, completion history.
   Each occurrence completes and reopens on its own (actions due_date_occurrence_complete and _reopen).
   Reads GET /api/v2/due-dates?subject_type=&subject_id= and each one's detail (GET /api/v2/due-dates/{id}, its history); writes go through finAct. Screens call them due dates.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OPS_REPEATS = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly'], ['every_days', 'Every … days'], ['every_months', 'Every … months']];
const opsRepeatLabel = d => d.repeats === 'every_days' ? `Every ${d.every_count} days` : d.repeats === 'every_months' ? `Every ${d.every_count} months` : opsLabel(OPS_REPEATS, d.repeats);
const opsAssignees = () => [...(S.emps || []).filter(e => e.status !== 'archived').map(e => ['bot:' + e.name, e.display_name || e.name]),
  ...(S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id])];

// How many due dates a panel shows before More. Each shown one costs a read for its history.
const OPS_DUE_PAGE = 50;
// The repeats that count from the last completion: the backend reopens only the latest of these.
const OPS_DUE_ANCHORED = new Set(['every_days', 'every_months']);
const OPS_DUE_RANK = {overdue: 0, due: 1, upcoming: 2};

function opsDueMount(hostId, subjectType, subjectId, {onLoad, onWrite} = {}) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const box = {host, subjectType, subjectId, onLoad, onWrite, open: new Set(), load: 0, shown: OPS_DUE_PAGE};
  host.opsDueBox = box;
  void opsDueLoad(box);
}
// A late answer or a finished write belongs to the panel that asked: never to the record the panel shows now.
const opsDueLive = box => box.host.isConnected && box.host.opsDueBox === box;
// The list has no occurrences; a due date's detail has them, as `history`.
const opsDueDetails = rows => Promise.all(rows.map(d => get(`/v2/due-dates/${encodeURIComponent(d.id)}`, {fresh: true}).then(x => x?.due_date || d).catch(() => d)));
// Most pressing first, so an overdue date is on top however its name sorts.
const opsDueOrder = rows => [...rows].sort((a, b) => (OPS_DUE_RANK[a.status] ?? 3) - (OPS_DUE_RANK[b.status] ?? 3)
  || String(a.next_due_on || '9').localeCompare(String(b.next_due_on || '9')) || String(a.name).localeCompare(String(b.name)));
async function opsDueLoad(box) {
  if (!opsDueLive(box)) return;
  const load = ++box.load;
  let rows = [], dues = [], failed = null;
  try {
    rows = opsDueOrder(await finPaged('/v2/due-dates', 'due_dates', opsQs({subject_type: box.subjectType, subject_id: box.subjectId})));
    dues = await opsDueDetails(rows.slice(0, box.shown));
  } catch (e) { if (!opsAbsent(e)) failed = e; }
  if (load !== box.load || !opsDueLive(box)) return;
  box.rows = rows;
  box.dues = dues;
  opsDueDraw(box, failed);
  box.onLoad?.(dues);
}
async function opsDueMore(box) {
  const load = box.load, next = box.rows.slice(box.dues.length, box.dues.length + OPS_DUE_PAGE);
  const dues = await opsDueDetails(next);
  if (load !== box.load || !opsDueLive(box)) return;
  box.dues = [...box.dues, ...dues];
  box.shown = box.dues.length;
  opsDueDraw(box);
  box.onLoad?.(box.dues);
}
function opsDueDraw(box, failed = null) {
  const host = box.host, edit = opsEditor();
  const add = edit ? '<span class="spacer"></span><button type="button" class="ghost ops-mini" data-due-add>+ Add</button>' : '';
  const more = (box.rows?.length || 0) > (box.dues?.length || 0) ? '<button type="button" class="ghost ops-more" data-due-more>More</button>' : '';
  const rows = (box.dues || []).filter(d => !d.archived_at).map(d => opsDueRow(box, d, edit)).join('') + more;
  // Nothing to show and nothing to add: no empty heading.
  if (!edit && !rows && !failed) { host.innerHTML = ''; return; }
  host.innerHTML = opsSec('Due dates', failed ? opsFail(failed) : rows, add);
  host.onclick = ev => {
    const hit = ev.target.closest('[data-due-add],[data-due-done],[data-due-reopen],[data-due-stop],[data-due-more]');
    if (!hit || !opsDueLive(box)) return;
    if (hit.matches('[data-due-add]')) return opsDueAdd(box);
    if (hit.matches('[data-due-more]')) { hit.disabled = true; return void opsDueMore(box); }
    const write = hit.matches('[data-due-done]') ? ['due_date_occurrence_complete', {due_date_id: hit.dataset.dueDone, due_on: hit.dataset.on}, 'Done']
      : hit.matches('[data-due-reopen]') ? ['due_date_occurrence_reopen', {due_date_id: hit.dataset.dueReopen, due_on: hit.dataset.on}, 'Reopened']
      : ['due_date_update', {due_date_id: hit.dataset.dueStop, active: false}, 'Stopped'];
    hit.disabled = true;
    void finAct(write[0], write[1]).then(done => { if (done?.status === 'committed') toast(write[2]); box.onWrite?.(); return opsDueLoad(box); }).catch(e => { toast(e.message || 'Not saved', true); hit.disabled = false; });
  };
  host.querySelectorAll('details[data-due-history]').forEach(el => {
    el.ontoggle = () => { if (el.open) box.open.add(el.dataset.dueHistory); else box.open.delete(el.dataset.dueHistory); };
  });
}
function opsDueRow(box, d, edit) {
  const tag = d.status === 'overdue' ? '<span class="pill fail">Overdue</span>' : d.status === 'due' ? '<span class="pill waiting">Due soon</span>' : '';
  const done = (d.history || []).filter(o => o.completed_at).sort((a, b) => String(b.completed_at).localeCompare(String(a.completed_at)));
  // The assignee works their own without the records right (the backend's _may_work); changing the schedule stays an editor's.
  const work = edit || (!!d.assignee_id && d.assignee_id === finMe());
  const latest = OPS_DUE_ANCHORED.has(d.repeats) ? done.map(o => String(o.due_on)).sort().at(-1) : null;
  const next = work && d.next_due_on ? `<button type="button" class="ghost ops-mini" data-due-done="${esc(d.id)}" data-on="${esc(d.next_due_on)}">Mark done</button>` : '';
  const hist = done.map(o => `<li><span>${esc(opsDay(o.due_on))}</span><span class="muted">${esc(opsDay(o.completed_at))}${o.completed_by_id ? ' · ' + esc(actorLabel(o.completed_by_id)) : ''}</span>
    ${work && (latest == null || o.due_on === latest) ? `<button type="button" class="linkish" data-due-reopen="${esc(d.id)}" data-on="${esc(o.due_on)}">Reopen</button>` : ''}</li>`).join('');
  const history = hist || edit ? `<details data-due-history="${esc(d.id)}"${box.open.has(d.id) ? ' open' : ''}><summary>History</summary>
      ${hist ? `<ul class="ops-hist">${hist}</ul>` : '<p class="muted ops-note">None yet</p>'}
      ${edit ? `<button type="button" class="linkish" data-due-stop="${esc(d.id)}">Stop this due date</button>` : ''}</details>` : '';
  return `<div class="ops-due" data-due="${esc(d.id)}"><div class="ops-due-top"><b>${esc(d.name)}</b>${tag}<span class="spacer"></span>${next}</div>
    <div class="muted ops-due-sub">${esc([opsRepeatLabel(d), d.next_due_on ? opsDay(d.next_due_on) : 'Finished', d.assignee_id ? actorLabel(d.assignee_id) : ''].filter(Boolean).join(' · '))}</div>${history}</div>`;
}
function opsDueAdd(box) {
  const body = `${opsInput('Name', 'name', '', 'maxlength="200" required')}
    ${opsPick('Repeats', 'repeats', OPS_REPEATS, 'yearly', null)}
    <label data-due-every hidden><span>Every</span><input name="every_count" type="number" min="1" max="3650" value="90"></label>
    ${opsInput('First due', 'starts_on', finToday(), 'type="date" required')}
    ${opsInput('Days ahead', 'lead_days', '30', 'type="number" min="0" max="3650"')}
    ${opsPick('Assigned to', 'assignee_id', opsAssignees(), finMe(), null)}`;
  const dlg = finDialog('New due date', body, 'Add', async form => {
    const v = n => form.elements[n].value.trim();
    if (!v('name')) throw new Error('A name');
    if (!v('starts_on')) throw new Error('A first due date');
    const repeats = v('repeats'), every = repeats.startsWith('every_');
    const payload = {name: v('name'), subject_type: box.subjectType, subject_id: box.subjectId, repeats, starts_on: v('starts_on'), lead_days: Number(v('lead_days') || 0)};
    if (every) {
      const count = Number(v('every_count'));
      if (!Number.isInteger(count) || count < 1) throw new Error('How often, as a whole number');
      payload.every_count = count;
    }
    payload.assignee_id = v('assignee_id');
    await finAct('due_date_create', payload);
    box.onWrite?.();
    await opsDueLoad(box);
  });
  const form = dlg.querySelector('form'), every = form.querySelector('[data-due-every]');
  form.elements.repeats.onchange = () => { every.hidden = !form.elements.repeats.value.startsWith('every_'); };
}
