/* ui/app/heartbeat.ts — The account status dot and its lines, account menu label, download link
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The account menu, its status dot and the hover card on the dot show two states only (Chris, Nov 4): Tico cannot be
// reached, or bots are not running. Health's checks and the person's operation issues are listed in Settings > Health,
// never here. The dot is coloured by the worst line: red when bots are not running, amber while the connection is lost.
const ACCOUNT_HEALTH = '<a class="ghost" href="#/settings" data-gs-tab="health" title="Open Health in Settings">Fix</a>';
// The issues Settings asks a person to act on (ui/app/settings.ts). Diagnostics that need nobody stay in Health.
const needsPerson = (issue: {needs_person?: unknown; action?: unknown; restart?: unknown}): boolean =>
  issue.needs_person === true && !!(issue.action || issue.restart);
// With no computer online, no bot can run, so it is red like a stopped scheduler. Health lists the computers for owners
// and admins; for anyone else there is no list, and this state is not shown.
const noComputerOnline = (): boolean => {
  const computers: unknown = window.hlComputers?.();
  return Array.isArray(computers) && !computers.some((computer: {online?: unknown}) => computer.online);
};
interface AccountLine {
  level: 'bad' | 'warn';
  text: string;
  fix: string;
}
function accountStatusLines(): AccountLine[] {
  const st = S.status, lines: AccountLine[] = [];
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
function renderAccountStatus(): void {
  const hb: HTMLElement | null = $('#heartbeat');
  if (!hb) return;
  const st = S.status, lines = accountStatusLines();
  const level = lines.some(line => line.level === 'bad') ? 'bad' : lines.length ? 'warn' : '';
  const only = lines.length === 1 ? lines[0] : undefined;
  const label = only ? only.text : lines.length ? `${lines.length} things to look at`
    : !st ? 'Connecting' : st.cloud ? 'Cloud backend connected' : 'Computer running';
  hb.title = label;
  hb.setAttribute('aria-label', label);
  hb.dataset.level = level;
  hb.hidden = !level;
  const html = lines.map(line => `<li class="account-status-line ${line.level}" data-level="${line.level}"><span class="account-status-text" title="${esc(line.text)}">${esc(line.text)}</span>${line.fix}</li>`).join('');
  const boxes: (HTMLElement | null)[] = [$('#account-status'), $('#account-status-pop')];
  for (const box of boxes) {
    if (!box) continue;
    patchLiveHTML(box, html ? `<ul>${html}</ul>` : '');
    if (!html) box.hidden = true;
    else if (box.id === 'account-status') box.hidden = false;
  }
}
function renderHeartbeat(): void {
  renderAccountStatus();
}
// Desktop: hovering the dot shows the lines without opening the menu. A tap opens the menu, which starts with them.
(() => {
  const wrap: HTMLElement | null = $('.account-wrap');
  const pop: HTMLElement | null = $('#account-status-pop');
  if (!wrap || !pop) return;
  let hide = 0;
  const canHover = (): boolean => window.matchMedia('(hover: hover)').matches;
  wrap.addEventListener('mouseover', ev => {
    const menu: HTMLElement | null = $('#account-menu');
    if (!canHover() || !eventElement(ev)?.closest('#heartbeat, #account-status-pop') || !menu || !menu.hidden || !pop.innerHTML) return;
    clearTimeout(hide); pop.hidden = false;
  });
  wrap.addEventListener('mouseout', ev => {
    const related = ev.relatedTarget;
    if (related instanceof Element && related.closest('#heartbeat, #account-status-pop')) return;
    clearTimeout(hide); hide = setTimeout(() => { pop.hidden = true; }, 200);
  });
  wrap.addEventListener('click', () => { pop.hidden = true; });
})();
document.addEventListener('click', ev => {
  const target = eventElement(ev);
  if (target?.closest('[data-account-retry]')) { void refresh(false); return; }
  const restart = target?.closest('[data-runner-restart]');
  if (restart instanceof HTMLButtonElement) void restartComputer(restart);
});
// A computer with a Tico update restarts from here. The runner takes main when
// that is safe and restarts once nothing is running, so the line can say nothing is interrupted.
const runnerRestartButton = (issue: {restart?: unknown; machine?: unknown}): string => issue.restart && issue.machine
  ? `<button class="primary" type="button" data-runner-restart="${esc(issue.machine)}">Restart</button>` : '';
// Sends the restart from any Restart button (the status line, the Settings checks) and says what it did on the
// line the button sits in, if there is one.
async function restartComputer(button: HTMLButtonElement): Promise<void> {
  button.disabled = true; button.textContent = 'Restarting…';
  const line = button.closest('.settings-issue')?.querySelector('p') ?? null;
  try {
    const r: {running?: number} = await post(`/v2/computers/${encodeURIComponent(button.dataset.runnerRestart ?? '')}/restart`, {});
    if (line) line.textContent = r.running
      ? `Restarting after ${r.running} run${r.running === 1 ? ' finishes' : 's finish'}.`
      : 'Restarting now. Nothing is running, so nothing will be interrupted.';
    button.textContent = 'Restarting';
  } catch (e) { button.disabled = false; button.textContent = 'Restart'; toast(errorMessage(e, ''), true); }
}
function renderAccount(): void {
  const el: HTMLElement | null = $('#account'), me = S.me;
  if (!el) return;
  const email = el.querySelector<HTMLElement>('.account-email');
  if (!me) { if (email) email.textContent = 'Not signed in'; return; }
  // A bot's read-only view of the app (backend/app_view.py) has no email: it is named, and said to be read only.
  const who = me.email || (me.read_only_view ? `${me.name || 'Bot'} · read-only view` : '');
  el.title = `Signed in as ${who}${me.local ? ' (local app)' : ''}`;
  if (email) email.textContent = who || 'Not signed in';
  renderAccountStatus();
  renderInboxNav();
  renderNewVersion();
  renderDownloadLink();
  nativeOrgSync();
  // Only a session an identity proxy vouches for has anything to end; a bearer or the local app does not.
  const signOut: HTMLElement | null = $('#sign-out');
  if (signOut) signOut.hidden = !me.proxy_session;
}
// Only the company's own app is offered: a menu link and a browser nudge. Neither appears
// inside the app. Dismissal hides the nudge for a week.
const visitorOS = (): string => /Win/.test(navigator.platform) ? 'windows' : /Linux/.test(navigator.platform) && !/Android/.test(navigator.userAgent) ? 'linux' : /Mac/.test(navigator.platform) ? 'mac' : '';
const OS_WORDS: Record<string, string> = {mac: 'Mac', windows: 'Windows', linux: 'Linux'};
interface DownloadInfo {
  available?: boolean;
  url?: unknown;
  version?: string;
  size_mb?: number;
  app_kind?: string;
  notarized?: boolean;
}
let downloadRequest = 0;
async function renderDownloadLink(): Promise<void> {
  const request = ++downloadRequest;
  const a: HTMLAnchorElement | null = $('#download-app');
  const nudge: HTMLElement | null = $('#app-nudge');
  const nudgeGet = nudge?.querySelector<HTMLAnchorElement>('.app-nudge-get') ?? null;
  const nudgeText = nudge?.querySelector<HTMLElement>('.app-nudge-text') ?? null;
  const nudgeClose = nudge?.querySelector<HTMLElement>('.app-nudge-close') ?? null;
  const os = visitorOS();
  const eligible = (): boolean => !S.me?.local && !!os && visitorOS() === os && !document.body.classList.contains('native');
  if (a) { a.hidden = true; a.removeAttribute('href'); }
  if (nudge) { nudge.hidden = true; nudgeGet?.removeAttribute('href'); }
  if (!a || !eligible()) return;
  try {
    const r = await fetch('/api/download/' + os, {credentials: 'same-origin'});
    const d: DownloadInfo | null = r.ok ? await r.json() : null;
    if (request !== downloadRequest || !eligible() || !d?.available || d.app_kind !== 'company' || typeof d.url !== 'string' || !d.url.trim()) return;
    // The API validates the installer source. Use that exact version, without selecting a
    // manifest again, and never turn a non-web URL into an executable link.
    const url = new URL(d.url, location.href);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return;
    // Short, per OS (Chris, Nov 13: "Just say 'Download Mac App'"); the nudge's sentence still names the team.
    const label = `Download ${OS_WORDS[os]} app`;
    a.href = d.url;
    a.textContent = '';
    a.insertAdjacentHTML('beforeend', `<span class="nav-icon" aria-hidden="true">download</span>${esc(label)}${os === 'mac' ? betaPill('mac_app') : ''}`);
    a.title = `${appName()} ${d.version || ''} for ${OS_WORDS[os]} (${d.size_mb || '?'} MB)${d.notarized || os !== 'mac' ? '' : '. First open: System Settings → Privacy & Security → Open Anyway'}`;
    a.hidden = false;
    if (!nudge) return;
    let snoozed = 0;
    try { snoozed = Number(localStorage.getItem('hub.app-nudge.until') || 0); } catch {}
    if (snoozed > Date.now()) { nudge.hidden = true; return; }
    if (nudgeText) nudgeText.textContent = `Tico for ${companyName()} opens your company directly, with no server setup.`;
    if (nudgeGet) { nudgeGet.href = d.url; nudgeGet.textContent = label; }
    if (nudgeClose) nudgeClose.onclick = () => { nudge.hidden = true; try { localStorage.setItem('hub.app-nudge.until', String(Date.now() + 7 * 86400e3)); } catch {} };
    nudge.hidden = false;
  } catch { if (request === downloadRequest) { a.hidden = true; if (nudge) nudge.hidden = true; } }
}
