/* ui/app/state.js — Entry route, the state object S, team names, derived bot state
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Chat is the front door, unless this team has never been set up: the boot below sends a
// defaulted address to #/welcome once the config says onboarding is still needed. A typed or
// shared address is never redirected, so the rest of the app stays reachable during setup.
// Links shared outside Tico use ordinary query parameters. Some chat and mail clients drop URL
// fragments, so `?task=...` and `?bot=...&tab=tasks` first become the app's native hash routes.
const entryRoute = () => {
  const params = new URLSearchParams(location.search);
  const task = (params.get('task') || '').trim();
  if (task) return `#/task/${encodeURIComponent(task)}`;
  const bot = (params.get('bot') || '').trim();
  if (!bot) return '';
  const tab = (params.get('tab') || '').trim();
  return `#/bot/${encodeURIComponent(bot)}${tab ? '/' + encodeURIComponent(tab) : ''}`;
};
let BOOT_DEFAULT_ROUTE = false;
const ENTRY_ROUTE = entryRoute();
if (ENTRY_ROUTE) history.replaceState(null, '', location.pathname + ENTRY_ROUTE);
else if (!location.hash || location.hash === '#/') { BOOT_DEFAULT_ROUTE = true; history.replaceState(null, '', UPDATES); }   // Updates is home
// Every team-facing name comes from the server (GET /api/me -> config, also GET /api/v2/config),
// so a second environment shows its own application, team and assistant names. The defaults keep
// an older server working, and strings built before /api/me answers read the names lazily.
const CONFIG_DEFAULTS = {environment_id: '', company_name: '', app_name: 'Tico', assistant_name: 'Assistant',
                         assistant_bot: 'coo', public_url: '', runner_url: location.origin,
                         github_owner: '', local: false, release: '', onboarding_needed: false, version: '', update: null};
const S = { emps: [], people: [], orgGroups: [], status: null, issues: [], me: null, route: location.hash || UPDATES,
            config: {...CONFIG_DEFAULTS},
            v2: {on: false, status: {}, needs: []} };
const appName = () => S.config.app_name || CONFIG_DEFAULTS.app_name;
// The assistant bot's name. Neither the product's name nor the team's is ever the bot's: a config that names it
// after the app (an older demo did) or the team (the old default) reads as "Assistant". A team's own name for it stays.
const assistantName = () => {
  const name = String(S.config.assistant_name || '').trim(), low = name.toLowerCase();
  return !name || low === appName().trim().toLowerCase() || low === String(S.config.company_name || '').trim().toLowerCase()
    ? CONFIG_DEFAULTS.assistant_name : name;
};
const assistantBot = () => S.config.assistant_bot || CONFIG_DEFAULTS.assistant_bot;
// The assistant, BotOps, the Librarian and the Goal Manager are built in to every team: pause and rename them, never archive or delete.
const isBuiltInBot = slug => slug === assistantBot() || slug === 'botops' || slug === 'librarian' || slug === 'goal-manager';
const companyName = () => S.config.company_name || appName();
// Tico's own wordmark, only while the app is called Tico; a team that named its app gets that name as text.
const brandLogo = () => appName() === CONFIG_DEFAULTS.app_name
  ? `<img class="brand-logo on-light" src="assets/tico/tico-wordmark.svg" alt="${esc(appName())}"><img class="brand-logo on-dark" src="assets/tico/tico-wordmark-reversed.svg" alt="${esc(appName())}">`
  : `<span class="brand-name">${esc(appName())}</span>`;
const publicUrl = () => S.config.public_url || location.origin;
const runnerUrl = () => S.config.runner_url || location.origin;
function applyConfig(config) {
  S.config = {...CONFIG_DEFAULTS, ...(config && typeof config === 'object' ? config : {})};
  document.title = (S.config.demo ? 'Demo · ' : S.config.rehearsal ? 'Rehearsal · ' : '') + appName();
  // The strip is shown by the server's say-so alone: only a demo's config carries `demo`, a rehearsal's `rehearsal`.
  const strip = !!(S.config.demo || S.config.rehearsal);
  document.documentElement.classList.toggle('demo', strip);
  $('#demo-banner').hidden = !strip;
  if (S.config.rehearsal && !S.config.demo) $('#demo-banner').innerHTML = '<strong>Rehearsal: nothing runs or leaves this server</strong>';
  renderOnboardingNav();
  renderNewVersion();
  noticeUpdatedServer();
  renderUsageNotice();
  return S.config;
}
// ----------------------------------------------------------------- derived state
function stateOf(slug) {
  const e = S.emps.find(x => x.name === slug);
  if (!e || e.status === 'planned') return 'planned';
  if (e.status === 'paused') return 'paused';
  const live = S.v2?.status?.[slug];
  if (live) return live.state === 'running' ? 'running' : ['waiting_human', 'blocked', 'crashed', 'quarantined'].includes(live.state) ? 'needs' : 'idle';
  if (S.status?.active?.some(a => a.employee === slug)) return 'running';
  if (S.issues.some(i => i.owner === slug && i.state === 'OPEN' && (i.needs_human || i.status === 'blocked'))) return 'needs';
  const last = (S.status?.recent_runs || []).find(r => r.employee === slug);
  if (last && last.exit !== 0) return 'failed';
  return 'idle';
}
const openCount = slug => S.v2?.status?.[slug]?.open_tasks || 0;
// The org tree's number is what needs you from that bot, counted the way the
// Needs you list groups it (companyNeedActor), so the two always agree.
function needsMeCount(slug) {
  return (S.v2?.needs || []).filter(it => ['task', 'question', 'waiting'].includes(it.kind) && taskNeedsMe(it)
    && companyNeedActor(it, it) === 'bot:' + slug).length;
}
