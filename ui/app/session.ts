/* ui/app/session.ts — Signed out: one banner and no more background retries, for every way of signing in
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A sign-in ends in one of three ways, and all three land here. Built-in sign-in (OIDC) answers a 401 that names its
// login page. Tico's own check of a proxy's assertion answers a 401 `identity`. A proxy in front of Tico (Cloudflare
// Access) answers a lapsed session itself, before Tico sees the request: a 302 to its login page on another origin,
// which fetch follows into a CORS failure and EventSource into an error, both looking like a dropped network; or a 401
// with `WWW-Authenticate: Cloudflare-Access`. So a request that fails as a network error asks GET /v2/me once without
// following redirects (sessionProbe): a redirect there, or a 401, is signed out; no answer at all is offline, and the
// page keeps reconnecting as before.
/** The task that was open when the page was signed out (SESSION.reopen). */
interface SessionTaskRef { id: string; thread?: boolean; }
/** One text kept for the sign-in: the box it came from (scope and key) and what was in it. */
interface SessionField { scope: string; key: string; value: string; base?: TaskFieldBase; }
/** What the page had when it was signed out (SESSION_RETURN, sessionKeep). */
interface SessionKept { identity: string; at: number; hash: string; task: SessionTaskRef | null; fields: SessionField[]; }
/** A box the page keeps text in: a text field, a text area, or a contenteditable task field. */
type SessionBox = HTMLElement & { name?: string; placeholder?: string; readOnly?: boolean; disabled?: boolean; value?: string };
interface SessionState {
  out: boolean; seen: boolean; probe: Promise<boolean> | null; probedAt: number; login: string; bar: HTMLElement | null;
  reopen: SessionTaskRef | null; restoring: SessionKept | null; identity: string; watch: MutationObserver | null; discarded: boolean;
}
const SESSION: SessionState = {out: false, seen: false, probe: null, probedAt: 0, login: '', bar: null, reopen: null, restoring: null, identity: '', watch: null, discarded: false};
// What the page had when it was signed out (where it was, the text in its boxes), kept for this tab while the
// person signs in again on another site, and put back when they return (sessionReturn).
const SESSION_RETURN = 'tico.signin.return';
// The authenticated config's environment id names the org; local unnamed installs use their origin.
function sessionIdentity() {
  if (typeof S === 'undefined' || !S.me?.id) return '';
  return JSON.stringify([String(S.config.environment_id || location.origin), String(S.me.id)]);
}

const signedOutError = (write?: boolean) => Object.assign(new Error(write ? "Not saved: you've been signed out. Sign in again, then try again."
  : "You've been signed out. Sign in again to continue."), {signedOut: true});
const sessionHere = () => location.pathname + location.search + location.hash;
const loginPage = (to: unknown): string => typeof to === 'string' && to.startsWith('/auth/') ? to : '';
// Is this answer "signed out"? `j` is its parsed body, when it has one.
function sessionLapsed(r: Response, j?: ApiFailureBody): boolean {
  if (r.type === 'opaqueredirect') return true;   // only GET /v2/me asks without following, and it never redirects itself
  if (r.status !== 401) return false;
  const err = typeof j?.error === 'object' ? j.error : undefined;
  return /cloudflare-access/i.test(r.headers.get('WWW-Authenticate') || '') || err?.code === 'identity' || !!loginPage(err?.sign_in);
}
// A failed answer from any request: signed out, or an ordinary refusal. Returns true when signed out.
function sessionCheck(r: Response, j?: ApiFailureBody): boolean {
  if (!sessionLapsed(r, j)) return false;
  const err = typeof j?.error === 'object' ? j.error : undefined;
  signedOut(err?.sign_in);
  return true;
}
// After a network error: are we offline, or signed out by a proxy? One question at a time, and at most every few
// seconds while the answer is "not signed out" (a server that is down fails every request). A question with no answer
// within a few seconds counts as offline: the stream goes on reconnecting meanwhile (live.js liveAfterProbe) and a
// write waits for it, so it never hangs on a stalled connection; and a late answer never clears a newer question.
const SESSION_PROBE_MS = 5000;
function sessionProbe(): Promise<boolean> {
  if (SESSION.out) return Promise.resolve(true);
  if (SESSION.probe) return SESSION.probe;
  if (navigator.onLine === false || Date.now() - SESSION.probedAt < 3000) return Promise.resolve(false);
  const stop = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const probe: Promise<boolean> = Promise.race([
    fetch(API + '/v2/me', {cache: 'no-store', redirect: 'manual', signal: stop.signal})
      .then(async r => sessionCheck(r, r.status === 401 ? await r.json().catch(() => ({})) : {}), () => false),
    new Promise<boolean>(done => { timer = setTimeout(() => { stop.abort(); done(false); }, SESSION_PROBE_MS); }),
  ]).finally(() => {
    clearTimeout(timer);
    if (SESSION.probe === probe) { SESSION.probe = null; SESSION.probedAt = Date.now(); }
  });
  return SESSION.probe = probe;
}
// A page that has not yet loaded anything goes straight to the built-in login page (nothing on it to lose);
// otherwise the banner says so and waits for the person.
function signedOut(to: unknown) {
  if (typeof clearNativeVisualOrigin === 'function') void clearNativeVisualOrigin();
  if (typeof bootSnapDrop === 'function') void bootSnapDrop();      // no screen of theirs is shown again here
  const login = loginPage(to);
  // The demo has no sign-in to ask for: its first page visit is the sign-in, and its secret is new with every copy
  // (deploy/demo/tico-demo). A tab left open across a new copy loads again, at most once a minute.
  if (!login && typeof S !== 'undefined' && S.config?.demo && demoReloadOnce()) return;
  if (login && !SESSION.seen) {
    sessionKeep();
    if (typeof taskLiveSuspend === 'function') taskLiveSuspend();
    location.assign(login + '?next=' + encodeURIComponent(sessionHere()));
    return;
  }
  if (SESSION.out) return;
  SESSION.watch?.disconnect(); SESSION.watch = null;
  SESSION.out = true; SESSION.login = login;
  sessionKeep();
  if (typeof taskLiveSuspend === 'function') taskLiveSuspend();
  if (typeof liveSignedOut === 'function') liveSignedOut();
  document.querySelectorAll('.toast.err').forEach(t => t.remove());
  // Focus stays where it was: someone typing goes on typing, and their text is kept either way.
  const bar = SESSION.bar = document.getElementById('signed-out');
  if (!bar) return;
  bar.hidden = false;
  sessionBarPlace();
  new MutationObserver(records => {
    for (const m of records) if (m.type === 'attributes' && m.target instanceof HTMLDialogElement && m.target.open) { SESSION_MODALS.delete(m.target); SESSION_MODALS.add(m.target); }
    sessionBarPlace();
  }).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['open']});
}
function demoReloadOnce(): boolean {
  const key = 'tico.demo.reloaded', now = Date.now();
  try {
    if (now - Number(sessionStorage.getItem(key) || 0) < 60000) return false;
    sessionStorage.setItem(key, String(now));
  } catch { return false; }
  sessionKeep();
  location.reload();
  return true;
}
// A modal dialog (a task, a settings form, the phone's task sheet) sits above the page and leaves everything outside it
// unclickable and out of Tab's reach, the banner too. So while one is open the banner goes inside the topmost one, first
// in its Tab order, and the work in it stays as it was; it moves back as the dialogs close, and back in if a dialog
// redraws over it.
const SESSION_MODALS = new Set<HTMLDialogElement>();         // dialogs in the order they opened, the newest last
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
const sessionKey = (el: SessionBox): string => el.id || el.name || el.getAttribute('aria-label') || el.placeholder || el.dataset.taskField || '';
const sessionScope = (el: SessionBox): string => {
  const task = el.closest<HTMLElement>('[data-task]')?.dataset.task, d = el.closest('dialog');
  return task ? 'task:' + task : location.hash + (d ? '|' + (d.id || d.getAttribute('aria-label') || '') : '');
};
// A bot's or the Assistant's chat box keeps its own draft (pill.js chatDraftSave, by slug) and clears it once the
// message is sent; kept here too, its text came back into the emptied box after sign-in, ready to send a second time.
// A chat box with no slug has no draft of its own and stays kept here. In the bundled UI this file runs before
// pill.js declares PILLS, so reading it during boot throws rather than being undefined.
const chatBox = (el: Element): boolean => {
  let pills; try { pills = PILLS; } catch { return false; }
  return [...pills].some(P => P.mode === 'chat' && P.slug && P.el?.contains(el));
};
const sessionBoxValue = (el: SessionBox): string => (el.hasAttribute('contenteditable') ? el.textContent : el.value) ?? '';
const sessionBoxes = (): SessionBox[] => [...document.querySelectorAll<SessionBox>('textarea, input[type="text"], input[data-task-field], [contenteditable][data-task-field]')]
  .filter(el => !el.readOnly && !el.disabled && !/search|filter/i.test(sessionKey(el)) && !chatBox(el));
const sessionSave = (kept: SessionKept) => {
  try {
    if (kept.fields.length || kept.task || kept.hash) sessionStorage.setItem(SESSION_RETURN, JSON.stringify(kept));
    else sessionStorage.removeItem(SESSION_RETURN);
  } catch {}
};
// A confirmed send consumes every saved version of its own box, including the live return watcher copy.
// Capture the scope before sending: the person can visit another page while the request is out.
function sessionAcknowledge(el: SessionBox, scope = sessionScope(el)) {
  const key = sessionKey(el);
  const consume = (kept: SessionKept | null) => {
    if (!kept?.fields) return;
    kept.fields = kept.fields.filter(f => f.scope !== scope || f.key !== key);
  };
  consume(SESSION.restoring);
  try {
    const kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null');
    if (kept) { consume(kept); sessionSave(kept); }
  } catch {}
}
function sessionKeep() {
  if (SESSION.discarded) return;
  // A bot's chat box keeps its own draft per bot (pill.js chatDraftSave), across reloads too.
  for (const P of [typeof BOT_PILL !== 'undefined' && BOT_PILL, typeof ASSISTANT_PILL !== 'undefined' && ASSISTANT_PILL])
    if (P) try { chatDraftSave(P); } catch {}
  const identity = sessionIdentity();
  if (!identity) return;
  const fields = new Map<string, SessionField>();
  // Texts from an earlier sign-in that have not found their box yet stay kept.
  try {
    const kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null');
    if (kept?.identity === identity) for (const f of kept.fields || []) fields.set(f.scope + '\n' + f.key, f);
  } catch {}
  // A task's comment box keeps its draft only while the page lives (task-modal.js TASK_DRAFTS), for tasks closed
  // since too.
  if (typeof TASK_DRAFTS !== 'undefined')
    for (const [id, value] of TASK_DRAFTS) if (value.trim()) fields.set(`task:${id}\nMessage`, {scope: 'task:' + id, key: 'Message', value});
  for (const el of sessionBoxes()) {
    const key = sessionKey(el), scope = sessionScope(el);
    const value = sessionBoxValue(el), live = el.closest('dialog')?.taskLive, field = el.dataset.taskField;
    if (field && !(field in (live?.draft.dirty || {}))) continue;
    const base = field && live ? live.draft.bases[field] || live.fields[field] : undefined;
    if (key && (value.trim() || field)) fields.set(scope + '\n' + key, {scope, key, value, ...(base ? {base} : {})});
  }
  // The task open in full, to open again on return (sessionResume).
  const d = document.getElementById('task-modal');
  const task = d instanceof HTMLDialogElement && d.open && d.dataset.task ? {id: d.dataset.task, thread: d.classList.contains('thread')} : null;
  sessionSave({identity, at: Date.now(), hash: location.hash, task, fields: [...fields.values()]});
}
// Back from signing in: the same place and the task that was open, and each text put back into its own empty box as
// the page draws it, however late (a task opened an hour on). Each text stays kept for this tab until it is back in its box.
function sessionReturn() {
  const identity = sessionIdentity();
  SESSION.watch?.disconnect(); SESSION.watch = null;
  taskLiveIdentity(identity);
  SESSION.identity = identity;
  if (typeof chatIdentityRestore === 'function') chatIdentityRestore();   // bot drafts and pending sends (pill.ts)
  SESSION.restoring = null; SESSION.reopen = null;
  let kept: SessionKept | null = null;
  try { kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null'); } catch {}
  if (!identity || !kept || kept.identity !== identity || !(Date.now() - kept.at < 86400000)) { try { sessionStorage.removeItem(SESSION_RETURN); } catch {} return; }
  // A proxy's login sends the browser back to the address without its #, which never reached the server.
  if (kept.hash && (!location.hash || location.hash === '#' || BOOT_DEFAULT_ROUTE)) {
    history.replaceState(null, '', location.pathname + location.search + kept.hash);
    BOOT_DEFAULT_ROUTE = false;
  }
  if (kept.task && typeof kept.task.id === 'string') SESSION.reopen = kept.task;
  kept = {identity, at: kept.at, hash: '', task: null,
    fields: (kept.fields || []).filter(f => f && typeof f.scope === 'string' && typeof f.key === 'string' && typeof f.value === 'string')};
  SESSION.restoring = kept;
  sessionSave(kept);
  if (!kept.fields.length) return;
  const fill = (): boolean => {
    if (SESSION.out || SESSION.restoring !== kept || sessionIdentity() !== identity) return true;
    if (!kept) return true;   // set above and never cleared; the closure cannot see that narrowing
    let put = false;
    for (const el of sessionBoxes()) {
      const field = el.dataset.taskField, live = el.closest('dialog')?.taskLive;
      if (sessionBoxValue(el) && !field) continue;
      const key = sessionKey(el), scope = sessionScope(el);
      const i = kept.fields.findIndex(f => f.key === key && f.scope === scope);
      if (i < 0 || (field && !live)) continue;
      const saved = kept.fields[i];
      if (!saved) continue;   // i was just found in kept.fields
      if (!field || !live || !(field in live.draft.dirty)) {
        if (live && field && saved.base && Number.isFinite(saved.base.version) && (typeof saved.base.value === 'string' || field === 'size' && (saved.base.value === null || Number.isFinite(saved.base.value)))) live.fields[field] = saved.base;
        if (el.hasAttribute('contenteditable')) el.textContent = saved.value; else el.value = saved.value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        if (field) {
          // live came from this box's dialog; without it the old call threw on live.draft (taskLiveSchedule)
          if (!live) throw new TypeError('This task field has no live task');
          taskLiveSchedule(el.closest<TaskDialog>('dialog')!, live);
        }
      }
      kept.fields.splice(i, 1); put = true;
    }
    if (put) sessionSave(kept);
    return !kept.fields.length;
  };
  // At most once a frame, however busy the page is (a reply streaming in).
  let soon = 0;
  const watch = SESSION.watch = new MutationObserver(() => { soon ||= requestAnimationFrame(() => { soon = 0; if (fill()) watch.disconnect(); }); });
  const start = () => { if (!fill()) watch.observe(document.body, {childList: true, subtree: true}); };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once: true});
}
// Called once the first page is drawn (boot.js): the task that was open opens again over it.
function sessionResume() {
  const t = SESSION.reopen;
  SESSION.reopen = null;
  if (!t || typeof taskModalShow !== 'function') return;
  get('/v2/tasks/' + encodeURIComponent(t.id))
    .then(data => { if (data?.task && !$('#task-modal')?.open) return taskModalShow(data.task, undefined, {thread: !!t.thread, direct: true}); })
    .catch(() => {});
}
document.addEventListener('click', e => { if (e.target instanceof Element && e.target.closest('#signed-out button')) signInAgain(); });
// Reloading by hand while signed out keeps the text too.
window.addEventListener('pagehide', () => { if (SESSION.out) sessionKeep(); });
