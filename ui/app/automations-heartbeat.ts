/* ui/app/automations-heartbeat.ts — The Heartbeat: the short beat that runs the steps which must happen within seconds
   (due times, recovery, outside polls), as a built-in row on the Automations list and its own screen
   (#/automations/heartbeat[/runs]): the seconds between beats, the last beat and the day's count, each step grouped by
   kind with its cadence, last run and result, day counts, time and last error (and its switch where it has one), and
   the log of beats that did something, each opening to its steps' lines.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Payloads: backend/heartbeat.py (GET/PUT /api/v2/heartbeat); a step's switch is PUT /api/v2/system-routines/{key}.
   Anyone signed in reads it; `can_change` (an owner or an admin) decides whether the controls work. No push: while the
   screen is open and the page visible it reads the first page again once a minute, and once on coming back. */
'use strict';

type HeartbeatView = import('../types/api').HeartbeatView;
type HeartbeatBeat = import('../types/api').HeartbeatBeat;
type HeartbeatStep = import('../types/api').HeartbeatStep;
type HeartbeatBeatStep = HeartbeatBeat['steps'][number];
type AutomationRow = AuRow;
// A list row the page builds: the list's own flags (builtin, readonly) are not in api.d.ts.
type ListRow = AutomationRow & {builtin: boolean; readonly: boolean; description: string};
type SystemRoutineSaved = import('../types/api').SystemRoutineSaved;

const AU_HEART = 'heartbeat';
const AU_HEART_INTERVALS = [5, 10, 15, 20, 30, 45, 60];
const AU_HEART_KINDS: Array<[string, string]> = [['due', 'Due times'], ['recovery', 'Recovery'], ['poll', 'Outside polls']];
const AU_HEART_POLL_MS = 60000;
const AU_HEART_PAGE = 20;
const auHeartOk = (h: HeartbeatView | null | undefined): h is HeartbeatView => !!h && Array.isArray(h.steps) && Number.isFinite(Number(h.interval_s));
const auHeartStep = (key: string): HeartbeatStep | undefined => (AU?.heart?.steps || []).find((s: HeartbeatStep) => s.key === key);
// The last beat failed when a loaded beat that failed is it (a folded repeat's last time counts); a quiet beat is not logged.
const auHeartLastFailed = (h: HeartbeatView): boolean => (h.beats || []).some((b: HeartbeatBeat) => Number(b.failed) > 0
  && [b.started_at, b.last_repeated_at].includes(h.last_beat_at));

// The list row: a read-only built-in of kind system, Tico's own, always on.
function auHeartRow(h: HeartbeatView): ListRow {
  return {id: AU_HEART, name: 'Heartbeat', kind: 'system', builtin: true, readonly: true, owner_id: AU_MAINT_OWNER,
    enabled: true, schedule: `Every ${h.interval_s} s`, trigger: {type: 'schedule'}, steps: [],
    description: h.steps.map(s => s.name).join(' '), can_edit: false, can_pause: false,
    ...(h.last_beat_at ? {last_run: {id: 'hb', started_at: h.last_beat_at, status: auHeartLastFailed(h) ? 'failed' : 'ok'}} : {})};
}
// The Heartbeat's row goes right after Maintenance (first when there is none); an older hub leaves the list as it is.
// Generic: a list comes back as the type it went in (automations.js passes one it has not typed).
function auHeartList<T extends AutomationRow[] | null | undefined>(list: T, h: HeartbeatView | null | undefined): T {
  if (!auHeartOk(h) || !Array.isArray(list)) return list;
  const rest = (list as AutomationRow[]).filter(a => a.id !== AU_HEART), at = rest.findIndex(a => a.id === AU_MAINT) + 1;
  return [...rest.slice(0, at), auHeartRow(h), ...rest.slice(at)] as T;
}
// A fresh listing: kept, its first page of beats merged into the log, the list row matched, and what is shown drawn.
function auHeartAdopt(state: AutomationsState, h: HeartbeatView): void {
  if (!auHeartOk(h) || AU !== state) return;
  state.heart = h;
  const log = state.hbLog;
  if (!log) state.hbLog = {rows: h.beats || [], next: h.next || null, open: new Set(), loading: false, error: ''};
  else {
    const fresh = new Map((h.beats || []).map((b): [string, HeartbeatBeat] => [b.id, b]));
    log.rows = [...(h.beats || []).filter(b => !log.rows.some(x => x.id === b.id)), ...log.rows.map(x => fresh.get(x.id) || x)];
    if (!log.rows.length) log.next = h.next || null;
  }
  if (state.list) state.list = auHeartList(state.list, h);
  if (state.id !== AU_HEART) { auDrawList(); return; }
  auHeartDrawHead(); auDrawPane();
}
// The header is not drawn again while its interval select is focused (or open): only its meta line changes.
function auHeartDrawHead(): void {
  const sel = document.activeElement?.closest?.('[data-hb-interval]');
  const meta = sel && document.querySelector('[data-hb-meta]');
  if (meta && auIs(AU, 'the Automations page') && AU.heart) meta.textContent = auHeartMeta(AU.heart);
  else auDrawHead();
}
async function auHeartLoad(state: AutomationsState): Promise<void> {
  const gen = state.hbGen;
  try {
    const h = await get<HeartbeatView>('/v2/heartbeat');
    if (AU !== state || state.hbGen !== gen) return;
    state.heartError = auHeartOk(h) ? '' : 'The Heartbeat needs a newer hub';
    if (auHeartOk(h)) auHeartAdopt(state, h);
  } catch (e) { if (AU === state && state.hbGen === gen) { const status = errorStatus(e); state.heartError = status !== undefined && [403, 404].includes(status) ? 'The Heartbeat needs a newer hub' : errorMessage(e, ''); } }
  if (AU === state && state.id === AU_HEART && !state.heart) auDrawPane();
}
// The safety poll: once a minute while the screen is open and the page visible; leaving it (or the page) ends it.
function auHeartPoll(state: AutomationsState): void {
  if (state.hbPoll !== null) clearTimeout(state.hbPoll);   // N: clearTimeout(null) is a no-op
  state.hbPoll = null;
  if (AU !== state || state.id !== AU_HEART) return;
  state.hbPoll = setTimeout(async () => {
    if (AU !== state || state.id !== AU_HEART) return;
    if (!document.hidden) await auHeartLoad(state);
    auHeartPoll(state);
  }, AU_HEART_POLL_MS);
}
// Back on the page: one read straight away, then the minute starts over.
document.addEventListener('visibilitychange', () => {
  const state = AU;
  if (document.hidden || !state || state.id !== AU_HEART) return;
  void auHeartLoad(state).then(() => auHeartPoll(state));
});

// -------------------------------------------------------------------------------------------- drawing
const auHeartMeta = (h: HeartbeatView): string => [h.last_beat_at ? 'Last beat ' + auRunTime(h.last_beat_at) : 'No beat yet',
  `${Number(h.beats_24h || 0).toLocaleString()} in 24 h`].join(' · ');
const auHeartMs = (ms: number | null | undefined): string => ms == null ? '' : Number(ms) < 1000 ? `${Number(ms)} ms` : `${(Number(ms) / 1000).toFixed(1)} s`;

function auHeartHead(hd: HTMLElement): void {
  auHave(AU);
  const h = AU.heart;
  if (!h) { hd.innerHTML = '<h2 class="au-name">Heartbeat</h2>'; return; }
  const off = !h.can_change || AU.hbBusy, cur = Number(h.interval_s);
  const opts = [...new Set([...AU_HEART_INTERVALS, cur])].sort((x, y) => x - y)
    .map(v => `<option value="${v}"${v === cur ? ' selected' : ''}>${v} s</option>`).join('');
  const meta = auHeartMeta(h);
  const seg = ['flow', 'runs'].map(v => `<a role="tab" href="${auHref(AU_HEART, v)}" aria-selected="${auPage().view === v}">${v === 'flow' ? 'Steps' : 'Log'}</a>`).join('');
  hd.innerHTML = `<h2 class="au-name">Heartbeat</h2>
    <span class="mt-set hb-every"><label class="mt-lb" for="hb-interval">Every</label>
      <select id="hb-interval" data-hb-interval${off ? ' disabled' : ''}>${opts}</select></span>
    <span class="mt-meta muted" data-hb-meta>${esc(meta)}</span>
    <span class="au-sp"></span>
    <div class="au-hd2">
      <span class="au-sp au-sp-phone"></span>
      <div class="au-seg ph-seg" role="tablist" aria-label="View">${seg}</div>
    </div>`;
}

function auHeartPane(pane: HTMLElement): void {
  auHave(AU);
  const h = AU.heart;
  if (!h) { pane.innerHTML = AU.heartError ? `<p class="err au-pad">${esc(AU.heartError)}</p>` : '<p class="muted au-pad">Loading…</p>'; return; }
  // A redraw keeps the place; Steps and Log each open at the top.
  const top = AU.hbPaneView === AU.view ? pane.scrollTop : 0;
  AU.hbPaneView = AU.view;
  pane.innerHTML = AU.view === 'runs' ? auHeartLogHTML() : auHeartStepsHTML(h);
  pane.scrollTop = top;
}

function auHeartStepsHTML(h: HeartbeatView): string {
  const kinds: Array<[string, string]> = Array.isArray(h.kinds) && h.kinds.length ? h.kinds.map((k): [string, string] => [k.key, k.name]) : AU_HEART_KINDS;
  const groups = kinds.map(([kind, title]) => {
    const steps = h.steps.filter(s => s.kind === kind);
    if (!steps.length) return '';
    return `<section class="hb-group" data-hb-kind="${esc(kind)}"><h3 class="mt-lb hb-kind">${esc(title)}</h3>
      <ol class="mt-steps">${steps.map(auHeartStepHTML).join('')}</ol></section>`;
  }).join('');
  return `<div class="mt hb">${groups}</div>`;
}

function auHeartStepHTML(s: HeartbeatStep): string {
  const on = s.on !== false, off = !s.can_change || auNeed(AU, 'the Automations page').hbBusy;
  const last = s.last_run_at ? `<span title="${esc(fmt(s.last_run_at))}">${esc(auRunTime(s.last_run_at))}</span>` : 'Not run yet';
  const result = s.last_result ? ' · ' + esc(s.last_result) : '';
  // The last error is red only while the latest run failed; after a good run it stays, muted, with its time.
  const error = s.last_error ? `<div class="${s.failing ? 'hb-err' : 'hb-err-old muted'}" data-hb-error>${esc(s.last_error)}${s.last_error_at
    ? ` <span class="muted" title="${esc(fmt(s.last_error_at))}">· ${esc(auRunTime(s.last_error_at))}</span>` : ''}</div>` : '';
  const did = Number(s.did_24h || 0), failed = Number(s.failed_24h || 0);
  return `<li class="mt-step${on ? '' : ' off'}" data-hb-step="${esc(s.key)}">
    <div class="mt-row">
      <div class="mt-main hb-main">
        <div class="mt-name"><b>${esc(s.name)}</b><span class="hb-every-p muted">${esc(s.every || '')}</span></div>
        <div class="mt-what muted">${last}${result}</div>
        ${error}
      </div>
      <span class="mt-when muted" data-hb-every>${esc(s.every || '')}</span>
      <span class="hb-cells">
        <span class="au-n" data-hb-did title="Did something in 24 h">${esc(did.toLocaleString())} did</span>
        <span class="au-n${failed ? ' hb-bad' : ' muted'}" data-hb-failed title="Failed in 24 h">${esc(failed.toLocaleString())} failed</span>
        <span class="au-n muted" data-hb-ms title="Last run time">${esc(auHeartMs(s.last_ms))}</span>
      </span>
      ${s.switch ? auMaintSw(on, s.name + ' on', `data-hb-on="${esc(s.key)}"`, off) : '<span class="hb-sw-none"></span>'}
    </div>
  </li>`;
}

function auHeartLogHTML(): string {
  auHave(AU);
  const r: AuRunList<HeartbeatBeat> | null = AU.hbLog;
  if (!r) return '<p class="muted au-pad">Loading…</p>';
  if (r.error && !r.rows.length) return `<p class="err au-pad">${esc(r.error)}</p>`;
  if (!r.rows.length) return '<p class="muted au-pad">No beat has done anything yet</p>';
  const name = (x: HeartbeatBeatStep): string => x.name || auHeartStep(x.key)?.name || auCap(String(x.key || '').replace(/_/g, ' '));
  const outcome = (b: HeartbeatBeat): string => (b.steps || []).map(x => x.error ? `${name(x)} failed` : `${name(x)}: ${x.text || 'Done'}`).join(' · ');
  // The same failure beat after beat is one row: how many times, and the last.
  const repeats = (b: HeartbeatBeat): string => Number(b.repeats) > 1 ? ` <span class="muted" data-hb-repeats title="${esc(fmt(b.last_repeated_at))}">×${esc(String(b.repeats))}, last ${esc(auRunTime(b.last_repeated_at))}</span>` : '';
  return `<table class="au-runs mt-runs hb-log"><thead><tr><th>Time</th><th><span class="au-vh">Result</span></th><th>Outcome</th><th class="au-n">Time taken</th></tr></thead><tbody>
    ${r.rows.map(b => {
      const open = r.open.has(b.id), bad = Number(b.failed) > 0;
      return `<tr class="au-rr${open ? ' open' : ''}" role="button" tabindex="0" aria-expanded="${open}" data-hb-beat="${esc(b.id)}">
        <td class="au-rt"><span class="nav-icon" aria-hidden="true">${open ? 'expand_more' : 'chevron_right'}</span><span title="${esc(fmt(b.started_at))}">${esc(auRunTime(b.started_at))}</span></td>
        <td><span class="au-dot ${bad ? 'fail' : 'ok'}" role="img" aria-label="${bad ? 'Failed' : 'Done'}"></span></td>
        <td class="au-ro">${esc(outcome(b))}${repeats(b)}</td>
        <td class="au-n muted">${esc(auHeartMs(b.total_ms))}</td></tr>`
        + (open ? `<tr class="au-rx"><td colspan="4">${(b.steps || []).map(x => `<div class="au-sl" data-hb-line="${esc(x.key)}">
            <span class="au-tag${x.error ? ' mt-bad' : ' acc'}">${x.error ? 'failed' : 'did'}</span>
            <span><b>${esc(name(x))}</b> · ${esc(x.error || x.text || 'Done')}</span>
            <span class="au-n muted">${esc(auHeartMs(x.ms))}</span></div>`).join('') || '<span class="muted">No steps</span>'}</td></tr>` : '');
    }).join('')}</tbody></table>
    ${r.next ? `<div class="au-more"><button class="ghost" type="button" data-hb-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : ''}
    ${r.error ? `<p class="err au-pad">${esc(r.error)}</p>` : ''}`;
}

// The log's next page: the same read, `before` the last page's `next`.
async function auHeartMore(state: AutomationsState): Promise<void> {
  const r = state.hbLog;
  if (!r || r.loading || !r.next) return;
  r.loading = true; r.error = '';
  auDrawPane();
  try {
    const page = await get<HeartbeatView>(`/v2/heartbeat?limit=${AU_HEART_PAGE}&before=${encodeURIComponent(r.next)}`);
    if (state.hbLog !== r) return;
    const have = new Set(r.rows.map(x => x.id));
    r.rows = r.rows.concat((page.beats || []).filter(x => !have.has(x.id)));
    r.next = page.next || null;
  } catch (e) { r.error = errorMessage(e, ''); }
  r.loading = false;
  if (AU === state && state.id === AU_HEART && state.view === 'runs') auDrawPane();
}

// -------------------------------------------------------------------------------------------- writes
// One write; a refusal says why and draws the saved values back.
async function auHeartWrite(work: (state: AutomationsState) => Promise<void>): Promise<void> {
  const state: AutomationsState | null = AU;
  if (!state) return;   // G: the page was left; the old code threw a TypeError on the null AU here
  if (state.hbBusy) return;
  state.hbBusy = true; state.hbGen++;
  auDrawHead(); auDrawPane();
  try {
    await work(state);
  } catch (e) { toast(errorMessage(e, ''), true); }
  state.hbBusy = false; state.hbGen++;   // a read that started during the write holds the old values
  if (AU === state && state.id === AU_HEART) { auDrawHead(); auDrawPane(); }
}
const auHeartInterval = (seconds: number) => auHeartWrite(async state => {
  const h: HeartbeatView = await put('/v2/heartbeat', {interval_s: seconds});
  state.hbBusy = false;
  auHeartAdopt(state, h);
});
// A step's switch is its job's (PUT /v2/system-routines/{key}); the answer is the step's row.
const auHeartSwitch = (key: string, on: boolean) => auHeartWrite(async state => {
  const out: SystemRoutineSaved = await put('/v2/system-routines/' + encodeURIComponent(key), {on});
  if (AU !== state || !state.heart || !out?.job) return;
  state.heart = {...state.heart, steps: state.heart.steps.map(s => s.key === key ? {...s, ...out.job} : s)};
});

// -------------------------------------------------------------------------------------------- events
function auHeartClick(t: Element): boolean {
  auHave(AU);
  if (AU.id !== AU_HEART) return false;
  if (t.closest('a[href]')) return false;   // the tabs are links
  const beat = t.closest<HTMLElement>('tr[data-hb-beat]');
  if (beat) {
    const r = AU.hbLog, id = String(beat.dataset.hbBeat);   // C: the selector guarantees the attribute
    if (r) { if (r.open.has(id)) r.open.delete(id); else r.open.add(id); auDrawPane(); }
    return true;
  }
  if (t.closest('[data-hb-more]')) { void auHeartMore(AU); return true; }
  return false;
}
function auHeartChange(e: Event): void {
  const t = eventElement(e);
  if (!AU || AU.id !== AU_HEART || !t?.closest?.('.au-hd,.au-pane')) return;
  if (AU.hbBusy) { auDrawHead(); auDrawPane(); return; }
  if (!(t instanceof HTMLInputElement || t instanceof HTMLSelectElement)) return;   // G: a change on another element threw on .dataset
  const d = t.dataset;
  if ('hbInterval' in d) void auHeartInterval(Number(t.value));
  else if (d.hbOn && t instanceof HTMLInputElement) void auHeartSwitch(d.hbOn, t.checked);   // N: data-hb-on is only on a switch input
}
