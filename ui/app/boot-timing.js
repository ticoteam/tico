/* ui/app/boot-timing.js — How long opening the app took, from the page's first byte to its first useful screen
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// User Timing marks, each set once per page load, in the order an open passes them (ms since navigation start):
//   tico:head          theme.js ran in the head: the page's HTML has arrived
//   tico:bundle-start  the bundle's first statement (backend/ui_bundle.py BUNDLE_MARK); not set with the files unbundled
//   tico:boot          every script has run and boot.js starts
//   tico:roster        the roster, /me and the people answered
//   tico:route         the opening page is drawn (its shell and any skeleton)
//   tico:native-ack    the desktop app answered the signed-in visual address, or its 5 s wait ended; nothing waits on it
//   tico:paint-fresh   the opening page shows its data: #main has content and no skeleton (aria-busy) is left
// With them goes each API read before that paint, with the server's own time (Server-Timing app and auth). In a
// browser this stays in the page: window.ticoBootTiming, and the marks in the performance timeline. The desktop app
// gets it once per load as `bootTiming` and writes one line to its log (app/src/bridge.rs boot_line).
const BOOT_MARKS = ['head', 'bundle-start', 'boot', 'roster', 'route', 'native-ack', 'paint-fresh'];
// A page that never settles (a skeleton left up, an error) is reported as it stands after this long.
const BOOT_PAINT_WAIT_MS = 20000;
// What the report keeps: the reads before the paint, and paths short enough for one log line.
const BOOT_SERVER_MAX = 16;
// No time is longer than this (ten minutes, as the desktop app takes them): a page left open in the background longer
// leaves out what came after.
const BOOT_MS_MAX = 600000;
const bootMs = n => Number.isFinite(n) && n >= 0 && n <= BOOT_MS_MAX ? Math.round(n) : null;
let BOOT_REPORTED = false, BOOT_ROUTE = '';

function bootMark(name) {
  try { if (!performance.getEntriesByName('tico:' + name, 'mark').length) performance.mark('tico:' + name); }
  catch { /* no User Timing */ }
}
function bootMarkAt(name) {
  try { const m = performance.getEntriesByName('tico:' + name, 'mark')[0]; return m ? Math.round(m.startTime) : null; }
  catch { return null; }
}
// The page, not what is on it: `#/bot/ops/dm` is `#/bot`, so the log never names a bot, a task or a person.
const bootRouteName = hash => { const m = /^#\/([a-z][a-z0-9-]{0,31})/.exec(String(hash || '')); return m ? '#/' + m[1] : ''; };
// An API path without its query, read the REST way: after `/api/` (and `v2/`) parts alternate between a kind and the
// one meant, and every one meant is `:id`, as is any kind that is not a plain lowercase word. `/api/v2/bots/ops/dm`
// is `/api/v2/bots/:id/dm`, so the log names no bot, task or person here either.
function bootPath(url) {
  let path; try { path = new URL(url, location.href).pathname; } catch { return ''; }
  if (!path.startsWith('/api/')) return '';
  const parts = path.split('/'), base = parts[2] === 'v2' ? 3 : 2;
  return parts.map((part, i) => i < base || ((i - base) % 2 === 0 && /^[a-z][a-z-]{0,23}$/.test(part)) ? part : ':id')
    .slice(0, 9).join('/');
}
const bootServerMs = (entry, name) => {
  const t = (entry.serverTiming || []).find(s => s.name === name);
  return t ? bootMs(t.duration) : null;
};
function bootTimingReport(settled) {
  const marks = {};
  for (const name of BOOT_MARKS) { const at = bootMs(bootMarkAt(name) ?? NaN); if (at !== null) marks[name] = at; }
  let html = null, bundle = null, server = [];
  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) html = bootMs(nav.responseEnd);
    const resources = performance.getEntriesByType('resource');
    const js = resources.find(r => new URL(r.name, location.href).pathname === '/tico/ui/app.bundle.js');
    if (js && bootMs(js.duration) !== null) bundle = {bytes: Math.max(0, Math.round(js.transferSize || 0)), ms: bootMs(js.duration)};
    const until = marks['paint-fresh'] ?? Infinity;
    server = resources.filter(r => r.startTime <= until && bootPath(r.name) && bootMs(r.duration) !== null).slice(0, BOOT_SERVER_MAX)
      .map(r => ({path: bootPath(r.name), ms: bootMs(r.duration), app: bootServerMs(r, 'app'), auth: bootServerMs(r, 'auth')}));
  } catch { /* no Resource Timing */ }
  return {route: bootRouteName(BOOT_ROUTE), settled, marks, html, bundle, server};
}
function bootTimingSend(settled) {
  if (BOOT_REPORTED) return;
  BOOT_REPORTED = true;
  const report = window.ticoBootTiming = bootTimingReport(settled);
  // Only a shell that lists it hears it (app/src/bridge.rs BRIDGE_SCRIPT), as for every other message.
  if (!nativeHas('bootTiming')) return;
  try { Promise.resolve(nativeHandler('bootTiming')?.postMessage(report)).catch(() => {}); } catch { /* shell gone */ }
}
// After route(): marks the first frame whose #main has content and no skeleton, then reports. A hidden page has no
// frames, so it looks again on a timer instead.
function bootPaintWatch() {
  BOOT_ROUTE = S.route;
  const started = Date.now();
  const check = () => {
    if (BOOT_REPORTED || SESSION.out) return;
    const main = document.getElementById('main');
    if (main && main.childElementCount && !main.querySelector('[aria-busy="true"]')) {
      bootMark('paint-fresh');
      bootTimingSend(true);
      return;
    }
    if (Date.now() - started > BOOT_PAINT_WAIT_MS) { bootTimingSend(false); return; }
    if (document.hidden) setTimeout(check, 100); else requestAnimationFrame(check);
  };
  check();
}
