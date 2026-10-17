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
const SESSION = {out: false, seen: false, probe: null, probedAt: 0, login: ''};
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
// seconds while the answer is "not signed out" (a server that is down fails every request).
function sessionProbe() {
  if (SESSION.out) return Promise.resolve(true);
  if (SESSION.probe) return SESSION.probe;
  if (navigator.onLine === false || Date.now() - SESSION.probedAt < 3000) return Promise.resolve(false);
  return SESSION.probe = fetch(API + '/v2/me', {cache: 'no-store', redirect: 'manual'})
    .then(async r => sessionCheck(r, r.status === 401 ? await r.json().catch(() => ({})) : {}), () => false)
    .finally(() => { SESSION.probe = null; SESSION.probedAt = Date.now(); });
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
  const bar = document.getElementById('signed-out');
  if (bar) bar.hidden = false;
}
// Sign in again: a top-level visit, so a proxy shows its own login and sends the person back to this address
// (in the Mac app the login opens in its own window, app/src/main.rs stays_in_app, so the cookie stays the app's).
function signInAgain() {
  sessionKeep();
  if (SESSION.login) location.assign(SESSION.login + '?next=' + encodeURIComponent(sessionHere()));
  else location.reload();
}
// The boxes worth keeping, by a key that is the same when the page draws them again.
const sessionKey = el => el.id || el.name || el.getAttribute('aria-label') || el.placeholder || '';
const sessionBoxes = () => [...document.querySelectorAll('textarea, input[type="text"]')]
  .filter(el => !el.readOnly && !el.disabled && !/search|filter/i.test(sessionKey(el)));
function sessionKeep() {
  // A bot's chat box keeps its own draft per bot (pill.js chatDraftSave), across reloads too.
  for (const P of [typeof BOT_PILL !== 'undefined' && BOT_PILL, typeof ASSISTANT_PILL !== 'undefined' && ASSISTANT_PILL])
    if (P) try { chatDraftSave(P); } catch {}
  const fields = sessionBoxes().filter(el => el.value.trim() && sessionKey(el)).map(el => ({key: sessionKey(el), value: el.value}));
  try { sessionStorage.setItem(SESSION_RETURN, JSON.stringify({at: Date.now(), hash: location.hash, fields})); } catch {}
}
// Back from signing in: the same place, and the text put back in each empty box as the page draws it.
(function sessionReturn() {
  let kept = null;
  try { kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null'); sessionStorage.removeItem(SESSION_RETURN); } catch {}
  if (!kept || !(Date.now() - kept.at < 3600000)) return;
  // A proxy's login sends the browser back to the address without its #, which never reached the server.
  if (kept.hash && (!location.hash || location.hash === '#')) history.replaceState(null, '', location.pathname + location.search + kept.hash);
  const fields = (kept.fields || []).filter(f => f && typeof f.key === 'string' && typeof f.value === 'string');
  if (!fields.length || location.hash !== kept.hash) return;
  const fill = () => {
    for (const f of fields) {
      if (f.done) continue;
      const el = sessionBoxes().find(b => sessionKey(b) === f.key && !b.value);
      if (el) { el.value = f.value; el.dispatchEvent(new Event('input', {bubbles: true})); f.done = true; }
    }
    return fields.every(f => f.done);
  };
  const watch = new MutationObserver(() => { if (fill()) watch.disconnect(); });
  const start = () => { if (!fill()) { watch.observe(document.body, {childList: true, subtree: true}); setTimeout(() => watch.disconnect(), 20000); } };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once: true});
})();
document.addEventListener('click', e => { if (e.target.closest?.('#signed-out button')) signInAgain(); });
// Reloading by hand while signed out keeps the text too.
window.addEventListener('pagehide', () => { if (SESSION.out) sessionKeep(); });
