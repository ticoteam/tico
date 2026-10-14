/* Settings > Health: what needs attention right now (backend/health.py). Everything shown is what
   GET /api/v2/health computed; the only local state is which page is open. The attention dot sits on
   the way into Settings, never in the main navigation. */
'use strict';
let HL = null;
let HL_LOAD = 0;
let HL_METRICS = null;   // owners and admins: the last hour from the flight recorder (GET /api/v2/system/metrics)
let HL_MONITOR = null;   // the owner: the Health Monitor's switch and what it found (GET /api/v2/health-monitor)

async function hlRefresh() {
  if (!S.me?.cloud) return null;
  const seq = ++HL_LOAD;
  const data = await v2Get('/v2/health');
  if (seq !== HL_LOAD) return HL;
  HL = data && Array.isArray(data.checks) ? data : null;
  if (HL && HL.audience !== 'human') {
    const metrics = await v2Get('/v2/system/metrics?minutes=60');
    if (seq !== HL_LOAD) return HL;
    HL_METRICS = metrics && metrics.requests ? metrics : null;
  } else HL_METRICS = null;
  if (HL?.audience === 'owner') {
    const monitor = await v2Get('/v2/health-monitor');
    if (seq !== HL_LOAD) return HL;
    HL_MONITOR = monitor && 'on' in monitor ? monitor : null;
  } else HL_MONITOR = null;
  // The sidebar's "New version" reads the same answer, so it never lags the page beside it.
  if (HL?.update && S.config) { S.config = {...S.config, update: HL.update}; window.renderNewVersion?.(); }
  hlNav();
  hlPageDraw();
  window.overviewStuckDraw?.();
  return HL;
}

// The dot follows the way in: the account button, the Settings entry and the Health tab.
window.hlNav = function hlNav() {
  const attention = HL?.attention || 0;
  document.querySelectorAll('[data-hl-alert]').forEach(dot => { dot.hidden = !attention; });
  const label = attention ? `Settings, ${attention} to look at in Health` : '';
  const setting = document.querySelector('#account-menu [data-nav="settings"]');
  if (setting) { if (label) setting.setAttribute('aria-label', label); else setting.removeAttribute('aria-label'); }
  const tab = document.querySelector('[data-settings-tab="health"]');
  if (tab) { if (attention) tab.setAttribute('aria-label', `Health, ${attention} to look at`); else tab.removeAttribute('aria-label'); }
};

const HL_ICONS = {ok: 'check_circle', warn: 'warning', bad: 'error', info: 'radio_button_unchecked', unknown: 'help'};
let HL_CHECKING = false;
let HL_CHECK_MSG = null;   // {text, error}: what the last "Check for updates" said, kept across redraws

function hlFixHtml(fix) {
  if (fix.login) return `<button class="ghost" type="button" data-model-login data-runner="${esc(fix.login.runner_id)}" data-runtime="${esc(fix.login.runtime)}" data-machine="${esc(fix.login.computer)}">${esc(fix.label)}</button>`;
  if (fix.move) return `<button class="ghost" type="button" data-stuck-move="${esc(fix.move.bot)}" data-runner="${esc(fix.move.runner_id)}" data-machine="${esc(fix.move.computer)}">${esc(fix.label)}</button>`;
  if (fix.finish) return `<button class="ghost" type="button" data-stuck-finish="${esc(fix.finish.bot)}">${esc(fix.label)}</button>`;
  if (fix.click) return `<button class="ghost" type="button" data-hl-click="${esc(fix.click)}">${esc(fix.label)}</button>`;
  return `<a class="ghost gs-link" href="${esc(fix.href)}"${fix.tab ? ` data-gs-tab="${esc(fix.tab)}"` : ''}>${esc(fix.label)}</a>`;
}

function hlCheckHtml(check) {
  const owner = check.id === 'version' && HL?.audience === 'owner';
  const checkButton = owner
    ? `<button class="ghost" type="button" data-hl-check-updates${HL_CHECKING ? ' disabled' : ''}>${HL_CHECKING ? 'Checking…' : 'Check for updates'}</button>` : '';
  const result = owner && HL_CHECK_MSG
    ? `<p class="hl-check-result${HL_CHECK_MSG.error ? ' err' : ''}" role="status">${esc(HL_CHECK_MSG.text)}</p>` : '';
  return `<li class="hl-check ${esc(check.status)}" data-hl-check="${esc(check.id)}" data-status="${esc(check.status)}">
    <span class="nav-icon hl-icon" aria-hidden="true">${HL_ICONS[check.status] || HL_ICONS.unknown}</span>
    <div class="hl-main"><strong>${esc(check.label)}</strong><p>${esc(check.summary)}</p>${result}</div>
    <div class="hl-fixes">${check.fixes.map(hlFixHtml).join('')}${checkButton}</div></li>`;
}

function hlCredentialFix(source) {
  if (source === 'credentials') return 'Replace the model key in <a href="#/credentials">Credentials</a>';
  if (source === 'computer') return 'Update the computer-local key or sign in again';
  return 'If the key is stored in <a href="#/credentials">Credentials</a>, replace it there; otherwise update the computer-local key or sign in again';
}

function hlComputerHtml(machine) {
  const models = machine.runtimes.map(row => {
    const state = row.rejected ? `sign-in rejected${row.rejected_at ? ' ' + esc(ago(row.rejected_at)) : ''}${row.rejected_reason ? ' (' + esc(row.rejected_reason) + ')' : ''}. ${hlCredentialFix(row.credential_source)}`
      : row.ready ? 'signed in' : row.installed ? 'not signed in' : 'not installed';
    const login = row.signable
      ? ` <button class="ghost" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(row.name)}" data-machine="${esc(machine.label)}">Sign in</button>` : '';
    // Red only when the team or an assigned bot needs it; the server sends only relevant harnesses.
    const bad = row.rejected || (!row.installed && row.needed !== false);
    return `<li data-ready="${row.ready}"${bad ? ' class="hl-model-bad"' : ''}>${esc(harnessWords(row.name))}: ${state}${login}</li>`;
  }).join('');
  return `<li class="hl-computer" data-online="${machine.online}"><span class="hl-dot ${machine.online ? 'on' : 'off'}" aria-hidden="true"></span>
    <div><strong>${esc(machine.label)}</strong> <span class="muted">${machine.online ? 'online' : 'offline'}${machine.last_seen ? ', last seen ' + esc(ago(machine.last_seen)) : ', never seen'}</span>
    ${window.runnerUpdateHtml?.(machine.update, machine.version) || ''}
    <ul class="hl-models">${models || '<li class="muted">No models reported</li>'}</ul></div></li>`;
}

// Owners only: where files live, how much, and the copy to S3 while it runs (`storage` on GET /api/v2/health).
function hlStorageHtml(st) {
  if (!st || HL?.audience !== 'owner') return '';
  const s3 = st.mode === 's3', copy = st.copy || {};
  const total = Number(copy.total) || 0, done = Number(copy.done) || 0, failed = Number(copy.failed) || 0;
  const files = Number(st.files) || 0;
  const where = s3 ? `S3${st.bucket ? ' · ' + st.bucket : ''}` : 'Local disk';
  const copying = total > 0 && done + failed < total;
  return `<div class="hl-storage" data-hl-storage="${s3 ? 's3' : 'local'}">
    <span class="nav-icon hl-icon" aria-hidden="true">inventory_2</span><div class="hl-storage-main"><strong>Storage</strong>
    <span class="hl-where" title="${esc(st.region ? where + ' · ' + st.region : where)}">${esc(where)}</span>
    <span class="muted tnum">${files.toLocaleString()} file${files === 1 ? '' : 's'} · ${esc(bytes(Number(st.bytes) || 0))}</span>
    ${copying ? `<span class="hl-copy tnum" data-hl-copy>Copying to S3 · ${done.toLocaleString()} of ${total.toLocaleString()}</span>` : ''}
    ${failed ? `<span class="err tnum" data-hl-copy-failed>${failed.toLocaleString()} failed</span>` : ''}
    ${s3 ? '' : `<a class="hl-s3" href="${GH}/blob/main/docs/files.md#storage" target="_blank" rel="noopener noreferrer">Set up S3</a>`}</div></div>`;
}

// The owner: the optional Health Monitor (backend/health_monitor.py). One switch turns it on or off; the second is the team
// rule that lets it file Tico bug reports without a Needs you card.
function hlMonitorHtml(m) {
  if (!m) return '';
  const found = (m.findings || []).slice(0, 8).map(f => `<li data-hl-finding="${esc(f.key)}"><strong>${esc(f.title)}</strong>
    <span class="muted">${f.kind === 'setup' ? 'BotOps fixes it' : f.ticket_id ? 'reported to Tico' : f.approval_id ? 'report waiting in Needs you' : 'Tico bug'} · since ${esc(ago(f.first_seen))}</span></li>`).join('');
  return `<section class="hl-monitor" data-hl-monitor="${m.on ? 'on' : 'off'}">
    <label class="hl-monitor-row"><input type="checkbox" role="switch" data-hl-monitor-switch${m.on ? ' checked' : ''}>
      <span><strong>Health Monitor</strong> <span class="muted">checks this install every hour, has BotOps fix setup problems and reports Tico bugs to the Tico team. It wakes a model only when it finds something new.</span></span></label>
    <label class="hl-monitor-row"><input type="checkbox" role="switch" data-hl-monitor-rule${m.reports_without_asking ? ' checked' : ''}${m.on ? '' : ' disabled'}>
      <span>Health Monitor may file Tico bug reports without asking <span class="muted">(only the finding and the redacted diagnostics, never task or message text)</span></span></label>
    ${m.on && found ? `<ul class="hl-bots">${found}</ul>` : ''}</section>`;
}

async function hlMonitorToggle(box) {
  box.disabled = true;
  try {
    if (box.matches('[data-hl-monitor-rule]')) await put('/v2/access/rules', {health_monitor_reports: box.checked});
    else await post(`/v2/health-monitor/${box.checked ? 'turn-on' : 'turn-off'}`, {});
    toast(box.matches('[data-hl-monitor-rule]') ? 'Saved' : box.checked ? 'The Health Monitor is on' : 'The Health Monitor is off');
  } catch (e) { toast(e.message, true); }
  await hlRefresh();
}

// Bots with queued work that is not starting, or left in setup for a day (backend/health.py `_stuck`): who, why, and
// one fix. Overview shows the same rows.
window.hlStuckHtml = function hlStuckHtml(rows) {
  return (rows || []).map(row => `<li class="hl-stuck" data-stuck="${esc(row.bot)}" data-reason="${esc(row.reason)}">
    <a href="#/bot/${esc(row.bot)}">${esc(row.name)}</a> <span class="muted">${esc(row.why)}${row.reason === 'slow' && row.oldest ? ' · ' + esc(ago(row.oldest)) : ''}${row.queued ? ` · ${row.queued} queued` : ''}</span>
    ${row.fix ? hlFixHtml(row.fix) : ''}</li>`).join('');
};

async function hlStuckMove(button) {
  const e = (S.emps || []).find(row => row.name === button.dataset.stuckMove);
  if (!e) { toast('Open Settings > Bots to move this bot', true); return; }
  if (!await settingsConfirmTransition(e, 'machine', button.dataset.machine)) return;
  await settingsBeginTransition(button, e, {kind: 'machine', runner_id: button.dataset.runner,
    expected_generation: e.machine?.generation || 0, expected_revision: e.revision});
  void hlRefresh();
}

// A bot stuck in setup: the same setup-done call as its page's "Mark setup done" (POST /v2/bots/{bot}/onboarded).
async function hlStuckFinish(button) {
  const slug = button.dataset.stuckFinish;
  button.disabled = true; button.textContent = 'Finishing…';
  try {
    const result = await post(`/v2/bots/${encodeURIComponent(slug)}/onboarded`, {});
    const bot = (S.emps || []).find(e => e.name === slug);
    if (bot) bot.onboarding_state = result.onboarding_state;
    toast(`${botDisplayName(slug)} finished setup`);
    void hlRefresh();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false; button.textContent = 'Finish with defaults';
  }
}

function hlPageDraw() {
  const host = $('#hl-page');
  if (!host) return;
  if (!HL) { host.innerHTML = '<div class="empty">Nothing to show yet.</div>'; return; }
  const bots = list => list.map(row => `<li><a href="#/bot/${esc(row.bot)}">${esc(row.name)}</a> <span class="muted">${row.reason === 'no_computer' ? 'no computer is online' : row.reason === 'computer_offline' ? esc(row.computer) + ' is offline' : 'waiting since ' + esc(ago(row.oldest))}${row.queued ? ', ' + row.queued + ' waiting' : ''}</span></li>`).join('');
  // A stuck bot is listed once, with its reason; "waiting" keeps only the offline ones with nothing queued.
  const stuck = HL.stuck || [], idle = (HL.waiting || []).filter(row => !stuck.some(s => s.bot === row.bot));
  // Settings' own checks that need nobody (ui/app/settings.js shows only the ones that do).
  const notes = (typeof SETTINGS_DATA !== 'undefined' && SETTINGS_DATA.issues || []).filter(issue => !needsPerson(issue));
  host.innerHTML = `<p class="muted" id="hl-summary">${HL.attention ? `${HL.attention} issue${HL.attention === 1 ? '' : 's'} to look at`
    : notes.length ? `Nothing urgent. ${notes.length} note${notes.length === 1 ? '' : 's'} below.` : 'Everything looks fine.'}</p>
    <ul class="hl-list">${HL.checks.map(hlCheckHtml).join('')}</ul>${hlMonitorHtml(HL_MONITOR)}
    ${stuck.length ? `<h2>Stuck</h2><ul class="hl-bots">${hlStuckHtml(stuck)}</ul>` : ''}${hlStorageHtml(HL.storage)}
    ${notes.length ? `<h2>Not urgent</h2><ul class="hl-notes">${notes.map(issue => `<li><strong>${esc(issue.title)}</strong> <span class="muted">${esc(issue.detail || '')}</span></li>`).join('')}</ul>` : ''}
    ${hlMetricsHtml(HL_METRICS)}
    ${HL.computers.length ? `<h2>Computers</h2><ul class="hl-computers">${HL.computers.map(hlComputerHtml).join('')}</ul>` : ''}
    ${idle.length ? `<h2>Bots waiting</h2><ul class="hl-bots">${bots(idle)}</ul>` : ''}
    ${HL.failures.length ? `<h2>Failed in the last day</h2><ul class="hl-bots">${HL.failures.map(row => `<li>${esc(botDisplayName(row.bot))} <span class="muted">${esc(ago(row.at))}</span></li>`).join('')}</ul>` : ''}`;
}

const hlMs = ms => ms == null ? '–' : ms >= 60000 ? `${(ms / 60000).toFixed(1)} min` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
const hlPct = n => n == null ? '–' : `${Math.round(n)}%`;
const hlAt = ts => ago(new Date(ts * 1000).toISOString());

// The flight recorder's last hour (backend/flight.py): one line of numbers, then where the time went.
function hlMetricsHtml(m) {
  if (!m) return '';
  const req = m.requests || {}, proc = m.process || {}, db = m.db || {};
  const stalls = (m.events?.recent || []).filter(e => e.kind === 'stall');
  const stat = (label, value, title = '') => `<span${title ? ` title="${esc(title)}"` : ''}><span class="muted">${label}</span> ${value}</span>`;
  const growth = db.growth_24h == null ? '' : ` (${db.growth_24h >= 0 ? '+' : '−'}${bytes(Math.abs(db.growth_24h))}/day)`;
  const stats = [
    stat('CPU', hlPct(proc.cpu_avg), proc.cpu_max == null ? '' : `max ${hlPct(proc.cpu_max)}`),
    stat('p95', hlMs(req.p95)),
    stat('Loop lag', hlMs(proc.lag_max), 'max'),
    stat('Requests', (req.n || 0).toLocaleString()),
    req.errors ? `<span class="err">${req.errors.toLocaleString()} errors</span>` : '',
    m.slow?.length ? stat('Slow', m.slow.length) : '',
    proc.locked ? `<span class="err">${proc.locked} locked</span>` : '',
    db.bytes ? stat('DB', bytes(db.bytes) + growth, db.wal_bytes ? `WAL ${bytes(db.wal_bytes)}` : '') : ''].filter(Boolean).join('');
  const table = (head, rows) => rows.length ? `<div class="scroll"><table class="hl-table"><tr>${head.map((h, i) => `<th${i ? ' class="tnum"' : ''}>${h}</th>`).join('')}</tr>${rows.join('')}</table></div>` : '';
  const routes = (req.routes || []).slice(0, 8).map(r => `<tr><td class="hl-code" title="${esc(Object.entries(r.callers || {}).map(([k, n]) => `${k} ${n}`).join(', '))}">${esc(r.route)}</td>
    <td class="tnum">${r.n.toLocaleString()}</td><td class="tnum">${hlMs(r.p95)}</td><td class="tnum">${hlMs(r.total_ms)}</td></tr>`);
  const sql = (m.sql?.queries || []).slice(0, 8).map(q => `<tr><td class="hl-code" title="${esc(q.sql)}">${esc(q.sql)}</td>
    <td class="tnum">${q.n.toLocaleString()}</td><td class="tnum">${hlMs(q.p95)}</td><td class="tnum">${hlMs(q.total_ms)}</td></tr>`);
  const slow = (m.slow || []).slice(0, 5).map(r => `<li><span class="hl-code">${esc(r.route)}</span> <span class="tnum">${hlMs(r.ms)}</span> <span class="muted">${esc(r.caller)} · ${esc(hlAt(r.ts))}</span></li>`).join('');
  const stall = stalls.slice(0, 3).map(e => {
    const loop = (e.threads?.busy || []).find(t => t.loop);
    return `<li><details><summary><span class="err">Stalled ${esc(String(e.stalled_s))} s</span> <span class="muted">${esc(hlAt(e.at))}</span></summary>
      <pre class="hl-stack">${esc((loop?.stack || []).slice().reverse().join('\n'))}</pre></details></li>`;
  }).join('');
  return `<h2>Performance <span class="muted hl-window">last hour</span></h2><div class="hl-stats tnum" data-hl-metrics>${stats}</div>
    ${stall ? `<ul class="hl-bots">${stall}</ul>` : ''}
    ${table(['Route', 'Calls', 'p95', 'Time'], routes)}${table(['SQL', 'Calls', 'p95', 'Time'], sql)}
    ${slow ? `<h2>Slow requests</h2><ul class="hl-bots">${slow}</ul>` : ''}`;
}

async function hlCheckUpdates() {
  if (HL_CHECKING) return;
  HL_CHECKING = true; HL_CHECK_MSG = null;
  hlPageDraw();
  try {
    // Not post(): that retries a 429 or 502 as a lost write, and here the refusal itself is the answer.
    const reply = await fetch(API + '/v2/system/update/check', {method: 'POST'});
    const notice = await reply.json().catch(() => ({}));
    if (!reply.ok) throw apiFailure(notice, reply);
    await hlRefresh();
    // An available update is already the Version line, refreshed above: say it once, not twice.
    HL_CHECK_MSG = notice?.checking ? {text: 'Still checking. Look again in a moment.'}
      : notice?.available ? null
      : {text: notice?.latest ? `You are on the latest release, ${nvVersion(notice.current)}.`
        : 'Checked. No release information came back.'};
  } catch (error) {
    HL_CHECK_MSG = {text: error.message || 'The check failed.', error: true};
  }
  HL_CHECKING = false;
  hlPageDraw();
}

window.hlMount = function hlMount() {
  const host = $('#hl-page');
  if (!host) return;
  if (HL) hlPageDraw(); else host.innerHTML = '<div class="empty">Loading…</div>';
  hlNav();
  void hlRefresh().then(() => { if (!HL) hlPageDraw(); });
};

window.hlBoot = function () {
  void hlRefresh();
  setInterval(() => { if (!document.hidden && S.me?.cloud) void hlRefresh(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.me?.cloud) void hlRefresh(); });
};

document.addEventListener('change', event => {
  const box = event.target.closest?.('[data-hl-monitor-switch], [data-hl-monitor-rule]');
  if (box) void hlMonitorToggle(box);
});

document.addEventListener('click', event => {
  if (event.target.closest('[data-hl-check-updates]')) { void hlCheckUpdates(); return; }
  const move = event.target.closest('[data-stuck-move]');
  if (move) { void hlStuckMove(move); return; }
  const finish = event.target.closest('[data-stuck-finish]');
  if (finish) { void hlStuckFinish(finish); return; }
  const open = event.target.closest('[data-hl-click]');
  // After this click finishes: the page's outside-click handler would otherwise close the popup it opens.
  if (open) setTimeout(() => document.querySelector(open.dataset.hlClick)?.click(), 0);
});
