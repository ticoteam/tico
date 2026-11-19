/* ui/app/router.ts — route(): hash routes to pages
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let LAST_ROUTE = '';
// The page an address draws: its path, with every thread and New message one page (the rail stays while the pane
// beside it changes).
const routePage = (h: string | undefined): string => { const [p = ''] = String(h || '').split('?'); return p === THREADS_ROUTE || p === THREADS_NEW || p.startsWith(THREAD_ROUTE) ? THREADS_ROUTE : p; };
function route(hash: string = location.hash): void {
  // A file's review view opens over the page and closes back to it, which is not drawn again (file-review.ts).
  if (fileReviewRoute(hash)) return;
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
    DECIDE_STAGE = DECIDE_WANT ? '' : q.get('stage') || '';
    location.replace(NEEDS_YOU); return;
  }
  DASH = null;
  $('#main').classList.remove('overview-layout');
  const from = LAST_ROUTE;
  S.route = LAST_ROUTE = hash || UPDATES;
  // The tab address Settings wrote for itself is still Settings: a redraw (route() called again) keeps the page as it is.
  if (from === SETTINGS && S.route === SETTINGS_ADDRESS) S.route = LAST_ROUTE = SETTINGS;
  if (S.route !== SETTINGS) { clearTimeout(SETTINGS_PRESENCE.timer ?? undefined); SETTINGS_PRESENCE.timer = null; }
  const botOf = (r: string): string => r.startsWith('#/bot/') ? (r.slice(6).split('/')[0] ?? '') : '';
  const botHere = botOf(S.route);
  document.body.classList.toggle('bot-page', !!botHere);   // a phone gives the bot page the whole screen
  if (!botHere) pageTitle();
  window.TicoObservability?.route(S.route);
  document.body.classList.remove('mobile-composer-focus');
  // Every page starts from a bare #main: a layout class left behind (the bot page's bot-split-layout made #main a
  // two-column grid) would lay the next page out in that grid. Each page adds its own again.
  const main = $('#main');
  main.className = '';
  // A new page, or a query change within the same page (a filter, an open row): only a new page starts at its top.
  const newPath = !from || routePage(from) !== routePage(S.route);
  if (!isMarketRoute(S.route)) window.marketStop?.();
  if (!isStrategyRoute(S.route)) window.strategyStop?.();
  if (!isLensesRoute(S.route)) window.lensesStop?.();
  if (!isAutomationsRoute(S.route)) automationsStop();
  pageChatRoute(S.route);   // each page's owner's chat, mounted here for every page (ui/app/page-chat.ts)
  taskChatStop(); taskModalRouteClose();
  if (!S.route.startsWith(DOCS)) DOC_LOAD++;
  // Leaving Mail abandons its fetches, its debounced search (which would set the hash back) and
  // its accumulated page, so returning re-reads the server instead of drawing a stale list.
  if (!S.route.startsWith(MAIL)) { MAIL_LOAD++; clearTimeout(MAIL_SEARCH); mailForget(); }
  if (!S.route.startsWith(MESSAGING)) { MESSAGING_LOAD++; MESSAGING_LIST = null; }
  decideStop();     // the Needs you card closes with its page; its waiting answer goes now
  window.liveMeetingsStop?.();
  if (!S.route.startsWith(CALL_ROUTE)) callWindowStop();   // the meeting window (ui/app/call-window.ts); the call goes on
  v2ChatStop(); meetStop(); onbStop(); tasksLeave(); // page-local work never outlives its page
  if (S.route !== '#/usage') USE = null;
  if (!S.route.startsWith('#/replay')) replayStop();
  if (!/^#\/computers\/[^/?]+\/screen$/.test(S.route)) screenStop();
  if (!/^#\/computers\/[^/?]+\/performance$/.test(S.route)) perfStop();
  if (!(S.route === UPDATES || S.route.startsWith(UPDATES + '?'))) { if (UPD) { UPD.io?.disconnect(); void updFlush(UPD); UPD = null; } }
  if (!S.route.startsWith('#/bot/')) botStopped();
  if (!S.route.startsWith('#/seat/')) recruitStop();
  threadsSync();      // the sidebar's Threads link and the rail's lists (ui/app/threads.ts)
  calendarStop(S.route); calRowSync(true);   // the Calendar page's card and reads; the sidebar's next event, reset (ui/app/calendar*.ts)
  setupMark();        // the wizard's mark first: Ask stays away from it (ui/app/notices.ts)
  askSync();          // Ask's button: away on the Assistant page and in setup (ui/app/ask-panel.ts)
  renderTree();
  if (redesignRoute(S.route)) { /* Strategy's tabs, Automations, Connections and the old addresses that moved (ui/app/redesign.ts) */ }
  else if (S.route === NOTES || S.route === RECORDINGS || S.route.startsWith(RECORDINGS + '?')) {
    // Old links land on Meetings, keeping a deep link to one meeting.
    const id = new URLSearchParams(S.route.split('?')[1] || '').get('recording');
    location.hash = MEETINGS + (id ? '?meeting=' + encodeURIComponent(id) : ''); return;
  }
  else if (S.route.startsWith(CALL_ROUTE)) pageCallWindow(decodeURIComponent(S.route.slice(CALL_ROUTE.length).split('?')[0] ?? ''));
  else if (S.route === '#/inbox' || S.route.startsWith('#/inbox?')) { location.hash = MAIL; return; }
  else if (S.route === HOME) pageDashboard('person:me');
  // Company is one page: the Dashboard, Floor, Campus, Graph and Factories addresses it once had tabs for land on it, so a
  // bookmark or an old message's link still opens. One factory's floor keeps its own page.
  else if (S.route === OVERVIEW || S.route === OVERVIEW_FLOOR || /^#\/overview\/(?:campus|graph|factories)(?:\?|$)/.test(S.route)) { location.replace(HOME); return; }
  else if (S.route.startsWith(OVERVIEW_FLOOR + '/')) pageFloorFactory(decodeURIComponent(S.route.slice(OVERVIEW_FLOOR.length + 1)));
  else if (S.route === OVERVIEW_COMPANY) pageOverviewCompany();
  else if (S.route.startsWith(TEAM_PAGE)) pageDashboard('team:' + decodeURIComponent(S.route.slice(TEAM_PAGE.length).split('?')[0] ?? ''));
  else if (S.route === WELCOME) pageWelcome();
  else if (S.route === '#/roadmap') pageRoadmap();
  else if (S.route === '#/tags') pageTags();
  else if (S.route.startsWith('#/tag/')) pageTag(decodeURIComponent(S.route.slice(6)));
  else if (S.route.startsWith('#/task/')) pageTasks(decodeURIComponent(S.route.slice(7)));
  else if (S.route.startsWith(TASKS + '/')) { location.replace('#/task/' + (S.route.slice(TASKS.length + 1).split('?')[0] ?? '')); return; }   // #/tasks/<id> is the same task
  else if (S.route === CHAT) { location.hash = TASKS; return; }
  else if (S.route.startsWith(MEETINGS + '/live/')) pageLiveMeetings(decodeURIComponent(S.route.slice((MEETINGS + '/live/').length)));
  else if (S.route === MEETINGS || S.route.startsWith(MEETINGS + '?')) {
    const live = new URLSearchParams(S.route.split('?')[1] || '').get('live');
    if (live) { location.replace(MEETINGS + '/live/' + encodeURIComponent(live)); return; }
    pageNotes();
  }
  else if (S.route.startsWith('#/bot/')) {
    // A bot's page is at its id; a link that names it by slug (typed, or from before ids) moves to the id.
    const [ref = '', tab] = S.route.slice(6).split('/'), found = botByRef(decodeURIComponent(ref));
    if (found && found.id !== decodeURIComponent(ref)) { location.replace('#/bot/' + encodeURIComponent(found.id) + (tab !== undefined ? '/' + tab : '')); return; }
    // The Assistant is talked to on the person's own Assistant page: its bot page, or the Home and DM it does not have,
    // goes on there. Its other tabs (settings, tasks, files) stay on the bot page.
    if ((found?.id || decodeURIComponent(ref)) === assistantBot() && (!tab || tab === 'home' || tab === 'dm')) { location.replace(ASSISTANT); return; }
    pageBot(decodeURIComponent(ref), tab);
  }
  else if (S.route === THREADS_ROUTE || S.route.startsWith(THREADS_ROUTE + '?')) pageThreads();
  else if (calendarRoute(S.route)) { /* the Calendar page (ui/app/calendar.ts) */ }
  else if (S.route === THREADS_NEW || S.route.startsWith(THREADS_NEW + '?')) pageNewMessage();
  else if (S.route.startsWith(THREAD_ROUTE)) pageThread(decodeURIComponent(S.route.slice(THREAD_ROUTE.length).split(/[/?]/)[0] ?? ''));
  else if (S.route.startsWith('#/seat/')) { const [id = '', template] = (S.route.slice(7).split('?')[0] ?? '').split('/').map(decodeURIComponent); void pageSeat(id, template || ''); }
  else if (S.route === ASSISTANT) pageAssistant();
  else if (S.route === LEARNED) pageLearned();
  else if (S.route === SKILLS_CONNECT || S.route.startsWith(SKILLS_CONNECT + '?')) pageSkillsConnect();   // the skills helper's approve page
  else if (S.route.startsWith('#/person/')) {
    const parts = S.route.slice(9).split('/').map(decodeURIComponent);
    pagePerson(parts[0], parts[1]);
  }
  else if (isTasksRoute(S.route)) pageTasks();   // #/tasks?owner=bot:eng&tag=bug: the filters ride in the query
  else if (S.route === UPDATES || S.route.startsWith(UPDATES + '?')) pageUpdates();
  else if (S.route === SETTINGS) pageSettings();
  else if (S.route === HELP) { location.replace(HOME); window.supportOpen?.(); return; }   // Help is the Support pop-up, over Company
  else if (S.route === '#/getting-started') { location.replace(TASKS); return; }   // the checklist is gone
  else if (S.route === '#/repositories') { SETTINGS_TAB = 'repos'; location.replace(SETTINGS); return; }   // Settings > General › Repositories
  // Settings > Webhooks (it was Triggers) and its parts: incoming on Automations, outgoing on Tools, through the
  // Gateway on Models (ui/app/settings-webhooks.ts whOldAddress).
  else if (['#/triggers', '#/webhooks', '#/settings/triggers', '#/settings/webhooks'].includes(S.route) || S.route.startsWith('#/settings/webhooks/')) {
    location.replace(whOldAddress(S.route)); return;
  }
  else if (S.route === '#/health') { SETTINGS_TAB = 'health'; location.replace(SETTINGS); return; }   // Health moved into Settings
  // Settings > Inboxes and the tabs it replaced are Tools pages now: ui/app/tools.ts toolsRoute redirects them first.
  else if (S.route === '#/settings/roadmap') { SETTINGS_TAB = 'tasks'; ROADMAP_SETTINGS_REVEAL = true; location.replace(SETTINGS); return; }   // Health's roadmap suggestions fix
  else if (S.route === '#/settings/storage') { SETTINGS_TAB = 'storage'; location.replace(SETTINGS); return; }   // Health's Set up S3
  // Settings > System (it was Rules, before that Routines): Tico's own jobs are Automations' System jobs now.
  else if (['#/settings/rules', '#/settings/routines'].includes(S.route.split('?')[0] ?? '')) { location.replace(settingsMovedOut('system')); return; }
  else if (S.route === '#/settings/people') { SETTINGS_TAB = 'people'; location.replace(SETTINGS); return; }   // setup's people to invite
  else if (S.route === '#/settings/computers') { SETTINGS_TAB = 'devices'; location.replace(SETTINGS); return; }
  else if (['#/settings/subscriptions', '#/subscriptions'].includes(S.route)) { SETTINGS_TAB = 'models'; location.replace(SETTINGS); return; }   // Subscriptions are on Models
  // Old links and bookmarks to the Strategy Owner's settings address still open its card (its bot page's Settings).
  else if (S.route === '#/settings/ceo') { location.replace(SETTINGS + '/strategy-bot'); return; }
  // Every other #/settings/<tab>[/<card>] opens that tab, and the card when named (the settings catalogue's links,
  // backend/settings_entries.py). settingsShow maps old names and sections and puts the tab's own address back in
  // the bar, so a tab can be bookmarked and shared: #/settings/people, #/settings/models, …
  else if (/^#\/settings\/[a-z-]+(?:\/[a-z0-9-]+)?\/?(\?.*)?$/.test(S.route)) {
    const [path = '', query] = S.route.slice(11).split('?'), [tab = '', card] = path.split('/');
    // A System card (or the tab) that moved out of Settings opens where it went; Releases reads its bot first.
    const moved = settingsMovedOut(tab, card);
    if (moved) { if (moved !== 'wait') location.replace(moved); return; }
    if (tab === 'history') settingsHistoryAddress(query);   // History's view and filters (ui/app/settings-history.ts)
    SETTINGS_REVEAL = card ? '#' + card : '';
    // The Models card and Company turn levels moved from Computers to Models.
    SETTINGS_TAB = tab === 'devices' && SETTINGS_MOVED_TO_MODELS.includes(SETTINGS_REVEAL) ? 'models' : tab; location.replace(SETTINGS); return;
  }
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
    const id = S.route.startsWith(DOCS + '/') ? (S.route.slice(DOCS.length + 1).split('?')[0] ?? '') : '';
    location.hash = MARKET_ROUTE + (id ? '?note=' + encodeURIComponent(id) : '');
    return;
  }
  else if (S.route === DOCS || S.route.startsWith(DOCS + '/') || S.route.startsWith(DOCS + '?')) pageCompanyDocs();
  else if (S.route === '#/runs') { SETTINGS_TAB = 'runs'; location.replace(SETTINGS); return; }   // Runs moved into Settings > History
  else if (S.route === LEARNINGS || S.route.startsWith(LEARNINGS + '?')) pageLearnings();
  else if (S.route === '#/usage') pageUsage();
  else if (S.route === '#/replay' || S.route.startsWith('#/replay?')) pageReplay();
  else if (/^#\/computers\/[^/?]+\/screen$/.test(S.route)) pageComputerScreen(decodeURIComponent(S.route.split('/')[2] ?? ''), from);
  else if (/^#\/computers\/[^/?]+\/performance$/.test(S.route)) pageComputerPerformance(decodeURIComponent(S.route.split('/')[2] ?? ''));
  else if (isAutomationsRoute(S.route)) pageAutomations();
  else if ((S.route.split('?')[0] ?? '') === '#/style-guide') pageStyleGuide();   // out of the navigation (ui/app/style-guide.ts)
  else location.hash = HOME;           // unknown or empty routes land on Company, home
  redesignAfterRoute();
  thvSweep();         // a thread view the new page replaced stops following
  // A new page starts at its top; a query change (a filter, an open row) or a redraw keeps the reader's place.
  if (newPath) $('#main').scrollTop = 0;
}
// A move to another page draws the new page at once and fades its content in (motionPage, ui/app/motion.ts): #main
// alone, so the sidebar and the chat column never move or flash, and nothing sits over the page to take a press. A
// query change draws with no fade. Then the reader is put where they should be: at the place they left on Back and
// Forward, at the top of a page they went on to, with the keyboard's focus on the new page's title.
function routeChanged(): void {
  if (fileReviewOwns(location.hash)) { route(); return; }        // the review view opening or closing: no page fades
  const from = LAST_ROUTE, main: HTMLElement = $('#main');
  const back = routeEntry(main);
  const paged = !!from && routePage(location.hash || UPDATES) !== routePage(from);
  if (!paged) { route(); return; }
  motionPage(() => route());
  if (back !== null && LAST_ROUTE !== from) routeScrollBack(main, back);
  routeArrive(main);
}
// ---- the reader's place. Each history entry the app has drawn carries a number in its state, and the place the
// page was scrolled to when it was left is kept by that number. An entry met again (Back, Forward) has its number, so
// its place is put back; a new entry has none and starts at the top (route()). A page whose state is written over
// without the number (history.replaceState(null, ...)) reads as new: it opens at the top.
// A page scrolls in #main, or in a pane of its own (the Tasks list): the pane the reader last scrolled is kept by its
// id, so a pane with no id is not put back.
/** How far #main was scrolled, and the page's own pane (its id; '' for none) and how far that was. */
interface RoutePlace { top: number; pane: string; paneTop: number }
const ROUTE_SCROLL = new Map<number, RoutePlace>();
let ROUTE_PANE: HTMLElement | null = null;
document.addEventListener('scroll', ev => {
  const el = ev.target;
  if (el instanceof HTMLElement && el.id && el.id !== 'main' && el.closest('#main')) ROUTE_PANE = el;
}, {capture: true, passive: true});
// Numbers start from the clock: an entry numbered before a reload never shares one with an entry made after it.
let ROUTE_ENTRY = 0, ROUTE_ENTRIES = Date.now();
const routeEntryOf = (): number => Number((history.state as {nav?: unknown} | null)?.nav) || 0;
function routeEntryMark(): void {
  ROUTE_ENTRY = ++ROUTE_ENTRIES;
  history.replaceState({...(history.state as object | null), nav: ROUTE_ENTRY}, '');
}
/** Keeps the place of the entry being left, and returns the place kept for the entry arrived at (null: a new entry). */
function routeEntry(main: HTMLElement): RoutePlace | null {
  const pane = ROUTE_PANE?.isConnected && ROUTE_PANE.scrollTop > 0 ? ROUTE_PANE : null;
  if (ROUTE_ENTRY) ROUTE_SCROLL.set(ROUTE_ENTRY, {top: main.scrollTop, pane: pane?.id ?? '', paneTop: pane?.scrollTop ?? 0});
  ROUTE_PANE = null;
  const seen = routeEntryOf();
  if (!seen) { routeEntryMark(); return null; }
  ROUTE_ENTRY = seen;
  return ROUTE_SCROLL.get(seen) ?? null;
}
if (routeEntryOf()) ROUTE_ENTRY = routeEntryOf(); else routeEntryMark();
// Rows that are read after the page is drawn make it tall enough a moment later: the place is put back as they
// land, until it is reached, the reader scrolls or presses, another page opens, or four seconds pass.
let ROUTE_SEEK: (() => void) | null = null;
function routeScrollBack(main: HTMLElement, place: RoutePlace): void {
  ROUTE_SEEK?.();
  const reach = (): boolean => {
    main.scrollTop = place.top;
    if (main.scrollTop < place.top - 1) return false;
    if (!place.pane) return true;
    const pane = document.getElementById(place.pane);
    if (!pane || !main.contains(pane)) return false;
    pane.scrollTop = place.paneTop;
    return pane.scrollTop >= place.paneTop - 1;
  };
  if (reach()) return;
  const types = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
  const seek = new MutationObserver(() => { if (reach()) stop(); });
  const stop = (): void => {
    seek.disconnect(); clearTimeout(timer); ROUTE_SEEK = null;
    for (const type of types) window.removeEventListener(type, stop, true);
  };
  const timer = setTimeout(stop, 4000);
  for (const type of types) window.addEventListener(type, stop, {capture: true, passive: true});
  seek.observe(main, {childList: true, subtree: true});
  ROUTE_SEEK = stop;
}
// ---- the focus. After a page change the keyboard's focus is on the new page's title (its h1, or #main when it has
// none yet), so Tab goes on from the page and a screen reader says where it is; a page with no title yet is named in
// a live region. No ring is drawn when a pointer made the change. A page that focuses something of its own keeps
// it, and so does a field someone is typing in and an open dialog. A page drawn again as its rows arrive takes the
// focused title with it: the new one is focused, until the reader presses a key or the pointer.
let ROUTE_KEYS = false, ROUTE_FOCUS: (() => void) | null = null, ROUTE_SAY: HTMLElement | null = null;
window.addEventListener('keydown', () => { ROUTE_KEYS = true; }, true);
window.addEventListener('pointerdown', () => { ROUTE_KEYS = false; }, true);
function routeArrive(main: HTMLElement): void {
  ROUTE_FOCUS?.();
  const keys = ROUTE_KEYS, types = ['pointerdown', 'keydown'];
  const aim = (): void => {
    const at = document.activeElement;
    if (at instanceof HTMLElement && at !== document.body && at !== main && !main.contains(at)
      && (at.isContentEditable || at.matches('input, textarea, select') || !!at.closest('dialog[open]'))) return;
    if (at && at !== main && main.contains(at) && !at.hasAttribute('data-route-title')) return;
    if (document.querySelector('dialog[open]:modal')) return;
    const title = main.querySelector<HTMLElement>('h1') ?? main;
    if (title === at) return;
    if (!title.hasAttribute('tabindex')) title.tabIndex = -1;
    title.toggleAttribute('data-route-title', true);
    title.toggleAttribute('data-quiet-focus', !keys);
    title.focus({preventScroll: true});
    routeSay(title === main ? document.title : '');
  };
  const again = new MutationObserver(() => { const at = document.activeElement; if (!at || at === document.body || at === main) aim(); });
  const stop = (): void => {
    again.disconnect(); clearTimeout(timer); ROUTE_FOCUS = null;
    for (const type of types) window.removeEventListener(type, stop, true);
  };
  const timer = setTimeout(stop, 4000);
  for (const type of types) window.addEventListener(type, stop, {capture: true, passive: true});
  again.observe(main, {childList: true, subtree: true});
  ROUTE_FOCUS = stop;
  aim();
}
function routeSay(text: string): void {
  if (!ROUTE_SAY) {
    ROUTE_SAY = document.createElement('div');
    ROUTE_SAY.id = 'route-say';
    ROUTE_SAY.setAttribute('role', 'status');
    ROUTE_SAY.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap';
    document.body.appendChild(ROUTE_SAY);
  }
  if (ROUTE_SAY.textContent !== text) ROUTE_SAY.textContent = text;
}
window.addEventListener('hashchange', routeChanged);
