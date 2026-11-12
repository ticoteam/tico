/* ui/app/bot-page.ts — The bot page: header, tabs, goals, ticker, slash commands, recent runs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- pages
// /status from the v2 server no longer carries recent_runs, so a bot page falls back to /runs,
// cached for a minute so the 30s refresh does not refetch it every pass.
// The bot's recent runs (GET /runs, its rows as /status sends them) and when they were read.
interface BotRunsCache { at: number; list: NonNullable<ServerStatus['recent_runs']> }
let RUNS_CACHE: BotRunsCache = {at: 0, list: []};
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
const BOT_TAB_LABELS: Record<string, string> = {home: 'Home', dm: 'DM', tasks: 'Tasks', info: 'More'};
// The assistant is talked to only on each person's own Assistant page (#/assistant), and its helpers take their work
// from it: neither has a DM, and the assistant has no Home.
const botTabs = (slug: string): string[] => {
  const tabs = BOT_TABS.filter(t => !(t === 'dm' && (slug === assistantBot() || isAssistantHelper(slug))) && !(t === 'home' && slug === assistantBot()));
  const e = botRow(slug), mine = e?.my_access;
  const noDm = e && e.can_chat === false ? tabs.filter(t => t !== 'dm') : tabs;
  // Seen but not read: no Home, updates or docs, and DM and Tasks only for someone who may write to it (the
  // page then holds their own DM and tasks with the bot). Every teammate reads its Rules.
  return mine && !mine.read ? noDm.filter(t => t === 'info' || t === 'rules' || (mine.write && (t === 'dm' || t === 'tasks'))) : noDm;
};
const botLimited = (e: Employee | null | undefined) => !!e?.my_access && !e.my_access.read;
const botPersonName = (id: string | null | undefined) => (S.people || []).find(p => p.id === id)?.name || settingsPersonName(id);
// The open bot page (BotPageView, ui/types/bot-page.d.ts); null when no bot page is open.
let BOT: BotPageView | null = null;
// BOT where only an open bot page can reach (the old code read BOT.x there and threw when it was null).
const botNow = (): BotPageView => BOT!;
function botStopped() {
  BOT?.settingsCleanup?.();
  if (BOT?.thread) threadUnmount(BOT.thread.view);
  BOT = null;
}
function botStatePill(slug: string) {
  const st = stateOf(slug);
  const act = (S.status?.active || []).find(a => a.employee === slug);
  const last = (S.status?.recent_runs || []).find(r => r.employee === slug);
  if (st === 'running') return `<span class="pill in-progress">Running${act?.issue ? ` on #${esc(act.issue)}` : ''}</span>`;
  if (st === 'needs') return '<span class="pill needs">Needs you</span>';
  if (st === 'paused') return '<span class="pill">Paused</span>';
  if (st === 'planned') return '<span class="pill">Setting up</span>';
  if (st === 'failed') return `<span class="pill fail">Last run failed${last?.finished_at ? ` ${ago(last.finished_at)}` : ''}</span>`;
  return `<span class="pill">Idle${last?.finished_at ? `, last active ${ago(last.finished_at)}` : ''}</span>`;
}
// The server cuts the Role section at 600 characters, so the lead can stop mid-sentence: end at the
// last full sentence instead, and put whatever is left behind "more".
function goalParts(role: string | null | undefined) {
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
// The newest goal load (botGoalLoad, ui/app/bot-page-goals.ts): an older one that finishes late draws nothing.
let BOT_GOAL_LOAD = 0;
// Why the bot's last run ended without finishing (backend/execution.py last_stop), never a bare "Crashed": one plain
// line, grey when nothing is needed and red when a person must act, and only an action that exists (`review`: the
// Stopped runs dialog, which retries or dismisses the run). A click opens what happened.
// Why a bot's last run stopped (V2BotState.last_stop).
type BotLastStop = NonNullable<V2BotState['last_stop']>;
const lastStopOf = (slug: string) => { const s = v2StatusOf(slug); return s && ['crashed', 'idle'].includes(s.state) ? s.last_stop || null : null; };
const stopReviewable = (slug: string, stop: BotLastStop) => stop.action === 'review' && settingsCanManageBot(botRow(slug));
function lastStopHTML(slug: string) {
  const stop = lastStopOf(slug); if (!stop) return '';
  const soft = stop.tone === 'soft';
  return `<button type="button" class="bot-alert bot-stop${soft ? ' soft' : ''}" data-last-stop${soft ? '' : ' role="status"'}>${soft ? '' : '<span aria-hidden="true">⚠</span> '}${esc(stop.text)}</button>`
    + (stopReviewable(slug, stop) ? '<button class="primary bot-alert-resume" type="button" data-stop-review>Retry or dismiss</button>' : '');
}
const stopReview = (slug: string) => void executionReview(slug, async () => { await v2Refresh(); botAlertDraw(); });
// A bot's uncertain run that holds queued work waits for a review (backend/views.py uncertain_work). The bot's page offers
// it under More, with the same Stopped runs card as Retry or dismiss; the account menu never lists it.
const botReviewIssue = (slug: string) => (S.status?.health_issues || []).find(issue => issue.kind === 'uncertain_work' && issue.bot === slug && issue.can_review !== false) || null;
function botReviewHTML(slug: string) {
  const issue = botReviewIssue(slug), e = botRow(slug);
  if (!issue || !e || botLimited(e) || !settingsCanManageBot(e)) return '';
  return `<section class="card" id="bot-review"><header><h2>Stopped runs</h2><button class="ghost" type="button" data-bot-review>Review</button></header>
      <p>${esc(issue.title)}</p></section>`;
}
function lastStopOpen(slug: string) {
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
  dialog.querySelector<HTMLElement>('[data-close]')!.onclick = () => dialog.close();
  const open = dialog.querySelector<HTMLElement>('[data-stop-review]');
  if (open) open.onclick = () => { dialog.close(); stopReview(slug); };
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
}
// Beside the bot's name only when something is wrong (#514): crashed, quarantined, rate limited,
// blocked, a failed last run, or an agent that is offline while work waits for it. Idle says nothing.
// The top line while the bot can't run (docs/pipelines-v3.md S14): a red dot, the server's cause as it is, and the one
// fix a person makes. GET /v2/bots/{bot} `cant_run` (backend/org_routes.py), read with the page and again as the status
// moves, at most every 15 seconds.
type BotCantRun = NonNullable<import('../types/api').BotDetail['cant_run']>;
const BOT_CANT_RUN: {slug: string; at: number; busy: boolean; value: BotCantRun | null} = {slug: '', at: 0, busy: false, value: null};
async function botCantRunLoad(slug: string, force = false) {
  if (BOT_CANT_RUN.busy || (!force && BOT_CANT_RUN.slug === slug && Date.now() - BOT_CANT_RUN.at < 15_000)) return;
  BOT_CANT_RUN.busy = true;
  try {
    const bot = await get<import('../types/api').BotDetail>(`/v2/bots/${encodeURIComponent(slug)}`, {fresh: true});
    Object.assign(BOT_CANT_RUN, {slug, at: Date.now(), value: bot.cant_run?.cause ? bot.cant_run : null});
  } catch { Object.assign(BOT_CANT_RUN, {slug, at: Date.now(), value: null}); }
  finally { BOT_CANT_RUN.busy = false; }
  const alert = $('#bot-alert');
  if (BOT?.slug === slug && alert) patchLiveHTML(alert, botAlertHTML(slug));
}
const BOT_BILLING = '#/settings/billing';
function botCantRunFixHTML(slug: string, fix: BotCantRun['fix']): string {
  const more = `#/bot/${encodeURIComponent(slug)}/info`, cls = 'primary bot-alert-resume';
  switch (fix) {
    case 'resume': return `<button class="${cls}" type="button" data-bot-unpause>Unpause</button>`;
    case 'release_quarantine': return `<button class="${cls}" type="button" data-quarantine-resume>Resume bot</button>`;
    case 'release_hold': return botReleaseButton(slug, cls);
    case 'finish_setup': return `<a class="${cls}" href="${more}">Finish setup</a>`;
    case 'assign_computer': return `<a class="${cls}" href="${more}">Assign a computer</a>`;
    case 'resume_company': return `<a class="${cls}" href="${BOT_BILLING}">Resume company</a>`;
    case 'unlock_cloud': return `<a class="${cls}" href="${BOT_BILLING}">Unlock</a>`;
    case 'add_credit': return `<a class="${cls}" href="${BOT_BILLING}">Add credit</a>`;
    case 'open_computers': return `<a class="${cls}" href="#/settings/computers">Computers</a>`;
    default: return '';
  }
}
function botCantRunHTML(slug: string): string {
  const cant = BOT_CANT_RUN.slug === slug ? BOT_CANT_RUN.value : null;
  if (!cant?.cause) return '';
  return `<span class="bot-alert bot-cant-run" role="status"><span class="tk-mark tk-mark-cant" aria-hidden="true"></span> ${esc(cant.cause)}</span>${botCantRunFixHTML(slug, cant.fix)}`;
}
function botAlertHTML(slug: string) {
  const cantRun = botCantRunHTML(slug);
  if (cantRun) return cantRun;
  const e: Partial<Employee> = botRow(slug) || {}, s = v2StatusOf(slug);
  let word: string | undefined = '', why = '';
  if (s?.state === 'crashed' && s.last_stop) return lastStopHTML(slug);
  // Tico stopped it (failed runs, or over its run budget): the words are the server's, Release lifts it.
  if (s?.stopped) return `<span class="bot-alert" role="status" title="${esc(s.focus || '')}" data-bot-stopped="${esc(s.stopped)}"><span aria-hidden="true">⚠</span> Stopped</span>${botReleaseButton(slug, 'primary bot-alert-resume')}`;
  if (s && ['crashed', 'quarantined', 'limited'].includes(s.state)) {
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
function botQuarantineCard(slug: string) {
  const s = v2StatusOf(slug), e = botRow(slug);
  if (s?.bot_state !== 'quarantined' || !e || botLimited(e) || !settingsCanManageBot(e)) return '';
  const r: NonNullable<Quarantine['review']> = s.quarantine?.review || {};
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
function botLimitHTML(slug: string) {
  const limit = overLimit(slug); if (!limit) return '';
  const usd = (n: unknown) => '$' + Number(n || 0).toFixed(2);
  return `<section class="bot-limit" role="status"><span aria-hidden="true">⚠</span> ${esc(overLimitWord(limit))}${limit.limit != null ? ` (${usd(limit.spent)} of ${usd(limit.limit)})` : ''}. <a href="#/strategy/budget/${encodeURIComponent('bot:' + slug)}">Raise limit</a></section>`;
}
// The alert beside the name and the limit banner come and go with the status poll and live events.
// The Settings pause row re-reads HQ-backed state only when the page's view of paused is out of date, or a minute old.
const PAUSE_STALE_MS = 60_000;
function botAlertDraw() {
  if (!BOT) return;
  if (botPauseStale(BOT)) void botPauseLoad();
  void botCantRunLoad(BOT.slug);
  const alert = $('#bot-alert'), limit = $('#bot-limit-host'), held = $('#bot-quarantine-host'), paused = $('#bot-paused');
  if (alert) patchLiveHTML(alert, botAlertHTML(BOT.slug));
  if (paused) patchLiveHTML(paused, botPausedBadge(BOT.slug));
  if (held) patchLiveHTML(held, botQuarantineCard(BOT.slug));
  const review = $('#bot-review-host');
  if (review) patchLiveHTML(review, botReviewHTML(BOT.slug));
  if (limit) patchLiveHTML(limit, botLimitHTML(BOT.slug));
}
// Files dragged over any part of a bot's chat attach to its composer, which lights up to say so
// (#524, after bot-desk). A file dropped anywhere else on the page is swallowed: the browser's own
// answer is to navigate away to it, and nothing here wants that. A file input keeps its own drop.
// A type guard: true only when the event carries files, so dataTransfer is there.
const draggingFiles = (ev: DragEvent): ev is DragEvent & {dataTransfer: DataTransfer} => [...(ev.dataTransfer?.types || [])].includes('Files');
(['dragover', 'drop'] as const).forEach(t => window.addEventListener(t, ev => {
  if (draggingFiles(ev) && !eventElement(ev)?.closest('input[type=file]')) ev.preventDefault();
}));
// `pillNow` is the pill, or a function giving the one there now (the Ask panel keeps its zone and makes a new pill per load).
// The composer a drop attaches to: a pill (PillState) or the Ask panel's (AskComposer).
function chatDropZone(zone: HTMLElement, pillNow: Pill | null | (() => Pill | null | undefined)) {
  const cur = () => typeof pillNow === 'function' ? pillNow() : pillNow;
  let depth = 0;
  const on = (v: boolean) => { const P = cur(); if (P) pq(P, '.p-pill')?.classList.toggle('over', v); };
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
function bindChatSwipe(thread: HTMLElement | null) {
  if (!thread) return;
  let x0 = 0, y0 = 0, sideways: boolean | null = null;
  thread.addEventListener('touchstart', ev => {
    const touch = ev.touches[0]; if (!touch) return;
    x0 = touch.clientX; y0 = touch.clientY; sideways = null; thread.classList.remove('stamps-settle');
  }, {passive: true});
  thread.addEventListener('touchmove', ev => {
    const touch = ev.touches[0]; if (!touch) return;
    const dx = touch.clientX - x0, dy = touch.clientY - y0;
    if (sideways === null && Math.abs(dx) + Math.abs(dy) > 8) sideways = Math.abs(dx) > Math.abs(dy);
    if (sideways) thread.style.setProperty('--stamp-shift', Math.max(0, Math.min(70, -dx)) + 'px');
  }, {passive: true});
  const settle = () => { if (sideways) { thread.classList.add('stamps-settle'); thread.style.setProperty('--stamp-shift', '0px'); } sideways = null; };
  thread.addEventListener('touchend', settle);
  thread.addEventListener('touchcancel', settle);
}
async function pageBot(slug: string, tab: string | undefined) {
  const e = botRow(slug);
  if (!e) { BOT = null; $('#main').innerHTML = `<div class="empty">Unknown bot "${esc(slug)}".</div>`; return; }
  if (BOT?.slug === slug && $('#btabs')) { showBotTab(tab); return; }   // same bot, another tab
  const boss = botRow(e.reports_to);
  const role = String(e.role || e.description || '').trim();
  const limited = botLimited(e);       // seen, not read: no activity anywhere on the page
  const work = botTabs(slug).filter(t => BOT_WORK_TABS.includes(t));
  const base = `#/bot/${encodeURIComponent(slug)}`;
  const talkTabs = work.filter(t => t === 'home' || t === 'dm');
  // One chat tab at every width: with both Home and DM, the lock in the top line (Incognito) flips to the DM.
  const tabsShown = [...work.filter(t => !(t === 'dm' && talkTabs.length > 1)), 'info'];
  // The goal leads the page; chat and tasks sit side by side under it on a
  // desktop. Narrower, the top line holds the bot and Chat · Tasks · Info; configuration has its own tabs.
  // On a phone the goal and the extra controls move into Info (placeBotHead). A status shows
  // only when it is an alert. Configuration opens on Info.
  BOT?.settingsCleanup?.();
  pageTitle(botDisplayName(slug));
  $('#main').innerHTML = `
  <div class="bot-top" id="bot-top">
    <button class="bot-back-arrow" id="bot-back-arrow" type="button" aria-label="Back" title="Back">‹</button>
    <div class="bot-ident"><span data-tip-bot="${esc(slug)}" tabindex="0" role="img" aria-label="${esc(e.display_name || e.slug)} status">${avatar(slug, 36, stateOf(slug))}</span>
      <div class="botid"><div class="bot-nameline"><h1${role ? ` title="${esc(role)}"` : ''}>${shownName(e)}${runtimeTag(e)}${botBetaPill(e)}</h1><span id="bot-paused">${botPausedBadge(slug)}</span><button type="button" class="bot-learn" id="bot-learn" aria-label="Learnings" title="Learnings" hidden><span class="nav-icon" aria-hidden="true">psychology</span><span class="bl-label">Learnings</span><span class="bl-n" hidden></span></button>${limited ? '' : botScreenLinkHTML(slug)}<span id="bot-tool-strip" hidden></span></div>
        <div class="meta" id="bot-branches"></div>
        <div class="meta" id="bot-alert">${limited ? '' : botAlertHTML(slug)}</div></div></div>
    <div class="bot-switch" id="btabs" role="tablist" aria-label="${esc(e.display_name || e.slug)}">
      ${tabsShown.map(t => { const label = BOT_TAB_LABELS[t];
        return `<button type="button" data-bt="${t}" role="tab">${t === 'home' && talkTabs.length > 1 ? 'Chat' : label}</button>`; }).join('')}
    </div>
    ${limited ? '' : '<span class="bot-top-acts" id="bot-top-acts"></span>'}
    ${limited ? '' : '<section class="bot-goal" id="bot-goal" aria-label="Goal"><span class="muted">Loading the goal…</span></section>'}
    ${limited || talkTabs.length < 2 ? '' : `<button type="button" class="bot-incognito" id="bot-incognito" aria-pressed="false" aria-label="Incognito" title="Incognito: a private chat with this bot, not the team's Home"><span class="nav-icon" aria-hidden="true">lock</span><span class="bi-label">Incognito</span></button>`}
    <a class="ghost bot-more-btn" id="bot-more-btn" href="${base}/info" aria-label="More" title="More"><svg class="bot-more-i" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></a>
  </div>
  <div id="bot-banners"><div id="bot-limit-host">${limited ? '' : botLimitHTML(slug)}</div>
  <div id="bot-onboard-host">${frBotBannerHTML(e)}</div><div id="bot-dream-host"></div>
  ${limited || isBuiltInBot(slug) ? '' : `<div id="bot-github-host" data-bot="${esc(slug)}"></div>`}</div>
  <a class="bot-back" id="bot-back" href="${base}" hidden>‹ ${BOT_TAB_LABELS[work[0] ?? ''] || 'Tasks'}</a>

  <div class="bot-work" id="bot-work">
  <div id="pane-chat" hidden>
    <div id="conv-paused" class="conv-paused" role="status" hidden></div>
    <div class="bot-thread" id="bot-thread"></div>
  </div>

  <aside id="pane-tasks" class="bot-rail" aria-label="Tasks" hidden${!botSidebarLayout(slug) ? ' data-loading' : ''}>
    ${!limited ? `<button type="button" class="rail-ico bot-sidebar-edit" id="bot-sidebar-edit" aria-label="Edit sidebar" title="Edit"${botSidebarLayout(slug)?.can_edit ? '' : ' hidden'}><span class="nav-icon" aria-hidden="true">edit</span></button>
    <div class="bot-rail-editbar" hidden><span class="muted" id="bot-sidebar-status" role="status"></span><span class="spacer"></span>
      <button type="button" class="ghost" id="bot-sidebar-add">Add</button><button type="button" class="ghost" id="bot-sidebar-cancel">Cancel</button><button type="button" class="primary" id="bot-sidebar-done">Done</button></div>` : ''}
    <div class="bot-rail-loading rail-empty"${!botSidebarLayout(slug) ? '' : ' hidden'}>Loading…</div>
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
      ${BOT_DETAIL_TABS.filter(t => botTabs(slug).includes(t)).map(t => `<button type="button" id="bot-tab-${t}" data-bot-detail-tab="${t}" role="tab" aria-controls="${t === 'info' ? 'pane-more' : t === 'instructions' ? 'pane-docs' : 'pane-' + t}">${t[0]!.toUpperCase() + t.slice(1)}</button>`).join('')}
    </div>
    <div id="pane-more" role="tabpanel" aria-labelledby="bot-tab-info" hidden>
    <div class="bot-more-top" id="bot-more-top"></div>
    <div id="bot-review-host">${botReviewHTML(slug)}</div>
    ${slug === 'strategy-owner' && strategyBotLogShown() ? `<section class="card" id="strategy-bot-log-card"><header><h2>${esc(strategyBotName())} log</h2></header>
      <div id="strategy-bot-log"><div class="empty">Loading…</div></div></section>` : ''}
    ${limited ? `    <section class="card"><header><h2>About</h2></header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd>${botRoleHTML(role)}</dd>` : ''}
        <dt>Owner</dt><dd>${esc(botPersonName(e.owner_id))}</dd>
        <dt>Works for</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${encodeURIComponent(boss.id)}">${esc(boss.display_name)}</a></dd>`
          : String(e.reports_to || '').startsWith('human:') ? `<dt>Reports to</dt><dd>${esc(botPersonName(e.reports_to!.slice(6)))}</dd>` : ''}
        <dt>Your access</dt><dd>${e.my_access!.write ? 'See it and send requests' : 'See it only'}</dd>
        ${botScreenRowHTML(slug)}
      </dl></section>
    <section class="card" id="bot-tools-card"><header><h2>Tools</h2></header><div id="bot-tools"><div class="empty">Loading…</div></div></section>
` : `    <section class="card" id="bot-setup-card"><header><h2>Info</h2></header>
      <dl class="bot-setup">
        ${role ? `<dt>Role</dt><dd>${botRoleHTML(role)}</dd>` : ''}
        <dt>Humans</dt><dd>${userChips(e) || '<span class="muted">Nobody assigned</span>'}</dd>
        ${!e.agent ? `<dt>Computer</dt><dd>${cloudMachineOn(e) ? cloudMachineComputerHTML(e) : e.machine ? `${esc(e.machine.label || e.machine.runner_id)} <span class="muted">· ${e.online ? 'Online' : 'Offline'}</span>` : '<span class="muted">Not assigned</span>'}</dd>
        <dt>Harness</dt><dd>${esc(harnessWords(e.harness || e.runtime || e.resolved_runtime) || 'Team default')}</dd>` : ''}
        ${e.agent ? `<dt>Run by</dt><dd>${esc(agentKind(e.agent))}${e.agent.profile ? ` · profile <code>${esc(e.agent.profile)}</code>` : ''}${e.agent.version ? ` · ${esc(e.agent.version)}` : ''}${e.agent.platform ? ` · ${esc(e.agent.platform)}` : ''}<span class="muted"> · ${esc(agentPresenceLabel(e.agent, e.online).toLowerCase())}${e.agent.last_seen_at ? `, last ${e.agent.synced ? 'sync' : 'seen'} ${esc(ago(e.agent.last_seen_at))}` : ''}</span></dd>
        <dt>Model</dt><dd>${e.agent.model ? `${esc(e.agent.model)}${e.agent.provider ? `<span class="muted"> · ${esc(e.agent.provider)}</span>` : ''}` : "<span class=\"muted\">the profile's own</span>"}</dd>`
        : `<dt>Model</dt><dd>${esc(e.model ? settingsChoiceLabel(e.harness || e.runtime, e.model, e.reasoning_effort || e.effort) : settingsDefaultLabel(e))}${e.fallback ? `<span class="muted"> · fallback ${esc(settingsChoiceLabel(e.fallback.harness, e.fallback.model, e.fallback.reasoning_effort || e.fallback.effort))}</span>` : ''}</dd>`}
        ${boss ? `<dt>Reports to</dt><dd><a href="#/bot/${encodeURIComponent(boss.id)}">${esc(boss.display_name)}</a></dd>` : ''}
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
    <section class="card" id="bot-status-card"><header><h2>Status history</h2></header>
      <div id="v2-history"><div class="empty">Loading…</div></div></section>
    <section class="card" id="bot-runs-card"><header><h2>Runs</h2></header>
      ${(rs => rs.length ? `<div id="bot-recent-runs">${runsTable(rs, false)}</div>`
        : '<div id="bot-recent-runs" data-pending="1"><div class="muted">Loading…</div></div>'
      )((S.status?.recent_runs || []).filter(r => r.employee === slug).slice(0, 10))}</section>

    ${settingsCanManageBot(e) ? '<section class="card"><header><h2>Settings changes</h2></header><div id="bot-settings-history">Loading…</div></section>' : ''}
  </div>
  <div id="pane-settings" role="tabpanel" aria-labelledby="bot-tab-settings" hidden>
    <section class="card"><div class="bot-editor bot-editor-inline" id="bot-editor"><div class="empty">Loading settings…</div></div><div id="bot-settings-panels"></div></section>
    <div id="bot-builtin-cards">${botBuiltinCardsHTML(slug)}</div>
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
    void botDreamLoad(slug);
    if (!isBuiltInBot(slug)) void githubNudgeBot(slug);
  }
  if (!limited) void botGoalLoad(slug);
  $('#btabs').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-bt]'); if (!b) return;
    location.hash = b.dataset.bt === work[0] ? base : `${base}/${b.dataset.bt}`;
  };
  // The lock in the top line flips the chat between Home and the private DM; it says Incognito only while on.
  const incognito = $('#bot-incognito');
  if (incognito) incognito.onclick = () => {
    const next = incognito.getAttribute('aria-pressed') === 'true' ? 'home' : 'dm';
    location.hash = next === work[0] ? base : `${base}/${next}`;
  };
  // "The back arrow in a bot should always go back to home. Not previous page."
  $('#bot-back-arrow').onclick = () => { location.hash = UPDATES; };   // home
  $('#bot-detail-tabs').onkeydown = (event: KeyboardEvent & {currentTarget: HTMLElement}) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...event.currentTarget.querySelectorAll('button')];
    const i = buttons.indexOf(event.target as HTMLButtonElement); if (i < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    const button = buttons[next]!;
    button.click(); button.focus();
  };
  void botCantRunLoad(slug, true);
  $('#bot-detail-tabs').onclick = (event: MouseEvent) => {
    const button = eventElement(event)?.closest<HTMLElement>('[data-bot-detail-tab]');
    if (button) location.hash = `${base}/${button.dataset.botDetailTab}`;
  };
  // One click, from the warning beside the name or the card under More. Stopped runs settle on their own;
  // nothing to write or review.
  const resumeClick = async (ev: MouseEvent) => {
    const target = eventElement(ev);
    if (target?.closest('[data-stop-review], [data-bot-review]')) { stopReview(slug); return; }
    if (target?.closest('[data-last-stop]')) { lastStopOpen(slug); return; }
    const unpause = target?.closest<HTMLButtonElement>('[data-bot-unpause]');
    if (unpause && !unpause.disabled && BOT) {
      unpause.disabled = true;
      const view = BOT;
      if (!view.pauseState) await botPauseLoad();
      await botPauseChange(view, 'resume');
      await botCantRunLoad(slug, true);
      return;
    }
    const retry = target?.closest<HTMLButtonElement>('[data-limit-retry]');
    if (retry && !retry.disabled) {
      retry.disabled = true;
      try { await limitRetry(slug); botAlertDraw(); if (typeof pausedRender === 'function') pausedRender(); }
      catch (error) { toast(errorMessage(error, ''), true); retry.disabled = false; }
      return;
    }
    const button = target?.closest<HTMLButtonElement>('[data-quarantine-resume]');
    if (!button || button.disabled) return;
    const result = $('#bot-quarantine-result');
    button.disabled = true;
    if (result) result.textContent = 'Resuming…';
    try {
      const tab = botNow().tab;
      await quarantineResume(slug);
      await refresh(true);
      BOT = null;
      await pageBot(slug, tab || 'more');
    } catch (error) {
      if (result) result.textContent = errorMessage(error, ''); else toast(errorMessage(error, ''), true);
      button.disabled = false;
    }
  };
  for (const host of [$('#bot-alert'), $('#bot-quarantine-host'), $('#bot-review-host')]) if (host) host.onclick = resumeClick;
  // An external agent's credential is issued here, on the bot it runs (the Settings bots
  // table that held these buttons is gone).
  $('#pane-more')?.querySelectorAll('[data-agent-credential],[data-agent-revoke],[data-agent-pair]').forEach((button: HTMLElement) => {
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
  botSidebarBind(slug); void botSidebarLoad(slug); void botPinsLoad(slug);
  // A click opens a folded section without focusing it, so its ring shows only for the keyboard.
  $('#pane-tasks').querySelectorAll('.rail-fold>summary').forEach((sum: Element) => sum.addEventListener('mousedown', ev => ev.preventDefault()));
  $('#pane-tasks').addEventListener('click', (ev: MouseEvent) => {
    const tabButton = eventElement(ev)?.closest<HTMLElement>('[data-bot-task-tab]');
    if (tabButton) { BOT_TASK_TAB.set(slug, tabButton.dataset.botTaskTab); botTaskTabShow(slug, tabButton.dataset.botTaskTab, true); return; }
    // A task row opens: in Needs you, its card over the rail; else the task.
    const task = eventElement(ev)?.closest<HTMLElement>('.tpane [data-task]')?.dataset.task;
    if (!task) return;
    const need = eventElement(ev)?.closest('#t-needs') ? botNeeds(slug).find(it => botNeedTaskId(it) === task) : null;
    if (!need || !decideOpen(String(need.id))) taskModalOpen(task);
  });
  $('#pane-tasks').addEventListener('keydown', (ev: KeyboardEvent) => {
    const row = eventElement(ev)?.closest<HTMLElement>('.tpane [data-task]');
    if ((ev.key === 'Enter' || ev.key === ' ') && row && row === eventElement(ev)) { ev.preventDefault(); row.click(); }
  });
  $('#pane-tasks .bot-task-tabs').addEventListener('keydown', (ev: KeyboardEvent) => botTaskTabKey(slug, ev));
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
  if (learn) host.append(learn);   // Learnings sit under More on a wide screen too, out of the top line
  if (screen?.parentElement === host) $('#bot-tool-strip').before(screen);
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
function showBotTab(tab: string | null | undefined) {
  if (!BOT || !$('#btabs')) return;
  const wantTools = tab === 'tools';
  const aliases: Record<string, string> = {more: 'info', docs: 'instructions', session: 'history', tools: 'info', chat: 'dm'};
  const tabs = botTabs(BOT.slug);
  const want = aliases[String(tab)] || tab;
  const view = typeof want === 'string' && tabs.includes(want) ? want : tabs[0]!;
  BOT.tab = view;
  const work = BOT_WORK_TABS.includes(view);
  const talk: string[] = tabs.filter(t => t === 'home' || t === 'dm');
  const split = work && BOT_WIDE.matches && talk.length > 0;
  const chat = split || view === 'home' || view === 'dm';
  BOT.split = split;
  $('#main').classList.toggle('chat-layout', chat);
  $('#main').classList.toggle('bot-chat-layout', chat);
  $('#main').classList.toggle('bot-split-layout', split);
  // The DM has no tab of its own when Home has one: Chat stays current under Incognito.
  const curTab = !work ? 'info' : view === 'dm' && !$('#btabs [data-bt="dm"]') ? 'home' : view;
  $('#btabs').querySelectorAll('[data-bt]').forEach((b: HTMLElement) => { const on = b.dataset.bt === curTab; b.classList.toggle('cur', on); b.setAttribute('aria-selected', String(on)); });
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
  $('#bot-detail-tabs').querySelectorAll('[data-bot-detail-tab]').forEach((button: HTMLElement) => {
    const on = button.dataset.botDetailTab === view;
    button.classList.toggle('cur', on); button.setAttribute('aria-selected', String(on));
    button.tabIndex = on ? 0 : -1;
  });
  if (view === 'history') void botHistoryLoad(BOT.slug);
  if (view === 'rules') void botRulesLoad(BOT.slug);   // read each time: rules change on the Rules page
  placeBotHead();
  if (!work) { botScreenRefresh(BOT.slug); botInfoRefresh(BOT.slug); }
  // Home or DM on the left: the one asked for, else (Tasks beside it) the last one shown, else the first.
  if (chat) botThreadShow(view === 'home' || view === 'dm' ? view : BOT.thread && talk.includes(BOT.thread.kind) ? BOT.thread.kind : talk[0]);
  else botThreadStop();
  const incognitoOn = $('#bot-incognito');
  if (incognitoOn) { incognitoOn.setAttribute('aria-pressed', String(BOT.thread?.kind === 'dm')); incognitoOn.hidden = !chat; }
  const once = (key: string, fn: (slug: string) => unknown) => { const bot = botNow(); if (!bot.loaded.has(key)) { bot.loaded.add(key); fn(bot.slug); } };
  if (split || view === 'tasks') {
    once('tasks', loadBotTasksV2);
    once('files', (slug: string) => window.botFiles?.mount($('#bot-files'), {slug, get, esc, openFile: openFileLink}));
    botLatestSeen();
  }
  if (view === 'info') once('toolslist', (slug: string) => window.botTools?.mountList($('#bot-tools'), {slug, get, esc}));
  if (view === 'info' && BOT.slug === 'strategy-owner' && strategyBotLogShown())
    once('strategy-log', () => renderStrategyBotLog($('#strategy-bot-log')));
  if (!BOT.limited) {
    if (view === 'history') {
      once('history', v2HistoryLoad);
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
function botThreadShow(kind: string | undefined) {
  if (!BOT || !kind) return;
  const host = $('#bot-thread');
  if (BOT.thread?.kind === kind && BOT.thread.view.host === host && !BOT.thread.view.gone) return;
  botThreadStop();
  const actor = 'bot:' + BOT.slug;
  // The thread's actions (call, Members, menu) sit in the page's top line beside the tabs, never a row of their own.
  // Both keep the bot chat's goal (chat-goal.js): the target in the composer, the pinned goal above the thread.
  const acts = {header: 'compact', actsHost: $('#bot-top-acts'), goals: BOT.slug, menu: false};
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
const isCommand = (text: unknown) => /^\s*\//.test(String(text || ''));
const commandWord = (text: unknown) => (String(text || '').trim().match(/^\/([a-z0-9-]*)/i) || [, ''])[1]!.toLowerCase();
const helpHTML = () => `<div class="cmd-out"><div class="lbl">Commands</div>${SLASH_HELP.map(([c, d]) =>
  `<div><code>${esc(c)}</code> <span class="muted">${esc(d)}</span></div>`).join('')}
  <div class="muted" style="margin-top:6px">Anything starting with <code>/</code> stays here; it is never sent to a bot.</div></div>`;
const unknownHTML = (word: string) => `<div class="cmd-out"><span class="err">Unknown command${word ? ` <code>/${esc(word)}</code>` : ''}.</span>
  <span class="muted">Messages starting with <code>/</code> are not sent. Commands: ${SLASH_HELP.map(([c]) => `<code>${esc(c)}</code>`).join(', ')}.</span></div>`;
// True when the text was a command and has been handled here, so the caller must not send it.
function runCommand(text: unknown, out: Element | null | undefined, slug: string | null | undefined) {
  if (!isCommand(text)) return false;
  const word = commandWord(text);
  const say = (html: string) => { if (out instanceof HTMLElement) { out.innerHTML = html; out.hidden = false; } };
  if (word === 'help') { say(helpHTML()); return true; }
  if (word === 'clear' && !slug) { say(`<div class="cmd-out"><span class="muted"><code>/clear</code> works on a bot's page, in its conversation box.</span></div>`); return true; }
  if (word === 'clear' && slug) {
    say(`<div class="cmd-out"><div>Start a fresh session for <strong>${empName(slug)}</strong> on its next run? Its history stays visible.</div>
      <div class="row" style="margin-top:7px"><button class="primary" type="button" data-cmd="yes">Clear session</button>
      <button class="ghost" type="button" data-cmd="no">Cancel</button><span class="muted" id="cmd-msg"></span></div></div>`);
    if (!(out instanceof HTMLElement)) return true;
    out.querySelector<HTMLElement>('[data-cmd="no"]')!.onclick = () => { out.hidden = true; out.innerHTML = ''; };
    out.querySelector<HTMLElement>('[data-cmd="yes"]')!.onclick = async ev => {
      const msg = out.querySelector<HTMLElement>('#cmd-msg')!, button = ev.target as HTMLButtonElement; button.disabled = true; msg.textContent = 'Clearing…';
      try {
        await post(`/employees/${encodeURIComponent(slug)}/session/clear`);
        out.hidden = true; out.innerHTML = '';
        toast('Session cleared: the next run starts fresh');
      } catch (e) {
        if (/\b403\b|only the owner|not shared|owner only/i.test(errorMessage(e, ''))) { out.hidden = true; out.innerHTML = ''; toast('only the owner can do that', true); return; }
        msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; button.disabled = false;
      }
    };
    return true;
  }
  say(unknownHTML(word));
  return true;
}
