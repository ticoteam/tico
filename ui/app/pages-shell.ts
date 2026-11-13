/* ui/app/pages-shell.ts — What the shell knows of the pages it does not load (ui/parts.json)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   A page part's code is fetched the first time a person goes to one of its pages (ui/app/parts.ts). The shell still
   needs a few of each page's names before that: its routes and the tests the router and the sidebar run on an address,
   the state the router resets on leaving it, and the few helpers a shell screen draws with. They live here, by the
   file they came from, so the page parts can stay out of a cold open; scripts/check_ui_parts.cjs finds any other use. */
'use strict';

// docs-page.ts
let DOC_LOAD = 0;

// market-page.ts
   // what is mounted: {kind: 'page' | 'map', shell, open(at), stop()}
// Old #/market links land here (ui/app/router.ts), keeping their query or note path.
const MARKET_ROUTE = '#/strategy/market';
const isMarketRoute = (r: string) => r === MARKET_ROUTE || r.startsWith(MARKET_ROUTE + '?') || r.startsWith(MARKET_ROUTE + '/');

// strategy-page.ts
const isStrategyRoute = (r: string) => r === '#/strategy' || r.startsWith('#/strategy?');

// lenses-page.ts
const LENSES_ROUTE = '#/strategy/lenses';
const isLensesRoute = (r: string): boolean => r === LENSES_ROUTE || r.startsWith(LENSES_ROUTE + '?') || r.startsWith(LENSES_ROUTE + '/');

// getting-started.ts
// Setup's last step (ui/app/welcome.ts) still has a row open: the computer (a hosted company's Tico Cloud computers
// count, as the step shows them ticked) or the subscription line, not done or skipped. Finish setup stays in the
// sidebar for the owner until both are, or until they hide it for good from that step (the checklist's own `dismissed`).
function gsSetupOpen(): boolean {
  if (!GS || GS.dismissed || S.me?.role !== 'owner') return false;
  const computer = GS.items.find(item => item.id === 'computer'), sub = GS.items.find(item => item.id === 'subscription');
  return (!!computer && !computer.done && !S.config?.hosted) || (!!sub && !sub.done && !sub.skipped);
}

// first-run.ts
// ----------------------------------------------------------------- the bot page and the org chart
const frNeedsSetup = (e: FrBot | null | undefined): e is FrBot => !!e && ['needs_setup', 'needs_onboarding'].includes(String(e.onboarding_state));
// The org chart's mark: small, beside the name, a gear so the name keeps the row; it says the bot page's words on hover.
const frTreeMark = (e: FrBot | null | undefined): string => (frNeedsSetup(e) ? '<span class="tree-setup" role="img" aria-label="Onboarding" title="Onboarding: it learns in chat and takes no work until someone above it marks onboarding complete"><span class="nav-icon" aria-hidden="true">settings</span></span>' : '');

// app/person-page.ts
// The human's tasks: what they own, active first, finished behind a toggle. The rows are
// the shared task row (S1), opening the shared task detail.
/** The person whose tasks the page shows: ui/app/refresh.ts reads it on every live change. */ let PERSON_TASKS: string | null = null;

// app/assistant-page.ts
// A task of the assistant's, as the rail lists it (GET /v2/tasks).
const ASSISTANT_HINTS = ["What's waiting on me?", 'Find the launch plan', 'How do I add a bot?'];
let ASSISTANT_PILL: AssistantPill | null = null, ASSISTANT_LOAD = 0;

// app/bot-branches.ts

// app/github-nudge.ts
// The host a "Build a bot" task's page draws, empty for any other task.
function githubNudgeTaskHostHTML(task: GithubNudgeTask): string {
  return ghNudgeBuildName(task) ? `<div class="gh-nudge-host" data-gh-nudge-task data-task="${esc(String(task.id))}"></div>` : '';
}

// app/bot-page.ts
// The open bot page (BotPageView, ui/types/bot-page.d.ts); null when no bot page is open.
let BOT: BotPageView | null = null;
function botStatePill(slug: string) {
  const st = stateOf(slug);
  const act = (S.status?.active || []).find(a => a.employee === slug);
  const last = (S.status?.recent_runs || []).find(r => r.employee === slug);
  if (st === 'running') return `<span class="pill in-progress">Running${act?.issue ? ` on #${esc(act.issue)}` : ''}</span>`;
  if (st === 'needs') return '<span class="pill needs">Needs you</span>';
  if (st === 'paused') return '<span class="pill">Paused</span>';
  if (st === 'planned') return '<span class="pill">Setting up</span>';
  if (st === 'failed') return `<span class="pill fail">Last run failed${last?.finished_at ? ` ${ago(last.finished_at)}` : ''}</span>`;
  return `<span class="pill">Idle${last?.finished_at ? `, last active ${ago(last.finished_at)}` : ''}</span>`;
}
// Files dragged over any part of a bot's chat attach to its composer, which lights up to say so
// (#524, after bot-desk). A file dropped anywhere else on the page is swallowed: the browser's own
// answer is to navigate away to it, and nothing here wants that. A file input keeps its own drop.
// A type guard: true only when the event carries files, so dataTransfer is there.
const draggingFiles = (ev: DragEvent): ev is DragEvent & {dataTransfer: DataTransfer} => [...(ev.dataTransfer?.types || [])].includes('Files');
// True when the text was a command and has been handled here, so the caller must not send it.
function runCommand(text: unknown, out: Element | null | undefined, slug: string | null | undefined) {
  if (!isCommand(text)) return false;
  const word = commandWord(text);
  const say = (html: string) => { if (out instanceof HTMLElement) { out.innerHTML = html; out.hidden = false; } };
  if (word === 'help') { say(helpHTML()); return true; }
  if (word === 'clear' && !slug) { say(`<div class="cmd-out"><span class="muted"><code>/clear</code> works on a bot's page, in its conversation box.</span></div>`); return true; }
  if (word === 'clear' && slug) {
    say(`<div class="cmd-out"><div>Start a fresh session for <strong>${empName(slug)}</strong> on its next run? Its history stays visible.</div>
      <div class="row" style="margin-top:7px"><button class="primary" type="button" data-cmd="yes">Clear session</button>
      <button class="ghost" type="button" data-cmd="no">Cancel</button><span class="muted" id="cmd-msg"></span></div></div>`);
    if (!(out instanceof HTMLElement)) return true;
    out.querySelector<HTMLElement>('[data-cmd="no"]')!.onclick = () => { out.hidden = true; out.innerHTML = ''; };
    out.querySelector<HTMLElement>('[data-cmd="yes"]')!.onclick = async ev => {
      const msg = out.querySelector<HTMLElement>('#cmd-msg')!, button = ev.target as HTMLButtonElement; button.disabled = true; msg.textContent = 'Clearing…';
      try {
        await post(`/employees/${encodeURIComponent(slug)}/session/clear`);
        out.hidden = true; out.innerHTML = '';
        toast('Session cleared: the next run starts fresh');
      } catch (e) {
        if (/\b403\b|only the owner|not shared|owner only/i.test(errorMessage(e, ''))) { out.hidden = true; out.innerHTML = ''; toast('only the owner can do that', true); return; }
        msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; button.disabled = false;
      }
    };
    return true;
  }
  say(unknownHTML(word));
  return true;
}

// app/bot-conversation.ts
const plural = (n: number, one: string, many?: string): string => `${n} ${n === 1 ? one : (many || one + 's')}`;
// The CLI's own resume command comes from its manifest (`resume_command`); a CLI without one gets `<name> --resume`.
function botResumeCommand(runtime: string | null | undefined, sid: string, slug: string): string {
  const r = String(runtime || '').toLowerCase();
  return `cd ~/tico-work/emp-${botSlug(slug)} && ${harnessCaps(r).resume_command || `${r} --resume`} ${sid}`;
}

// app/skills.ts
const SKILLS_ROUTE = '#/library/skills';
// The redesign's router asks first (ui/app/redesign.ts).
function skillsRoute(r: string) {
  const [path = ''] = r.split('?');
  if (!rdUnder(path, SKILLS_ROUTE)) return false;
  const parts = path.slice(SKILLS_ROUTE.length).split('/').filter(Boolean).map(x => { try { return decodeURIComponent(x); } catch { return x; } });
  if (parts[0] === 'proposals' && parts[1]) { void pageSkillProposal(parts[1]); return true; }
  if (parts.length) { void pageSkill(parts.join('/')); return true; }
  void pageSkills();
  return true;
}

// app/directory.ts
const DIR_ROUTE = '#/library/contacts', DIR_OLD_ROUTE = '#/library/directory';
// ----------------------------------------------------------------- routing
// The redesign's router asks first (ui/app/redesign.ts).
function directoryRoute(r: string): boolean {
  // The old address of the page, from before it was Contacts.
  if (rdUnder(r, DIR_OLD_ROUTE)) { location.replace(DIR_ROUTE + r.slice(DIR_OLD_ROUTE.length)); return true; }
  const [path = '', query = ''] = r.split('?');
  if (!rdUnder(path, DIR_ROUTE)) return false;
  const rest = path.slice(DIR_ROUTE.length).replace(/^\//, '');
  if (rest) {
    let id = rest;
    try { id = decodeURIComponent(rest); } catch { /* an address typed by hand: use it as it is */ }
    void pageDirectoryEntity(id);
    return true;
  }
  void pageDirectory(query);
  return true;
}

// app/mail.ts
let MAIL_LOAD = 0, MAIL_SEARCH = 0;

// app/messaging.ts
// ----------------------------------------------------------------- Message bot review
// Bots are the primary unit. Provider messages remain evidence behind a run or action.
let MESSAGING_LOAD = 0, MESSAGING_LIST: MessagingList | null = null;
function messagingParams(): {bot: string; source: string} {
  const raw = S.route.startsWith(MESSAGING) ? S.route.slice(MESSAGING.length) : '';
  const q = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : '');
  return {bot: q.get('bot') || '', source: q.get('source') || ''};
}

// app/settings.ts
// ----------------------------------------------------------------- settings
// People stay deliberately compact. Bot ownership, model, and physical placement are separate
// controls because changing who can use a bot must never silently move its runtime (or vice versa).
// Teams come from the team's data; a name reads as its words (`success-owner` is Customer Success).
const teamLabel = (t?: string) => (S.orgGroups || []).find(g => g.id === t)?.name || String(t || '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
let SETTINGS_DATA: SettingsData = {people: [], machines: [], services: [], models: [], issues: [], history: {changes: [], transitions: []}};
// Presence is judged when a response arrives, never by aging a cached heartbeat on a later paint.
const SETTINGS_PRESENCE: SettingsPresence = {interval: 25000, timeout: 10000, at: 0, checking: false, failed: false, timer: null, pending: null, request: 0, applied: 0};
const harnessCaps = (id: string | undefined): HarnessCaps => harnessRow(id)?.capabilities || {};
const SETTINGS_TAB_KEY = 'tico.settings.tab';
// Remembered per browser tab so a reload lands where the person was; a first visit opens General.
let SETTINGS_TAB = (() => { try { return sessionStorage.getItem(SETTINGS_TAB_KEY) || 'general'; } catch { return 'general'; } })();
// The address the shown tab writes (settingsAddress) so it can be copied, bookmarked or shared; route() reads it as Settings.
let SETTINGS_ADDRESS = '';
function settingsMovedOut(tab: string, card = '') {
  if (tab === 'strategy-bot') card = 'set-strategy-bot-card';
  else if (tab === 'releases') card = 'set-release-card';
  else if ((SETTINGS_ALIAS[tab] || tab) !== 'system') return '';
  if (card === 'set-screening-card') return SETTINGS + '/security/' + card;
  if (card === 'set-release-card') { void settingsReleasesOpen(); return 'wait'; }
  if (SETTINGS_SYSTEM_BOTS[card]) return `#/bot/${SETTINGS_SYSTEM_BOTS[card]}/settings`;
  return AUTOMATIONS + '/' + AU_SYSJOBS;
}
// Cards that moved from Computers to Models: an old link to one (the settings catalogue's) opens Models.
const SETTINGS_MOVED_TO_MODELS = ['#settings-models', '#set-providers', '#set-gateway', '#set-turn-levels'];
// A section a link points into (an old tab's name, settingsShow); it scrolls there once drawn.
let SETTINGS_REVEAL = '';
// Owners and admins always; a member unless an admin switched create_bots off (the server decides, and counts the limit).
const settingsCanCreateBots = () => S.me?.role === 'owner' || !!S.me?.bot_admin || S.me?.can_create_bots !== false;
// The owner, an admin, one of the bot's owners, or someone it reports up to: what the server says (`can_manage`).
const settingsCanManageBot = (e: (object & {can_manage?: boolean}) | null | undefined) => S.me?.role === 'owner' || !!S.me?.bot_admin || !!e?.can_manage;
const settingsIsAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Whoever the server lets make a personal token (`can_create_tokens`); an admin also sees everyone's. Someone
// who still holds a token keeps the card to revoke it.
const settingsCanMakeTokens = () => S.me?.can_create_tokens ?? settingsIsAdmin();

// app/settings-webhooks.ts
// An old Settings > Webhooks address (#/triggers, #/settings/webhooks[/<part>]) at the part's new home.
function whOldAddress(route: string) {
  const part = String(route).split('?')[0]!.split('/')[3];
  if (part === 'outgoing') return WH_HOOKS_ROUTE;
  if (part === 'gateway') return SETTINGS + '/models/wh-gateway';
  return WH_INCOMING_ROUTE;
}

// app/settings-webhooks-state.ts
// settings-webhooks.ts reassigns WH_TRIGGERS (null until a part opens, then the list), so a reader calls this each time.
function webhookTriggers(): WebhookTrigger[] | null { return WH_TRIGGERS; }

// app/settings-devices.ts
// An external agent (a Hermes profile) has no computer: the cell holds its credential instead.
// The token is shown once, when it is minted; after that only rotate and revoke remain.
const agentKind = (a: BotAgent) => `${settingsHarnessName(a.harness) || a.harness} agent`;
// An imported snapshot's freshness says nothing about its external process.
const agentPresenceLabel = (a: {synced?: boolean; last_seen_at?: string | null; credential?: unknown}, online: boolean | undefined) => a.synced ? (online ? 'History synced recently' : a.last_seen_at ? 'History not synced lately' : 'History not synced yet')
  : !a.credential ? 'No credential yet' : online ? 'Reporting in' : 'Not reporting';

// app/skills-connect.ts
const SKILLS_CONNECT = '#/skills/connect';

// app/settings-access.ts
const accessEveryone = (): SaAudience => ({everyone: true, people: [], teams: [], bots: []});
function accessLevelText(level: SaAudience | undefined) {
  if (!level || level.everyone) return 'Everyone';
  const names = [...(level.teams || []).map(accessTeamName), ...(level.people || []).map(id => firstName(settingsPersonName(id)) || id),
    ...(level.bots || []).map(settingsBotName)];
  if (!names.length) return 'Managers only';
  return names.length > 3 ? `${names.slice(0, 2).join(', ')} +${names.length - 2}` : names.join(', ');
}
function teammateEditAccess(kind: 'human' | 'group', id: string, name: string, after?: ((saved: SaSaved) => unknown) | undefined) {
  return accessEditor({path: teammateAccessPath(kind, id), name, levels: TEAMMATE_ACCESS_LEVELS[kind],
    presets: ACCESS_PRESETS.filter(([value]) => value !== 'requests'), skipPerson: kind === 'human' ? id : '', after});
}

// app/tools.ts
const TOOLS_ROUTE = '#/tools';
// ----------------------------------------------------------------- routes
// The redesign's router asks first (ui/app/redesign.ts). Old addresses are replaced, so Back skips them.
function toolsRoute(r: string): boolean {
  const [path = '', query = ''] = r.split('?'), q = query ? '?' + query : '';
  const go = (to: string) => { location.replace(to); return true; };
  // Library › Tools and its old names: a tool's page, a credential's, or the list.
  for (const old of ['#/library/tools', INTEGRATIONS]) {
    if (!rdUnder(path, old)) continue;
    const rest = path.slice(old.length).replace(/^\/+/, '');
    if (!rest) return go(new URLSearchParams(query).has('who') ? TOOLS_KEYS + q : TOOLS_ROUTE);
    return go((rest.startsWith('credentials/') ? TOOLS_KEYS + '/' : TOOLS_ROUTE + '/') + rest + q);
  }
  if (rdUnder(path, '#/library/connections')) {
    const rest = path.slice('#/library/connections'.length).replace(/^\/+/, '');
    return go(rest ? TOOLS_ROUTE + '/' + rest : TOOLS_ROUTE);
  }
  if (rdUnder(path, CREDENTIALS)) return go(TOOLS_KEYS + '?show=credentials');
  // The kinds and views of inbox register themselves as Tools' inbox code runs (ui/app/inboxes*.ts, the connect page
  // part), and Tools' pages and its Add tool menu read them: an address there waits for it, then routes again (or shows
  // Retry where the page would be).
  const inboxed = rdUnder(path, '#/settings/inboxes') || rdUnder(path, TOOLS_ROUTE);
  if (inboxed && !Object.keys(INBOX_KINDS).length && UI_PARTS.parts.connect) { void uiPartCall('connect', 'route', [], true); return true; }
  // Settings › Inboxes and the tabs it replaced (Mailboxes, Email & Phone, Mail; Google's sign-in return).
  if (rdUnder(path, '#/settings/inboxes')) return go(toolsFromInboxes(path.slice('#/settings/inboxes'.length).replace(/^\/+/, '')));
  const oldTab = path.match(/^#\/settings\/(mailboxes|inbound|company-inbox|email-phone|mail|inbox|google)(?:\/([^/]+))?\/?$/);
  if (oldTab) {
    const [, tab = '', rest] = oldTab;
    return go(tab === 'mailboxes' ? (rest ? '#/tools/mailbox/' + rest : TOOLS_ROUTE + '?show=mail')
      : tab === 'mail' ? '#/tools/settings/letters-in' : ['inbox', 'google'].includes(tab) || rest === 'set-inbound' ? '#/tools/settings/email'
      : TOOLS_ROUTE + '?show=mail');
  }
  if (!rdUnder(path, TOOLS_ROUTE)) return false;
  const parts = path.slice(TOOLS_ROUTE.length).split('/').filter(Boolean).map(p => { try { return decodeURIComponent(p); } catch { return p; } });
  if (!parts.length) { pageTools(); return true; }
  if (parts[0] === 'api' && parts[1]) { pageToolApi(parts[1], parts[2] === 'activity' ? 'activity' : ''); return true; }
  if (parts[0] === 'website' && parts[1]) { void pageToolWebsite(parts[1], parts[2] === 'activity' ? 'activity' : ''); return true; }
  if (parts[0] === 'keys') { pageRdTools(); return true; }
  if (parts[0] === 'webhooks') { pageToolWebhooks(parts[1] || ''); return true; }
  const view = inboxesRoute(parts);
  if (view !== null) { toolsInboxPage(view); return true; }
  pageToolService(parts.join('/'));
  return true;
}

// app/settings-repositories.ts
let REPOS: ReposView | null = null;
// app/recruit.ts
let RECRUIT_HANDOFF: RecruitSeed | null = null;
const recruitAddress = (id: string, template = '') => '#/seat/' + encodeURIComponent(id) + (template ? '/' + encodeURIComponent(template) : '');
const recruitSeatCount = (id: string): number | undefined => RECRUIT.counts[id];

// app/settings-people.ts
function peopleSwitch(attrs: string, checked: boolean | null | undefined, disabled: boolean | null | undefined, label: string): string {
  return `<input type="checkbox" role="switch" class="people-switch" ${attrs} aria-label="${esc(label)}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}>`;
}

// app/updates.ts
let UPD: UpdState | null = null;

// app/automations.ts
const AUTOMATIONS = '#/automations';
function isAutomationsRoute(r: string): boolean { return auUnder(r, AUTOMATIONS) || auUnder(r, LISTENING); }

// app/usage.ts
let USE: UseState | null = null;

// app/computer-screen.ts
// A task's owner bot's screen, in the task's header: a monitor icon, as quiet as the header's other icon buttons.
const taskScreenLinkHTML = (owner: string | null | undefined) => {
  const id = botScreenComputer(actorSlug(owner));
  return id ? `<a class="ghost peek-btn" href="${screenHref(id)}" aria-label="Screen" title="Screen">${TL_ICON.screen}</a>` : '';
};

// app/overview.ts
// Read by ui/app/refresh.ts on every live change.
let OVERVIEW_PAGE: OverviewPageState | null = null;
async function overviewLoadComputers() {
  const state = OVERVIEW_PAGE;
  if (!state || state.loadingComputers) return;
  state.loadingComputers = true;
  try {
    const result = await get<OverviewComputersAnswer>('/v2/computers');
    if (OVERVIEW_PAGE === state && Array.isArray(result.computers)) state.computers = result.computers.map(c => ({id: c.id, label: c.label, revoked_at: c.revoked_at}));
  } catch {} // Readable bot assignments still provide their buildings when inventory is unavailable.
  finally { state.loadingComputers = false; }
}

// app/finance.ts
const FIN_ROUTE = '#/finance';

// app/company-page.ts
const CP_ROUTE = '#/company-page';
// ----------------------------------------------------------------- who sees it
const cpAllowed = () => S.me?.role === 'owner' || S.me?.bot_admin === true;

// app/growth-page.ts
const GR_ROUTE = '#/growth', GR_SOURCES = GR_ROUTE + '/sources', BRAND_ROUTE = '#/strategy/brand', GR_GOALS = '#/strategy/goals';
// Where Growth and Go-to-market lived, under Strategy: redesignRoute sends them on.
const GR_OLD_ROUTE = '#/strategy/growth', GTM_OLD_ROUTE = '#/strategy/gtm';

// app/org-rules.ts
const RULES_ROUTE = '#/rules';
// A rule save's refusal in words (the Rules page and a stage's rules panel, ui/app/stage-rules.ts).
const ruError = (e: unknown) => errorStatus(e) === 403 ? "You can't change this." : errorMessage(e, 'Something went wrong.');
const ruHref = (tab: string | null, q: Record<string, string> = {}) => {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v)).toString();
  return RULES_ROUTE + (tab && tab !== 'internal' ? '/' + tab : '') + (qs ? '?' + qs : '');
};
// A rule's text as HTML, its @mentions as chips. `as`: 'link' (an <a>), 'inline' (inside a row that is itself a link:
// a span that opens the teammate on its own click) or 'plain' (inside a button: the chip's look only).
function ruTextHTML(text: string, as: 'link' | 'inline' | 'plain' = 'link') {
  return esc(text).replace(/(^|[^\w@])@(\w[\w.-]*)((?:&#39;s\b|[,;:!?)])*)/g, (all: string, pre: string, word: string, after: string) => {
    const handle = word.replace(/[.-]+$/, ''), hit = handle && ruMentionOf(handle);
    if (!hit) return all;
    const label = '@' + esc(hit.name), tail = word.slice(handle.length) + after;
    const chip = as === 'link' ? `<a class="ru-at" href="${esc(hit.href)}">${label}</a>`
      : as === 'inline' ? `<span class="ru-at" data-href="${esc(hit.href)}">${label}</span>` : `<span class="ru-at">${label}</span>`;
    return pre + (tail ? `<span class="ru-at-w">${chip}${tail}</span>` : chip);
  });
}

// An inline rule chip (drawn above, in a thread or on the Rules page) opens its teammate, not the row around it.
document.addEventListener('click', ev => {
  const at = eventElement(ev)?.closest<HTMLElement>('.ru-at[data-href]');
  if (!at) return;
  ev.preventDefault(); ev.stopPropagation();
  location.hash = at.dataset.href ?? '';
}, true);

// app/calendar.ts
// ----------------------------------------------------------------- page and load
function calendarRoute(r: string): boolean {
  if (!rdUnder((r || '').split('?')[0] ?? '', CAL_ROUTE)) return false;
  pageCalendar(r);
  return true;
}

// app/meetings.ts
// Review stays personal until approval; counts are quiet and never enter Needs you.
function meetPendingBadge(count: number): void {
  for (const el of document.querySelectorAll<HTMLElement>('[data-meet-pending]')) {
    el.hidden = !count; countSet(el, count > 99 ? '99+' : String(count || ''));   // a new number ticks in (ui/app/motion.ts)
    el.setAttribute('aria-label', `${count} pending meetings`);
  }
}

// app/person-page.ts
// Each browser the person opens Tico in with Browser notifications on and allowed takes push too.
function pushKeep(on: boolean | undefined) {
  if (on && 'Notification' in window && Notification.permission === 'granted') void pushSubscribe().catch(() => {});
}

// app/bot-branches.ts

// app/github-nudge.ts
// The bot a Build a bot task is making, by its name in the title ('' for any other task, or one that is over).
function ghNudgeBuildName(task: GithubNudgeTask): string {
  const title = String(task.title ?? '');
  if (task.owner_id !== 'bot:botops' || !title.startsWith(GH_NUDGE_BUILD_PREFIX)) return '';
  if (GH_NUDGE_OVER.has(String(task.status ?? ''))) return '';
  return title.slice(GH_NUDGE_BUILD_PREFIX.length).trim();
}

// app/bot-page.ts
const isCommand = (text: unknown) => /^\s*\//.test(String(text || ''));
const commandWord = (text: unknown) => (String(text || '').trim().match(/^\/([a-z0-9-]*)/i) || ['', ''])[1]!.toLowerCase();
const helpHTML = () => `<div class="cmd-out"><div class="lbl">Commands</div>${SLASH_HELP.map(([c, d]) =>
  `<div><code>${esc(c)}</code> <span class="muted">${esc(d)}</span></div>`).join('')}
  <div class="muted" style="margin-top:6px">Anything starting with <code>/</code> stays here; it is never sent to a bot.</div></div>`;
const unknownHTML = (word: string) => `<div class="cmd-out"><span class="err">Unknown command${word ? ` <code>/${esc(word)}</code>` : ''}.</span>
  <span class="muted">Messages starting with <code>/</code> are not sent. Commands: ${SLASH_HELP.map(([c]) => `<code>${esc(c)}</code>`).join(', ')}.</span></div>`;

// app/task-modal.ts
let TASK_CHAT: TaskChatState | null = null;
   // {task, id}: a link's message, once that task's conversation shows
const TASK_DRAFTS = new Map<string, string>();function taskChatCurrent(state: TaskChatState) { return TASK_CHAT === state && state.dialog.open; }

// app/roadmaps.ts
// Set by the #/settings/roadmap route (Health's fix link): bring the Public roadmap card into view once it is drawn.
let ROADMAP_SETTINGS_REVEAL = false;


// app/task-props.ts
// who may change what (the server holds the rules; this only hides controls that could never work)
function taskPropRights(t: PropTask): PropRights {
  const me = myActor(), mover = canMove();
  const party = !!me && [t.owner_id, t.requester_id, taskRequester(t)].includes(me);
  const live = !taskFinished(t);
  const onTask = !!me && Object.values<string[]>(t.roles || {}).some(actors => actors.includes(me));
  return {mover, party, live, edit: (mover || party) && live, links: mover && live, reopen: mover || party,
    snooze: live && (mover || party || onTask)};
}

// app/settings.ts
const harnessRow = (id: string | undefined) => HARNESS_ROWS.find(row => row.id === id) || null;
// System (it was Rules, before that Routines) is gone: settingsMovedOut sends its addresses on. chat: the old Chat tab; providers and
// subscriptions: what is the Models tab now; limits: its turn levels; gateway: the Gateway page, now the Tico Gateway
// card on Models (settingsShow scrolls to it); repos: Repositories, now a card on General. Inboxes (and Mailboxes, Email & Phone and Mail
// before it) moved to Tools; their addresses redirect there (ui/app/tools.ts toolsRoute).
const SETTINGS_ALIAS: Record<string, string> = {humans: 'people', computers: 'devices', repositories: 'repos',
  rules: 'system', routines: 'system', recurring: 'system', screening: 'security', chat: 'models', providers: 'models', subscriptions: 'models',
  limits: 'models', 'turn-levels': 'models', gateway: 'models'};
// System's cards left Settings: its jobs are Automations' System jobs, Releases, General Manager and the Strategy Owner are
// those built-in bots' Settings, Screening is Security. The router asks for an old address's new home (or '' to stay).
const SETTINGS_SYSTEM_BOTS: Record<string, string> = {'set-general-manager-card': 'general-manager', 'set-strategy-bot-card': 'strategy-owner'};

// app/settings-webhooks.ts
const WH_HOOKS_ROUTE = '#/tools/webhooks', WH_INCOMING_ROUTE = '#/automations/webhooks';
// The incoming webhooks (GET /v2/triggers), loaded when a part opens; other files read it through webhookTriggers().
let WH_TRIGGERS: WebhookTrigger[] | null = null;

// app/settings-bots.ts
const settingsBotName = (id: unknown) => botRow(id)?.display_name || botSlug(id);
const settingsPersonName = (id: string | null | undefined) => settingsPerson(id)?.name || id || 'Unassigned';
const settingsHarnessName = (id: string | null | undefined) => settingsHarness(id)?.label || id || '';

// app/inboxes.ts
// The view a Tools address names (its parts after #/tools/), or null when it is not one of these pages.
function inboxesRoute(parts: string[]): string | null {
  const [seg = '', id, tab] = parts;
  if (seg === 'settings') return INBOX_SECTIONS.some(([k]) => k === id) ? 'settings/' + id : 'settings';
  if (INBOX_VIEWS[seg] && !id) return seg;
  if (!id) return null;
  if (seg === 'address' && id === INBOX_OUTBOX_ID) return 'outbox/company';
  const kinds: Record<string, string> = {mailbox: 'mailbox', number: 'number', address: 'postal'};
  const kind = kinds[seg];
  if (!kind) return null;
  return kind + '/' + id + (tab === 'activity' && INBOX_KINDS[kind]?.activity ? '/activity' : '');
}

// app/tools.ts
const TOOLS_KEYS = '#/tools/keys';
// An old Settings › Inboxes address (what follows #/settings/inboxes/) at its home under Tools.
function toolsFromInboxes(rest: string): string {
  const [head = '', id = ''] = rest.split('/');
  if (!head) return TOOLS_ROUTE + '?show=mail';
  if (head === 'settings' || INBOX_SECTIONS.some(([k]) => k === head)) {
    const section = head === 'settings' ? id : head;
    return '#/tools/settings' + (INBOX_SECTIONS.some(([k]) => k === section) ? '/' + section : '');
  }
  if (head === 'messages') return '#/tools/messages';
  if (INBOX_KINDS[head] && id) return inboxesLink(head, decodeURIComponent(id));
  return TOOLS_ROUTE + '?show=mail';
}

// app/recruit.ts
// Page-local state: which load is current, the arrival in progress, each seat's applicants once listed (the sidebar
// shows their count), and the Computer and Model chosen while posting, which the Hire box starts from.
const RECRUIT: RecruitState = {load: 0, arrive: '', run: null, found: {}, counts: {}, prefs: {}};

// app/updates.ts
// Startup asks for the feed beside the roster when Updates is the page it opens on, a round trip sooner (boot.js).
function updPrefetch(): void {
  if (!(location.hash === UPDATES || location.hash.startsWith(UPDATES + '?'))) return;
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  getPrefetch(updFeedPath({...updPrefs(), kind: params.get('kind') === 'weekly' ? 'weekly' : 'daily', view: params.get('view') === 'archive' ? 'archive' : 'inbox'}));
}
// The badge on the rail and the phone's bar, from the 30 s refresh (one small count).
// Only your direct reports' updates alert, one level down the org chart.
// Reads the server may not count yet: seen cards waiting to be sent (UPD.pending) and those in flight. A count or a
// list asked for before they settle still has them unread, and would put the badge (and the cards) back: updLoad keeps
// them read and takes them off its count, and updUnreadRefresh drops a count asked for then.
// `landed` keeps when each read was counted, so a reply asked for before then is caught too (kept a minute).
const UPD_READS = {sending: new Set<string>(), landed: new Map<string, number>(), at: 0};
function updUnsent(asked = Date.now()): Set<string> {
  const now = Date.now();
  for (const [id, at] of UPD_READS.landed) if (now - at > 60000) UPD_READS.landed.delete(id);
  return new Set([...(UPD?.pending || []), ...UPD_READS.sending, ...[...UPD_READS.landed].filter(([, at]) => at >= asked).map(([id]) => id)]);
}
async function updUnreadRefresh(): Promise<UpdData | null> {
  const asked = Date.now();
  if (UPD?.pending.size) void updFlush(UPD);           // a read whose save failed is sent again, or its count stays held
  const r = await v2Get('/v2/updates/unread?scope=direct');
  if (r) {
    // A count asked for while this page's reads were unsent, or before they settled, still counts them: the next
    // refresh brings the right number.
    if (!updUnsent(asked).size && asked > UPD_READS.at) updBadge(r.unread);
    meetPendingBadge(r.meetings_pending || 0);
  }
  return r;
}

// app/automations.ts
// Listening (ui/app/listening.ts) is this page over the automations whose trigger is `listening`: its own sidebar row,
// its own list, the same header, flow, editors and runs.
const LISTENING = '#/listening';
const auUnder = (r: string, base: string): boolean => r === base || r.startsWith(base + '/') || r.startsWith(base + '?');

// app/automations-maintenance.ts
const AU_SYSJOBS = 'system-jobs';

// app/computer-screen.ts
// The bot's computer when its screen is on and this person may open it; '' otherwise.
function botScreenComputer(slug: string): string | undefined {
  const id = botRow(slug)?.machine?.runner_id;
  const machine = screenMachine(id);
  return screenOn(machine) && screenMayOpen(machine) ? id : '';
}
const screenHref = (id: string) => `#/computers/${encodeURIComponent(id)}/screen`;

// app/finance-pnl.ts
const FIN_PNL: FinPnlControls = {period: 'month', basis: 'cash', start: '', end: '', rangeOf: '', picked: false, compare: '', teams: [], mates: [],
  who: 'both', unattributed: false, sel: null, budgetCol: -1, last: null};

// app/org-rules.ts
// ----------------------------------------------------------------- mentions
// A rule names a teammate the way a thread's composer writes a mention: @slug for a bot, @handle for a person (their
// id, the part of their email before the @, or a first name no one else shares). Each shows as a mention chip.
function ruMentionOf(word: string) {
  const w = word.toLowerCase();
  const bot = (S.emps || []).find(e => e.status !== 'archived' && (String(e.id).toLowerCase() === w || String(e.name || '').toLowerCase() === w));
  if (bot) return {href: '#/bot/' + encodeURIComponent(bot.id), name: botDisplayName(bot.id)};
  const people = (S.people || []).filter((p: RuPerson) => !p.archived), first = (p: RuPerson) => String(p.name || '').split(' ')[0]?.toLowerCase();
  const firsts = people.filter(p => first(p) === w);
  const p = people.find(p => String(p.id).toLowerCase() === w || (p.email && personHandle(p.email) === w)) || (firsts.length === 1 ? firsts[0] : null);
  return p ? {href: '#/person/' + encodeURIComponent(p.id), name: p.name || p.id} : null;
}

// app/bot-branches.ts
function botBranchChatTarget(slug: string): string;
function botBranchChatTarget(slug: string | undefined): string | undefined;
function botBranchChatTarget(slug: string | undefined): string | undefined {
  const original = botRow(slug);
  if (!original?.shared) return slug;
  return S.emps.find(e => e.shared_from === slug && e.owner_id === S.me?.id && e.status === 'active')?.id || slug;
}

// app/github-nudge.ts
// A task BotOps files to build a bot: "Build a bot: <name>", owned by BotOps (playbooks/set-up-a-bot.md).
const GH_NUDGE_BUILD_PREFIX = 'Build a bot: ';
// A task that is over gets no card: there is nothing left to build.
const GH_NUDGE_OVER = new Set(['done', 'closed', 'declined']);

// app/bot-page.ts
// ----------------------------------------------------------------- slash commands
// A message starting with "/" is a command to the hub, never a task for a bot: a typed /clear
// once became an Issue that the bot then went and ran.
const SLASH_HELP = [
  ['/clear', 'start a fresh session for this bot on its next run'],
  ['/help', 'show this list'],
];

// app/settings.ts
// Each model CLI's capabilities, from its manifest (GET /v2/models `harnesses`, runner-rs/harnesses/*.toml): what the UI
// asks instead of naming CLIs. Fetched once; until then, or if the fetch fails, every capability reads as unknown.
let HARNESS_ROWS: HarnessRow[] = [];

// app/settings-bots.ts
const settingsPerson = (id: string | null | undefined) => SETTINGS_DATA.people.find(person => person.id === id);
const settingsHarness = (id: string | null | undefined) => (SETTINGS_DATA.harnesses || []).find(row => row.id === id);

// app/inboxes.ts
const INBOX_KINDS: Record<string, InboxKind> = {}, INBOX_VIEWS: Record<string, InboxView> = {}, INBOX_SETTINGS: InboxSetting[] = [], INBOX_PANELS: InboxPanel[] = [];
const INBOX_SECTIONS: [string, string][] = [['email', 'Email'], ['texts', 'Texts'], ['letters-in', 'Letters in'], ['letters-out', 'Letters out']];
const INBOX_OUTBOX_ID = 'outbox';
// The Tools address of an inbox ('mailbox', id), of Tools › Settings ('settings', section) or of a view ('messages').
function inboxesLink(kind: string, id?: string): string {
  if (kind === 'settings') return '#/tools/settings' + (id ? '/' + encodeURIComponent(id) : '');
  if (kind === 'outbox') return '#/tools/address/' + INBOX_OUTBOX_ID;
  if (!INBOX_SEGMENTS[kind]) return '#/tools/' + kind;
  return `#/tools/${INBOX_SEGMENTS[kind]}${id ? '/' + encodeURIComponent(id) : ''}`;
}

// app/updates.ts
function updPrefs(): UpdPrefs {
  const out: UpdPrefs = {mine: true, layout: 'tree', scope: null};
  try {
    out.mine = localStorage.getItem('tico.updates.mine') !== '0';
    out.layout = localStorage.getItem('tico.updates.layout') === 'list' ? 'list' : 'tree';
    const scope = localStorage.getItem('tico.updates.scope');
    out.scope = scope === 'below' || scope === 'all' ? scope : null;
  } catch {}
  return out;
}
function updFeedPath(state: UpdFeedQuery, more = false): string {
  if (updIsTree(state)) {
    const q = new URLSearchParams({kind: state.kind});
    if (state.day) q.set('day', state.day);
    if (state.scope) q.set('scope', state.scope);
    return '/v2/updates/tree?' + q;
  }
  const q = new URLSearchParams({kind: state.kind, limit: '40'});
  if (state.mine) q.set('mine', 'true');           // the count and the feed agree with My bots
  if (state.view === 'archive') q.set('archive', 'true');
  if (more && state.data?.next_before) q.set('before', state.data.next_before);
  return '/v2/updates?' + q;
}

// app/computer-screen.ts
// ----------------------------------------------------------------- who sees it
// The server's rule (the sessions route): the computer's operator or a bot admin.
const screenMayOpen = (machine: ScreenMachine | null | undefined): boolean => !!machine && !machine.revoked_at && (settingsIsAdmin() || (!!S.me?.id && machine.owner_id === S.me.id));
const screenOn = (machine: ScreenMachine | null | undefined) => !!machine && machine.screen && machine.screen !== 'off' && machine.screen_available !== false;
// A computer's row from what is already loaded: the bot rows' own computer first (every refresh reloads them), then
// Settings' operations listing, which is read only when Settings opens, for a computer with no bots and for revoked_at.
function screenMachine(id: string | null | undefined): ScreenMachine | null {
  if (!id) return null;
  const row = (SETTINGS_DATA.machines || []).find(m => m.id === id);
  const bot = (S.emps || []).find(e => e.machine?.runner_id === id)?.machine;
  if (!row && !bot) return null;
  return {id, label: bot?.label || row?.label, owner_id: bot?.owner_id ?? row?.owner_id, revoked_at: row?.revoked_at,
          screen: bot?.screen ?? row?.screen, screen_available: bot?.screen_available ?? row?.screen_available};
}

// app/inboxes.ts
// A kind's segment in its Tools address. Tico addresses and phone numbers are one page (inDetail tells them apart);
// letters out is the company's one outbox, so it has a fixed id.
const INBOX_SEGMENTS: Record<string, string> = {mailbox: 'mailbox', tico: 'number', number: 'number', postal: 'address', outbox: 'address'};

// app/updates.ts
const updIsTree = (state: Pick<UpdState, 'view' | 'layout'>): boolean => state.view !== 'archive' && state.layout !== 'list';

// app/updates.ts
function updBadge(n: number): void {
  S.updUnread = n;
  // A new number ticks in (countSet, ui/app/motion.ts).
  for (const el of document.querySelectorAll<HTMLElement>('[data-upd-badge]')) { el.hidden = !n; countSet(el, n > 99 ? '99+' : String(n || '')); }
  // In the tree the badge also counts direct reports' updates from other days: the button follows it both ways.
  const allRead = $('#upd-allread');
  if (allRead && UPD && updIsTree(UPD)) allRead.hidden = !(n > 0) && !(UPD.list || []).some(u => !u.read && !u.quiet);
}

// first-run.ts
// Onboarding (backend/lifecycle.py): the bot learns in chat; a person above it on the org chart marks it complete.
function frBotBannerHTML(e: FrBot | null | undefined): string {
  if (!frNeedsSetup(e)) return '';
  const manager = typeof settingsCanManageBot === 'function' && settingsCanManageBot(e);
  const planned = e.status === 'planned';
  const can = e.can_chat && (!planned || manager);
  const ready = e.onboarding_ready?.summary;
  return `<section class="bot-onboard" id="bot-onboard" role="status" aria-label="Onboarding">
      <span class="pill needs">Onboarding</span>
      ${can ? '<button class="primary" type="button" id="bot-start-setup">Start onboarding</button>'
        : `<span class="muted">${planned ? 'Getting ready.' : 'It learns in chat with its manager before it takes any work.'}</span>`}
      ${e.can_complete_onboarding ? '<button type="button" id="bot-finish-setup">Mark onboarding complete</button>' : ''}
      <button class="ghost" type="button" data-setup-help="bot">Want help onboarding it?</button>
      ${can ? '<p class="muted bot-onboard-tip">Tip: open the mic and talk. Say everything; it sorts it out.</p>' : ''}
      ${ready ? `<p class="muted bot-ready" id="bot-ready">It says it is ready: ${esc(ready)}</p>` : ''}
    </section>`;
}

// goals-kpis.ts
// A status dot: green, yellow, red, gray (stale, missing or no data) or a ring (fresh, no target to judge by).
function gdot(status: string | null | undefined, title = '') {
  const s = ['green', 'yellow', 'red', 'none'].includes(status!) ? status! : 'gray';
  return `<span class="gdot ${s}" role="img" aria-label="${esc(KPI_LABEL[s])}"${title ? ` title="${esc(title)}"` : ''}></span>`;
}
function kpiNum(value: number | string | null | undefined, unit: string | undefined = '') {
  if (value == null || value === '') return '';
  const text = Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  unit = String(unit || '').trim();
  if (!unit) return text;
  if (unit === '%' || unit === 'pp') return text + unit;
  if (['$', '€', '£'].includes(unit)) return unit + text;
  // "1 accounts" reads wrong: a word unit drops its plural s for exactly one.
  if (Number(value) === 1 && /^[A-Za-z]+s$/.test(unit) && !/ss$/.test(unit)) unit = unit.slice(0, -1);
  return `${text} ${unit}`;
}
// A tiny line of the last readings, oldest first; the last point takes the status colour.
function kpiSpark(values: number[] | undefined, status: string, w = 64, h = 18) {
  const v = (values || []).map(Number).filter(Number.isFinite);
  const s = ['green', 'yellow', 'red'].includes(status) ? status : 'gray';
  if (v.length < 2) return `<svg class="kspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${v.length ? `<circle class="kspark-end ${s}" cx="${w - 3}" cy="${h / 2}" r="2.2"/>` : ''}</svg>`;
  const lo = Math.min(...v), span = Math.max(...v) - lo || 1, pad = 3;
  const pts = v.map((y, i): [number, number] => [pad + i * (w - 2 * pad) / (v.length - 1), h - pad - (y - lo) / span * (h - 2 * pad)]);
  const last = pts.at(-1)!;
  return `<svg class="kspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ')}"/><circle class="kspark-end ${s}" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.2"/></svg>`;
}

// app/person-page.ts
// Manage on them, as the server says it: yourself, the owner, an admin, or anyone they report up to.
function personCanEdit(p: Pick<RosterPerson, 'id' | 'my_access'>) {
  return p.my_access ? !!p.my_access.manage : S.me?.role === 'owner' || S.me?.id === p.id;
}

// app/bot-page.ts
const botPersonName = (id: string | null | undefined) => (S.people || []).find(p => p.id === id)?.name || settingsPersonName(id);
// The server cuts the Role section at 600 characters, so the lead can stop mid-sentence: end at the
// last full sentence instead, and put whatever is left behind "more".
function goalParts(role: string | null | undefined) {
  const full = String(role || '').trim();
  const para = (full.split(/\n\s*\n/)[0] || full).trim();
  let lead = para;
  if (!/[.!?]["'’)\]]?$/.test(para)) {
    let cut = 0;
    for (const m of para.matchAll(/[.!?]["'’)\]]?(?=\s|$)/g)) cut = m.index + m[0].length;
    if (cut > 60) lead = para.slice(0, cut).trim();
  }
  return {lead, rest: full.slice(lead.length).trim()};
}
// A desktop is wide enough for both: chat on the left, the bot's tasks on the right, each scrolling
// on its own (#514). A phone keeps one at a time behind the Chat | Tasks switch.
const BOT_WIDE = window.matchMedia('(min-width: 1100px)');

// app/bot-page-view.ts
// BOT is reassigned as the page changes (null when no bot page is open), so readers call this each time and narrow it.
function botPageView(): BotPageView | null { return BOT; }

// app/task-modal.ts
// Runs on (POST /v2/tasks/{id}/model, backend/task_run_models.py): the model this task's runs use, or the bot's task model
// until one is set on it. The model list is GET /v2/models, read once here and by the automation step editor, which offers
// the same models. Gateway tier picks (tico-*) are made in the bot's settings, so they stay out of both.
let TASK_RUN_ROWS: TaskRunRow[] | null = null, TASK_RUN_LOADING: Promise<ModalModelRow[]> | null = null;

// app/settings.ts
// The team owner's name, for "who can change this" lines; '' when the roster has not loaded.
function settingsOwnerName() {
  const people: SettingsPerson[] = [...(SETTINGS_DATA.people || []), ...(S.people || [])], id = S.me?.owner_id;
  const owner = people.find(p => (id && p.id === id) || p.owner || p.role === 'owner');
  return owner?.name || S.config?.owner_name || '';
}
// Ask before anything hard to undo. Resolves true only on the confirm button.
function settingsConfirm({title, text, ok = 'Confirm', danger = true}: {title: string; text: string; ok?: string; danger?: boolean}) {
  return new Promise<boolean>(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal settings-confirm'; dialog.setAttribute('aria-label', title);
    dialog.innerHTML = `<form method="dialog"><div class="tmodal-title">${esc(title)}</div><div class="tmodal-body"><p>${esc(text)}</p>
      <div class="onb-actions"><button type="submit" class="ghost" value="cancel" data-confirm-cancel>Cancel</button><span class="spacer"></span>
      <button type="submit" class="primary${danger ? ' people-danger' : ''}" value="ok" data-confirm-ok>${esc(ok)}</button></div></div></form>`;
    dialog.addEventListener('close', () => { resolve(dialog.returnValue === 'ok'); dialog.remove(); });
    document.body.appendChild(dialog); dialog.showModal();
    (dialog.querySelector('[data-confirm-cancel]') as HTMLElement).focus();
  });
}

// app/settings-bots.ts
// "Claude Opus 5 · high · 1M" (modelChoiceWords and modelContextWords, ui/app/format.ts); the tool that runs it only when it is not the model's own.
const settingsChoiceLabel = (harness: string | null | undefined, model: string | null | undefined, effort: string | null | undefined) => {
  if (!model) return 'not set';
  const row = settingsModel(model), own = row?.harnesses?.[0] || row?.runtime || harness;
  const words = modelChoiceWords(model, settingsEffortName(effort), harness, own) || settingsModelName(model);
  const context = modelContextWords(row?.context_tokens);
  return context ? `${words} · ${context}` : words;
};
// A bot with no model of its own follows the team default; say which one it resolves to.
function settingsDefaultLabel(e: SettingsBot) {
  if (e.model || !e.resolved_model) return 'not set';
  const row = settingsModel(e.resolved_model);
  return `team default (${settingsChoiceLabel(e.resolved_runtime, e.resolved_model, row?.default_effort || '')})`;
}

// app/inboxes.ts
// 'settings' or 'settings/<section>', '<view>' (inboxesView), or '<kind>/<id>[/activity]' (one inbox). Set by Tools' router.
let INBOXES_VIEW: string = '';
const IB: {run: number} = {run: 0};
const inboxesPill = (status: string, text?: string): string => {
  const [tone, label] = INBOX_STATUS[status] || ['', status];
  return `<span class="pill${tone ? ' ' + tone : ''}" data-ib-status="${esc(status)}">${esc(text || label)}</span>`;
};
function inboxesCtx(run: number): InboxCtx {
  const loads: Record<string, Promise<unknown> | undefined> = {};
  return {
    admin: settingsIsAdmin(), me: S.me, run,
    live: () => run === IB.run,
    once: <T>(name: string, load: () => T | PromiseLike<T>) => {
      loads[name] ||= Promise.resolve().then(load);
      return loads[name] as Promise<T>;
    },
    refresh: () => inboxesRender(),
    go: inboxesGo,
  };
}
// A card on Tools › Settings, or on an inbox's page.
const inboxesCard = (id: string, title: string, body: string, action = ''): string =>
  `<section class="card ib-card"${id ? ` id="${esc(id)}"` : ''}><header><h2>${esc(title)}</h2>${action}</header>${body}</section>`;

// app/tools-api.ts
const tafMoney = (cents: number, currency?: string | null): string => { try { return (cents / 100).toLocaleString('en-US', {style: 'currency', currency: currency || 'USD'}); } catch { return (cents / 100).toFixed(2); } };

// app/settings-strategy-bot.ts
const strategyBotLogShown = (): boolean => S.me?.role === 'owner';

// app/cloud-machines.ts
const cloudMachineOn = (e: CloudMachineBot | null | undefined) => e?.cloud_machine?.mode === 'cloud';
// The Computer value on the bot's page and in its Info for a bot on a cloud machine; '' for any other.
function cloudMachineComputerHTML(e: CloudMachineBot): string {
  if (!cloudMachineOn(e)) return '';
  const m = e.cloud_machine;
  if (!m) return '';
  return `${cloudMachineShared(e) ? 'Shared' : 'Own'} cloud machine <span class="muted">· ${esc(cloudMachineStatus(m))}</span>`;
}

// app/bot-profile.ts
// The picker reads its choices and their one-line descriptions from GET /v2/profiles, so a profile the server adds
// shows with no UI change. The labels here are for history rows and a picker drawn before that answer lands.
const PROFILE_LABELS: Record<string, string> = {doer: 'Doer', reviewer: 'Reviewer', lead: 'Lead', manager: 'Manager', responder: 'Responder', monitor: 'Monitor', custom: 'Custom'};
let PROFILE_LIST: BpChoice[] | null = null, PROFILE_LOAD: Promise<BpChoice[]> | null = null;

// app/settings-repositories.ts
// Two to three choices as one row of real radios: arrow keys move between them, as everywhere else.
function repoSegHTML(name: string, label: string, options: ReadonlyArray<readonly [string, string]>, value: string, disabled: boolean, extra = '') {
  return `<div class="repo-seg${extra}" role="radiogroup" aria-label="${esc(label)}">${options.map(([v, text]) =>
    `<label><input type="radio" name="${esc(name)}" value="${esc(v)}"${v === value ? ' checked' : ''}${disabled ? ' disabled' : ''}><span>${esc(text)}</span></label>`).join('')}</div>`;
}

// app/settings-providers.ts
// The two choices that run main work on Claude and ChatGPT tick them when nothing is ticked yet.
const PRESET_PROVIDERS: Record<string, string[]> = {'subscriptions-gateway': ['anthropic', 'openai'], subscriptions: ['anthropic', 'openai']};

// app/goals.ts
const goalLive = (g: {status?: string | null}) => !['done', 'dropped'].includes(String(g.status));
const goalRank = (a: GkGoal | GkStrategyBet, b: GkGoal | GkStrategyBet) => (a.rank ?? 1e9) - (b.rank ?? 1e9);

// app/automations.ts
const auHref = (id: string | undefined, view?: string) => auBase() + (id ? '/' + encodeURIComponent(id) : '') + (id && view && view !== 'flow' ? '/' + view : '');

// app/automations-maintenance.ts
const AU_MAINT_OWNER = 'tico';const AU_BUILTIN_PILL = '<span class="pill">Built-in</span>';

// app/usage.ts
function useSpan(range: string, custom?: { from?: string; to?: string } | null): { from: string; to: string } {
  const today = useDay(new Date());
  if (range === 'today') return {from: today, to: today};
  if (range === '30d') return {from: useAdd(today, -29), to: today};
  if (range === 'month') return {from: today.slice(0, 8) + '01', to: today};
  if (range === 'custom' && custom?.from && custom?.to) return {from: custom.from, to: custom.to};
  return {from: useAdd(today, -6), to: today};
}
function useMoney(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n > 0 && n < 0.01) return '<$0.01';
  return '$' + n.toLocaleString(undefined, n >= 100 ? {maximumFractionDigits: 0} : {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
const useSpend = (r: UseFigures): number => (r.est_cost_usd || 0) + (r.subscription_equiv_usd || 0);
// The cell on a Usage row and in Settings > Bots: the limit, a chip when it is near or met, and a click to change it.
function useLimitButton(slug: string, name: string | undefined, limit: UseLimit | null | undefined): string {
  if (!limit?.may_edit) return '<span class="use-limit"></span>';
  const text = useLimitText(limit);
  const chip = limit.blocked ? '<span class="pill fail">Paused</span>' : limit.percent >= 80 ? `<span class="pill waiting">${limit.percent}%</span>` : '';
  return `<button type="button" class="use-limit${text ? '' : ' none'}" data-use-limit="${esc(slug)}" aria-label="Limit for ${esc(name || slug)}"
    title="${limit.blocked ? esc(`${useHeldBy(limit)}: no new work until it resets or the limit is raised`) : 'Change limit'}">${chip}<span>${text ? esc(text) : 'Set limit'}</span></button>`;
}

// app/replay.ts
function replayScopeOptions(selected: string) {
  const opt = (value: string, label: string) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`;
  const groups = (S.orgGroups || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'planned' && e.status !== 'archived').sort((a, b) => botDisplayName(a.id).localeCompare(botDisplayName(b.id)));
  const people = (S.people || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  return opt('', 'Everyone')
    + (groups.length ? `<optgroup label="Teams">${groups.map(g => opt('team:' + g.id, g.name || g.id)).join('')}</optgroup>` : '')
    + (bots.length ? `<optgroup label="Bots">${bots.map(e => opt('bot:' + e.id, botDisplayName(e.id))).join('')}</optgroup>` : '')
    + (people.length ? `<optgroup label="People">${people.map(p => opt('human:' + p.id, p.name || p.id)).join('')}</optgroup>` : '');
}
// Real names throughout, the viewer's own too: a replay is watched and recorded, so "You" would read wrong.
// A bot no longer on the roster keeps its history: the server's name for it ("Deleted bot"), never its id.
const replayName = (data: { names?: Record<string, string> | undefined }) => (a: ReplayActor): string => {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug) return botRow(slug) ? botDisplayName(slug) : data.names?.[String(a)] || 'Deleted bot';
  if (pid) return data.names?.[String(a)] || (S.people || []).find(p => p.id === pid)?.name || personDisplay(pid);
  return a === 'keeper' ? AUTOMATIC_LABEL : String(a || '');     // the server itself, never the Assistant or the app
};

// app/computer-screen.ts
// The bot header's button: only when its Screen row in More says ready (so not while the computer is offline).
const botScreenLinkHTML = (slug: string) => {
  const s = botScreenState(slug), id = s.state === 'ready' ? s.id : '';
  return id ? `<a class="bot-learn bot-screen" id="bot-screen" href="${screenHref(id)}" aria-label="Screen" title="Screen"><span class="nav-icon" aria-hidden="true">computer</span><span class="bl-label">Screen</span></a>` : '';
};
function botScreenRowHTML(slug: string) {
  const s = botScreenState(slug);
  const status = (text: string) => `<span class="bot-screen-status">${text}</span>`;
  const body = {
    ready: () => `<a class="bot-screen-open" href="${screenHref(s.id ?? '')}"><span class="nav-icon" aria-hidden="true">computer</span>Open</a>${status(` · ${s.machine?.screen === 'control' ? 'Control' : 'View'}`)}`,
    off: () => status('Off'),
    not_offered: () => status('Off'),
    offline: () => status("This computer is offline. Screen works when it's back."),
    not_allowed: () => status(s.owner_id ? `Only ${esc(s.owner_id)} and admins can open this computer's screen.` : "Only admins can open this computer's screen."),
    none: () => status('This bot has no computer yet.'),
    external: () => status("This bot runs outside Tico's computers, so there is no screen to show."),
    hidden: () => status("You can see this bot but not its activity, so its computer and screen aren't shown to you."),
  }[s.state]();
  return `<dt>Screen${betaPill('computer_screen')}</dt><dd class="bot-screen-row" id="bot-screen-row" data-screen-state="${s.state}" data-screen-key="${esc(botScreenKey(s))}">${body}</dd>`;
}
function screenControlHTML(machine: ScreenMachine | null | undefined, online = true): string {
  if (!machine) return '<span class="muted">No computer assigned.</span>';
  if (!screenMayOpen(machine)) return '<span class="muted">Only the computer’s operator and admins can change its screen.</span>';
  const id = machine.id || machine.runner_id || '';
  const on = !!machine.screen_available && machine.screen !== 'off' && !!machine.screen;
  const pending = SCREEN_SETUP_PENDING.has(id) && !on;
  const key = [id, machine.screen, machine.screen_available, online, pending, SCREEN_SAVING.has(id)].join('|');
  return `<div class="screen-control" data-screen-control="${esc(id)}" data-screen-control-key="${esc(key)}">
    <label class="screen-toggle">${peopleSwitch(`data-screen-toggle="${esc(id)}"`, on || pending, SCREEN_SAVING.has(id), 'Enable screen')}<span>${pending ? 'Waiting for computer setup' : on ? 'On' : 'Off'}${!online ? ' · Computer offline' : ''}</span></label>
    ${on ? `<select class="settings-inline-select" data-screen-mode="${esc(id)}" aria-label="Screen access" ${SCREEN_SAVING.has(id) ? 'disabled' : ''}><option value="view" ${machine.screen === 'view' ? 'selected' : ''}>View</option><option value="control" ${machine.screen === 'control' ? 'selected' : ''}>Control</option></select>${online ? `<a class="bot-screen-open" href="${screenHref(id)}">Open screen</a>` : ''}` : ''}
    ${pending ? `<div class="bot-screen-steps"><p>Run this on ${esc(machine.label || 'the computer')}, in its runner folder (usually <code>/opt/tico-runner</code>):</p>${screenCmdHTML(SCREEN_DOCKER_CMD, 'Docker')}
      <details><summary>Running from a checkout?</summary>${screenCmdHTML(SCREEN_CHECKOUT_CMD, 'checkout')}</details>
      <p>The screen becomes available when the computer reports it is ready. <a href="${GH}/blob/main/docs/install.md#screen" target="_blank" rel="noopener noreferrer">Setup help</a></p></div>` : ''}
  </div>`;
}

// app/computer-performance.ts
const performanceHref = (id: string) => `#/computers/${encodeURIComponent(id)}/performance`;

// app/redesign-org.ts
// A teammate's home group: the `team` its roster row carries (backend/groups.py), else the group whose members list (GET
// /v2/groups `bots`) holds the bot, which is where a built-in bot is.
function rdoHome(ref: string): string {
  const id = ref.slice(ref.indexOf(':') + 1);
  if (ref.startsWith('person:')) return (S.people || []).find(p => p.id === id)?.team || '';
  return botRow(id)?.team || rdoGroups().find((g: {id: string; bots?: string[]}) => (g.bots || []).includes(id))?.id || '';
}

// goals-kpis.ts
const KPI_LABEL: Record<string, string> = {green: 'On track', yellow: 'At risk', red: 'Off track', gray: 'No fresh data', none: 'No target'};

// app/settings-bots.ts
const settingsModel = (id: string | null | undefined) => SETTINGS_DATA.models.find(model => model.id === id);
const settingsModelName = (id: string | null | undefined) => settingsModel(id)?.label || settingsTierName(id) || id || 'Not set';
const settingsEffortName = (effort: string | null | undefined) => ({low:'low', medium:'medium', high:'high', xhigh:'xhigh', max:'max', ultra:'ultra'} as Record<string, string>)[String(effort)] || effort || '';

// app/inboxes.ts
const INBOX_STATUS: Record<string, [string, string]> = {ok: ['ok', 'Connected'], attention: ['fail', 'Needs attention'], pending: ['waiting', 'Pending'], off: ['', 'Not in use']};

// app/cloud-machines.ts
const cloudMachineShared = (e: CloudMachineBot) => cloudMachineOn(e) && e.cloud_machine?.placement !== 'own';
// "Running · Default · $0.42 this month · 3 starts", then a size waiting for the restart, and a grow note only while it
// needs someone ("Disk 96% full: XLarge is the largest size"); a problem replaces the state.
function cloudMachineStatus(m: CloudMachineRow): string {
  const parts: (string | null | undefined)[] = [m.problem || CLOUD_STATES[m.state ?? ''] || m.state, cloudSizeName(m.machine_size || m.size)];
  if (m.cost_month_usd) parts.push(`$${Number(m.cost_month_usd).toFixed(2)} this month`);
  if (m.starts_month) parts.push(`${m.starts_month} start${m.starts_month === 1 ? '' : 's'}`);
  if (m.machine_size && m.size !== m.machine_size)
    parts.push(`${cloudSizeName(m.size)} ${m.resizing ? 'once its turns finish' : 'at next start'}`);
  if (m.always_on) parts.push('Always on');
  if (m.grow_note) parts.push(m.grow_note);
  return parts.filter(Boolean).join(' · ');
}

// app/automations.ts
/** The page's own address: Listening's while its list is the one shown, else Automations'. */
const auBase = (): string => AU?.lst ? LISTENING : AUTOMATIONS;

// app/usage.ts
const useDay = (date: Date): string => date.toISOString().slice(0, 10);
const useAdd = (day: string, n: number): string => useDay(new Date(Date.parse(day + 'T00:00:00Z') + n * 864e5));
function useLimitText(limit: UseLimit | null | undefined): string {
  return USE_PERIODS.filter(([key]) => limit?.[key]).map(([key, , unit]) => useCap(useNum(limit?.[key]) ?? 0) + unit).join(' · ');
}
// What holds a bot, in words: its own limit, or the team's or a group's.
const useHeldBy = (limit: UseLimit | null | undefined): string => limit?.blocked_by && limit.blocked_by.scope !== 'bot'
  ? `${limit.blocked_by.name} is over its ${USE_WORDS[String(limit.blocked_by.period)] || limit.blocked_by.period} limit`
  : `Over its ${USE_WORDS[String(limit?.blocked)] || limit?.blocked} limit`;

// app/computer-screen.ts
// ----------------------------------------------------------------- the Screen row in a bot's More
// Always there, so the feature stays findable when it is not working: the status, and when it is not ready, why and
// what to do. Read from the bot row (online, its computer's platform) and the computer's row; no extra request.
function botScreenState(slug: string): ScreenRow {
  const e = botRow(slug);
  // Someone who may see the bot but not read it gets a row without its computer (backend/views.py): say so, name nobody.
  if (e && !('machine' in e)) return {state: 'hidden'};
  const id = e?.machine?.runner_id, machine = screenMachine(id);
  if (!machine) return {state: e?.agent ? 'external' : 'none'};
  const base = {id, machine, owner_id: machine.owner_id ? botPersonName(machine.owner_id) : '', may: screenMayOpen(machine)};
  if (!e?.online) return {...base, state: 'offline'};
  if (!machine.screen_available) return {...base, state: 'not_offered'};
  if (!machine.screen || machine.screen === 'off') return {...base, state: 'off'};
  return {...base, state: base.may ? 'ready' : 'not_allowed'};
}
// Docker: `screen on` inside the runner's container turns it on live and saves it in runner.json on the runner-home
// volume, so it survives updates. Installation troubleshooting lives in the screen documentation.
// A named or --server-network runner has runner.override.yaml beside the stock file, used on every compose call
// (install.sh, the updater); without it the command reaches the default runner's project.
const SCREEN_DOCKER_CMD = 'sudo docker compose -f runner.compose.yaml $([ -f runner.override.yaml ] && echo -f runner.override.yaml) exec runner tico-runner --config /home/runner/runner.json screen on';
const SCREEN_CHECKOUT_CMD = 'scripts/tico-computer screen on';
const screenCmdHTML = (cmd: string, label: string) => `<div class="bot-screen-cmd"><code>${esc(cmd)}</code><button class="ghost" type="button" data-screen-copy="${esc(cmd)}" aria-label="Copy the ${label} command">Copy</button></div>`;
// What the row and the header button are drawn from; a refresh redraws them only when it changes.
const botScreenKey = (s: ScreenRow): string => [s.state, s.id, s.machine?.screen, s.may, s.owner_id].map(v => v ?? '').join('|');
const SCREEN_SETUP_PENDING = new Set<string>(), SCREEN_SAVING = new Set<string>();

// app/redesign-org.ts
// The groups with their leads: GET /v2/groups when it has answered (a list, or {groups} from an older hub), else the
// roster's copy, which carries no leads.
const rdoGroups = (): OrgGroup[] => RDO?.groups || S.orgGroups || [];

// app/settings-bots.ts
function settingsTierName(id: string | null | undefined) {
  const t = settingsTierParse(id);
  return t ? `${SETTINGS_DATA.gateway?.tiers[t.tier]?.name}${t.version === 'auto' ? ' (auto)' : ' ' + t.version}` : '';
}

// app/cloud-machines.ts
const CLOUD_STATES: Record<string, string> = {none: 'Not started yet', starting: 'Starting', running: 'Running', stopping: 'Stopping',
  stopped: 'Stopped', deleted: 'Deleted', failed: 'Failed'};
const cloudSizeName = (name: string | null | undefined) => name ? name.charAt(0).toUpperCase() + name.slice(1) : '';

// app/automations.ts
// One page visit's state; a route within the page keeps it (the list, the chat), leaving drops it.
let AU: AutomationsState | null = null;

// app/usage.ts
// A number a limit or a pool holds under a field name; anything else reads as no amount.
const useNum = (v: unknown): number | null => typeof v === 'number' ? v : null;
// ----------------------------------------------------------------- limits
// A cap in estimated USD per 30 minutes, day, week and month (backend/usage_limits.py): on a bot, on the combined spend of
// a group or of the whole team, and the default for each bot with none of its own. Over one, the bots it covers take no
// new job; at 80% someone is told. The hour came before the half hour replaced it: still enforced where it is set, so it
// shows only where it is set and is never sent elsewhere (a new hour would be saved as half of it per 30 minutes).
// [field, column, unit in a row's summary, key of `spent`]
const USE_PERIODS: [string, string, string, string][] = [['per_30m_usd', '30 min', '/30m', '30m'], ['hourly_usd', 'Hour', '/hr', 'hour'], ['daily_usd', 'Day', '/day', 'day'],
                     ['weekly_usd', 'Week', '/wk', 'week'], ['monthly_usd', 'Month', '/mo', 'month']];
const USE_WORDS: Record<string, string> = {per_30m: '30-minute', hourly: 'hourly', daily: 'daily', weekly: 'weekly', monthly: 'monthly'};
const useCap = (n: number): string => '$' + (n >= 100 || Number.isInteger(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 0}) : n.toFixed(2));

// app/redesign-org.ts
let RDO: RdoState | null = null;
// app/settings-bots.ts
// {tier, level, fast, version ('auto' when none)} for a tier id, or null for any other id.
function settingsTierParse(id: string | null | undefined): SettingsTierPick | null {
  const gw = settingsGateway(), m = /^(tico-[a-z0-9-]+?)(?:-(\d+\.\d+))?$/.exec(String(id || ''));
  if (!m) return null;
  const tier = gw?.tiers[String(m[1])];
  return tier ? {tier: String(m[1]), level: tier.level, fast: tier.fast, version: m[2] || 'auto'} : null;
}

// app/settings-bots.ts
const settingsGateway = () => SETTINGS_DATA.gateway?.tiers && Object.keys(SETTINGS_DATA.gateway.tiers).length ? SETTINGS_DATA.gateway : null;

// app/bot-page.ts
const botLimited = (e: Employee | null | undefined) => !!e?.my_access && !e.my_access.read;

// app/settings-recurring.ts
// "What Tico runs on its own": every job Tico runs on its own (backend/system_routines.py) in one flat list,
// no group headings, with a search box that matches name, trigger, action, department tag and group word. Each row reads
// WHEN <trigger> THEN <action>, with its department tag, schedule, last and next run and, for a job that asks Decide,
// which route answered and this month's spend. An owner or admin switches the six AI/people jobs (and Topic changed),
// picks their schedule preset and runs them now. Who may change a job is the server's `can_change`, enforced there too.
// Fetched again at most once a minute while the screen is open (ui/app/automations-maintenance.ts auSysJobsPane).
let SYSTEM_ROUTINES: SysRoutinesData | null = null, SYSTEM_ROUTINES_AT = 0, SYSTEM_ROUTINES_Q = '', SYSTEM_ROUTINES_HELD = false;

// app/settings-webhooks.ts
// A link to the Gateway part (#/settings/models/wh-gateway) opens the fold, or does once the card is drawn
// (settingsShow, renderSettingsProviders).
let WH_OPEN_GATEWAY = false;

// app/goals.ts
// One goal, editable in place (the bot page's Goals card): the words, and what it supports.
function goalEditorHtml(g: GkGoal, goals: GkGoal[]) {
  const may = goalMayEdit(g, goals), parent = goals.find(x => x.id === g.parent_id);
  return `<div class="goal-ed" data-goal-ed="${esc(g.id)}">
      <div class="goal-ed-row"><input class="goal-ed-text" type="text" maxlength="300" value="${esc(g.title)}" aria-label="Goal"${may ? '' : ' readonly'}>
        ${may ? `<button class="ghost goal-ed-link" type="button" title="Change the goal this supports" aria-label="Change the goal this supports"><span class="nav-icon" aria-hidden="true">link</span></button>` : ''}</div>
      ${may ? `<button type="button" class="goal-ed-supports goal-ed-change" aria-label="Change the goal this supports">Supports <u>${esc(parent ? parent.title : 'nothing')}</u></button>`
        : parent ? `<div class="goal-ed-supports">Supports <a href="${GOALS}/${encodeURIComponent(parent.id)}" data-goal-go="${esc(parent.id)}">${esc(parent.title)}</a></div>` : ''}
      ${may ? `<select class="goal-ed-pick" hidden aria-label="The goal this supports">${goalLinkOptions(g, goals)}</select>` : ''}
    </div>`;
}

// app/goals.ts
// Mirrors the hub's rule: the goal's owner, the owner of the goal it supports, or anyone above the
// owner on the org chart; the environment's owner edits every goal.
function goalMayEdit(g: GoalRef, goals: GkGoal[] = []) {
  if (S.me?.role === 'owner') return true;
  const me = mePerson(), actor = me ? 'human:' + me.id : '';
  if (!actor) return false;
  const parent = goals.find(x => x.id === g.parent_id);
  return g.owner_id === actor || parent?.owner_id === actor || goalOrgPlace(g.owner_id).above.has('p:' + me!.id);
}
const GOAL_COMPANY = 'company';
function goalOwnerInfo(actor: string | undefined) {
  if (actor === GOAL_COMPANY) return {kind: 'company', id: GOAL_COMPANY, name: S.config?.company_name || 'Team', avatar: '', href: ''};
  const [kind, id] = String(actor || '').split(':');
  if (kind === 'bot') {
    const e = botRow(id);
    return {kind, id, name: e?.display_name || e?.slug || id, avatar: avatar(id, 20, stateOf(id)), href: `#/bot/${encodeURIComponent(String(id))}`};
  }
  const p = (S.people || []).find(x => x.id === id);
  return {kind: 'person', id, name: p?.name || id, avatar: personAvatar(p || {id, name: id}, 20), href: `#/person/${encodeURIComponent(String(id))}`};
}
// The options of a "Supports" select: Nothing first (a goal needs no parent), then team goals,
// goals above the owner on the org chart, and goals alongside it. Never the goal itself or anything
// under it (that would loop).
function goalSupportOptions(g: GoalRef, owner: string | null | undefined, goals: GkGoal[]) {
  const below = new Set(g.id ? [g.id] : []);
  for (let grew = Boolean(g.id); grew;) {
    grew = false;
    for (const x of goals) if (x.parent_id && below.has(x.parent_id) && !below.has(x.id)) { below.add(x.id); grew = true; }
  }
  const me = goalOrgPlace(owner);
  const rows: {company: GkGoal[]; above: GkGoal[]; alongside: GkGoal[]} = {company: [], above: [], alongside: []};
  for (const x of goals) {
    if (!goalLive(x) || below.has(x.id)) continue;
    const them = goalOrgPlace(x.owner_id);
    const where = x.owner_id === GOAL_COMPANY ? 'company' : me.above.has(them.key) ? 'above'
      : them.key && (them.key === me.key || (them.parent && them.parent === me.parent)) ? 'alongside' : '';
    if (where || x.id === g.parent_id) rows[where || 'above'].push(x);
  }
  const option = (x: GkGoal) => `<option value="${esc(x.id)}"${x.id === g.parent_id ? ' selected' : ''}>${esc(x.title)}${x.owner_id === GOAL_COMPANY ? '' : ' · ' + esc(goalOwnerInfo(x.owner_id).name)}</option>`;
  const group = (label: string, list: GkGoal[]) => list.length ? `<optgroup label="${label}">${list.sort(goalRank).map(option).join('')}</optgroup>` : '';
  return `<option value=""${g.parent_id ? '' : ' selected'}>Nothing</option>`
    + group('Team', rows.company) + group('Above on the team chart', rows.above) + group('Alongside', rows.alongside);
}
const goalLinkOptions = (g: GkGoal, goals: GkGoal[]) => goalSupportOptions(g, g.owner_id, goals);

// app/goals.ts
// Where an owner sits on the org chart: its node key and the chain of keys above it. Who is above whom is who reports
// to whom, not the groups people are in.
function goalOrgPlace(owner: string | null | undefined) {
  const up: Record<string, string> = {};
  for (const p of S.people || []) up['p:' + p.id] = orgBossKey({kind: 'person', person: p});
  for (const e of S.emps || []) up['b:' + e.id] = orgBossKey({kind: 'bot', ...e});
  const key = String(owner || '').startsWith('human:') ? 'p:' + owner!.slice(6) : String(owner || '').startsWith('bot:') ? 'b:' + (owner as string).slice(4) : '';
  const above = new Set<string>();
  for (let at = up[key]; at && !above.has(at); at = up[at]) above.add(at);
  return {key, parent: up[key] || '', above};
}

// app/settings.ts
let HARNESS_ROWS_LOADING: Promise<void> | null = null;
function loadHarnessRows() {
  HARNESS_ROWS_LOADING ||= get('/v2/models').then(d => { HARNESS_ROWS = d?.harnesses || []; }, () => { HARNESS_ROWS_LOADING = null; });
  return HARNESS_ROWS_LOADING;
}

// app/bot-learnings.ts
// A diff as lines: added and removed ones tinted with their marker dropped, each hunk header a small gap.
function learnDiff(text: string) {
  return text.split('\n').filter(l => !/^(diff --git|index |--- |\+\+\+ |\\ No newline)/.test(l)).map(l => {
    if (l.startsWith('@@')) return '<span class="hunk">⋯</span>';
    const kind = l[0] === '+' ? 'add' : l[0] === '-' ? 'del' : '';
    return `<span class="${kind}">${esc(l.slice(1)) || ' '}</span>`;
  }).join('').replace(/^<span class="hunk">⋯<\/span>/, '');
}

// app/bot-sidebar.ts
const BOT_SVG = {
  grip: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><g fill="currentColor"><circle cx="5.5" cy="3.5" r="1.3"/><circle cx="10.5" cy="3.5" r="1.3"/><circle cx="5.5" cy="8" r="1.3"/><circle cx="10.5" cy="8" r="1.3"/><circle cx="5.5" cy="12.5" r="1.3"/><circle cx="10.5" cy="12.5" r="1.3"/></g></svg>',
  eye: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  shut: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><path stroke="currentColor" stroke-width="1.6" d="M4 4l16 16"/></svg>',
};

// app/bot-tasks.ts (a bot's task row, also the Assistant page's)
// Stage names by stage id, from the pipelines list: a list row names only its stage's id.
const BOT_STAGE_NAMES = new Map<string, string>();
function botTaskStage(t: TaskAny): string {
  const stage = 'stage' in t ? t.stage as {name?: string} | null | undefined : null;
  return stage?.name || BOT_STAGE_NAMES.get(t.stage_id || '') || '';
}
function botTaskRowHTML(t: TaskAny): string {
  const stage = t.status === 'open' ? botTaskStage(t) : '';
  return taskRowHTML(t, {after: stage ? `<span class="muted">${esc(stage)}</span>` : ''});
}
