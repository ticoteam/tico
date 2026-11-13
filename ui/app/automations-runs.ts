/* ui/app/automations-runs.ts — An automation's run log: one dense row per run, a tap opens its per-step lines
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/automations-runs.js, which the page runs in this file's place.
   GET /api/v2/automations/{id}/runs?limit=&before= (CONTRACT §2) pages newest first; More reads the next page. A run
   dropped by a trigger's filters is only counted on the server, so it never shows here. */
'use strict';

type AuRunRow = import('../types/api').AutomationRun;

/** A step of a run as the log reads it. The server sends a step as an object (AutomationRun.steps is Record<string, unknown>);
 *  each field the log reads is taken as its type, or as missing when the server sent another type (auStepView). */
interface AuStepView {
  id: unknown;
  type: string;
  status: string;
  cost: unknown;
  outputs: Record<string, unknown>;
  missed: number;  // a listening's items this step did not deliver: they went back to waiting
}

const AU_RUNS_PAGE = 25;
const AU_RUN_STATUS: Record<string, string> = {ok: 'Done', failed: 'Failed', skipped: 'Skipped', empty: 'Nothing found', waiting: 'Waiting', running: 'Running',
  queued: 'Queued', held: 'Held', partial: 'Partly delivered'};
const AU_STEP_STATUS: Record<string, string> = {skipped: 'skipped', failed: 'failed', spend_cap: 'spend cap', held: 'held'};
// A run's outcome in one to three words: what its last action did.
const AU_DONE: Record<string, string> = {archive: 'Archived', leave_for_you: 'Left for you', draft_reply: 'Draft reply', send_reply: 'Replied',
  create_task: 'Task created', label: 'Labeled', forward: 'Forwarded', wake_bot: 'Bot woken', note: 'Noted',
  post_update: 'Update posted', call_webhook: 'Webhook called', assign: 'Assigned', unsubscribe: 'Unsubscribed'};

function auIsRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
// One step as the log reads it: each field as its type, or as missing when the server sent another type.
function auStepView(raw: Record<string, unknown>): AuStepView {
  return {
    id: raw.id,
    type: typeof raw.type === 'string' ? raw.type : '',
    status: typeof raw.status === 'string' ? raw.status : '',
    cost: raw.cost,
    outputs: auIsRecord(raw.outputs) ? raw.outputs : {},
    missed: typeof raw.missed === 'number' ? raw.missed : 0,
  };
}
function auRunSteps(run: AuRunRow): AuStepView[] {
  return (run.steps || []).map(auStepView);
}
// The step with this id in an automation's flow (automations-flow.js auFind), or undefined.
function auFlowStep(flow: AuStep[], id: unknown): AuStep | undefined {
  const hit: AuFindHit | null = auFind(flow, id);
  return hit?.step;
}
// automations-flow.js's word tables, read by any key: a key the table does not have reads as undefined, as it always did.
function auStepWord(type: unknown): string | undefined {
  const words: Record<string, string | undefined> = AU_STEP_WORDS;
  return words[String(type)];
}
function auActionWord(name: unknown): string | undefined {
  const words: Record<string, string | undefined> = AU_ACTIONS;
  return words[String(name)];
}
// outputs.actions[].detail of the first action that failed, if the step has one.
function auFailedDetail(actions: unknown): unknown {
  if (!Array.isArray(actions)) return undefined;
  const list: unknown[] = actions;
  const hit: unknown = list.find(a => auIsRecord(a) && a.ok === false);
  return auIsRecord(hit) ? hit.detail : undefined;
}
// The first line of a text. split always gives one line or more; the ?? '' only satisfies the index type (noUncheckedIndexedAccess).
function auFirstLine(text: unknown): string {
  return String(text).split('\n')[0] ?? '';
}
// The number of items a step found: its list's length, or 0 when the server sent no list.
function auItemCount(items: unknown): number {
  return Array.isArray(items) ? items.length : 0;
}

async function auRunsLoad(state: AutomationsState, more = false): Promise<void> {
  const id = state.cur?.id;
  if (!id) return;
  let r = state.runs;
  if (!r) { r = {id, rows: [], next: null, open: new Set(), loading: false}; state.runs = r; }
  if (r.loading || (more && !r.next)) return;
  r.loading = true; r.error = '';
  if (automationsState() === state && state.view === 'runs') auDrawPane();
  try {
    const before = more && r.next ? '&before=' + encodeURIComponent(r.next) : '';
    const page = await get<import('../types/api').AutomationRuns>(`${auApi(id)}/runs?limit=${AU_RUNS_PAGE}${before}`);
    if (state.runs !== r) return;
    r.rows = more ? r.rows.concat(page.runs || []) : page.runs || [];
    r.next = page.next || null;
    if (!more) state.lastRun = r.rows[0] || null;
    if (state.openRun) { r.open.add(state.openRun); state.openRun = null; }
    // A listening's newest send opens with the list: its counts and what it sent are what the tab is for.
    else if (!more && state.lst && r.rows[0] && !r.open.size) r.open.add(r.rows[0].id);
  } catch (e) { r.error = errorMessage(e, ''); }
  r.loading = false;
  if (automationsState() === state && state.view === 'runs') auDrawPane();
}
function auRunToggle(id: string): void {
  const au = automationsState();
  if (!au) return;
  const r = au.runs;
  if (!r) return;
  if (r.open.has(id)) r.open.delete(id); else r.open.add(id);
  auDrawPane();
}

// Today's runs show the time; older ones the day too.
function auRunTime(at: string | null | undefined): string {
  const d = new Date(at ?? '');
  if (Number.isNaN(d.getTime())) return '';
  return d.toDateString() === new Date().toDateString() ? clockTime(at!)
    : d.toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
}
const auMoney = (n: unknown): string => {
  const v = Number(n) || 0;
  return v > 0 && v < 0.005 ? '<$0.01' : '$' + v.toFixed(2);
};
function auRunDot(run: AuRunRow): string {
  if (run.status === 'failed') return 'fail';
  if (run.status === 'held' || run.status === 'partial') return 'wait';
  if (auRunSteps(run).some(s => s.status === 'spend_cap' || s.status === 'failed')) return 'wait';
  return run.status === 'ok' ? 'ok' : 'idle';
}
// The outcome in a few words: the server's summary, else the last action that ran, else the run's status.
function auRunOutcome(run: AuRunRow): string {
  if (run.summary) return run.summary;
  const flow = automationsState()?.cur?.steps || [];
  const ran = auRunSteps(run).filter(s => s.status === 'ok' && s.type === 'action');
  const last = ran.at(-1), step = last ? auFlowStep(flow, last.id) : undefined;
  if (last) return AU_DONE[String(step?.do || last.outputs.do)] || 'Done';
  const bad = auRunSteps(run).find(s => s.status === 'failed' || s.status === 'spend_cap');
  if (bad) return bad.status === 'spend_cap' ? 'Spend cap' : `${auStepWord(bad.type) || 'Step'} failed`;
  return AU_RUN_STATUS[run.status] || run.status || '';
}
// One step of a run as a short line: what it decided, matched, wrote or did.
function auRunStepText(rs: AuStepView): string {
  const o = rs.outputs;
  // A step that is waiting (a queued mail action, a bot's turn) has already said what it did.
  if (rs.status === 'waiting') return [auRunStepDid({...rs, status: 'ok'}), 'waiting'].filter(Boolean).join(' · ');
  if (rs.status !== 'ok') {
    const reason = o.error || (rs.status === 'failed' ? auFailedDetail(o.actions) : '') || (o.delivered === 'before' ? 'delivered in an earlier run' : '');
    const missed = rs.missed ? ` · ${rs.missed} not delivered, back to waiting` : '';
    return (AU_STEP_STATUS[rs.status] || rs.status || '') + (reason ? ' · ' + auFirstLine(reason).slice(0, 80) : '') + missed;
  }
  return auRunStepDid(rs);
}
function auRunStepDid(rs: AuStepView): string {
  const o = rs.outputs, flow = automationsState()?.cur?.steps || [];
  const step = auFlowStep(flow, rs.id);
  if (typeof o.summary === 'string') return o.summary;
  if (rs.type === 'filter') {
    if (o.rule == null) return 'no match';
    const n = Number(o.rule);
    const rule = step?.rules?.[n];
    return rule ? `${auCondsText(rule.when, rule.match) || 'rule ' + (n + 1)} → ${auThenWords(rule.then).join(', ')}` : 'rule ' + (n + 1);
  }
  if (rs.type === 'decide') return Object.entries(o).filter(([, v]) => v && typeof v === 'object').map(([q, v]) => auAnswer(q, v as AuJudge).text).join(' · ');
  if (rs.type === 'llm') return [String(auActorName(step?.as)), o.text ? auFirstLine(o.text).slice(0, 80) : ''].filter(Boolean).join(' · ');
  if (rs.type === 'find') return `${o.count ?? auItemCount(o.items)} found`;
  if (rs.type === 'bot_turn') return String(auActorName(step?.bot)) + ' · turn started';
  if (rs.type === 'paths') {
    if (o.path == null) return '';
    const path = step?.paths?.[Number(o.path)];
    return '→ ' + String(path?.name || (o.path === 'otherwise' ? 'Otherwise' : o.path));
  }
  if (rs.type === 'action') return [auActionWord(step?.do) || '', o.result && typeof o.result === 'string' ? o.result : ''].filter(Boolean).join(' · ');
  return '';
}
function auRunsDraw(pane: HTMLElement): void {
  const au = automationsState();
  if (!au) return;
  const r = au.runs;
  if (!r) { pane.innerHTML = '<p class="muted au-pad">Loading…</p>'; return; }
  if (r.error && !r.rows.length) { pane.innerHTML = `<p class="err au-pad">${esc(r.error)}</p>`; return; }
  if (!r.rows.length) { pane.innerHTML = `<p class="muted au-pad">${r.loading ? 'Loading…' : 'No runs yet'}</p>`; return; }
  pane.innerHTML = `<table class="au-runs"><thead><tr><th>Time</th><th><span class="au-vh">Result</span></th><th>Outcome</th><th class="au-n">Cost</th></tr></thead><tbody>
    ${r.rows.map(run => {
      const open = r.open.has(run.id), dot = auRunDot(run);
      return `<tr class="au-rr${open ? ' open' : ''}" role="button" tabindex="0" aria-expanded="${open}" data-run="${esc(run.id)}">
        <td class="au-rt"><span class="nav-icon" aria-hidden="true">${open ? 'expand_more' : 'chevron_right'}</span>${esc(auRunTime(run.started_at))}</td>
        <td><span class="au-dot ${dot}" role="img" aria-label="${esc(AU_RUN_STATUS[run.status] || run.status)}"></span></td>
        <td class="au-ro">${esc(auRunOutcome(run))}</td><td class="au-n">${esc(auMoney(run.cost))}</td></tr>`
        + (open ? `<tr class="au-rx"><td colspan="4">${lstRunHTML(run)}${auRunSteps(run).map(s => `<div class="au-sl">${auTag(s.type)}<span>${esc(auRunStepText(s))}</span>
          ${s.cost ? `<span class="au-n muted">${esc(auMoney(s.cost))}</span>` : ''}</div>`).join('') || '<span class="muted">No steps</span>'}
          ${run.version != null && run.version !== au.cur?.version ? `<div class="au-sl muted">v${esc(run.version)}</div>` : ''}</td></tr>` : '');
    }).join('')}</tbody></table>
    ${r.next ? `<div class="au-more"><button class="ghost" type="button" data-runs-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : ''}
    ${r.error ? `<p class="err au-pad">${esc(r.error)}</p>` : ''}`;
}
