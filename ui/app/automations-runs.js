/* ui/app/automations-runs.js — An automation's run log: one dense row per run, a tap opens its per-step lines
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   GET /api/v2/automations/{id}/runs?limit=&before= (CONTRACT §2) pages newest first; More reads the next page. A run
   dropped by a trigger's filters is only counted on the server, so it never shows here. */
'use strict';

const AU_RUNS_PAGE = 25;
const AU_RUN_STATUS = {ok: 'Done', failed: 'Failed', skipped: 'Skipped', empty: 'Nothing found', waiting: 'Waiting', running: 'Running',
  queued: 'Queued', held: 'Held'};
const AU_STEP_STATUS = {skipped: 'skipped', failed: 'failed', spend_cap: 'spend cap', held: 'held'};
// A run's outcome in one to three words: what its last action did.
const AU_DONE = {archive: 'Archived', leave_for_you: 'Left for you', draft_reply: 'Draft reply', send_reply: 'Replied',
  create_task: 'Task created', label: 'Labeled', forward: 'Forwarded', wake_bot: 'Bot woken', note: 'Noted',
  post_update: 'Update posted', call_webhook: 'Webhook called', assign: 'Assigned'};

async function auRunsLoad(state, more = false) {
  const id = state.cur?.id;
  if (!id) return;
  const r = state.runs ||= {id, rows: [], next: null, open: new Set(), loading: false};
  if (r.loading || (more && !r.next)) return;
  r.loading = true; r.error = '';
  if (AU === state && state.view === 'runs') auDrawPane();
  try {
    const page = await get(`${auApi(id)}/runs?limit=${AU_RUNS_PAGE}${more ? '&before=' + encodeURIComponent(r.next) : ''}`);
    if (state.runs !== r) return;
    r.rows = more ? r.rows.concat(page.runs || []) : page.runs || [];
    r.next = page.next || null;
    if (!more) state.lastRun = r.rows[0] || null;
    if (state.openRun) { r.open.add(state.openRun); state.openRun = null; }
  } catch (e) { r.error = e.message; }
  r.loading = false;
  if (AU === state && state.view === 'runs') auDrawPane();
}
function auRunToggle(id) {
  const r = AU.runs;
  if (!r) return;
  if (r.open.has(id)) r.open.delete(id); else r.open.add(id);
  auDrawPane();
}

// Today's runs show the time; older ones the day too.
function auRunTime(at) {
  const d = new Date(at);
  if (isNaN(d)) return '';
  return d.toDateString() === new Date().toDateString() ? clockTime(at)
    : d.toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
}
const auMoney = n => (n = Number(n) || 0) > 0 && n < 0.005 ? '<$0.01' : '$' + n.toFixed(2);
function auRunDot(run) {
  if (run.status === 'failed') return 'fail';
  if (run.status === 'held') return 'wait';
  if ((run.steps || []).some(s => s.status === 'spend_cap' || s.status === 'failed')) return 'wait';
  return run.status === 'ok' ? 'ok' : 'idle';
}
// The outcome in a few words: the server's summary, else the last action that ran, else the run's status.
function auRunOutcome(run) {
  if (run.summary) return run.summary;
  const flow = AU.cur?.steps || [];
  const ran = (run.steps || []).filter(s => s.status === 'ok' && s.type === 'action');
  const last = ran.at(-1), step = last && auFind(flow, last.id)?.step;
  if (last) return AU_DONE[step?.do || last.outputs?.do] || 'Done';
  const bad = (run.steps || []).find(s => s.status === 'failed' || s.status === 'spend_cap');
  if (bad) return bad.status === 'spend_cap' ? 'Spend cap' : `${AU_STEP_WORDS[bad.type] || 'Step'} failed`;
  return AU_RUN_STATUS[run.status] || run.status || '';
}
// One step of a run as a short line: what it decided, matched, wrote or did.
function auRunStepText(rs) {
  const o = rs.outputs || {};
  // A step that is waiting (a queued mail action, a bot's turn) has already said what it did.
  if (rs.status === 'waiting') return [auRunStepDid({...rs, status: 'ok'}), 'waiting'].filter(Boolean).join(' · ');
  if (rs.status !== 'ok') return (AU_STEP_STATUS[rs.status] || rs.status || '') + (o.error ? ' · ' + o.error : '');
  return auRunStepDid(rs);
}
function auRunStepDid(rs) {
  const o = rs.outputs || {}, step = auFind(AU.cur?.steps || [], rs.id)?.step;
  if (typeof o.summary === 'string') return o.summary;
  if (rs.type === 'filter') {
    if (o.rule == null) return 'no match';
    const rule = step?.rules?.[o.rule];
    return rule ? `${auCondsText(rule.when, rule.match) || 'rule ' + (o.rule + 1)} → ${auThenWords(rule.then).join(', ')}` : 'rule ' + (Number(o.rule) + 1);
  }
  if (rs.type === 'decide') return Object.entries(o).filter(([, v]) => v && typeof v === 'object').map(([q, v]) => auAnswer(q, v).text).join(' · ');
  if (rs.type === 'llm') return [auActorName(step?.as), o.text ? String(o.text).split('\n')[0].slice(0, 80) : ''].filter(Boolean).join(' · ');
  if (rs.type === 'find') return `${o.count ?? (o.items || []).length} found`;
  if (rs.type === 'bot_turn') return auActorName(step?.bot) + ' · turn started';
  if (rs.type === 'paths') return o.path != null ? '→ ' + (step?.paths?.[o.path]?.name || (o.path === 'otherwise' ? 'Otherwise' : o.path)) : '';
  if (rs.type === 'action') return [AU_ACTIONS[step?.do] || '', o.result && typeof o.result === 'string' ? o.result : ''].filter(Boolean).join(' · ');
  return '';
}
function auRunsDraw(pane) {
  const r = AU.runs;
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
        + (open ? `<tr class="au-rx"><td colspan="4">${(run.steps || []).map(s => `<div class="au-sl">${auTag(s.type)}<span>${esc(auRunStepText(s))}</span>
          ${s.cost ? `<span class="au-n muted">${esc(auMoney(s.cost))}</span>` : ''}</div>`).join('') || '<span class="muted">No steps</span>'}
          ${run.version != null && run.version !== AU.cur?.version ? `<div class="au-sl muted">v${esc(run.version)}</div>` : ''}</td></tr>` : '');
    }).join('')}</tbody></table>
    ${r.next ? `<div class="au-more"><button class="ghost" type="button" data-runs-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : ''}
    ${r.error ? `<p class="err au-pad">${esc(r.error)}</p>` : ''}`;
}
