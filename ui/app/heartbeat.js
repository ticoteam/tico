/* ui/app/heartbeat.js — The account status dot and its lines, account menu label, download link
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The account menu, its status dot and the hover card on the dot show two states only (Chris, Nov 4): Tico cannot be
// reached, or bots are not running. Health's checks and the person's operation issues are listed in Settings > Health,
// never here. The dot is coloured by the worst line: red when bots are not running, amber while the connection is lost.
const ACCOUNT_HEALTH = '<a class="ghost" href="#/settings" data-gs-tab="health" title="Open Health in Settings">Fix</a>';
// The issues Settings asks a person to act on (ui/app/settings.js). Diagnostics that need nobody stay in Health.
const needsPerson = issue => issue.needs_person === true && !!(issue.action || issue.restart);
// With no computer online, no bot can run, so it is red like a stopped scheduler. Health lists the computers for owners
// and admins; for anyone else there is no list, and this state is not shown.
const noComputerOnline = () => {
  const computers = window.hlComputers?.();
  return Array.isArray(computers) && !computers.some(computer => computer.online);
};
function accountStatusLines() {
  const st = S.status, lines = [];
  if (!st) {
    // A lost connection comes back by itself, so it is a warning, never red.
    if (!S.statusPending) lines.push({level: 'warn', text: "Can't reach Tico. Trying again", fix: '<button class="ghost" type="button" data-account-retry>Retry</button>'});
  } else if (!st.keeper_alive) {
    lines.push({level: 'bad', text: st.cloud ? "Bots aren't running: the cloud scheduler stopped" : "Tico stopped on this computer, so bots aren't running", fix: ACCOUNT_HEALTH});
  } else if (noComputerOnline()) {
    lines.push({level: 'bad', text: "Bots aren't running: no computer is online", fix: ACCOUNT_HEALTH});
  }
  return lines;
}
function renderAccountStatus() {
  const hb = $('#heartbeat');
  if (!hb) return;
  const st = S.status, lines = accountStatusLines();
  const level = lines.some(line => line.level === 'bad') ? 'bad' : lines.length ? 'warn' : '';
  const label = lines.length === 1 ? lines[0].text : lines.length ? `${lines.length} things to look at`
    : !st ? 'Connecting' : st.cloud ? 'Cloud backend connected' : 'Computer running';
  hb.title = label;
  hb.setAttribute('aria-label', label);
  hb.dataset.level = level;
  hb.hidden = !level;
  const html = lines.map(line => `<li class="account-status-line ${line.level}" data-level="${line.level}"><span class="account-status-text" title="${esc(line.text)}">${esc(line.text)}</span>${line.fix}</li>`).join('');
  for (const box of [$('#account-status'), $('#account-status-pop')]) {
    if (!box) continue;
    patchLiveHTML(box, html ? `<ul>${html}</ul>` : '');
    if (!html) box.hidden = true;
    else if (box.id === 'account-status') box.hidden = false;
  }
}
function renderHeartbeat() {
  renderAccountStatus();
}
// Desktop: hovering the dot shows the lines without opening the menu. A tap opens the menu, which starts with them.
(() => {
  const wrap = $('.account-wrap'), pop = $('#account-status-pop');
  if (!wrap || !pop) return;
  let hide = 0;
  const canHover = () => window.matchMedia('(hover: hover)').matches;
  wrap.addEventListener('mouseover', ev => {
    if (!canHover() || !ev.target.closest('#heartbeat, #account-status-pop') || !$('#account-menu').hidden || !pop.innerHTML) return;
    clearTimeout(hide); pop.hidden = false;
  });
  wrap.addEventListener('mouseout', ev => {
    if (ev.relatedTarget?.closest?.('#heartbeat, #account-status-pop')) return;
    clearTimeout(hide); hide = setTimeout(() => { pop.hidden = true; }, 200);
  });
  wrap.addEventListener('click', () => { pop.hidden = true; });
})();
document.addEventListener('click', ev => {
  if (ev.target.closest('[data-account-retry]')) { void refresh(false); return; }
  const restart = ev.target.closest('[data-runner-restart]');
  if (restart) { void restartComputer(restart); return; }
});
// A computer with a Tico update restarts from here. The runner takes main when
// that is safe and restarts once nothing is running, so the line can say nothing is interrupted.
const runnerRestartButton = issue => issue.restart && issue.machine
  ? `<button class="primary" type="button" data-runner-restart="${esc(issue.machine)}">Restart</button>` : '';
// Sends the restart from any Restart button (the status line, the Settings checks) and says what it did on the
// line the button sits in, if there is one.
async function restartComputer(button) {
  button.disabled = true; button.textContent = 'Restarting…';
  const line = button.closest('.settings-issue')?.querySelector('p');
  try {
    const r = await post(`/v2/computers/${encodeURIComponent(button.dataset.runnerRestart)}/restart`, {});
    if (line) line.textContent = r.running
      ? `Restarting after ${r.running} run${r.running === 1 ? ' finishes' : 's finish'}.`
      : 'Restarting now. Nothing is running, so nothing will be interrupted.';
    button.textContent = 'Restarting';
  } catch (e) { button.disabled = false; button.textContent = 'Restart'; toast(e.message, true); }
}
function renderAccount() {
  const el = $('#account'), me = S.me;
  if (!el) return;
  if (!me) { el.querySelector('.account-email').textContent = 'Not signed in'; return; }
  el.title = `Signed in as ${me.email}${me.local ? ' (local app)' : ''}`;
  el.querySelector('.account-email').textContent = me.email || 'Not signed in';
  renderAccountStatus();
  renderInboxNav();
  renderNewVersion();
  renderDownloadLink();
  nativeOrgSync();
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
    a.insertAdjacentHTML('beforeend', `<span class="nav-icon" aria-hidden="true">download</span>${esc(label)}${os === 'mac' ? betaPill('mac_app') : ''}`);
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
