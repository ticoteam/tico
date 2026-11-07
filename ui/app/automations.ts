/* ui/app/automations.ts — Automations (redesign): routing, the page's state and an open automation's header
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   The list of every automation, its filters and the Runs tab are automations-overview.ts; the flow and its "+" menu
   automations-flow.ts, the step editors automations-editors.ts, the run log automations-runs.ts, and New inbox and Apply
   template automations-new.ts. Ask (the Ask panel) has the Assistant build or change one. Payloads: redesign CONTRACT
   §2 (/api/v2/automations).
   #/automations?q=<words> opens the list searched (a bot's or a webhook's automations, linked from their pages). */
'use strict';

const AUTOMATIONS = '#/automations';
function isAutomationsRoute(r: string): boolean { return r === AUTOMATIONS || r.startsWith(AUTOMATIONS + '/') || r.startsWith(AUTOMATIONS + '?'); }
const AU_BOT = 'botops';                             // builds automations for the Assistant; not offered as a step's bot
const auPhone = (): boolean => matchMedia('(max-width:760px)').matches;
const auClone = <T>(x: T): T => x == null ? x : JSON.parse(JSON.stringify(x));
const auApi = (id: string): string => '/v2/automations/' + encodeURIComponent(id);

// The element a selector names in the page's markup. auShell draws the shell before any of this runs; a missing element
// throws at its first use, as it always did.
function auEl<T extends Element>(scope: ParentNode, sel: string): T {
  return scope.querySelector<T>(sel)!;   // `!`: the shell's fixed markup always has the element (see above)
}
// The open automation, for the actions only the detail page offers; they run while one is open (a closed page throws
// at the first read, as before).
function auOpenAutomation(): AuAutomation {
  return AU.cur!;   // `!`: the detail page's actions are the only callers, and they run while an automation is open
}

// AU is typed without null (see below), so the one place that makes its null value is this function.
// `!`: there is no page state between visits; AU holds null then.
function auNoState(): AutomationsState { return null!; }
// One page visit's state; a route within the page keeps it (the list, the chat), leaving drops it.
// AU is null between visits. It is typed without null so the JS readers (automations-heartbeat.ts and the rest) still
// check as before; the .ts readers use automationsState() (ui/app/automations-state.ts), which says null.
let AU: AutomationsState = auNoState();   // AU is null between visits (see the comment above)
// Leaving keeps the list, its teams and its counts for the next visit (S.lastRead), which draws them at once.
function automationsStop(): void {
  if (!AU) return;
  if (AU.list && !AU.listError) S.lastRead.automations = {list: AU.list, groups: AU.groups, stats: AU.stats, range: AU.range, maint: AU.maint, heart: AU.heart};
  clearTimeout(AU.hbPoll ?? undefined);
  AU = auNoState();   // the visit is over (see AU above)
}

// #/automations[/<id>[/runs]]; an old #/automations/bot or <id>/bot link opens the list or the flow.
function auRouteParts(r: string): {id: string; view: AuView} {
  const [id = '', view = ''] = (r.slice(AUTOMATIONS.length + 1).split('?')[0] ?? '').split('/').map(decodeURIComponent);
  if (id === 'bot') return {id: '', view: 'flow'};
  return {id, view: view === 'runs' ? view : 'flow'};
}
const auHref = (id: string, view?: string): string => AUTOMATIONS + (id ? '/' + encodeURIComponent(id) : '') + (id && view && view !== 'flow' ? '/' + view : '');

// The hub writes people as person:<id> (CONTRACT); older rows say human:<id>.
const auPersonId = (a: unknown): string => /^(person|human):/.test(String(a || '')) ? String(a).split(':').slice(1).join(':') : '';
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
  listening: 'Listening match', event: 'Event', due: 'Before a due date'};
// The triggers + New and the trigger editor offer; the others (a message, a webhook...) are made where they come from.
const AU_WHEN: Array<[string, string]> = [['schedule', 'On a schedule'], ['event', 'When something happens'], ['due', 'Before a due date']];
const AU_REPEATS: Array<[string, string]> = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly']];
const auCap = (s: string | undefined): string => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);
// An event trigger's event in a few words: the catalog's (backend/event_catalog.py, old spellings too) and the
// built-ins' tico.builtin.* (backend/redesign_migration.py). The dotted name is only ever a chip's tooltip.
const AU_EVENTS: Record<string, string> = {'task.created': 'Task created', 'task.assigned': 'Task assigned', 'task.step_changed': 'Task step changed',
  'task.state_changed': 'Task state changed', 'task.commented': 'Task comment', 'task.done': 'Task finished',
  'task.declined': 'Task declined', 'task.blocked': 'Task waiting', 'task.closed': 'Task closed', 'task.reopened': 'Task reopened',
  'approval.requested': 'Approval asked', 'approval.decided': 'Approval decided', 'approval.withdrawn': 'Approval withdrawn',
  'needs_you.created': 'Needs you', 'needs_you.answered': 'Needs you answered', 'bot.run_failed': 'Bot run failed',
  'bot.held': 'Bot held', 'bot.released': 'Bot released', 'release.published': 'Release published', 'meeting.ready': 'Meeting ready',
  'strategy.published': 'Strategy published', 'market.insight.urgent': 'Urgent insight', 'market.briefing': 'Market briefing',
  'inbound.received': 'Message received', 'health.finding': 'Health finding', 'webhook.received': 'Webhook received',
  'tico.notice': 'Notice'};
const AU_EVENT_MORE: Record<string, string> = {release_published: 'Release published', 'recording.ready': 'Meeting ready',
  'tico.builtin.wakes': 'Every event', 'tico.builtin.task_rule': 'Task rule', 'tico.builtin.health': 'Health check'};
function auEventWords(name: unknown): string {
  const n = String(name || '');
  const known = AU_EVENTS[n] || AU_EVENT_MORE[n];
  if (known) return known;
  if (n.startsWith('tico.builtin.job.')) return 'System job';
  // Anything else (custom.<x>, a newer catalog event): its last words, never the dotted name.
  const words = n.replace(/^(tico\.builtin\.|tico\.|custom\.)/, '').replace(/[._*-]+/g, ' ').trim();
  return auCap(words) || 'Event';
}
// A schedule in words; every hour (or few) on some weekdays reads "every hour, weekdays", not its cron.
function auCronWords(cron: string | undefined): string {
  const f = String(cron || '').trim().split(/\s+/), [mi = '', hr = '', dom, mon, dow = ''] = f;
  if (f.length === 5 && dom === '*' && mon === '*' && /^[\d,-]+$/.test(dow) && /^(\*|\*\/\d+)$/.test(hr) && /^(\d+|\*\/\d+)$/.test(mi)) {
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
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(year && d.getFullYear() !== new Date().getFullYear() ? {year: 'numeric'} : {})});
}
// A local day `n` days from today, as YYYY-MM-DD.
function auInDays(n: number): string {
  const d = new Date(); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// A fresh trigger of each kind + New and the trigger editor make (backend/automation_due.py for a due date).
function auNewTrigger(type: string): AuTrigger {
  if (type === 'event') return {type, name: 'task.created'};
  if (type === 'due') return {type, first_due: auInDays(30), repeats: 'yearly', open_days: 14, tz: auZone()};
  return {type: 'schedule', cron: '0 9 * * 1-5', tz: auZone()};
}
// Where an automation's work comes from, in a few words: the list's "· source" and the trigger card's chip.
function auSource(t: AuTrigger = {}): string {
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
const auSpendWords = (a: AuAutomation): string => a.spend_cap?.per_30m != null ? `$${Number(a.spend_cap.per_30m).toFixed(2)} / 30 min` : 'Bot’s limit';

async function pageAutomations(): Promise<void> {
  const {id, view} = auRouteParts(S.route);
  auEl<HTMLElement>(document, '#main').classList.add('au-layout');
  if (!AU || !document.body.contains(AU.root)) {
    automationsStop();
    AU = {list: null, groups: [], sets: null, cur: null, id: '', view: 'flow', ed: null, runs: null, menu: null, root: null!,   // `!`: auShell sets root on the next lines
          maint: null, maintError: '', mtGen: 0, mtRuns: null, mtOpen: new Set(),
          heart: null, heartError: '', hbGen: 0, hbLog: null, hbPoll: null, ...auOverviewState()};
    const kept = S.lastRead.automations;
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
  if (q) { AU.f.q = q.trim().toLowerCase(); auEl<HTMLInputElement>(AU.root, '.au-q').value = q; }
  const state = AU;
  state.view = view;
  state.root.dataset.screen = id ? 'detail' : 'list';
  if (state.list === null) { auRangeDraw(); auDrawTable(); await auLoadList(state); }
  if (AU !== state) return;
  // The page opens on the list; an automation opens from its row.
  if (id !== state.id) {
    state.id = id; state.ed = null; state.runs = null; state.cur = null; state.lastRun = undefined; state.mtRuns = null;
    state.hbLog = null; clearTimeout(state.hbPoll ?? undefined); state.hbPoll = null;
    // Maintenance is its own screen (automations-maintenance.ts), read from /v2/maintenance, not an automation.
    if (id === AU_MAINT) {
      auDrawHead(); auDrawPane();
      await auMaintLoad(state);
      if (AU !== state || state.id !== id) return;
    } else if (id === AU_HEART) {
      // The Heartbeat is its own screen (automations-heartbeat.ts), read from /v2/heartbeat, then once a minute.
      auDrawHead(); auDrawPane();
      await auHeartLoad(state);
      if (AU !== state || state.id !== id) return;
      auHeartPoll(state);
    } else if (id === AU_SYSJOBS) {
      // System jobs is its own screen too (automations-maintenance.ts), read from /v2/system-routines.
    } else if (id === AU_HOOKS) {
      // Incoming webhooks are their own screen too (ui/app/settings-webhooks.js), read from /v2/triggers.
    } else if (id) {
      auDrawHead(); auDrawPane();
      // A slow answer for an automation left behind must not land on the one now open.
      let cur: AuAutomation;
      try { cur = auNormal(await get(auApi(id))); }
      catch (e) {
        const status = errorStatus(e);
        if (AU === state && state.id === id) { state.cur = null; auEl<HTMLElement>(state.root, '.au-pane').innerHTML = (status === 403 || status === 404)
        ? '<p class="muted au-pad">Not shared with you</p>' : `<p class="err">${esc(errorMessage(e, ''))}</p>`; } return; }
      if (AU !== state || state.id !== id) return;
      state.cur = cur;
    }
  }
  auDrawHead(); auDrawPane(); auDrawSide();
  if (id) auFacetsDraw(); else auOverviewShow(state);
  if (state.view === 'runs' && state.cur && !state.runs) void auRunsLoad(state);
  if (state.view === 'runs' && id === AU_MAINT) void auMaintRunsLoad(state);   // each visit: a run may have ended since
}
// GET answers the automation itself; a wrapped {automation} is read the same.
type AuReply = Partial<AuAutomation> & {automation?: AuAutomation | undefined};
const auNormal = (x: AuReply | null | undefined): AuAutomation => {
  const a = (x?.automation || x || {}) as AuAutomation;   // `as`: the server's answer is an automation; an empty answer reads as {} as before
  a.steps ||= []; a.trigger ||= {}; return a;
};

function auShell(): void {
  auEl<HTMLElement>(document, '#main').innerHTML = `<div class="au" id="au-root">
    <header class="au-ph"><div class="au-hrow"><h1>Automations</h1><div class="au-seg au-range" role="group" aria-label="Time range"></div></div><span class="au-sp"></span><button class="docs-ask" type="button" data-ask-open aria-label="Ask" title="Ask">${ldotHTML({state: 'still', size: 16})}<span class="docs-ask-word">Ask</span></button>
      <div class="au-hrow"><button type="button" class="au-fbtn" data-filters aria-label="Filters" aria-expanded="false">${filterIcon(18)}<span class="au-fbn" hidden></span></button><span class="au-newbox"><button class="primary au-new" type="button" aria-label="New automation" aria-haspopup="menu" aria-expanded="false"><span class="nav-icon" aria-hidden="true">add</span><span class="au-new-w">+ New</span></button>
      <div class="au-newm" role="menu" hidden><button type="button" role="menuitem" data-new="inbox"><span class="nav-icon" aria-hidden="true">mail</span>New inbox</button>${AU_WHEN.map(([k, l]) => `<button type="button" role="menuitem" data-new="${k}"><span class="nav-icon" aria-hidden="true">${AU_TRIGGER_ICONS[k]}</span>${l}</button>`).join('')}</div></span></div></header>
    <div class="au-body">
      <aside class="au-rail" aria-label="Filters">
        <div class="au-s"><input class="au-q" type="search" placeholder="Search" aria-label="Search automations"></div>
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
  const root = auEl<HTMLElement>(document, '#au-root');
  AU.root = root;
  auPlace();
  auOverviewWire(root);
  auEl<HTMLButtonElement>(root, '.au-new').onclick = () => auNewMenu(auEl<HTMLElement>(root, '.au-newm').hidden);
  auEl<HTMLElement>(root, '.au-newm').onclick = e => {
    const m = eventElement(e)?.closest<HTMLElement>('[data-new]');
    if (m?.dataset.new === 'inbox') auNewInbox(); else if (m) void auBlank(m.dataset.new ?? '');
  };
  auEl<HTMLElement>(root, '.au-scrim').onclick = () => auEdClose();
  root.addEventListener('change', auOnSwitch);
  root.addEventListener('change', auMaintChange);
  root.addEventListener('change', auHeartChange);
  root.addEventListener('click', auOnClick);
  root.addEventListener('keydown', e => {
    if (e.key === 'Escape') { if (!auEl<HTMLElement>(root, '.au-newm').hidden) auNewMenu(false); else if (AU.menu) auMenuClose(); else if (AU.ed) auEdClose(); return; }
    const t = eventElement(e);
    if (t instanceof HTMLElement && (e.key === 'Enter' || e.key === ' ') && t.matches('[role="button"]')) { e.preventDefault(); t.click(); }
  });
  auEdWire(auEl<HTMLElement>(root, '.au-ed'));
}

// + New's menu: New inbox (automations-new.ts), or which trigger a blank one starts with.
function auNewMenu(open: boolean): void {
  const menu = auEl<HTMLElement>(AU.root, '.au-newm');
  menu.hidden = !open;
  auEl<HTMLElement>(AU.root, '.au-new').setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector<HTMLElement>('button')?.focus();
}
// + New: the viewer's own, off, no steps, on the trigger picked (a schedule is weekdays at 9 in their zone; a due date a
// month out, yearly; auNewTrigger), POST /v2/automations, then open it. Ask (the Ask panel) has the Assistant build
// one instead.
async function auBlank(type: string): Promise<void> {
  const state = AU, button = auEl<HTMLButtonElement>(state.root, '.au-new');
  auNewMenu(false);
  button.disabled = true;
  try {
    const out = auNormal(await post('/v2/automations', {name: 'New automation', owner: 'person:' + S.me!.id, enabled: false,   // `!`: a signed-in viewer; null throws into the catch below, as before
      builtin: false, trigger: auNewTrigger(type), steps: []}));
    if (AU !== state) return;
    await auLoadList(state);
    if (AU === state) location.hash = auHref(out.id);
  } catch (e) { toast(errorMessage(e, ''), true); }
  finally { if (button.isConnected) button.disabled = false; }
}

// Every automation the viewer may see: the list pages by cursor, up to 500 a page.
async function auAllAutomations(): Promise<AuAutomation[]> {
  let out: AuAutomation[] = [], cursor = 0;
  for (let i = 0; i < 20; i++) {
    const page = await get('/v2/automations?limit=500' + (cursor ? '&cursor=' + cursor : ''));
    out = out.concat(page.automations || []);
    if (page.next == null || page.next === cursor) return out;
    cursor = page.next;
  }
  return out;
}
async function auLoadList(state: AutomationsState): Promise<void> {
  // Maintenance, the Heartbeat and the incoming webhooks are read alongside: their rows lead the list
  // (automations-maintenance.ts, automations-heartbeat.ts, settings-webhooks.js); an older hub has none.
  const gen = state.mtGen, hbGen = state.hbGen;
  const [found, groups, maint, hooks, heart] = await Promise.all([
    auAllAutomations().catch(e => ({error: e})),
    get('/v2/groups').catch(() => ({groups: []})),
    get('/v2/maintenance').catch(() => null),
    get('/v2/triggers').catch(() => null),
    get('/v2/heartbeat?limit=1').catch(() => null),   // the row needs no page of beats
  ]);
  const m = state.mtGen === gen ? maint : state.maint;
  const h = state.hbGen === hbGen ? heart : state.heart;
  // The list, or the failure it came to (`found` is the error wrapper): the two never share a value, so no union reaches the page.
  const failed = 'error' in found ? found.error : undefined;
  const list = 'error' in found ? null : auHeartList(auMaintList(auHooksList(found, hooks), m), h);
  // A read that lands after the page was left is kept for the next visit when nothing is kept yet.
  if (AU !== state) {
    if (list && !S.lastRead.automations) S.lastRead.automations = {list, groups: Array.isArray(groups) ? groups : groups.groups || [], maint: auMaintOk(m) ? m : null, heart: auHeartOk(h) ? h : null};
    return;
  }
  state.groups = Array.isArray(groups) ? groups : groups.groups || [];
  if (auMaintOk(m)) {
    state.maint = m;
    if (m.live_run_id && !state.mtFollow) auMaintFollow(state);
  }
  if (auHeartOk(h)) state.heart = h;
  if (failed) { state.list = []; state.listError = errorMessage(failed, ''); return; }
  state.listError = '';
  state.list = list;
}
const auRowName = (a: AuAutomation): {name: string; src: string} => { const src = auSource(a.trigger); return {name: a.name || 'Untitled', src: src && !String(a.name || '').toLowerCase().includes(src.toLowerCase()) ? src : ''}; };
// The on/off switch: `can_pause` (a read-only built-in shows it to the top of the org only); older hubs send can_edit.
const auMayPause = (a: AuAutomation): boolean => a.can_pause ?? !!a.can_edit;
function auSwitch(on: boolean, label: string, attrs = ''): string {
  return `<input type="checkbox" role="switch" class="people-switch au-sw" aria-label="${esc(label)}"${on ? ' checked' : ''} ${attrs}>`;
}
// The header's on/off: the switch for whoever may pause it, else whether it is off.
function auHeadSwitch(a: AuAutomation, name: string): string {
  if (auMayPause(a)) return auSwitch(a.enabled !== false, name + ' on', 'data-on-cur');
  return a.enabled === false ? `<span class="pill${a.paused ? ' waiting' : ''}">${a.paused ? 'Paused' : 'Off'}</span>` : '';
}
// The run guard's hold, on its own line: how many runs wait, and Release or Drop for whoever may change it.
function auHoldLine(a: AuAutomation): string {
  const h = a.hold;
  if (!h) return '';
  const n = h.held + ' held' + (h.dropped ? ` · ${h.dropped} dropped` : '');
  return `<div class="au-hold"><span class="pill waiting">On hold</span><span>${esc(n)}</span>`
    + (a.can_edit ? `<button class="au-chip" type="button" data-hold="release">Release</button>
      <button class="au-chip" type="button" data-hold="drop">Drop</button>` : '') + '</div>';
}
async function auHoldAct(action: string): Promise<void> {
  const a = AU.cur, state = AU;
  if (!a?.hold || (action === 'drop' && !confirm(`Drop ${a.hold.held} held runs of ${a.name}?`))) return;
  try {
    const out: AuAutomation & {moved?: boolean} = auNormal(await post(`${auApi(a.id)}/held`, {action}));
    if (AU !== state) return;
    delete out.moved;
    out.hold ??= null;   // the list row keeps what it had unless told the hold is gone
    auAdopt(out);
    if (state.runs) { state.runs = null; void auRunsLoad(state); }
    toast(action === 'release' ? 'Released' : 'Dropped');
  } catch (e) { toast(errorMessage(e, ''), true); }
}
// Why a built-in has no switch, on its own line under the name so the name stays whole.
const auHeadLock = (a: AuAutomation): string => a.builtin && !auMayPause(a)
  ? '<span class="au-lock"><span class="nav-icon" aria-hidden="true">lock</span>Built-in: can’t be turned off</span>' : '';
function auDrawHead(): void {
  const hd = auEl<HTMLElement>(AU.root, '.au-hd'), a = AU.cur;
  auEl<HTMLElement>(AU.root, '.au-crumb').innerHTML = AU.id ? `<a class="au-up" href="${AUTOMATIONS}"><span class="nav-icon" aria-hidden="true">chevron_left</span>Automations</a>` : '';
  if (!AU.id) { hd.innerHTML = ''; return; }
  if (AU.id === AU_MAINT) { auMaintHead(hd); return; }
  if (AU.id === AU_HEART) { auHeartHead(hd); return; }
  if (AU.id === AU_SYSJOBS) { auSysJobsHead(hd); return; }
  if (AU.id === AU_HOOKS) { auHooksHead(hd); return; }
  const row = a ? (AU.list || []).find(x => x.id === a.id) : undefined;
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

function auDrawPane(): void {
  const pane = auEl<HTMLElement>(AU.root, '.au-pane');
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
function auDrawSide(): void {
  const ed = auEl<HTMLElement>(AU.root, '.au-ed'), html = AU.ed ? auEdHTML() : '';
  const active = document.activeElement;
  const typing = active instanceof HTMLElement && ed.contains(active) ? active : null;
  const b = typing?.dataset.b;
  const field = typing instanceof HTMLInputElement || typing instanceof HTMLTextAreaElement ? typing : null;
  const sel: [number, number] | null = b && field && typeof field.selectionStart === 'number' ? [field.selectionStart, field.selectionEnd ?? field.selectionStart] : null;
  ed.hidden = !html;
  ed.innerHTML = html;
  const again = b && html ? ed.querySelector<HTMLElement>(`[data-b="${CSS.escape(b)}"]`) : null;
  if (again) {
    again.focus({preventScroll: true});
    if (sel && (again instanceof HTMLInputElement || again instanceof HTMLTextAreaElement)) try { again.setSelectionRange(...sel); } catch { /* not a text field */ }
  }
  AU.root.classList.toggle('ed-open', !!html);
  // The board's pop-ups float over it on a computer; a phone keeps the sheet.
  AU.root.classList.toggle('ed-pop', !!html && AU.ed?.kind === 'board');
  auEl<HTMLElement>(AU.root, '.au-scrim').hidden = !html;
}

// Writes. The answer is the automation as saved (a flow change carries its new version).
async function auWrite(body: unknown, done?: (out: AuAutomation) => string): Promise<AuAutomation | null> {
  const a = auOpenAutomation(), state = AU;
  try {
    const out = auNormal(await patch(auApi(a.id), body));
    if (AU !== state) return null;
    auAdopt(out);
    if (done) toast(done(out));
    return out;
  } catch (e) { toast(errorMessage(e, ''), true); if (AU === state) { auDrawList(); auDrawHead(); auDrawPane(); } return null; }
}
function auAdopt(a: AuAutomation): void {
  if (!AU) return;
  if (AU.cur?.id === a.id) AU.cur = a;
  const list = AU.list ?? [];
  const i = list.findIndex(x => x.id === a.id), old = list[i];
  if (old) list[i] = {...old, ...a};   // a row not in the list has no old copy (i = -1 reads nothing)
  auDrawList(); auDrawHead(); auDrawPane(); auDrawSide();
}

async function auOnSwitch(e: Event): Promise<void> {
  const t = eventElement(e);
  if (!(t instanceof HTMLInputElement) || !t.matches('.au-sw')) return;
  const listId = t.dataset.on, stepId = t.dataset.step;
  if (stepId) { await auStepToggle(stepId, t.checked); return; }
  const id = listId || AU.cur?.id, row = (AU.list || []).find(x => x.id === id) || AU.cur;
  if (!row || !id) return;
  // A built-in is paused only by the top of the org (the server decides), and never without saying what stops.
  if (!t.checked && row.builtin && !confirm(`${row.name} is built in. Turning it off stops it for everyone.`)) { t.checked = true; return; }
  const state = AU;
  try {
    const out = auNormal(await patch(auApi(id), {enabled: t.checked}));
    if (AU !== state) return;
    const list = state.list ?? [];
    const i = list.findIndex(x => x.id === id), old = list[i];
    if (old) list[i] = {...old, enabled: out.enabled ?? t.checked};   // a row not in the list has no old copy
    if (state.cur?.id === id) state.cur = {...state.cur, ...out, enabled: out.enabled ?? t.checked};
    auDrawList(); auDrawHead();
  } catch (err) { toast(errorMessage(err, ''), true); t.checked = !t.checked; }
}

function auOnClick(e: Event): void {
  const t = eventElement(e);
  if (!t) return;
  if (!t.closest('.au-newbox') && !auEl<HTMLElement>(AU.root, '.au-newm').hidden) auNewMenu(false);
  if (AU.menu && !t.closest('.au-menu') && !t.closest('[data-add],[data-more]')) auMenuClose();
  if (auOverviewClick(t)) return;
  if (auMaintClick(t)) return;
  if (auHeartClick(t)) return;
  let m: HTMLElement | null;
  m = t.closest<HTMLElement>('.au-menu [data-ty]');
  if (m) { auInsert(m.dataset.ty ?? ''); return; }
  m = t.closest<HTMLElement>('.au-menu [data-mi]');
  if (m) { auMoreAct(m.dataset.mi ?? ''); return; }
  m = t.closest<HTMLElement>('[data-add]');
  if (m) { auMenuOpen(m, 'add'); return; }
  m = t.closest<HTMLElement>('[data-more]');
  if (m) { auMenuOpen(m, 'more'); return; }
  m = t.closest<HTMLElement>('[data-help]');
  if (m) { m.classList.toggle('on'); m.dataset.tip = m.dataset.help; return; }
  m = t.closest<HTMLElement>('[data-spend]');
  if (m) { auEdOpen({kind: 'spend'}); return; }
  m = t.closest<HTMLElement>('[data-versions]');
  if (m) { auEdOpen({kind: 'versions'}); return; }
  m = t.closest<HTMLElement>('[data-dot]');
  if (m) { auPathsGo(m); return; }
  m = t.closest<HTMLElement>('[data-run]');
  if (m) { auRunToggle(m.dataset.run ?? ''); return; }
  m = t.closest<HTMLElement>('[data-runs-more]');
  if (m) { void auRunsLoad(AU, true); return; }
  m = t.closest<HTMLElement>('[data-hold]');
  if (m) { void auHoldAct(m.dataset.hold ?? ''); return; }
  if (t.closest('.au-sw')) return;
  m = t.closest<HTMLElement>('[data-bmode]');
  if (m) { if (!m.hasAttribute('disabled')) void auBoardMode(m.dataset.bmode); return; }
  m = t.closest<HTMLElement>('[data-board]');
  if (m) { auBoardOpen(m.dataset.board); return; }
  m = t.closest<HTMLElement>('[data-open]');
  if (m) { auEdOpenStep(m.dataset.open, m.dataset.path); return; }
}

// The ⋯ menu: test run, Apply template (an October inbox, automations-new.ts), versions, delete (refused for a built-in,
// so it is shown off with why).
function auMoreHTML(): string {
  const a = auOpenAutomation();
  return `${a.can_edit && !a.readonly ? '<button type="button" role="menuitem" data-mi="run"><span class="nav-icon" aria-hidden="true">play_arrow</span>Test run</button>' : ''}
    ${auApplies(a) ? '<button type="button" role="menuitem" data-mi="apply"><span class="nav-icon" aria-hidden="true">dashboard_customize</span>Apply template</button>' : ''}
    <button type="button" role="menuitem" data-mi="versions"><span class="nav-icon" aria-hidden="true">history</span>Versions</button>
    ${a.can_edit ? `<button type="button" role="menuitem" data-mi="delete"${a.builtin ? ' disabled title="Built-in automations can’t be deleted"' : ''}>
      <span class="nav-icon" aria-hidden="true">delete</span>${a.builtin ? 'Built-in' : 'Delete'}</button>` : ''}`;
}
async function auMoreAct(what: string): Promise<void> {
  auMenuClose();
  const a = auOpenAutomation(), state = AU;
  if (what === 'versions') { auEdOpen({kind: 'versions'}); return; }
  if (what === 'run') { auEdOpen({kind: 'run', text: auSample(a.trigger, a.next_due_at ?? '')}); return; }
  if (what === 'apply') { auApplyTemplate(); return; }
  if (what !== 'delete' || a.builtin || !confirm(`Delete ${a.name}?`)) return;
  try {
    await writeRequest('DELETE', auApi(a.id), undefined);
    if (AU !== state) return;
    state.list = state.list!.filter(x => x.id !== a.id);   // `!`: the page lists its automations before this menu opens
    toast('Deleted');
    state.id = ''; state.cur = null;
    location.hash = AUTOMATIONS;
    auDrawList();
  } catch (e) { toast(errorMessage(e, ''), true); }
}

function auMenuOpen(btn: HTMLElement, kind: 'add' | 'more'): void {
  const menu = auEl<HTMLElement>(AU.root, '.au-menu'), c = auEl<HTMLElement>(AU.root, '.au-c');
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
function auMenuClose(): void { AU.menu = null; const m = auEl<HTMLElement>(AU.root, '.au-menu'); m.hidden = true; m.innerHTML = ''; }
