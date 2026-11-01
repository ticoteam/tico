/* ui/app/automations.js — Automations (redesign): routing, the page's state and an open automation's header
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   The list of every automation, its filters and the Runs tab are automations-overview.js; the flow and its "+" menu
   automations-flow.js, the step editors automations-editors.js and the run log automations-runs.js. Ask (the Ask
   panel) has the Assistant build or change one. Payloads: redesign CONTRACT §2 (/api/v2/automations).
   #/automations?q=<words> opens the list searched (a bot's or a webhook's automations, linked from their pages). */
'use strict';

const AUTOMATIONS = '#/automations';
function isAutomationsRoute(r) { return r === AUTOMATIONS || r.startsWith(AUTOMATIONS + '/') || r.startsWith(AUTOMATIONS + '?'); }
const AU_BOT = 'botops';                             // builds automations for the Assistant; not offered as a step's bot
const auPhone = () => matchMedia('(max-width:760px)').matches;
const auClone = x => x == null ? x : JSON.parse(JSON.stringify(x));
const auApi = id => '/v2/automations/' + encodeURIComponent(id);
// One page visit's state; a route within the page keeps it (the list, the chat), leaving drops it.
let AU = null;
// Leaving keeps the list, its teams and its counts for the next visit (S.lastRead), which draws them at once.
function automationsStop() {
  if (!AU) return;
  if (AU.list && !AU.listError) S.lastRead.automations = {list: AU.list, groups: AU.groups, stats: AU.stats, range: AU.range};
  AU = null;
}

// #/automations[/<id>[/runs]]; an old #/automations/bot or <id>/bot link opens the list or the flow.
function auRouteParts(r) {
  const [id = '', view = ''] = r.slice(AUTOMATIONS.length + 1).split('?')[0].split('/').map(decodeURIComponent);
  if (id === 'bot') return {id: '', view: 'flow'};
  return {id, view: view === 'runs' ? view : 'flow'};
}
const auHref = (id, view) => AUTOMATIONS + (id ? '/' + encodeURIComponent(id) : '') + (id && view && view !== 'flow' ? '/' + view : '');

// The hub writes people as person:<id> (CONTRACT); older rows say human:<id>.
const auPersonId = a => /^(person|human):/.test(String(a || '')) ? String(a).split(':').slice(1).join(':') : '';
function auActorName(a) {
  const pid = auPersonId(a);
  if (pid) return pid === S.me?.id ? (S.me?.name || personDisplay(pid)) : personDisplay(pid);
  if (String(a || '').startsWith('group:')) return auGroupName(a);
  if (String(a || '').startsWith('assistant:')) {
    const who = String(a).slice(10);
    return (who === S.me?.id ? firstName(S.me?.name) || 'My' : firstName(personDisplay(who))) + '’s Assistant';
  }
  return actorLabel(String(a || '').startsWith('bot:') ? a : 'bot:' + a);
}
const auGroupName = g => (AU?.groups || []).find(x => x.id === g || 'group:' + x.id === g)?.name || titleCase(String(g || '').replace(/^group:/, ''));

const AU_TRIGGER_ICONS = {message: 'mail', task_received: 'task_alt', schedule: 'schedule', webhook: 'api', listening: 'travel_explore', event: 'campaign',
  due: 'event'};
const AU_TRIGGER_WORDS = {message: 'Message received', task_received: 'Task received', schedule: 'Schedule', webhook: 'Webhook',
  listening: 'Listening match', event: 'Event', due: 'Before a due date'};
// The triggers + New and the trigger editor offer; the others (a message, a webhook...) are made where they come from.
const AU_WHEN = [['schedule', 'On a schedule'], ['event', 'When something happens'], ['due', 'Before a due date']];
const AU_REPEATS = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly']];
const auCap = s => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);
// An event trigger's event in a few words: the catalog's (backend/event_catalog.py, old spellings too) and the
// built-ins' tico.builtin.* (backend/redesign_migration.py). The dotted name is only ever a chip's tooltip.
const AU_EVENTS = {'task.created': 'Task created', 'task.assigned': 'Task assigned', 'task.step_changed': 'Task step changed',
  'task.state_changed': 'Task state changed', 'task.commented': 'Task comment', 'task.done': 'Task finished',
  'task.declined': 'Task declined', 'task.blocked': 'Task waiting', 'task.closed': 'Task closed', 'task.reopened': 'Task reopened',
  'approval.requested': 'Approval asked', 'approval.decided': 'Approval decided', 'approval.withdrawn': 'Approval withdrawn',
  'needs_you.created': 'Needs you', 'needs_you.answered': 'Needs you answered', 'bot.run_failed': 'Bot run failed',
  'bot.held': 'Bot held', 'bot.released': 'Bot released', 'release.published': 'Release published', 'meeting.ready': 'Meeting ready',
  'strategy.published': 'Strategy published', 'market.insight.urgent': 'Urgent insight', 'market.briefing': 'Market briefing',
  'inbound.received': 'Message received', 'health.finding': 'Health finding', 'webhook.received': 'Webhook received',
  'tico.notice': 'Notice'};
const AU_EVENT_MORE = {release_published: 'Release published', 'recording.ready': 'Meeting ready',
  'tico.builtin.wakes': 'Every event', 'tico.builtin.task_rule': 'Task rule', 'tico.builtin.health': 'Health check'};
function auEventWords(name) {
  const n = String(name || '');
  if (AU_EVENTS[n] || AU_EVENT_MORE[n]) return AU_EVENTS[n] || AU_EVENT_MORE[n];
  if (n.startsWith('tico.builtin.job.')) return 'System job';
  // Anything else (custom.<x>, a newer catalog event): its last words, never the dotted name.
  const words = n.replace(/^(tico\.builtin\.|tico\.|custom\.)/, '').replace(/[._*-]+/g, ' ').trim();
  return auCap(words) || 'Event';
}
// A schedule in words; every hour (or few) on some weekdays reads "every hour, weekdays", not its cron.
function auCronWords(cron) {
  const f = String(cron || '').trim().split(/\s+/), [mi, hr, dom, mon, dow] = f;
  if (f.length === 5 && dom === '*' && mon === '*' && /^[\d,-]+$/.test(dow) && /^(\*|\*\/\d+)$/.test(hr) && /^(\d+|\*\/\d+)$/.test(mi)) {
    const each = cronWords(`${mi} ${hr} * * *`), days = cronWords(`0 0 * * ${dow}`).replace(/ at 00:00$/, '');
    if (each !== `${mi} ${hr} * * *` && !days.includes('*')) return `${each}, ${days}`;
  }
  return cronWords(cron || '');
}
// The viewer's time zone, the default for a new schedule or due date.
function auZone() { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } }
// A day (YYYY-MM-DD, a calendar day with no zone) as "Mar 1"; with `year`, "Mar 1, 2027" outside this year.
function auDay(iso, year = false) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!m) return '';
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(year && d.getFullYear() !== new Date().getFullYear() ? {year: 'numeric'} : {})});
}
// A local day `n` days from today, as YYYY-MM-DD.
function auInDays(n) {
  const d = new Date(); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// A fresh trigger of each kind + New and the trigger editor make (backend/automation_due.py for a due date).
function auNewTrigger(type) {
  if (type === 'event') return {type, name: 'task.created'};
  if (type === 'due') return {type, first_due: auInDays(30), repeats: 'yearly', open_days: 14, tz: auZone()};
  return {type: 'schedule', cron: '0 9 * * 1-5', tz: auZone()};
}
// Where an automation's work comes from, in a few words: the list's "· source" and the trigger card's chip.
function auSource(t = {}) {
  if (t.type === 'message') return t.inbox || '';
  if (t.type === 'task_received') return t.to ? 'Tasks to ' + auActorName(t.to) : 'Tasks';
  if (t.type === 'schedule') return auCap(auCronWords(t.cron));
  if (t.type === 'webhook') return t.name || t.id || '';
  if (t.type === 'listening') return t.destination ? 'Listening · ' + t.destination : 'Listening';
  if (t.type === 'event') return t.name ? auEventWords(t.name) : '';
  // A repeat reads as its day ("Due Mar 1 · yearly"); only a one-off says its year.
  if (t.type === 'due') return [`Due ${auDay(t.first_due, t.repeats === 'once')}`, t.repeats, `${t.open_days ?? 14}d before`].filter(Boolean).join(' · ');
  return '';
}
const auSpendWords = a => a.spend_cap?.per_30m != null ? `$${Number(a.spend_cap.per_30m).toFixed(2)} / 30 min` : 'Bot’s limit';

async function pageAutomations() {
  const {id, view} = auRouteParts(S.route);
  $('#main').classList.add('au-layout');
  if (!AU || !document.body.contains(AU.root)) {
    automationsStop();
    AU = {list: null, groups: [], sets: null, cur: null, id: '', view: 'flow', ed: null, runs: null, menu: null, root: null,
          ...auOverviewState()};
    const kept = S.lastRead.automations;
    if (kept) {
      Object.assign(AU, {list: kept.list, groups: kept.groups}, kept.range === AU.range ? {stats: kept.stats} : {});
      // Read again behind the kept copy; only an answer that differs draws the list again.
      const state = AU;
      void auLoadList(state).then(() => {
        if (AU !== state) return;
        if (state.listError) { state.list = kept.list; state.listError = ''; return; }   // a failed read leaves the list as it was
        if (sameData(state.list, kept.list) && sameData(state.groups, kept.groups)) return;
        if (state.id) auFacetsDraw(); else auDrawList();
      });
    }
    auShell();
    const q = new URLSearchParams(S.route.split('?')[1] || '').get('q');
    if (q) { AU.f.q = q.trim().toLowerCase(); AU.root.querySelector('.au-q').value = q; }
  }
  const state = AU;
  state.view = view;
  state.root.dataset.screen = id ? 'detail' : 'list';
  if (state.list === null) { auRangeDraw(); auDrawTable(); await auLoadList(state); }
  if (AU !== state) return;
  // The page opens on the list; an automation opens from its row.
  if (id !== state.id) {
    state.id = id; state.ed = null; state.runs = null; state.cur = null; state.lastRun = undefined;
    if (id) {
      auDrawHead(); auDrawPane();
      // A slow answer for an automation left behind must not land on the one now open.
      let cur;
      try { cur = auNormal(await get(auApi(id))); }
      catch (e) { if (AU === state && state.id === id) { state.cur = null; state.root.querySelector('.au-pane').innerHTML = [403, 404].includes(e.status)
        ? '<p class="muted au-pad">Not shared with you</p>' : `<p class="err">${esc(e.message)}</p>`; } return; }
      if (AU !== state || state.id !== id) return;
      state.cur = cur;
    }
  }
  auDrawHead(); auDrawPane(); auDrawSide();
  if (id) auFacetsDraw(); else auOverviewShow(state);
  if (state.view === 'runs' && state.cur && !state.runs) void auRunsLoad(state);
}
// GET answers the automation itself; a wrapped {automation} is read the same.
const auNormal = x => { const a = x?.automation || x || {}; a.steps ||= []; a.trigger ||= {}; return a; };

function auShell() {
  $('#main').innerHTML = `<div class="au" id="au-root">
    <header class="au-ph"><h1>Automations</h1><div class="au-seg au-range" role="group" aria-label="Time range"></div><span class="au-sp"></span><button class="docs-ask" type="button" data-ask-open aria-label="Ask" title="Ask">${ldotHTML({state: 'still', size: 16})}<span class="docs-ask-word">Ask</span></button><span class="au-newbox"><button class="primary au-new" type="button" aria-haspopup="menu" aria-expanded="false">+ New</button>
      <div class="au-newm" role="menu" hidden>${AU_WHEN.map(([k, l]) => `<button type="button" role="menuitem" data-new="${k}"><span class="nav-icon" aria-hidden="true">${AU_TRIGGER_ICONS[k]}</span>${l}</button>`).join('')}</div></span></header>
    <div class="au-body">
      <aside class="au-rail" aria-label="Filters">
        <div class="au-s"><input class="au-q" type="search" placeholder="Search" aria-label="Search automations"></div>
        <div class="au-facets"></div>
      </aside>
      <section class="au-c" aria-live="polite">
        <div class="au-ov"><div class="au-ctl"></div><div class="au-tbl"></div></div>
        <div class="au-crumb"></div><div class="au-hd"></div><div class="au-pane"></div><div class="au-menu" role="menu" hidden></div></section>
      <aside class="au-side"><div class="au-ed" hidden></div></aside>
      <div class="au-scrim" hidden></div>
    </div>
  </div>`;
  const root = AU.root = $('#au-root');
  auOverviewWire(root);
  root.querySelector('.au-new').onclick = () => auNewMenu(root.querySelector('.au-newm').hidden);
  root.querySelector('.au-newm').onclick = e => { const m = e.target.closest('[data-new]'); if (m) void auBlank(m.dataset.new); };
  root.querySelector('.au-scrim').onclick = () => auEdClose();
  root.addEventListener('change', auOnSwitch);
  root.addEventListener('click', auOnClick);
  root.addEventListener('keydown', e => {
    if (e.key === 'Escape') { if (!root.querySelector('.au-newm').hidden) auNewMenu(false); else if (AU.menu) auMenuClose(); else if (AU.ed) auEdClose(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[role="button"]')) { e.preventDefault(); e.target.click(); }
  });
  auEdWire(root.querySelector('.au-ed'));
}

// + New's menu: which trigger the new one starts with.
function auNewMenu(open) {
  const menu = AU.root.querySelector('.au-newm');
  menu.hidden = !open;
  AU.root.querySelector('.au-new').setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector('button')?.focus();
}
// + New: the viewer's own, off, no steps, on the trigger picked (a schedule is weekdays at 9 in their zone; a due date a
// month out, yearly; auNewTrigger), POST /v2/automations, then open it. Ask (the Ask panel) has the Assistant build
// one instead.
async function auBlank(type) {
  const state = AU, button = state.root.querySelector('.au-new');
  auNewMenu(false);
  button.disabled = true;
  try {
    const out = auNormal(await post('/v2/automations', {name: 'New automation', owner: 'person:' + S.me.id, enabled: false,
      builtin: false, trigger: auNewTrigger(type), steps: []}));
    if (AU !== state) return;
    await auLoadList(state);
    if (AU === state) location.hash = auHref(out.id);
  } catch (e) { toast(e.message, true); }
  finally { if (button.isConnected) button.disabled = false; }
}

// Every automation the viewer may see: the list pages by cursor, up to 500 a page.
async function auAllAutomations() {
  let out = [], cursor = 0;
  for (let i = 0; i < 20; i++) {
    const page = await get('/v2/automations?limit=500' + (cursor ? '&cursor=' + cursor : ''));
    out = out.concat(page.automations || []);
    if (page.next == null || page.next === cursor) return out;
    cursor = page.next;
  }
  return out;
}
async function auLoadList(state) {
  const [list, groups] = await Promise.all([
    auAllAutomations().catch(e => ({error: e})),
    get('/v2/groups').catch(() => ({groups: []})),
  ]);
  // A read that lands after the page was left is kept for the next visit when nothing is kept yet.
  if (AU !== state) {
    if (!list.error && !S.lastRead.automations) S.lastRead.automations = {list, groups: Array.isArray(groups) ? groups : groups.groups || []};
    return;
  }
  state.groups = Array.isArray(groups) ? groups : groups.groups || [];
  if (list.error) { state.list = []; state.listError = list.error.message; return; }
  state.listError = '';
  state.list = list;
}
const auRowName = a => { const src = auSource(a.trigger); return {name: a.name || 'Untitled', src: src && !String(a.name || '').toLowerCase().includes(src.toLowerCase()) ? src : ''}; };
// The on/off switch: `can_pause` (a read-only built-in shows it to the top of the org only); older hubs send can_edit.
const auMayPause = a => a.can_pause ?? !!a.can_edit;
function auSwitch(on, label, attrs = '') {
  return `<input type="checkbox" role="switch" class="people-switch au-sw" aria-label="${esc(label)}"${on ? ' checked' : ''} ${attrs}>`;
}
// The header's on/off: the switch for whoever may pause it, else whether it is off.
function auHeadSwitch(a, name) {
  if (auMayPause(a)) return auSwitch(a.enabled !== false, name + ' on', 'data-on-cur');
  return a.enabled === false ? `<span class="pill${a.paused ? ' waiting' : ''}">${a.paused ? 'Paused' : 'Off'}</span>` : '';
}
// The run guard's hold, on its own line: how many runs wait, and Release or Drop for whoever may change it.
function auHoldLine(a) {
  const h = a.hold;
  if (!h) return '';
  const n = h.held + ' held' + (h.dropped ? ` · ${h.dropped} dropped` : '');
  return `<div class="au-hold"><span class="pill waiting">On hold</span><span>${esc(n)}</span>`
    + (a.can_edit ? `<button class="au-chip" type="button" data-hold="release">Release</button>
      <button class="au-chip" type="button" data-hold="drop">Drop</button>` : '') + '</div>';
}
async function auHoldAct(action) {
  const a = AU.cur, state = AU;
  if (!a?.hold || (action === 'drop' && !confirm(`Drop ${a.hold.held} held runs of ${a.name}?`))) return;
  try {
    const out = auNormal(await post(`${auApi(a.id)}/held`, {action}));
    if (AU !== state) return;
    delete out.moved;
    out.hold ??= null;   // the list row keeps what it had unless told the hold is gone
    auAdopt(out);
    if (state.runs) { state.runs = null; void auRunsLoad(state); }
    toast(action === 'release' ? 'Released' : 'Dropped');
  } catch (e) { toast(e.message, true); }
}
// Why a built-in has no switch, on its own line under the name so the name stays whole.
const auHeadLock = a => a.builtin && !auMayPause(a)
  ? '<span class="au-lock"><span class="nav-icon" aria-hidden="true">lock</span>Built-in: can’t be turned off</span>' : '';
function auDrawHead() {
  const hd = AU.root.querySelector('.au-hd'), a = AU.cur;
  AU.root.querySelector('.au-crumb').innerHTML = AU.id ? `<a class="au-up" href="${AUTOMATIONS}"><span class="nav-icon" aria-hidden="true">chevron_left</span>Automations</a>` : '';
  if (!AU.id) { hd.innerHTML = ''; return; }
  const row = a && (AU.list || []).find(x => x.id === a.id);
  const name = a?.name || row?.name || '';
  const seg = ['flow', 'runs'].map(v => `<a role="tab" href="${auHref(AU.id, v)}" aria-selected="${AU.view === v}">${v === 'flow' ? 'Flow' : 'Runs'}</a>`).join('');
  hd.innerHTML = `<h2 class="au-name">${esc(name)}</h2>
    ${a ? `<span class="au-own" title="${esc(auActorName(a.owner_id))}">${personCircle(auActorName(a.owner_id), 18)}<span class="au-own-n">${esc(auActorName(a.owner_id))}</span></span>
    <span class="au-sp"></span>
    <div class="au-hd2">
      ${auHeadSwitch(a, name)}
      ${a.readonly ? '' : `${a.can_edit ? `<button class="au-chip" type="button" data-spend><span class="nav-icon" aria-hidden="true">payments</span>${esc(auSpendWords(a))}</button>`
        : `<span class="au-chip"><span class="nav-icon" aria-hidden="true">payments</span>${esc(auSpendWords(a))}</span>`}
      <button class="au-help" type="button" aria-label="Spend limit help" data-help="Spend limit per 30 minutes. It resumes on its own.">?</button>`}
      <span class="au-sp au-sp-phone"></span>
      <div class="au-seg" role="tablist" aria-label="View">${seg}</div>
    </div>
    <button class="au-chip au-ver" type="button" data-versions aria-label="Versions, v${a.version}">v${esc(a.version)}${row?.pending ? ' · Proposed' : ''}</button>
    <button class="au-icon au-more" type="button" aria-label="Options" aria-haspopup="menu" data-more><span class="nav-icon" aria-hidden="true">more_horiz</span></button>
    ${auHeadLock(a)}${auHoldLine(a)}${auWarn(a)}` : ''}`;
}

function auDrawPane() {
  const pane = AU.root.querySelector('.au-pane');
  if (!AU.id) { pane.innerHTML = ''; return; }
  if (!AU.cur) { pane.innerHTML = '<p class="muted au-pad">Loading…</p>'; return; }
  const top = pane.scrollTop;
  if (AU.view === 'runs') auRunsDraw(pane); else pane.innerHTML = auFlowHTML(AU.cur);
  pane.scrollTop = top;
  if (AU.view !== 'runs') auPathsWire(pane);
}

// The right side: an open editor, else nothing (the list or flow takes the width). A phone shows it as a sheet over the flow.
// A redraw while a field is being typed in (a late load of question sets or the last run) keeps that field focused.
function auDrawSide() {
  const ed = AU.root.querySelector('.au-ed'), html = AU.ed ? auEdHTML() : '';
  const typing = ed.contains(document.activeElement) ? document.activeElement : null, b = typing?.dataset?.b;
  const sel = b && typeof typing.selectionStart === 'number' ? [typing.selectionStart, typing.selectionEnd] : null;
  ed.hidden = !html;
  ed.innerHTML = html;
  const again = b && html ? ed.querySelector(`[data-b="${CSS.escape(b)}"]`) : null;
  if (again) { again.focus({preventScroll: true}); if (sel) try { again.setSelectionRange(...sel); } catch { /* not a text field */ } }
  AU.root.classList.toggle('ed-open', !!html);
  AU.root.querySelector('.au-scrim').hidden = !html;
}

// Writes. The answer is the automation as saved (a flow change carries its new version).
async function auWrite(body, done) {
  const a = AU.cur, state = AU;
  try {
    const out = auNormal(await patch(auApi(a.id), body));
    if (AU !== state) return null;
    auAdopt(out);
    if (done) toast(done(out));
    return out;
  } catch (e) { toast(e.message, true); if (AU === state) { auDrawList(); auDrawHead(); auDrawPane(); } return null; }
}
function auAdopt(a) {
  if (!AU) return;
  if (AU.cur?.id === a.id) AU.cur = a;
  const i = (AU.list || []).findIndex(x => x.id === a.id);
  if (i >= 0) AU.list[i] = {...AU.list[i], ...a};
  auDrawList(); auDrawHead(); auDrawPane(); auDrawSide();
}

async function auOnSwitch(e) {
  const t = e.target;
  if (!t.matches?.('.au-sw')) return;
  const listId = t.dataset.on, stepId = t.dataset.step;
  if (stepId) { await auStepToggle(stepId, t.checked); return; }
  const id = listId || AU.cur?.id, row = (AU.list || []).find(x => x.id === id) || AU.cur;
  if (!row) return;
  // A built-in is paused only by the top of the org (the server decides), and never without saying what stops.
  if (!t.checked && row.builtin && !confirm(`${row.name} is built in. Turning it off stops it for everyone.`)) { t.checked = true; return; }
  const state = AU;
  try {
    const out = auNormal(await patch(auApi(id), {enabled: t.checked}));
    if (AU !== state) return;
    const i = state.list.findIndex(x => x.id === id);
    if (i >= 0) state.list[i] = {...state.list[i], enabled: out.enabled ?? t.checked};
    if (state.cur?.id === id) state.cur = {...state.cur, ...out, enabled: out.enabled ?? t.checked};
    auDrawList(); auDrawHead();
  } catch (err) { toast(err.message, true); t.checked = !t.checked; }
}

function auOnClick(e) {
  const t = e.target;
  if (!t.closest) return;
  if (!t.closest('.au-newbox') && !AU.root.querySelector('.au-newm').hidden) auNewMenu(false);
  if (AU.menu && !t.closest('.au-menu') && !t.closest('[data-add],[data-more]')) auMenuClose();
  if (auOverviewClick(t)) return;
  let m;
  if ((m = t.closest('.au-menu [data-ty]'))) { auInsert(m.dataset.ty); return; }
  if ((m = t.closest('.au-menu [data-mi]'))) { auMoreAct(m.dataset.mi); return; }
  if ((m = t.closest('[data-add]'))) { auMenuOpen(m, 'add'); return; }
  if ((m = t.closest('[data-more]'))) { auMenuOpen(m, 'more'); return; }
  if ((m = t.closest('[data-help]'))) { m.classList.toggle('on'); m.dataset.tip = m.dataset.help; return; }
  if ((m = t.closest('[data-spend]'))) { auEdOpen({kind: 'spend'}); return; }
  if ((m = t.closest('[data-versions]'))) { auEdOpen({kind: 'versions'}); return; }
  if ((m = t.closest('[data-dot]'))) { auPathsGo(m); return; }
  if ((m = t.closest('[data-run]'))) { auRunToggle(m.dataset.run); return; }
  if ((m = t.closest('[data-runs-more]'))) { void auRunsLoad(AU, true); return; }
  if ((m = t.closest('[data-hold]'))) { void auHoldAct(m.dataset.hold); return; }
  if (t.closest('.au-sw')) return;
  if ((m = t.closest('[data-open]'))) { auEdOpenStep(m.dataset.open, m.dataset.path); return; }
}

// The ⋯ menu: versions, delete (refused for a built-in, so it is shown off with why).
function auMoreHTML() {
  const a = AU.cur;
  return `${a.can_edit && !a.readonly ? '<button type="button" role="menuitem" data-mi="run"><span class="nav-icon" aria-hidden="true">play_arrow</span>Test run</button>' : ''}
    <button type="button" role="menuitem" data-mi="versions"><span class="nav-icon" aria-hidden="true">history</span>Versions</button>
    ${a.can_edit ? `<button type="button" role="menuitem" data-mi="delete"${a.builtin ? ' disabled title="Built-in automations can’t be deleted"' : ''}>
      <span class="nav-icon" aria-hidden="true">delete</span>${a.builtin ? 'Built-in' : 'Delete'}</button>` : ''}`;
}
async function auMoreAct(what) {
  auMenuClose();
  const a = AU.cur, state = AU;
  if (what === 'versions') { auEdOpen({kind: 'versions'}); return; }
  if (what === 'run') { auEdOpen({kind: 'run', text: auSample(a.trigger, a.next_due)}); return; }
  if (what !== 'delete' || a.builtin || !confirm(`Delete ${a.name}?`)) return;
  try {
    await writeRequest('DELETE', auApi(a.id));
    if (AU !== state) return;
    state.list = state.list.filter(x => x.id !== a.id);
    toast('Deleted');
    state.id = ''; state.cur = null;
    location.hash = AUTOMATIONS;
    auDrawList();
  } catch (e) { toast(e.message, true); }
}

function auMenuOpen(btn, kind) {
  const menu = AU.root.querySelector('.au-menu'), c = AU.root.querySelector('.au-c');
  AU.menu = {kind, at: btn.dataset.add || ''};
  menu.innerHTML = kind === 'add' ? auAddMenuHTML(AU.menu.at) : auMoreHTML();
  menu.hidden = false;
  menu.classList.toggle('au-menu-more', kind === 'more');
  if (auPhone()) { menu.style.top = menu.style.left = ''; menu.querySelector('button:not([disabled])')?.focus(); return; }
  const br = btn.getBoundingClientRect(), cr = c.getBoundingClientRect();
  let top = br.bottom - cr.top + 4, left = kind === 'more' ? br.right - cr.left - menu.offsetWidth : br.left - cr.left - 8;
  if (top + menu.offsetHeight > c.clientHeight - 8) top = Math.max(8, br.top - cr.top - menu.offsetHeight - 4);
  menu.style.top = top + 'px';
  menu.style.left = Math.max(8, Math.min(left, c.clientWidth - menu.offsetWidth - 8)) + 'px';
  menu.querySelector('button:not([disabled])')?.focus();
}
function auMenuClose() { AU.menu = null; const m = AU.root.querySelector('.au-menu'); m.hidden = true; m.innerHTML = ''; }
