/* ui/app/heartbeat.js — Heartbeat, the Needs-you strip, account menu label, download link
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Only urgent issues with a concrete human action interrupt the person. The full diagnostic
// list remains in Settings, including temporary outages and bot execution reviews.
const needsPerson = issue => issue.needs_person === true && !!(issue.action || issue.restart);
const needsYouKey = issue => [issue.kind, issue.bot || '', issue.machine || '', issue.title].join('|');
const NEEDS_YOU_KEY = 'tico.needsYou';
function needsYouMemory() { try { return JSON.parse(localStorage.getItem(NEEDS_YOU_KEY) || '{}'); } catch { return {}; } }
function needsYouRemember(patch) { try { localStorage.setItem(NEEDS_YOU_KEY, JSON.stringify({...needsYouMemory(), ...patch})); } catch {} }
function renderHeartbeat() {
  const st = S.status, hb = $('#heartbeat');
  const issues = st?.health_issues || [];
  const urgent = issues.filter(needsPerson);
  const problem = !st ? (S.statusPending ? '' : 'Tico unreachable') : !st.keeper_alive ? (st.cloud ? 'Cloud scheduler needs attention' : 'Computer stopped')
    : urgent.length ? `${urgent.length} ${urgent.length === 1 ? 'thing needs' : 'things need'} you · click to handle` : '';
  const label = problem || (!st ? 'Connecting' : st.cloud ? 'Cloud backend connected' : 'Computer running');
  hb.title = label;
  hb.setAttribute('aria-label', label);
  hb.innerHTML = `<span class="dot ${problem ? 'failed' : ''}" ${!problem && st ? 'style="background:var(--ok)"' : ''} aria-hidden="true"></span>`;
  const alert = $('#heartbeat-alert');
  alert.textContent = problem;
  alert.hidden = !problem;
  alert.classList.toggle('err', !!problem);
  alert.classList.remove('muted');
  alert.onclick = st && urgent.length ? () => needsYou() : null;
  alert.style.cursor = st && urgent.length ? 'pointer' : '';
  needsYouAutoOpen(urgent);
}
// The first time a new issue that needs a person shows up, it opens in front of them, wherever
// they are in the app. Closing it is remembered per issue, and Snooze quiets everything for an
// hour, so a person is asked once, not on every poll.
function needsYouAutoOpen(urgent) {
  // Only an issue the server explicitly marked for a person opens by itself; anything else waits
  // behind the sidebar alert. Never over another dialog: whatever the person is doing comes first.
  const explicit = urgent.filter(needsPerson);
  if (!explicit.length || document.querySelector('dialog[open], .needs-you')) return;
  const memory = needsYouMemory();
  if (memory.snoozedUntil && Date.now() < memory.snoozedUntil) return;
  const seen = new Set(memory.seen || []);
  if (explicit.every(issue => seen.has(needsYouKey(issue)))) return;
  needsYou();
}
// A panel above the sidebar alert, not a modal: it pops open in front of the person without
// trapping them or covering what they were doing, and it is not a <dialog>, so nothing that
// waits for `dialog[open]` (the review it can launch, for one) is confused by it.
// A computer with a Tico update restarts from here. The runner takes main when
// that is safe and restarts once nothing is running, so the line can say nothing is interrupted.
const runnerRestartButton = issue => issue.restart && issue.machine
  ? `<button class="primary" type="button" data-runner-restart="${esc(issue.machine)}">Restart</button>` : '';
function bindRunnerRestart(root) {
  root.querySelectorAll('[data-runner-restart]').forEach(button => {
    button.onclick = async () => {
      button.disabled = true; button.textContent = 'Restarting…';
      const line = button.closest('.settings-issue')?.querySelector('p');
      try {
        const r = await post(`/v2/computers/${encodeURIComponent(button.dataset.runnerRestart)}/restart`, {});
        if (line) line.textContent = r.running
          ? `Restarting after ${r.running} run${r.running === 1 ? ' finishes' : 's finish'}.`
          : 'Restarting now. Nothing is running, so nothing will be interrupted.';
        button.textContent = 'Restarting';
      } catch (e) { button.disabled = false; button.textContent = 'Restart'; toast(e.message, true); }
    };
  });
}
function needsYou() {
  document.querySelector('.needs-you')?.remove();
  const issues = (S.status?.health_issues || []).filter(needsPerson);
  const panel = document.createElement('section'); panel.className = 'needs-you';
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Needs you');
  const row = issue => `<div class="settings-issue" data-key="${esc(needsYouKey(issue))}"><div><strong>${esc(issue.title)}</strong><p>${esc(issue.detail)}</p>${issue.action && issue.action !== 'review' ? `<p class="muted">${esc(issue.action)}</p>` : ''}</div>
    ${issue.kind === 'uncertain_work' ? `<button class="ghost" data-execution-review="${esc(issue.bot)}">Review now</button>` : ''}${runnerRestartButton(issue)}${issue.bot ? `<a class="ghost" href="#/bot/${esc(issue.bot)}/more">Open bot</a>` : ''}</div>`;
  panel.innerHTML = `<header><h2>${issues.length === 1 ? 'One thing needs you' : `${issues.length} things need you`}</h2><button class="ghost" data-close aria-label="Close">Close</button></header>
    <div data-list>${issues.map(row).join('') || '<p>Nothing needs you right now.</p>'}</div>
    <div class="row"><button class="ghost" data-snooze>Snooze for an hour</button><a class="ghost" href="#/settings">Open Settings</a></div>`;
  const close = () => { needsYouRemember({seen: issues.map(needsYouKey)}); panel.remove(); };
  panel.querySelector('[data-close]').onclick = close;
  panel.querySelector('[data-snooze]').onclick = () => { needsYouRemember({snoozedUntil: Date.now() + 3600e3}); close(); };
  panel.querySelectorAll('[data-execution-review]').forEach(button => {
    button.onclick = () => { close(); executionReview(button.dataset.executionReview); };
  });
  panel.querySelectorAll('a[href]').forEach(a => a.addEventListener('click', close));
  bindRunnerRestart(panel);
  document.body.appendChild(panel);
  return panel;
}
function renderAccount() {
  const el = $('#account'), me = S.me;
  if (!el) return;
  if (!me) { el.innerHTML = '<span class="account-email">Not signed in</span>'; return; }
  el.title = `Signed in as ${me.email}${me.local ? ' (local app)' : ''}`;
  el.innerHTML = `<span class="account-email">${esc(me.email || 'Not signed in')}</span><span class="hl-alert" data-hl-alert hidden></span>`;
  window.hlNav?.();
  $('#connect-agent').hidden = !settingsCanMakeTokens();
  renderInboxNav();
  renderNewVersion();
  renderDownloadLink();
  // Only a session an identity proxy vouches for has anything to end; a bearer or the local app does not.
  $('#sign-out').hidden = !me.proxy_session;
}
// The company app gets a browser nudge; a generic build is only a manual-setup option in
// the account menu. Neither appears inside the app. Dismissal hides the nudge for a week.
const visitorOS = () => /Win/.test(navigator.platform) ? 'windows' : /Linux/.test(navigator.platform) && !/Android/.test(navigator.userAgent) ? 'linux' : /Mac/.test(navigator.platform) ? 'mac' : '';
const OS_WORDS = {mac: 'Mac', windows: 'Windows', linux: 'Linux'};
let downloadRequest = 0;
async function renderDownloadLink() {
  const request = ++downloadRequest;
  const a = $('#download-app'), nudge = $('#app-nudge');
  const os = visitorOS();
  const eligible = () => !S.me?.local && os && visitorOS() === os && !document.body.classList.contains('native');
  if (a) { a.hidden = true; a.removeAttribute('href'); }
  if (nudge) { nudge.hidden = true; nudge.querySelector('.app-nudge-get').removeAttribute('href'); }
  if (!a || !eligible()) return;
  try {
    const r = await fetch('/api/download/' + os, {credentials: 'same-origin'});
    const d = r.ok ? await r.json() : null;
    if (request !== downloadRequest || !eligible() || !d?.available || typeof d.url !== 'string' || !d.url.trim()) return;
    // The API validates the installer source. Use that exact version, without selecting a
    // manifest again, and never turn a non-web URL into an executable link.
    const url = new URL(d.url, location.href);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return;
    const company = d.app_kind === 'company';
    const label = company ? `Download Tico for ${companyName()}` : 'Generic Tico · manual setup';
    a.href = d.url;
    a.textContent = '';
    a.insertAdjacentHTML('beforeend', `<span class="nav-icon" aria-hidden="true">download</span>${esc(label)}`);
    a.title = `${company ? appName() : 'Generic Tico'} ${d.version || ''} for ${OS_WORDS[os]} (${d.size_mb || '?'} MB)${company ? '' : '. Requires manual server setup'}${d.notarized || os !== 'mac' ? '' : '. First open: System Settings → Privacy & Security → Open Anyway'}`;
    a.hidden = false;
    if (!nudge || !company) return;
    let snoozed = 0;
    try { snoozed = Number(localStorage.getItem('hub.app-nudge.until') || 0); } catch {}
    if (snoozed > Date.now()) { nudge.hidden = true; return; }
    nudge.querySelector('.app-nudge-text').textContent = `Tico for ${companyName()} opens your company directly, with no server setup.`;
    const get = nudge.querySelector('.app-nudge-get');
    get.href = d.url; get.textContent = label;
    nudge.querySelector('.app-nudge-close').onclick = () => { nudge.hidden = true; try { localStorage.setItem('hub.app-nudge.until', String(Date.now() + 7 * 86400e3)); } catch {} };
    nudge.hidden = false;
  } catch { if (request === downloadRequest) { a.hidden = true; if (nudge) nudge.hidden = true; } }
}
