/* ui/app/settings-release.ts — The built-in bots' cards on their bot page's Settings (Releases on the Release Manager's,
   Task Manager's and the Strategy Bot's), Settings > Security's Screening card, and the board's Release now
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/release-schedule answers (backend/release_flow.py): the daily release as the page shows it.
interface SettingsReleaseLast { at?: string; sent?: boolean; tickets?: number; task?: string | null; reason?: string | null; }
interface SettingsReleaseSchedule {
  on: boolean;
  times?: string[];
  zone?: string;
  bot?: string | null;
  writer?: string | null;
  release_manager?: string | null;
  technical_writer?: string | null;
  next?: string | null;
  last?: SettingsReleaseLast | null;
  pending: number;
  can_change?: boolean;
  can_release?: boolean;
}
// What POST /v2/release/now answers: whether a task was filed or woken, and why not when neither.
interface SettingsReleaseNowAnswer { sent?: boolean; tickets?: number; reason?: string | null; }

// The daily release (backend/release_flow.py): on or off, one or two company times, the Release Manager and
// Technical Writer ("" finds each by its starter template), the next and last hour, and what waits at Pending
// Release. Owners and admins change it (`can_change`); a Dev tickets mover asks for a release now (`can_release`).
let RELEASE_VIEW: SettingsReleaseSchedule | null = null, RELEASE_AT = 0;
// The last answer under the card (Saved, or what Release now did) survives the repaint the fresh schedule causes.
let RELEASE_NOTE = {text: '', failed: false};
const RELEASE_REASONS: Record<string, string> = {nothing_pending: 'Nothing at Pending Release', already_open: 'Woke the open release task', no_release_manager: 'No Release Manager'};
async function releaseScheduleLoad(force?: boolean): Promise<SettingsReleaseSchedule> {
  if (!force && RELEASE_VIEW && Date.now() - RELEASE_AT < 60000) return RELEASE_VIEW;
  RELEASE_AT = Date.now();
  RELEASE_VIEW = await get<SettingsReleaseSchedule>('/v2/release-schedule');
  return RELEASE_VIEW;
}
function releaseLastHTML(last: SettingsReleaseLast | null | undefined): string {
  if (!last) return '<span class="muted">Not run yet</span>';
  const when = `<span class="tnum muted" title="${esc(fmt(last.at))}">${esc(ago(last.at))}</span>`;
  const what = last.sent ? `${last.tickets} ticket${last.tickets === 1 ? '' : 's'}${last.task ? ` · <a href="#/task/${encodeURIComponent(last.task)}">Release task</a>` : ''}`
    : esc(RELEASE_REASONS[last.reason ?? ''] || last.reason || 'Nothing sent');
  return `<span data-release-last>${what}</span> ${when}`;
}
function releaseBotPick(name: string, chosen: string | null | undefined, found: string | null | undefined, label: string): string {
  const bots = (S.emps || []).filter(e => e.state !== 'archived').map(e => e.id).sort((a, b) => empName(a).localeCompare(empName(b)));
  const auto = found && !chosen ? `Find by template (${empName(found)})` : 'Find by template';
  return `<select class="settings-inline-select" name="${name}" aria-label="${esc(label)}"><option value="">${esc(auto)}</option>
    ${bots.map(b => `<option value="${esc(b)}"${b === chosen ? ' selected' : ''}>${esc(empName(b))}</option>`).join('')}</select>`;
}
function releaseNowButtonHTML(v: SettingsReleaseSchedule | null | undefined, cls = 'ghost'): string {
  if (!v?.can_release) return '';
  return `<button type="button" class="${cls}" data-release-now${v.pending ? '' : ' disabled title="Nothing at Pending Release"'}>Release now</button>`;
}
// With a note the server asks even when nothing is at Pending Release (work merged without a Dev ticket), so the
// card's field lights the button; the board's button has no field and stays off while nothing waits.
function releaseNoteHTML(v: SettingsReleaseSchedule): string {
  return v.can_release ? '<input type="text" data-release-note maxlength="1000" placeholder="Note" aria-label="Release note">' : '';
}
function releaseCardHTML(v: SettingsReleaseSchedule): string {
  const times = v.times?.length ? v.times : ['10:00'];
  const timeBox = (t: string, i: number): string => v.can_change
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
function releaseStatus(text: string, failed?: boolean): void {
  RELEASE_NOTE = {text, failed: !!failed};
  const box = document.querySelector<HTMLElement>('#set-release [data-release-status]');
  if (box) { box.textContent = text; box.classList.toggle('err', !!failed); }
}
async function releaseSave(body: Record<string, unknown>): Promise<void> {
  try { RELEASE_VIEW = await patch('/v2/release-schedule', body); RELEASE_AT = Date.now(); releaseCardPaint(); releaseStatus('Saved'); }
  catch (e) { releaseCardPaint(); releaseStatus(errorMessage(e, 'Try again'), true); }
}
// One press asks the Release Manager; the answer says whether a task was filed, an open one woken, or why not.
async function releaseNow(button: HTMLButtonElement | null, note = ''): Promise<{text: string; failed: boolean}> {
  if (button) button.disabled = true;
  let text = '', failed = false;
  try {
    const out: SettingsReleaseNowAnswer = await post('/v2/release/now', note ? {note} : {});
    text = out.sent ? (out.tickets ? `Asked for a release of ${out.tickets} ticket${out.tickets === 1 ? '' : 's'}` : 'Asked for a release') : (RELEASE_REASONS[out.reason ?? ''] || 'Nothing sent');
    void releaseScheduleLoad(true).then(() => { releaseCardPaint(); boardReleaseSlots(); }).catch(() => {});
  } catch (e) { text = errorMessage(e, 'Try again'); failed = true; }
  finally { if (button?.isConnected) button.disabled = false; }
  return {text, failed};
}
function releaseCardPaint(): void {
  const el = document.querySelector<HTMLElement>('#set-release'), card = document.querySelector<HTMLElement>('#set-release-card');
  if (!el || !card || !RELEASE_VIEW) return;
  if (formBusy(el)) return;
  card.hidden = false;
  el.innerHTML = releaseCardHTML(RELEASE_VIEW);
  el.oninput = ev => {
    const now = el.querySelector<HTMLButtonElement>('[data-release-now]');
    const note = ev.target;
    if (note instanceof HTMLInputElement && note.matches('[data-release-note]') && now && RELEASE_VIEW) now.disabled = !RELEASE_VIEW.pending && !note.value.trim();
  };
  el.onchange = ev => {
    const t = ev.target;
    if (!(t instanceof HTMLInputElement || t instanceof HTMLSelectElement)) return;
    if (t instanceof HTMLInputElement && t.matches('[data-release-on]')) void releaseSave({on: t.checked});
    else if (t.name === 'bot' || t.name === 'writer') void releaseSave({[t.name]: t.value});
  };
  // A time saves when its box is left, not per keystroke, so the minutes can be typed after the hour.
  if (!el.dataset.relBound) {
    el.dataset.relBound = '1';
    el.addEventListener('focusout', ev => {
      const t = ev.target;
      if (!(t instanceof HTMLInputElement) || !t.matches('[data-release-time]') || !t.value) return;
      const times = [...el.querySelectorAll<HTMLInputElement>('[data-release-time]')].map(i => i.value).filter(Boolean);
      if (times.join() !== (RELEASE_VIEW?.times || []).join()) void releaseSave({times});
    });
  }
  el.onclick = async ev => {
    const target = eventElement(ev);
    if (target?.closest('[data-release-add]')) { void releaseSave({times: [...(RELEASE_VIEW?.times || []), '15:00'].slice(0, 2)}); return; }
    const drop = target?.closest<HTMLElement>('[data-release-drop]');
    if (drop) { void releaseSave({times: (RELEASE_VIEW?.times ?? []).filter((_, i) => i !== Number(drop.dataset.releaseDrop))}); return; }
    const now = target?.closest<HTMLButtonElement>('[data-release-now]');
    if (now && !now.disabled) {
      const box = el.querySelector<HTMLInputElement>('[data-release-note]');
      const out = await releaseNow(now, box?.value.trim());
      if (box?.isConnected && !out.failed) { box.value = ''; now.disabled = !RELEASE_VIEW?.pending; }
      releaseStatus(out.text, out.failed);
    }
  };
}
async function renderReleaseCard(): Promise<void> {
  if (!document.querySelector('#set-release')) return;
  try { await releaseScheduleLoad(); } catch { return; }   // a team with no schedule to read keeps the card hidden
  if (Array.isArray(RELEASE_VIEW?.times)) releaseCardPaint();
}

// The board's Pending Release column carries Release now for those who may ask (tasks.js draws the slot).
function boardReleaseSlots(): void {
  const slots = document.querySelectorAll<HTMLElement>('[data-release-slot]');
  if (!slots.length) return;
  const fill = (v: SettingsReleaseSchedule): void => slots.forEach(slot => {
    slot.innerHTML = releaseNowButtonHTML(v, 'ghost board-release');
    const button = slot.querySelector<HTMLButtonElement>('[data-release-now]');
    if (button) button.onclick = async ev => { ev.stopPropagation(); const out = await releaseNow(button); toast(out.text); };
  });
  if (RELEASE_VIEW && Date.now() - RELEASE_AT < 60000) { fill(RELEASE_VIEW); return; }
  releaseScheduleLoad().then(v => { if (Array.isArray(v?.times)) fill(v); }).catch(() => {});
}

// The Task Manager (backend/task_manager.py): a built-in helper that checks each department's tasks on a schedule
// and files one check task per department with what it found. The owner pauses and resumes it.
interface SettingsTaskManagerDepartment {
  department: string;
  name: string;
  checked?: string | null;
  items: number;
  task?: { id: string; title?: string } | null;
}
interface SettingsTaskManagerView {
  on: boolean;
  state?: string;
  bot?: string;
  last_run?: { started_at?: string | null; finished_at?: string | null } | null;
  departments?: SettingsTaskManagerDepartment[];
}
let TASK_MANAGER_VIEW: SettingsTaskManagerView | null = null;
function taskManagerHTML(v: SettingsTaskManagerView): string {
  const owner = S.me?.role === 'owner';
  const state = v.on ? 'On' : v.state === 'paused' ? 'Paused' : 'Off';
  const last = v.last_run && (v.last_run.finished_at || v.last_run.started_at);
  const toggle = owner ? `<button type="button" class="ghost" data-tm-toggle="${v.on ? 'off' : 'on'}">${v.on ? 'Pause' : 'Resume'}</button>` : '';
  const rows = (v.departments || []).map(d => `<div class="tm-row" data-tm-dept="${esc(d.department)}">
      <strong>${esc(d.name)}</strong>
      <span class="tnum${d.checked ? '' : ' muted'}" ${d.checked ? `title="${esc(fmt(d.checked))}"` : ''}>${d.checked ? esc(ago(d.checked)) : 'Not checked'}</span>
      <span class="tnum${d.items ? '' : ' muted'}">${d.items} item${d.items === 1 ? '' : 's'}</span>
      ${d.task ? `<a href="#/task/${encodeURIComponent(d.task.id)}" title="${esc(d.task.title || '')}">Check task</a>` : '<span class="muted">—</span>'}
    </div>`).join('');
  return `<div class="tm-head"><span class="sysr-state" data-tm-state>${state}</span>
      ${last ? `<span class="muted">Last run <span class="tnum" title="${esc(fmt(last))}">${esc(ago(last))}</span></span>` : '<span class="muted">Not run yet</span>'}
      ${toggle}${botPageView()?.slug === v.bot ? '' : `<a class="ghost" href="#/bot/${encodeURIComponent(String(v.bot))}">Open</a>`}</div>
    <div class="tm-list">${rows}</div><p class="settings-cell-note" role="status" data-tm-status></p>`;
}
function taskManagerPaint(): void {
  const el = document.querySelector<HTMLElement>('#set-task-manager'), card = document.querySelector<HTMLElement>('#set-task-manager-card');
  if (!el || !card || !TASK_MANAGER_VIEW) return;
  card.hidden = false;
  el.innerHTML = taskManagerHTML(TASK_MANAGER_VIEW);
  el.onclick = async ev => {
    const toggle = eventElement(ev)?.closest<HTMLButtonElement>('[data-tm-toggle]'); if (!toggle) return;
    toggle.disabled = true;
    try {
      await post('/v2/task-manager/turn-' + toggle.dataset.tmToggle, {});
      TASK_MANAGER_VIEW = await get<SettingsTaskManagerView>('/v2/task-manager'); taskManagerPaint();
    } catch (e) {
      toggle.disabled = false;
      // The status line is drawn with the card above.
      const box = el.querySelector<HTMLElement>('[data-tm-status]')!; box.textContent = errorMessage(e, 'Try again'); box.classList.add('err');
    }
  };
}
async function renderTaskManagerCard(): Promise<void> {
  if (!document.querySelector('#set-task-manager')) return;
  // An install without the Task Manager answers 404; the card stays hidden.
  try { TASK_MANAGER_VIEW = await get<SettingsTaskManagerView>('/v2/task-manager'); } catch { return; }
  if (Array.isArray(TASK_MANAGER_VIEW?.departments)) taskManagerPaint();
}

// Screening (backend/screening.py): a switch per detector (patterns, decision model), a mode per door outside text comes
// in by, a Test of the built-in samples, and the newest holds and warnings. Owners and admins only: Security is their tab.
interface SettingsScreeningView {
  modes: Record<string, string>;
  sources: { id: string; name: string }[];
  layers?: { patterns?: boolean; model?: boolean } | null;
  model?: boolean;
  recent?: SettingsScreeningRecent[];
}
interface SettingsScreeningRecent { action: string; source?: string | null; score?: number | null; at?: string; task?: string | null; }
interface SettingsScreeningTest {
  passed: number;
  total: number;
  model?: boolean;
  results: { name: string; attack?: boolean; action: string; score?: number | null; ok?: boolean }[];
}
let SCREENING_VIEW: SettingsScreeningView | null = null, SCREENING_TEST: SettingsScreeningTest | null = null;
const SCREENING_MODES: Record<string, string> = {off: 'Off', warn: 'Warn', hold: 'Hold'};
const screeningScore = (s: number | null | undefined): string => (typeof s === 'number' ? `${Math.round(s * 100)}%` : '—');
function screeningSourceName(id: string | null | undefined): string { return (SCREENING_VIEW?.sources || []).find(s => s.id === id)?.name || id || ''; }
function screeningTestHTML(t: SettingsScreeningTest | null | undefined): string {
  if (!t) return '';
  const rows = t.results.map(r => `<div class="tm-row"><strong>${esc(r.name)}</strong>
      <span class="muted">${r.attack ? 'Attack' : 'Safe'}</span>
      <span class="tnum">${esc(r.action === 'pass' ? 'Pass' : SCREENING_MODES[r.action] || r.action)} ${screeningScore(r.score)}</span>
      <span class="${r.ok ? 'muted' : 'err'}">${r.ok ? 'OK' : 'Wrong'}</span></div>`).join('');
  return `<div class="tm-head"><strong class="tnum">${t.passed}/${t.total}</strong>${t.model ? '' : '<span class="muted">Patterns only</span>'}</div><div class="tm-list">${rows}</div>`;
}
function screeningRecentHTML(items: SettingsScreeningRecent[] | null | undefined): string {
  if (!items?.length) return '<p class="muted">No holds or warnings yet</p>';
  return `<div class="tm-list">${items.map(r => `<div class="tm-row"><strong>${r.action === 'held' ? 'Held' : 'Warned'} · ${esc(screeningSourceName(r.source))}</strong>
      <span class="tnum">${screeningScore(r.score)}</span>
      <span class="tnum muted" title="${esc(fmt(r.at))}">${esc(ago(r.at))}</span>
      ${r.task ? `<a href="#/task/${encodeURIComponent(r.task)}">Task</a>` : '<span class="muted">—</span>'}</div>`).join('')}</div>`;
}
function screeningHTML(v: SettingsScreeningView): string {
  const pick = (s: { id: string; name: string }): string => `<select class="settings-inline-select" data-screening-mode="${esc(s.id)}" aria-label="${esc(s.name)}">${
    (s.id === 'mail' ? ['off', 'warn'] : ['off', 'warn', 'hold']).map(m => `<option value="${m}"${v.modes[s.id] === m ? ' selected' : ''}>${SCREENING_MODES[m]}</option>`).join('')}</select>`;
  const layers: { patterns?: boolean; model?: boolean } = v.layers || {patterns: true, model: true};
  const layer = (id: 'patterns' | 'model', name: string, extra = ''): string => `<div class="sb-row"><span>${name}</span><div class="rel-line"><label class="sysr-switch">${
    peopleSwitch(`data-screening-layer="${id}"`, layers[id], false, name)}<span>${layers[id] ? 'On' : 'Off'}</span></label>${extra}</div><span></span></div>`;
  return `<div class="sb-rows rel-rows">
    ${layer('patterns', 'Patterns')}
    ${layer('model', 'Model', layers.model && !v.model ? '<span class="muted">Decide is off</span>' : '')}
    ${v.sources.map(s => `<div class="sb-row"><span>${esc(s.name)}</span>${pick(s)}<span></span></div>`).join('')}
    <div class="sb-row"><span>Test</span><div class="rel-line"><button type="button" class="ghost" data-screening-test>Run test</button></div><span></span></div>
  </div><p class="settings-cell-note" role="status" data-screening-status></p>
  <div data-screening-results>${screeningTestHTML(SCREENING_TEST)}</div>
  <h3>Recent</h3>${screeningRecentHTML(v.recent)}`;
}
function screeningStatus(text: string, failed?: boolean): void {
  const box = document.querySelector<HTMLElement>('#set-screening [data-screening-status]');
  if (box) { box.textContent = text; box.classList.toggle('err', !!failed); }
}
function screeningPaint(): void {
  const el = document.querySelector<HTMLElement>('#set-screening'), card = document.querySelector<HTMLElement>('#set-screening-card');
  if (!el || !card || !SCREENING_VIEW) return;
  if (formBusy(el)) return;
  card.hidden = false;
  el.innerHTML = screeningHTML(SCREENING_VIEW);
  el.onchange = async ev => {
    // A layer is a switch (a checkbox); a mode is a select.
    const t = eventElement(ev)?.closest<HTMLElement>('[data-screening-mode], [data-screening-layer]'); if (!t) return;
    const layerKey = t.dataset.screeningLayer;
    const body = layerKey ? {[layerKey]: t instanceof HTMLInputElement && t.checked} : {[t.dataset.screeningMode ?? '']: t instanceof HTMLSelectElement ? t.value : ''};
    try { SCREENING_VIEW = await put('/v2/screening', body); screeningPaint(); screeningStatus('Saved'); }
    catch (e) { screeningPaint(); screeningStatus(errorMessage(e, 'Try again'), true); }
  };
  el.onclick = async ev => {
    const button = eventElement(ev)?.closest<HTMLButtonElement>('[data-screening-test]'); if (!button) return;
    button.disabled = true; screeningStatus('Running…');
    try {
      SCREENING_TEST = await post('/v2/screening/test', {});
      // The results box is drawn with the card above.
      el.querySelector<HTMLElement>('[data-screening-results]')!.innerHTML = screeningTestHTML(SCREENING_TEST); screeningStatus('');
    } catch (e) { screeningStatus(errorMessage(e, 'Try again'), true); }
    finally { if (button.isConnected) button.disabled = false; }
  };
}
async function renderScreeningCard(): Promise<void> {
  if (!document.querySelector('#set-screening')) return;
  try { SCREENING_VIEW = await get<SettingsScreeningView>('/v2/screening'); }
  catch (e) { const el = document.querySelector<HTMLElement>('#set-screening'); if (el && !SCREENING_VIEW) el.innerHTML = `<div class="empty">Couldn't load screening: ${esc(errorMessage(e, 'try again'))}</div>`; return; }
  if (SCREENING_VIEW?.modes) screeningPaint();
}

// A built-in bot's own card on its page's Settings tab (ui/app/bot-page.js), drawn only on that bot's page: the Task
// Manager's checks, the Strategy Bot's levels (the owner's alone), and Releases on whichever bot the release schedule
// names or finds as its Release Manager. Each keeps the permissions it had in Settings: the server's answers decide.
function botBuiltinCardsHTML(slug: string): string {
  if (slug === 'task-manager') return '<section class="card" id="set-task-manager-card" hidden><header><h2>Task Manager</h2><span class="pill">Built-in</span></header><div id="set-task-manager"></div></section>';
  if (slug === 'goal-manager' && settingsStrategyBotShown()) return `<section class="card" id="set-strategy-bot-card"><header><h2>${esc(strategyBotName())}</h2><span class="pill">Built-in</span></header>
    <div id="set-strategy-bot">${skelRows(3, {card: true, dot: false})}</div></section>`;
  return '';
}
async function botBuiltinCardsMount(slug: string): Promise<void> {
  if (slug === 'task-manager') void renderTaskManagerCard();
  if (slug === 'goal-manager') void renderSettingsStrategyBot();
  const host = document.querySelector<HTMLElement>('#bot-builtin-cards');
  try { await releaseScheduleLoad(); } catch { return; }   // a team with no schedule to read has no Releases card
  if (botPageView()?.slug !== slug || !host?.isConnected || (RELEASE_VIEW?.bot || RELEASE_VIEW?.release_manager) !== slug) return;
  if (!document.querySelector('#set-release-card')) host.insertAdjacentHTML('afterbegin', '<section class="card" id="set-release-card" hidden><header><h2>Releases</h2></header><div id="set-release"></div></section>');
  void renderReleaseCard();
}
