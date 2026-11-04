/* ui/app/boot-snap.js — The last screen, shown again the moment the same person opens the app, until the new one is ready
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What #main showed once it settled (boot-timing.js bootPaintWatch's test: content, none of its loading rows), kept in
// this browser's IndexedDB: one entry per org (this origin), naming whose screen it is (the org's owner, the person's
// id and email) and the bundle it was drawn by. The next open reads it alongside the boot reads and shows it as soon
// as /me answers as that same person, before the roster and the page's own read; until /me answers nothing is shown,
// so another person signed in here never sees it.
// Only the read-mostly pages are kept (snapRouteOk): Home, Overview, the task list, the threads list and a team's
// page. Never Settings, keys, sign-ins, connections, setup, mail, a task or a chat. What is kept holds no frames,
// media, dialogs or scripts, nothing typed (no field's value, no textarea or editable text, no password field), nothing
// marked data-secret, and no code or pre block inside a settings, secret, credential or token card.
// The screen is shown in a shadow root over #main's box: its ids, labels and radio groups live there, out of the
// page's own id space, so `$('#main')` and getElementById only ever find the real page; the host is aria-hidden,
// inert and takes no pointer. route() draws the page into #main as always, hidden under it, and the copy goes the
// moment that page settles, the person moves, the wait ends (boot-timing.js) or SNAP_SHOW_MAX_MS after it was shown,
// whatever boot is still waiting on (the page's loading rows then take its place, never a blank page).
// The entry goes, and a copy on screen with it, when this browser signs out (a sign-out link, or a lapsed session;
// nothing is kept again in that page),
// when /me answers as anyone else or no one, and when the bundle or SNAP_SCHEMA changes; a screen over
// SNAP_MAX_BYTES is not kept.
// Under automation (navigator.webdriver) it is off unless the page sets window.ticoBootSnap = true first, so a browser
// test that loads a page twice never meets a copy it did not ask for.
const SNAP_DB = 'tico-boot', SNAP_STORE = 'screens', SNAP_SCHEMA = 2;
const SNAP_MAX_BYTES = 256 * 1024;
// A new page settles this long after the last address change before it is kept; one still loading is tried again.
const SNAP_SETTLE_MS = 1500, SNAP_SAVE_TRIES = 8;
// The longest a kept screen stays up, from the moment it is shown.
const SNAP_SHOW_MAX_MS = 12000;
// `banned`: signed out since this page loaded, in this tab or any other of this org's (SNAP_OUT_KEY, and SNAP_CHANNEL
// to tell the open ones at once). Nothing is kept again until a new load's /me names a person, whatever answer (a late
// roster, a timer, pagehide) comes first. `loaded` is before this page's /me was asked: a sign-out after it means
// that answer may be stale.
const SNAP = {db: null, who: '', shown: '', done: false, copy: null, timer: 0, watch: 0, banned: false, loaded: Date.now()};
const SNAP_OUT_KEY = 'tico-boot-signed-out', SNAP_CHANNEL = 'tico-boot';
const snapChannel = (() => { try { return new BroadcastChannel(SNAP_CHANNEL); } catch { return null; } })();
// Signed out anywhere since this page loaded: it is banned too, and drops whose screen it was.
function snapSignedOut() {
  if (!SNAP.banned) {
    let at = 0;
    try { at = Number(localStorage.getItem(SNAP_OUT_KEY)) || 0; } catch { /* no storage: this tab's own ban only */ }
    if (at >= SNAP.loaded) SNAP.banned = true;
  }
  if (SNAP.banned) SNAP.who = '';
  return SNAP.banned;
}
const snapOn = () => { try { return typeof indexedDB !== 'undefined' && (!navigator.webdriver || window.ticoBootSnap === true); } catch { return false; } };
// The pages kept: Home, Overview, Tasks (its list, any view), Threads (the list) and a team's page.
function snapRouteOk(hash) {
  const [page] = String(hash || '').split('?');
  if ([HOME, OVERVIEW, TASKS, THREADS_ROUTE].includes(page)) return true;
  const team = page.startsWith(TEAM_PAGE) ? page.slice(TEAM_PAGE.length) : '';
  return !!team && !team.includes('/');
}
// The bundle that draws the page (its ?v=); the separate files of a development checkout have none.
const SNAP_VERSION = (() => {
  const js = document.querySelector('script[src*="/tico/ui/app.bundle.js"]');
  try { return SNAP_SCHEMA + ':' + (js ? new URL(js.src, location.href).searchParams.get('v') || '' : 'files'); }
  catch { return SNAP_SCHEMA + ':'; }
})();
const snapWho = me => JSON.stringify([location.origin, String(me?.owner_id || ''), String(me?.id || ''), String(me?.email || '')]);

function snapDb() {
  if (SNAP.db) return SNAP.db;
  const open = new Promise((resolve, reject) => {
    const req = indexedDB.open(SNAP_DB, SNAP_SCHEMA);
    // A new schema starts empty: whatever an older one kept is dropped with its store.
    req.onupgradeneeded = () => {
      const db = req.result;
      for (const name of [...db.objectStoreNames]) db.deleteObjectStore(name);
      db.createObjectStore(SNAP_STORE);
    };
    req.onsuccess = () => {
      const db = req.result;
      // Made by someone else at this version without the store (a script poking at it): start again next time.
      if (!db.objectStoreNames.contains(SNAP_STORE)) { db.close(); indexedDB.deleteDatabase(SNAP_DB); reject(new Error('no store')); return; }
      db.onversionchange = () => { db.close(); SNAP.db = null; };
      resolve(db);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () => reject(new Error('blocked'));
  });
  SNAP.db = open;
  open.catch(() => { if (SNAP.db === open) SNAP.db = null; });
  return open;
}
// One request in its own transaction; resolves when the transaction is done.
function snapTx(mode, work) {
  return snapDb().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(SNAP_STORE, mode), req = work(tx.objectStore(SNAP_STORE));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = tx.onabort = () => reject(tx.error);
  }));
}
const snapRead = () => snapOn() ? snapTx('readonly', s => s.get(location.origin)).catch(() => null) : Promise.resolve(null);
const snapForget = () => snapOn() ? snapTx('readwrite', s => s.delete(location.origin)).catch(() => {}) : Promise.resolve();
// Signed out, or someone else: nothing more is kept in this page, a copy on screen goes now, and what was kept goes.
function bootSnapDrop() {
  SNAP.who = ''; SNAP.done = true; SNAP.banned = true;
  clearTimeout(SNAP.timer);
  bootSnapRelease();
  try { localStorage.setItem(SNAP_OUT_KEY, String(Date.now())); } catch { /* the channel still tells the open tabs */ }
  try { snapChannel?.postMessage({signedOut: true}); } catch { /* closed */ }
  return snapForget();
}

// At boot, with the /me read already on its way: the kept screen, once /me says it is this person's.
function bootSnapShow(meRead) {
  if (!snapOn()) return;
  void Promise.all([snapRead(), meRead.catch(() => null)]).then(([kept, me]) => {
    // No answer (offline): shown later, or not at all. Signed out since this page loaded: never.
    if (!kept || !me || SNAP.done || SESSION.out || snapSignedOut()) return;
    if (!authenticatedConfigPerson(me) || kept.who !== snapWho(me) || kept.version !== SNAP_VERSION) { void snapForget(); return; }
    const main = document.getElementById('main');
    if (kept.route !== location.hash || !snapRouteOk(kept.route) || !main || main.childElementCount || typeof kept.html !== 'string') return;
    snapPaint(main, kept);
  });
}
// The copy: a host over #main's box holding, in its own shadow root, the page's stylesheets and a <main id="main"> with
// the kept screen, so the page's #main rules draw it as it was. It shows once those stylesheets are in (from the cache).
function snapPaint(main, kept) {
  const box = main.getBoundingClientRect(), host = document.createElement('div');
  host.dataset.bootSnap = '';
  host.setAttribute('aria-hidden', 'true');
  host.inert = true;
  Object.assign(host.style, {position: 'fixed', left: box.left + 'px', top: box.top + 'px', width: box.width + 'px',
    height: (box.height || Math.max(0, innerHeight - box.top)) + 'px', margin: '0', zIndex: '5', pointerEvents: 'none',
    overflow: 'hidden', visibility: 'hidden'});
  const root = host.attachShadow({mode: 'open'});
  const sheets = [...document.querySelectorAll('link[rel="stylesheet"], style')].map(el => el.cloneNode(true));
  const screen = document.createElement('main');
  screen.id = 'main';
  screen.className = typeof kept.cls === 'string' ? kept.cls : '';
  screen.style.cssText = 'height:100%;overflow:hidden;margin:0';
  screen.innerHTML = kept.html;
  root.append(...sheets, screen);
  main.after(host);
  main.style.visibility = 'hidden';
  SNAP.copy = host; SNAP.shown = kept.route;
  clearTimeout(SNAP.watch);
  SNAP.watch = setTimeout(snapWaited, SNAP_SHOW_MAX_MS);
  const loads = sheets.filter(el => el.tagName === 'LINK').map(el => new Promise(done => { el.onload = el.onerror = done; }));
  void Promise.race([Promise.all(loads), new Promise(done => setTimeout(done, 400))]).then(() => {
    if (SNAP.copy !== host) return;
    host.style.visibility = '';
    bootMark('paint-cached');
  });
  // Any move of the person's own (another page, Back) shows that page as it draws.
  window.addEventListener('hashchange', bootSnapRelease, {once: true});
}
// The copy was up as long as it may be and boot is still waiting (a stalled roster): the page shows its own loading
// rows in its place, never a blank page, and route() draws over them when boot gets there.
function snapWaited() {
  const main = document.getElementById('main');
  if (SNAP.copy && main && !main.childElementCount) main.innerHTML = skelRows(6);
  bootSnapRelease();
}
// Just before route(): a late read of the kept screen no longer draws, and boot opening another page (a first run)
// takes the copy down at once.
function bootSnapHandOff() {
  SNAP.done = true;
  if (SNAP.copy && SNAP.shown !== location.hash) bootSnapRelease();
}
// The new page settled, the wait ended, the person moved on or signed out: the copy goes and the page shows.
function bootSnapRelease() {
  SNAP.done = true;
  clearTimeout(SNAP.watch);
  const copy = SNAP.copy;
  SNAP.copy = null; SNAP.shown = '';
  if (!copy) return;
  copy.remove();
  const main = document.getElementById('main');
  if (main) main.style.visibility = '';
}

// After boot knows who is signed in: from now on the screen is kept as it settles. Anyone else's goes at once.
function bootSnapWho(me) {
  const who = authenticatedConfigPerson(me) ? snapWho(me) : '';
  if (!who) { void bootSnapDrop(); return; }
  if (!snapSignedOut()) SNAP.who = who;
}
// What a kept screen may never hold: anything that runs or loads on its own, and anything private.
const SNAP_DROP = 'iframe,frame,object,embed,video,audio,canvas,script,noscript,dialog,template,img[src^="blob:"],'
  + 'input[type="password"],[data-secret],'
  + ':is([class*="setting"],[class*="secret"],[class*="credential"],[class*="token"],[class*="api-key"]) :is(pre,code)';
// The screen as it is now, if it settled and is one kept; null when it cannot be kept yet.
function snapScreen() {
  const main = document.getElementById('main');
  if (!main || SNAP.copy || !snapRouteOk(location.hash) || !main.childElementCount || main.querySelector(BOOT_SKELETONS)) return null;
  const copy = main.cloneNode(true);
  copy.querySelectorAll(SNAP_DROP).forEach(el => el.remove());
  copy.querySelectorAll('[autofocus]').forEach(el => el.removeAttribute('autofocus'));
  copy.querySelectorAll('input[value]').forEach(el => el.removeAttribute('value'));
  copy.querySelectorAll('textarea, [contenteditable]:not([contenteditable="false"])').forEach(el => el.replaceChildren());
  const html = copy.innerHTML;
  // UTF-8 is never shorter than its UTF-16 length, so the length alone rules most big screens out.
  if (html.length > SNAP_MAX_BYTES || new TextEncoder().encode(html).length > SNAP_MAX_BYTES) return {html: null};
  return {html, cls: main.className};
}
// Keep the screen now: on leaving (pagehide, hidden), and after a page settles. One still loading is tried again a
// few times; a page that is never kept leaves the older entry, which shows only on its own address.
function bootSnapSave(tries = 0) {
  clearTimeout(SNAP.timer);
  if (!snapOn() || snapSignedOut() || !SNAP.who || SESSION.out) return;
  const screen = snapScreen();
  if (!screen) {
    if (tries && tries < SNAP_SAVE_TRIES) SNAP.timer = setTimeout(() => bootSnapSave(tries + 1), SNAP_SETTLE_MS);
    return;
  }
  const who = SNAP.who;
  // A screen too big to keep takes the older one with it: the next open must not show a page from before it.
  if (screen.html === null) { void snapForget(); return; }
  const entry = {who, version: SNAP_VERSION, route: location.hash, cls: screen.cls, html: screen.html, at: Date.now()};
  void snapTx('readwrite', s => s.put(entry, location.origin)).catch(() => {});
}
function bootSnapSaveSoon() {
  clearTimeout(SNAP.timer);
  SNAP.timer = setTimeout(() => bootSnapSave(1), SNAP_SETTLE_MS);
}
// Another tab signed out: this one keeps nothing more, and a copy on screen goes.
if (snapChannel) snapChannel.onmessage = e => {
  if (!e.data?.signedOut) return;
  SNAP.banned = true; SNAP.who = ''; clearTimeout(SNAP.timer);
  bootSnapRelease();
};
window.addEventListener('hashchange', bootSnapSaveSoon);
document.addEventListener('visibilitychange', () => { if (document.hidden) bootSnapSave(); });
window.addEventListener('pagehide', () => bootSnapSave());
// Signing out leaves no screen behind, as decide.js leaves no unsent action (the same links).
document.addEventListener('click', e => {
  const link = e.target.closest?.('a[href]');
  if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href'))) void bootSnapDrop();
}, true);
