/* ui/app/task-checks-bar.js — the Tasks page's right bar (backend/task_manager.py): the checks and task rules for the
   task type in view, their recent catches with right / wrong, Check now / Check all, and the Task Manager chat.
   Collapsed it is a thin strip with today's catch count. Every change the Task Manager proposes is a card the person
   confirms; the server replays it as them, so nothing here grants more than the person already has.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TCHK_OPEN = 'tico.tasks.checks.open';
function taskChecksMount(state) {
  const page = $('.tasks-page');
  if (!page || $('#tchk')) return;
  const bar = document.createElement('aside');
  bar.id = 'tchk'; bar.className = 'tchk'; bar.setAttribute('aria-label', 'Task checks');
  page.appendChild(bar);
  page.classList.add('has-checks');
  let open = false;
  try { open = localStorage.getItem(TCHK_OPEN) === '1'; } catch {}
  state.checks = {open, data: null, chat: null, type: null, busy: false};
  bar.addEventListener('click', ev => taskChecksClick(state, ev));
  bar.addEventListener('change', ev => taskChecksSwitch(state, ev));
  bar.addEventListener('submit', ev => { ev.preventDefault(); void taskChecksSay(state, ev.target); });
  bar.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target.matches('[data-tchk-input]')) { ev.preventDefault(); ev.target.form.requestSubmit(); }
    ev.stopPropagation();            // the list's own keys (j/k, c, /) stay out of the bar
  });
  taskChecksSync(state);
}
function taskChecksSync(state) {
  if (!state.checks || TASKS_ST !== state) return;
  const type = state.type || 'general';
  if (state.checks.type !== type) void taskChecksLoad(state);
}
async function taskChecksLoad(state) {
  const type = state.checks.type = state.type || 'general';
  const data = await v2Get('/v2/task-checks/bar?type=' + encodeURIComponent(type));
  if (TASKS_ST !== state || state.checks.type !== type) return;
  state.checks.data = data;
  if (state.checks.open && data?.chat && !state.checks.chat) {
    const chat = await v2Get('/v2/task-manager');
    if (TASKS_ST !== state) return;
    state.checks.chat = chat?.messages || [];
  }
  taskChecksPaint(state);
}
function taskChecksPaint(state) {
  const bar = $('#tchk'), ck = state.checks, data = ck?.data;
  if (!bar) return;
  bar.classList.toggle('open', !!ck.open);
  $('.tasks-page')?.classList.toggle('checks-open', !!ck.open);
  const today = data?.today ?? 0;
  if (!ck.open) {
    bar.innerHTML = `<button type="button" class="tchk-strip" data-tchk-toggle aria-expanded="false" aria-controls="tchk" title="Task checks: ${today} caught today">
      <span class="tchk-count" data-tchk-today>${today}</span><span class="tchk-strip-label">Checks</span></button>`;
    return;
  }
  if (!data) { bar.innerHTML = `${taskChecksHead(state, today)}<p class="muted tchk-pad">Could not load the checks.</p>`; return; }
  const person = !!data.chat;
  const rules = (data.rules || []).map(r => taskChecksRule(r, person)).join('') || '<p class="muted tchk-pad">No checks or rules for this type.</p>';
  bar.innerHTML = `${taskChecksHead(state, today)}
    <div class="tchk-scroll">
      <div class="tchk-tools"><button type="button" class="ghost" data-tchk-check-all>Check all</button>
        <a href="#/settings/routines" class="linkish">Settings › Routines</a>
        <button type="button" class="linkish" data-tchk-bot-routines>Bot routines</button></div>
      <p class="tchk-note err" role="status" data-tchk-note>${esc(ck.note || '')}</p>
      <div class="tchk-rules">${rules}</div>
      ${taskChecksReport(data.report)}
      ${person ? taskChecksChat(ck.chat || []) : ''}
    </div>
    ${person ? `<form class="tchk-say" data-tchk-form><textarea data-tchk-input rows="2" maxlength="2000" placeholder="Ask the Task Manager" aria-label="Message the Task Manager"${ck.busy ? ' disabled' : ''}></textarea>
      <button type="submit" class="primary"${ck.busy ? ' disabled' : ''}>Send</button></form>` : ''}`;
  const log = bar.querySelector('.tchk-log');
  if (log) log.scrollTop = log.scrollHeight;
}
function taskChecksHead(state, today) {
  const name = state.checks.data?.type?.name || 'General';
  return `<header class="tchk-head"><h2>Checks <span class="muted">· ${esc(name)}</span></h2>
    <span class="tchk-today" data-tchk-today>${today} today</span>
    <button type="button" class="ghost tchk-close" data-tchk-toggle aria-expanded="true" aria-controls="tchk" aria-label="Collapse the checks">›</button></header>`;
}
function taskChecksRule(r, person) {
  const words = r.check_words ? `FOR ${r.for} · CHECK ${r.check_words} · THEN ${r.then.join(', ')}` : `${r.when.join(', ')} · ${r.then.join(', ')}`;
  const control = r.can_change
    ? `<label class="tchk-switch"><input type="checkbox" role="switch" data-tchk-on="${esc(r.key)}"${r.on ? ' checked' : ''}><span>${r.on ? 'On' : 'Off'}</span></label>`
    : `<span class="tchk-state">${r.on ? 'On' : 'Off'}</span>`;
  const catches = (r.catches || []).map(x => `<li data-tchk-catch="${esc(x.id)}"><a href="#/task/${encodeURIComponent(x.task.id)}">${esc(x.task.title)}</a>
      ${person ? `<span class="tchk-marks" role="group" aria-label="Was this catch right?">
        <button type="button" class="ghost" data-tchk-mark="right" aria-pressed="${x.mark === 'right'}">Right</button>
        <button type="button" class="ghost" data-tchk-mark="wrong" aria-pressed="${x.mark === 'wrong'}">Wrong</button></span>` : ''}</li>`).join('');
  return `<section class="tchk-rule" data-tchk-rule="${esc(r.key)}"><div class="tchk-rule-top"><h3>${esc(r.name)}</h3>${control}</div>
    ${r.read_only ? `<p class="tchk-ro">${esc(r.read_only)}</p>` : ''}
    <p class="tchk-words">${esc(words)}</p>
    ${r.stats ? `<p class="muted tchk-stats">${esc(r.stats)}</p>` : ''}
    ${r.checkable ? `<button type="button" class="linkish" data-tchk-check="${esc(r.key)}">Check now</button>` : ''}
    ${catches ? `<ul class="tchk-catches">${catches}</ul>` : ''}</section>`;
}
function taskChecksReport(rep) {
  if (!rep?.week) return '';
  return `<section class="tchk-report" aria-label="Weekly flow report"><h3>Week of ${esc(rep.week)}</h3>
    <p>${rep.catches} caught · ${rep.overrides} overridden · ${rep.wrong} of ${rep.marked} marks wrong · ${rep.quiet_waits} quiet waits</p>
    ${(rep.suggestions || []).map(s => `<p class="tchk-suggest">${esc(s)}</p>`).join('')}</section>`;
}
function taskChecksChat(messages) {
  return `<section class="tchk-chat" aria-label="Task Manager"><h3>Task Manager</h3><div class="tchk-log" role="log" aria-live="polite">
    ${messages.map(taskChecksMessage).join('') || '<p class="muted">Tell it what goes wrong with tasks, ask for a tab, or ask it to check everything.</p>'}</div></section>`;
}
function taskChecksMessage(m) {
  const card = m.card;
  const actions = card && card.state === 'pending'
    ? `<div class="tchk-card-actions"><button type="button" class="primary" data-tchk-confirm="${esc(m.id)}">Confirm</button>
       <button type="button" class="ghost" data-tchk-dismiss="${esc(m.id)}">Dismiss</button></div>`
    : card ? `<p class="muted tchk-card-state">${esc(card.result || card.state)}</p>` : '';
  return `<div class="tchk-msg ${m.role === 'person' ? 'mine' : ''}${card ? ' tchk-card' : ''}" data-tchk-msg="${esc(m.id)}"><p>${esc(m.body)}</p>${actions}</div>`;
}
function taskChecksMerge(state, messages) {
  const list = state.checks.chat ||= [];
  for (const m of messages || []) {
    const at = list.findIndex(x => x.id === m.id);
    if (at >= 0) list[at] = m; else list.push(m);
  }
}
async function taskChecksClick(state, ev) {
  const ck = state.checks, t = ev.target;
  ck.note = '';
  if (t.closest('[data-tchk-toggle]')) {
    ck.open = !ck.open;
    try { localStorage.setItem(TCHK_OPEN, ck.open ? '1' : '0'); } catch {}
    if (ck.open && ck.data?.chat && !ck.chat) { void taskChecksLoad(state); }
    taskChecksPaint(state);
    $('#tchk [data-tchk-toggle]')?.focus();
    return;
  }
  if (t.closest('[data-tchk-check-all]')) { taskCheckRun(); return; }
  const now = t.closest('[data-tchk-check]');
  if (now) { taskCheckRun(now.dataset.tchkCheck); return; }
  if (t.closest('[data-tchk-bot-routines]')) { tasksSetView(state, 'recurring'); return; }
  const mark = t.closest('[data-tchk-mark]');
  if (mark) {
    const id = mark.closest('[data-tchk-catch]').dataset.tchkCatch;
    const verdict = mark.getAttribute('aria-pressed') === 'true' ? null : mark.dataset.tchkMark;
    try { await post('/v2/task-checks/catches/' + encodeURIComponent(id) + '/mark', {verdict}); } catch (e) { ck.note = e.message || 'Could not save the mark'; taskChecksPaint(state); return; }
    for (const r of ck.data?.rules || []) for (const x of r.catches || []) if (x.id === id) x.mark = verdict;
    taskChecksPaint(state);
    return;
  }
  const confirm = t.closest('[data-tchk-confirm]'), dismiss = t.closest('[data-tchk-dismiss]');
  if (confirm || dismiss) {
    const id = (confirm || dismiss).dataset[confirm ? 'tchkConfirm' : 'tchkDismiss'];
    (confirm || dismiss).disabled = true;
    try {
      const out = await post(`/v2/task-manager/cards/${encodeURIComponent(id)}/${confirm ? 'confirm' : 'dismiss'}`, {});
      taskChecksMerge(state, out.messages);
    } catch (e) { taskChecksMerge(state, [{id: 'err-' + Date.now(), role: 'bot', body: e.message || 'That did not go through'}]); }
    taskChecksPaint(state);
    if (confirm) void taskChecksLoad(state);       // a rule change or fix shows on its row
  }
}
async function taskChecksSwitch(state, ev) {
  const input = ev.target.closest('[data-tchk-on]');
  if (!input) return;
  input.disabled = true;
  try { await put('/v2/task-rules/' + encodeURIComponent(input.dataset.tchkOn), {on: input.checked}); }
  catch (e) { input.checked = !input.checked; state.checks.note = e.message || 'Could not change the rule'; }
  void taskChecksLoad(state);
}
async function taskChecksSay(state, form) {
  const input = form.querySelector('[data-tchk-input]'), text = input.value.trim();
  if (!text || state.checks.busy) return;
  state.checks.busy = true;
  taskChecksMerge(state, [{id: 'mine-' + Date.now(), role: 'person', body: text}]);
  taskChecksPaint(state);
  try {
    const out = await post('/v2/task-manager', {text, type: state.type || 'general'});
    state.checks.chat = state.checks.chat.filter(m => !String(m.id).startsWith('mine-'));
    taskChecksMerge(state, out.messages);
  } catch (e) { taskChecksMerge(state, [{id: 'err-' + Date.now(), role: 'bot', body: e.message || 'The Task Manager did not answer'}]); }
  state.checks.busy = false;
  taskChecksPaint(state);
  $('#tchk [data-tchk-input]')?.focus();
}
