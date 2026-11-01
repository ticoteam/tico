/* ui/app/router.js — route(): hash routes to pages
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let LAST_ROUTE = '', ROUTE_FADING = false, ROUTE_SEQ = 0, ROUTE_PENDING = '', ROUTE_FADE = null;
function route(hash = location.hash) {
  if (hash.startsWith('#/settings?recruit=')) {
    try {
      const raw = new URLSearchParams(hash.split('?')[1]).get('recruit');
      if (raw && raw.length <= 5000) {
        const value = JSON.parse(raw);
        if (value && typeof value.title === 'string' && typeof value.brief === 'string') RECRUIT_HANDOFF = value;
      }
    } catch { /* A malformed external handoff never changes a bot. */ }
    SETTINGS_TAB = 'bots'; location.replace(SETTINGS); return;
  }
  // Decide is the Needs you card now: an old link goes to Needs you, and one naming an item opens that item's card.
  if (hash === DECIDE || hash.startsWith(DECIDE + '?')) {
    const q = new URLSearchParams(hash.split('?')[1] || '');
    DECIDE_WANT = q.get('item') || q.get('task') || q.get('approval') || '';
    location.replace(NEEDS_YOU); return;
  }
  overviewStop(); DASH = null;
  $('#main').classList.remove('overview-layout');
  const from = LAST_ROUTE;
  S.route = LAST_ROUTE = hash || UPDATES;
  // The tab address Settings wrote for itself is still Settings: a redraw (route() called again) keeps the page as it is.
  if (from === SETTINGS && S.route === SETTINGS_ADDRESS) S.route = LAST_ROUTE = SETTINGS;
  if (S.route !== SETTINGS) { clearTimeout(SETTINGS_PRESENCE.timer); SETTINGS_PRESENCE.timer = null; }
  const botOf = r => r.startsWith('#/bot/') ? r.slice(6).split('/')[0] : '';
  const botHere = botOf(S.route);
  document.body.classList.toggle('bot-page', !!botHere);   // a phone gives the bot page the whole screen
  window.TicoObservability?.route(S.route);
  document.body.classList.remove('mobile-composer-focus');
  // Every page starts from a bare #main: a layout class left behind (the bot page's bot-split-layout made #main a
  // two-column grid) would lay the next page out in that grid. Each page adds its own again.
  const main = $('#main');
  main.className = '';
  // A new page fades in (opacity only); a query change within the same page does not. A view transition
  // (routeChanged) already cross-fades it, so this is the fallback for a browser without one.
  const newPath = !from || from.split('?')[0] !== S.route.split('?')[0];
  if (from && newPath && !ROUTE_FADING && main.animate && !matchMedia('(prefers-reduced-motion: reduce)').matches)
    main.animate({opacity: [0, 1]}, {duration: 120, easing: 'cubic-bezier(.2,0,0,1)'});
  document.body.classList.remove('task-peek-open');
  if (!isMarketRoute(S.route)) window.marketStop?.();
  if (!isStrategyRoute(S.route)) window.strategyStop?.();
  if (!isLensesRoute(S.route)) window.lensesStop?.();
  if (!isAutomationsRoute(S.route)) automationsStop();
  taskChatStop(); $('#task-modal')?.close();
  if (!S.route.startsWith(DOCS)) DOC_LOAD++;
  // Leaving Mail abandons its fetches, its debounced search (which would set the hash back) and
  // its accumulated page, so returning re-reads the server instead of drawing a stale list.
  if (!S.route.startsWith(MAIL)) { MAIL_LOAD++; clearTimeout(MAIL_SEARCH); mailForget(); }
  if (!S.route.startsWith(MESSAGING)) { MESSAGING_LOAD++; MESSAGING_LIST = null; }
  decideStop();     // the Needs you card closes with its page; its waiting answer goes now
  convStop(); meetStop(); onbStop(); TASKS_ST?.layoutAbort?.abort(); tasksKeep(TASKS_ST); TASKS_ST = null; // page-local work never outlives its page
  if (S.route !== '#/usage') USE = null;
  if (!S.route.startsWith('#/replay')) replayStop();
  if (!/^#\/computers\/[^/?]+\/screen$/.test(S.route)) screenStop();
  if (!/^#\/computers\/[^/?]+\/performance$/.test(S.route)) perfStop();
  if (!(S.route === UPDATES || S.route.startsWith(UPDATES + '?'))) { if (UPD) { UPD.io?.disconnect(); void updFlush(UPD); UPD = null; } updcStop(); }
  if (!S.route.startsWith('#/bot/')) botStopped();
  if (!S.route.startsWith('#/seat/')) recruitStop();
  askSync();          // Ask's button: away on the Assistant page and in setup (ui/app/ask-panel.js)
  renderTree();
  if (redesignRoute(S.route)) { /* Strategy's tabs, Automations, Connections and the old addresses that moved (ui/app/redesign.js) */ }
  else if (S.route === NOTES || S.route === RECORDINGS || S.route.startsWith(RECORDINGS + '?')) {
    // Old links land on Meetings, keeping a deep link to one meeting.
    const id = new URLSearchParams(S.route.split('?')[1] || '').get('recording');
    location.hash = MEETINGS + (id ? '?meeting=' + encodeURIComponent(id) : ''); return;
  }
  else if (S.route === '#/inbox' || S.route.startsWith('#/inbox?')) { location.hash = MAIL; return; }
  else if (S.route === OVERVIEW) pageDashboard('dashboard');
  else if (S.route === OVERVIEW_CAMPUS) pageOverview();
  else if (S.route.startsWith(TEAM_PAGE)) pageDashboard('team:' + decodeURIComponent(S.route.slice(TEAM_PAGE.length).split('?')[0]));
  else if (S.route === WELCOME) pageWelcome();
  else if (S.route === '#/roadmap') pageRoadmap();
  else if (S.route === '#/tags') pageTags();
  else if (S.route.startsWith('#/tag/')) pageTag(decodeURIComponent(S.route.slice(6)));
  else if (S.route.startsWith('#/task/')) pageTasks('', decodeURIComponent(S.route.slice(7)));
  else if (S.route.startsWith(TASKS + '/')) { location.replace('#/task/' + S.route.slice(TASKS.length + 1).split('?')[0]); return; }   // #/tasks/<id> is the same task
  else if (S.route === CHAT) { location.hash = TASKS; return; }
  else if (S.route === MEETINGS || S.route.startsWith(MEETINGS + '?')) pageNotes();
  else if (S.route.startsWith('#/bot/')) { const [slug, tab] = S.route.slice(6).split('/'); pageBot(slug, tab); }
  else if (S.route.startsWith('#/seat/')) { const [id, template] = S.route.slice(7).split('?')[0].split('/').map(decodeURIComponent); void pageSeat(id, template || ''); }
  else if (S.route === ASSISTANT) pageAssistant();
  else if (S.route === LEARNED) pageLearned();
  else if (S.route.startsWith('#/person/')) {
    const parts = S.route.slice(9).split('/').map(decodeURIComponent);
    pagePerson(parts[0], parts[1]);
  }
  else if (isTasksRoute(S.route)) {
    // #/tasks?owner=bot:eng&tag=bug: the filters ride in the query (ui/app/task-list.js)
    const base = S.route.split('?')[0];
    pageTasks(base === BOARD ? 'board' : base === ISSUES ? 'list' : '');
  }
  else if (S.route === UPDATES || S.route.startsWith(UPDATES + '?')) pageUpdates();
  else if (S.route === SETTINGS) pageSettings();
  else if (S.route === HELP) { location.replace(OVERVIEW); window.supportOpen?.(); return; }   // Help is the Support pop-up, over Overview
  else if (S.route === '#/getting-started') { location.replace(TASKS); return; }   // the checklist is gone
  else if (S.route === '#/repositories') { SETTINGS_TAB = 'repos'; location.replace(SETTINGS); return; }   // Settings > Repositories
  // Settings > Webhooks (it was Triggers); #/settings/webhooks/outgoing and /gateway open that tab.
  else if (['#/triggers', '#/webhooks', '#/settings/triggers', '#/settings/webhooks'].includes(S.route) || S.route.startsWith('#/settings/webhooks/')) {
    const tab = S.route.split('/')[3];
    if (WH_TABS.some(([k]) => k === tab)) Object.assign(WH, {tab, trigger: '', hook: '', shown: null, hookShown: null});
    SETTINGS_TAB = 'webhooks'; location.replace(SETTINGS); return;
  }
  else if (S.route === '#/health') { SETTINGS_TAB = 'health'; location.replace(SETTINGS); return; }   // Health moved into Settings
  else if (['#/settings/company-inbox', '#/settings/email-phone'].includes(S.route)) { SETTINGS_TAB = 'inbound'; location.replace(SETTINGS); return; }
  else if (S.route === '#/settings/inbox') { SETTINGS_TAB = 'inbox'; location.replace(SETTINGS); return; }   // Google's sign-in returns here
  else if (S.route === '#/settings/roadmap') { SETTINGS_TAB = 'tasks'; ROADMAP_SETTINGS_REVEAL = true; location.replace(SETTINGS); return; }   // Health's roadmap suggestions fix
  else if (S.route === '#/settings/storage') { SETTINGS_TAB = 'storage'; location.replace(SETTINGS); return; }   // Health's Set up S3
  // Settings > System (it was Rules, before that Routines): Tico's own jobs. Wake rules and routines are Automations now.
  else if (['#/settings/rules', '#/settings/routines'].includes(S.route.split('?')[0])) { SETTINGS_TAB = 'system'; location.replace(SETTINGS); return; }
  else if (S.route === '#/settings/people') { SETTINGS_TAB = 'people'; location.replace(SETTINGS); return; }   // setup's people to invite
  else if (S.route === '#/settings/computers') { SETTINGS_TAB = 'devices'; location.replace(SETTINGS); return; }
  else if (['#/settings/subscriptions', '#/subscriptions'].includes(S.route)) { SETTINGS_TAB = 'models'; location.replace(SETTINGS); return; }   // Subscriptions are on Models
  // Old links and bookmarks to the Strategy Bot's settings address still open its card (Settings > System).
  else if (S.route === '#/settings/ceo') { location.replace(SETTINGS + '/strategy-bot'); return; }
  // Every other #/settings/<tab>[/<card>] opens that tab, and the card when named (the settings catalogue's links,
  // backend/settings_entries.py). settingsShow maps old names and sections and puts the tab's own address back in
  // the bar, so a tab can be bookmarked and shared: #/settings/gateway, #/settings/models, …
  else if (/^#\/settings\/[a-z-]+(?:\/[a-z0-9-]+)?\/?(\?.*)?$/.test(S.route)) {
    const [tab, card] = S.route.slice(11).split('?')[0].split('/'); SETTINGS_REVEAL = card ? '#' + card : '';
    // The Models card and Company turn levels moved from Computers to Models.
    SETTINGS_TAB = tab === 'devices' && SETTINGS_MOVED_TO_MODELS.includes(SETTINGS_REVEAL) ? 'models' : tab; location.replace(SETTINGS); return;
  }
  else if (S.route === CREDENTIALS) pageCredentials();
  else if (S.route === SQL_PAGE) pageSql();
  else if (S.route === MAIL || S.route.startsWith(MAIL + '?')) pageMail();
  else if (S.route === MESSAGING || S.route.startsWith(MESSAGING + '?')) pageMessaging();
  else if (S.route === '#/changelog') pageChangelog();
  // Market is a tab of Strategy now: old links keep their query or note path.
  else if (S.route === '#/market' || S.route.startsWith('#/market?') || S.route.startsWith('#/market/')) { location.replace(MARKET_ROUTE + S.route.slice('#/market'.length)); return; }
  else if (isMarketRoute(S.route)) pageMarket();
  else if (isLensesRoute(S.route)) pageLenses();
  else if (isStrategyRoute(S.route)) pageStrategy();
  else if ((S.route === DOCS || S.route.startsWith(DOCS + '/') || S.route.startsWith(DOCS + '?')) && new URLSearchParams(S.route.split('?')[1] || '').get('collection') === 'market') {
    const id = S.route.startsWith(DOCS + '/') ? S.route.slice(DOCS.length + 1).split('?')[0] : '';
    location.hash = MARKET_ROUTE + (id ? '?note=' + encodeURIComponent(id) : '');
    return;
  }
  else if (S.route === DOCS || S.route.startsWith(DOCS + '/') || S.route.startsWith(DOCS + '?')) pageCompanyDocs();
  else if (S.route === INTEGRATIONS || S.route.startsWith(INTEGRATIONS + '/')) pageIntegrations();
  else if (S.route === '#/runs') { SETTINGS_TAB = 'runs'; location.replace(SETTINGS); return; }   // Runs moved into Settings > History
  else if (S.route === LEARNINGS || S.route.startsWith(LEARNINGS + '?')) pageLearnings();
  else if (S.route === '#/usage') pageUsage();
  else if (S.route === '#/replay' || S.route.startsWith('#/replay?')) pageReplay();
  else if (/^#\/computers\/[^/?]+\/screen$/.test(S.route)) pageComputerScreen(decodeURIComponent(S.route.split('/')[2]), from);
  else if (/^#\/computers\/[^/?]+\/performance$/.test(S.route)) pageComputerPerformance(decodeURIComponent(S.route.split('/')[2]));
  else if (isAutomationsRoute(S.route)) pageAutomations();
  else location.hash = UPDATES;         // unknown or empty routes land on Updates, home
  redesignAfterRoute();
  // A new page starts at its top; a query change (a filter, an open row) or a redraw keeps the reader's place.
  if (newPath) $('#main').scrollTop = 0;
}
// A move to another page cross-fades from the old page to the new one in 150ms (a view transition, ui/styles/base.css):
// the browser holds the old page's picture while route() draws, so the switch never shows a half-drawn frame. A query
// change, reduced motion, a hidden window and a browser without view transitions draw at once. An error in route()
// still surfaces as an error (not a rejected transition).
function routeChanged() {
  const path = h => (h || UPDATES).split('?')[0], seq = ++ROUTE_SEQ;
  // The address changed again before its fade drew (a quick second click, a script): that address's page is drawn now
  // and this one right after it, in this task and without a fade, as they would be without the fade. An address the
  // first page writes for itself (Settings names its tab) gives way to the newer one.
  if (ROUTE_PENDING) {
    const was = ROUTE_PENDING, now = location.hash; ROUTE_PENDING = '';
    route(was);
    if (location.hash !== now) history.replaceState(history.state, '', now);
    route(); return;
  }
  if (!LAST_ROUTE || path(location.hash) === path(LAST_ROUTE) || !document.startViewTransition || document.hidden
    || matchMedia('(prefers-reduced-motion: reduce)').matches) { route(); return; }
  ROUTE_PENDING = location.hash || UPDATES;
  const fade = document.startViewTransition(() => {
    if (seq !== ROUTE_SEQ) return;
    ROUTE_PENDING = ''; ROUTE_FADING = true;
    try { route(); } catch (e) { setTimeout(() => { throw e; }); } finally { ROUTE_FADING = false; }
  });
  ROUTE_FADE = fade;
  fade.finished.finally(() => { if (ROUTE_FADE !== fade) return; ROUTE_FADE = null; routeFadeHover(); }).catch(() => {});
  for (const done of [fade.ready, fade.updateCallbackDone, fade.finished]) done.catch(() => {});
}
// While a fade runs, Chrome gives the pointer to the root element, not the page under it, whatever pointer-events says
// (ui/styles/base.css). So a press during the fade ends it at once and goes to what is under it: the press, and the
// click it makes (which the browser gives to the root, where the press began). A pointer moved during the fade is told
// where it is when the fade ends, so a tooltip or a hover shows without another move.
let ROUTE_PRESS = null, ROUTE_MOVE = null;
const routeMouse = (type, e, el) => {
  const init = {bubbles: true, cancelable: true, composed: true, view: window, detail: e.detail, button: e.button,
    buttons: e.buttons, clientX: e.clientX, clientY: e.clientY, screenX: e.screenX, screenY: e.screenY,
    ctrlKey: e.ctrlKey, shiftKey: e.shiftKey, altKey: e.altKey, metaKey: e.metaKey};
  const pointer = type.startsWith('pointer');
  if (pointer) Object.assign(init, {pointerId: e.pointerId, pointerType: e.pointerType, isPrimary: e.isPrimary,
    width: e.width, height: e.height, pressure: e.pressure});
  return el.dispatchEvent(pointer ? new PointerEvent(type, init) : new MouseEvent(type, init));
};
const routeUnder = e => { const el = document.elementFromPoint(e.clientX, e.clientY); return el === document.documentElement ? null : el; };
function routeFadeHover() {
  const e = ROUTE_MOVE, el = e && routeUnder(e); ROUTE_MOVE = null;
  if (el) for (const type of ['pointerover', 'mouseover', 'pointermove', 'mousemove']) routeMouse(type, e, el);
}
document.addEventListener('pointerdown', e => {
  if (!e.isTrusted) return;
  ROUTE_PRESS = null;
  if (!ROUTE_FADE || e.target !== document.documentElement) return;
  ROUTE_MOVE = null; ROUTE_FADE.skipTransition();
  const el = routeUnder(e); if (!el) return;
  ROUTE_PRESS = el; e.stopImmediatePropagation();
  routeMouse('pointerdown', e, el);
}, true);
for (const type of ['mousedown', 'click', 'auxclick', 'dblclick', 'contextmenu']) document.addEventListener(type, e => {
  const el = ROUTE_PRESS;
  if (!el || !e.isTrusted || e.target !== document.documentElement) return;
  if (type !== 'mousedown') ROUTE_PRESS = null;
  e.stopImmediatePropagation(); e.preventDefault();
  const hit = routeUnder(e);
  if (type !== 'mousedown' && !(hit && (el.contains(hit) || hit.contains(el)))) return;   // released somewhere else
  if (routeMouse(type, e, el) && type === 'mousedown')
    el.closest('a[href],button,input,select,textarea,summary,[tabindex],[contenteditable]')?.focus({preventScroll: true});
}, true);
// A scroll during the fade ends it too, so the page under the pointer scrolls, not the window.
document.addEventListener('wheel', e => { if (ROUTE_FADE && e.target === document.documentElement) ROUTE_FADE.skipTransition(); }, {capture: true, passive: true});
for (const type of ['pointermove', 'mousemove']) document.addEventListener(type, e => {
  if (ROUTE_FADE && e.isTrusted && e.target === document.documentElement) ROUTE_MOVE = e;
}, true);
window.addEventListener('hashchange', routeChanged);
