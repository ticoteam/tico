/* ui/app/boot-snap.js — The last screen, shown again the moment the same person opens the app, until the new one is ready
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What #main showed once it settled (boot-timing.js bootPaintWatch's test: content, no skeleton), kept in this browser's
// IndexedDB: one entry per org (this origin), naming whose screen it is (the org's owner, the person's id and email) and
// the bundle it was drawn by. The next open reads it alongside the boot reads and shows it as soon as /me answers as
// that same person, before the roster and the page's own read; until /me answers nothing is shown, so another person
// signed in here never sees it. route() then draws the page as always, hidden under a still copy of that screen, and
// the copy goes the moment the new page settles (or after boot-timing's wait). The copy is inert: nothing on it
// can be clicked or focused, and it holds no frames, media or dialogs (a visual frame still waits for the desktop
// app's ACK), and no typed text (innerHTML carries no input's value).
// The entry goes when this browser signs out (a sign-out link, or a lapsed session), when /me answers as anyone else
// or no one, and when the bundle or SNAP_SCHEMA changes; a screen over SNAP_MAX_BYTES is not kept.
// Under automation (navigator.webdriver) it is off unless the page sets window.ticoBootSnap = true first: a browser
// test that loads a page twice would otherwise meet the copy, a second #main, on its second load.
const SNAP_DB = 'tico-boot', SNAP_STORE = 'screens', SNAP_SCHEMA = 1;
const SNAP_MAX_BYTES = 256 * 1024;
// A new page settles this long after the last address change before it is kept.
const SNAP_SETTLE_MS = 1500;
const SNAP = {db: null, who: '', shown: '', done: false, copy: null, timer: 0};
const snapOn = () => { try { return typeof indexedDB !== 'undefined' && (!navigator.webdriver || window.ticoBootSnap === true); } catch { return false; } };
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
// Signed out: nothing more is kept in this page, and what was kept goes (session.js signedOut, the sign-out links).
function bootSnapDrop() {
  SNAP.who = '';
  return snapForget();
}

// At boot, with the /me read already on its way: the kept screen, once /me says it is this person's.
function bootSnapShow(meRead) {
  if (!snapOn()) return;
  void Promise.all([snapRead(), meRead.catch(() => null)]).then(([kept, me]) => {
    if (!kept || !me || SNAP.done || SESSION.out) return;     // no answer (offline): shown later, or not at all
    if (!authenticatedConfigPerson(me) || kept.who !== snapWho(me) || kept.version !== SNAP_VERSION) { void snapForget(); return; }
    const main = document.getElementById('main');
    if (kept.route !== location.hash || !main || main.childElementCount || typeof kept.html !== 'string') return;
    main.className = typeof kept.cls === 'string' ? kept.cls : '';
    main.innerHTML = kept.html;
    main.inert = true;
    main.setAttribute('aria-busy', 'true');
    SNAP.shown = kept.route;
    bootMark('paint-cached');
  });
}
// Just before route(): the kept screen, if it is up, becomes a still copy over #main while route() draws under it.
// A late read of the kept screen no longer draws. Boot opening another page (a first run) takes it down at once.
function bootSnapHandOff() {
  SNAP.done = true;
  const main = document.getElementById('main');
  if (!SNAP.shown || !main) return;
  if (SNAP.shown !== location.hash) { bootSnapClear(main); return; }
  const box = main.getBoundingClientRect(), copy = main.cloneNode(true);
  copy.setAttribute('aria-hidden', 'true');
  copy.dataset.bootSnap = '';
  Object.assign(copy.style, {position: 'fixed', left: box.left + 'px', top: box.top + 'px', width: box.width + 'px',
    height: box.height + 'px', margin: '0', zIndex: '5', pointerEvents: 'none', overflow: 'hidden'});
  main.after(copy);           // after it, so $('#main') and getElementById stay the page's own
  bootSnapClear(main);
  main.style.visibility = 'hidden';
  SNAP.copy = copy;
  // Any move of the person's own (another page, Back) shows that page as it draws.
  window.addEventListener('hashchange', bootSnapRelease, {once: true});
}
// #main as boot found it, for route() or boot's own message to draw in.
function bootSnapClear(main) {
  SNAP.shown = '';
  main.innerHTML = ''; main.className = '';
  main.inert = false; main.removeAttribute('aria-busy');
}
// The new page settled, the wait ended, or the person moved on: the copy goes and the page shows.
function bootSnapRelease() {
  SNAP.done = true;
  const copy = SNAP.copy;
  if (!copy) return;
  SNAP.copy = null;
  copy.remove();
  const main = document.getElementById('main');
  if (main) main.style.visibility = '';
}

// After boot knows who is signed in: from now on the screen is kept as it settles.
function bootSnapWho(me) { SNAP.who = authenticatedConfigPerson(me) ? snapWho(me) : ''; }
// The screen as it is now, if it settled; nothing that could run or load on its own, and nothing typed.
function snapScreen() {
  const main = document.getElementById('main');
  if (!main || SNAP.copy || !main.childElementCount || main.querySelector('[aria-busy="true"]')) return null;
  const copy = main.cloneNode(true);
  copy.querySelectorAll('iframe,frame,object,embed,video,audio,canvas,script,dialog,template,img[src^="blob:"]').forEach(el => el.remove());
  copy.querySelectorAll('[autofocus]').forEach(el => el.removeAttribute('autofocus'));
  const html = copy.innerHTML;
  // UTF-8 is never shorter than its UTF-16 length, so the length alone rules most big screens out.
  if (html.length > SNAP_MAX_BYTES || new TextEncoder().encode(html).length > SNAP_MAX_BYTES) return {html: null};
  return {html, cls: main.className};
}
function bootSnapSave() {
  clearTimeout(SNAP.timer);
  if (!snapOn() || !SNAP.who || SESSION.out) return;
  const screen = snapScreen();
  if (!screen) return;
  const who = SNAP.who;
  // A screen too big to keep takes the older one with it: the next open must not show a page from before it.
  if (screen.html === null) { void snapTx('readwrite', s => s.delete(location.origin)).catch(() => {}); return; }
  const entry = {who, version: SNAP_VERSION, route: location.hash, cls: screen.cls, html: screen.html, at: Date.now()};
  void snapTx('readwrite', s => s.put(entry, location.origin)).catch(() => {});
}
function bootSnapSaveSoon() {
  clearTimeout(SNAP.timer);
  SNAP.timer = setTimeout(bootSnapSave, SNAP_SETTLE_MS);
}
window.addEventListener('hashchange', bootSnapSaveSoon);
document.addEventListener('visibilitychange', () => { if (document.hidden) bootSnapSave(); });
window.addEventListener('pagehide', bootSnapSave);
// Signing out leaves no screen behind, as decide.js leaves no unsent action (the same links).
document.addEventListener('click', e => {
  const link = e.target.closest?.('a[href]');
  if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href'))) void bootSnapDrop();
}, true);
