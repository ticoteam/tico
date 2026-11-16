/* ui/app/state.ts — Entry route, the state object S, team names, derived bot state
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Chat is the front door, unless this team has never been set up: the boot below sends a
// defaulted address to #/welcome once the config says onboarding is still needed. A typed or
// shared address is never redirected, so the rest of the app stays reachable during setup.
// Links shared outside Tico use ordinary query parameters. Some chat and mail clients drop URL
// fragments, so `?task=...` and `?bot=...&tab=tasks` first become the app's native hash routes.
const entryRoute = (): string => {
  const params = new URLSearchParams(location.search);
  const task = (params.get('task') || '').trim();
  if (task) return `#/task/${encodeURIComponent(task)}`;
  const bot = (params.get('bot') || '').trim();
  if (!bot) return '';
  const tab = (params.get('tab') || '').trim();
  return `#/bot/${encodeURIComponent(bot)}${tab ? '/' + encodeURIComponent(tab) : ''}`;
};
// `&message=<id>` (and, for a bot, `&conversation=<id>`: the chat room it is in) with either of them (the "N visuals - view in Tico" line in Slack, backend/visuals.py elsewhere)
// also brings that message into view once the task or chat has loaded (ui/app/task-modal.js, ui/app/chat-outline.js).
/** A message to bring into view: its id, with the task it is in or the bot (and chat room) it is in. */
interface EntryJump {
  id: string;
  task?: string;
  slug?: string;
  conversation?: string;
}
const entryJump = (): EntryJump | null => {
  const params = new URLSearchParams(location.search);
  const id = (params.get('message') || '').trim(), task = (params.get('task') || '').trim(), bot = (params.get('bot') || '').trim();
  const conversation = (params.get('conversation') || '').trim();
  return !id ? null : task ? {task, id} : bot ? {slug: bot, id, conversation} : null;
};
let BOOT_DEFAULT_ROUTE = false;
const ENTRY_JUMP = entryJump();
const ENTRY_ROUTE = entryRoute();
if (ENTRY_ROUTE) history.replaceState(null, '', location.pathname + ENTRY_ROUTE);
else if (!location.hash || location.hash === '#/') { BOOT_DEFAULT_ROUTE = true; history.replaceState(null, '', HOME); }   // My dashboard is home
// Every team-facing name comes from the server (GET /api/me -> config, also GET /api/v2/config),
// so a second environment shows its own application, team and assistant names. The defaults keep
// an older server working, and strings built before /api/me answers read the names lazily.
const CONFIG_DEFAULTS: AppConfig = {environment_id: '', company_name: '', app_name: 'Tico', assistant_name: 'Assistant',
                         assistant_bot: 'assistant', public_url: '', runner_url: location.origin, visual_origin: '',
                         github_owner: '', local: false, release: '', onboarding_needed: false, version: '', update: null, s3_view_urls: {}};
const S: AppState = { emps: [], people: [], orgGroups: [], status: null, statusPending: true, me: null, route: location.hash || HOME,
            config: {...CONFIG_DEFAULTS},
            v2: {on: false, status: {}, needs: []},
            // What each main page last read (Tasks, Strategy, Automations, a dashboard, the campus's hours card): a
            // return visit paints it in the same task as the page's shell, then reads again and patches what changed,
            // so switching pages never flashes a skeleton. Live events keep or drop it (ui/app/refresh.ts).
            lastRead: {} };
const appName = (): string => S.config.app_name || CONFIG_DEFAULTS.app_name;
// The assistant bot's name. Neither the product's name nor the team's is ever the bot's: a config that names it
// after the app (an older demo did) or the team (the old default) reads as "Assistant". A team's own name for it stays.
const assistantName = (): string => {
  const name = String(S.config.assistant_name || '').trim(), low = name.toLowerCase();
  return !name || low === appName().trim().toLowerCase() || low === String(S.config.company_name || '').trim().toLowerCase()
    ? CONFIG_DEFAULTS.assistant_name : name;
};
const assistantBot = (): string => S.config.assistant_bot || CONFIG_DEFAULTS.assistant_bot;
// The built-in Rules Owner's slug (backend/hubdb.py COUNSEL): Tico's own name, so a team's own bot named counsel stays theirs.
const RULES_OWNER_BOT = 'rules-owner';
// The General Manager's team, the group the server keeps the built-in bots in (backend/groups.py BUILT_IN): Tico's own.
const BUILT_IN_GROUP = 'built-in';
// The assistant, BotOps, the Librarian, the Strategy Owner, the General Manager, the Finance Owner, the Rules Owner, the Design Owner and the
// Updates and Automations Owners are built in to every team: pause and rename them, never archive or delete. They sit in
// the General group of the team chart, which nobody drags them out of; their settings are Settings > Bots and their bot
// page's Settings.
const isBuiltInBot = (slug: string): boolean => slug === assistantBot() || slug === 'botops' || slug === 'librarian' || slug === 'strategy-owner' || slug === 'general-manager' || slug === 'finance-owner' || slug === RULES_OWNER_BOT || slug === 'design-owner'
  || slug === 'updates-owner' || slug === 'automations-owner';
// The Assistant's helpers (backend/assistant.py HELPERS): everyone's one chat is their Assistant, which hands these the
// work. Nobody chats with them: their bot pages have no composer, and every "ask" on a page opens the Ask panel.
const ASSISTANT_HELPERS = ['botops', 'strategy-owner', 'librarian', RULES_OWNER_BOT];
// The slug may be absent (thread-call.js passes an employee's name, which the type leaves optional): an absent one is no helper.
const isAssistantHelper = (slug: string | undefined): boolean => slug !== undefined && ASSISTANT_HELPERS.includes(slug);
// The Strategy Owner (backend/strategy_bot.py, slug strategy-owner) is on every install, by the name the owner gave it,
// else
// "Strategy Owner". An install that has not started the new server yet still stores its older default names ("CEO Bot",
// "Goal Manager", "Strategy Bot"), so those read as the default too.
const strategyDefaultName = (): string => 'Strategy Owner';
const strategyDefaultish = (name: string): boolean =>
  !name || name === 'Goal Manager' || name === 'CEO Bot' || name === 'Strategy Bot';
const strategyBotName = (): string => {
  const name = String(botRow('strategy-owner')?.display_name || '').trim();
  return strategyDefaultish(name) ? strategyDefaultName() : name;
};
// Everyone's chat is their Assistant, the owner's too (backend/assistant.py): the Strategy Owner is a helper it asks.
const myChatBot = (): string => assistantBot();
const myChatName = (): string => assistantName();
const companyName = (): string => S.config.company_name || appName();
// Tico's own wordmark, only while the app is called Tico; a team that named its app gets that name as text.
const brandLogo = (): string => appName() === CONFIG_DEFAULTS.app_name
  ? `<img class="brand-logo on-light" src="assets/tico/tico-wordmark.svg" alt="${esc(appName())}"><img class="brand-logo on-dark" src="assets/tico/tico-wordmark-reversed.svg" alt="${esc(appName())}">`
  : `<span class="brand-name">${esc(appName())}</span>`;
const publicUrl = (): string => S.config.public_url || location.origin;
const runnerUrl = (): string => S.config.runner_url || location.origin;
// Only a successful authenticated workspace config may grant native visual navigation.
// Local name/provider edits still use synchronous applyConfig and never change that grant.
const NATIVE_VISUAL_WAIT_MS = 5000, NATIVE_VISUAL_TIMEOUT = Symbol('native visual wait');
// NATIVE_VISUAL_FILES: Tico file hosting's address (config.visual_files) once the shell acknowledged it: a page's frame
// may then follow the app's own /visuals route on to it (ui/app/visual-cards.js visualFrameOk).
let NATIVE_VISUAL_VERSION = 0, NATIVE_VISUAL_PENDING: Promise<unknown> = Promise.resolve(), NATIVE_VISUAL_ORIGIN = '', NATIVE_VISUAL_FILES = '';
const nativeVisualVersion = (): number => NATIVE_VISUAL_VERSION;
const authenticatedConfigPerson = (me: SelfPerson | null | undefined): boolean => !!(me && typeof me.id === 'string' && me.id.trim()
  && me.cloud === true && ['owner', 'viewer'].includes(me.role));
function nativeVisualOrigin(config: Partial<AppConfig> | null | undefined, field: 'visual_origin' | 'visual_files' = 'visual_origin'): string {
  const raw = config?.[field];
  if (typeof raw !== 'string' || !raw || /[^\x21-\x7e]|[@\\%?#]/.test(raw)) return '';
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && !url.username && !url.password && url.pathname === '/'
      && !url.search && !url.hash && url.origin !== location.origin ? url.origin : '';
  } catch { return ''; }
}
/** The native shell's visual-origin handler: it takes a message and answers whether it accepted it. */
interface NativeHandler {
  postMessage(message: unknown): Promise<unknown>;
}
function nativeVisualChange(origin: string, clear = false, files = ''): {version: number; pending: Promise<boolean>} {
  const version = ++NATIVE_VISUAL_VERSION;
  NATIVE_VISUAL_ORIGIN = NATIVE_VISUAL_FILES = '';
  const pending = NATIVE_VISUAL_PENDING.catch(() => {}).then(async (): Promise<boolean> => {
    if (!clear && version !== NATIVE_VISUAL_VERSION) return false;
    const features: string[] = window.ticoNativeFeatures || [];
    if (features.includes('visualOrigin') && window.ticoNative) {
      // A shell without `visualFiles` hears only {origin}: it would refuse the whole message.
      const body: {origin: string; files?: string} = features.includes('visualFiles') ? {origin, files} : {origin};
      let handler: NativeHandler | undefined, accepted = false;
      try { const native: NativeHandler = window.ticoNative('visualOrigin'); handler = native; accepted = await native.postMessage(body) === true; } catch {}
      if ((origin || body.files) && !accepted && handler) try { await handler.postMessage({origin: ''}); } catch {}
      return accepted;
    }
    return false;
  });
  NATIVE_VISUAL_PENDING = pending;
  return {version, pending};
}
function nativeVisualWait(pending: Promise<unknown>): Promise<unknown> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([pending.catch(() => false), new Promise<typeof NATIVE_VISUAL_TIMEOUT>(done => {
    timer = setTimeout(() => done(NATIVE_VISUAL_TIMEOUT), NATIVE_VISUAL_WAIT_MS);
  })]).finally(() => clearTimeout(timer));
}
function clearNativeVisualOrigin(): Promise<unknown> {
  S.config.visual_origin = '';
  // Clears follow an in-flight invocation; queued grants are invalidated before
  // either resolves, so an old ACK cannot restore trust after signing out.
  return nativeVisualWait(nativeVisualChange('', true).pending);
}
async function applyAuthenticatedConfig(config: AppConfig, expected = nativeVisualVersion()): Promise<{config: AppConfig; version: number} | null> {
  if (SESSION.out || expected !== nativeVisualVersion()) return null;
  const origin = nativeVisualOrigin(config), files = nativeVisualOrigin(config, 'visual_files');
  const {version, pending} = nativeVisualChange(origin, false, files);
  const accepted = await nativeVisualWait(pending);
  if (SESSION.out || version !== nativeVisualVersion()) return null;
  if (accepted === NATIVE_VISUAL_TIMEOUT) {
    // Bound only the UI wait. The actual invocation/FIFO stays intact; its late
    // ACK cannot grant UI trust, and its ordered clear still waits behind it.
    void clearNativeVisualOrigin();
    const clearedVersion = nativeVisualVersion();
    return {config: applyConfig(config), version: clearedVersion};
  }
  if (accepted) {
    NATIVE_VISUAL_ORIGIN = origin;
    NATIVE_VISUAL_FILES = (window.ticoNativeFeatures || []).includes('visualFiles') ? files : '';
  }
  const value = applyConfig(config);
  if (accepted && (origin || NATIVE_VISUAL_FILES) && typeof nativeVisualRepaint === 'function') nativeVisualRepaint();
  return {config: value, version};
}
// A page reload is not evidence that the previous browser session is still valid.
void clearNativeVisualOrigin();
// Whether a config was read yet: until then S.config holds only the defaults, which say nothing about this server.
let CONFIG_READ = false;
// The browser tab: the open page's subject first when it has one ("Builder 2 · Tico"), else the app's name.
let PAGE_TITLE = '';
function pageTitle(subject = ''): void {
  PAGE_TITLE = subject;
  document.title = (subject ? subject + ' · ' : '') + (S.config.demo ? 'Demo · ' : S.config.rehearsal ? 'Rehearsal · ' : '') + appName();
}
function applyConfig(config: unknown): AppConfig {
  S.config = {...CONFIG_DEFAULTS, ...(config && typeof config === 'object' ? config : {})};
  CONFIG_READ = true;
  pageTitle(PAGE_TITLE);
  // The strip is shown by the server's say-so alone: only a demo's config carries `demo`, a rehearsal's `rehearsal`.
  const strip = !!(S.config.demo || S.config.rehearsal);
  document.documentElement.classList.toggle('demo', strip);
  document.documentElement.classList.toggle('strip', strip || document.documentElement.classList.contains('cloud-frozen'));
  $('#demo-banner').hidden = !strip;
  if (S.config.rehearsal && !S.config.demo) $('#demo-banner').innerHTML = '<strong>Rehearsal: nothing runs or leaves this server</strong>';
  if (S.config.demo && S.config.demo_public) $('#demo-banner').innerHTML = '<strong>Read only demo</strong>';
  renderOnboardingNav();
  renderNewVersion();
  renderAdministration();
  if (typeof nativeOrgSync === 'function') nativeOrgSync();
  renderChangelogNotice();
  noticeUpdatedServer();
  void renderAppUpdate();
  renderUsageNotice();
  betaMark();
  renderVersionMessage();
  renderFeedbackAsk();
  document.documentElement.classList.toggle('no-support', S.config.support_on === false);
  return S.config;
}
// Beta: the server's one list (backend/beta.py), so the app, Help, the manual and support diagnostics agree. The pill
// shows only for an id on it; a static spot in the page is an empty `<span data-beta="id">` that betaMark fills.
const betaFeature = (id: string | undefined) => (S.config.beta || []).find(f => f.id === id);
const betaPill = (id: string | undefined): string => betaFeature(id) ? '<span class="pill beta" title="Beta: works today and may still change">Beta</span>' : '';
// The pill for a bot that is a Beta feature by its template (a message bot, Listening) or harness (an external agent).
const botBetaPill = (e: Pick<Employee, 'template' | 'harness' | 'agent'> | null | undefined): string => betaPill((S.config.beta || []).find(f => (f.templates || []).some(t => t === e?.template)
  || (f.harnesses || []).some(h => h === (e?.harness || e?.agent?.harness)))?.id);
function betaMark(root: ParentNode = document): void {
  root.querySelectorAll<HTMLElement>('[data-beta]').forEach(el => { el.innerHTML = betaPill(el.dataset.beta); });
}
// ----------------------------------------------------------------- derived state
// Returns one of planned, paused, running, needs, idle. Typed as string because bot-page.ts compares it with 'failed', which
// this never returns: the comparison is a dead branch in that file, and a literal union would make it a new tsc finding.
function stateOf(slug: string | undefined): string {
  const e = botRow(slug);
  if (!e || e.status === 'planned') return 'planned';
  if (e.status === 'paused') return 'paused';
  const live = S.v2?.status?.[slug ?? ''];
  if (live) return live.state === 'running' ? 'running' : ['crashed', 'quarantined'].includes(live.state) ? 'needs' : 'idle';
  if (S.status?.active?.some(a => a.employee === slug)) return 'running';
  return 'idle';
}
const openCount = (slug: string): number => S.v2?.status?.[slug]?.open_tasks || 0;
// The bot one Needs-you item goes with (backend views.needs_actor): the bot that asked, for an ask; the bot that asked
// for the task, for a task; the first bot that cannot run, for a stuck stretch. A mention is a person's.
function needsBotOf(it: NeedsEntry): string | null {
  const me = S.me?.id ? `human:${S.me.id}` : '', task = it.task;
  const ask = it.ask as {asked_by?: string | null} | null | undefined;
  const options = it.kind === 'paused' ? (it.bots || []).slice(0, 1)
    : it.kind === 'ask' ? [ask?.asked_by, task?.owner_id, task?.requester_id]
      : it.kind === 'task' ? [task?.requester_id] : [];
  return options.find((a): a is string => typeof a === 'string' && a.startsWith('bot:') && a !== me) || null;
}
// What needs you from that bot. The org tree's number is a fixed attention count matching the default Needs you tab,
// independent of edits to that tab.
// S.v2.needs holds the Needs-you items as the server sends them (hub-v2.ts needsTake).
const needsEntries = (): NeedsEntry[] => (S.v2?.needs || []) as unknown as NeedsEntry[];
function botNeeds(slug: string): NeedsEntry[] {
  return needsEntries().filter(it => needsBotOf(it) === 'bot:' + slug);
}
// While only some pages are held, the counts are the server's over the whole list (its `bots`).
const needsBotsWhole = (): Record<string, number> | null => S.v2?.needsNext && S.v2.needsBots ? S.v2.needsBots : null;
function needsMeCount(slug: string): number { const whole = needsBotsWhole(); return whole ? whole['bot:' + slug] || 0 : botNeeds(slug).length; }
// needsMeCount for every bot in one pass over the needs list ("bot:<slug>" -> count), for views that count many rows.
function needsMeCounts(): Map<string, number> {
  const whole = needsBotsWhole();
  if (whole) return new Map(Object.entries(whole));
  const counts = new Map<string, number>();
  for (const it of needsEntries()) {
    const actor = needsBotOf(it);
    if (actor) counts.set(actor, (counts.get(actor) || 0) + 1);
  }
  return counts;
}
