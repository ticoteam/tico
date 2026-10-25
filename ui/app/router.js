/* ui/app/router.js — route(): hash routes to pages
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let LAST_ROUTE = '';
function route() {
  if (location.hash.startsWith('#/settings?recruit=')) {
    try {
      const raw = new URLSearchParams(location.hash.split('?')[1]).get('recruit');
      if (raw && raw.length <= 5000) {
        const value = JSON.parse(raw);
        if (value && typeof value.title === 'string' && typeof value.brief === 'string') RECRUIT_HANDOFF = value;
      }
    } catch { /* A malformed external handoff never changes a bot. */ }
    SETTINGS_TAB = 'bots'; location.replace(SETTINGS); return;
  }
  overviewStop();
  GOAL_MANAGER_STOP?.();
  $('#main').classList.remove('overview-layout');
  const from = LAST_ROUTE;
  S.route = LAST_ROUTE = location.hash || UPDATES;
  if (S.route !== SETTINGS) { clearTimeout(SETTINGS_PRESENCE.timer); SETTINGS_PRESENCE.timer = null; }
  const botOf = r => r.startsWith('#/bot/') ? r.slice(6).split('/')[0] : '';
  const botHere = botOf(S.route);
  document.body.classList.toggle('bot-page', !!botHere);   // a phone gives the bot page the whole screen
  window.TicoObservability?.route(S.route);
  document.body.classList.remove('mobile-composer-focus');
  // Every page starts from a bare #main: a layout class left behind (the bot page's bot-split-layout made #main a
  // two-column grid) would lay the next page out in that grid. Each page adds its own again; the Librarian's
  // rail keeps its own class (ui/docs-ask.js re-syncs it after the route).
  const main = $('#main');
  main.className = main.classList.contains('librarian-open') ? 'librarian-open' : '';
  // A new page fades in (opacity only); a query change within the same page does not.
  if (from && from.split('?')[0] !== S.route.split('?')[0] && main.animate && !matchMedia('(prefers-reduced-motion: reduce)').matches)
    main.animate({opacity: [0, 1]}, {duration: 120, easing: 'cubic-bezier(.2,0,0,1)'});
  document.body.classList.remove('task-peek-open');
  if (!isMarketRoute(S.route)) window.marketStop?.();
  if (!isStrategyRoute(S.route)) window.strategyStop?.();
  if (!isLensesRoute(S.route)) window.lensesStop?.();
  taskChatStop(); $('#task-modal')?.close();
  if (!S.route.startsWith(DOCS)) DOC_LOAD++;
  // Leaving Mail abandons its fetches, its debounced search (which would set the hash back) and
  // its accumulated page, so returning re-reads the server instead of drawing a stale list.
  if (!S.route.startsWith(MAIL)) { MAIL_LOAD++; clearTimeout(MAIL_SEARCH); mailForget(); }
  if (!S.route.startsWith(MESSAGING)) { MESSAGING_LOAD++; MESSAGING_LIST = null; }
  if (S.route !== DECIDE) decideStop();     // its waiting answer goes now
  convStop(); meetStop(); onbStop(); TASKS_ST?.layoutAbort?.abort(); TASKS_ST = null; // page-local work never outlives its page
  if (S.route !== '#/usage') USE = null;
  if (!S.route.startsWith('#/replay')) replayStop();
  if (!/^#\/computers\/[^/?]+\/screen$/.test(S.route)) screenStop();
  if (!/^#\/computers\/[^/?]+\/performance$/.test(S.route)) perfStop();
  if (!(S.route === UPDATES || S.route.startsWith(UPDATES + '?'))) { if (UPD) { UPD.io?.disconnect(); void updFlush(UPD); UPD = null; } updcStop(); }
  if (!S.route.startsWith('#/bot/')) botStopped();
  renderTree();
  if (S.route === NOTES || S.route === RECORDINGS || S.route.startsWith(RECORDINGS + '?')) {
    // Old links land on Meetings, keeping a deep link to one meeting.
    const id = new URLSearchParams(S.route.split('?')[1] || '').get('recording');
    location.hash = MEETINGS + (id ? '?meeting=' + encodeURIComponent(id) : ''); return;
  }
  else if (S.route === '#/inbox' || S.route.startsWith('#/inbox?')) { location.hash = MAIL; return; }
  else if (S.route === OVERVIEW) pageOverview();
  else if (S.route === WELCOME) pageWelcome();
  else if (S.route === '#/roadmap') pageRoadmap();
  else if (S.route === '#/tags') pageTags();
  else if (S.route.startsWith('#/tag/')) pageTag(decodeURIComponent(S.route.slice(6)));
  else if (S.route.startsWith('#/task/')) pageTasks('', decodeURIComponent(S.route.slice(7)));
  else if (S.route.startsWith(TASKS + '/')) { location.replace('#/task/' + S.route.slice(TASKS.length + 1).split('?')[0]); return; }   // #/tasks/<id> is the same task
  else if (S.route === CHAT) { location.hash = TASKS; return; }
  else if (S.route === DECIDE) pageDecide();
  else if (S.route === MEETINGS || S.route.startsWith(MEETINGS + '?')) pageNotes();
  else if (S.route.startsWith('#/bot/')) { const [slug, tab] = S.route.slice(6).split('/'); pageBot(slug, tab); }
  else if (S.route === ASSISTANT) pageAssistant();
  else if (S.route === LEARNED) pageLearned();
  else if (S.route.startsWith('#/person/')) {
    const parts = S.route.slice(9).split('/').map(decodeURIComponent);
    pagePerson(parts[0], parts[1]);
  }
  else if (isTasksRoute(S.route)) {
    // #/tasks?owner=bot:eng&tag=bug: the filters ride in the query (ui/app/task-list.js)
    const base = S.route.split('?')[0];
    pageTasks(base === BOARD ? 'board' : base === ISSUES ? 'list' : base === RECURRING ? 'recurring' : '');
  }
  else if (S.route === UPDATES || S.route.startsWith(UPDATES + '?')) pageUpdates();
  else if (S.route === GOALS || S.route.startsWith(GOALS + '/') || S.route.startsWith(GOALS + '?')) pageGoals();
  else if (S.route === SETTINGS) pageSettings();
  else if (S.route === HELP) { location.replace(OVERVIEW); window.supportOpen?.(); return; }   // Help is the Support pop-up, over Overview
  else if (S.route === '#/getting-started') { location.replace(TASKS); return; }   // the checklist is gone
  else if (S.route === '#/repositories') { SETTINGS_TAB = 'repos'; location.replace(SETTINGS); return; }   // Settings > Repositories
  else if (S.route === '#/settings/listening') { SETTINGS_TAB = 'listening'; location.replace(SETTINGS); return; }   // Settings > Listening
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
  // Settings > Rules; `?task=` narrows the wake rules to one task's (the task modal's Wake rules link).
  else if (S.route === '#/settings/rules' || S.route.startsWith('#/settings/rules?')) {
    // ?task=<id>, ?bot=<slug>, or the API's own scope_kind=task|bot&scope_id=<id>.
    const q = new URLSearchParams(S.route.split('?')[1] || ''), kind = q.get('task') ? 'task' : q.get('bot') ? 'bot' : q.get('scope_kind');
    const id = q.get('task') || q.get('bot') || q.get('scope_id') || '';
    RULES_FOR = ['task', 'bot'].includes(kind) && id ? `${kind}:${id}` : ''; RULES_FOR_TITLE = '';
    SETTINGS_TAB = 'recurring'; SETTINGS_REVEAL = RULES_FOR ? '#set-rules-card' : ''; location.replace(SETTINGS); return;
  }
  else if (S.route === '#/settings/routines') { SETTINGS_TAB = 'recurring'; location.replace(SETTINGS); return; }   // General > Team's moved switches
  else if (S.route === '#/settings/people') { SETTINGS_TAB = 'people'; location.replace(SETTINGS); return; }   // setup's people to invite
  else if (['#/settings/subscriptions', '#/subscriptions', '#/settings/computers'].includes(S.route)) { SETTINGS_TAB = 'devices'; location.replace(SETTINGS); return; }   // Subscriptions moved to Computers
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
  else location.hash = UPDATES;         // unknown or empty routes land on Updates, home
  window.syncLibrarianRail?.();
  $('#main').scrollTop = 0;
}
window.addEventListener('hashchange', route);
