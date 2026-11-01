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
// The rail is rearranged in place, once, not drawn again: the links keep their own badges and handlers (sidebar.js,
// notices.js).
let RD_NAV_DONE = false;
const RD_NAV_ICONS = {automations: 'bolt', library: 'local_library', strategy: 'flag', growth: 'rocket_launch', rules: 'gavel', operations: 'inventory_2'};
function redesignNav() {
  const nav = $('#side .side-scroll');
  if (!nav || RD_NAV_DONE) return;
  RD_NAV_DONE = true;
  const strategyLink = nav.querySelector('a[data-nav="strategy"]'), before = [...nav.children];
  const pick = sel => nav.querySelector(sel);
  const link = (key, href, icon, label) => {
    const a = Object.assign(document.createElement('a'), {className: 'nav-link', href, innerHTML: `<span class="nav-icon" aria-hidden="true">${icon}</span>${label}`});
    a.dataset.nav = key;
    return a;
  };
  const automations = link('automations', RD_AUTOMATIONS, RD_NAV_ICONS.automations, 'Automations');
  const growth = link('growth', GR_ROUTE, RD_NAV_ICONS.growth, 'Growth');   // ui/app/growth-page.js
  const operations = link('operations', OPS_ROUTE, RD_NAV_ICONS.operations, 'Operations');   // ui/app/operations-page.js
  const library = link('library', DOCS, RD_NAV_ICONS.library, 'Library'); library.classList.add('rd-lib-head');
  // Library's pages fold under it: a chevron beside it, remembered in this browser (rdLibSync).
  const libRow = document.createElement('div'); libRow.className = 'nav-row rd-lib-row';
  const libToggle = Object.assign(document.createElement('button'), {type: 'button', className: 'rd-lib-toggle',
    innerHTML: '<span class="section-chev" aria-hidden="true">›</span>'});
  libToggle.setAttribute('aria-label', 'Library pages');
  libToggle.setAttribute('aria-controls', 'rd-lib');
  libToggle.onclick = () => { const open = rdLibOpen(); RD_LIB.inside = false; rdLibRemember(!open); rdLibSync(); };
  libRow.append(library, libToggle);
  const subs = document.createElement('div'); subs.className = 'rd-lib'; subs.id = 'rd-lib';
  const tools = link('tools', RD_TOOLS, 'api', 'Tools');   // ui/app/redesign-library.js
  const files = link('files', RD_FILES, 'folder_shared', 'Files');   // ui/app/pages.js
  subs.append(...[pick('a[data-nav="docs"]'), files, pick('a[data-nav="meetings"]'), tools].filter(Boolean));
  // Rules (ui/app/org-rules.js) folds the same way, its tabs under it, folded until opened.
  const rules = link('rules', RULES_ROUTE, RD_NAV_ICONS.rules, 'Rules'); rules.classList.add('rd-lib-head');
  const rulesRow = document.createElement('div'); rulesRow.className = 'nav-row rd-lib-row rd-rules-row';
  const rulesToggle = Object.assign(document.createElement('button'), {type: 'button', className: 'rd-lib-toggle rd-rules-toggle',
    innerHTML: '<span class="section-chev" aria-hidden="true">›</span>'});
  rulesToggle.setAttribute('aria-label', 'Rules pages');
  rulesToggle.setAttribute('aria-controls', 'rd-rules');
  rulesToggle.onclick = () => { const open = rdRulesOpen(); RD_RULES.inside = false; rdRulesRemember(!open); rdLibSync(); };
  rulesRow.append(rules, rulesToggle);
  const ruleSubs = document.createElement('div'); ruleSubs.className = 'rd-lib rd-rules'; ruleSubs.id = 'rd-rules';
  ruleSubs.append(...RU_TABS.map(([k, label]) => link('rules-' + k, ruHref(k), 'gavel', label)));
  if (strategyLink) { strategyLink.setAttribute('href', MARKET_ROUTE); strategyLink.querySelector('.nav-icon').textContent = RD_NAV_ICONS.strategy; }
  const order = [pick('#side-update'), pick('a[data-nav="overview"]'), pick('#nav-welcome'), strategyLink, strategyLink && growth, operations,
    pick('a[data-nav="updates"]'), pick('a[data-nav="tasks"]'), automations, libRow, subs,
    rulesRow, ruleSubs, pick('#nav-pins')].filter(Boolean);
  const rest = before.filter(el => !order.includes(el) && !subs.contains(el) && !ruleSubs.contains(el));
  nav.append(...order, ...rest);
  rdLibSync();
  redesignSections();
  renderTree();   // marks the current page again: Automations and Tools did not exist when the first route drew the rail
}
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
  toggle.setAttribute('aria-expanded', String(open));
}
// The current page's highlight for what the redesign adds or moves (sidebar.js asks, after its own checks).
function redesignNavCurrent(nav) {
  const r = S.route || '';
  if (nav === 'automations') return rdUnder(r, RD_AUTOMATIONS);
  if (nav === 'tools') return rdUnder(r, RD_TOOLS);
  if (nav === 'files') return rdUnder(r, RD_FILES);
  if (nav === 'strategy') return rdUnder(r, '#/strategy');
  if (nav === 'growth') return rdUnder(r, GR_ROUTE);
  if (nav === 'operations') return rdUnder(r, OPS_ROUTE);
  if (nav === 'finance') return rdUnder(r, FIN_ROUTE);
  if (nav === 'rules') return rdUnder(r, RULES_ROUTE);
  if (nav.startsWith('rules-')) return rdUnder(r, RULES_ROUTE) && ruTabOf(r) === nav.slice(6);
  return false;
}

// The company's sidebar choice (plan §5, Settings > General > Sidebar). Display only: a hidden page still opens from a link.
const RD_SECTIONS = [['strategy', 'Strategy'], ['growth', 'Growth'], ['finance', 'Finance'], ['operations', 'Operations'], ['meetings', 'Meetings']];
const rdHiddenKeys = () => new Set(Array.isArray(S.config?.hidden_sections) ? S.config.hidden_sections : []);
// Hiding the parent keeps its tabs' choices; every tab hidden hides the section too.
function rdSectionsOff() {
  const keys = rdHiddenKeys(), off = new Set(RD_SECTIONS.map(([k]) => k).filter(k => keys.has(k)));
  if (OPS_TABS.every(([t]) => keys.has(opsTabKey(t)))) off.add('operations');
  return off;
}
const rdSectionOff = nav => rdSectionsOff().has(nav);
// Links are hidden by CSS on the rail, not removed: Finance's link comes and goes on its own (finance.js finNavSync).
function redesignSections() {
  const nav = $('#side .side-scroll');
  if (!nav) return;
  const off = rdSectionsOff(), keys = rdHiddenKeys();
  nav.dataset.off = [...off].join(' ');
  const names = [...RD_SECTIONS.filter(([k]) => off.has(k)).map(([, label]) => label),
    ...(off.has('operations') ? [] : OPS_TABS.filter(([t]) => keys.has(opsTabKey(t))).map(([, label]) => label))];
  let row = nav.querySelector('.rd-hidden-row');
  if (!settingsIsAdmin() || !names.length) { row?.remove(); return; }
  if (!row) {
    row = Object.assign(document.createElement('a'), {className: 'rd-hidden-row', href: '#/settings/general/settings-sidebar'});
    nav.append(row);
  }
  row.textContent = `Hidden: ${names.join(', ')}`;
}

// Called by applyConfig (state.js) whenever config is read: boot, and the poll every two minutes.
window.redesignApply = function redesignApply() {
  document.documentElement.classList.add('redesign');
  redesignNav();
  redesignSections();
};

// ----------------------------------------------------------------- routes
// Runs before the router's own list (router.js); true when it handled the address.
function redesignRoute(r) {
  // Old addresses of what moved.
  if (rdUnder(r, GOALS)) { location.replace(RD_GOALS + r.slice(GOALS.length)); return true; }
  if (r === '#/settings/listening') { location.replace(RD_LISTENING); return true; }
  if (r === '#/library') { location.replace(DOCS); return true; }
  if (rdUnder(r, GR_OLD_ROUTE)) { location.replace(GR_ROUTE + r.slice(GR_OLD_ROUTE.length)); return true; }
  if (rdUnder(r, GTM_OLD_ROUTE)) { location.replace(GR_ROUTE + '/gtm' + r.slice(GTM_OLD_ROUTE.length)); return true; }   // pageGrowth sends it on
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
  if (rdUnder(r, OPS_ROUTE)) { pageOperations(); return true; }   // ui/app/operations-page.js
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
  operationsStop();
  redesignSections();   // who is signed in is known by now: the Hidden row is for admins
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
