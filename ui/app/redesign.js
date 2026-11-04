/* ui/app/redesign.js — The app's shell (redesign PLAN §1, wave 1): the sidebar order, the Strategy tabs
   (Market / Strategy / Org / Goals / Budget), Growth's place under Strategy, the moved pages and the old addresses that lead to them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RD_GOALS = '#/strategy/goals', RD_ORG = '#/strategy/org', RD_BUDGET = '#/strategy/budget';
const RD_LISTENING = '#/strategy/market/listening', RD_AUTOMATIONS = '#/automations';
const rdUnder = (r, base) => r === base || r.startsWith(base + '/') || r.startsWith(base + '?');
// Where Goals lives: a Strategy tab.
const goalsBase = () => RD_GOALS;

// One "?" per panel: the explanation a row would otherwise carry, on hover, focus or tap.
const helpBubble = (text, attrs = '') => `<span class="rd-help"${attrs ? ' ' + attrs : ''} tabindex="0" role="button" aria-label="${esc(text)}">?<span class="rd-help-pop" role="tooltip">${esc(text)}</span></span>`;

// ----------------------------------------------------------------- the sidebar
// The rail is drawn whole in index.html, so the first paint is the one that stays: nothing here adds or moves a row.
// What waits on an answer starts as this browser last heard it (ui/theme.js marks <html> before the paint) and is set
// right here once the answer is in: Finance (finance.js finNavSync) and Finish setup (notices.js renderOnboardingNav).
// The answers are kept for whoever signed in here last (snapWho: this server, its owner, the person); /me answering as
// anyone else drops them before the config is applied (boot.js), and so does a sign-out (boot-snap.js bootSnapDrop).
// A kept answer only stands in until this visit's own arrives.
// A sign-out in any tab stops every tab writing, as the boot snapshot's epoch does (boot-snap.js): the kept entry
// carries an `epoch` a sign-out (or another person's /me) moves on, each tab notes the epoch its /me read, and a tab
// writes only while the entry still holds that epoch. The tab-wide sign-out message (SNAP_CHANNEL) drops the tab's
// person too, so nothing is written there until its own next /me.
const RD_NAV_KEPT = 'tico.nav.kept';
const RD_NAV_ROWS = {finance: 'nav-finance', welcome: 'nav-welcome'};
let RD_NAV_WHO = '', RD_NAV_EPOCH = 0;     // whose answers are kept now ('' until /me answers), and the epoch its /me read
function rdNavKept() {
  try { const kept = JSON.parse(localStorage.getItem(RD_NAV_KEPT) || 'null'); return kept && typeof kept === 'object' ? kept : {}; }
  catch { return {}; }
}
const rdNavEpoch = kept => Number(kept.epoch) || 0;
// Told another tab signed out: this one keeps nothing more until its own /me.
function rdNavStop() { RD_NAV_WHO = ''; }
// Signed out, or someone else: what was kept goes, the epoch moves on (every other tab stops writing), and nothing more
// is kept here until /me names who is here.
function rdNavForget() {
  RD_NAV_WHO = '';
  RD_NAV_EPOCH = rdNavEpoch(rdNavKept()) + 1;
  try { localStorage.setItem(RD_NAV_KEPT, JSON.stringify({epoch: RD_NAV_EPOCH})); } catch { /* nothing kept */ }
}
// The row and the mark on <html> together: the mark draws it before this file runs (styles/redesign.css), `hidden` after
// (search.js lists only rows that are not hidden).
function rdNavPaint(name, shown) {
  document.documentElement.classList.toggle(RD_NAV_ROWS[name], shown);
  const row = document.getElementById(RD_NAV_ROWS[name]);
  if (row && row.hidden !== !shown) row.hidden = !shown;   // an unchanged `hidden` is not written again (ask-panel.js askSet)
}
// This visit's answer: shown as it says, and kept for the next first paint while no sign-out came since this tab's /me.
function rdNavShow(name, shown) {
  rdNavPaint(name, shown);
  if (!RD_NAV_WHO || SESSION.out) return;
  const kept = rdNavKept();
  if (rdNavEpoch(kept) !== RD_NAV_EPOCH) { RD_NAV_WHO = ''; return; }
  const mine = kept.who === RD_NAV_WHO;
  if (mine && kept[name] === shown) return;
  try { localStorage.setItem(RD_NAV_KEPT, JSON.stringify({...(mine ? kept : {}), epoch: RD_NAV_EPOCH, who: RD_NAV_WHO, [name]: shown})); }
  catch { /* not kept */ }
}
// /me answered: someone else's answers go, and their rows with them, until this person's own come in.
function rdNavWho(me) {
  const who = me ? snapWho(me) : '', kept = rdNavKept();
  RD_NAV_EPOCH = rdNavEpoch(kept);
  if (!who || kept.who !== who) {
    if (kept.who || !who) rdNavForget();
    for (const name of Object.keys(RD_NAV_ROWS)) rdNavPaint(name, false);
  }
  RD_NAV_WHO = who;
}
for (const name of Object.keys(RD_NAV_ROWS)) rdNavPaint(name, document.documentElement.classList.contains(RD_NAV_ROWS[name]));
// Library open or folded: what this browser chose, opened while a Library page is showing (a fold there holds until
// the next visit). A browser that will not store it keeps Library open.
const RD_LIB_KEY = 'tico.nav.library';
const RD_LIB = {inside: false, was: false};
const rdInLibrary = r => [DOCS, MEETINGS, '#/library'].some(base => rdUnder((r || '').split('?')[0], base));
function rdLibRemember(open) { try { localStorage.setItem(RD_LIB_KEY, open ? 'open' : 'closed'); } catch { /* not kept */ } }
function rdLibSaved() { try { return localStorage.getItem(RD_LIB_KEY) !== 'closed'; } catch { return true; } }
const rdLibOpen = () => RD_LIB.inside || rdLibSaved();
// Rules folds the other way round: folded unless this browser opened it, opened while a Rules page is showing.
const RD_RULES_KEY = 'tico.nav.rules';
const RD_RULES = {inside: false, was: false};
function rdRulesRemember(open) { try { localStorage.setItem(RD_RULES_KEY, open ? 'open' : 'closed'); } catch { /* not kept */ } }
function rdRulesSaved() { try { return localStorage.getItem(RD_RULES_KEY) === 'open'; } catch { return false; } }
const rdRulesOpen = () => RD_RULES.inside || rdRulesSaved();
function rdLibSync() {
  rdRulesSync();
  const subs = document.getElementById('rd-lib'), toggle = document.querySelector('.rd-lib-toggle:not(.rd-rules-toggle)');
  if (!subs || !toggle) return;
  const here = rdInLibrary(S.route);
  if (here && !RD_LIB.was) RD_LIB.inside = true;
  if (!here) RD_LIB.inside = false;
  RD_LIB.was = here;
  const open = rdLibOpen();
  subs.hidden = !open;
  document.documentElement.classList.toggle('nav-lib-shut', !open);   // ui/theme.js set it for the first paint
  toggle.setAttribute('aria-expanded', String(open));
}
function rdRulesSync() {
  const subs = document.getElementById('rd-rules'), toggle = document.querySelector('.rd-rules-toggle');
  if (!subs || !toggle) return;
  const here = rdUnder((S.route || '').split('?')[0], RULES_ROUTE);
  if (here && !RD_RULES.was) RD_RULES.inside = true;
  if (!here) RD_RULES.inside = false;
  RD_RULES.was = here;
  const open = rdRulesOpen();
  subs.hidden = !open;
  document.documentElement.classList.toggle('nav-rules-open', open);
  toggle.setAttribute('aria-expanded', String(open));
}
// The folds' chevrons.
$('#side .rd-lib-toggle:not(.rd-rules-toggle)').onclick = () => { const open = rdLibOpen(); RD_LIB.inside = false; rdLibRemember(!open); rdLibSync(); };
$('#side .rd-rules-toggle').onclick = () => { const open = rdRulesOpen(); RD_RULES.inside = false; rdRulesRemember(!open); rdLibSync(); };
// The current page's highlight for what the redesign adds or moves (sidebar.js asks, after its own checks).
function redesignNavCurrent(nav) {
  const r = S.route || '';
  if (nav === 'automations') return rdUnder(r, RD_AUTOMATIONS);
  if (nav === 'tools') return rdUnder(r, RD_TOOLS);
  if (nav === 'connections') return r.split('?')[0] === RD_CONNECTIONS;
  if (nav === 'files') return rdUnder(r, RD_FILES);
  if (nav === 'strategy') return rdUnder(r, '#/strategy');
  if (nav === 'growth') return rdUnder(r, GR_ROUTE);
  if (nav === 'finance') return rdUnder(r, FIN_ROUTE);
  if (nav === 'rules') return rdUnder(r, RULES_ROUTE);
  if (nav.startsWith('rules-')) return rdUnder(r, RULES_ROUTE) && ruTabOf(r) === nav.slice(6);
  return false;
}

// ----------------------------------------------------------------- routes
// Runs before the router's own list (router.js); true when it handled the address.
function redesignRoute(r) {
  // Old addresses of what moved.
  if (rdUnder(r, GOALS)) { location.replace(RD_GOALS + r.slice(GOALS.length)); return true; }
  if (r === '#/settings/listening') { location.replace(RD_LISTENING); return true; }
  if (r === '#/library') { location.replace(DOCS); return true; }
  if (rdUnder(r, GR_OLD_ROUTE)) { location.replace(GR_ROUTE + r.slice(GR_OLD_ROUTE.length)); return true; }
  if (rdUnder(r, GTM_OLD_ROUTE + '/sources')) { location.replace(GR_SOURCES); return true; }   // Sources is growth's now
  if (rdUnder(r, GTM_OLD_ROUTE)) { location.replace('#/strategy'); return true; }
  if (['#/recurring', '#/tasks/routines'].includes(r.split('?')[0])) { location.replace(RD_AUTOMATIONS); return true; }   // the Tasks page's Routines view
  if (rdUnder(r, RD_AUTOMATIONS)) {
    // The Automations page is its own files (ui/app/automations*.js); until they load, an empty mount point.
    if (typeof window.pageAutomations === 'function') window.pageAutomations();
    else $('#main').innerHTML = '<div id="automations-root" class="rd-automations"></div>';
    return true;
  }
  if (rdToolsRoute(r)) return true;
  if (rdUnder(r, BRAND_ROUTE)) { pageBrand(); return true; }   // ui/app/brand-page.js
  if (rdUnder(r, GR_ROUTE)) { pageGrowth(); return true; }   // ui/app/growth-page.js
  if (rdUnder(r, RULES_ROUTE)) { pageRules(); return true; }   // ui/app/org-rules.js
  if (pagesRoute(r)) return true;   // Library › Files and #/page/<id> (ui/app/pages.js)
  if (rdUnder(r, RD_GOALS)) { pageGoals(); return true; }
  if (rdUnder(r, RD_ORG)) { pageRdOrg(); return true; }
  if (rdUnder(r, RD_BUDGET)) { pageBudget(); return true; }
  if (rdUnder(r, FIN_ROUTE)) { pageFinance(); return true; }   // ui/app/finance.js
  if (rdUnder(r, CP_ROUTE)) { pageCompanyPage(); return true; }   // ui/app/company-page.js
  if (rdUnder(r, RD_LISTENING)) { pageRdListening(); return true; }
  // Market opens on the latest briefing.
  if (r === MARKET_ROUTE || rdUnder(r, RD_BRIEFING)) { pageRdBriefing(); return true; }
  return false;
}
// After every route: leave Automations' and Tools' state when off their pages.
function redesignAfterRoute() {
  if (!rdUnder(S.route || '', RD_AUTOMATIONS)) window.automationsStop?.();
  if (!rdUnder(S.route || '', RD_TOOLS)) rdToolsStop();
  if (!rdUnder(S.route || '', RULES_ROUTE)) rulesStop();
  pagesStop(S.route || '');
  rdLibSync();
  financeStop();
  growthStop();
  companyPageStop();
  finProbeOnce();   // the Finance link shows once the viewer sees something in Finance
}

// The Strategy section's heading with the redesign's tabs. Lenses are the Strategy tab's second view; Brand opens
// from the Strategy tab and keeps it lit.
function redesignStrategyHead(tab) {
  const on = ['lenses', 'brand'].includes(tab) ? 'strategy' : tab;
  const tabs = [['market', MARKET_ROUTE, 'Market'], ['strategy', '#/strategy', 'Strategy'], ['org', RD_ORG, 'Org'],
    ['goals', RD_GOALS, 'Goals'], ['budget', RD_BUDGET, 'Budget']];
  const sub = tab === 'strategy' || tab === 'lenses'
    ? `<a class="strat-sub" href="${tab === 'lenses' ? '#/strategy' : LENSES_ROUTE}">${tab === 'lenses' ? 'Strategy' : 'Lenses'}</a>` : '';
  return `<div class="strat-head rd-strat-head"><h1>Strategy</h1>
      <nav class="tabs slim strat-tabs" aria-label="Strategy">${tabs.map(([id, href, label]) =>
        `<a href="${href}" data-strat-tab="${id}"${on === id ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>
      <span class="spacer"></span>${sub}<div class="strat-actions" id="strat-actions"></div></div>`;
}

// Org (ui/app/redesign-org.js), Market's Briefing and Listening (ui/app/redesign-market.js) are their own files.
