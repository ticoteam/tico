/* ui/app/session.js — Signed out: one banner and no more background retries, for every way of signing in
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A sign-in ends in one of three ways, and all three land here. Built-in sign-in (OIDC) answers a 401 that names its
// login page. Tico's own check of a proxy's assertion answers a 401 `identity`. A proxy in front of Tico (Cloudflare
// Access) answers a lapsed session itself, before Tico sees the request: a 302 to its login page on another origin,
// which fetch follows into a CORS failure and EventSource into an error, both looking like a dropped network; or a 401
// with `WWW-Authenticate: Cloudflare-Access`. So a request that fails as a network error asks GET /v2/me once without
// following redirects (sessionProbe): a redirect there, or a 401, is signed out; no answer at all is offline, and the
// page keeps reconnecting as before.
const SESSION = {out: false, seen: false, probe: null, probedAt: 0, login: '', bar: null, reopen: null};
// What the page had when it was signed out (where it was, the text in its boxes), kept for this tab while the
// person signs in again on another site, and put back when they return (sessionReturn).
const SESSION_RETURN = 'tico.signin.return';
const signedOutError = write => Object.assign(new Error(write ? "Not saved: you've been signed out. Sign in again, then try again."
  : "You've been signed out. Sign in again to continue."), {signedOut: true});
const sessionHere = () => location.pathname + location.search + location.hash;
const loginPage = to => typeof to === 'string' && to.startsWith('/auth/') ? to : '';
// Is this answer "signed out"? `j` is its parsed body, when it has one.
function sessionLapsed(r, j) {
  if (r.type === 'opaqueredirect') return true;   // only GET /v2/me asks without following, and it never redirects itself
  if (r.status !== 401) return false;
  return /cloudflare-access/i.test(r.headers.get('WWW-Authenticate') || '') || j?.error?.code === 'identity' || !!loginPage(j?.error?.sign_in);
}
// A failed answer from any request: signed out, or an ordinary refusal. Returns true when signed out.
function sessionCheck(r, j) {
  if (!sessionLapsed(r, j)) return false;
  signedOut(j?.error?.sign_in);
  return true;
}
// After a network error: are we offline, or signed out by a proxy? One question at a time, and at most every few
// seconds while the answer is "not signed out" (a server that is down fails every request). A question with no answer
// within a few seconds counts as offline: the stream goes on reconnecting meanwhile (live.js liveAfterProbe) and a
// write waits for it, so it never hangs on a stalled connection; and a late answer never clears a newer question.
const SESSION_PROBE_MS = 5000;
function sessionProbe() {
  if (SESSION.out) return Promise.resolve(true);
  if (SESSION.probe) return SESSION.probe;
  if (navigator.onLine === false || Date.now() - SESSION.probedAt < 3000) return Promise.resolve(false);
  const stop = new AbortController();
  let timer;
  const probe = Promise.race([
    fetch(API + '/v2/me', {cache: 'no-store', redirect: 'manual', signal: stop.signal})
      .then(async r => sessionCheck(r, r.status === 401 ? await r.json().catch(() => ({})) : {}), () => false),
    new Promise(done => { timer = setTimeout(() => { stop.abort(); done(false); }, SESSION_PROBE_MS); }),
  ]).finally(() => {
    clearTimeout(timer);
    if (SESSION.probe === probe) { SESSION.probe = null; SESSION.probedAt = Date.now(); }
  });
  return SESSION.probe = probe;
}
// A page that has not yet loaded anything goes straight to the built-in login page (nothing on it to lose);
// otherwise the banner says so and waits for the person.
function signedOut(to) {
  const login = loginPage(to);
  if (login && !SESSION.seen) {
    sessionKeep();
    location.assign(login + '?next=' + encodeURIComponent(sessionHere()));
    return;
  }
  if (SESSION.out) return;
  SESSION.out = true; SESSION.login = login;
  sessionKeep();
  if (typeof liveSignedOut === 'function') liveSignedOut();
  document.querySelectorAll('.toast.err').forEach(t => t.remove());
  // Focus stays where it was: someone typing goes on typing, and their text is kept either way.
  const bar = SESSION.bar = document.getElementById('signed-out');
  if (!bar) return;
  bar.hidden = false;
  sessionBarPlace();
  new MutationObserver(records => {
    for (const m of records) if (m.type === 'attributes' && m.target.open) { SESSION_MODALS.delete(m.target); SESSION_MODALS.add(m.target); }
    sessionBarPlace();
  }).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['open']});
}
// A modal dialog (a task, a settings form, the phone's task sheet) sits above the page and leaves everything outside it
// unclickable and out of Tab's reach, the banner too. So while one is open the banner goes inside the topmost one, first
// in its Tab order, and the work in it stays as it was; it moves back as the dialogs close, and back in if a dialog
// redraws over it.
const SESSION_MODALS = new Set();         // dialogs in the order they opened, the newest last
function sessionBarPlace() {
  const bar = SESSION.bar;
  if (!bar || !SESSION.out) return;
  const open = [...document.querySelectorAll('dialog')].filter(d => d.matches(':modal'));
  const top = [...SESSION_MODALS].filter(d => open.includes(d)).pop() || open.pop();
  if (top ? top.firstChild !== bar : bar.parentNode !== document.body) (top || document.body).prepend(bar);
}
// Sign in again: a top-level visit, so a proxy shows its own login and sends the person back to this address
// (in the Mac app the login opens in its own window, app/src/main.rs stays_in_app, so the cookie stays the app's).
function signInAgain() {
  sessionKeep();
  if (SESSION.login) location.assign(SESSION.login + '?next=' + encodeURIComponent(sessionHere()));
  else location.reload();
}
// The boxes worth keeping, by a key that is the same when the page draws them again, and whose they are: a box in a
// task (its comment box, an answer) is that task's, any other the page's (and its dialog's), so text goes back only into
// the same box of the same task or page, never into another task's.
const sessionKey = el => el.id || el.name || el.getAttribute('aria-label') || el.placeholder || '';
const sessionScope = el => {
  const task = el.closest('[data-task]')?.dataset.task, d = el.closest('dialog');
  return task ? 'task:' + task : location.hash + (d ? '|' + (d.id || d.getAttribute('aria-label') || '') : '');
};
const sessionBoxes = () => [...document.querySelectorAll('textarea, input[type="text"]')]
  .filter(el => !el.readOnly && !el.disabled && !/search|filter/i.test(sessionKey(el)));
const sessionSave = kept => {
  try {
    if (kept.fields.length || kept.task || kept.hash) sessionStorage.setItem(SESSION_RETURN, JSON.stringify(kept));
    else sessionStorage.removeItem(SESSION_RETURN);
  } catch {}
};
function sessionKeep() {
  // A bot's chat box keeps its own draft per bot (pill.js chatDraftSave), across reloads too.
  for (const P of [typeof BOT_PILL !== 'undefined' && BOT_PILL, typeof ASSISTANT_PILL !== 'undefined' && ASSISTANT_PILL])
    if (P) try { chatDraftSave(P); } catch {}
  const fields = new Map();
  // Texts from an earlier sign-in that have not found their box yet stay kept.
  try { for (const f of JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null')?.fields || []) fields.set(f.scope + '\n' + f.key, f); } catch {}
  // A task's comment box keeps its draft only while the page lives (task-modal.js TASK_DRAFTS), for tasks closed
  // since too.
  if (typeof TASK_DRAFTS !== 'undefined')
    for (const [id, value] of TASK_DRAFTS) if (value.trim()) fields.set(`task:${id}\nMessage`, {scope: 'task:' + id, key: 'Message', value});
  for (const el of sessionBoxes()) {
    const key = sessionKey(el), scope = sessionScope(el);
    if (key && el.value.trim()) fields.set(scope + '\n' + key, {scope, key, value: el.value});
  }
  // The task open in full, to open again on return (sessionResume).
  const d = document.getElementById('task-modal');
  const task = d?.open && d.dataset.task ? {id: d.dataset.task, thread: d.classList.contains('thread')} : null;
  sessionSave({at: Date.now(), hash: location.hash, task, fields: [...fields.values()]});
}
// Back from signing in: the same place and the task that was open, and each text put back into its own empty box as
// the page draws it, however late (a task opened an hour on). Each text stays kept for this tab until it is back in its box.
(function sessionReturn() {
  let kept = null;
  try { kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null'); } catch {}
  if (!kept || !(Date.now() - kept.at < 86400000)) { try { sessionStorage.removeItem(SESSION_RETURN); } catch {} return; }
  // A proxy's login sends the browser back to the address without its #, which never reached the server.
  if (kept.hash && (!location.hash || location.hash === '#')) history.replaceState(null, '', location.pathname + location.search + kept.hash);
  if (kept.task && typeof kept.task.id === 'string') SESSION.reopen = kept.task;
  kept = {at: kept.at, hash: '', task: null,
    fields: (kept.fields || []).filter(f => f && typeof f.scope === 'string' && typeof f.key === 'string' && typeof f.value === 'string')};
  sessionSave(kept);
  if (!kept.fields.length) return;
  const fill = () => {
    let put = false;
    for (const el of sessionBoxes()) {
      if (el.value) continue;
      const key = sessionKey(el), scope = sessionScope(el), i = kept.fields.findIndex(f => f.key === key && f.scope === scope);
      if (i < 0) continue;
      el.value = kept.fields[i].value; el.dispatchEvent(new Event('input', {bubbles: true}));
      kept.fields.splice(i, 1); put = true;
    }
    if (put) sessionSave(kept);
    return !kept.fields.length;
  };
  // At most once a frame, however busy the page is (a reply streaming in).
  let soon = 0;
  const watch = new MutationObserver(() => { soon ||= requestAnimationFrame(() => { soon = 0; if (fill()) watch.disconnect(); }); });
  const start = () => { if (!fill()) watch.observe(document.body, {childList: true, subtree: true}); };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once: true});
})();
// Called once the first page is drawn (boot.js): the task that was open opens again over it.
function sessionResume() {
  const t = SESSION.reopen;
  SESSION.reopen = null;
  if (!t || typeof taskModalShow !== 'function') return;
  get('/v2/tasks/' + encodeURIComponent(t.id))
    .then(data => { if (data?.task && !$('#task-modal')?.open) return taskModalShow(data.task, taskModal(), {thread: !!t.thread}); })
    .catch(() => {});
}
document.addEventListener('click', e => { if (e.target.closest?.('#signed-out button')) signInAgain(); });
// Reloading by hand while signed out keeps the text too.
window.addEventListener('pagehide', () => { if (SESSION.out) sessionKeep(); });
