/* ui/app/redesign.ts — The app's shell (redesign PLAN §1, wave 1): the sidebar order, the Strategy tabs
   (Market / Strategy / Org / Goals / Budget), Growth's place under Strategy, the moved pages and the old addresses that lead to them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RD_GOALS = '#/strategy/goals', RD_ORG = '#/strategy/org', RD_BUDGET = '#/strategy/budget';
const RD_LISTENING = '#/listening', RD_AUTOMATIONS = '#/automations';
const rdUnder = (r: string, base: string): boolean => r === base || r.startsWith(base + '/') || r.startsWith(base + '?');
// Where Goals lives: a Strategy tab.
const goalsBase = () => RD_GOALS;

// One "?" per panel: the explanation a row would otherwise carry, on hover, focus or tap.
const helpBubble = (text: string, attrs = ''): string => `<span class="rd-help"${attrs ? ' ' + attrs : ''} tabindex="0" role="button" aria-label="${esc(text)}">?<span class="rd-help-pop" role="tooltip">${esc(text)}</span></span>`;

// ----------------------------------------------------------------- the sidebar
// The rail is drawn whole in index.html, so the first paint is the one that stays: nothing here adds or moves a row.
// What waits on an answer starts as this browser last heard it (ui/theme.ts marks <html> before the paint) and is set
// right here once the answer is in: Finance (finance.ts finNavSync) and Finish setup (notices.js renderOnboardingNav).
// The answers are kept for whoever signed in here last (snapWho: this server, its owner, the person); /me answering as
// anyone else drops them before the config is applied (boot.js), and so does a sign-out (boot-snap.js bootSnapDrop).
// A kept answer only stands in until this visit's own arrives.
// A sign-out in any tab stops every tab writing, as the boot snapshot's epoch does (boot-snap.js): the kept entry
// carries an `epoch` a sign-out (or another person's /me) moves on, each tab notes the epoch its /me read, and a tab
// writes only while the entry still holds that epoch. The tab-wide sign-out message (SNAP_CHANNEL) drops the tab's
// person too, so nothing is written there until its own next /me. The epoch is read as boot's reads go out, so a
// sign-out elsewhere while they are out leaves this visit's /me keeping nothing.
const RD_NAV_KEPT = 'tico.nav.kept';
/** The sidebar rows that wait on an answer. */
type RdNavName = 'finance' | 'welcome';
/** What this browser keeps for the sidebar: the epoch, whose answers they are, and each row's last answer. */
interface RdNavKept { epoch?: unknown; who?: string; finance?: boolean; welcome?: boolean; }
const RD_NAV_ROWS: Record<RdNavName, string> = {finance: 'nav-finance', welcome: 'nav-welcome'};
const RD_NAV_NAMES = Object.keys(RD_NAV_ROWS) as RdNavName[];
let RD_NAV_WHO = '', RD_NAV_EPOCH = 0;     // whose answers are kept now ('' until /me answers), and the epoch boot read
let RD_NAV_STOPPED = false;                // another tab signed out since boot's reads went out
function rdNavKept(): RdNavKept {
  try { const kept = JSON.parse(localStorage.getItem(RD_NAV_KEPT) || 'null') as RdNavKept | null; return kept && typeof kept === 'object' ? kept : {}; }
  catch { return {}; }
}
const rdNavEpoch = (kept: RdNavKept): number => Number(kept.epoch) || 0;
// Told another tab signed out: this one keeps nothing more until its own /me.
function rdNavStop() { RD_NAV_WHO = ''; RD_NAV_STOPPED = true; }
// Boot's reads go out: the epoch they answer for.
function rdNavStart() { RD_NAV_EPOCH = rdNavEpoch(rdNavKept()); RD_NAV_STOPPED = false; }
// Signed out, or someone else: what was kept goes, the epoch moves on (every other tab stops writing), and nothing more
// is kept here until /me names who is here.
function rdNavForget() {
  RD_NAV_WHO = '';
  RD_NAV_EPOCH = rdNavEpoch(rdNavKept()) + 1;
  try { localStorage.setItem(RD_NAV_KEPT, JSON.stringify({epoch: RD_NAV_EPOCH})); } catch { /* nothing kept */ }
}
// The row and the mark on <html> together: the mark draws it before this file runs (styles/redesign.css), `hidden` after
// (search.js lists only rows that are not hidden).
function rdNavPaint(name: RdNavName, shown: boolean): void {
  document.documentElement.classList.toggle(RD_NAV_ROWS[name], shown);
  const row = document.getElementById(RD_NAV_ROWS[name]);
  if (row && row.hidden !== !shown) row.hidden = !shown;   // an unchanged `hidden` is not written again (ask-panel.js askSet)
}
// This visit's answer: shown as it says, and kept for the next first paint while no sign-out came since this tab's /me.
function rdNavShow(name: RdNavName, shown: boolean): void {
  rdNavPaint(name, shown);
  if (!RD_NAV_WHO || SESSION.out) return;
  const kept = rdNavKept();
  if (rdNavEpoch(kept) !== RD_NAV_EPOCH) { RD_NAV_WHO = ''; return; }
  const mine = kept.who === RD_NAV_WHO;
  if (mine && kept[name] === shown) return;
  // Not atomic with the check (no cross-tab lock): a sign-out in between keeps this answer until the next /me, as a switch without one does.
  try { localStorage.setItem(RD_NAV_KEPT, JSON.stringify({...(mine ? kept : {}), epoch: RD_NAV_EPOCH, who: RD_NAV_WHO, [name]: shown})); }
  catch { /* not kept */ }
}
// /me answered: someone else's answers go, and their rows with them, until this person's own come in. A sign-out
// anywhere since boot's reads went out (the epoch moved, or the stop came) leaves this visit keeping nothing.
function rdNavWho(me: SelfPerson | null | undefined): void {
  const who = me ? snapWho(me) : '', kept = rdNavKept();
  if (RD_NAV_STOPPED || rdNavEpoch(kept) !== RD_NAV_EPOCH) {
    RD_NAV_WHO = '';
    for (const name of RD_NAV_NAMES) rdNavPaint(name, false);
    return;
  }
  if (!who || kept.who !== who) {
    if (kept.who || !who) rdNavForget();
    for (const name of RD_NAV_NAMES) rdNavPaint(name, false);
  }
  RD_NAV_WHO = who;
}
for (const name of RD_NAV_NAMES) rdNavPaint(name, document.documentElement.classList.contains(RD_NAV_ROWS[name]));
// Library open or folded: folded unless opened here, opened while a Library page is showing (a fold there holds until
// the next visit). A browser that will not store it keeps the choice for this visit (RD_LIB.mem).
const RD_LIB_KEY = 'tico.nav.library';
const RD_LIB = {inside: false, was: false, mem: false};
// Contacts sits on the Team header, so its page (and its old Directory address) leaves Library as it is.
const rdInLibrary = (r: string): boolean => { const p = (r || '').split('?')[0] ?? ''; return !rdUnder(p, DIR_ROUTE) && !rdUnder(p, DIR_OLD_ROUTE) && [DOCS, MEETINGS, TOOLS_ROUTE, '#/library'].some(base => rdUnder(p, base)); };
function rdLibRemember(open: boolean): void { RD_LIB.mem = open; try { localStorage.setItem(RD_LIB_KEY, open ? 'open' : 'closed'); } catch { /* not kept */ } }
function rdLibSaved() { try { return localStorage.getItem(RD_LIB_KEY) === 'open'; } catch { return RD_LIB.mem; } }
const rdLibOpen = () => RD_LIB.inside || rdLibSaved();
// Company folds the same way: folded unless opened here, opened while Strategy, Finance, Growth, Automations, Listening
// or Rules is showing (its own page, #/home, shows its row whatever the fold).
const RD_CO_KEY = 'tico.nav.company';
const RD_CO = {inside: false, was: false, mem: false};
const rdInCompany = (r: string): boolean => ['#/strategy', GR_ROUTE, FIN_ROUTE, RD_AUTOMATIONS, RD_LISTENING, RULES_ROUTE].some(base => rdUnder((r || '').split('?')[0] ?? '', base));
function rdCoRemember(open: boolean): void { RD_CO.mem = open; try { localStorage.setItem(RD_CO_KEY, open ? 'open' : 'closed'); } catch { /* not kept */ } }
function rdCoSaved() { try { return localStorage.getItem(RD_CO_KEY) === 'open'; } catch { return RD_CO.mem; } }
const rdCoOpen = () => RD_CO.inside || rdCoSaved();
function rdLibSync() {
  rdCoSync();
  const subs = document.getElementById('rd-lib'), toggle = document.querySelector('#side .rd-lib-toggle:not(.rd-co-toggle)');
  if (!subs || !toggle) return;
  const here = rdInLibrary(S.route);
  if (here && !RD_LIB.was) RD_LIB.inside = true;
  if (!here) RD_LIB.inside = false;
  RD_LIB.was = here;
  const open = rdLibOpen();
  subs.hidden = !open;
  document.documentElement.classList.toggle('nav-lib-shut', !open);   // ui/theme.ts set it for the first paint
  toggle.setAttribute('aria-expanded', String(open));
}
function rdCoSync() {
  const subs = document.getElementById('rd-co'), toggle = document.querySelector('#side .rd-co-toggle');
  if (!subs || !toggle) return;
  const here = rdInCompany(S.route);
  if (here && !RD_CO.was) RD_CO.inside = true;
  if (!here) RD_CO.inside = false;
  RD_CO.was = here;
  const open = rdCoOpen();
  subs.hidden = !open;
  document.documentElement.classList.toggle('nav-co-shut', !open);   // ui/theme.ts set it for the first paint
  toggle.setAttribute('aria-expanded', String(open));
}
// The folds' chevrons.
$('#side .rd-lib-toggle:not(.rd-co-toggle)').onclick = () => { const open = rdLibOpen(); RD_LIB.inside = false; rdLibRemember(!open); rdLibSync(); };
$('#side .rd-co-toggle').onclick = () => { const open = rdCoOpen(); RD_CO.inside = false; rdCoRemember(!open); rdLibSync(); };
// The current page's highlight for what the redesign adds or moves (sidebar.js asks, after its own checks).
function redesignNavCurrent(nav: string | undefined): boolean {
  if (!nav) return false;   // sidebar.ts passes a row's data-nav, which a row without one lacks (the old code threw here)
  const r = S.route || '';
  if (nav === 'automations') return rdUnder(r, RD_AUTOMATIONS);
  if (nav === 'listening') return rdUnder(r, RD_LISTENING);
  if (nav === 'tools') return rdUnder(r, TOOLS_ROUTE);
  if (nav === 'files') return rdUnder(r, RD_FILES);
  if (nav === 'skills') return rdUnder(r, SKILLS_ROUTE);
  if (nav === 'contacts') return rdUnder(r, DIR_ROUTE);
  if (nav === 'strategy') return rdUnder(r, '#/strategy');
  if (nav === 'growth') return rdUnder(r, GR_ROUTE);
  if (nav === 'finance') return rdUnder(r, FIN_ROUTE);
  if (nav === 'rules') return rdUnder(r, RULES_ROUTE);
  if (nav === 'calendar') return rdUnder(r, CAL_ROUTE);
  return false;
}

// ----------------------------------------------------------------- routes
// Runs before the router's own list (router.js); true when it handled the address.
function redesignRoute(r: string): boolean {
  // Old addresses of what moved.
  if (rdUnder(r, GOALS)) { location.replace(RD_GOALS + r.slice(GOALS.length)); return true; }
  // Listening is its own page; its old places (a Settings tab, then a view of Market) lead to it.
  if (r === '#/settings/listening' || r === '#/strategy/market/listening') { location.replace(RD_LISTENING); return true; }
  if (r === '#/library') { location.replace(DOCS); return true; }
  if (rdUnder(r, GR_OLD_ROUTE)) { location.replace(GR_ROUTE + r.slice(GR_OLD_ROUTE.length)); return true; }
  if (rdUnder(r, GTM_OLD_ROUTE + '/sources')) { location.replace(GR_SOURCES); return true; }   // Sources is growth's now
  if (rdUnder(r, GTM_OLD_ROUTE)) { location.replace('#/strategy'); return true; }
  if (['#/recurring', '#/tasks/routines'].includes(r.split('?')[0] ?? '')) { location.replace(RD_AUTOMATIONS); return true; }   // the Tasks page's Routines view
  if (rdUnder(r, RD_AUTOMATIONS) || rdUnder(r, RD_LISTENING)) {
    // The Automations page is its own files (ui/app/automations*.js); until they load, an empty mount point.
    if (typeof window.pageAutomations === 'function') window.pageAutomations();
    else $('#main').innerHTML = '<div id="automations-root" class="rd-automations"></div>';
    return true;
  }
  if (toolsRoute(r)) return true;   // Tools and every old address of what it holds (ui/app/tools.ts)
  if (rdUnder(r, BRAND_ROUTE)) { pageBrand(); return true; }   // ui/app/brand-page.ts
  if (rdUnder(r, GR_ROUTE)) { pageGrowth(); return true; }   // ui/app/growth-page.ts
  if (rdUnder(r, RULES_ROUTE)) { pageRules(); return true; }   // ui/app/org-rules.ts
  if (skillsRoute(r)) return true;   // Library › Skills (ui/app/skills.ts)
  if (directoryRoute(r)) return true;   // Library › Contacts and its old #/library/directory (ui/app/directory.ts)
  if (pagesRoute(r)) return true;   // Library › Files and #/page/<id> (ui/app/pages.ts)
  if (rdUnder(r, RD_GOALS)) { pageGoals(); return true; }
  if (rdUnder(r, RD_ORG)) { pageRdOrg(); return true; }
  if (rdUnder(r, RD_BUDGET)) { pageBudget(); return true; }
  if (rdUnder(r, FIN_ROUTE)) { pageFinance(); return true; }   // ui/app/finance.ts
  if (rdUnder(r, CP_ROUTE)) { pageCompanyPage(); return true; }   // ui/app/company-page.ts
  return false;
}
// After every route: leave Automations' and Tools' state when off their pages.
function redesignAfterRoute() {
  if (!rdUnder(S.route || '', RD_AUTOMATIONS) && !rdUnder(S.route || '', RD_LISTENING)) window.automationsStop?.();
  if (!rdUnder(S.route || '', TOOLS_ROUTE)) toolsStop();
  if (!rdUnder(S.route || '', RULES_ROUTE)) rulesStop();
  pagesStop(S.route || '');
  skillsStop(S.route || '');
  directoryStop(S.route || '');
  rdLibSync();
  financeStop();
  growthStop();
  companyPageStop();
  finProbeOnce();   // the Finance link shows once the viewer sees something in Finance
}

// The Strategy section's heading with the redesign's tabs. Lenses are the Strategy tab's second view; Brand opens
// from the Strategy tab and keeps it lit.
function redesignStrategyHead(tab: string): string {
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

// Org (ui/app/redesign-org.ts) and the Market page (ui/app/redesign-market.ts) are their own files.
