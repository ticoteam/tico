/* ui/app/bot-page.js — The bot page: header, tabs, goals, ticker, slash commands, recent runs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- pages
// /status from the v2 server no longer carries recent_runs, so a bot page falls back to /runs,
// cached for a minute so the 30s refresh does not refetch it every pass.
let RUNS_CACHE = {at: 0, list: []};
async function recentRuns() {
  if (Date.now() - RUNS_CACHE.at > 60000) RUNS_CACHE = {at: Date.now(), list: await get('/runs')};
  return RUNS_CACHE.list || [];
}

// ----------------------------------------------------------------- bot page
// Header (name, alert, goal) stays visible; work and configuration tabs live in the URL
// as #/bot/<slug>/<tab>, so refresh and Back keep the tab. Panes stay in the DOM once loaded; only
// the thread stops following when you leave Home or DM.
// Configuration has its own five tabs (Rules lists what the bot must follow, ui/app/org-rules.js); old More, Docs and Session links retain a destination.
const BOT_DETAIL_TABS = ['info', 'rules', 'instructions', 'history', 'settings'];
// A bot's conversation is two threads: its Home (public, ui/app/thread-view.js) and the viewer's DM with it.
const BOT_TABS = ['home', 'dm', 'tasks', ...BOT_DETAIL_TABS];
const BOT_WORK_TABS = ['home', 'dm', 'tasks'];
const BOT_TAB_LABELS = {home: 'Home', dm: 'DM', tasks: 'Tasks', info: 'Info'};
// The assistant is talked to only on each person's own Assistant page (#/assistant), and its helpers take their work
// from it: neither has a DM, and the assistant has no Home.
const botTabs = slug => {
  const tabs = BOT_TABS.filter(t => !(t === 'dm' && (slug === assistantBot() || isAssistantHelper(slug))) && !(t === 'home' && slug === assistantBot()));
  const e = S.emps.find(x => x.name === slug), mine = e?.my_access;
  const noDm = e && e.can_chat === false ? tabs.filter(t => t !== 'dm') : tabs;
  // Seen but not read: no Home, updates or docs, and DM and Tasks only for someone who may write to it (the
  // page then holds their own DM and tasks with the bot). Every teammate reads its Rules.
  return mine && !mine.read ? noDm.filter(t => t === 'info' || t === 'rules' || (mine.write && (t === 'dm' || t === 'tasks'))) : noDm;
};
const botLimited = e => !!e?.my_access && !e.my_access.read;
const botPersonName = id => (S.people || []).find(p => p.id === id)?.name || settingsPersonName(id);
let BOT = null;
function botStopped() {
  BOT?.settingsCleanup?.();
  if (BOT?.thread) threadUnmount(BOT.thread.view);
  BOT = null;
}
// Pause is one row in the bot's Settings, not a strip on the page; the header's Paused badge says it at a glance.
// The control API remains the sole authority; failed writes never change the shown state.
function botPauseHTML(slug, state, error = '') {
  const retry = '<button type="button" class="ghost small" data-bot-pause-retry>Retry</button>';
  if (!state) return error ? `<p class="err" role="alert">${esc(error)} ${retry}</p>` : '<p class="muted" role="status">Loading…</p>';
  const e = S.emps.find(x => x.name === slug), name = e?.display_name || slug;
  const paused = state.state === 'paused', record = state.paused_by;
  const stamp = record && Number.isFinite(Date.parse(record.at))
    ? `${new Date(record.at).toISOString().slice(0, 16).replace('T', ' ')} UTC` : '';
  const can = state.can_control && !state.company_paused && ['active', 'paused'].includes(state.state);
  const tasks = paused && state.open_tasks ? ` <a href="#/bot/${encodeURIComponent(slug)}/tasks">${state.open_tasks} open task${state.open_tasks === 1 ? ' is' : 's are'} waiting on it</a>` : '';
  return `<div class="bot-pause-row">
    <div class="bot-pause-copy"><div class="bot-pause-title${paused ? ' bot-pause-record' : ''}" role="status" aria-live="polite" aria-atomic="true">${paused ? (stamp ? `Paused by ${esc(record.name)} on ${esc(stamp)}` : 'Paused') : 'Pause this bot'}</div>
      <p class="muted">${paused ? `${esc(name)} is paused: no tasks, messages or routines until it is unpaused.${tasks}${!state.can_control ? ` Ask ${esc(state.operator || 'its operator')} to unpause.` : ''}`
        : 'Stops new tasks, messages and routines; a turn in progress is interrupted.'}</p>
      ${state.company_paused ? '<p class="muted">Company is paused for billing. <a href="#/settings/billing">Add credit</a></p>' : ''}
      ${error ? `<p class="err" role="alert">${esc(error)} ${retry}</p>` : ''}</div>
    ${can ? `<button type="button" class="ghost" data-bot-pause-action="${paused ? 'resume' : 'pause'}">${paused ? 'Unpause' : 'Pause'}</button>` : ''}
  </div>`;
}
function botPauseDraw() {
  const host = $('#bot-control-host'); if (!host || !BOT) return;
  const focus = host.contains(document.activeElement);
  patchLiveHTML(host, botPauseHTML(BOT.slug, BOT.pauseState, BOT.pauseError));
  const button = host.querySelector('[data-bot-pause-action]');
  if (button) { button.disabled = !!BOT.pauseBusy; button.onclick = () => botPauseChange(BOT, button.dataset.botPauseAction); }
  const retry = host.querySelector('[data-bot-pause-retry]');
  if (retry) { retry.disabled = !!BOT.pauseBusy; retry.onclick = async () => { const view = BOT, action = view.pauseRetry; await botPauseLoad(); if (action && BOT === view) await botPauseChange(view, action); }; }
  if (focus && !host.contains(document.activeElement)) (button || retry)?.focus();
}
// The header's and the tree's Paused badges read the roster, which only catches up at its next poll. The pause state
// (a fresh read or a finished control) is the authority, so the roster entry follows it as soon as it is known.
function botPauseFollow(view) {
  const emp = S.emps.find(x => x.name === view.slug), paused = view.pauseState?.state === 'paused', badge = $('#bot-paused');
  if (emp && paused !== (emp.status === 'paused')) { emp.status = paused ? 'paused' : 'active'; renderTree(); }
  if (badge) patchLiveHTML(badge, botPausedBadge(view.slug));
}
async function botPauseLoad() {
  const view = BOT;
  if (!view || view.pauseLoading || view.pauseBusy) return;
  view.pauseLoading = true;
  const generation = view.pauseGeneration || 0;
  try {
    const value = await get(`/v2/bots/${encodeURIComponent(view.slug)}/pause-state`, {fresh: true});
    if (BOT !== view || view.pauseBusy || generation !== (view.pauseGeneration || 0)) return;
    view.pauseState = value;
    view.pauseLoadedAt = Date.now();
    if (!view.pauseRetry) view.pauseError = '';
    botPauseFollow(view);
  } catch (error) {
    if (BOT === view && generation === (view.pauseGeneration || 0) && !view.pauseState) view.pauseError = `Could not load bot state: ${error.message}`;
  } finally {
    view.pauseLoading = false;
    if (BOT === view) {
      botPauseDraw();
      // The control may have finished while this older read waited on billing. Re-read its committed state.
      if (generation !== (view.pauseGeneration || 0) && !view.pauseBusy && !view.pauseRetry) void botPauseLoad();
    }
  }
}
async function botPauseChange(view, action) {
  if (BOT !== view || view.pauseBusy || !view.pauseState?.can_control || view.pauseState.company_paused) return;
  // Invalidate any refresh already in flight, even if it arrives after the control finishes.
  view.pauseGeneration = (view.pauseGeneration || 0) + 1;
  view.pauseBusy = true; view.pauseError = ''; view.pauseRetry = null; botPauseDraw();
  try {
    const result = await post(`/v2/bots/${encodeURIComponent(view.slug)}/control`, {action, expected_revision: view.pauseState.revision});
    if (BOT !== view) return;
    view.pauseState = {...view.pauseState, state: action === 'pause' ? 'paused' : 'active', revision: result.revision,
      paused_by: null};
    botPauseFollow(view);
    const name = S.emps.find(x => x.name === view.slug)?.display_name || view.slug;
    toast(`${name} is ${action === 'pause' ? 'paused.' : 'unpaused'}`, false,
      action === 'pause' ? {label: 'Unpause', run: () => botPauseChange(view, 'resume')} : null);
  } catch (error) {
    if (BOT === view) { view.pauseError = `Could not ${action === 'pause' ? 'pause' : 'unpause'}: ${error.message}`; view.pauseRetry = action; }
  } finally {
    view.pauseBusy = false;
    if (BOT === view) { botPauseDraw(); if (!view.pauseRetry) void botPauseLoad(); }
  }
}
function botStatePill(slug) {
  const st = stateOf(slug);
  const act = (S.status?.active || []).find(a => a.employee === slug);
  const last = (S.status?.recent_runs || []).find(r => r.employee === slug);
  if (st === 'running') return `<span class="pill in-progress">Running${act?.issue ? ` on #${esc(act.issue)}` : ''}</span>`;
  if (st === 'needs') return '<span class="pill needs">Needs you</span>';
  if (st === 'paused') return '<span class="pill">Paused</span>';
  if (st === 'planned') return '<span class="pill">Setting up</span>';
  if (st === 'failed') return `<span class="pill fail">Last run failed${last?.finished ? ` ${ago(last.finished)}` : ''}</span>`;
  return `<span class="pill">Idle${last?.finished ? `, last active ${ago(last.finished)}` : ''}</span>`;
}
// The server cuts the Role section at 600 characters, so the lead can stop mid-sentence: end at the
// last full sentence instead, and put whatever is left behind "more".
function goalParts(role) {
  const full = String(role || '').trim();
  const para = (full.split(/\n\s*\n/)[0] || full).trim();
  let lead = para;
  if (!/[.!?]["'’)\]]?$/.test(para)) {
    let cut = 0;
    for (const m of para.matchAll(/[.!?]["'’)\]]?(?=\s|$)/g)) cut = m.index + m[0].length;
    if (cut > 60) lead = para.slice(0, cut).trim();
  }
  return {lead, rest: full.slice(lead.length).trim()};
}
// The bot's goal, at the top of its page (#514): its first goal in the owner's order, as words.
// Tapping it opens it on the Goals page. No goal is one quiet link: "Set goal".
let BOT_GOAL_LOAD = 0;
async function botGoalLoad(slug) {
  if (!$('#bot-goal')) return;
  const load = ++BOT_GOAL_LOAD;
  // A deploy restarts the hub for a few seconds and a phone drops a request now and then: try
  // again quietly before giving up, and a miss shows nothing and tries again in 30 s.
  let r = null;
  for (const wait of [0, 600, 2000]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (load !== BOT_GOAL_LOAD || BOT?.slug !== slug) return;
    r = await v2Get(`/v2/goals?owner=${encodeURIComponent('bot:' + slug)}`);
    if (r) break;
  }
  const mine = (r?.goals || []).filter(goalLive).sort(goalRank);
  if (load !== BOT_GOAL_LOAD || BOT?.slug !== slug || !$('#bot-goal')) return;
  botGoalRender(slug, r ? mine[0] || null : undefined, mine.length);
}
// The Goals card under More is the bot's goals as plain lines, each edited in
// place with the link icon for the goal it supports; the same goals the top line shows and the
// bot reads.
async function botGoalsCardLoad(slug) {
  const [r, goals] = await Promise.all([v2Get(`/v2/goals?owner=${encodeURIComponent('bot:' + slug)}`), goalOptions()]);
  const host = $('#bot-goals-list');
  if (!host || BOT?.slug !== slug) return;
  if (!r) { host.innerHTML = '<div class="empty">Could not load the goals.</div>'; return; }
  const live = (r.goals || []).filter(goalLive).sort(goalRank);
  const all = goals.some(g => g.id === live[0]?.id) ? goals : [...goals, ...live];
  host.innerHTML = live.map(g => `<div class="bot-goal-row">${goalEditorHtml(g, all)}</div>`).join('')
    + '<div class="bot-goal-row" id="bot-goal-adding" hidden></div>'
    + (live.length ? '' : '<div class="empty" id="bot-goals-none">No goal yet.</div>')
    + '<div id="bot-goals-finished"></div>';
  for (const el of host.querySelectorAll('[data-goal-ed]')) {
    const g = live.find(x => x.id === el.dataset.goalEd);
    bindGoalEditor(el, g, () => botGoalsCardLoad(slug));
  }
  for (const a of host.querySelectorAll('[data-goal-go]'))
    a.onclick = ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); openGoal(a.dataset.goalGo); };
  // Its done and dropped goals, under the live ones: "Finished (N)" (ui/app/goals.js).
  const finished = await v2Get('/v2/goals?all=true&status=done,dropped&owner=' + encodeURIComponent('bot:' + slug));
  if (!finished || BOT?.slug !== slug || !host.isConnected) return;
  goalFinishedMount(host.querySelector('#bot-goals-finished'), 'bot:' + slug, [...all, ...(finished.goals || [])], () => goalsRefresh());
}
function botGoalAdd(slug) {
  const host = $('#bot-goal-adding');
  if (!host) return;
  host.hidden = false; $('#bot-goals-none')?.remove();
  goalAddInline(host, 'bot:' + slug, () => botGoalsCardLoad(slug));
}
function botGoalRender(slug, g, count) {
  const el = $('#bot-goal');
  if (g === undefined) {
    el.removeAttribute('data-status'); el.innerHTML = '';
    setTimeout(() => { if (BOT?.slug === slug && $('#bot-goal') && !$('#bot-goal').innerHTML) botGoalLoad(slug); }, 30000);
    return;
  }
  if (!g) {
    el.dataset.status = 'none';
    el.innerHTML = '<a href="#" class="bot-goal-set" id="bot-goal-set">Set goal</a>';
    $('#bot-goal-set').onclick = async e => {
      e.preventDefault();
      el.innerHTML = '<span class="bot-goal-new"></span>';
      goalAddInline(el.firstChild, 'bot:' + slug, () => botGoalLoad(slug));
    };
    return;
  }
  el.dataset.status = 'set';
  el.innerHTML = `<a class="bot-goal-main" href="${GOALS}/${encodeURIComponent(g.id)}" data-goal-open="${esc(g.id)}"${count > 1 ? ` title="${count - 1} more under More"` : ''}>
      <span class="bot-goal-title">${esc(g.title)}</span></a>`;
  const a = el.querySelector('[data-goal-open]');
  a.onclick = ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); openGoal(a.dataset.goalOpen); };
}
// Why the bot's last run ended without finishing (backend/execution.py last_stop), never a bare "Crashed": one plain
// line, grey when nothing is needed and red when a person must act, and only an action that exists (`review`: the
// Stopped runs dialog, which retries or dismisses the run). A click opens what happened.
const lastStopOf = slug => { const s = v2StatusOf(slug); return ['crashed', 'idle'].includes(s?.state) ? s.last_stop || null : null; };
const stopReviewable = (slug, stop) => stop.action === 'review' && settingsCanManageBot(S.emps.find(x => x.name === slug));
function lastStopHTML(slug) {
  const stop = lastStopOf(slug); if (!stop) return '';
  const soft = stop.tone === 'soft';
  return `<button type="button" class="bot-alert bot-stop${soft ? ' soft' : ''}" data-last-stop${soft ? '' : ' role="status"'}>${soft ? '' : '<span aria-hidden="true">⚠</span> '}${esc(stop.text)}</button>`
    + (stopReviewable(slug, stop) ? '<button class="primary bot-alert-resume" type="button" data-stop-review>Retry or dismiss</button>' : '');
}
const stopReview = slug => void executionReview(slug, async () => { await v2Refresh(); botAlertDraw(); });
function lastStopOpen(slug) {
  const stop = lastStopOf(slug); if (!stop) return;
  const review = stopReviewable(slug, stop);
  const needed = !stop.needs_person ? 'No. Nothing was lost.'
    : stop.action === 'review' ? (review ? 'Yes: say whether to try it again or let it go.'
                                         : 'Yes: a person who manages this bot decides whether to try it again.')
    : 'Yes, as the line above says.';
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal'; dialog.id = 'last-stop';
  dialog.innerHTML = `<header><h2>Last run</h2><button class="ghost" type="button" data-close>Close</button></header>
    <p>${esc(stop.text)}</p>
    ${stop.reason ? `<p><strong>What happened</strong><br>${esc(stop.reason)}</p>` : ''}
    <p><strong>Is anything needed?</strong><br>${esc(needed)}</p>
    ${stop.at ? `<p class="muted">${esc(fmt(stop.at))}</p>` : ''}
    ${review ? '<div class="row"><button class="primary" type="button" data-stop-review>Retry or dismiss</button></div>' : ''}`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  const open = dialog.querySelector('[data-stop-review]');
  if (open) open.onclick = () => { dialog.close(); stopReview(slug); };
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
}
// Beside the bot's name only when something is wrong (#514): crashed, quarantined, rate limited,
// blocked, a failed last run, or an agent that is offline while work waits for it. Idle says nothing.
function botAlertHTML(slug) {
  const e = S.emps.find(x => x.name === slug) || {}, s = v2StatusOf(slug);
  let word = '', why = '';
  if (s?.state === 'crashed' && s.last_stop) return lastStopHTML(slug);
  // Tico stopped it (failed runs, or over its run budget): the words are the server's, Release lifts it.
  if (s?.stopped) return `<span class="bot-alert" role="status" title="${esc(s.focus || '')}" data-bot-stopped="${esc(s.stopped)}"><span aria-hidden="true">⚠</span> Stopped</span>${botReleaseButton(slug, 'primary bot-alert-resume')}`;
  if (s && ['crashed', 'quarantined', 'limited', 'blocked'].includes(s.state)) {
    word = s.state === 'crashed' ? 'Last run stopped' : s.state === 'quarantined' && s.quarantine?.auto ? 'Quarantined' : V2_WORD[s.state]; why = s.state === 'quarantined' ? quarantineWhy(slug) : s.focus || '';
  }
  else if (!s && stateOf(slug) === 'failed') word = 'Last run failed';
  else if (e.agent?.synced && !e.online) { word = agentPresenceLabel(e.agent, false); why = 'Imported history needs a sync.'; }
  else if (e.agent?.credential && !e.online && ((S.status?.queued || []).some(q => (q.bot || q.employee) === slug) || s?.open_tasks))
    word = 'Offline with work waiting';
  else if (lastStopOf(slug)) return lastStopHTML(slug);
  // The way out sits beside the warning, for a person who may take it.
  const resume = s?.bot_state === 'quarantined' && settingsCanManageBot(e) ? '<button class="primary bot-alert-resume" type="button" data-quarantine-resume>Resume bot</button>' : limitRetryButton(slug, 'primary bot-alert-resume');
  return word ? `<span class="bot-alert" role="status"${why ? ` title="${esc(why)}"` : ''}><span aria-hidden="true">⚠</span> ${esc(word)}</span>${resume}` : '';
}
// Under More for a person who manages a quarantined bot: why, the refused words and the task they were on.
function botQuarantineCard(slug) {
  const s = v2StatusOf(slug), e = S.emps.find(x => x.name === slug);
  if (s?.bot_state !== 'quarantined' || !e || botLimited(e) || !settingsCanManageBot(e)) return '';
  const r = s.quarantine?.review || {};
  return `
    <section class="card" id="bot-quarantine"><header><h2>${s.quarantine?.auto ? 'Quarantined for repeated refused writes' : 'Paused for review'}</h2>
        <button class="primary" type="button" data-quarantine-resume>Resume bot</button></header>
      <p>${esc(quarantineWhy(slug))} ${esc(QUARANTINE_RESUME_NOTE)}</p>
      ${r.preview ? `<blockquote class="bot-quarantine-text">${esc(r.preview)}</blockquote>` : ''}
      ${r.task ? `<p>Task: <a href="#/task/${encodeURIComponent(r.task.id)}">${esc(r.task.title)}</a></p>` : ''}
      <p class="muted" id="bot-quarantine-result"></p>
    </section>`;
}
// The banner at the top of a bot's page while it is over its spend limit; Budget, open at the bot, is where a limit is raised.
function botLimitHTML(slug) {
  const limit = overLimit(slug); if (!limit) return '';
  const usd = n => '$' + Number(n || 0).toFixed(2);
  return `<section class="bot-limit" role="status"><span aria-hidden="true">⚠</span> ${esc(overLimitWord(limit))}${limit.limit != null ? ` (${usd(limit.spent)} of ${usd(limit.limit)})` : ''}. <a href="#/strategy/budget/${encodeURIComponent('bot:' + slug)}">Raise limit</a></section>`;
}
// The alert beside the name and the limit banner come and go with the status poll and live events.
// The Settings pause row re-reads HQ-backed state only when the page's view of paused is out of date, or a minute old.
const PAUSE_STALE_MS = 60_000;
function botPauseStale(view) {
  if (!view.pauseState || view.pauseLoadedAt == null) return true;
  if ((stateOf(view.slug) === 'paused') !== (view.pauseState.state === 'paused')) return true;
  return Date.now() - view.pauseLoadedAt >= PAUSE_STALE_MS;
}
function botAlertDraw() {
  if (!BOT) return;
  if (botPauseStale(BOT)) void botPauseLoad();
  const alert = $('#bot-alert'), limit = $('#bot-limit-host'), held = $('#bot-quarantine-host'), paused = $('#bot-paused');
  if (alert) patchLiveHTML(alert, botAlertHTML(BOT.slug));
  if (paused) patchLiveHTML(paused, botPausedBadge(BOT.slug));
  if (held) patchLiveHTML(held, botQuarantineCard(BOT.slug));
  if (limit) patchLiveHTML(limit, botLimitHTML(BOT.slug));
}
// Files dragged over any part of a bot's chat attach to its composer, which lights up to say so
// (#524, after bot-desk). A file dropped anywhere else on the page is swallowed: the browser's own
// answer is to navigate away to it, and nothing here wants that. A file input keeps its own drop.
const draggingFiles = ev => [...(ev.dataTransfer?.types || [])].includes('Files');
['dragover', 'drop'].forEach(t => window.addEventListener(t, ev => {
  if (draggingFiles(ev) && !ev.target.closest?.('input[type=file]')) ev.preventDefault();
}));
// `pillNow` is the pill, or a function giving the one there now (the Ask panel keeps its zone and makes a new pill per load).
function chatDropZone(zone, pillNow) {
  const cur = () => typeof pillNow === 'function' ? pillNow() : pillNow;
  let depth = 0;
  const on = v => { const P = cur(); if (P) pq(P, '.p-pill')?.classList.toggle('over', v); };
  zone.addEventListener('dragenter', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); depth++; on(true); });
  zone.addEventListener('dragover', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) on(false); });
  zone.addEventListener('drop', ev => {
    const P = cur();
    if (!draggingFiles(ev) || !P) return;
    ev.preventDefault(); depth = 0; on(false);
    P.files.push(...ev.dataTransfer.files); pillChips(P);
    pq(P, '.p-text')?.focus();
  });
}
// On a phone, drag the thread left to see when each line was sent, as in Messages; it springs back.
function bindChatSwipe(thread) {
  if (!thread) return;
  let x0 = 0, y0 = 0, sideways = null;
  thread.addEventListener('touchstart', ev => {
    x0 = ev.touches[0].clientX; y0 = ev.touches[0].clientY; sideways = null; thread.classList.remove('stamps-settle');
  }, {passive: true});
  thread.addEventListener('touchmove', ev => {
    const dx = ev.touches[0].clientX - x0, dy = ev.touches[0].clientY - y0;
    if (sideways === null && Math.abs(dx) + Math.abs(dy) > 8) sideways = Math.abs(dx) > Math.abs(dy);
    if (sideways) thread.style.setProperty('--stamp-shift', Math.max(0, Math.min(70, -dx)) + 'px');
  }, {passive: true});
  const settle = () => { if (sideways) { thread.classList.add('stamps-settle'); thread.style.setProperty('--stamp-shift', '0px'); } sideways = null; };
  thread.addEventListener('touchend', settle);
  thread.addEventListener('touchcancel', settle);
}
async function pageBot(slug, tab) {
  const e = S.emps.find(x => x.name === slug);
  if (!e) { BOT = null; $('#main').innerHTML = `<div class="empty">Unknown bot "${esc(slug)}".</div>`; return; }
  if (BOT?.slug === slug && $('#btabs')) { showBotTab(tab); return; }   // same bot, another tab
  const keeper = isKeeper(slug);        // hosted by the keeper: hub.db is its record (docs/history/hub-v2.md)
  const boss = S.emps.find(x => x.name === e.reports_to);
  const role = String(e.role || e.description || '').trim();
  const limited = botLimited(e);       // seen, not read: no activity anywhere on the page
  const work = botTabs(slug).filter(t => BOT_WORK_TABS.includes(t));
  const base = `#/bot/${encodeURIComponent(slug)}`;
  const tabsShown = [...work, 'info'];
  const talkTabs = work.filter(t => t === 'home' || t === 'dm');
  // The goal leads the page; chat and tasks sit side by side under it on a
  // desktop. Narrower, the top line holds the bot and Chat · Tasks · Info; configuration has its own tabs.
  // On a phone the goal and the extra controls move into Info (placeBotHead). A status shows
  // only when it is an alert. Configuration opens on Info.
  BOT?.settingsCleanup?.();
  $('#main').innerHTML = `
  <div class="bot-top" id="bot-top">
    <button class="bot-back-arrow" id="bot-back-arrow" type="button" aria-label="Back" title="Back">‹</button>
    <div class="bot-ident"><span data-tip-bot="${esc(slug)}" tabindex="0" role="img" aria-label="${esc(e.display_name || slug)} status">${avatar(slug, 36, stateOf(slug))}</span>
      <div class="botid"><div class="bot-nameline"><h1${role ? ` title="${esc(role)}"` : ''}>${shownName(e)}${runtimeTag(e)}${botBetaPill(e)}</h1><span id="bot-paused">${botPausedBadge(slug)}</span><button type="button" class="bot-learn" id="bot-learn" aria-label="Learnings" title="Learnings" hidden><span class="nav-icon" aria-hidden="true">psychology</span><span class="bl-label">Learnings</span><span class="bl-n" hidden></span></button>${limited ? '' : botScreenLinkHTML(slug)}<span id="bot-tool-strip" hidden></span></div>
        <div class="meta" id="bot-branches"></div>
        <div class="meta" id="bot-alert">${limited ? '' : botAlertHTML(slug)}</div></div></div>
    <div class="bot-switch" id="btabs" role="tablist" aria-label="${esc(e.display_name || slug)}">
      ${tabsShown.map(t => { const label = BOT_TAB_LABELS[t];
        return `<button type="button" data-bt="${t}" role="tab">${label}</button>`; }).join('')}
    </div>
    ${limited ? '' : '<section class="bot-goal" id="bot-goal" aria-label="Goal"><span class="muted">Loading the goal…</span></section>'}
    <a class="ghost bot-more-btn" id="bot-more-btn" href="${base}/info" aria-label="Info" title="Info"><span class="nav-icon" aria-hidden="true">more_vert</span></a>
  </div>
  <div id="bot-banners"><div id="bot-limit-host">${limited ? '' : botLimitHTML(slug)}</div>
  <div id="bot-onboard-host">${frBotBannerHTML(e)}</div></div>
  <a class="bot-back" id="bot-back" href="${base}" hidden>‹ ${BOT_TAB_LABELS[work[0]] || 'Tasks'}</a>

  <div class="bot-work" id="bot-work">
  <div id="pane-chat" hidden>
    ${limited ? '' : `<div class="bot-talk-bar">${talkTabs.length > 1 ? `<div class="bot-talk-switch" role="tablist" aria-label="Home or DM">${talkTabs.map(t =>
      `<button type="button" role="tab" data-talk="${t}">${BOT_TAB_LABELS[t]}</button>`).join('')}</div>` : ''}<span class="spacer"></span>
      <a class="bot-threads-link" href="${THREADS_ROUTE}?with=${encodeURIComponent('bot:' + slug)}">Threads with ${esc(e.display_name || slug)}</a><span class="bot-talk-acts"></span></div>`}
    <div id="conv-paused" class="conv-paused" role="status" hidden></div>
    <div class="bot-thread" id="bot-thread"></div>
  </div>

  <aside id="pane-tasks" class="bot-rail" aria-label="Tasks" hidden${keeper && !botSidebarLayout(slug) ? ' data-loading' : ''}>
    ${keeper && !limited ? `<button type="button" class="rail-ico bot-sidebar-edit" id="bot-sidebar-edit" aria-label="Edit sidebar" title="Edit"${botSidebarLayout(slug)?.can_edit ? '' : ' hidden'}><span class="nav-icon" aria-hidden="true">edit</span></button>
    <div class="bot-rail-editbar" hidden><span class="muted" id="bot-sidebar-status" role="status"></span><span class="spacer"></span>
      <button type="button" class="ghost" id="bot-sidebar-add">Add</button><button type="button" class="ghost" id="bot-sidebar-cancel">Cancel</button><button type="button" class="primary" id="bot-sidebar-done">Done</button></div>` : ''}
    <div class="bot-rail-loading rail-empty"${keeper && !botSidebarLayout(slug) ? '' : ' hidden'}>Loading…</div>
    ${botWidgetsHTML(slug, {
      tasks: `<section class="rail-sec bot-tasks-sec" id="bot-tasks" aria-labelledby="bot-tasks-h"><header class="rail-head"><h2 class="rail-h" id="bot-tasks-h">Tasks</h2></header>
      <div class="tabs slim bot-task-tabs" role="tablist" aria-label="Task lists">${botTaskTabsBarHTML(slug)}</div>
      <div class="bot-task-panes">
      <div class="bot-needs" id="bot-needs" role="tabpanel" aria-labelledby="bot-tab-t-needs" hidden><div id="t-needs" class="tpane"><div class="rail-empty">Loading…</div></div></div>
      <div class="bot-active" id="bot-active" role="tabpanel" aria-labelledby="bot-tab-t-active" hidden><div id="t-open" class="tpane"><div class="rail-empty">Loading…</div></div></div>
      <div class="bot-blocked" id="bot-blocked" role="tabpanel" aria-labelledby="bot-tab-t-blocked" hidden><div id="t-blocked" class="tpane"><div class="rail-empty">Loading…</div></div></div>
      <div class="bot-done" id="bot-done" role="tabpanel" aria-labelledby="bot-tab-t-done" hidden><div id="t-done" class="tpane"><div class="rail-empty">Loading…</div></div></div>
      <div id="bot-task-custom">${botTaskCustomPanelsHTML(slug)}</div></div>
    </section>`,
      ...(limited ? {} : {
        updates: '<section class="rail-sec bot-latest" id="bot-latest" aria-label="Updates" hidden></section>',
        files: '<section class="rail-sec bot-files" id="bot-files" aria-label="Files" hidden></section>'})})}
  </aside>
  ${railEdgeHTML('right')}
  </div>

  <div id="bot-details" hidden>
    <div class="tabs bot-detail-tabs" id="bot-detail-tabs" role="tablist" aria-label="Bot details">
      ${BOT_DETAIL_TABS.filter(t => botTabs(slug).includes(t)).map(t => `<button type="button" id="bot-tab-${t}" data-bot-detail-tab="${t}" role="tab" aria-controls="${t === 'info' ? 'pane-more' : t === 'instructions' ? 'pane-docs' : 'pane-' + t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}
    </div>
    <div id="pane-more" role="tabpanel" aria-labelledby="bot-tab-info" hidden>
    <div class="bot-more-top" id="bot-more-top"></div>
    ${slug === 'goal-manager' && strategyBotLogShown() ? `<section class="card" id="strategy-bot-log-card"><header><h2>${esc(strategyBotName())} log</h2></header>
      <div id="strategy-bot-log"><div class="empty">Loading…</div></div></section>` : ''}
    ${limited ? `    <section class="card"><header><h2>About</h2></header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd>${botRoleHTML(role)}</dd>` : ''}
        <dt>Owner</dt><dd>${esc(botPersonName(e.operator))}</dd>
        <dt>Works for</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${boss.name}">${esc(boss.display_name)}</a></dd>`
          : String(e.reports_to || '').startsWith('human:') ? `<dt>Reports to</dt><dd>${esc(botPersonName(e.reports_to.slice(6)))}</dd>` : ''}
        <dt>Your access</dt><dd>${e.my_access.write ? 'See it and send requests' : 'See it only'}</dd>
        ${botScreenRowHTML(slug)}
      </dl></section>
    <section class="card" id="bot-tools-card"><header><h2>Tools</h2></header><div id="bot-tools"><div class="empty">Loading…</div></div></section>
` : `    <section class="card" id="bot-setup-card"><header><h2>Info</h2></header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd>${botRoleHTML(role)}</dd>` : ''}
        <dt>Humans</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${!e.agent ? `<dt>Computer</dt><dd>${cloudMachineOn(e) ? cloudMachineComputerHTML(e) : e.machine ? `${esc(e.machine.label || e.machine.runner_id)} <span class="muted">· ${e.online ? 'Online' : 'Offline'}</span>` : '<span class="muted">Not assigned</span>'}</dd>
        <dt>Harness</dt><dd>${esc(harnessWords(e.harness || e.runtime || e.resolved_runtime) || 'Team default')}</dd>` : ''}
        ${e.agent ? `<dt>Run by</dt><dd>${esc(agentKind(e.agent))}${e.agent.profile ? ` · profile <code>${esc(e.agent.profile)}</code>` : ''}${e.agent.version ? ` · ${esc(e.agent.version)}` : ''}${e.agent.platform ? ` · ${esc(e.agent.platform)}` : ''}<span class="muted"> · ${esc(agentPresenceLabel(e.agent, e.online).toLowerCase())}${e.agent.last_seen ? `, last ${e.agent.synced ? 'sync' : 'seen'} ${esc(ago(e.agent.last_seen))}` : ''}</span></dd>
        <dt>Model</dt><dd>${e.agent.model ? `${esc(e.agent.model)}${e.agent.provider ? `<span class="muted"> · ${esc(e.agent.provider)}</span>` : ''}` : "<span class=\"muted\">the profile's own</span>"}</dd>`
        : `<dt>Model</dt><dd>${esc(e.model ? settingsChoiceLabel(e.harness || e.runtime, e.model, e.reasoning_effort || e.effort) : settingsDefaultLabel(e))}${e.fallback ? `<span class="muted"> · fallback ${esc(settingsChoiceLabel(e.fallback.harness, e.fallback.model, e.fallback.reasoning_effort || e.fallback.effort))}</span>` : ''}</dd>`}
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${boss.name}">${esc(boss.display_name)}</a></dd>` : ''}
        ${botRepoHTML(e) ? `<dt>Repository</dt><dd>${botRepoHTML(e).replace(/^Repository /, '')}</dd>` : ''}
        ${botScreenRowHTML(slug)}
        <dt>Automations</dt><dd><a href="${AUTOMATIONS}?q=${encodeURIComponent(botDisplayName(slug))}" data-bot-automations>Open</a></dd>
      </dl></section>
    <section class="card" id="bot-tools-card"><header><h2>Tools</h2></header><div id="bot-tools"><div class="empty">Loading…</div></div></section>
    <div id="bot-quarantine-host">${botQuarantineCard(slug)}</div>
    <section class="card" id="bot-goals-card"><header><h2>Goals</h2>
        <button class="linkish" type="button" id="bot-goals-add">Add goal</button></header>
      <div id="bot-goals-list"><div class="empty">Loading…</div></div></section>

`}
  </div>
  ${limited ? '' : `<div id="pane-history" role="tabpanel" aria-labelledby="bot-tab-history" hidden>
    <section class="card" id="bot-updates-card"><header><h2>Updates</h2><a class="linkish" href="${UPDATES}">See updates</a></header>
      <div id="bot-updates"><div class="empty">Loading…</div></div></section>
    <section class="card"><header><h2>Activity</h2></header><div id="bot-history" class="bot-history"><div class="empty">Loading…</div></div></section>
    ${keeper ? `<section class="card" id="bot-status-card"><header><h2>Status history</h2></header>
      <div id="v2-history"><div class="empty">Loading…</div></div></section>` : ''}
    <section class="card" id="bot-runs-card"><header><h2>Runs</h2></header>
      ${(rs => rs.length ? `<div id="bot-recent-runs">${runsTable(rs, false)}</div>`
        : '<div id="bot-recent-runs" data-pending="1"><div class="muted">Loading…</div></div>'
      )((S.status?.recent_runs || []).filter(r => r.employee === slug).slice(0, 10))}</section>

    ${settingsCanManageBot(e) ? '<section class="card"><header><h2>Settings changes</h2></header><div id="bot-settings-history">Loading…</div></section>' : ''}
  </div>
  <div id="pane-settings" role="tabpanel" aria-labelledby="bot-tab-settings" hidden>
    <section class="card"><div class="bot-editor bot-editor-inline" id="bot-editor"><div class="empty">Loading settings…</div></div><div id="bot-settings-panels"></div></section>
    <section class="card"><header><h2>Screen</h2></header><dl class="bot-setup" id="bot-screen-settings"></dl></section>
    <section class="card" id="bot-access-card"><header><h2>Access</h2></header>
      ${accessTable(e)}</section>
    <section class="card" id="bot-pause-card" aria-label="Pause"><div id="bot-control-host">${botPauseHTML(slug, null)}</div></section>

  </div>`}

  <div id="pane-rules" role="tabpanel" aria-labelledby="bot-tab-rules" hidden><div id="bot-rules" class="ru-bot"></div></div>
  ${limited ? '<div id="pane-docs" role="tabpanel" aria-labelledby="bot-tab-instructions" hidden>' : `<div id="pane-docs" role="tabpanel" aria-labelledby="bot-tab-instructions" hidden>
    <section class="card"><header><h2>Instructions</h2></header><div class="md" id="agent">Loading…</div>
      <div id="bot-instructions-editor"></div></section>
    <section class="card"><header><h2>Working notes</h2></header>
      <div class="tabs" id="tabs"></div><div class="md" id="doc">Loading…</div></section>
  `}
  </div>
  </div>`;

  BOT = {slug, tab: null, loaded: new Set(), limited};
  void botPauseLoad();
  botScreenWire(slug);
  frBotWire(slug);
  orgHistoryVisit('b:' + slug);
  window.botTools?.mountStrip($('#bot-tool-strip'), {slug, get, esc, href: `${base}/tools`, skipModel: !!runtimeTag(e)});
  if (!limited) {
    void botBranchesLoad(slug);
    void botLearningsLoad(slug);
  }
  if (!limited) void botGoalLoad(slug);
  $('#btabs').onclick = ev => {
    const b = ev.target.closest('[data-bt]'); if (!b) return;
    location.hash = b.dataset.bt === work[0] ? `#/bot/${slug}` : `#/bot/${slug}/${b.dataset.bt}`;
  };
  // Wide, the top tabs give way to Home and DM beside Tasks; this switch picks which one the left side shows.
  const talk = $('#pane-chat .bot-talk-switch');
  if (talk) talk.onclick = ev => {
    const b = ev.target.closest('[data-talk]'); if (!b) return;
    location.hash = b.dataset.talk === work[0] ? `#/bot/${slug}` : `#/bot/${slug}/${b.dataset.talk}`;
  };
  // "The back arrow in a bot should always go back to home. Not previous page."
  $('#bot-back-arrow').onclick = () => { location.hash = UPDATES; };   // home
  $('#bot-detail-tabs').onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...event.currentTarget.querySelectorAll('button')];
    const i = buttons.indexOf(event.target); if (i < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].click(); buttons[next].focus();
  };
  $('#bot-detail-tabs').onclick = event => {
    const button = event.target.closest('[data-bot-detail-tab]');
    if (button) location.hash = `${base}/${button.dataset.botDetailTab}`;
  };
  // One click, from the warning beside the name or the card under More. Stopped runs settle on their own;
  // nothing to write or review.
  const resumeClick = async ev => {
    if (ev.target.closest('[data-stop-review]')) { stopReview(slug); return; }
    if (ev.target.closest('[data-last-stop]')) { lastStopOpen(slug); return; }
    const retry = ev.target.closest('[data-limit-retry]');
    if (retry && !retry.disabled) {
      retry.disabled = true;
      try { await limitRetry(slug); botAlertDraw(); if (typeof pausedRender === 'function') pausedRender(); }
      catch (error) { toast(error.message, true); retry.disabled = false; }
      return;
    }
    const button = ev.target.closest('[data-quarantine-resume]');
    if (!button || button.disabled) return;
    const result = $('#bot-quarantine-result');
    button.disabled = true;
    if (result) result.textContent = 'Resuming…';
    try {
      const tab = BOT.tab;
      await quarantineResume(slug);
      await refresh(true);
      BOT = null;
      await pageBot(slug, tab || 'more');
    } catch (error) {
      if (result) result.textContent = error.message; else toast(error.message, true);
      button.disabled = false;
    }
  };
  for (const host of [$('#bot-alert'), $('#bot-quarantine-host')]) if (host) host.onclick = resumeClick;
  // An external agent's credential is issued here, on the bot it runs (the Settings bots
  // table that held these buttons is gone).
  $('#pane-more')?.querySelectorAll('[data-agent-credential],[data-agent-revoke],[data-agent-pair]').forEach(button => {
    button.onclick = () => button.dataset.agentPair ? void settingsAgentPair(button.dataset.agentPair)
      : button.dataset.agentCredential ? void settingsAgentCredential(button.dataset.agentCredential)
      : void settingsAgentRevoke(button.dataset.agentRevoke);
  });
  if (!limited) {
    $('#bot-goals-add').onclick = () => botGoalAdd(slug);
    void botUpdatesCardLoad(slug);
    void botLatestUpdate(slug);
    void botGoalsCardLoad(slug);
  }

  // The right rail: Tasks with its tabs, Updates and Files, in the bot's own order (ui/app/bot-sidebar.js).
  if (BOT_EDIT && BOT_EDIT.slug !== slug) BOT_EDIT = null;
  botTaskTabShow(slug, botTaskTabFirst(slug, needsMeCount(slug)));
  botSidebarApply(slug);
  if (keeper) { botSidebarBind(slug); void botSidebarLoad(slug); void botPinsLoad(slug); }
  // A click opens a folded section without focusing it, so its ring shows only for the keyboard.
  $('#pane-tasks').querySelectorAll('.rail-fold>summary').forEach(sum => sum.addEventListener('mousedown', ev => ev.preventDefault()));
  $('#pane-tasks').addEventListener('click', ev => {
    const tabButton = ev.target.closest('[data-bot-task-tab]');
    if (tabButton) { BOT_TASK_TAB.set(slug, tabButton.dataset.botTaskTab); botTaskTabShow(slug, tabButton.dataset.botTaskTab, true); }
  });
  $('#pane-tasks .bot-task-tabs').addEventListener('keydown', ev => botTaskTabKey(slug, ev));
  $('#pane-tasks .bot-task-tabs').addEventListener('wheel', botTaskTabWheel, {passive: false});

  moreClampWatch();
  showBotTab(tab);
  pausedRender();
}
// A desktop is wide enough for both: chat on the left, the bot's tasks on the right, each scrolling
// on its own (#514). A phone keeps one at a time behind the Chat | Tasks switch.
const BOT_WIDE = window.matchMedia('(min-width: 1100px)');
// On a phone the top line is back, the bot and its tabs, nothing else. More is the rest, condensed: the Goals
// card first, then an alert, Learnings and branches (moved here from beside the name,
// and back on a wider screen), then the cards in groups.
const BOT_PHONE = window.matchMedia('(max-width: 760px)');
function placeBotHead() {
  const host = $('#bot-more-top'), top = $('#bot-top');
  if (!host || !top) return;
  const [alert, learn, screen, ...rest] = ['#bot-alert', '#bot-learn', '#bot-screen', '#bot-branches'].map(q => $(q));
  if (screen) screen.hidden = BOT_PHONE.matches; // The Info summary already has the screen link on a phone.
  if (BOT_PHONE.matches) { host.append(...[alert, learn, screen, ...rest].filter(Boolean)); return; }
  for (const el of [learn, screen]) if (el?.parentElement === host) $('#bot-tool-strip').before(el);
  for (const el of [...rest, alert]) if (el?.parentElement === host) top.querySelector('.botid').append(el);
}
// The lists in More cut to their three newest rows on every width: [host, rows, keep the newest last]. Each
// fills in its own time, so one observer cuts whatever has arrived; "Show more" undoes it for that list until
// the page is redrawn, and "Show less" cuts it again.
const MORE_LISTS = [['#bot-tools', '.bt-item'], ['#bot-access-card .scroll', 'tr:not(:first-child)'],
  ['#v2-history', '.sh-row'], ['#bot-recent-runs', 'tr:not(:first-child)']];
const MORE_KEEP = 3;
function moreClamp() {
  const pane = $('#bot-details'); if (!pane) return;
  for (const [q, rows, newest] of MORE_LISTS) {
    const host = pane.querySelector(q); if (!host) continue;
    const items = [...host.querySelectorAll(rows)];
    const long = items.length > MORE_KEEP + 1;   // never hide just one
    const cut = long && !host.dataset.all;
    items.forEach((el, i) => el.classList.toggle('more-cut', cut && (newest ? i < items.length - MORE_KEEP : i >= MORE_KEEP)));
    let btn = host.nextElementSibling?.matches('.more-all') ? host.nextElementSibling : null;
    if (!long) { btn?.remove(); continue; }
    const label = cut ? 'Show more' : 'Show less';
    if (!btn) {
      btn = Object.assign(document.createElement('button'), {type: 'button', className: 'more-all'});
      btn.onclick = () => { if (host.dataset.all) delete host.dataset.all; else host.dataset.all = '1'; moreClamp(); };
      host.after(btn);
    }
    if (btn.textContent !== label) btn.textContent = label;
    btn.setAttribute('aria-expanded', String(!cut));
  }
}
function moreClampWatch() {
  const pane = $('#bot-details'); if (!pane) return;
  new MutationObserver(moreClamp).observe(pane, {childList: true, subtree: true});
  moreClamp();
}
BOT_PHONE.addEventListener('change', placeBotHead);
function showBotTab(tab) {
  if (!BOT || !$('#btabs')) return;
  const wantTools = tab === 'tools';
  const aliases = {more: 'info', docs: 'instructions', session: 'history', tools: 'info', chat: 'dm'};
  const tabs = botTabs(BOT.slug);
  const view = tabs.includes(aliases[tab] || tab) ? aliases[tab] || tab : tabs[0];
  BOT.tab = view;
  const work = BOT_WORK_TABS.includes(view);
  const talk = tabs.filter(t => t === 'home' || t === 'dm');
  const split = work && BOT_WIDE.matches && talk.length > 0;
  const chat = split || view === 'home' || view === 'dm';
  BOT.split = split;
  $('#main').classList.toggle('chat-layout', chat);
  $('#main').classList.toggle('bot-chat-layout', chat);
  $('#main').classList.toggle('bot-split-layout', split);
  $('#btabs').querySelectorAll('[data-bt]').forEach(b => { const on = b.dataset.bt === (work ? view : 'info'); b.classList.toggle('cur', on); b.setAttribute('aria-selected', String(on)); });
  const more = $('#bot-more-btn');
  more.classList.toggle('cur', !work);
  if (!work) more.setAttribute('aria-current', 'page'); else more.removeAttribute('aria-current');
  $('#bot-work').hidden = !work;
  $('#bot-back').hidden = work;
  $('#pane-chat').hidden = !chat;
  $('#pane-tasks').hidden = !(split || view === 'tasks');
  $('#pane-tasks').setAttribute('role', split ? 'complementary' : 'region');
  $('#bot-details').hidden = work;
  $('#pane-more').hidden = view !== 'info';
  $('#pane-docs').hidden = view !== 'instructions';
  for (const t of ['history', 'rules', 'settings']) if ($('#pane-' + t)) $('#pane-' + t).hidden = view !== t;
  $('#bot-detail-tabs').querySelectorAll('[data-bot-detail-tab]').forEach(button => {
    const on = button.dataset.botDetailTab === view;
    button.classList.toggle('cur', on); button.setAttribute('aria-selected', String(on));
    button.tabIndex = on ? 0 : -1;
  });
  if (view === 'history') void botHistoryLoad(BOT.slug);
  if (view === 'rules') void botRulesLoad(BOT.slug);   // read each time: rules change on the Rules page
  placeBotHead();
  if (!work) { botScreenRefresh(BOT.slug); botInfoRefresh(BOT.slug); }
  // Home or DM on the left: the one asked for, else (Tasks beside it) the last one shown, else the first.
  if (chat) botThreadShow(view === 'home' || view === 'dm' ? view : talk.includes(BOT.thread?.kind) ? BOT.thread.kind : talk[0]);
  else botThreadStop();
  $('#pane-chat').querySelectorAll('[data-talk]').forEach(b => { const on = b.dataset.talk === BOT.thread?.kind; b.classList.toggle('cur', on); b.setAttribute('aria-selected', String(on)); });
  const once = (key, fn) => { if (!BOT.loaded.has(key)) { BOT.loaded.add(key); fn(BOT.slug); } };
  if (split || view === 'tasks') {
    once('tasks', isKeeper(BOT.slug) ? loadBotTasksV2 : loadBotIssues);
    if (isKeeper(BOT.slug)) once('files', slug => window.botFiles?.mount($('#bot-files'), {slug, get, esc, openFile: openFileLink}));
    botLatestSeen();
  }
  if (view === 'info') once('toolslist', slug => window.botTools?.mountList($('#bot-tools'), {slug, get, esc}));
  if (view === 'info' && BOT.slug === 'goal-manager' && strategyBotLogShown())
    once('strategy-log', () => renderStrategyBotLog($('#strategy-bot-log')));
  if (!BOT.limited) {
    if (view === 'history') {
      if (isKeeper(BOT.slug)) once('history', v2HistoryLoad);
      once('runs', fillBotRuns);
      once('settings-history', botSettingsHistoryLoad);
    }
    if (view === 'instructions' || view === 'info') once('docs', loadDocs);
    if (view === 'instructions') once('instructions-editor', botInstructionsMount);
    if (view === 'settings') once('settings', botSettingsMount);
  }
  $('#main').scrollTop = 0;
  if (wantTools) $('#bot-tools-card')?.scrollIntoView({block: 'start'});

}
BOT_WIDE.addEventListener('change', () => { if (BOT && $('#btabs')) showBotTab(BOT.tab); });
// The thread on the bot page: its Home, or the viewer's DM with it (made on the first message). It stops following
// when another tab, page or bot takes its place.
function botThreadShow(kind) {
  if (!BOT || !kind) return;
  const host = $('#bot-thread');
  if (BOT.thread?.kind === kind && BOT.thread.view.host === host && !BOT.thread.view.gone) return;
  botThreadStop();
  const actor = 'bot:' + BOT.slug;
  // The thread's actions (call, Members, menu) sit in the Home/DM bar instead of a title line.
  const acts = {header: 'compact', actsHost: $('#pane-chat .bot-talk-acts')};
  const view = threadMount(host, kind === 'home' ? {home: actor, ...acts} : {dm: actor, ...acts});
  BOT.thread = {kind, view};
}
function botThreadStop() {
  if (!BOT?.thread) return;
  threadUnmount(BOT.thread.view);
  BOT.thread = null;
}

// ----------------------------------------------------------------- slash commands
// A message starting with "/" is a command to the hub, never a task for a bot: a typed /clear
// once became an Issue that the bot then went and ran.
const SLASH_HELP = [
  ['/clear', 'start a fresh session for this bot on its next run'],
  ['/help', 'show this list'],
];
const isCommand = text => /^\s*\//.test(String(text || ''));
const commandWord = text => (String(text || '').trim().match(/^\/([a-z0-9-]*)/i) || [, ''])[1].toLowerCase();
const helpHTML = () => `<div class="cmd-out"><div class="lbl">Commands</div>${SLASH_HELP.map(([c, d]) =>
  `<div><code>${esc(c)}</code> <span class="muted">${esc(d)}</span></div>`).join('')}
  <div class="muted" style="margin-top:6px">Anything starting with <code>/</code> stays here; it is never sent to a bot.</div></div>`;
const unknownHTML = word => `<div class="cmd-out"><span class="err">Unknown command${word ? ` <code>/${esc(word)}</code>` : ''}.</span>
  <span class="muted">Messages starting with <code>/</code> are not sent. Commands: ${SLASH_HELP.map(([c]) => `<code>${esc(c)}</code>`).join(', ')}.</span></div>`;
// True when the text was a command and has been handled here, so the caller must not send it.
function runCommand(text, out, slug) {
  if (!isCommand(text)) return false;
  const word = commandWord(text);
  const say = html => { if (out) { out.innerHTML = html; out.hidden = false; } };
  if (word === 'help') { say(helpHTML()); return true; }
  if (word === 'clear' && !slug) { say(`<div class="cmd-out"><span class="muted"><code>/clear</code> works on a bot's page, in its conversation box.</span></div>`); return true; }
  if (word === 'clear') {
    say(`<div class="cmd-out"><div>Start a fresh session for <strong>${empName(slug)}</strong> on its next run? Its history stays visible.</div>
      <div class="row" style="margin-top:7px"><button class="primary" type="button" data-cmd="yes">Clear session</button>
      <button class="ghost" type="button" data-cmd="no">Cancel</button><span class="muted" id="cmd-msg"></span></div></div>`);
    out.querySelector('[data-cmd="no"]').onclick = () => { out.hidden = true; out.innerHTML = ''; };
    out.querySelector('[data-cmd="yes"]').onclick = async ev => {
      const msg = out.querySelector('#cmd-msg'); ev.target.disabled = true; msg.textContent = 'Clearing…';
      try {
        await post(`/employees/${slug}/session/clear`);
        out.hidden = true; out.innerHTML = '';
        toast('Session cleared: the next run starts fresh');
        if (CONV?.slug === slug) { CONV.cleared = new Date().toISOString(); convRender(CONV); }
      } catch (e) {
        if (/\b403\b|only the owner|not shared|owner only/i.test(e.message)) { out.hidden = true; out.innerHTML = ''; toast('only the owner can do that', true); return; }
        msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; ev.target.disabled = false;
      }
    };
    return true;
  }
  say(unknownHTML(word));
  return true;
}
