/* ui/app/settings-release.js — Settings > Rules: the Releases and Task Manager cards, and the board's Release now
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The daily release (backend/release_flow.py): on or off, one or two company times, the Release Manager and
// Technical Writer ("" finds each by its starter template), the next and last hour, and what waits at Pending
// Release. Owners and admins change it (`can_change`); a Dev tickets mover asks for a release now (`can_release`).
let RELEASE_VIEW = null, RELEASE_AT = 0;
// The last answer under the card (Saved, or what Release now did) survives the repaint the fresh schedule causes.
let RELEASE_NOTE = {text: '', failed: false};
const RELEASE_REASONS = {nothing_pending: 'Nothing at Pending Release', already_open: 'Woke the open release task', no_release_manager: 'No Release Manager'};
async function releaseScheduleLoad(force) {
  if (!force && RELEASE_VIEW && Date.now() - RELEASE_AT < 60000) return RELEASE_VIEW;
  RELEASE_AT = Date.now();
  RELEASE_VIEW = await get('/v2/release-schedule');
  return RELEASE_VIEW;
}
function releaseLastHTML(last) {
  if (!last) return '<span class="muted">Not run yet</span>';
  const when = `<span class="tnum muted" title="${esc(fmt(last.at))}">${esc(ago(last.at))}</span>`;
  const what = last.sent ? `${last.tickets} ticket${last.tickets === 1 ? '' : 's'}${last.task ? ` · <a href="#/task/${encodeURIComponent(last.task)}">Release task</a>` : ''}`
    : esc(RELEASE_REASONS[last.reason] || last.reason || 'Nothing sent');
  return `<span data-release-last>${what}</span> ${when}`;
}
function releaseBotPick(name, chosen, found, label) {
  const bots = (S.emps || []).filter(e => e.state !== 'archived').map(e => e.name).sort((a, b) => empName(a).localeCompare(empName(b)));
  const auto = found && !chosen ? `Find by template (${empName(found)})` : 'Find by template';
  return `<select class="settings-inline-select" name="${name}" aria-label="${esc(label)}"><option value="">${esc(auto)}</option>
    ${bots.map(b => `<option value="${esc(b)}"${b === chosen ? ' selected' : ''}>${esc(empName(b))}</option>`).join('')}</select>`;
}
function releaseNowButtonHTML(v, cls = 'ghost') {
  if (!v?.can_release) return '';
  return `<button type="button" class="${cls}" data-release-now${v.pending ? '' : ' disabled title="Nothing at Pending Release"'}>Release now</button>`;
}
// With a note the server asks even when nothing is at Pending Release (work merged without a Dev ticket), so the
// card's field lights the button; the board's button has no field and stays off while nothing waits.
function releaseNoteHTML(v) {
  return v.can_release ? '<input type="text" data-release-note maxlength="1000" placeholder="Note" aria-label="Release note">' : '';
}
function releaseCardHTML(v) {
  const times = v.times?.length ? v.times : ['10:00'];
  const timeBox = (t, i) => v.can_change
    ? `<input type="time" step="60" data-release-time="${i}" value="${esc(t)}" aria-label="Release time ${i + 1}" title="${esc(v.zone)}">${times.length > 1 ? `<button type="button" class="ghost" data-release-drop="${i}" aria-label="Remove this time">×</button>` : ''}`
    : `<span class="tnum">${esc(t)}</span>`;
  const on = v.can_change ? `<label class="sysr-switch">${peopleSwitch('data-release-on', v.on, false, 'Daily release')}<span>${v.on ? 'On' : 'Off'}</span></label>`
    : `<span>${v.on ? 'On' : 'Off'}</span>`;
  const manager = v.can_change ? releaseBotPick('bot', v.bot, v.release_manager, 'Release Manager') : `<span>${esc(v.release_manager ? empName(v.release_manager) : 'None')}</span>`;
  const writer = v.can_change ? releaseBotPick('writer', v.writer, v.technical_writer, 'Technical Writer') : `<span>${esc(v.technical_writer ? empName(v.technical_writer) : 'None')}</span>`;
  return `<div class="sb-rows rel-rows">
    <div class="sb-row"><span>Daily</span><div class="rel-line">${on}</div><span></span></div>
    <div class="sb-row"><span>Times</span><div class="rel-line">${times.map(timeBox).join('')}${v.can_change && times.length < 2 ? '<button type="button" class="ghost" data-release-add>Add time</button>' : ''}</div><span></span></div>
    <div class="sb-row"><span>Manager</span>${manager}<span></span></div>
    <div class="sb-row"><span>Writer</span>${writer}<span></span></div>
    <div class="sb-row"><span>Next</span><div class="rel-line">${v.on && v.next ? `<span class="tnum" title="${esc(fmt(v.next))}" data-release-next>${esc(until(v.next))}</span>` : '<span class="muted">Off</span>'}</div><span></span></div>
    <div class="sb-row"><span>Last</span><div class="rel-line">${releaseLastHTML(v.last)}</div><span></span></div>
    <div class="sb-row"><span>Pending</span><div class="rel-line"><span class="tnum" data-release-pending>${v.pending} ticket${v.pending === 1 ? '' : 's'}</span>${releaseNoteHTML(v)}${releaseNowButtonHTML(v)}</div><span></span></div>
  </div><p class="settings-cell-note${RELEASE_NOTE.failed ? ' err' : ''}" role="status" data-release-status>${esc(RELEASE_NOTE.text)}</p>`;
}
function releaseStatus(text, failed) {
  RELEASE_NOTE = {text, failed: !!failed};
  const box = $('#set-release [data-release-status]');
  if (box) { box.textContent = text; box.classList.toggle('err', !!failed); }
}
async function releaseSave(body) {
  try { RELEASE_VIEW = await patch('/v2/release-schedule', body); RELEASE_AT = Date.now(); releaseCardPaint(); releaseStatus('Saved'); }
  catch (e) { releaseCardPaint(); releaseStatus(e.message || 'Try again', true); }
}
// One press asks the Release Manager; the answer says whether a task was filed, an open one woken, or why not.
async function releaseNow(button, note = '') {
  if (button) button.disabled = true;
  let text = '', failed = false;
  try {
    const out = await post('/v2/release/now', note ? {note} : {});
    text = out.sent ? (out.tickets ? `Asked for a release of ${out.tickets} ticket${out.tickets === 1 ? '' : 's'}` : 'Asked for a release') : (RELEASE_REASONS[out.reason] || 'Nothing sent');
    void releaseScheduleLoad(true).then(() => { releaseCardPaint(); boardReleaseSlots(); }).catch(() => {});
  } catch (e) { text = e.message || 'Try again'; failed = true; }
  finally { if (button?.isConnected) button.disabled = false; }
  return {text, failed};
}
function releaseCardPaint() {
  const el = $('#set-release'), card = $('#set-release-card'); if (!el || !RELEASE_VIEW) return;
  if (formBusy(el)) return;
  card.hidden = false;
  el.innerHTML = releaseCardHTML(RELEASE_VIEW);
  el.oninput = ev => {
    const now = el.querySelector('[data-release-now]');
    if (ev.target.matches('[data-release-note]') && now) now.disabled = !RELEASE_VIEW.pending && !ev.target.value.trim();
  };
  el.onchange = ev => {
    const t = ev.target;
    if (t.matches('[data-release-on]')) void releaseSave({on: t.checked});
    else if (t.name === 'bot' || t.name === 'writer') void releaseSave({[t.name]: t.value});
  };
  // A time saves when its box is left, not per keystroke, so the minutes can be typed after the hour.
  if (!el.dataset.relBound) {
    el.dataset.relBound = '1';
    el.addEventListener('focusout', ev => {
      if (!ev.target.matches('[data-release-time]') || !ev.target.value) return;
      const times = [...el.querySelectorAll('[data-release-time]')].map(i => i.value).filter(Boolean);
      if (times.join() !== (RELEASE_VIEW.times || []).join()) void releaseSave({times});
    });
  }
  el.onclick = async ev => {
    if (ev.target.closest('[data-release-add]')) { void releaseSave({times: [...(RELEASE_VIEW.times || []), '15:00'].slice(0, 2)}); return; }
    const drop = ev.target.closest('[data-release-drop]');
    if (drop) { void releaseSave({times: RELEASE_VIEW.times.filter((_, i) => i !== Number(drop.dataset.releaseDrop))}); return; }
    const now = ev.target.closest('[data-release-now]');
    if (now && !now.disabled) {
      const box = el.querySelector('[data-release-note]');
      const out = await releaseNow(now, box?.value.trim());
      if (box?.isConnected && !out.failed) { box.value = ''; now.disabled = !RELEASE_VIEW.pending; }
      releaseStatus(out.text, out.failed);
    }
  };
}
async function renderReleaseCard() {
  if (!$('#set-release')) return;
  try { await releaseScheduleLoad(); } catch { return; }   // a team with no schedule to read keeps the card hidden
  if (Array.isArray(RELEASE_VIEW?.times)) releaseCardPaint();
}

// The board's Pending Release column carries Release now for those who may ask (tasks.js draws the slot).
function boardReleaseSlots() {
  const slots = document.querySelectorAll('[data-release-slot]');
  if (!slots.length) return;
  const fill = v => slots.forEach(slot => {
    slot.innerHTML = releaseNowButtonHTML(v, 'ghost board-release');
    const button = slot.querySelector('[data-release-now]');
    if (button) button.onclick = async ev => { ev.stopPropagation(); const out = await releaseNow(button); toast(out.text); };
  });
  if (RELEASE_VIEW && Date.now() - RELEASE_AT < 60000) { fill(RELEASE_VIEW); return; }
  releaseScheduleLoad().then(v => { if (Array.isArray(v?.times)) fill(v); }).catch(() => {});
}

// The Task Manager (backend/task_manager.py): a built-in helper that checks each department's tasks on a schedule
// and files one check task per department with what it found. The owner pauses and resumes it.
let TASK_MANAGER_VIEW = null;
function taskManagerHTML(v) {
  const owner = S.me?.role === 'owner';
  const state = v.on ? 'On' : v.state === 'paused' ? 'Paused' : 'Off';
  const last = v.last_run && (v.last_run.finished || v.last_run.started);
  const toggle = owner ? `<button type="button" class="ghost" data-tm-toggle="${v.on ? 'off' : 'on'}">${v.on ? 'Pause' : 'Resume'}</button>` : '';
  const rows = (v.departments || []).map(d => `<div class="tm-row" data-tm-dept="${esc(d.department)}">
      <strong>${esc(d.name)}</strong>
      <span class="tnum${d.checked ? '' : ' muted'}" ${d.checked ? `title="${esc(fmt(d.checked))}"` : ''}>${d.checked ? esc(ago(d.checked)) : 'Not checked'}</span>
      <span class="tnum${d.items ? '' : ' muted'}">${d.items} item${d.items === 1 ? '' : 's'}</span>
      ${d.task ? `<a href="#/task/${encodeURIComponent(d.task.id)}" title="${esc(d.task.title || '')}">Check task</a>` : '<span class="muted">—</span>'}
    </div>`).join('');
  return `<div class="tm-head"><span class="sysr-state" data-tm-state>${state}</span>
      ${last ? `<span class="muted">Last run <span class="tnum" title="${esc(fmt(last))}">${esc(ago(last))}</span></span>` : '<span class="muted">Not run yet</span>'}
      ${toggle}<a class="ghost" href="#/bot/${encodeURIComponent(v.bot)}">Open</a></div>
    <div class="tm-list">${rows}</div><p class="settings-cell-note" role="status" data-tm-status></p>`;
}
function taskManagerPaint() {
  const el = $('#set-task-manager'), card = $('#set-task-manager-card'); if (!el || !TASK_MANAGER_VIEW) return;
  card.hidden = false;
  el.innerHTML = taskManagerHTML(TASK_MANAGER_VIEW);
  el.onclick = async ev => {
    const toggle = ev.target.closest('[data-tm-toggle]'); if (!toggle) return;
    toggle.disabled = true;
    try {
      await post('/v2/task-manager/turn-' + toggle.dataset.tmToggle, {});
      TASK_MANAGER_VIEW = await get('/v2/task-manager'); taskManagerPaint();
    } catch (e) {
      toggle.disabled = false;
      const box = el.querySelector('[data-tm-status]'); box.textContent = e.message || 'Try again'; box.classList.add('err');
    }
  };
}
async function renderTaskManagerCard() {
  if (!$('#set-task-manager')) return;
  // An install without the Task Manager answers 404; the card stays hidden.
  try { TASK_MANAGER_VIEW = await get('/v2/task-manager'); } catch { return; }
  if (Array.isArray(TASK_MANAGER_VIEW?.departments)) taskManagerPaint();
}
