/* ui/app/notices.js — New-version pill, usage notice, Finish setup nav, native hooks
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A tab left open through an update keeps running the old UI. The config poll already runs, so what this page
// loaded with is compared to the server's on every answer: its release, and the build of its script and
// stylesheet (the `?v=` of their tags, which the config carries as `ui_build`; empty when the files are served
// unbundled). The bar offers the reload and never reloads by itself, since the person may be typing.
let LOADED_VERSION = '';
const PAGE_BUILD = (() => {
  const v = sel => (document.querySelector(sel)?.getAttribute(sel.startsWith('script') ? 'src' : 'href') || '').split('?v=')[1] || '';
  const js = v('script[src*="app.bundle.js"]'), css = v('link[href*="app.bundle.css"]');
  return js && css ? js + '.' + css : '';
})();
function noticeUpdatedServer() {
  const v = String(S.config.version || '');
  if (!v) return;
  if (!LOADED_VERSION) LOADED_VERSION = v;
  const bar = $('#stale-banner');
  if (!bar) return;
  const build = String(S.config.ui_build || '');
  bar.hidden = v === LOADED_VERSION && !(build && PAGE_BUILD && build !== PAGE_BUILD);
}
document.addEventListener('click', ev => { if (ev.target.closest('#stale-reload')) location.reload(); });
// The desktop app itself. Its own updater fails silently (a feed behind a sign-in, no build published, a
// shell too old to report anything), so the page compares the app's version (window.ticoNativeVersion,
// app/src/main.rs bridge_script) with the server's published build and says so. An app without a version
// predates that and is out of date by definition. Checked at most hourly; Later hides it for this page.
const verParts = v => String(v).split(/[-+]/)[0].split('.').map(n => parseInt(n, 10) || 0);
function verLess(a, b) {
  const x = verParts(a), y = verParts(b);
  for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  return false;
}
let appUpdateAt = 0, appUpdateOff = false, appUpdateBusy = false, appUpdateLink = '';
async function renderAppUpdate() {
  const bar = $('#app-update'), os = visitorOS();
  if (!bar || !os || appUpdateOff || appUpdateBusy || !document.body.classList.contains('native') || Date.now() - appUpdateAt < 3600e3) return;
  appUpdateAt = Date.now();
  let d = null;
  try { const r = await fetch('/api/download/' + os, {credentials: 'same-origin'}); d = r.ok ? await r.json() : null; } catch {}
  if (!d?.available || !d.version || appUpdateOff) return;
  const mine = window.ticoNativeVersion;
  if (mine && !verLess(mine, d.version)) { bar.hidden = true; return; }
  // A link to this same host would open inside the app; only another host (runner, GitHub) opens the browser.
  appUpdateLink = '';
  try { const u = new URL(d.url, location.href); if (u.protocol === 'https:' && u.host !== location.host) appUpdateLink = u.href; } catch {}
  const can = !!mine && (window.ticoNativeFeatures || []).includes('checkForUpdates');
  $('#app-update-text').textContent = can ? `App ${nvVersion(d.version)} is ready ·`
    : appUpdateLink ? 'This app is out of date ·' : `This app is out of date. Get ${nvVersion(d.version)} from ${location.host} in a browser.`;
  $('#app-update-go').hidden = !can;
  const dl = $('#app-update-dl');
  dl.hidden = can || !appUpdateLink;
  if (appUpdateLink) dl.href = appUpdateLink;
  bar.hidden = false;
}
document.addEventListener('click', ev => {
  if (ev.target.closest('#app-update-later')) { appUpdateOff = true; $('#app-update').hidden = true; return; }
  if (!ev.target.closest('#app-update-go') || appUpdateBusy) return;
  appUpdateBusy = true;
  nativeHandler('checkForUpdates')?.postMessage();
  $('#app-update-go').hidden = true;
  $('#app-update-text').textContent = 'Updating · the app restarts when it is done';
  // The shell reports each step (window.ticoAppUpdate); no word in two minutes is treated as a failure.
  clearTimeout(appUpdateTimer);
  appUpdateTimer = setTimeout(() => appUpdateFailed(''), 120e3);
});
let appUpdateTimer = 0;
function appUpdateFailed(detail) {
  clearTimeout(appUpdateTimer);
  appUpdateBusy = false;
  const why = detail ? ` (${detail})` : '';
  $('#app-update-text').textContent = appUpdateLink ? `The update did not install${why} ·`
    : `The update did not install${why}. Get the new app from ${location.host} in a browser.`;
  $('#app-update-go').hidden = true;
  const dl = $('#app-update-dl');
  dl.hidden = !appUpdateLink;
  if (appUpdateLink) dl.href = appUpdateLink;
  $('#app-update').hidden = false;
}
// Called by the shell after every check (app/src/main.rs report_update), whether the page or the timer asked.
window.ticoAppUpdate = ({state, detail} = {}) => {
  if (state === 'installing') {
    clearTimeout(appUpdateTimer);
    appUpdateTimer = setTimeout(() => appUpdateFailed(''), 300e3);
    appUpdateBusy = true;
    $('#app-update-go').hidden = $('#app-update-dl').hidden = true;
    $('#app-update-text').textContent = `Installing ${nvVersion(detail)} · the app restarts when it is done`;
    $('#app-update').hidden = false;
  } else if (state === 'failed' && (appUpdateBusy || !$('#app-update').hidden)) {
    appUpdateFailed(detail);
  } else if (state === 'current' && appUpdateBusy) {
    // The page saw a newer build than the app's own feed offers: the feed is behind.
    appUpdateFailed('the update feed does not have it yet');
  }
};
// The anonymous usage count (PRIVACY.md): the owner sees this once, and nothing is counted before they have. It
// would sit over the first-run wizard and its after-Create screen, so it waits for the first other page. Before the
// first route() (LAST_ROUTE, ui/app/router.js) there is no page yet: a new install's boot sends it to the wizard after
// its config is read.
let usageNoticeShown = false;
function renderUsageNotice() {
  const bar = $('#usage-notice');
  if (!bar) return;
  bar.hidden = !S.config.usage_count_notice || !LAST_ROUTE || S.route === WELCOME;
  if (!bar.hidden && !usageNoticeShown) { usageNoticeShown = true; post('/v2/system/usage-count/notice', {state: 'shown'}).catch(() => {}); }
}
document.addEventListener('click', ev => {
  if (!ev.target.closest('#usage-notice-x')) return;
  S.config.usage_count_notice = false; renderUsageNotice();
  post('/v2/system/usage-count/notice', {state: 'dismissed'}).catch(() => {});
});
// A short message Tico HQ has for this version (backend/releases.py), shown once to each person in this browser: it is
// marked seen as it appears, so a reload or another tab does not bring it back.
const VM_KEY = 'tico.version-message.seen';
const vmSeenKey = () => VM_KEY + ':' + (S.me?.id || S.me?.email || '');
let vmShowing = 0;
function renderVersionMessage() {
  const bar = $('#version-message'), m = S.config.update?.message;
  if (!bar) return;
  if (vmShowing) { bar.hidden = vmShowing === -1; return; }
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(vmSeenKey()) || '[]'); } catch {}
  if (!m || !m.id || !S.me || seen.includes(m.id)) { bar.hidden = true; return; }
  $('#version-message-text').textContent = m.body;
  const link = $('#version-message-link');
  link.hidden = !/^https:\/\//.test(m.url || '');
  if (!link.hidden) link.href = m.url;
  bar.hidden = false;
  vmShowing = m.id;
  try { localStorage.setItem(vmSeenKey(), JSON.stringify([...seen, m.id].slice(-20))); } catch {}
}
document.addEventListener('click', ev => {
  if (!ev.target.closest('#version-message-x')) return;
  vmShowing = -1; $('#version-message').hidden = true;
});

// "How is Tico working for you?" (backend/support_asks.py): after the first bot result and 7 and 30 days on, once
// each. Send files the answer as a feedback request; Skip sends nothing. Either way that moment is not asked again.
function renderFeedbackAsk() {
  const ask = S.config.feedback_question;
  let box = $('#feedback-ask');
  if (!ask) { if (box) box.hidden = true; return; }
  if (box && !box.hidden && box.dataset.moment === ask.moment) return;
  if (!box) { box = document.createElement('form'); box.id = 'feedback-ask'; box.setAttribute('aria-label', 'Feedback'); document.body.appendChild(box); }
  box.dataset.moment = ask.moment; box.hidden = false;
  box.innerHTML = `<label for="feedback-answer">${esc(ask.question)}</label>
    <textarea id="feedback-answer" name="answer" rows="2" maxlength="3800" placeholder="A sentence is plenty. A person at Tico reads it."></textarea>
    <div class="feedback-row"><span class="muted">Sent to Tico support as feedback.</span><span class="spacer"></span>
      <button class="ghost" type="button" data-skip>Skip</button><button class="primary" type="submit">Send</button></div>`;
  const done = () => { S.config.feedback_question = null; box.hidden = true; };
  box.querySelector('[data-skip]').onclick = () => { done(); post('/v2/support/feedback', {moment: ask.moment}).catch(() => {}); };
  box.onsubmit = async ev => {
    ev.preventDefault();
    const answer = box.elements.answer.value.trim(), buttons = [...box.querySelectorAll('button')];
    if (!answer) { box.elements.answer.focus(); return; }
    buttons.forEach(b => { b.disabled = true; });
    try { await post('/v2/support/feedback', {moment: ask.moment, answer}); done(); toast('Thanks. Sent to Tico support.'); }
    catch (error) { toast(error.message || 'Not sent. Try again.', true); buttons.forEach(b => { b.disabled = false; }); }
  };
}
// "Finish setup" tops the nav while the owner has not finished setup, and disappears the
// moment the server stops asking for one. Everyone else never sees it. On the wizard itself the rest of the app's
// pages and What's new are not shown until setup is done: a new install has nothing there yet (ui/styles/sidebar.css).
function renderOnboardingNav() {
  const link = $('#nav-welcome');
  if (link) link.hidden = !S.config.onboarding_needed;
  document.body.classList.toggle('in-setup', !!S.config.onboarding_needed && S.route === WELCOME);
  renderUsageNotice();
}
// "New version" in the sidebar: quiet, only when the server says a newer release exists. Anyone
// may read the changelog; only the owner may start an update. Dismissal is per person and version.
const NV_KEY = 'tico.new-version.dismissed';
const nvDismissedKey = () => NV_KEY + ':' + (S.me?.id || S.me?.email || '');
const nvDismissed = v => { try { return localStorage.getItem(nvDismissedKey()) === v; } catch { return false; } };
let nvBusy = false;
function nvVersion(v) { return /^\d/.test(v) ? 'v' + v : v; }
function renderNewVersion() {
  const wrap = $('#new-version-wrap');
  if (!wrap) return;
  const u = S.config.update;
  const show = !!(u && u.available && u.latest && !nvDismissed(u.latest)) || nvBusy;
  wrap.hidden = !show;
  if (!show) { setNewVersionPop(false); return; }
  $('#new-version-label').textContent = 'New version ' + nvVersion(u.latest);
  if (!nvBusy) nvRenderPop();
}
function setNewVersionPop(open) {
  const pop = $('#new-version-pop'), btn = $('#new-version');
  if (!pop) return;
  pop.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
}
function nvRenderPop() {
  const u = S.config.update, pop = $('#new-version-pop');
  const owner = S.me?.role === 'owner';
  const safeUrl = /^https:\/\//.test(u.url || '') ? u.url : '';
  pop.innerHTML = `<p><strong>Update available.</strong> ${esc(appName())} ${esc(nvVersion(u.latest))} is out. You are on ${esc(nvVersion(u.current))}.</p>
    <div class="nv-actions">${safeUrl ? `<a class="ghost btnlike" id="nv-changelog" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">View changelog</a>` : ''}${owner ? '<button class="primary" id="nv-update" type="button">Update now</button>' : ''}</div>
    <div id="nv-out"></div>
    <button class="nv-dismiss" id="nv-dismiss" type="button">Dismiss for this version</button>`;
}
function nvOut(html) { const el = $('#nv-out'); if (el) el.innerHTML = html; }
async function nvStart() {
  const u = S.config.update, from = u.current;
  nvBusy = true;
  nvOut('<p class="nv-status" role="status">Updating…</p>');
  try {
    await post('/v2/system/update', {version: u.latest});
  } catch (e) {
    nvBusy = false;
    const command = e.body?.error?.command;
    if (e.status === 409 && command) {
      nvOut(`<p class="nv-status">Update from the server:</p><div class="nv-cmd"><code>${esc(command)}</code><button class="ghost" id="nv-copy" type="button" data-command="${esc(command)}">Copy</button></div>`);
    } else nvOut(`<p class="nv-status err">${esc(e.message)}</p>`);
    return;
  }
  // The server restarts under us; a failed poll is part of the update, not an error.
  const deadline = Date.now() + 10 * 60000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    let st = null;
    try { st = await get('/v2/system/update'); } catch { nvOut('<p class="nv-status" role="status">Updating…</p>'); continue; }
    if (st.state === 'healthy') {
      let cfg = null; try { cfg = await get('/v2/config'); } catch {}
      if (cfg && cfg.version && cfg.version !== from) { nvOut('<p class="nv-status" role="status">Updated, reloading</p>'); setTimeout(() => location.reload(), 600); return; }
      continue;
    }
    if (st.state === 'rolled_back') { nvBusy = false; nvOut(`<p class="nv-status err">Rolled back — still on ${esc(nvVersion(st.from || from))}</p>`); return; }
    if (st.state === 'failed') { nvBusy = false; nvOut(`<p class="nv-status err">Update failed${st.message ? ': ' + esc(st.message) : ''}</p>`); return; }
    nvOut('<p class="nv-status" role="status">Updating…</p>');
  }
  nvBusy = false;
  nvOut('<p class="nv-status err">The update is taking longer than expected. Check the server.</p>');
}
document.addEventListener('click', ev => {
  const t = ev.target;
  if (t.closest('#new-version')) { setNewVersionPop($('#new-version-pop').hidden); return; }
  if (t.closest('#nv-update')) { if (!nvBusy) void nvStart(); return; }
  const copy = t.closest('#nv-copy');
  if (copy) { navigator.clipboard?.writeText(copy.dataset.command).then(() => { copy.textContent = 'Copied'; }, () => {}); return; }
  if (t.closest('#nv-dismiss')) {
    try { localStorage.setItem(nvDismissedKey(), S.config.update.latest); } catch {}
    renderNewVersion();
    return;
  }
  if (!t.closest('#new-version-wrap')) setNewVersionPop(false);
});
const refreshConfig = () => { if (!document.hidden && !nvBusy && S.me) get('/v2/config').then(c => applyConfig(c)).catch(() => {}); };
setInterval(refreshConfig, 30000);  // with the app's poll: a release the server just learned of shows within a poll, not an hour
document.addEventListener('visibilitychange', refreshConfig);   // a tab that was in the background asks the moment it is shown again
// The main assistant is shown under the environment's assistant name, never its slug.
// The built-in assistant reads as what it is (assistantName, ui/app/state.js).
const assistantShownName = () => assistantName();
const namedRoster = rows => rows.map(e => e.name === assistantBot() ? {...e, display_name: assistantShownName()} : e);
// Small compatibility hook for separately loaded UI modules. It exposes only deployment mode,
// never identity or credentials.
window.ticoIsCloud = () => !!S.me?.cloud;
// Modules loaded on their own still need the environment's names for anything a person reads.
window.ticoAppName = () => appName();
window.ticoAssistantName = () => assistantName();
