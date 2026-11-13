/* ui/app/automations.ts — Automations (redesign): routing, the page's state and an open automation's header
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   The list of every automation, its filters and the Runs tab are automations-overview.js; the flow and its "+" menu
   automations-flow.js, the step editors automations-editors.ts, the run log automations-runs.js, and New inbox and Apply
   template automations-new.js. Ask (the Ask panel) has the Assistant build or change one. Payloads: redesign CONTRACT
   §2 (/api/v2/automations).
   #/automations?q=<words> opens the list searched (a bot's or a webhook's automations, linked from their pages). */
'use strict';

const AUTOMATIONS = '#/automations';
// Listening (ui/app/listening.ts) is this page over the automations whose trigger is `listening`: its own sidebar row,
// its own list, the same header, flow, editors and runs.
const LISTENING = '#/listening';
const auUnder = (r: string, base: string): boolean => r === base || r.startsWith(base + '/') || r.startsWith(base + '?');
function isAutomationsRoute(r: string): boolean { return auUnder(r, AUTOMATIONS) || auUnder(r, LISTENING); }
/** The page's own address: Listening's while its list is the one shown, else Automations'. */
const auBase = (): string => AU?.lst ? LISTENING : AUTOMATIONS;
const AU_BOT = 'botops';                             // builds automations for the Assistant; not offered as a step's bot
const auPhone = () => matchMedia('(max-width:760px)').matches;
const auClone = <T>(x: T): T => x == null ? x : JSON.parse(JSON.stringify(x));
const auApi = (id: string) => '/v2/automations/' + encodeURIComponent(id);
// One page visit's state; a route within the page keeps it (the list, the chat), leaving drops it.
let AU: AutomationsState | null = null;
// Where the old code read x.y with x null (AU once the page is left, the open automation, the shell's #au-root or one
// of its parts) and so threw: the same stop, at the same read, as a plain Error naming what is missing. auNeed gives
// the value back; auSure is the statement form and auIs the form inside a condition (never false: it throws), both
// letting the rest of the scope read x. auHave is auSure for AU; auDrawn also needs the shell (#au-root, auShell).
function auNeed<T>(x: T | null | undefined, what: string): T {
  if (!x) throw new Error(`Missing ${what}`);
  return x;
}
function auSure<T>(x: T, what: string): asserts x is NonNullable<T> {
  if (!x) throw new Error(`Missing ${what}`);
}
function auIs<T>(x: T, what: string): x is NonNullable<T> {
  auSure(x, what);
  return true;
}
function auHave(a: AutomationsState | null): asserts a is AutomationsState {
  auSure(a, 'the Automations page');
}
// AU read inside a callback (where the check above does not reach): the same stop at the same read.
function auPage(): AutomationsState {
  return auNeed(AU, 'the Automations page');
}
/** The open automation, read where the old code read AU.cur.x (an editor or a pane of the open one). */
function auCur(): AuAutomation { return auNeed(auPage().cur, 'the open automation'); }
function auDrawn(a: AutomationsState | null): asserts a is AutomationsState & {root: HTMLElement} {
  auHave(a);
  auSure(a.root, '#au-root');
}
// A part of the shell (or of a dialog) the old code used at once: it threw where the part was missing.
function auEl<T extends Element = HTMLElement>(from: ParentNode, sel: string): T {
  return auNeed(from.querySelector<T>(sel), sel);
}
// Leaving keeps the list, its teams and its counts for the next visit (S.lastRead), which draws them at once.
function automationsStop() {
  if (!AU) return;
  if (AU.list && !AU.listError && !AU.lst) S.lastRead.automations = {list: AU.list, groups: AU.groups, stats: AU.stats, range: AU.range, maint: AU.maint, heart: AU.heart};
  clearTimeout(AU.hbPoll ?? undefined);
  AU = null;
}

// #/automations[/<id>[/runs]]; an old #/automations/bot or <id>/bot link opens the list or the flow.
function auRouteParts(r: string): {id: string; view: AuView} {
  const [id = '', view = ''] = r.slice((auUnder(r, LISTENING) ? LISTENING : AUTOMATIONS).length + 1).split('?')[0]!.split('/').map(decodeURIComponent);
  if (id === 'bot') return {id: '', view: 'flow'};
  return {id, view: view === 'runs' ? view : 'flow'};
}
const auHref = (id: string | undefined, view?: string) => auBase() + (id ? '/' + encodeURIComponent(id) : '') + (id && view && view !== 'flow' ? '/' + view : '');

// The hub writes people as person:<id> (CONTRACT); older rows say human:<id>.
const auPersonId = (a: unknown) => /^(person|human):/.test(String(a || '')) ? String(a).split(':').slice(1).join(':') : '';
function auActorName(a: unknown): string {
  if (a === AU_MAINT_OWNER) return 'Tico';
  const pid = auPersonId(a);
  if (pid) return pid === S.me?.id ? (S.me?.name || personDisplay(pid)) : personDisplay(pid);
  if (String(a || '').startsWith('group:')) return auGroupName(a);
  if (String(a || '').startsWith('assistant:')) {
    const who = String(a).slice(10);
    return (who === S.me?.id ? firstName(S.me?.name) || 'My' : firstName(personDisplay(who))) + '’s Assistant';
  }
  return actorLabel(String(a || '').startsWith('bot:') ? a : 'bot:' + a);
}
const auGroupName = (g: unknown): string => (AU?.groups || []).find(x => x.id === g || 'group:' + x.id === g)?.name || titleCase(String(g || '').replace(/^group:/, ''));

const AU_TRIGGER_ICONS: Record<string, string> = {message: 'mail', task_received: 'task_alt', schedule: 'schedule', webhook: 'api', listening: 'travel_explore', event: 'campaign',
  due: 'event'};
const AU_TRIGGER_WORDS: Record<string, string> = {message: 'Message received', task_received: 'Task received', schedule: 'Schedule', webhook: 'Webhook',
  listening: 'Listening', event: 'Event', due: 'Before a due date'};
// The triggers + New and the trigger editor offer; the others (a message, a webhook...) are made where they come from.
const AU_WHEN: Array<[string, string]> = [['schedule', 'On a schedule'], ['event', 'When something happens'], ['due', 'Before a due date']];
const AU_REPEATS: Array<[string, string]> = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly']];
const auCap = (s: unknown) => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);
// An event trigger's event in a few words: the catalog's (backend/event_catalog.py) and the built-ins'
// tico.builtin.*. The dotted name is only ever a chip's tooltip.
const AU_EVENTS: Record<string, string> = {'task.created': 'Task created', 'task.assigned': 'Task assigned', 'task.step_changed': 'Task stage changed',
  'task.commented': 'Task comment', 'task.done': 'Task finished', 'task.closed': 'Task closed', 'task.reopened': 'Task reopened',
  'thread.created': 'Thread started', 'message.created': 'Message sent', 'message.held': 'Message held',
  'bot.run_failed': 'Bot run failed', 'bot.held': 'Bot held', 'bot.released': 'Bot released', 'org_rule.retired': 'Rule retired',
  'rules.blocked': 'Rule blocked', 'rules.escalated': 'Rule escalated', 'rules.warned': 'Rule warned', 'rules.unchecked': 'Rule unchecked',
  'meeting.ready': 'Meeting ready', 'strategy.published': 'Strategy published', 'brand.published': 'Brand published',
  'market.insight.urgent': 'Urgent insight', 'market.briefing': 'Market briefing', 'inbound.received': 'Message received',
  'health.finding': 'Health finding', 'webhook.received': 'Webhook received', 'tico.notice': 'Notice'};
const AU_EVENT_MORE: Record<string, string> = {'tico.builtin.wakes': 'Every event', 'tico.builtin.health': 'Health check'};
function auEventWords(name: unknown) {
  const n = String(name || '');
  if (AU_EVENTS[n] || AU_EVENT_MORE[n]) return AU_EVENTS[n] || AU_EVENT_MORE[n];
  if (n.startsWith('tico.builtin.job.')) return 'System job';
  // Anything else (custom.<x>, a newer catalog event): its last words, never the dotted name.
  const words = n.replace(/^(tico\.builtin\.|tico\.|custom\.)/, '').replace(/[._*-]+/g, ' ').trim();
  return auCap(words) || 'Event';
}
// A schedule in words; every hour (or few) on some weekdays reads "every hour, weekdays", not its cron.
function auCronWords(cron: string | null | undefined): string {
  const f = String(cron || '').trim().split(/\s+/), [mi, hr, dom, mon, dow] = f;
  if (f.length === 5 && dom === '*' && mon === '*' && /^[\d,-]+$/.test(dow!) && /^(\*|\*\/\d+)$/.test(hr!) && /^(\d+|\*\/\d+)$/.test(mi!)) {
    const each = cronWords(`${mi} ${hr} * * *`), days = cronWords(`0 0 * * ${dow}`).replace(/ at 00:00$/, '');
    if (each !== `${mi} ${hr} * * *` && !days.includes('*')) return `${each}, ${days}`;
  }
  return cronWords(cron || '');
}
// The viewer's time zone, the default for a new schedule or due date.
function auZone(): string { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } }
// A day (YYYY-MM-DD, a calendar day with no zone) as "Mar 1"; with `year`, "Mar 1, 2027" outside this year.
function auDay(iso: unknown, year = false): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!m) return '';
  const d = new Date(+m[1]!, +m[2]! - 1, +m[3]!);
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(year && d.getFullYear() !== new Date().getFullYear() ? {year: 'numeric'} : {})});
}
// A local day `n` days from today, as YYYY-MM-DD.
function auInDays(n: number): string {
  const d = new Date(); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// A fresh trigger of each kind + New and the trigger editor make (backend/automation_due.py for a due date).
function auNewTrigger(type: string | undefined) {
  if (type === 'event') return {type, name: 'task.created'};
  if (type === 'due') return {type, first_due: auInDays(30), repeats: 'yearly', open_days: 14, tz: auZone()};
  return {type: 'schedule', cron: '0 9 * * 1-5', tz: auZone()};
}
// Where an automation's work comes from, in a few words: the list's "· source" and the trigger card's chip.
function auSource(t: Partial<AuFlowTrigger> = {}) {
  if (t.type === 'message') return t.inbox || '';
  if (t.type === 'task_received') return t.to ? 'Tasks to ' + auActorName(t.to) : 'Tasks';
  if (t.type === 'schedule') return auCap(auCronWords(t.cron));
  if (t.type === 'webhook') return t.name || t.id || '';
  if (t.type === 'listening') return lstLimitsWords(t.limits);
  if (t.type === 'event') return t.name ? auEventWords(t.name) : '';
  // A repeat reads as its day ("Due Mar 1 · yearly"); only a one-off says its year.
  if (t.type === 'due') return [`Due ${auDay(t.first_due, t.repeats === 'once')}`, t.repeats, `${t.open_days ?? 14}d before`].filter(Boolean).join(' · ');
  return '';
}
const auSpendWords = (a: AuRow) => a.spend_cap?.per_30m != null ? `$${Number(a.spend_cap.per_30m).toFixed(2)} / 30 min` : 'Bot’s limit';

async function pageAutomations() {
  const {id, view} = auRouteParts(S.route), lst = auUnder(S.route, LISTENING);
  $('#main').classList.add('au-layout');
  if (!AU || !document.body.contains(AU.root) || !!AU.lst !== lst) {
    automationsStop();
    AU = {lst, list: null, groups: [], sets: null, cur: null, id: '', view: 'flow', ed: null, runs: null, menu: null, root: null,
          maint: null, maintError: '', mtGen: 0, mtRuns: null, mtOpen: new Set(),
          heart: null, heartError: '', hbGen: 0, hbLog: null, hbPoll: null, ...auOverviewState()};
    const kept = lst ? null : S.lastRead.automations;
    if (kept) {
      Object.assign(AU, {list: kept.list, groups: kept.groups, maint: kept.maint || null, heart: kept.heart || null}, kept.range === AU.range ? {stats: kept.stats} : {});
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
  }
  // A search in the address (a webhook's Automations link, from inside this page too) narrows the list.
  const q = new URLSearchParams(S.route.split('?')[1] || '').get('q');
  if (q) { AU.f.q = q.trim().toLowerCase(); auEl<HTMLInputElement>(auNeed(AU.root, '#au-root'), '.au-q').value = q; }
  const state = AU;
  state.view = view;
  auDrawn(state);
  state.root.dataset.screen = id ? 'detail' : 'list';
  if (state.list === null) { auRangeDraw(); auDrawTable(); await auLoadList(state); }
  if (AU !== state) return;
  // The page opens on the list; an automation opens from its row.
  if (id !== state.id) {
    state.id = id; state.ed = null; state.runs = null; state.cur = null; state.lastRun = undefined; state.mtRuns = null;
    state.lstAddr = {};   // a push source's address is shown once, on the visit that made it
    state.hbLog = null; clearTimeout(state.hbPoll ?? undefined); state.hbPoll = null;
    // Maintenance is its own screen (automations-maintenance.js), read from /v2/maintenance, not an automation.
    if (id === AU_MAINT) {
      auDrawHead(); auDrawPane();
      await auMaintLoad(state);
      if (AU !== state || state.id !== id) return;
    } else if (id === AU_HEART) {
      // The Heartbeat is its own screen (automations-heartbeat.js), read from /v2/heartbeat, then once a minute.
      auDrawHead(); auDrawPane();
      await auHeartLoad(state);
      if (AU !== state || state.id !== id) return;
      auHeartPoll(state);
    } else if (id === AU_SYSJOBS) {
      // System jobs is its own screen too (automations-maintenance.js), read from /v2/system-routines.
    } else if (id === AU_HOOKS) {
      // Incoming webhooks are their own screen too (ui/app/settings-webhooks.ts), read from /v2/triggers.
    } else if (id) {
      auDrawHead(); auDrawPane();
      // A slow answer for an automation left behind must not land on the one now open.
      let cur: AuAutomation;
      try { cur = auNormal(await get(auApi(id))); }
      catch (e) { if (AU === state && state.id === id) { state.cur = null; auEl(state.root, '.au-pane').innerHTML = [403, 404].includes(errorStatus(e) ?? 0)
        ? '<p class="muted au-pad">Not shared with you</p>' : `<p class="err">${esc(errorMessage(e, ''))}</p>`; } return; }
      if (AU !== state || state.id !== id) return;
      // A listening opened from an old Automations address is shown on its own page.
      if (!lst && cur.trigger.type === 'listening') { location.replace(LISTENING + '/' + encodeURIComponent(id) + (view === 'runs' ? '/runs' : '')); return; }
      state.cur = cur;
    }
  }
  auDrawHead(); auDrawPane(); auDrawSide();
  if (id) auFacetsDraw(); else auOverviewShow(state);
  if (state.view === 'runs' && state.cur && !state.runs) void auRunsLoad(state);
  if (state.view === 'runs' && id === AU_MAINT) void auMaintRunsLoad(state);   // each visit: a run may have ended since
}
// GET answers the automation itself; a wrapped {automation} is read the same.
type AuAnswer = (AuRow & {automation?: undefined}) | {automation: AuRow};
const auNormal = (x: AuAnswer | null | undefined): AuAutomation => { const a: AuRow = x?.automation || x || ({} as AuRow); a.steps ||= []; a.trigger ||= {}; return a as AuAutomation; };

function auShell() {
  auHave(AU);
  const lst = !!AU.lst;
  $('#main').innerHTML = `<div class="au${lst ? ' au-lst' : ''}" id="au-root">
    <header class="au-ph"><div class="au-hrow"><h1>${lst ? 'Listening' : 'Automations'}</h1><div class="au-seg au-range" role="group" aria-label="Time range"></div></div><span class="au-sp"></span>
      <div class="au-hrow"><button type="button" class="au-fbtn" data-filters aria-label="Filters" aria-expanded="false">${filterIcon(18)}<span class="au-fbn" hidden></span></button><span class="au-newbox"><button class="primary add-ctl au-new" type="button" ${lst ? 'aria-label="New listening"' : 'aria-label="New automation" aria-haspopup="menu" aria-expanded="false"'}>${TL_ICON.plus}</button>
      <div class="au-newm" role="menu" hidden><button type="button" role="menuitem" data-new="inbox"><span class="nav-icon" aria-hidden="true">mail</span>New inbox</button>${AU_WHEN.map(([k, l]) => `<button type="button" role="menuitem" data-new="${k}"><span class="nav-icon" aria-hidden="true">${AU_TRIGGER_ICONS[k]}</span>${l}</button>`).join('')}</div></span></div></header>
    <div class="au-body">
      <aside class="au-rail" aria-label="Filters">
        <div class="au-s"><input class="au-q" type="search" placeholder="Search" aria-label="${lst ? 'Search listening' : 'Search automations'}"></div>
        <div class="au-facets"></div>
      </aside>
      <section class="au-c" aria-live="polite">
        <div class="au-ov"><div class="au-ctl"><div class="au-tabs" role="tablist" aria-label="View">
          <button type="button" role="tab" data-tab="list" aria-selected="true">Automations</button>
          <button type="button" role="tab" data-tab="runs" aria-selected="false">Runs</button></div><span class="au-sp"></span><span class="au-sels"></span></div><div class="au-tbl"></div></div>
        <div class="au-crumb"></div><div class="au-hd"></div><div class="au-pane"></div><div class="au-menu" role="menu" hidden></div></section>
      <aside class="au-side"><div class="au-ed" hidden></div></aside>
      <div class="au-scrim" hidden></div>
    </div>
  </div>`;
  const root = AU.root = $('#au-root');
  auPlace();
  auOverviewWire(root);
  root.querySelector('.au-new').onclick = () => lst ? void lstNew() : auNewMenu(root.querySelector('.au-newm').hidden);
  root.querySelector('.au-newm').onclick = (e: MouseEvent) => {
    const m = eventElement(e)?.closest<HTMLElement>('[data-new]');
    if (m?.dataset.new === 'inbox') auNewInbox(); else if (m) void auBlank(m.dataset.new);
  };
  root.querySelector('.au-scrim').onclick = () => auEdClose();
  root.addEventListener('change', auOnSwitch);
  root.addEventListener('change', auMaintChange);
  root.addEventListener('change', auHeartChange);
  root.addEventListener('click', auOnClick);
  root.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Escape') { if (!root.querySelector('.au-newm').hidden) auNewMenu(false); else if (auPage().menu) auMenuClose(); else if (auPage().ed) auEdClose(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target instanceof HTMLElement && e.target.matches('[role="button"]')) { e.preventDefault(); e.target.click(); }
  });
  auEdWire(root.querySelector('.au-ed'));
}

// + New's menu: New inbox (automations-new.js), or which trigger a blank one starts with.
function auNewMenu(open: boolean) {
  auDrawn(AU);
  const menu = auEl(AU.root, '.au-newm');
  menu.hidden = !open;
  auEl(AU.root, '.au-new').setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector('button')?.focus();
}
// + New: the viewer's own, off, no steps, on the trigger picked (a schedule is weekdays at 9 in their zone; a due date a
// month out, yearly; auNewTrigger), POST /v2/automations, then open it. Ask (the Ask panel) has the Assistant build
// one instead.
async function auBlank(type: string | undefined) {
  const state = AU;
  auDrawn(state);
  const button = auEl<HTMLButtonElement>(state.root, '.au-new');
  auNewMenu(false);
  button.disabled = true;
  try {
    const out = auNormal(await post('/v2/automations', {name: 'New automation', owner: 'person:' + S.me!.id, enabled: false,
      builtin: false, trigger: auNewTrigger(type), steps: []}));
    if (AU !== state) return;
    await auLoadList(state);
    if (AU === state) location.hash = auHref(out.id);
  } catch (e) { toast(errorMessage(e, ''), true); }
  finally { if (button.isConnected) button.disabled = false; }
}

// Every automation the viewer may see: the list pages by cursor, up to 500 a page. A failed read is {error} in its place
// (auLoadList), so the list says it has none.
async function auAllAutomations(): Promise<AuRow[] & {error?: undefined}> {
  let out: AuRow[] = [], cursor = 0;
  for (let i = 0; i < 20; i++) {
    const page = await get<Omit<import('../types/api').AutomationList, 'automations'> & {automations?: AuRow[]}>('/v2/automations?limit=500' + (cursor ? '&cursor=' + cursor : ''));
    out = out.concat(page.automations || []);
    if (page.next == null || page.next === cursor) return out;
    cursor = page.next;
  }
  return out;
}
async function auLoadList(state: AutomationsState) {
  // Maintenance, the Heartbeat and the incoming webhooks are read alongside: their rows lead the list
  // (automations-maintenance.js, automations-heartbeat.js, settings-webhooks.ts); an older hub has none.
  const gen = state.mtGen, hbGen = state.hbGen;
  const [found, groups, maint, hooks, heart] = await Promise.all([
    auAllAutomations().catch(e => ({error: e as Error})),
    get('/v2/groups').catch(() => ({groups: []})),
    state.lst ? null : get('/v2/maintenance').catch(() => null),
    state.lst ? null : get('/v2/triggers').catch(() => null),
    state.lst ? null : get('/v2/heartbeat?limit=1').catch(() => null),   // the row needs no page of beats
    state.lst ? lstSourcesLoad(state) : null,
  ]);
  const m = state.mtGen === gen ? maint : state.maint;
  const h = state.hbGen === hbGen ? heart : state.heart;
  // A listening is listed on its own page only (ui/app/listening.ts), and that page lists nothing else.
  const mine = (rows: AuRow[]): AuRow[] & {error?: undefined} => rows.filter(a => (a.trigger?.type === 'listening') === !!state.lst);
  const list = found.error ? found : state.lst ? mine(found) : auHeartList(auMaintList(auHooksList(mine(found), hooks), m), h);
  // A read that lands after the page was left is kept for the next visit when nothing is kept yet.
  if (AU !== state) {
    if (!list.error && !S.lastRead.automations) S.lastRead.automations = {list, groups: Array.isArray(groups) ? groups : groups.groups || [], maint: auMaintOk(m) ? m : null, heart: auHeartOk(h) ? h : null};
    return;
  }
  state.groups = Array.isArray(groups) ? groups : groups.groups || [];
  if (auMaintOk(m)) {
    state.maint = m;
    if (m.live_run_id && !state.mtFollow) auMaintFollow(state);
  }
  if (auHeartOk(h)) state.heart = h;
  if (list.error) { state.list = []; state.listError = list.error.message; return; }
  state.listError = '';
  state.list = list;
}
const auRowName = (a: AuRow) => { const src = auSource(a.trigger); return {name: a.name || 'Untitled', src: src && !String(a.name || '').toLowerCase().includes(src.toLowerCase()) ? src : ''}; };
// The on/off switch: `can_pause` (a read-only built-in shows it to the top of the org only); older hubs send can_edit.
const auMayPause = (a: AuRow) => a.can_pause ?? !!a.can_edit;
function auSwitch(on: boolean, label: string, attrs = '') {
  return `<input type="checkbox" role="switch" class="people-switch au-sw" aria-label="${esc(label)}"${on ? ' checked' : ''} ${attrs}>`;
}
// The header's on/off: the switch for whoever may pause it, else whether it is off.
function auHeadSwitch(a: AuRow, name: string) {
  if (auMayPause(a)) return auSwitch(a.enabled !== false, name + ' on', 'data-on-cur');
  return a.enabled === false ? `<span class="pill${a.paused ? ' waiting' : ''}">${a.paused ? 'Paused' : 'Off'}</span>` : '';
}
// The run guard's hold, on its own line: how many runs wait, and Release or Drop for whoever may change it.
function auHoldLine(a: AuRow) {
  const h = a.hold;
  if (!h) return '';
  const n = h.held + ' held' + (h.dropped ? ` · ${h.dropped} dropped` : '');
  return `<div class="au-hold"><span class="pill waiting">On hold</span><span>${esc(n)}</span>`
    + (a.can_edit ? `<button class="au-chip" type="button" data-hold="release">Release</button>
      <button class="au-chip" type="button" data-hold="drop">Drop</button>` : '') + '</div>';
}
async function auHoldAct(action: string) {
  auHave(AU);
  const a = AU.cur, state = AU;
  if (!a?.hold || (action === 'drop' && !(await askConfirm(`Drop ${a.hold.held} held runs of ${a.name}?`)))) return;
  try {
    const out = auNormal(await post(`${auApi(a.id)}/held`, {action}));
    if (AU !== state) return;
    delete out.moved;
    out.hold ??= null;   // the list row keeps what it had unless told the hold is gone
    auAdopt(out);
    if (state.runs) { state.runs = null; void auRunsLoad(state); }
    toast(action === 'release' ? 'Released' : 'Dropped');
  } catch (e) { toast(errorMessage(e, ''), true); }
}
// Why a built-in has no switch, on its own line under the name so the name stays whole.
const auHeadLock = (a: AuRow) => a.builtin && !auMayPause(a)
  ? '<span class="au-lock"><span class="nav-icon" aria-hidden="true">lock</span>Built-in: can’t be turned off</span>' : '';
function auDrawHead() {
  auDrawn(AU);
  const hd = auEl(AU.root, '.au-hd'), a = AU.cur;
  AU.root.classList.toggle('au-one', !!AU.id);   // Listening's search and + belong to its list (listening.css)
  auEl(AU.root, '.au-crumb').innerHTML = AU.id ? `<a class="au-up" href="${auBase()}"><span class="nav-icon" aria-hidden="true">chevron_left</span>${AU.lst ? 'Listening' : 'Automations'}</a>` : '';
  if (!AU.id) { hd.innerHTML = ''; return; }
  if (AU.id === AU_MAINT) { auMaintHead(hd); return; }
  if (AU.id === AU_HEART) { auHeartHead(hd); return; }
  if (AU.id === AU_SYSJOBS) { auSysJobsHead(hd); return; }
  if (AU.id === AU_HOOKS) { auHooksHead(hd); return; }
  const row = a && (AU.list || []).find(x => x.id === a.id);
  const name = a?.name || row?.name || '';
  const au = AU;   // L: read once for the tabs below
  const seg = ['flow', 'runs'].map(v => `<a role="tab" href="${auHref(au.id, v)}" aria-selected="${au.view === v}">${v === 'flow' ? 'Flow' : 'Runs'}</a>`).join('');
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
  auDrawn(AU);
  const pane = auEl(AU.root, '.au-pane');
  if (!AU.id) { pane.innerHTML = ''; return; }
  if (AU.id === AU_MAINT) { auMaintPane(pane); return; }
  if (AU.id === AU_HEART) { auHeartPane(pane); return; }
  if (AU.id === AU_SYSJOBS) { auSysJobsPane(pane); return; }
  if (AU.id === AU_HOOKS) { auHooksPane(pane); return; }
  if (!AU.cur) { pane.innerHTML = '<p class="muted au-pad">Loading…</p>'; return; }
  const top = pane.scrollTop;
  if (AU.view === 'runs') auRunsDraw(pane); else pane.innerHTML = auFlowHTML(AU.cur);
  pane.scrollTop = top;
  if (AU.view !== 'runs') { auPathsWire(pane); auBoardWire(pane); } else AU.branchRO?.disconnect();
}

// The right side: an open editor, else nothing (the list or flow takes the width). A phone shows it as a sheet over the flow.
// A redraw while a field is being typed in (a late load of question sets or the last run) keeps that field focused.
// The focused element as auDrawSide reads it: any element; a text field's binding and caret where it has them.
type AuTyping = Element & {dataset?: DOMStringMap; selectionStart?: number | null; selectionEnd?: number | null};
function auDrawSide() {
  auDrawn(AU);
  const ed = auEl(AU.root, '.au-ed'), html = AU.ed ? auEdHTML() : '';
  const typing: AuTyping | null = ed.contains(document.activeElement) ? document.activeElement : null, b = typing?.dataset?.b;
  const sel: [number, number] | null = b && typeof typing?.selectionStart === 'number' ? [typing.selectionStart, typing.selectionEnd!] : null;
  ed.hidden = !html;
  ed.innerHTML = html;
  const again = b && html ? ed.querySelector<HTMLInputElement>(`[data-b="${CSS.escape(b)}"]`) : null;
  if (again) { again.focus({preventScroll: true}); if (sel) try { again.setSelectionRange(...sel); } catch { /* not a text field */ } }
  AU.root.classList.toggle('ed-open', !!html);
  // The board's pop-ups float over it on a computer; a phone keeps the sheet.
  AU.root.classList.toggle('ed-pop', !!html && AU.ed?.kind === 'board');
  auEl(AU.root, '.au-scrim').hidden = !html;
}

// Writes. The answer is the automation as saved (a flow change carries its new version).
async function auWrite(body: Record<string, unknown>, done?: (out: AuRow) => string): Promise<AuRow | null> {
  auHave(AU);
  const a = AU.cur, state = AU;
  try {
    const out = auNormal(await patch(auApi(auNeed(a, 'the open automation').id), body));
    if (AU !== state) return null;
    auAdopt(out);
    if (done) toast(done(out));
    return out;
  } catch (e) { toast(errorMessage(e, ''), true); if (AU === state) { auDrawList(); auDrawHead(); auDrawPane(); } return null; }
}
function auAdopt(a: AuAutomation) {
  if (!AU) return;
  if (AU.cur?.id === a.id) AU.cur = a;
  const i = (AU.list || []).findIndex(x => x.id === a.id);
  if (i >= 0 && AU.list) AU.list[i] = {...AU.list[i], ...a};   // G: i >= 0 only when there is a list
  auDrawList(); auDrawHead(); auDrawPane(); auDrawSide();
}

async function auOnSwitch(e: Event) {
  const t = e.target;
  if (!(t instanceof HTMLInputElement) || !t.matches('.au-sw')) return;   // N: .au-sw is only ever a checkbox (auSwitch)
  const listId = t.dataset.on, stepId = t.dataset.step;
  if (stepId) { await auStepToggle(stepId, t.checked); return; }
  auHave(AU);
  const id = listId || AU.cur?.id, row = (AU.list || []).find(x => x.id === id) || AU.cur;
  if (!row) return;
  // A built-in is paused only by the top of the org (the server decides), and never without saying what stops.
  if (!t.checked && row.builtin && !(await askConfirm(`${row.name} is built in. Turning it off stops it for everyone.`))) { t.checked = true; return; }
  const state = AU;
  try {
    const out = auNormal(await patch(auApi(id as string), {enabled: t.checked}));
    if (AU !== state || !state.list) return;   // G: a switch is drawn from a read list (null only before the first read)
    const i = state.list.findIndex(x => x.id === id);
    if (i >= 0) state.list[i] = {...state.list[i]!, enabled: out.enabled ?? t.checked};
    if (state.cur?.id === id) state.cur = {...state.cur, ...out, enabled: out.enabled ?? t.checked};
    auDrawList(); auDrawHead();
  } catch (err) { toast(errorMessage(err, ''), true); t.checked = !t.checked; }
}

function auOnClick(e: Event) {
  const t = eventElement(e);
  if (!t?.closest) return;
  auHave(AU);
  if (!t.closest('.au-newbox') && !auEl(auNeed(AU.root, '#au-root'), '.au-newm').hidden) auNewMenu(false);
  if (AU.menu && !t.closest('.au-menu') && !t.closest('[data-add],[data-more]')) auMenuClose();
  if (auOverviewClick(t)) return;
  if (auMaintClick(t)) return;
  if (auHeartClick(t)) return;
  let m: (HTMLElement & {disabled?: boolean}) | null;   // a [data-bmode] is a button
  if ((m = t.closest<HTMLElement>('.au-menu [data-ty]'))) { auInsert(m.dataset.ty!); return; }   // matched on [data-ty], so it is set
  if ((m = t.closest<HTMLElement>('.au-menu [data-mi]'))) { auMoreAct(m.dataset.mi); return; }
  if ((m = t.closest<HTMLElement>('[data-add]'))) { auMenuOpen(m, 'add'); return; }
  if ((m = t.closest<HTMLElement>('[data-more]'))) { auMenuOpen(m, 'more'); return; }
  if ((m = t.closest<HTMLElement>('[data-help]'))) { m.classList.toggle('on'); m.dataset.tip = m.dataset.help; return; }
  if ((m = t.closest<HTMLElement>('[data-spend]'))) { auEdOpen({kind: 'spend'}); return; }
  if ((m = t.closest<HTMLElement>('[data-versions]'))) { auEdOpen({kind: 'versions'}); return; }
  if ((m = t.closest<HTMLElement>('[data-dot]'))) { auPathsGo(m); return; }
  if (lstClick(t)) return;
  if ((m = t.closest<HTMLElement>('[data-run]'))) { auRunToggle(m.dataset.run ?? ''); return; }
  if ((m = t.closest<HTMLElement>('[data-runs-more]'))) { void auRunsLoad(AU, true); return; }
  if ((m = t.closest<HTMLElement>('[data-hold]'))) { void auHoldAct(m.dataset.hold ?? ''); return; }
  if (t.closest('.au-sw')) return;
  if ((m = t.closest<HTMLElement>('[data-bmode]'))) { if (!m.disabled) void auBoardMode(m.dataset.bmode ?? ''); return; }
  if ((m = t.closest<HTMLElement>('[data-board]'))) { auBoardOpen(m.dataset.board ?? ''); return; }
  if ((m = t.closest<HTMLElement>('[data-open]'))) { auEdOpenStep(m.dataset.open ?? '', m.dataset.path); return; }
}

// The ⋯ menu: test run, Apply template (an October inbox, automations-new.js), versions, delete (refused for a built-in,
// so it is shown off with why).
function auMoreHTML() {
  auHave(AU);
  const a = auNeed(AU.cur, 'the open automation');
  return `${a.can_edit && !a.readonly ? '<button type="button" role="menuitem" data-mi="run"><span class="nav-icon" aria-hidden="true">play_arrow</span>Test run</button>' : ''}
    ${auApplies(a) ? '<button type="button" role="menuitem" data-mi="apply"><span class="nav-icon" aria-hidden="true">dashboard_customize</span>Apply template</button>' : ''}
    <button type="button" role="menuitem" data-mi="versions"><span class="nav-icon" aria-hidden="true">history</span>Versions</button>
    ${a.can_edit ? `<button type="button" role="menuitem" data-mi="delete"${a.builtin ? ' disabled title="Built-in automations can’t be deleted"' : ''}>
      <span class="nav-icon" aria-hidden="true">delete</span>${a.builtin ? 'Built-in' : 'Delete'}</button>` : ''}`;
}
async function auMoreAct(what: string | undefined) {
  auMenuClose();
  auHave(AU);
  const a = AU.cur, state = AU;
  if (what === 'versions') { auEdOpen({kind: 'versions'}); return; }
  if (what === 'run') { auSure(a, 'the open automation'); auEdOpen({kind: 'run', text: auSample(a.trigger, a.next_due_at)}); return; }
  if (what === 'apply') { auApplyTemplate(); return; }
  if (what !== 'delete' || !auIs(a, 'the open automation') || a.builtin || !(await askConfirm(`Delete ${a.name}?`))) return;
  try {
    await writeRequest('DELETE', auApi(a.id));
    if (AU !== state || !state.list) return;   // G: the ⋯ menu is drawn on a read list
    state.list = state.list.filter(x => x.id !== a.id);
    toast('Deleted');
    state.id = ''; state.cur = null;
    location.hash = auBase();
    auDrawList();
  } catch (e) { toast(errorMessage(e, ''), true); }
}

function auMenuOpen(btn: HTMLElement, kind: 'add' | 'more') {
  auDrawn(AU);
  const menu = auEl(AU.root, '.au-menu'), c = auEl(AU.root, '.au-c');
  AU.menu = {kind, at: btn.dataset.add || ''};
  menu.innerHTML = kind === 'add' ? auAddMenuHTML(AU.menu.at) : auMoreHTML();
  menu.hidden = false;
  menu.classList.toggle('au-menu-more', kind === 'more');
  if (auPhone()) { menu.style.top = menu.style.left = ''; menu.querySelector<HTMLElement>('button:not([disabled])')?.focus(); return; }
  const br = btn.getBoundingClientRect(), cr = c.getBoundingClientRect();
  let top = br.bottom - cr.top + 4, left = kind === 'more' ? br.right - cr.left - menu.offsetWidth : br.left - cr.left - 8;
  if (top + menu.offsetHeight > c.clientHeight - 8) top = Math.max(8, br.top - cr.top - menu.offsetHeight - 4);
  menu.style.top = top + 'px';
  menu.style.left = Math.max(8, Math.min(left, c.clientWidth - menu.offsetWidth - 8)) + 'px';
  menu.querySelector<HTMLElement>('button:not([disabled])')?.focus();
}
function auMenuClose() {
  auHave(AU);
  AU.menu = null; const m = auEl(auNeed(AU.root, '#au-root'), '.au-menu');
  m.hidden = true; m.innerHTML = '';
}
