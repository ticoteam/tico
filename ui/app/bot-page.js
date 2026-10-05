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
// Header (name, state, goal) always visible; everything else behind four tabs that live in the URL
// as #/bot/<slug>/<tab>, so refresh and Back keep the tab. Panes stay in the DOM once loaded; only
// the conversation is torn down when you leave Chat, because it polls.
// The session view is a card inside More; #/bot/<slug>/session still opens it
// there and scrolls to it, so every link a run row wrote keeps working.
const BOT_TABS = ['chat', 'tasks', 'history', 'docs', 'more'];   // history: its updates
// The assistant is chatted with only on each person's own Assistant page (#/assistant), so its bot page has no Chat.
const botTabs = slug => {
  const tabs = slug === assistantBot() ? BOT_TABS.filter(t => t !== 'chat') : BOT_TABS;
  const mine = S.emps.find(e => e.name === slug)?.my_access;
  // Seen but not read: no updates or docs, and Chat and Tasks only for someone who may write to it (the
  // page then holds their own threads and tasks with the bot, and the request box).
  return mine && !mine.read ? tabs.filter(t => t === 'more' || (mine.write && (t === 'chat' || t === 'tasks'))) : tabs;
};
const botLimited = e => !!e?.my_access && !e.my_access.read;
const botPersonName = id => (S.people || []).find(p => p.id === id)?.name || settingsPersonName(id);
let BOT = null;
function botStopped() {
  BOT_CHAT_TASK_LOAD++; BOT_CHAT_TASKS = new Map(); BOT = null;
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
    + (live.length ? '' : '<div class="empty" id="bot-goals-none">No goal yet.</div>');
  for (const el of host.querySelectorAll('[data-goal-ed]')) {
    const g = live.find(x => x.id === el.dataset.goalEd);
    bindGoalEditor(el, g, () => botGoalsCardLoad(slug));
  }
  for (const a of host.querySelectorAll('[data-goal-go]'))
    a.onclick = ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); openGoal(a.dataset.goalGo); };
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
// The Recurring section of the bot's right rail: each routine on one line, its name and how often, and when
// it runs next. No routines, no section. Editing stays in the Routines card under More.
function botRecurringHTML(e, slug) {
  const live = S.status?.schedules || [];
  const rows = (e.schedules || live.filter(r => (r.employee || r.bot) === slug)).map(s => {
    const st = live.find(x => (s.id ? x.id === s.id : (x.employee || x.bot) === slug && x.title === s.title)) || {};
    return {...s, ...st, employee: slug, active: st.active != null ? st.active : s.active};
  }).sort((a, b) => (a.active ? 0 : 1) - (b.active ? 0 : 1) || String(a.next || '~').localeCompare(String(b.next || '~')) || String(a.title).localeCompare(String(b.title)));
  if (!rows.length) return '';
  const words = r => { const w = cadenceZoned(r) || ''; return w.charAt(0).toUpperCase() + w.slice(1); };
  const paused = r => r.enabled === false || r.enabled === 0;
  const next = r => paused(r) ? '<span class="muted" title="kept, not running">paused</span>'
    : !r.active ? '<span class="muted">not armed</span>'
    : r.on ? '<span class="muted">on event</span>'
    : r.next ? `<span class="tnum" title="${esc(fmt(r.next))}">${esc(until(r.next))}</span>` : '';
  return `<h2 class="rail-h">Recurring</h2>
    <ul class="bot-recurring-list">${rows.map(r => `<li class="${paused(r) ? 'paused' : ''}" data-routine="${esc(r.id || r.title)}" title="${esc(r.title + ' · ' + words(r))}">
        <span class="ttl">${esc(r.title)}</span><span class="when">${esc(words(r))}</span>
        <span class="bot-recurring-next">${next(r)}</span></li>`).join('')}</ul>`;
}
function botRecurringPaint(e, slug) {
  const host = $('#bot-recurring'); if (!host || !e) return;
  host.innerHTML = botRecurringHTML(e, slug);
  host.hidden = !host.innerHTML;
}
// Beside the bot's name only when something is wrong (#514): crashed, quarantined, rate limited,
// blocked, a failed last run, or an agent that is offline while work waits for it. Idle says nothing.
function botAlertHTML(slug) {
  const e = S.emps.find(x => x.name === slug) || {}, s = v2StatusOf(slug);
  let word = '', why = '';
  if (s && ['crashed', 'quarantined', 'limited', 'blocked'].includes(s.state)) { word = V2_WORD[s.state]; why = s.focus || ''; }
  else if (s?.state === 'paused' && /^Paused: over /.test(s.focus || '')) { word = s.focus; why = 'No new work until it resets or the limit is raised. A run in progress finishes.'; }
  else if (!s && stateOf(slug) === 'failed') word = 'Last run failed';
  else if (e.agent?.credential && !e.online && ((S.status?.queued || []).some(q => (q.bot || q.employee) === slug) || s?.open_tasks))
    word = 'Offline with work waiting';
  return word ? `<span class="bot-alert" role="status"${why ? ` title="${esc(why)}"` : ''}><span aria-hidden="true">⚠</span> ${esc(word)}</span>` : '';
}
// On a phone the top line says what this bot is doing. Something blocked on the
// human stays put ("! Needs you: …", tap to open); otherwise the bot's active tasks cycle one at a
// time, ticker-style. Nothing active, nothing shown.
let BOT_TICKER = {slug: '', needs: [], active: [], i: 0, timer: 0};
async function botTickerLoad(slug) {
  const [owned, asked] = await Promise.all([
    v2Get(`/v2/tasks?owner=${encodeURIComponent(slug)}&status=all`),
    v2Get(`/v2/tasks?requester=${encodeURIComponent(slug)}&status=all`)]);
  if (BOT?.slug !== slug) return;
  const live = t => ['open', 'doing', 'waiting', 'declined'].includes(String(t.status));
  const mine = (owned?.tasks || []).filter(live), theirs = (asked?.tasks || []).filter(live);
  const needs = [...theirs, ...mine].filter(t => taskNeedsMe(t) || (t.owner === myActor() && t.status !== 'done'));
  const run = v2StatusOf(slug), runningId = run?.state === 'running' ? run.task_id : '';
  const active = mine.filter(t => ['doing', 'open'].includes(String(t.status)) && !needs.includes(t))
    .sort((a, b) => (b.id === runningId) - (a.id === runningId) || String(b.updated || '').localeCompare(String(a.updated || '')));
  const same = BOT_TICKER.slug === slug;
  BOT_TICKER = {...BOT_TICKER, slug, needs, active, i: same ? BOT_TICKER.i % Math.max(1, active.length) : 0, runningId};
  botTickerPaint();
  clearInterval(BOT_TICKER.timer);
  if (!needs.length && active.length > 1) BOT_TICKER.timer = setInterval(() => {
    if (BOT?.slug !== BOT_TICKER.slug || !$('#bot-ticker')) return clearInterval(BOT_TICKER.timer);
    BOT_TICKER.i = (BOT_TICKER.i + 1) % BOT_TICKER.active.length;
    botTickerPaint(true);
  }, 3500);
}
function botTickerPaint(slide) {
  const el = $('#bot-ticker'); if (!el) return;
  const {needs, active, i, runningId} = BOT_TICKER;
  if (needs.length) {
    const t = needs[0];
    el.innerHTML = `<button type="button" class="bot-ticker-needs" data-task-conversation="${esc(t.id)}"><span class="st-ic st-needs" aria-hidden="true">!</span><span>${needs.length > 1 ? `${needs.length} need you · ` : 'Needs you: '}${esc(t.title || '')}</span></button>`;
  } else if (active.length) {
    const t = active[i % active.length];
    el.innerHTML = `<span class="bot-ticker-line${slide ? ' slide' : ''}"><span class="st-ic ${t.id === runningId ? 'st-doing' : 'st-doing-idle'}" aria-hidden="true"></span><span>${esc(t.title || '')}</span>${active.length > 1 ? `<span class="bot-ticker-n">${(i % active.length) + 1}/${active.length}</span>` : ''}</span>`;
  } else el.innerHTML = '';
  el.hidden = !el.innerHTML;
}
// Files dragged over any part of a bot's chat attach to its composer, which lights up to say so
// (#524, after bot-desk). A file dropped anywhere else on the page is swallowed: the browser's own
// answer is to navigate away to it, and nothing here wants that. A file input keeps its own drop.
const draggingFiles = ev => [...(ev.dataTransfer?.types || [])].includes('Files');
['dragover', 'drop'].forEach(t => window.addEventListener(t, ev => {
  if (draggingFiles(ev) && !ev.target.closest?.('input[type=file]')) ev.preventDefault();
}));
function chatDropZone(zone, P) {
  const pill = pq(P, '.p-pill');
  let depth = 0;
  const on = v => pill?.classList.toggle('over', v);
  zone.addEventListener('dragenter', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); depth++; on(true); });
  zone.addEventListener('dragover', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) on(false); });
  zone.addEventListener('drop', ev => {
    if (!draggingFiles(ev)) return;
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
  const work = botTabs(slug).filter(t => t === 'chat' || t === 'tasks');
  const base = `#/bot/${encodeURIComponent(slug)}`;
  const typing = BOT_PILL?.slug === slug && document.activeElement === pq(BOT_PILL, '.p-text');   // the redraw below must not take the cursor
  const tabsShown = [...work, ...(isKeeper(slug) && !limited ? ['history'] : []), 'more'];
  // The goal leads the page; chat and tasks sit side by side under it on a
  // desktop. Narrower, one top line holds back, the bot and Chat · Tasks · More, with the goal as one
  // line under it. A status shows only when it is an alert. Docs live inside More.
  $('#main').innerHTML = `
  <div class="bot-top" id="bot-top">
    <button class="bot-back-arrow" id="bot-back-arrow" type="button" aria-label="Back" title="Back">‹</button>
    <div class="bot-ident"><span data-tip-bot="${esc(slug)}" tabindex="0" role="img" aria-label="${esc(e.display_name || slug)} status">${avatar(slug, 36, stateOf(slug))}</span>
      <div class="botid"><div class="bot-nameline"><h1${role ? ` title="${esc(role)}"` : ''}>${shownName(e)}${runtimeTag(e)}</h1><span id="bot-tool-strip" hidden></span></div>
        <div class="meta" id="bot-branches"></div>
        <div class="meta" id="bot-assignment-branches"></div>
        <div class="meta" id="bot-alert">${limited ? '' : botAlertHTML(slug)}</div>
        <div class="bot-ticker" id="bot-ticker" aria-live="polite" hidden></div></div></div>
    <div class="bot-switch" id="btabs" role="tablist" aria-label="${esc(e.display_name || slug)}">
      ${tabsShown.map(t => { const label = t[0].toUpperCase() + t.slice(1);
        return `<button type="button" data-bt="${t}" role="tab" aria-label="${label}" title="${label}"><span class="nav-icon bt-icon" aria-hidden="true">${BOT_TAB_ICONS[t] || 'more_horiz'}</span><span class="bt-label">${label}</span></button>`; }).join('')}
    </div>
    ${limited ? '' : '<section class="bot-goal" id="bot-goal" aria-label="Goal"><span class="muted">Loading the goal…</span></section>'}
    <a class="ghost bot-more-btn" id="bot-more-btn" href="${base}/more" aria-label="More" title="More"><span class="nav-icon" aria-hidden="true">more_vert</span></a>
  </div>
  <div id="bot-onboard-host">${frBotBannerHTML(e)}</div>
  <a class="bot-back" id="bot-back" href="${base}" hidden>‹ ${work.includes('chat') ? 'Chat and tasks' : 'Tasks'}</a>

  <div class="bot-work" id="bot-work">
  <div id="pane-chat" hidden>
    <section class="chat-goal" id="chat-goal" aria-label="Goal" hidden></section>
    <section class="bot-chat-tasks" id="bot-chat-tasks" aria-label="What needs you" hidden></section>
    <section class="card conv" id="conv"><div class="conv-top"><span class="sub" id="conv-state">Loading…</span><span class="spacer" style="flex:1"></span><span class="sub" id="conv-access"></span></div>
      <div class="conv-older" id="conv-older"></div>
      <div id="conv-thread"><div class="empty">Loading the thread…</div></div>
      <div id="conv-bots" class="conv-bots"></div>
      <button type="button" class="conv-jump" id="conv-jump" hidden></button>
    </section>
    <div id="conv-paused" class="conv-paused" role="status" hidden></div>
    ${limited ? '<h2 class="bot-request-head">Send a request</h2>' : ''}
    <div id="chat-composer"></div>
  </div>

  <aside id="pane-tasks" class="bot-rail" aria-label="Tasks" hidden>
    <section class="rail-sec bot-active" aria-labelledby="bot-active-h"><h2 class="rail-h" id="bot-active-h">Active</h2>
      <div id="t-open" class="tpane"><div class="rail-empty">Loading…</div></div>
    </section>
    ${limited ? '' : `<section class="rail-sec bot-latest" id="bot-latest" aria-label="Updates" hidden></section>
    <section class="rail-sec bot-files" id="bot-files" aria-label="Files" hidden></section>
    <section class="rail-sec bot-recurring" id="bot-recurring" aria-label="Recurring" hidden></section>`}
    <details class="rail-sec rail-fold" id="bot-assigned" hidden><summary class="rail-h">Assigned to others <span class="cnt" id="cnt-assigned"></span></summary>
      <div id="t-assigned" class="tpane"></div></details>
    <details class="rail-sec rail-fold bot-done"><summary class="rail-h">Done <span class="cnt" id="cnt-done"></span></summary><div id="t-done" class="tpane"><div class="rail-empty">Loading…</div></div></details>
  </aside>
  ${railEdgeHTML('right')}
  </div>

  ${limited ? '' : `<div id="pane-history" hidden>
    <section class="card"><header><h2>Updates</h2></header>
      <div id="bot-history" class="bot-history"><div class="empty">Loading…</div></div></section>
  </div>
  `}
  <div id="pane-more" hidden>
    ${limited ? `    <section class="card"><header><h2>About</h2></header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd style="white-space:pre-wrap">${esc(role)}</dd>` : ''}
        <dt>Owner</dt><dd>${esc(botPersonName(e.operator))}</dd>
        <dt>Works for</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${boss.name}">${esc(boss.display_name)}</a></dd>`
          : String(e.reports_to || '').startsWith('human:') ? `<dt>Reports to</dt><dd>${esc(botPersonName(e.reports_to.slice(6)))}</dd>` : ''}
        <dt>Your access</dt><dd>${e.my_access.write ? 'See it and send requests' : 'See it only'}</dd>
      </dl></section>
    <section class="card" id="bot-tools-card"><header><h2>Tools</h2></header><div id="bot-tools"><div class="empty">Loading…</div></div></section>
` : `    <section class="card"><header><h2>Setup</h2>${settingsCanManageBot(e) ? '<button class="ghost" type="button" id="bot-edit-settings">Bot settings</button>' : ''}</header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd style="white-space:pre-wrap">${esc(role)}</dd>` : ''}
        <dt>Humans</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${e.agent ? `<dt>Run by</dt><dd>${esc(agentKind(e.agent))}${e.agent.profile ? ` · profile <code>${esc(e.agent.profile)}</code>` : ''}${e.agent.version ? ` · ${esc(e.agent.version)}` : ''}${e.agent.platform ? ` · ${esc(e.agent.platform)}` : ''}<span class="muted"> · ${!e.agent.credential ? 'no credential yet' : e.online ? 'reporting in' : 'not reporting'}${e.agent.last_seen ? `, last seen ${esc(ago(e.agent.last_seen))}` : ''}</span>${settingsCanManageBot(e) ? `<span class="settings-agent-actions">${agentPairButton(e)}<button class="ghost" type="button" data-agent-credential="${esc(e.name)}">${e.agent.credential ? 'Rotate credential' : 'Create credential'}</button>${e.agent.credential ? `<button class="ghost" type="button" data-agent-revoke="${esc(e.name)}">Revoke</button>` : ''}</span>` : ''}</dd>
        <dt>Model</dt><dd>${e.agent.model ? `${esc(e.agent.model)}${e.agent.provider ? `<span class="muted"> · ${esc(e.agent.provider)}</span>` : ''}` : "<span class=\"muted\">the profile's own</span>"}</dd>`
        : `<dt>Model</dt><dd>${esc(e.model ? settingsChoiceLabel(e.harness || e.runtime, e.model, e.reasoning_effort || e.effort) : settingsDefaultLabel(e))}${e.fallback ? `<span class="muted"> · fallback ${esc(settingsChoiceLabel(e.fallback.harness, e.fallback.model, e.fallback.reasoning_effort || e.fallback.effort))}</span>` : ''}</dd>`}
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${boss.name}">${esc(boss.display_name)}</a></dd>` : ''}
        ${botRepoHTML(e) ? `<dt>Repository</dt><dd>${botRepoHTML(e).replace(/^Repository /, '')}</dd>` : ''}
      </dl></section>
    <section class="card" id="bot-tools-card"><header><h2>Tools</h2></header><div id="bot-tools"><div class="empty">Loading…</div></div></section>
    ${keeper && v2StatusOf(slug)?.bot_state === 'quarantined' && settingsCanManageBot(e) ? `
    <section class="card" id="bot-quarantine"><header><h2>Paused</h2>
        <button class="primary" type="button" id="bot-quarantine-resume">Resume</button></header>
      <p>${esc(v2StatusOf(slug)?.focus || 'Paused after refused actions.')}${/refused writes today$/.test(v2StatusOf(slug)?.focus || '') ? ' It resumes by itself within an hour.' : ''}</p>
      <p class="muted" id="bot-quarantine-result"></p>
    </section>` : ''}
    <section class="card" id="bot-updates-card"><header><h2>Updates</h2></header>
      <div id="bot-updates"><div class="empty">Loading…</div></div></section>
    <section class="card" id="bot-goals-card"><header><h2>Goals</h2>
        <button class="linkish" type="button" id="bot-goals-add">Add goal</button></header>
      <div id="bot-goals-list"><div class="empty">Loading…</div></div></section>
    ${keeper ? `<section class="card"><header><h2>Status history</h2></header>
      <div id="v2-history"><div class="empty">Loading…</div></div></section>` : ''}
    <section class="card"><header><h2>Access</h2></header>
      ${accessTable(e)}</section>

    <section class="card" id="bot-routines"><header><h2>Routines</h2></header>
      <div id="bot-routines-list">${botRoutinesHTML(e, slug)}</div>
    </section>

    <section class="card"><header><h2>Runs</h2></header>
      ${(rs => rs.length ? `<div id="bot-recent-runs">${runsTable(rs, false)}</div>`
        : '<div id="bot-recent-runs" data-pending="1"><div class="muted">Loading…</div></div>'
      )((S.status?.recent_runs || []).filter(r => r.employee === slug).slice(0, 10))}</section>

    <section class="card" id="sess-card"><header><h2>Session</h2></header>
      <div class="sess-head" id="sess-head">Loading…</div>
      <div id="sess-turns"></div></section>
`}
    <dialog class="bot-editor" id="bot-editor" aria-labelledby="bot-editor-title"></dialog>
    <dialog class="owner-picker" id="owner-picker" aria-labelledby="owner-picker-title"></dialog>

  </div>

  ${limited ? '<div id="pane-docs" hidden>' : `<div id="pane-docs" hidden>
    <section class="card"><header><h2>Instructions</h2>${settingsCanManageBot(e) && !e.shared_from ? `<button class="ghost" type="button" data-edit-instructions="${esc(slug)}">Edit Instructions</button>` : ''}</header><div class="md" id="agent">Loading…</div></section>
    <section class="card"><header><h2>Working notes</h2></header>
      <div class="tabs" id="tabs"></div><div class="md" id="doc">Loading…</div></section>
  `}
  </div>`;

  BOT = {slug, tab: null, loaded: new Set(), limited};
  frBotWire(slug);
  orgHistoryVisit('b:' + slug);
  bindChatSwipe($('#conv-thread'));
  if (e.can_chat) {
    $('#chat-composer').append(botPill(slug).el);
    chatDropZone($('#pane-chat'), BOT_PILL);
    // A restored draft sizes the box once it is on the page.
    const restored = pq(BOT_PILL, '.p-text');
    if (restored?.value && restored.scrollHeight > restored.clientHeight) restored.style.height = restored.scrollHeight + 'px';
  } else {
    if (BOT_PILL) chatDraftSave(BOT_PILL);
    if (BOT_PILL?.slug === slug) { PILLS.delete(BOT_PILL); BOT_PILL = null; }
    $('#chat-composer').innerHTML = `<section class="card"><h2>Chat is not open to you</h2><p class="muted">You can see ${esc(e.display_name || slug)} but not send it requests. Ask its owner for Write access.</p></section>`;
  }
  window.botTools?.mountStrip($('#bot-tool-strip'), {slug, get, esc, href: `${base}/tools`, skipModel: !!runtimeTag(e)});
  if (!limited) {
    void botBranchesLoad(slug);
    void assignmentBranchesLoad(slug);
  }
  if (!limited) void botGoalLoad(slug);
  if (isKeeper(slug) && !limited) void botTickerLoad(slug);
  $('#btabs').onclick = ev => {
    const b = ev.target.closest('[data-bt]'); if (!b) return;
    location.hash = b.dataset.bt === 'chat' ? `#/bot/${slug}` : `#/bot/${slug}/${b.dataset.bt}`;
  };
  // "The back arrow in a bot should always go back to home. Not previous page."
  $('#bot-back-arrow').onclick = () => { location.hash = UPDATES; };   // home
  const editSettings = $('#bot-edit-settings');
  if (editSettings) editSettings.onclick = async () => {
    if (!SETTINGS_DATA.people.length) await loadSettings();
    settingsEditBot(slug);
  };
  // One click. Stopped runs settle on their own; nothing to write or review.
  const resumeButton = $('#bot-quarantine-resume');
  if (resumeButton) resumeButton.onclick = async () => {
    const button = resumeButton;
    const result = $('#bot-quarantine-result');
    button.disabled = true;
    result.textContent = 'Resuming…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/quarantine/clear`, {});
      await v2Refresh();
      await refresh(true);
      BOT = null;
      await pageBot(slug, 'more');
      toast(`${empName(slug)} resumed`);
    } catch (error) {
      result.textContent = error.message;
      button.disabled = false;
    }
  };
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

  // The right rail: Active, Updates, Files, Recurring, then Assigned to others and Done folded away.
  const showTab = t => { if (t === 'done') $('#pane-tasks .bot-done').open = true; };
  // A click opens a folded section without focusing it, so its ring shows only for the keyboard.
  $('#pane-tasks').querySelectorAll('.rail-fold>summary').forEach(sum => sum.addEventListener('mousedown', ev => ev.preventDefault()));
  if (!limited) botRecurringPaint(e, slug);
  $('#pane-tasks').addEventListener('click', ev => { if (ev.target.dataset.tab) { ev.preventDefault(); showTab(ev.target.dataset.tab); } });

  if (!limited) bindRoutineExpand($('#pane-more'));
  showBotTab(tab);
  if (typing) pq(BOT_PILL, '.p-text')?.focus();   // once the pane is shown; a hidden box cannot take it
}
// A desktop is wide enough for both: chat on the left, the bot's tasks on the right, each scrolling
// on its own (#514). A phone keeps one at a time behind the Chat | Tasks switch.
const BOT_WIDE = window.matchMedia('(min-width: 1100px)');
// On a phone the top line is back, the bot, and three icons on the right; the
// goal moves into More so the top line is not crowded.
const BOT_PHONE = window.matchMedia('(max-width: 760px)');
const BOT_TAB_ICONS = {chat: 'chat_bubble', tasks: 'task_alt', history: 'dynamic_feed', more: 'menu'};
function placeBotGoal(view) {
  const goal = $('#bot-goal'), more = $('#pane-more'), top = $('#bot-top');
  if (!goal || !more || !top) return;
  if (BOT_PHONE.matches && view === 'more') { if (goal.parentElement !== more) more.prepend(goal); }
  else if (goal.parentElement !== top) top.insertBefore(goal, $('#bot-more-btn'));
}   // the sidebar takes ~300px; below this one column fits
function showBotTab(tab) {
  if (!BOT || !$('#btabs')) return;
  const wantSession = tab === 'session';                         // the old tab, now a card in More
  const wantTools = tab === 'tools';                             // the Tools card in More, from the icons beside the name
  const tabs = botTabs(BOT.slug);
  const t = tabs.includes(tab) ? tab : wantSession || wantTools ? 'more' : tabs[0];
  BOT.tab = t;
  const view = t === 'docs' ? 'more' : t;                        // Docs live inside More
  const work = view === 'chat' || view === 'tasks';
  const split = work && BOT_WIDE.matches && tabs.includes('chat');
  const chat = split || view === 'chat';
  BOT.split = split;
  $('#main').classList.toggle('chat-layout', chat);
  $('#main').classList.toggle('bot-chat-layout', chat);
  $('#main').classList.toggle('bot-split-layout', split);
  $('#btabs').querySelectorAll('[data-bt]').forEach(b => { const on = b.dataset.bt === view; b.classList.toggle('cur', on); b.setAttribute('aria-selected', String(on)); });
  const more = $('#bot-more-btn');
  more.classList.toggle('cur', view === 'more');
  if (!work) more.setAttribute('aria-current', 'page'); else more.removeAttribute('aria-current');
  $('#bot-work').hidden = !work;
  $('#bot-back').hidden = work;
  $('#pane-chat').hidden = !chat;
  $('#pane-tasks').hidden = !(split || view === 'tasks');
  $('#pane-tasks').setAttribute('role', split ? 'complementary' : 'region');
  $('#pane-more').hidden = $('#pane-docs').hidden = view !== 'more';
  if ($('#pane-history')) $('#pane-history').hidden = view !== 'history';
  if (view === 'history') void botHistoryLoad(BOT.slug);
  placeBotGoal(view);
  if (!chat) convStop(); // the thread and microphone stop off-screen
  else if (isKeeper(BOT.slug)) {
    if (!V2C || V2C.slug !== BOT.slug) v2ChatLoad(BOT.slug);
  }
  else if (!CONV || CONV.slug !== BOT.slug) loadConversation(BOT.slug);
  if (chat && !split) void loadBotChatTasks(BOT.slug);            // side by side, the tasks column says it
  const once = (key, fn) => { if (!BOT.loaded.has(key)) { BOT.loaded.add(key); fn(BOT.slug); } };
  if (split || view === 'tasks') {
    once('tasks', isKeeper(BOT.slug) ? loadBotTasksV2 : loadBotIssues);
    if (isKeeper(BOT.slug)) once('files', slug => window.botFiles?.mount($('#bot-files'), {slug, get, esc, openFile: openFileLink}));
    if (!BOT.limited) botRecurringPaint(S.emps.find(x => x.name === BOT.slug), BOT.slug);   // routines change under More; redraw on return
    botLatestSeen();
  }
  if (view === 'more') once('toolslist', slug => window.botTools?.mountList($('#bot-tools'), {slug, get, esc}));
  if (view === 'more' && !BOT.limited) {
    if (isKeeper(BOT.slug)) once('history', v2HistoryLoad);
    once('docs', loadDocs);            // Routines expands playbooks
    once('runs', fillBotRuns);                                    // /status carries no recent_runs
    sessionLoad(BOT.slug, SESS_PICK);                             // read afresh: the file keeps moving
  }
  SESS_PICK = null;
  $('#main').scrollTop = 0;
  if (wantSession) $('#sess-card')?.scrollIntoView({block: 'start'});
  if (wantTools) $('#bot-tools-card')?.scrollIntoView({block: 'start'});
  if (t === 'docs') $('#pane-docs')?.scrollIntoView({block: 'start'});
}
BOT_WIDE.addEventListener('change', () => { if (BOT && $('#btabs')) showBotTab(BOT.tab); });

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
