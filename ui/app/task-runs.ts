/* ui/app/task-runs.ts — A task's Runs section: where the work left off (the checkpoint), what is still running in the
   background, and every run with what followed it and the subagents it started (GET /v2/tasks/{id}/run-view).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The run-view answer (backend/run_view.py), as this file reads it. Only the fields it reads are named.
type TrRanOn = import('../types/api').RanOn;
interface TrSubagent { id: string; parent_id?: string | null; type?: string | null; model?: string | null; running?: boolean; status?: string | null; duration_ms?: number | null; tool_uses?: number | null; background?: boolean; }
interface TrShell { tool: string; summary?: string | null; running?: boolean; started_at?: string | null; status?: string | null; }
interface TrDecision { by_kind?: string | null; by?: string | null; at?: string | null; decision: string; state?: string | null; note?: string | null; }
interface TrNext { kind: string; by?: string | null; at?: string | null; note?: string | null; }
// How the release policy let a job go (backend/wake_presets.py): warm or cold, a forked or a fresh session, and how long it waited.
interface TrRelease { warm: boolean; session: 'fork' | 'fresh'; waited_seconds: number; due?: string | null; wait_seconds?: number | null; deadline?: string | null; }
// preset_label is on runs from before profiles only; release only on a job the policy released.
interface TrWake { reason: string; profile_label?: string | null; preset_label?: string | null; held_until?: string | null; release?: TrRelease | null; }
interface TrAttempt {
  id: string; state: string; outcome?: string | null; reason?: string | null; try: number; tries: number; bot?: string | null;
  waiting?: boolean; paused?: boolean; moved?: boolean; took_s?: number | null; started_at?: string | null; created_at?: string | null;
  ran_on?: TrRanOn | null; decision?: TrDecision | null; next?: TrNext | null; lease?: Array<{ kind: string; at: string }> | null;
  wake?: TrWake | null; background?: TrShell[] | null; subagents?: TrSubagent[] | null;
  cache_read_tokens?: number | null; cache_write_tokens?: number | null;
}
interface TaskRunsView { runs?: TrAttempt[] | null; checkpoint?: { text: string; by?: string | null; at?: string | null } | null; }
// The task dialog, with the expandos this file keeps on it between loads.
type TrDialog = HTMLDialogElement & { taskOpening?: unknown; runsSeq?: number; runsTimer?: number };

const RUNS_POLL_MS = 15000;
const RUN_STATE_WORDS: Record<string, string> = {leased: 'starting', running: 'running', expired: 'lease expired', failed: 'failed',
  interrupted: 'stopped', completed: 'done', canceled: 'canceled', stopped: 'stopped', limited: 'hit a usage limit'};
const RUN_NEXT_WORDS: Record<string, string> = {resumed: 'Run again', dismissed: 'Not run again', retried: 'Run again', waiting: 'Waiting for a person',
  queued: 'Queued again'};

function runStateWord(a: TrAttempt) {
  if (a.waiting) return 'waiting';   // only while live (backend/run_view.py)
  if (a.paused) return 'paused';     // gave its shared login to a waiting turn; continues next
  if (a.state === 'leased' || a.state === 'running') return RUN_STATE_WORDS[a.state];
  const word = a.state === 'completed' ? (a.outcome || 'completed') : a.state;
  return RUN_STATE_WORDS[word] || String(word || '').replace(/_/g, ' ');
}
// The job's state beside its recovery decision; a saved choice still waiting for its lane is held (backend/run_view.py).
const RUN_JOB_WORDS: Record<string, string> = {uncertain: 'Held', canceled: 'Canceled', queued: 'Queued', leased: 'Running', running: 'Running',
  input: 'Running', completed: 'Done'};
// "Canceled: dismissed by BotOps at 09:50Z": the job's state and the resume or dismiss decided about this try, read
// from the decision record each time (job_recovery), so a later decision replaces it. The decision carries `state` only
// while it is about the job's current try; once a later try ran, it says only what was decided ("Resumed by BotOps
// at 09:50Z"). '' with no decision.
function runDecisionWords(a: TrAttempt): string {
  const d = a.decision;
  if (!d) return '';
  const who = d.by_kind === 'tico' ? "Tico's auto-reconcile" : actorLabel(d.by), at = new Date(d.at || '');
  const hhmm = isNaN(at.getTime()) ? '' : ` at ${String(at.getUTCHours()).padStart(2, '0')}:${String(at.getUTCMinutes()).padStart(2, '0')}Z`;
  const verb = d.decision === 'resume' ? 'resumed' : 'dismissed';
  const what = d.state ? `${RUN_JOB_WORDS[d.state] || String(d.state).replace(/^./, ch => ch.toUpperCase())}: ${verb}`
    : verb.replace(/^./, ch => ch.toUpperCase());
  return `${what} by ${who === 'You' ? 'you' : who}${hhmm}`;
}
const runByWord = (by: string | null | undefined): string => !by ? '' : by === 'keeper' ? 'automatically' : 'by ' + actorLabel(by);
const runMs = (ms: number | null | undefined): string => ms == null ? '' : durationWords(ms / 1000);

// The subagent tree: a subagent another subagent started sits under it (`parent_id` is the starting call's id).
function runAgentsHTML(agents: TrSubagent[], model: string | null | undefined): string {
  if (!agents.length) return '';
  const ids = new Set<string | null | undefined>(agents.map(a => a.id));
  const kids = (p: string | null) => agents.filter(a => (ids.has(a.parent_id) && a.parent_id !== a.id ? a.parent_id : null) === p);
  const seen = new Set<string>();
  const tree = (p: string | null): string => {
    const rows = kids(p).filter(a => !seen.has(a.id) && seen.add(a.id));
    return rows.length ? `<ul class="truns-tree">${rows.map(a => `<li><span class="truns-agent">${esc(a.type || 'agent')}</span>
      <span class="muted">${esc([a.model || (model ? model + ' (the run’s model)' : 'the run’s model'), a.running ? 'running' : a.status,
        runMs(a.duration_ms), a.tool_uses != null ? `${a.tool_uses} tool ${a.tool_uses === 1 ? 'call' : 'calls'}` : '',
        a.background ? 'in the background' : ''].filter(Boolean).join(' · '))}</span>${tree(a.id)}</li>`).join('')}</ul>` : '';
  };
  return `<div class="truns-sub"><div class="lbl">Subagents</div>${tree(null)}</div>`;
}
function runShellHTML(s: TrShell): string {
  return `<li><span class="mono">${esc(s.tool)}${s.summary ? ' · ' + esc(s.summary) : ''}</span> <span class="muted">${esc(s.running
    ? 'running · started ' + ago(s.started_at) : s.status || 'ended')}</span></li>`;
}
// A failed try offers Report this: Support opens with the run and its reason attached (ui/support.ts).
function runReportHTML(a: TrAttempt): string {
  if (!['failed', 'expired'].includes(a.state) || !window.supportOn?.() || !window.reportThisHTML) return '';
  return `<p class="truns-report">${window.reportThisHTML({kind: 'run', title: `${a.bot ? a.bot + ' run ' : 'Run '}${runStateWord(a)}${a.reason ? ': ' + a.reason : ''}`,
    detail: a.reason || '', ref: a.id || '', group: 'run:' + (a.state || '') + ':' + String(a.reason || '').replace(/\d+/g, '#').slice(0, 80)})}</p>`;
}
function runAttemptHTML(a: TrAttempt, open: boolean): string {
  const model = ranOn(a.ran_on);
  const took = a.took_s != null ? durationWords(a.took_s) : a.started_at ? 'for ' + ago(a.started_at).replace(/ ago$/, '') : '';
  const decided = runDecisionWords(a);
  const decision = a.decision;
  const head = [`Try ${a.try} of ${a.tries}`, runStateWord(a), decided, took, model].filter(Boolean).join(' · ');
  // The decision line says what `next` would for a resume or dismiss, with the note beside it.
  const nxt = decision ? `<p class="truns-next truns-decision" data-decision="${esc(decision.decision)}"><span class="truns-what">${esc(decided)}</span>${decision.note
    ? `<span class="truns-note">, ${esc(decision.note)}</span>` : ''}</p>` : a.next ? `<p class="truns-next"><span class="truns-what">${esc(RUN_NEXT_WORDS[a.next.kind] || a.next.kind)}</span> ${esc([runByWord(a.next.by),
    a.next.at ? ago(a.next.at) : ''].filter(Boolean).join(', '))}${a.next.note ? `<span class="truns-note">${esc(a.next.note)}</span>` : ''}</p>` : '';
  const lease = (a.lease || []).map(l => `<li class="muted">${esc(l.kind === 'grace' ? 'Lease lapsed; the runner was given time to report back'
    : 'Lease restored by the runner')} · ${esc(ago(l.at))}</li>`).join('');
  // What woke the turn, the bot's profile then (the preset on runs from before profiles), and until when it was held
  // (backend/wake_presets.py record).
  const woke = a.wake ? `<p class="muted truns-wake">Woke for: ${esc([a.wake.reason, a.wake.profile_label || a.wake.preset_label].filter(Boolean).join(' · '))}${a.wake.held_until
    ? ` · held until ${esc(fmt(a.wake.held_until))}` : ''}</p>${runReleaseHTML(a)}` : '';
  const background = a.background || [];
  const shells = background.length ? `<div class="truns-sub"><div class="lbl">Background</div><ul class="truns-list">${background.map(runShellHTML).join('')}</ul></div>` : '';
  return `<details class="truns-try${a.state === 'running' || a.state === 'leased' ? ' live' : ''}"${open ? ' open' : ''} data-run="${esc(a.id)}">
    <summary><span>${esc(head)}</span><span class="muted tnum" title="${esc(fmt(a.started_at || a.created_at))}">${esc(ago(a.started_at || a.created_at))}</span></summary>
    ${woke}${a.reason ? `<p class="truns-reason">${esc(a.reason)}</p>` : ''}${[a.waiting, a.paused, a.moved, ranOnNote(a.ran_on)].filter(Boolean).map(t =>
      `<p class="muted truns-moved">${esc(t)}</p>`).join('')}${runReportHTML(a)}${lease ? `<ul class="truns-list truns-lease">${lease}</ul>` : ''}${nxt}
    ${runAgentsHTML(a.subagents || [], ranOn(a.ran_on, false))}${shells}
    ${(a.subagents || []).length || shells || nxt || a.reason || a.waiting || a.paused || a.moved || ranOnNote(a.ran_on) ? '' : '<p class="muted truns-empty">No subagents or background work.</p>'}</details>`;
}
// "1.2M", "182k", "640": a run's token count on its record line.
const runTokens = (n: number): string => n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
// How the release policy let the job go, with the run's cache use: "warm · fresh · waited 3 h 10 min · cache read 182k".
function runReleaseHTML(a: TrAttempt): string {
  const r = a.wake?.release;
  if (!r) return '';
  const parts = [r.warm ? 'warm' : 'cold', r.session === 'fork' ? 'fork' : 'fresh', `waited ${callLength(r.waited_seconds)}`,
    a.cache_read_tokens != null ? `cache read ${runTokens(a.cache_read_tokens)}` : '', a.cache_write_tokens != null ? `cache write ${runTokens(a.cache_write_tokens)}` : ''];
  return `<p class="muted truns-release">${esc(parts.filter(Boolean).join(' · '))}</p>`;
}
function taskRunsHTML(v: TaskRunsView): string {
  const runs: TrAttempt[] = v.runs || [], cp = v.checkpoint;
  const live = runs.flatMap(a => (a.background || []).filter(s => s.running));
  if (!runs.length && !cp) return '';
  return `<h3 class="rail-h">Runs</h3>
    ${cp ? `<div class="truns-cp"><div class="lbl">Where it left off</div><div class="truns-cp-text">${esc(cp.text)}</div>
      <div class="muted">${esc(actorLabel(cp.by))} · <span title="${esc(fmt(cp.at))}">${esc(ago(cp.at))}</span></div></div>` : ''}
    ${live.length ? `<div class="truns-live"><div class="lbl">Still running in the background</div><ul class="truns-list">${live.map(runShellHTML).join('')}</ul></div>` : ''}
    ${runs.map((a, i) => runAttemptHTML(a, i === 0)).join('')}`;
}
// Loads once per open and again every RUNS_POLL_MS while a run is live; a closed or moved dialog stops it.
async function taskRunsLoad(d: TrDialog, id: string | number): Promise<void> {
  const opening = d.taskOpening, seq = d.runsSeq = (d.runsSeq || 0) + 1;
  clearTimeout(d.runsTimer);
  const view: TaskRunsView | null = await v2Get(`/v2/tasks/${encodeURIComponent(id)}/run-view`);
  if (!d.open || String(d.dataset.task) !== String(id) || d.taskOpening !== opening || d.runsSeq !== seq) return;
  const el: HTMLElement | null = $('[data-task-runs]', d);
  if (!el || !view) return;
  const html = taskRunsHTML(view);
  if (el.dataset.html !== html) {
    const opened = new Set([...el.querySelectorAll<HTMLDetailsElement>('details[data-run]')].map(x => (x.open ? '+' : '-') + x.dataset.run));
    el.innerHTML = el.dataset.html = html;
    // A person's own open/closed choice survives the refresh.
    el.querySelectorAll<HTMLDetailsElement>('details[data-run]').forEach(x => {
      if (opened.has('+' + x.dataset.run)) x.open = true; else if (opened.has('-' + x.dataset.run)) x.open = false;
    });
  }
  el.hidden = !html;
  if ((view.runs || []).some(a => a.state === 'running' || a.state === 'leased')) d.runsTimer = setTimeout(() => taskRunsLoad(d, id), RUNS_POLL_MS);
}
