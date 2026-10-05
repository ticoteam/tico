/* ui/app/boot.js — Last: native class, service worker registration and the boot sequence
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

setDrawer(false);
if (/TicoHub/.test(navigator.userAgent)) document.body.classList.add('native');
// installable app: the server maps /manifest.webmanifest and /sw.js at the root like /assets/
if ('serviceWorker' in navigator) window.addEventListener('load', () => {
  const had = !!navigator.serviceWorker.controller;
  let reloading = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!had || reloading) return;
    reloading = true;
    location.reload();
  });
  navigator.serviceWorker.register('/sw.js?v=40', {updateViaCache: 'none'}).then(reg => reg.update()).catch(() => {});
});
(async () => {
  try {
    const [st, emps, issues, me, people] = await Promise.all([get('/status').catch(() => null), get('/employees'), get('/issues').catch(() => []), get('/me').catch(() => null), get('/humans').catch(() => ({people: []}))]);
    applyConfig(me?.config);           // team-facing names before the first render
    S.status = st; S.emps = namedRoster(emps); S.issues = issues; S.me = me; setPeople(people);
  } catch (e) {
    $('#main').innerHTML = `<section class="card"><h2>Tico server not running</h2><p>Start it with <code>scripts/tico server start</code> in the Tico repo, or install it with <code>scripts/tico server install</code> so it runs at login.</p><p class="err">${esc(e.message)}</p></section>`;
    return;
  }
  if (S.me?.cloud) {
    window.TicoObservability?.start();
    setInterval(() => window.TicoObservability?.start(), 30000);
  }
  await v2Refresh();
  void orgHistorySync();
  void railsSync();
  void tasksPinsSync();
  window.gsBoot?.();
  window.hlBoot?.();
  // A team that has never been set up opens on its first run, not on an empty Chat.
  if (BOOT_DEFAULT_ROUTE && S.config.onboarding_needed) history.replaceState(null, '', WELCOME);
  route(); renderHeartbeat(); renderAccount();
  if (S.me?.cloud) void updUnreadRefresh();
  nativeHandler('windowMode')?.postMessage('state');
  // Tasks, bot status and Needs you arrive as they change (ui/app/live.js). The issues, the roster, computers and
  // Updates are not on that stream: they still refresh, every 30 s while the stream is down and every 2 minutes
  // while it is up. A tab in the background does not poll; coming back to a stale tab refreshes it at once.
  liveWire();
  let refreshedAt = Date.now();
  const every = () => liveConnected() ? 120000 : 30000;
  const poll = () => { refreshedAt = Date.now(); return refresh(false); };
  setInterval(() => { if (!document.hidden && Date.now() - refreshedAt >= every() - 1000) void poll(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - refreshedAt > every()) void poll(); });
})();
