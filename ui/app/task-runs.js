/* ui/app/task-runs.js — A task's Runs section: where the work left off (the checkpoint), what is still running in the
   background, and every attempt with what followed it and the subagents it started (GET /v2/tasks/{id}/run-view).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RUNS_POLL_MS = 15000;
const RUN_STATE_WORDS = {leased: 'starting', running: 'running', expired: 'lease expired', failed: 'failed',
  interrupted: 'stopped', completed: 'done', cancelled: 'cancelled', stopped: 'stopped', limited: 'hit a usage limit'};
const RUN_NEXT_WORDS = {resumed: 'Run again', dismissed: 'Not run again', retried: 'Run again', waiting: 'Waiting for a person',
  queued: 'Queued again'};

function runStateWord(a) {
  if (a.waiting) return 'waiting';   // only while live (backend/run_view.py)
  if (a.state === 'leased' || a.state === 'running') return RUN_STATE_WORDS[a.state];
  const word = a.state === 'completed' ? (a.outcome || 'completed') : a.state;
  return RUN_STATE_WORDS[word] || String(word || '').replace(/_/g, ' ');
}
const runByWord = by => !by ? '' : by === 'keeper' ? 'automatically' : 'by ' + actorLabel(by);
const runMs = ms => ms == null ? '' : durationWords(ms / 1000);

// The subagent tree: a subagent another subagent started sits under it (`parent_id` is the starting call's id).
function runAgentsHTML(agents, model) {
  if (!agents.length) return '';
  const ids = new Set(agents.map(a => a.id));
  const kids = p => agents.filter(a => (ids.has(a.parent_id) && a.parent_id !== a.id ? a.parent_id : null) === p);
  const seen = new Set();
  const tree = p => {
    const rows = kids(p).filter(a => !seen.has(a.id) && seen.add(a.id));
    return rows.length ? `<ul class="truns-tree">${rows.map(a => `<li><span class="truns-agent">${esc(a.type || 'agent')}</span>
      <span class="muted">${esc([a.model || (model ? model + ' (the run’s model)' : 'the run’s model'), a.running ? 'running' : a.status,
        runMs(a.duration_ms), a.tool_uses != null ? `${a.tool_uses} tool ${a.tool_uses === 1 ? 'call' : 'calls'}` : '',
        a.background ? 'in the background' : ''].filter(Boolean).join(' · '))}</span>${tree(a.id)}</li>`).join('')}</ul>` : '';
  };
  return `<div class="truns-sub"><div class="lbl">Subagents</div>${tree(null)}</div>`;
}
function runShellHTML(s) {
  return `<li><span class="mono">${esc(s.tool)}${s.summary ? ' · ' + esc(s.summary) : ''}</span> <span class="muted">${esc(s.running
    ? 'running · started ' + ago(s.started) : s.status || 'ended')}</span></li>`;
}
// A failed try offers Report this: Support opens with the run and its reason attached (ui/support.js).
function runReportHTML(a) {
  if (!['failed', 'expired'].includes(a.state) || !window.supportOn?.() || !window.reportThisHTML) return '';
  return `<p class="truns-report">${window.reportThisHTML({kind: 'run', title: `${a.bot ? a.bot + ' run ' : 'Run '}${runStateWord(a)}${a.reason ? ': ' + a.reason : ''}`,
    detail: a.reason || '', ref: a.run || '', group: 'run:' + (a.state || '') + ':' + String(a.reason || '').replace(/\d+/g, '#').slice(0, 80)})}</p>`;
}
function runAttemptHTML(a, open) {
  const model = ranOn(a.ran_on);
  const took = a.took_s != null ? durationWords(a.took_s) : a.started ? 'for ' + ago(a.started).replace(/ ago$/, '') : '';
  const head = [`Try ${a.try} of ${a.tries}`, runStateWord(a), took, model].filter(Boolean).join(' · ');
  const nxt = a.next ? `<p class="truns-next"><span class="truns-what">${esc(RUN_NEXT_WORDS[a.next.kind] || a.next.kind)}</span> ${esc([runByWord(a.next.by),
    a.next.at ? ago(a.next.at) : ''].filter(Boolean).join(', '))}${a.next.note ? `<span class="truns-note">${esc(a.next.note)}</span>` : ''}</p>` : '';
  const lease = (a.lease || []).map(l => `<li class="muted">${esc(l.kind === 'grace' ? 'Lease lapsed; the runner was given time to report back'
    : 'Lease restored by the runner')} · ${esc(ago(l.at))}</li>`).join('');
  // What woke the turn, under which wake preset, and until when a preset held it (backend/wake_presets.py record).
  const woke = a.wake ? `<p class="muted truns-wake">Woke for: ${esc(a.wake.reason)} · ${esc(a.wake.preset_label)}${a.wake.held_until
    ? ` · held until ${esc(fmt(a.wake.held_until))}` : ''}</p>` : '';
  const shells = (a.background || []).length ? `<div class="truns-sub"><div class="lbl">Background</div><ul class="truns-list">${a.background.map(runShellHTML).join('')}</ul></div>` : '';
  return `<details class="truns-try${a.state === 'running' || a.state === 'leased' ? ' live' : ''}"${open ? ' open' : ''} data-run="${esc(a.run)}">
    <summary><span>${esc(head)}</span><span class="muted tnum" title="${esc(fmt(a.started || a.created))}">${esc(ago(a.started || a.created))}</span></summary>
    ${woke}${a.reason ? `<p class="truns-reason">${esc(a.reason)}</p>` : ''}${[a.waiting, a.moved].filter(Boolean).map(t =>
      `<p class="muted truns-moved">${esc(t)}</p>`).join('')}${runReportHTML(a)}${lease ? `<ul class="truns-list truns-lease">${lease}</ul>` : ''}${nxt}
    ${runAgentsHTML(a.subagents || [], model)}${shells}
    ${(a.subagents || []).length || shells || nxt || a.reason || a.waiting || a.moved ? '' : '<p class="muted truns-empty">No subagents or background work.</p>'}</details>`;
}
function taskRunsHTML(v) {
  const attempts = v.attempts || [], cp = v.checkpoint;
  const live = attempts.flatMap(a => (a.background || []).filter(s => s.running));
  if (!attempts.length && !cp) return '';
  return `<h3 class="rail-h">Runs</h3>
    ${cp ? `<div class="truns-cp"><div class="lbl">Where it left off</div><div class="truns-cp-text">${esc(cp.text)}</div>
      <div class="muted">${esc(actorLabel(cp.by))} · <span title="${esc(fmt(cp.at))}">${esc(ago(cp.at))}</span></div></div>` : ''}
    ${live.length ? `<div class="truns-live"><div class="lbl">Still running in the background</div><ul class="truns-list">${live.map(runShellHTML).join('')}</ul></div>` : ''}
    ${attempts.map((a, i) => runAttemptHTML(a, i === 0)).join('')}`;
}
// Loads once per open and again every RUNS_POLL_MS while a run is live; a closed or moved dialog stops it.
async function taskRunsLoad(d, id) {
  const opening = d.taskOpening, seq = d.runsSeq = (d.runsSeq || 0) + 1;
  clearTimeout(d.runsTimer);
  const view = await v2Get(`/v2/tasks/${encodeURIComponent(id)}/run-view`);
  if (!d.open || String(d.dataset.task) !== String(id) || d.taskOpening !== opening || d.runsSeq !== seq) return;
  const el = $('[data-task-runs]', d);
  if (!el || !view) return;
  const html = taskRunsHTML(view);
  if (el.dataset.html !== html) {
    const opened = new Set([...el.querySelectorAll('details[data-run]')].map(x => (x.open ? '+' : '-') + x.dataset.run));
    el.innerHTML = el.dataset.html = html;
    // A person's own open/closed choice survives the refresh.
    el.querySelectorAll('details[data-run]').forEach(x => {
      if (opened.has('+' + x.dataset.run)) x.open = true; else if (opened.has('-' + x.dataset.run)) x.open = false;
    });
  }
  el.hidden = !html;
  if ((view.attempts || []).some(a => a.state === 'running' || a.state === 'leased')) d.runsTimer = setTimeout(() => taskRunsLoad(d, id), RUNS_POLL_MS);
}
