/* ui/app/boot.ts — Last: native class, service worker registration and the boot sequence
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

window.dispatchEvent(new Event('tico:app-loaded'));
setDrawer(false);
if (/TicoHub/.test(navigator.userAgent)) document.body.classList.add('native');
// installable app: the server maps /manifest.webmanifest and /sw.js at the root like /assets/. Reading
// navigator.serviceWorker throws in a sandboxed document (no allow-same-origin), where no worker can register, so the
// read is guarded: it gives null there, as it does where the API is missing.
function serviceWorkerContainer(): ServiceWorkerContainer | null {
  try { return navigator.serviceWorker || null; } catch { return null; }
}
window.addEventListener('load', () => {
  const sw = serviceWorkerContainer();
  if (!sw) return;
  const had = !!sw.controller;
  let reloading = false;
  sw.addEventListener('controllerchange', () => {
    if (!had || reloading) return;
    reloading = true;
    location.reload();
  });
  sw.register('/sw.js?v=41', {updateViaCache: 'none'}).then(reg => reg.update()).catch(() => {});
});
// The longest boot waits for the roster and /me before it says it couldn't load.
const BOOT_ROSTER_WAIT_MS = 10000;
(async () => {
  bootMark('boot');
  let configVersion = nativeVisualVersion();
  // What the first page needs goes out first: a browser opens only a few connections to one server, so the
  // roster and the opening page's own read are not queued behind the sidebar's counts.
  rdNavStart();                       // the sidebar memory's epoch, before /me goes out (redesign.js)
  const employees = get('/employees'), meRead = get('/me');
  // A roster or /me that never answers (a stalled connection, a hub that hangs) ends in the error card below, with
  // Try again, rather than a page that waits for ever.
  const late = new Promise<never>((_, fail) => setTimeout(() => fail(Object.assign(new Error('Tico took too long to answer.'),
    {timedOut: true})), BOOT_ROSTER_WAIT_MS));
  const roster = Promise.race([Promise.all([employees, meRead, get('/humans').catch(() => ({people: []}))]), late]);
  bootSnapShow(meRead);              // the last screen, as soon as /me says it is this person's (boot-snap.js)
  updPrefetch();
  dashPrefetch();
  try {
    const [emps, me, people] = await roster;
    bootMark('roster');
    rdNavWho(me);                      // the sidebar rows kept for someone else go before this config draws them (redesign.js)
    if (authenticatedConfigPerson(me)) {
      if (SESSION.out || configVersion !== nativeVisualVersion()) return;
      // The page opens on this config now. Only trust for visual frames waits for the desktop app's answer: until
      // it, a visual is a file chip or an Open card (visual-cards.js), and an accepted answer applies the config
      // again and redraws the open threads with it (state.ts applyAuthenticatedConfig, nativeVisualRepaint).
      applyConfig(me.config);
      bootSnapWho(me);
      void applyAuthenticatedConfig(me.config, configVersion).then(done => { if (done) bootMark('native-ack'); });
    } else {
      if (SESSION.out || configVersion !== nativeVisualVersion()) return;
      const cleared = clearNativeVisualOrigin();
      configVersion = nativeVisualVersion();
      await cleared;
      if (SESSION.out || configVersion !== nativeVisualVersion()) return;
      applyConfig(me?.config);          // an older response may name the page, but cannot grant native navigation
    }
    S.emps = namedRoster(emps); S.me = me; setPeople(people);
    viewOnlyStart();                   // a bot's read-only view offers nothing that writes (view-only.ts)
    sessionReturn();                  // restore drafts only after the authenticated org and person are known
    renderVersionMessage();            // once per person, so it waits for who is signed in
  } catch (e) {
    if (SESSION.out || configVersion !== nativeVisualVersion()) return;
    const cleared = clearNativeVisualOrigin();
    configVersion = nativeVisualVersion();
    await cleared;
    if (SESSION.out || configVersion !== nativeVisualVersion()) return;
    bootSnapHandOff(); bootSnapRelease();
    askSync();                         // no one to ask for: Ask's row, there from the first paint, goes (ask-panel.js)
    // The timed-out roster is the error Object.assign made above (timedOut: true); any other thrown value is not one.
    if ((e as {timedOut?: unknown}).timedOut) {
      $('#main').innerHTML = `<section class="card"><h2>Couldn't load Tico</h2><p class="err">${esc(errorMessage(e, ''))}</p><button type="button" data-boot-retry>Try again</button></section>`;
      $('#main [data-boot-retry]').onclick = () => location.reload();
      return;
    }
    $('#main').innerHTML = `<section class="card"><h2>Tico server not running</h2><p>Start it with <code>scripts/tico-computer server start</code> in the Tico repo, or install it with <code>scripts/tico-computer server install</code> so it runs at login.</p><p class="err">${esc(errorMessage(e, ''))}</p></section>`;
    return;
  }
  if (S.me?.cloud) {
    // Its settings change seldom (an owner turning telemetry on or off): read again every ten minutes, and when a
    // tab that was hidden is shown, not every 30 s from every open tab.
    window.TicoObservability?.start();
    setInterval(() => { if (!document.hidden) window.TicoObservability?.start(); }, 600000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) window.TicoObservability?.start(); });
  }
  void orgHistorySync();
  void railsSync();
  void sendWaySync();
  window.gsBoot?.();
  window.hlBoot?.();
  // A team that has never been set up opens on its first run, not on an empty Chat.
  if (BOOT_DEFAULT_ROUTE && S.config.onboarding_needed) history.replaceState(null, '', WELCOME);
  bootSnapHandOff();                 // a kept screen stays up, still, until this one settles
  route(); renderHeartbeat(); renderAccount();
  bootMark('route');
  // Health and sidebar counts enrich the page, so they go out once the roster is in and the page has asked for its own
  // data: started alongside the roster they took the hub's time from it (Tico Team, Nov 4: roster 660-860 ms with them,
  // 225-390 ms without), and every page waits on the roster.
  const status = get('/status').catch(() => null).then(st => { S.status = st; S.statusPending = false; cloudAccessFromStatus(); });
  const counts = v2Refresh();
  bootPaintWatch();                  // marks the first screen with data and reports the open (boot-timing.js)
  sessionResume();                   // back from signing in: the task that was open opens again (session.js)
  void Promise.all([status, counts]).then(() => {
    renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync();
    botAlertDraw();
    overviewRefresh();                 // a campus opened before the first status answer shows it now, not at the next poll
    if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
  });
  if (S.me?.cloud) void updUnreadRefresh();
  // Tasks, bot status and Needs you arrive as they change (ui/app/live.ts). The roster, computers and
  // Updates are not on that stream: they still refresh every 2 minutes. When the live stream is down the
  // previous 30 s poll multiplied /api/status load across tabs while the hub was already busy; visibility
  // still refreshes a stale tab the moment it is shown again.
  // Subscribe after the initial count snapshots: an older snapshot must not overwrite a live change.
  void counts.then(liveWire);
  let refreshedAt = Date.now();
  const every = () => 120000;
  const poll = () => { refreshedAt = Date.now(); return refresh(false); };
  setInterval(() => { if (!document.hidden && Date.now() - refreshedAt >= every() - 1000) void poll(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - refreshedAt > every()) void poll(); });
})();
