/* ui/app/settings-history.ts — Settings > History: the Log, Runs, and the owner's Changes
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A setting's value as a history row carries it: an object for a bot's piece of setup, a count or switch, or a list.
interface SettingsValueObject {
  harness?: string;
  runtime?: string;
  model?: string;
  effort?: string;
  reasoning_effort?: string;
  display_name?: string;
  description?: string;
  slug?: string;
  status?: string;
  reports_to?: string;
  state?: string;
  label?: string;
  operator?: string;
}
type SettingsValue = SettingsValueObject | string | number | boolean | string[] | null | undefined;

function settingsValue(field: string, value: SettingsValue) {
  const obj = (value ?? {}) as SettingsValueObject;
  if (field === 'owners') return value === null ? 'registry defaults' : ((value as string[] | undefined) || []).map(settingsPersonName).join(', ') || 'nobody';
  if (field === 'access') return accessSummary(value as SaPolicy | null | undefined);
  if (field === 'model') return settingsChoiceLabel(obj.harness || obj.runtime, obj.model, obj.effort);
  if (field === 'fallback') return value ? settingsChoiceLabel(obj.harness, obj.model, obj.reasoning_effort || obj.effort) : 'None (fail)';
  if (field === 'profile') return profileHistoryWords(value as BpHistoryValue | null | undefined);
  if (field === 'wake_preset') return SETTINGS_WAKE_PRESETS.find(([preset]) => preset === value)?.[1] || String(value);
  if (field === 'turns_at_once') return value == null ? 'No limit' : `${value} ${value === 1 ? 'turn' : 'turns'}`;
  if (field === 'task_model') return obj.model ? settingsChoiceLabel(obj.harness || obj.runtime, obj.model, obj.reasoning_effort) : 'Same as bot';
  if (field === 'identity') return [obj.display_name, obj.description].filter(Boolean).join(' · ') || 'blank';
  if (field === 'manages_reports') return value ? 'On' : 'Off';
  if (field === 'lifecycle') return obj.state ? `${obj.state.charAt(0).toUpperCase()}${obj.state.slice(1)}` : 'Not made';
  if (field === 'compact_at') return SETTINGS_COMPACT_AT.find(([size]) => size === value)?.[1] || String(value);
  if (field === 'placement') return obj.label ? `${obj.label} · ${settingsPersonName(obj.operator)}` : 'not assigned';
  if (field === 'definition') return [obj.display_name, obj.status,
    obj.reports_to ? `reports to ${settingsBotName(obj.reports_to)}` : 'top level'].filter(Boolean).join(' · ');
  return JSON.stringify(value);
}

interface SettingsCheckpoint {
  current_objective?: string | null;
  next_recommended_action?: string | null;
  open_decisions?: string[] | null;
}

interface SettingsTransition {
  id: string;
  bot: string;
  kind: string;
  state: string;
  error?: string | null;
  checkpoints?: { checkpoint?: SettingsCheckpoint | null }[] | null;
  progress?: { prepared: number; total: number } | null;
}

function settingsCheckpointDetails(transition: SettingsTransition | undefined) {
  const prepared = (transition?.checkpoints || []).filter(row => row.checkpoint);
  if (!prepared.length) return '';
  return `<details><summary>${prepared.length} saved conversation checkpoint${prepared.length === 1 ? '' : 's'}</summary>${prepared.map(row => {
    const cp = row.checkpoint;
    return `<div class="transition-progress"><strong>${esc(cp?.current_objective || 'No active objective')}</strong><p>${esc(cp?.next_recommended_action || 'No next action recorded')}</p>
      ${cp?.open_decisions?.length ? `<p><strong>Open decisions:</strong> ${esc(cp.open_decisions.join(' · '))}</p>` : ''}</div>`;
  }).join('')}</details>`;
}
// A bot's setup changing, in words: only what changed ("Setting up → active", "Now reports to Ana").
const historyStatus = (v: string | null | undefined) => { const w = statusWord(v); return w.charAt(0).toUpperCase() + w.slice(1); };
function settingsDefinitionChange(before: SettingsValue, after: SettingsValue) {
  const b = (before || {}) as SettingsValueObject, a = (after || {}) as SettingsValueObject, out: string[] = [];
  if (b.display_name !== a.display_name) out.push(b.display_name ? `Renamed from ${b.display_name} to ${a.display_name}` : `Named ${a.display_name}`);
  if (b.slug && a.slug && b.slug !== a.slug) out.push(`Slug ${b.slug} → ${a.slug}`);
  if (b.status !== a.status) out.push(`${historyStatus(b.status) || 'New'} → ${historyStatus(a.status)}`);
  if ((b.reports_to || '') !== (a.reports_to || '')) out.push(a.reports_to ? `Now reports to ${settingsBotName(a.reports_to)}` : 'Now at the top level');
  return out.join(' · ') || 'No visible change';
}
// ----------------------------------------------------------------- Log
// The audit log (GET /api/v2/history, backend/history_log.py): what every teammate did, asked and was blocked from,
// newest first. The reader sees only rows their own SQL would show them. The view and the filters ride in the address
// (#/settings/history?kind=decision&bot=ops), so a filtered view can be shared.
interface HistoryLogItem {
  id: string;
  at: string;
  actor: string;
  about?: string;
  kind: string;
  subject?: string;
  link?: string;
  result: string;
}
interface HistoryLogPage {
  items: HistoryLogItem[];
  names: Record<string, string>;
  next?: string | null;
}
const HISTORY_KINDS: [string, string][] = [['run', 'Runs'], ['task', 'Tasks'], ['approval.asked', 'Approvals asked'],
  ['approval.granted', 'Approvals granted'], ['approval.denied', 'Approvals denied'], ['outbound.blocked', 'Outbound blocked'],
  ['credential.revealed', 'Credential reveals'], ['permission.changed', 'Permission changes'], ['decision', 'Decisions']];
const HISTORY_KIND_LABEL: Record<string, string> = {run: 'Run', task: 'Task', 'approval.asked': 'Approval asked', 'approval.granted': 'Approval granted',
  'approval.denied': 'Approval denied', 'outbound.blocked': 'Outbound blocked', 'credential.revealed': 'Credential revealed',
  'permission.changed': 'Permission change', decision: 'Decision'};
let SETTINGS_HISTORY_VIEW = 'log';
let SETTINGS_LOG: {scope: string; kind: string} = {scope: '', kind: ''};
// Runs showing only the day's failed runs (?view=runs&failed=1): where Health's Failed runs opens (backend/health.py).
let SETTINGS_RUNS_FAILED = false;
// The router hands over the query of #/settings/history?..; settingsAddress writes it back with settingsHistoryQuery.
function settingsHistoryAddress(query?: string | null) {
  const q = new URLSearchParams(query || '');
  const view = q.get('view');
  SETTINGS_HISTORY_VIEW = view === 'runs' || view === 'changes' ? view : 'log';
  SETTINGS_RUNS_FAILED = SETTINGS_HISTORY_VIEW === 'runs' && q.get('failed') === '1';
  const scope = ['bot', 'person', 'team'].map(k => q.get(k) ? `${k === 'person' ? 'human' : k}:${q.get(k)}` : '').find(Boolean) || '';
  const kind = q.get('kind') ?? '';
  SETTINGS_LOG = {scope, kind: HISTORY_KINDS.some(([k]) => k === kind) ? kind : ''};
}
// kind, and bot, person or team: the filters, as the API takes them.
function settingsLogFilters() {
  const q = new URLSearchParams();
  if (SETTINGS_LOG.kind) q.set('kind', SETTINGS_LOG.kind);
  if (SETTINGS_LOG.scope) { const [kind = '', id = ''] = SETTINGS_LOG.scope.split(/:(.*)/); q.set(kind === 'human' ? 'person' : kind, id); }
  return q;
}
function settingsHistoryQuery() {
  const q = SETTINGS_HISTORY_VIEW === 'log' ? settingsLogFilters() : new URLSearchParams();
  if (SETTINGS_HISTORY_VIEW !== 'log') q.set('view', SETTINGS_HISTORY_VIEW);
  if (SETTINGS_HISTORY_VIEW === 'runs' && SETTINGS_RUNS_FAILED) q.set('failed', '1');
  return q.toString() ? '?' + q : '';
}
function settingsLogWho(actor: string, names: Record<string, string>, item: Partial<HistoryLogItem> = {}) {
  // A row the server made names what made it (rowSource): a built-in or an automation, else "Automatic".
  const src = rowSource(item, actor);
  if (SOURCE_LINES.has(src.kind)) return src.kind === 'automation' && src.link
    ? `<a class="chip" href="${esc(src.link)}">${esc(src.name)}</a>` : `<span class="chip">${esc(src.name || AUTOMATIC_LABEL)}</span>`;
  const slug = actorSlug(actor);
  return slug ? empChip(slug) : `<span class="chip">${esc(names[actor] || actorLabel(actor) || AUTOMATIC_LABEL)}</span>`;
}
function settingsLogRowsHTML(items: HistoryLogItem[], names: Record<string, string>) {
  return items.map(item => {
    const about = item.about && item.about !== item.actor ? `<span class="log-about">${esc(names[item.about] || actorLabel(item.about))}</span>` : '';
    const link = item.link || '', away = link.startsWith('/');
    const subject = link ? `<a href="${esc(link)}"${away ? ' target="_blank" rel="noopener"' : ''}>${esc(item.subject || 'Open')}</a>` : esc(item.subject);
    return `<div class="log-row" data-log-id="${esc(item.id)}">
      <span class="log-when tnum">${agoSpan(item.at)}</span>
      <span class="log-who">${settingsLogWho(item.actor, names, item)}</span>
      <span class="log-kind">${esc(HISTORY_KIND_LABEL[item.kind] || item.kind)}</span>
      <span class="log-subject">${about}<span>${subject}</span></span>
      <span class="log-result" title="${esc(item.result)}">${esc(item.result)}</span></div>`;
  }).join('');
}
async function renderSettingsLog() {
  const el = $('#set-log'); if (!el) return;
  const filters = settingsLogFilters();
  el.innerHTML = `<div class="log-filters">
      <select id="log-who" aria-label="Who">${replayScopeOptions(SETTINGS_LOG.scope)}</select>
      <select id="log-kind" aria-label="Kind"><option value="">Everything</option>${HISTORY_KINDS.map(([k, label]) =>
        `<option value="${k}"${k === SETTINGS_LOG.kind ? ' selected' : ''}>${label}</option>`).join('')}</select>
      ${settingsIsAdmin() ? `<button class="linkish log-csv" id="log-csv" type="button">Export CSV</button>` : ''}
    </div><div id="log-rows">${skelRows(3, {card: true, dot: false})}</div>`;
  const change = () => {
    SETTINGS_LOG = {scope: $('#log-who').value, kind: $('#log-kind').value};
    settingsAddress(); void renderSettingsLog();
  };
  $('#log-who').onchange = change; $('#log-kind').onchange = change;
  const csv = $('#log-csv'); if (csv) csv.onclick = () => settingsLogCsv(csv, filters);
  const rows = $('#log-rows'), drawn = el._logDrawn = {};
  const draw = async (before: string | null | undefined, shown: {items: HistoryLogItem[]; names: Record<string, string>}): Promise<void> => {
    const q = new URLSearchParams(filters); if (before) q.set('before', before);
    const page = await get<HistoryLogPage>(`/v2/history${q.toString() ? '?' + q : ''}`);
    if (el._logDrawn !== drawn) return;   // a newer filter drew over this one
    const items = [...shown.items, ...page.items], names = {...shown.names, ...page.names};
    rows.innerHTML = items.length ? `<div class="log-list"><div class="log-row log-head"><span>When</span><span>Who</span><span>Kind</span><span>What</span><span>Result</span></div>${settingsLogRowsHTML(items, names)}</div>`
      + (page.next ? '<button class="ghost" type="button" data-log-older>Show older</button>' : '')
      : '<div class="empty">Nothing here yet.</div>';
    const older: HTMLButtonElement | null = rows.querySelector('[data-log-older]');
    if (older) older.onclick = async () => {
      older.disabled = true;
      try { await draw(page.next, {items, names}); } catch (error) { older.disabled = false; toast(errorMessage(error, ''), true); }
    };
  };
  try { await draw('', {items: [], names: {}}); } catch (e) { if (el._logDrawn === drawn) rows.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; }
}
// A CSV holds at most four 5,000-row server pages; the header row is kept once.
async function settingsLogCsv(button: HTMLButtonElement, filters: URLSearchParams) {
  button.disabled = true;
  try {
    let text = '', before = '', pages = 0;
    do {
      const q = new URLSearchParams(filters); if (before) q.set('before', before);
      const r = await fetch(`${API}/v2/history.csv?${q}`, {cache: 'no-store'}), body = await r.text();
      if (!r.ok) { let j; try { j = JSON.parse(body); } catch { j = {}; } throw apiFailure(j, r); }
      text = text ? text + body.slice(body.indexOf('\n') + 1) : body;
      before = r.headers.get('X-Next') || '';
      pages++;
    } while (before && pages < 4);
    const url = URL.createObjectURL(new Blob([text], {type: 'text/csv'})), link = document.createElement('a');
    link.href = url; link.download = 'tico-history.csv';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (before) toast('Exported the newest 20,000. Narrow the dates for more.');
  } catch (error) { toast(errorMessage(error, ''), true); }
  finally { button.disabled = false; }
}
// ----------------------------------------------------------------- Runs
// One run as GET /runs lists it (backend/runs.py): the bot's own Runs card keeps runsTable (ui/app/issues.ts).
interface HistoryRun {
  id?: string | null;
  employee: string;
  state: string;
  exit: number | null;
  outcome?: string | null;
  failure_reason?: string | null;
  cost_usd?: number | null;
  output_tokens?: number | null;
  ran_on?: RanOn | null;
  fallback?: string | null;
  resumed?: boolean;
  duration_s?: number | null;
  finished_at?: string | null;
}
// Every run this person may see (GET /runs), one line each: the bot's own Runs card keeps runsTable (ui/app/issues.ts).
// Changes is the owner's alone: anyone else sees the Log or Runs, even on a page drawn before they lost the role.
function settingsHistoryShow(view = SETTINGS_HISTORY_VIEW) {
  const log = $('#set-log'), runs = $('#set-runs'), changes = $('#set-history'), owner = S.me?.role === 'owner'; if (!runs) return;
  SETTINGS_HISTORY_VIEW = view === 'runs' ? 'runs' : changes && owner && view === 'changes' ? 'changes' : 'log';
  const own = $('[data-history-view=changes]'); if (own) own.hidden = !owner;
  if (log) log.hidden = SETTINGS_HISTORY_VIEW !== 'log';
  runs.hidden = SETTINGS_HISTORY_VIEW !== 'runs';
  if (changes) changes.hidden = SETTINGS_HISTORY_VIEW !== 'changes';
  document.querySelectorAll<HTMLElement>('[data-history-view]').forEach(button => button.setAttribute('aria-checked', String(button.dataset.historyView === SETTINGS_HISTORY_VIEW)));
  settingsAddress();
  if (SETTINGS_HISTORY_VIEW === 'log') void renderSettingsLog();
  if (SETTINGS_HISTORY_VIEW === 'runs') void renderSettingsRuns();
}
// "18K out", so a run with no price fits its column on a phone; the exact count is in the CSV and the log.
const settingsRunTokens = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1});
const settingsRunTook = (s?: number | null) => s == null ? '' : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
function settingsRunsHTML(list: HistoryRun[], failed = false) {
  // Each job that failed in the last day, by the try that failed it; retries that went back in the queue are not here.
  const head = failed ? `<p class="runs-filter">Failed runs in the last day · <a href="#/settings/history?view=runs">Show all runs</a></p>` : '';
  if (!list.length) return head + `<div class="empty">${failed ? 'No failed runs in the last day.' : 'No runs yet.'}</div>`;
  return `${head}<div class="runs-list"><div class="run-row run-head"><span>Bot</span><span>Result</span><span>Model</span><span>Took</span><span>Cost</span><span>Finished</span><span></span></div>
    ${list.map(r => {
      const live = runLive(r), unfinished = runUnfinished(r), ok = !unfinished && r.exit === 0;
      // A failed or expired run reads its outcome alone; its id is in the pill's tooltip.
      const result = ok ? 'Done' : r.outcome || `exit ${r.exit}`;
      const why = unfinished ? r.failure_reason || '' : '';
      const cost = r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : r.output_tokens != null ? `${settingsRunTokens.format(r.output_tokens)} out` : '';
      return `<div class="run-row">
        <span class="run-bot">${empChip(r.employee)}</span>
        <span class="run-result">${live ? RUN_LIVE_PILL : `<span class="pill ${ok ? 'ok' : 'fail'}"${unfinished ? ` title="${esc(`Run ${r.id || ''}`.trim())}"` : ''}>${esc(result)}</span>${ok ? '' : runsReportHTML(r, true)}`}</span>
        <span class="run-model">${why ? `<span class="run-why" title="${esc(why)}">${esc(why)}</span>` : ''}<span class="mono" title="The model that did the work; a fallback changes it">${esc(ranOn(r.ran_on))}</span>${r.fallback
          ? `<span class="pill waiting" title="Ran on the fallback after the primary was unavailable">${esc(r.fallback)}</span>` : ''}${r.resumed ? '<span class="run-tag" title="Picked up where it left off">resumed</span>' : ''}</span>
        <span class="run-took tnum">${settingsRunTook(r.duration_s)}</span>
        <span class="run-cost tnum">${cost}</span>
        <span class="run-when tnum" title="${esc(fmt(r.finished_at))}">${esc(ago(r.finished_at))}</span>
        <a class="run-log" href="${API}/runs/${encodeURIComponent(r.id || '')}/log" target="_blank" rel="noopener">Log</a></div>`;
    }).join('')}</div>`;
}
async function renderSettingsRuns() {
  const el = $('#set-runs'); if (!el) return;
  const failed = SETTINGS_RUNS_FAILED;
  try { el.innerHTML = settingsRunsHTML(await get<HistoryRun[]>(failed ? '/runs?failed=true' : '/runs'), failed); } catch (e) { el.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; }
}

// ----------------------------------------------------------------- Changes
// One settings change as GET /v2/settings/history lists it (backend/settings_admin.py).
interface SettingsChange {
  id: string;
  bot: string;
  field: string;
  actor: string;
  via?: string | null;
  created_at: string;
  undone_at?: string | null;
  can_undo?: boolean;
  truncated?: boolean;
  transition_id?: string | null;
  before?: SettingsValue;
  after?: SettingsValue;
}
interface SettingsHistoryData {
  changes: SettingsChange[];
  transitions: SettingsTransition[];
  transitions_truncated?: boolean | undefined;
  next_before?: string | null | undefined;
}
interface SettingsHistoryPage {
  changes?: SettingsChange[];
  next_before?: string | null | undefined;
}
// The label of each field in a change's line (the Changes list reads it as "Bot · field").
const SETTINGS_FIELD_LABEL: Record<string, string> = {owners:'who it works for',access:'access',model:'model',placement:'computer',fallback:'fallback',
  compact_at:'compact at',wake_preset:'wakes',profile:'profile',
  turns_at_once:'Turns at once',definition:'setup',task_model:'task model',identity:'name and description',
  manages_reports:'manages its reports', lifecycle:'archive or restore'};
// A page of changes older than `before` (a page's next_before): the owner's list, or one bot's card with `bot`.
function settingsHistoryOlder(before: string, bot = ''): Promise<SettingsHistoryPage> {
  const scope = bot ? `bot=${encodeURIComponent(bot)}&` : '';
  return get<SettingsHistoryPage>(`/v2/settings/history?${scope}before=${encodeURIComponent(before)}`);
}
// "Show older" fetches the page after the last one drawn, with `older`, and draws all the rows together. A page still
// on its way is dropped when a later draw replaces this one, as a fresh first page after an undo does.
function renderSettingsHistory(el = $('#set-history'), history: SettingsHistoryData = SETTINGS_DATA.history, older: (before: string) => Promise<SettingsHistoryPage> = settingsHistoryOlder) {
  if (!el) return;
  const changes = history?.changes || [], transitions = history?.transitions || [];
  const pending = transitions.filter(t => ['preparing','blocked','failed','prepared'].includes(t.state));
  const transitionById = new Map<string | null | undefined, SettingsTransition>(transitions.map((t): [string, SettingsTransition] => [t.id, t]));
  const pendingRows = pending.map(t => `<div class="settings-history-row"><span>${avatar(t.bot, 24, stateOf(t.bot))}</span><div><strong>${esc(settingsBotName(t.bot))} · ${t.kind === 'model' ? 'model change' : 'computer move'}</strong><p>${esc(t.state === 'preparing' ? `Checkpointing ${t.progress?.prepared} of ${t.progress?.total} conversations` : t.error || t.state)}</p></div><div class="history-action"><span class="pill ${t.state === 'failed' ? 'fail' : 'waiting'}">${esc(t.state)}</span><button class="ghost" type="button" data-transition-review="${esc(t.id)}">Review</button></div></div>`).join('');
  const rows = changes.map(change => {
    const e = botRow(change.bot), transition = transitionById.get(change.transition_id);
    const field = SETTINGS_FIELD_LABEL[change.field] || change.field;
    // A change too big for a page comes without its values (backend/settings_admin.py `truncated`).
    const what = change.truncated ? 'Too big to show here.'
      : change.field === 'definition' ? settingsDefinitionChange(change.before, change.after)
      : `${settingsValue(change.field, change.before)} → ${settingsValue(change.field, change.after)}`;
    // A manager bot's change says so: it acted for the bot, as its manager (backend/report_managers.py).
    const by = change.via === 'report_manager' ? `${actorLabel(change.actor)}, as its manager` : actorLabel(change.actor);
    return `<div class="settings-history-row"><span>${avatar(change.bot, 24, stateOf(change.bot))}</span><div><strong>${esc(settingsBotName(change.bot))} · ${esc(field)}</strong>
      <p>${esc(what)}</p><p>${esc(by)} · ${esc(fmt(change.created_at))}${change.undone_at ? ` · undone ${esc(ago(change.undone_at))}` : ''}</p></div>
      <div class="history-action">${change.can_undo && e ? `<button class="ghost" type="button" data-settings-undo="${esc(change.id)}" data-bot="${esc(change.bot)}" data-revision="${e.revision}">Undo</button>` : ''}</div>
      ${settingsCheckpointDetails(transition)}</div>`;
  }).join('');
  const more = history?.next_before
    ? '<button class="ghost" type="button" data-settings-older>Show older</button>' : '';
  // The page leaves out the oldest transitions when they would not fit (backend/settings_admin.py history).
  const late = history?.transitions_truncated
    ? '<p class="empty">Some older in-progress changes are not listed here.</p>' : '';
  el.innerHTML = `${pendingRows || late ? `<h3>In progress</h3>${pendingRows}${late}` : ''}`
    + `${rows || '<div class="empty">No settings changes yet.</div>'}${more}`;
  const drawn = el._settingsDrawn = {};
  el.onclick = async (event: Event) => {
    const review = eventElement(event)?.closest<HTMLElement>('[data-transition-review]');
    if (review) { settingsWatchTransition(review.dataset.transitionReview!); return; }
    const showOlder = eventElement(event)?.closest<HTMLButtonElement>('[data-settings-older]');
    if (showOlder) {
      showOlder.disabled = true;
      try {
        const next = await older(history.next_before ?? '');
        if (el._settingsDrawn === drawn) renderSettingsHistory(el, {changes: [...changes, ...(next.changes || [])],
          transitions, transitions_truncated: history.transitions_truncated, next_before: next.next_before}, older);
      } catch (error) { showOlder.disabled = false; toast(errorMessage(error, ''), true); }
      return;
    }
    const undo = eventElement(event)?.closest<HTMLButtonElement>('[data-settings-undo]'); if (!undo) return;
    undo.disabled = true;
    try {
      const result: {id?: string} = await post(`/v2/settings/history/${encodeURIComponent(undo.dataset.settingsUndo ?? '')}/undo`, {expected_revision: Number(undo.dataset.revision)});
      if (result.id) settingsWatchTransition(result.id); else { await loadSettings(); if (el.id === 'bot-settings-history' && BOT) await botSettingsHistoryLoad(BOT.slug); toast('Settings change undone'); }
    } catch (error) {undo.disabled = false; toast(errorMessage(error, ''), true);}
  };
}
