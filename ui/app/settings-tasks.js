/* ui/app/settings-tasks.js — Settings > Tasks: what bots may put on you
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';
// What bots may put on you (backend/ask_ingest.py): your own Off or On, how many open items you take before
// the check tightens and your criteria; an owner or admin also sets the team default everyone starts
// from. A change applies to the next item; nothing already on a plate moves.
const ASK_FILTER_MODES = [['off', 'Off', 'Bots put things on you as before.'],
  ['on', 'On', 'Asks that don’t meet your criteria are refused.']];
let ASK_FILTER = null;
const TASK_AUTOMATION_TRIGGERS = [
  ['task.created', 'a task is created', 'a task is created'],
  ['task.step_changed', 'a task enters a step', 'a task enters a step'],
  ['task.done', 'a task is done', 'a task is marked done'],
  ['task.declined', 'a task is declined', 'a task is declined'],
  ['task.closed', 'a task is closed', 'a task is closed'],
];
let TASK_AUTOMATION_TYPE = '';
async function askFilterSettingsLoad() {
  const el = $('#set-ask-filter'); if (!el) return;
  const r = await v2Get('/v2/ask-ingest/settings');
  if (!r?.settings) { $('#set-ask-filter-card').hidden = true; return; }
  ASK_FILTER = {...r, editing: ASK_FILTER?.editing || 'mine'};
  askFilterSettingsPaint();
}
function taskAutomationSettingsMount() {
  const host = $('#set-task-automations');
  if (host) host.innerHTML = '<p class="settings-cell-note">Choose a task type to see its Triggers and Actions.</p>';
}
function taskAutomationSettingsShow(tab) {
  if (tab === 'tasks' && S.me?.cloud) void taskAutomationSettingsLoad();
}
async function taskAutomationSettingsLoad() {
  const host = $('#set-task-automations');
  if (!host) return;
  host.textContent = 'Loading…';
  try {
    const [types, automations] = await Promise.all([taskTypesLoad({cached: true}), taskAutomationList()]);
    if (!host.isConnected) return;
    const selected = types.some(type => type.id === TASK_AUTOMATION_TYPE) ? TASK_AUTOMATION_TYPE : '';
    TASK_AUTOMATION_TYPE = selected;
    const mayEdit = canMove();
    const typeName = selected ? types.find(type => type.id === selected)?.name : '';
    const rows = automations.filter(rule => TASK_AUTOMATION_TRIGGERS.some(([name]) => rule.trigger?.name === name)
      && (!selected || (rule.trigger?.filters || []).some(filter => filter.field === 'event.data.type_id' && filter.value === selected)));
    host.innerHTML = `<label class="task-automation-type">Task type
      <select data-task-automation-type aria-label="Task type"><option value="">All tasks</option>${types.map(type =>
        `<option value="${esc(type.id)}"${type.id === selected ? ' selected' : ''}>${esc(type.name)}</option>`).join('')}</select></label>
      ${!mayEdit ? '<p class="settings-cell-note">Only people who can change Settings can edit task Triggers and Actions. Ask a Settings manager for help.</p>' : ''}
      <div class="task-automation-sections">${TASK_AUTOMATION_TRIGGERS.map(([name, label, words]) => {
        const intro = typeName ? `a task of type ${typeName} ${words.replace(/^a task /, '')}` : words;
        const matches = rows.filter(rule => rule.trigger.name === name);
        return `<section class="task-automation-section" data-trigger="${name}"><h3>${esc(label[0].toUpperCase() + label.slice(1))}</h3>
          <p class="settings-cell-note"><b>Trigger:</b> ${esc(intro)}.</p>
          ${mayEdit ? `<button class="ghost" type="button" data-task-add-action="${name}">Add action</button>` : ''}
          ${matches.map(rule => taskAutomationRuleRow(rule, mayEdit)).join('') || '<p class="muted">No Actions</p>'}</section>`;
      }).join('')}</div>`;
    host.querySelector('[data-task-automation-type]').onchange = event => {
      TASK_AUTOMATION_TYPE = event.target.value;
      void taskAutomationSettingsLoad();
    };
    host.querySelectorAll('[data-task-add-action]').forEach(button => button.onclick = () => {
      const trigger = {type: 'event', name: button.dataset.taskAddAction,
        filters: selected ? [{field: 'event.data.type_id', op: 'is', value: selected}] : []};
      void taskAutomationAddAction(trigger, typeName);
    });
  } catch (error) { host.textContent = error.message; }
}
async function taskAutomationList() {
  const all = [];
  let cursor = '';
  do {
    const page = await get('/v2/automations?limit=500' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
    all.push(...(page.automations || []));
    if (page.next == null || page.next === cursor) break;
    cursor = page.next;
  } while (cursor);
  return all;
}
function taskAutomationRuleRow(rule, mayEdit) {
  const href = auHref(rule.id), failed = !rule.builtin && rule.last_run?.status === 'failed';
  const when = rule.last_run?.finished_at || rule.last_run?.started_at;
  const time = when ? new Date(when).toLocaleString() : '';
  return `<div class="task-automation-rule"><a href="${href}">${esc(rule.name || 'Untitled')}</a>
    ${rule.paused_reason === 'Paused: too many runs' ? '<span class="pill waiting">Paused: too many runs</span>' : ''}
    ${mayEdit ? ` <a class="ghost" data-task-edit href="${href}">Edit Trigger or Action</a>` : ''}
    ${failed ? `<p class="err">Last Action failed${time ? ' at ' + esc(time) : ''}: ${esc(rule.last_run.error || 'failed')}</p>` : ''}</div>`;
}
async function taskAutomationAddAction(trigger, typeName = '') {
  if (!canMove()) return;
  auOpenTaskActionDraft(trigger, typeName);
}
function askFilterSettingsPaint() {
  const el = $('#set-ask-filter'); if (!el || !ASK_FILTER) return;
  const team = ASK_FILTER.editing === 'team', v = team ? ASK_FILTER.team : ASK_FILTER.settings;
  const mine = ASK_FILTER.settings;
  el.innerHTML = `<form class="ask-filter">
    ${ASK_FILTER.may_edit_team ? `<div class="theme-seg" role="radiogroup" aria-label="Whose settings">
      <button type="button" role="radio" aria-checked="${!team}" data-ask-filter-edit="mine">Yours</button>
      <button type="button" role="radio" aria-checked="${team}" data-ask-filter-edit="team">Team default</button></div>` : ''}
    <p class="settings-cell-note">${team ? 'Everyone without their own settings uses these.'
      : mine.own ? 'Your own settings.' : 'You use the team default until you save your own.'}</p>
    <fieldset class="ask-filter-modes"><legend>Mode</legend>${ASK_FILTER_MODES.map(([k, label, help]) =>
      `<label><input type="radio" name="mode" value="${k}"${v.mode === k ? ' checked' : ''}> <b>${esc(label)}</b> <span class="muted">${esc(help)}</span></label>`).join('')}</fieldset>
    <label>Open items before it tightens <input type="number" name="budget" min="1" max="200" value="${esc(v.budget)}" required></label>
    <label>What you want to be asked about, one per line (up to 10)
      <textarea name="criteria" rows="6">${esc((v.criteria || []).join('\n'))}</textarea></label>
    <div class="row"><button class="primary" type="submit">Save</button>
      ${!team && mine.own ? '<button type="button" class="ghost" data-ask-filter-reset>Use the team default</button>' : ''}
      <span class="muted" data-ask-filter-msg></span></div></form>
    ${team ? '' : `<form class="ask-filter-replay" aria-label="Replay a day">
      <p class="settings-cell-note">Before you turn it On: see what On would have done with one day of bots\u2019 items, with these settings. Nothing moves.</p>
      <div class="row"><input type="date" name="day" required value="${esc(new Date().toISOString().slice(0, 10))}" aria-label="Day">
        <button type="submit" class="ghost">Replay a day</button><span class="muted" data-ask-replay-msg></span></div>
      <div class="ask-filter-replay-rows" role="list"></div></form>`}`;
  const form = $('form.ask-filter', el);
  el.querySelectorAll('[data-ask-filter-edit]').forEach(b => b.onclick = () => { ASK_FILTER.editing = b.dataset.askFilterEdit; askFilterSettingsPaint(); });
  const msg = $('[data-ask-filter-msg]', el);
  const reset = $('[data-ask-filter-reset]', el);
  if (reset) reset.onclick = async () => {
    try { await writeRequest('DELETE', '/v2/ask-ingest/settings'); toast('Using the team default'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  const replayForm = $('.ask-filter-replay', el);
  if (replayForm) replayForm.onsubmit = ev => { ev.preventDefault(); void askFilterReplay(replayForm); };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = new FormData(form);
    const body = {mode: f.get('mode'), budget: Number(f.get('budget')),
      criteria: String(f.get('criteria') || '').split('\n').map(s => s.trim()).filter(Boolean), team};
    msg.textContent = 'Saving…';
    try { await put('/v2/ask-ingest/settings', body); toast('Saved; the next item uses it'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}
const ASK_REPLAY_WHY = {
  status: 'A progress note: post it as an update', covered: 'Same as one you already have open',
  unclear: 'Doesn’t say what to decide', own_work: 'Work a bot can do itself',
  criteria: 'Over your budget and meets none of your lines',
  recommends: 'Needs one of your criteria and a recommendation',
  urgent_only: 'Your plate is full and it isn’t urgent',
};
const ASK_REPLAY_WOULD = {through: 'Reaches you', fyi: 'Refused: post an update', sent_back: 'Refused', merged: 'Merged', failed_open: 'Reaches you (no answer)'};
// One day replayed a page at a time (POST /v2/ask-ingest/replay); what would stay off the plate leads.
async function askFilterReplay(form) {
  const msg = $('[data-ask-replay-msg]', form), list = $('.ask-filter-replay-rows', form), button = $('button', form);
  const day = new FormData(form).get('day');
  const rows = [];
  button.disabled = true; list.innerHTML = '';
  try {
    let offset = 0;
    do {
      msg.textContent = rows.length ? `${rows.length} judged…` : 'Judging…';
      const page = await post('/v2/ask-ingest/replay', {person: myActor(), date: day, offset});
      rows.push(...page.items);
      offset = page.next_offset;
    } while (offset != null);
    const off = rows.filter(r => ['sent_back', 'fyi', 'merged'].includes(r.would));
    msg.textContent = `${rows.length} items; On would have kept ${off.length} off your plate`;
    list.innerHTML = [...off, ...rows.filter(r => !off.includes(r))].map(r => `<div class="ask-replay-row" role="listitem">
      <span class="ask-replay-text"><span class="req-from">${esc(r.asker_name || actorLabel(r.asker))}:</span> ${esc(r.private ? 'A private item' : r.title || 'Untitled')}
        <span class="muted">${esc(ASK_REPLAY_WHY[r.reason] || '')} · ${esc(r.load)} open then</span></span>
      <span class="ask-replay-act">${esc(ASK_REPLAY_WOULD[r.would] || r.would)}</span></div>`).join('');
  } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  button.disabled = false;
}
