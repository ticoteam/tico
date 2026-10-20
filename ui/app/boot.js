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
  let configVersion = nativeVisualVersion();
  // What the first page needs goes out first: a browser opens only a few connections to one server, so the
  // roster and the opening page's own read are not queued behind the sidebar's counts.
  const roster = Promise.all([get('/employees'), get('/me'), get('/humans').catch(() => ({people: []}))]);
  updPrefetch();
  // Health and sidebar counts enrich the page; a slow diagnostic must not hold up opening a chat or task.
  const status = get('/status').catch(() => null).then(st => { S.status = st; S.statusPending = false; });
  const issues = get('/issues').catch(() => []).then(rows => { S.issues = rows; });
  const counts = v2Refresh();
  try {
    const [emps, me, people] = await roster;
    if (authenticatedConfigPerson(me)) {
      const applied = applyAuthenticatedConfig(me.config, configVersion);
      configVersion = nativeVisualVersion();
      const accepted = await applied;
      if (!accepted || SESSION.out || accepted.version !== nativeVisualVersion()) return;
      configVersion = accepted.version;  // the helper may have queued its own timeout clear
    } else {
      if (SESSION.out || configVersion !== nativeVisualVersion()) return;
      const cleared = clearNativeVisualOrigin();
      configVersion = nativeVisualVersion();
      await cleared;
      if (SESSION.out || configVersion !== nativeVisualVersion()) return;
      applyConfig(me?.config);          // an older response may name the page, but cannot grant native navigation
    }
    S.emps = namedRoster(emps); S.me = me; setPeople(people);
    renderVersionMessage();            // once per person, so it waits for who is signed in
  } catch (e) {
    if (SESSION.out || configVersion !== nativeVisualVersion()) return;
    const cleared = clearNativeVisualOrigin();
    configVersion = nativeVisualVersion();
    await cleared;
    if (SESSION.out || configVersion !== nativeVisualVersion()) return;
    $('#main').innerHTML = `<section class="card"><h2>Tico server not running</h2><p>Start it with <code>scripts/tico server start</code> in the Tico repo, or install it with <code>scripts/tico server install</code> so it runs at login.</p><p class="err">${esc(e.message)}</p></section>`;
    return;
  }
  if (S.me?.cloud) {
    window.TicoObservability?.start();
    setInterval(() => window.TicoObservability?.start(), 30000);
  }
  void orgHistorySync();
  void railsSync();
  void tasksPinsSync();
  window.gsBoot?.();
  window.hlBoot?.();
  // A team that has never been set up opens on its first run, not on an empty Chat.
  if (BOOT_DEFAULT_ROUTE && S.config.onboarding_needed) history.replaceState(null, '', WELCOME);
  route(); renderHeartbeat(); renderAccount();
  sessionResume();                   // back from signing in: the task that was open opens again (session.js)
  void Promise.all([status, issues, counts]).then(() => {
    renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync();
    botAlertDraw();
    overviewRefresh();                 // a campus opened before the first status answer shows it now, not at the next poll
    if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
  });
  if (S.me?.cloud) void updUnreadRefresh();
  // Tasks, bot status and Needs you arrive as they change (ui/app/live.js). The issues, the roster, computers and
  // Updates are not on that stream: they still refresh, every 30 s while the stream is down and every 2 minutes
  // while it is up. A tab in the background does not poll; coming back to a stale tab refreshes it at once.
  // Subscribe after the initial count snapshots: an older snapshot must not overwrite a live change.
  void counts.then(liveWire);
  let refreshedAt = Date.now();
  const every = () => liveConnected() ? 120000 : 30000;
  const poll = () => { refreshedAt = Date.now(); return refresh(false); };
  setInterval(() => { if (!document.hidden && Date.now() - refreshedAt >= every() - 1000) void poll(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - refreshedAt > every()) void poll(); });
})();
