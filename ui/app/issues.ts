/* ui/app/issues.ts — Shared fragments, Runs and Access tables
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- shared fragments
// Memoized per roster: task rows and sort comparators call this many times per render. Rebuilt when S.emps is
// replaced (every refresh), grows in place (bot-branches.js) or the assistant's name changes.
// A bot is named everywhere by its id (S.emps rows' `id`, the `bot:<id>` actor, routes and API paths); its slug is a
// label people type and read, and it can change. `botRow(id)` is the roster row for an id (a leading `bot:` is
// dropped); `botByRef(text)` also takes a slug, for what a person typed or an old link; `botSlug(id)` is the label.
/** A bot id as the page holds it: a roster id, a route segment or a link value, or nothing. */
type BotRef = string | null | undefined;
const botRow = (id: unknown): Employee | undefined => { const key = String(id || '').replace(/^bot:/, ''); return key ? (S.emps || []).find(e => e.id === key) : undefined; };
const botByRef = (ref: unknown): Employee | undefined => { const key = String(ref || '').replace(/^bot:/, ''); return key ? botRow(key) || (S.emps || []).find(e => e.slug === key) : undefined; };
const botSlug = (id: unknown): string => botRow(id)?.slug || String(id || '').replace(/^bot:/, '');
interface BotNameCache { emps: Employee[] | null; len: number; key: string; map: Map<BotRef, string>; }
let BOT_NAMES: BotNameCache = {emps: null, len: -1, key: '', map: new Map()};
const botDisplayName = (id: BotRef): string => {
  const key = assistantBot() + '\n' + assistantName();
  if (BOT_NAMES.emps !== S.emps || BOT_NAMES.len !== S.emps.length || BOT_NAMES.key !== key)
    BOT_NAMES = {emps: S.emps, len: S.emps.length, key, map: new Map()};
  const hit = BOT_NAMES.map.get(id);
  if (hit !== undefined) return hit;
  const row = botRow(id);
  const name = id === assistantBot() ? assistantName() : row?.display_name || row?.slug || id || '—';
  const out = S.emps.filter(e => e.status !== 'archived' && (e.display_name || e.slug || e.id).toLowerCase() === name.toLowerCase()).length > 1
    ? `${name} (${row?.slug || id})` : name;
  BOT_NAMES.map.set(id, out);
  return out;
};
const empName = (id: BotRef) => esc(botDisplayName(id));
const empChip = (id: BotRef) => id && botRow(id) ? `<a class="chip" href="#/bot/${encodeURIComponent(id)}">${avatar(id, 16)}<span>${empName(id)}</span></a>` : `<span class="chip">${empName(id)}</span>`;

// A bot's code lives in a repository named by the environment. The server sends repo_url when it
// knows the full address; otherwise a bare name or owner/name is resolved against github_owner, and
// an environment with no Git hosting simply shows the name.
const repoHostUrl = (repo: string): string => {
  if (/^https?:\/\//i.test(repo)) return repo;
  const owner = String(S.config.github_owner || '').trim();
  if (repo.includes('/')) return owner ? `https://github.com/${repo}` : '';
  return owner ? `https://github.com/${owner}/${repo}` : '';
};
function botRepoInfo(e: Employee | null | undefined) {
  const repo = String(e?.repo || '').trim();
  if (!repo) return null;
  const url = String(e?.repo_url || '').trim() || repoHostUrl(repo);
  return {url, label: repo.replace(/^https?:\/\/[^/]+\//i, '').replace(/\.git$/i, '')};
}
function botRepoHTML(e: Employee | null | undefined) {
  const info = botRepoInfo(e);
  if (!info) return '';
  return info.url
    ? `Repository <a href="${esc(info.url)}" target="_blank" rel="noopener">${esc(info.label)}</a>`
    : `Repository <span class="mono">${esc(info.label)}</span>`;
}



// The models that did a run's work, each with its effort (not the bot's configured one); empty when nothing was counted.
/** Which models did a run's work (run.ran_on): one segment per model stretch, and the task model's choice. */
interface RanOn {
  segments?: { model?: string | null; effort?: string | null }[];
  task_model?: { note?: string | null; source?: string | null } | null;
}
/** One run as the Runs and Access tables read it (GET runs, ui/app/task-runs.ts). */
interface RunRow {
  id?: string | null;
  employee?: string | null;
  state?: string;
  outcome?: string | null;
  exit?: number | null;
  failure_reason?: string | null;
  finished_at?: string | null;
  duration_s?: number | null;
  cost_usd?: number | null;
  output_tokens?: number | null;
  ran_on?: RanOn | null;
  fallback?: string | null;
  resumed?: boolean;
  didnt_start?: boolean;
}
const ranOn = (made: RanOn | null | undefined, effort = true): string => [...new Set(((made || {}).segments || []).filter(s => s.model)
  .map(s => s.model + (effort && s.effort ? ` (${s.effort})` : '')))].join(' then ');
// Why a task run was on that model: the bot's task model, the fallback from it, or a check kept (backend/task_models.py).
const ranOnNote = (made: RanOn | null | undefined): string => ((made || {}).task_model || {}).note || '';
// A run that failed or whose lease ran out (run.state, docs/status-and-state.md Runs): its reason shows with it.
const runUnfinished = (r: RunRow): boolean => typeof r.state === 'string' && ['failed', 'expired'].includes(r.state);
// A run still going (runs.LIVE: leased, running, input): GET /runs gives it no exit and no finished time yet.
const runLive = (r: RunRow): boolean => typeof r.state === 'string' && ['leased', 'running', 'input'].includes(r.state);
const RUN_LIVE_PILL = '<span class="pill in-progress">Running</span>';
// A run that ended before its computer started it (run.didnt_start): its reason names that computer.
const RUN_DIDNT_START = "Didn't start";
// A failed run offers Report this: Support opens with it attached (ui/support.ts).
function runsReportHTML(r: RunRow, icon = false) {
  if (!window.supportOn?.() || !window.reportThisHTML) return '';
  const why = r.failure_reason || r.outcome || `exit ${r.exit}`;
  return `<div>${window.reportThisHTML({kind: 'run', title: `${r.employee ? botDisplayName(r.employee) : 'Run'} failed: ${why}`, detail: why, ref: r.id || '',
    group: 'run:' + String(why).replace(/\d+/g, '#').slice(0, 80)}, icon)}</div>`;
}
function runsTable(list: RunRow[], showEmp = true) {
  if (!list.length) return `<div class="empty">No runs yet.</div>`;
  return `<div class="scroll"><table><tr>${showEmp ? '<th>Bot</th>' : ''}<th>Finished</th><th>Result</th><th>Duration</th><th>Cost</th><th>Model</th><th>Picked up</th><th></th></tr>
  ${list.map(r => `<tr>${showEmp ? `<td>${empChip(r.employee)}</td>` : ''}
    <td class="muted tnum">${ago(r.finished_at)}</td><td>${runLive(r) ? RUN_LIVE_PILL : runUnfinished(r) ? `<span class="pill fail">${r.didnt_start ? RUN_DIDNT_START : `${esc(r.outcome)} run`}</span><div class="mono muted">${esc(r.id)}</div>${r.failure_reason ? `<div class="muted">${esc(r.failure_reason)}</div>` : ''}${runsReportHTML(r)}` : r.exit === 0 ? '<span class="pill ok">Done</span>' : `<span class="pill fail">${esc(r.outcome || `exit ${r.exit}`)}</span>${runsReportHTML(r)}`}</td>
    <td class="tnum">${r.duration_s != null ? Math.round(r.duration_s/60) + 'm ' + (r.duration_s%60) + 's' : ''}</td>
    <td class="tnum">${r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : (r.output_tokens != null ? r.output_tokens.toLocaleString() + ' tokens out' : '')}</td>
    <td class="mono muted" title="${esc(ranOnNote(r.ran_on) || 'the model that actually ran it, which a fallback changes')}">${esc(ranOn(r.ran_on))}${(r.ran_on?.task_model || {}).source === 'fallback' ? ` <span class="pill waiting" title="${esc(ranOnNote(r.ran_on))}">task model off</span>` : ''}</td>
    <td>${r.fallback ? `<span class="pill waiting" title="ran on the fallback harness after the primary was unavailable">${esc(r.fallback)}</span>` : r.resumed ? 'Where it left off' : 'From the start'}</td><td><a href="${API}/runs/${encodeURIComponent(r.id || '')}/log" target="_blank" rel="noopener">Log</a></td></tr>`).join('')}</table></div>`;
}

// ----------------------------------------------------------------- access card
const SERVICE_NAMES: Record<string, string> = {gmail:'Gmail', 'google-calendar':'Google Calendar', 'google-workspace-admin':'Google Workspace admin', 'google-ads':'Google Ads', 'meta-ads':'Meta Ads', linkedin:'LinkedIn', x:'X', reddit:'Reddit', 'close-crm':'Close CRM', calendly:'Calendly', posthog:'PostHog', brex:'Brex', mercury:'Mercury', stripe:'Stripe', upfluence:'Upfluence', pangram:'Pangram', postiz:'Postiz', 'ein-presswire':'EIN Presswire', 'product-hunt':'Product Hunt', wellfound:'Wellfound', postjobfree:'PostJobFree', 'hugging-face':'Hugging Face', slack:'Slack', 'web-search':'Web search', cursor:'Cursor', 'marketing-email-sender':'Marketing email sender', elevenlabs:'ElevenLabs', gemini:'Gemini', heygen:'HeyGen', xai:'xAI'};
const SEND_VERBS = new Set(['send', 'post', 'publish', 'spend', 'write']);
function accessTable(e: Employee) {
  const acc: BotAccess[] = e.access || [];
  const mail = acc.filter(a => /gmail|email/.test(a.service));
  const emailLine = mail.length
    ? mail.map(a => `can ${a.can.includes('send') && e.outbound_send ? '<strong>send</strong>' : 'read and draft'} as <strong>${esc(a.identity || 'unknown address')}</strong>${a.can.includes('send') && !e.outbound_send ? ' <span class="muted">(send is declared but outbound is off)</span>' : ''}`).join('; ')
    : '<strong>none</strong>';
  const verb = (v: string, a: BotAccess) => `<span class="pill ${SEND_VERBS.has(v) ? (e.outbound_send ? 'ok' : 'waiting') : ''}" title="${SEND_VERBS.has(v) && !e.outbound_send ? 'gated: outbound_send is off' : ''}">${v}</span>`;
  const rows = acc.map(a => `<tr><td><strong>${esc(SERVICE_NAMES[a.service] || a.service)}</strong></td><td>${esc(a.identity || '—')}</td>
    <td class="tags">${(a.can || []).map(v => verb(v, a)).join(' ')}</td>
    <td>${a.connected ? '<span class="pill ok">connected</span>' : '<span class="pill">not connected</span>'}</td><td class="muted">${esc(a.note || '')}</td></tr>`).join('');
  return `<div class="acc-summary">
      <div><span class="k">Email</span> ${emailLine}</div>
      <div><span class="k">Outbound</span> ${e.outbound_send ? '<strong>on</strong>' : '<strong>off</strong>: drafts only'}</div>
      ${e.secrets_file ? '' : '<div><span class="muted">No credentials file.</span></div>'}
    </div>
    ${rows ? `<div class="scroll"><table><tr><th>Service</th><th>As</th><th>Can</th><th>Status</th><th></th></tr>${rows}</table></div>` : ''}`;
}
