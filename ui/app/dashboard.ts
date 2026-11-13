/* ui/app/dashboard.ts — Company › Mine (My dashboard, #/home, the landing page), Company › Dashboard
   and team pages (#/team/<group>): a grid of pinned built-in cards and pages (backend/cards.py). My dashboard is the
   place person:me, the reader's own. Each card shows its reader only what they may already see; a person who arranges the
   place drags, sizes, adds and removes pins. The bot sidebar draws its card and page widgets with the same pieces.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OVERVIEW_CAMPUS = OVERVIEW + '/campus';
const OVERVIEW_GRAPH = OVERVIEW + '/graph';           // ui/app/overview-graph.ts
const OVERVIEW_FACTORIES = OVERVIEW + '/factories';   // ui/app/overview-factories.ts
const OVERVIEW_COMPANY = OVERVIEW + '/company-page';   // ui/app/company-page.ts
const TEAM_PAGE = '#/team/';
const DASH_SIZES = [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']];
// {place, month, key, view, choices, root, menu, editing}: the place on screen. A write's answer is drawn only while it is
// still the place. `editing`: Edit shows the grips, the ⋯ menus and + Card / + Page until Done, Escape or leaving the page;
// it is this view's alone, never saved.
interface DashState {
  place: string;
  month: string;
  key?: string;
  view: DashView | null;
  choices: null;
  root: HTMLElement | null;
  menu: HTMLElement | null;
  editing: boolean;
  readAt: number;
  refreshSeq?: number;
  reading?: boolean;
  choicesBy?: Record<string, Promise<DashChoices>>;
  choicesAt?: number;
  /** The place's reads against the live task events (task-api.ts TaskFeed): a read that lands late undoes no newer event. */
  feed: TaskFeed;
}
let DASH: DashState | null = null;
// A place drawn on this grid under its own head (the Growth page, ui/app/growth-page.ts): {head(state) → its HTML,
// pages() → whether + Page shows, drawn(state) after each draw, wire(state) once}.
const DASH_PLACES: Record<string, DashPlace> = {};
// By template: the heading a card shows when it has no title of its own, and what it says when it shows nothing.
const DASH_HEADING: Record<string, (card: DashCard) => string | undefined> = {};
const DASH_EMPTY: Record<string, string> = {};
// By template, HTML for a card that shows nothing, in place of DASH_EMPTY's words: Growth's empty cards name the setup
// step that fills them, with a link (ui/app/growth-start.ts).
const DASH_NONE: Record<string, (card: DashCard) => string> = {};

// A place as GET /v2/places/<place> answers it (backend/cards.py), with who sees a team page.
type DashCard = import('../types/api').Card;
type ApiCard = DashCard;
// A place drawn on the grid under its own head (DASH_PLACES, ui/app/growth-page.ts).
interface DashPlace { first?: string; pages: () => boolean; head: (state: DashState) => string; wire?: (state: DashState) => void; drawn?: (state: DashState) => void }
// A page as a pin and the Add page form list it.
interface DashPageItem { file_id: string; title?: string | null; bot: string; updated_at?: string | null }
// A pin holds a card or a page.
type DashPin = Omit<import('../types/api').Place['pins'][number], 'card' | 'page'>
  & ({ card: DashCard; page?: undefined } | { card?: undefined; page: DashPageItem });
// The place's kind is the place's own prefix when the answer had no pins (pageDashboard).
interface DashView {
  kind: string | undefined;
  name?: string;
  group?: string;
  month?: string;
  months?: string[];
  pins: DashPin[];
  can_edit: boolean;
  saved: boolean;
  audience?: import('../types/api').PlaceAudience;
}
// What a built-in card shows, by template (backend/cards.py): each template's card carries its own of these fields.
interface DashWeek { week: string; done: number }
interface DashNeedsItem { task_id?: string | null; title?: string | null; line?: string | null; act?: string | null }
interface DashThread extends ThreadRow { unread?: number | null; updated_at?: string | null }
interface DashTask { id: string; title?: string | null; due_at?: string | null }
interface DashUpdate { read?: boolean; headline?: string | null; author?: string | null; created_at?: string | null }
interface DashRun { name: string; status: string; started_at?: string | null }
interface DashCardData {
  counts: Record<string, number>;
  weeks?: DashWeek[];
  count: number;
  items: DashNeedsItem[];
  threads: DashThread[];
  tasks: DashTask[];
  unread: number;
  updates: DashUpdate[];
  spent: number;
  budget?: number | null;
  month: string;
  value?: number | null;
  unit?: string | null;
  name: string;
  at?: string | null;
  spark?: number[];
  total: number;
  failed?: number;
  running?: number;
  runs: DashRun[];
  pages: DashPageItem[];
  [field: string]: unknown;
}
type DashBody = (d: DashCardData, card: DashCard) => string;
interface DashTemplate { id: string; name: string; fields?: string[] }
// GET /v2/cards/choices (api.d.ts has it as unknown): the templates and their fields, the teams, and what a field picks.
interface DashChoices {
  templates: [DashTemplate, ...DashTemplate[]];
  teams: { id: string; name: string }[];
  kpis: { id: string; name: string }[];
  pages: DashPageItem[];
  metrics?: { id: string; label: string }[];
  channels?: { slug: string; name: string }[];
}
// The card and page forms' named controls, as dashCardForm and dashPageForm draw them.
interface DashFormControls extends HTMLFormControlsCollection {
  namedItem(name: 'title'): HTMLInputElement;
  namedItem(name: 'template'): HTMLSelectElement;
  namedItem(name: 'size'): HTMLSelectElement;
  namedItem(name: string): RadioNodeList | Element | null;
}
type DashForm = HTMLFormElement & { elements: DashFormControls };

const dashApi = (place: string) => '/v2/places/' + encodeURIComponent(place);
// The place as it reads: the Growth page's for the month picked there.
const dashReadUrl = (state: DashState) => dashApi(state.place) + (state.month ? '?month=' + encodeURIComponent(state.month) : '');
const dashKey = (state: DashState) => state.place + (state.month ? '?month=' + state.month : '');
const dashTabsHTML = (on: string) => `<nav class="tabs slim strat-tabs dash-tabs" aria-label="Company">${
  [['mine', HOME, 'Mine'], ['dashboard', OVERVIEW, 'Dashboard'], ['campus', OVERVIEW_CAMPUS, 'Campus'], ['graph', OVERVIEW_GRAPH, 'Graph'], ['factories', OVERVIEW_FACTORIES, 'Factories'], ...(cpAllowed() ? [['company', OVERVIEW_COMPANY, 'Share']] : [])].map(([id, href, label]) =>
    `<a href="${href}"${on === id ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
const dashNum = (n: unknown) => n == null ? '–' : Number(n).toLocaleString(undefined, {maximumFractionDigits: 2});
const dashMoney = (n: unknown) => n == null ? '–' : '$' + Number(n).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
const dashBot = (slug: string | null | undefined) => typeof botDisplayName === 'function' ? botDisplayName(slug) : slug;

// ---- one card's body, from what the server says it shows this reader
function dashBars(weeks: DashWeek[]) {
  const max = Math.max(1, ...weeks.map(w => w.done)), n = weeks.length, gap = 2, w = (100 - gap * (n - 1)) / n;
  const bars = weeks.map((x, i) => {
    const h = Math.max(x.done ? 3 : 1, x.done / max * 56);
    return `<rect x="${(i * (w + gap)).toFixed(2)}" y="${(60 - h).toFixed(2)}" width="${w.toFixed(2)}" height="${h.toFixed(2)}" rx="1"${i === n - 1 ? ' class="now"' : ''}><title>Week of ${esc(x.week)}: ${x.done}</title></rect>`;
  }).join('');
  return `<svg class="dash-bars" viewBox="0 0 100 60" preserveAspectRatio="none" role="img" aria-label="Done each week">${bars}</svg>`;
}
function dashSpark(values: number[]) {
  if (values.length < 2) return '';
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1) * 100).toFixed(2)},${(36 - (v - lo) / span * 32).toFixed(2)}`).join(' ');
  return `<svg class="dash-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}
// Every task the reader may see, by status, the card's team only when it has one (ui/app/task-list.ts filters).
const dashTasksHref = (card: DashCard) => `${TASKS}?view=list&who=all${card.settings?.team ? '&team=' + encodeURIComponent(String(card.settings.team)) : ''}`;
function dashPageRow(p: DashPageItem) {
  return `<li><a href="#/page/${encodeURIComponent(p.file_id)}">${esc(p.title || 'Untitled')}</a>
    <span class="muted">${esc(dashBot(p.bot))} · ${agoSpan(p.updated_at)}</span></li>`;
}
// A thread's address: the threads page's own when this build has it (ui/app/threads*.js).
const dashThreadHref = (id: string) => typeof window.threadHref === 'function' ? window.threadHref(id) : '#/threads/' + encodeURIComponent(id);
// Needs you cards (needs_attention, needs_you): each row its title and the word for what it needs; the count is dashHeadN's.
const DASH_ACT: Record<string, string> = {done: 'Done', ok: 'OK', answer: 'Answer', reply: 'Reply', fix: 'Fix'};
// A count card's number sits on its header line, 13px muted (#355): the field that holds it, where it links, what it counts.
const DASH_HEAD_N: Record<string, [string, string, string]> = {
  needs_you: ['count', NEEDS_YOU, 'waiting on you'], needs_attention: ['count', NEEDS_YOU, 'waiting on you'],
  my_tasks: ['count', TASKS + '?view=list', 'open'], updates: ['unread', UPDATES, 'unread'],
  automation_runs: ['total', AUTOMATIONS, 'runs in 24 hours'],
};
function dashHeadCount(card: DashCard): [number, string, string] | null {
  const h = DASH_HEAD_N[card.template];
  if (!h || card.hidden || !card.data) return null;
  return [Number((card.data as Record<string, unknown>)[h[0]]) || 0, h[1], h[2]];
}
// The header count beside the card's <h2> (a sibling, so a long name never cuts it); hidden at 0.
function dashHeadN(card: DashCard) {
  const c = dashHeadCount(card);
  if (!c) return '';
  const [n, href, words] = c;
  return `<a class="dash-head-n" href="${href}" aria-label="${dashNum(n)} ${words}" title="${dashNum(n)} ${words}"${n ? '' : ' hidden'}>${dashNum(n)}</a>`;
}
function dashNeeds(d: DashCardData) {
  return d.items.length ? `<ul class="dash-list dash-needs">${d.items.map(it => `<li><a href="${it.task_id ? '#/task/' + encodeURIComponent(it.task_id) : NEEDS_YOU}"${it.line && it.line !== it.title ? ` title="${esc(it.line)}"` : ''}><span class="dash-needs-t">${esc(it.title || 'Untitled')}</span>${it.act && DASH_ACT[it.act] ? `<span class="muted">${DASH_ACT[it.act]}</span>` : ''}</a></li>`).join('')}</ul>` : `<p class="muted dash-none"><a href="${NEEDS_YOU}">Nothing waiting on you</a></p>`;
}
const dashDue = (due: string | null | undefined) => {
  if (!due) return '';
  const day = String(due).slice(0, 10), today = new Date().toISOString().slice(0, 10);
  return `<span class="${day < today ? 'dash-bad' : 'muted'}">${day < today ? 'Overdue' : 'Due'} ${esc(day.slice(5))}</span>`;
};
const DASH_BODY: Record<string, DashBody> = {
  // Only what the server counts (backend/cards.py tasks_by_status): no column without a number.
  tasks_by_status: (d, card) => `<div class="dash-stats">${([['open', 'Open'], ['done', 'Done this week']] as const)
    .map(([k, label]) => `<a class="dash-stat" href="${dashTasksHref(card)}"><strong>${dashNum(d.counts[k])}</strong><span>${label}</span></a>`).join('')}</div>`,
  done_per_week: d => {
    const weeks = d.weeks || [], last = weeks[weeks.length - 1]?.done ?? 0, total = weeks.reduce((s, w) => s + w.done, 0);
    return `<div class="dash-big"><strong>${dashNum(last)}</strong><span class="muted">this week · ${dashNum(total)} in ${weeks.length} weeks</span></div>${dashBars(weeks)}`;
  },
  needs_you: dashNeeds,
  needs_attention: dashNeeds,
  unread_threads: d => d.threads.length ? `<ul class="dash-list">${d.threads.map(t => `<li><a href="${esc(window.threadRowHref ? window.threadRowHref(t) : dashThreadHref(t.id))}">${esc(threadTitle(t))}</a>
      <span class="muted">${t.unread ? dashNum(t.unread) + ' new · ' : ''}${agoSpan(t.updated_at)}</span></li>`).join('')}</ul>` : '<p class="muted dash-none">Nothing unread</p>',
  my_tasks: d => d.tasks.length ? `<ul class="dash-list">${d.tasks.map(t => `<li><a href="#/task/${encodeURIComponent(t.id)}">${esc(t.title || 'Untitled')}</a> ${dashDue(t.due_at)}</li>`).join('')}</ul>`
    : '<p class="muted dash-none">No open tasks</p>',
  updates: d => `${d.updates.length ? `<ul class="dash-list">${d.updates.map(u => `<li><a href="${UPDATES}"${u.read ? '' : ' class="dash-unread"'}>${esc(u.headline || 'Update')}</a>
      <span class="muted">${esc(String(u.author || '').startsWith('human:') ? (S.people || []).find(p => 'human:' + p.id === u.author)?.name || '' : dashBot(u.author))} · ${agoSpan(u.created_at)}</span></li>`).join('')}</ul>` : '<p class="muted dash-none">No updates</p>'}`,
  spend: d => {
    const pct = d.budget ? Math.min(100, d.spent / d.budget * 100) : 0;
    return `<div class="dash-big"><strong>${dashMoney(d.spent)}</strong><span class="muted">${d.budget ? 'of ' + dashMoney(d.budget) : 'no budget'} · ${esc(d.month)}</span></div>
      ${d.budget ? `<div class="dash-meter${pct >= 90 ? ' hot' : ''}" role="meter" aria-valuemin="0" aria-valuemax="${d.budget}" aria-valuenow="${d.spent}" aria-label="Spend"><span style="width:${pct.toFixed(1)}%"></span></div>` : ''}`;
  },
  kpi: d => `<div class="dash-big"><strong>${dashNum(d.value)}${d.unit ? ` <small>${esc(d.unit)}</small>` : ''}</strong>
    <span class="muted">${esc(d.name)}${d.at ? ' · ' + esc(d.at) : ''}</span></div>${dashSpark(d.spark || [])}`,
  automation_runs: d => `${d.failed || d.running ? `<p class="muted dash-sub">${[d.failed ? `<span class="dash-bad">${d.failed} failed</span>` : '', d.running ? `${d.running} running` : ''].filter(Boolean).join(' · ')}</p>` : ''}
    ${d.runs.length ? `<ul class="dash-list">${d.runs.map(r => `<li><span class="dash-dot ${r.status === 'failed' ? 'bad' : r.status === 'succeeded' || r.status === 'done' ? 'ok' : ''}" aria-hidden="true"></span>${esc(r.name)} <span class="muted">${agoSpan(r.started_at)}</span></li>`).join('')}</ul>` : ''}`,
  latest_pages: d => d.pages.length ? `<ul class="dash-list dash-pages">${d.pages.map(dashPageRow).join('')}</ul>` : '<p class="muted dash-none">No pages yet</p>',
};
// What a built-in card shows (its template's fields).
const dashData = (card: DashCard) => card.data as DashCardData;
function dashCardBody(card: DashCard) {
  if (card.hidden || !card.data) return DASH_NONE[card.template]?.(card) ?? `<p class="muted dash-none">${esc(DASH_EMPTY[card.template] || 'Not shown to you')}</p>`;
  return (DASH_BODY[card.template] || (() => ''))(dashData(card), card);
}
// A page pin: the page itself through pageCard (ui/app/pages.ts) when this build has it, else a link to it.
function dashPageEl(page: DashPageItem, size: string | undefined) {
  if (typeof window.pageCard === 'function') {
    try { return window.pageCard(page, {size}); } catch { /* the link below */ }
  }
  const a = document.createElement('a');
  a.className = 'dash-page-link'; a.href = '#/page/' + encodeURIComponent(page.file_id);
  a.innerHTML = `<span class="nav-icon" aria-hidden="true">article</span><span><strong>${esc(page.title || 'Untitled')}</strong>
    <span class="muted">${esc(dashBot(page.bot))} · Updated ${agoSpan(page.updated_at)}</span></span>`;
  return a;
}
// A card names its team, except on that team's own page.
const dashTeamShown = (card: DashCard, view?: DashView | null) => card.team_name && !(view?.kind === 'team' && card.settings?.team === view.group) ? card.team_name : '';
const dashHeading = (card: DashCard) => (!card.title && DASH_HEADING[card.template]?.(card)) || card.heading;
const dashPinTitle = (pin: { card?: DashCard | undefined; page?: { title?: string | null } | undefined }, view?: DashView | null) => pin.card ? dashHeading(pin.card) + (dashTeamShown(pin.card, view) ? ' · ' + pin.card.team_name : '') : pin.page?.title || 'Page';
// Who sees a team page, in a few words: Everyone, the one team or person, else how many.
function dashAudienceWords(aud: import('../types/api').PlaceAudience | undefined) {
  if (!aud || aud.everyone !== false) return 'Everyone';
  const teams = aud.teams || [], people = aud.people || [], bots = aud.bots || [];
  const groupName = (id: string | undefined) => (S.orgGroups || []).find(g => g.id === id)?.name || id;
  const personName = (id: string | undefined) => (S.people || []).find(p => p.id === id)?.name || id;
  if (teams.length === 1 && !people.length && !bots.length) return groupName(teams[0]);
  if (people.length === 1 && !teams.length && !bots.length) return personName(people[0]);
  const parts = [[teams.length, 'team'], [people.length, 'person'], [bots.length, 'bot']].filter(([n]) => n)
    .map(([n, w]) => `${n} ${n === 1 ? w : w === 'person' ? 'people' : w + 's'}`);
  return parts.join(', ') || 'Nobody';
}

// ---- the page
// A place seen before is drawn at once from what it showed last (S.lastRead.places), then read again; the grid is drawn
// again only when the answer differs, so its page widgets are not reloaded for nothing.
// Startup asks for My dashboard (or the company's) beside the roster when it is the page the app opens on, a round
// trip sooner (boot.js); pageDashboard's read then takes that answer (api.js getPrefetch).
function dashPrefetch() {
  const place = location.hash === HOME ? 'person:me' : location.hash === OVERVIEW ? 'dashboard' : '';
  if (place) getPrefetch(dashApi(place));
}
async function pageDashboard(place: string, {month = ''}: {month?: string} = {}) {
  const team = place.startsWith('team:'), own = DASH_PLACES[place];
  const state: DashState = {place, month, view: null, choices: null, root: null, menu: null, editing: false, readAt: Date.now(), feed: taskFeedNew()};
  const key = state.key = dashKey(state), kept = (S.lastRead.places ||= {})[key];
  const main = $('#main'); main.classList.add('dash-layout');
  const head = own ? own.head(state) : `<h1>${team ? '' : 'Company'}</h1>${team ? '' : dashTabsHTML(place === 'person:me' ? 'mine' : 'dashboard')}<span class="spacer"></span>`;
  main.innerHTML = `<div class="dash" id="dash-root"><header class="dash-head">${head}
      <span class="dash-actions" hidden>${team ? '<button type="button" class="ghost dash-audience" data-dash-audience></button>' : ''}
        <button type="button" class="ghost" data-dash-add="card" hidden><span class="dash-plus" aria-hidden="true">+</span>Card</button>
        ${!own || own.pages() ? '<button type="button" class="ghost" data-dash-add="page" hidden><span class="dash-plus" aria-hidden="true">+</span>Page</button>' : ''}
        <button type="button" class="ghost" data-dash-mode="edit">Edit</button><button type="button" class="primary" data-dash-mode="done" hidden>Done</button></span></header>
    <div class="dash-grid" id="dash-grid">${kept ? '' : skelRows(4, {card: true})}</div>
    ${team ? '<section class="card home-card dash-home" id="dash-home"><header><h2>Home</h2><div class="home-card-acts" id="dash-home-acts"></div></header><div id="dash-home-thread"></div></section>' : ''}
    <dialog class="tmodal tmodal-shell dash-dialog" id="dash-dialog" aria-label="Card"></dialog></div>`;
  DASH = state; state.root = $('#dash-root');
  // A team's Home: the thread everyone who can see the team reads.
  if (team) threadMount($('#dash-home-thread'), {home: 'group:' + place.slice(5), card: true, header: 'compact', actsHost: $('#dash-home-acts'), hideGone: $('#dash-home'), compactEmpty: true});
  dashWire(state);
  own?.wire?.(state);
  if (kept) { state.view = kept; dashDraw(state); }
  let view: DashView;
  const ticket = taskFeedRead(state.feed);
  try { view = await get<DashView>(dashReadUrl(state)); }
  catch (e) {
    taskFeedFailed(state.feed, ticket);
    if (DASH !== state) return;
    if (kept) delete S.lastRead.places[key];
    $('#dash-grid').innerHTML = `<p class="empty">${esc(!team ? errorMessage(e, '') : errorStatus(e) === 404 ? 'No such team' : errorStatus(e) === 403 ? 'This team page is not shared with you' : errorMessage(e, ''))}</p>`;
    return;
  }
  if (!Array.isArray(view?.pins)) view = {kind: place.split(':')[0], pins: [], can_edit: false, saved: true};
  // A read that lands after the page was left is kept for the next visit when nothing is kept yet.
  if (DASH !== state) { S.lastRead.places[key] ||= view; return; }
  // The month moved while the read was out: that read draws.
  const held = taskFeedLanded(state.feed, ticket);
  if (dashKey(state) !== key || !held) return;
  // A write made while the read was out already drew its own answer.
  if (state.view !== kept && state.view) return;
  if (kept && sameData(view, kept)) return;
  state.view = view;
  dashDraw(state);
  dashTasksPut(held);
}
// Read the place again and draw it (the Growth page after its month or its settings change). The newest read wins.
async function dashRefresh(state: DashState) {
  const seq = state.refreshSeq = (state.refreshSeq || 0) + 1, ticket = taskFeedRead(state.feed);
  try {
    const view = await get<DashView>(dashReadUrl(state), {fresh: true});
    const held = DASH === state && state.refreshSeq === seq ? taskFeedLanded(state.feed, ticket) : null;
    if (!held || !Array.isArray(view?.pins)) { taskFeedFailed(state.feed, ticket); return; }
    state.view = view;
    dashDraw(state);
    dashTasksPut(held);
  } catch (e) { taskFeedFailed(state.feed, ticket); if (DASH === state && state.refreshSeq === seq) dashToast(errorMessage(e, '')); }
}
// "Seen by" is the group's Read (backend/teammate_access.py): it opens the group's Access editor, for its leads
// and the admins (the same people who arrange the page), and the page is read again after a save.
function dashAccessEdit(state: DashState) {
  const gid = state.place.slice(5);
  return teammateEditAccess('group', gid, state.view?.name || teamLabel(gid), async () => {
    const view = await get<DashView>(dashApi(state.place), {fresh: true}).catch(() => null);
    if (view && DASH === state) { state.view = view; dashDraw(state); }
  });
}
// My dashboard follows what its cards show from the live events themselves (Nov 3: nothing is read because an event
// came): My tasks from the reader's task events, Needs you from the list the app holds, Unread threads from read and
// activity events on its rows. What no event can settle (a count over tasks not shown, Done this week, a thread not
// listed) waits for the safety read: every 2 minutes while on screen, or every 5 with the live stream up. It is drawn
// again only when the answer differs, and not while a menu or dialog is open or a card is being moved.
const DASH_POLL_MS = 120e3, DASH_LIVE_POLL_MS = 300e3;
// Each of My dashboard's cards of a template, with its pin's place on the grid: `patch` gives its new data, or null for
// no change; a changed card's body alone is drawn again.
function dashCardsPatch(template: string, patch: (d: DashCardData, card: DashCard) => DashCardData | null): void {
  const state = DASH, v = state?.view;
  if (!state || !v || state.place !== 'person:me' || !state.root) return;
  v.pins.forEach((pin, i) => {
    const card = pin.card;
    if (!card || card.template !== template || card.hidden || !card.data) return;
    const data = patch(dashData(card), card);
    if (!data) return;
    const next = {...card, data};
    v.pins[i] = {...pin, card: next};
    const body = state.root?.querySelector(`.dash-pin[data-at="${i}"] .dash-pin-body`);
    if (body) body.innerHTML = dashCardBody(next);
    const n = state.root?.querySelector(`.dash-pin[data-at="${i}"] .dash-head-n`);
    if (n) n.outerHTML = dashHeadN(next).trim();
  });
}
// My tasks (backend/cards.py my_tasks): the open tasks the reader owns, the soonest due first, the first N and how many.
// A shown task changes in place or leaves (one fewer); a new one of theirs joins (one more). A task not shown that is now
// theirs and open joins where it sorts; it is one more only when the card showed all of them, since a task event carries
// no earlier owner or status.
type DashTaskRow = DashTask & {updated_at?: string | null};
const dashTaskOrder = (a: DashTaskRow, b: DashTaskRow) => (a.due_at ? 0 : 1) - (b.due_at ? 0 : 1)
  || String(a.due_at || '').localeCompare(String(b.due_at || '')) || String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
  || a.id.localeCompare(b.id);
function dashTasksLive(events: LiveTaskEvent[]): void {
  if (DASH?.place !== 'person:me' || !events.length) return;
  dashTasksPut(taskFeedEvents(DASH.feed, events));
}
function dashTasksPut(events: LiveTaskEvent[]): void {
  if (DASH?.place !== 'person:me' || !events.length) return;
  const me = myActor();
  dashCardsPatch('my_tasks', (d, card) => {
    const n = Number(card.settings?.count) || 5;
    let rows: DashTaskRow[] = [...(d.tasks || [])], count = Number(d.count) || 0, moved = false;
    const all = count <= rows.length;                           // the card shows every one of them
    for (const ev of events) {
      if (ev.bulk) continue;                                    // many at once: the safety read
      const id = taskEventId(ev), t = taskOfEvent<TaskLiveRow & {owner_id?: unknown; status?: unknown; title?: string | null; due_at?: string | null; updated_at?: string | null}>(ev);
      const at = rows.findIndex(r => r.id === id), mine = !!t && t.owner_id === me && t.status === 'open';
      const inserted = Array.isArray(ev.kinds) && ev.kinds.includes('insert');
      if (at >= 0 && !mine) { rows.splice(at, 1); count = Math.max(0, count - 1); moved = true; continue; }
      if (!t || !mine) continue;
      const row: DashTaskRow = {id, title: t.title ?? null, due_at: t.due_at ?? null, updated_at: t.updated_at ?? null};
      if (at >= 0) rows[at] = row;
      else {
        if (inserted || all) count++;
        rows.push(row);
      }
      moved = true;
    }
    if (!moved) return null;
    rows = rows.sort(dashTaskOrder).slice(0, n);
    return {...d, tasks: rows, count};
  });
}
// Needs you (needs_attention, and needs_you without a team): the list the app holds (S.v2.needs, in the server's order)
// and its count, each item by the server's rule (backend/cards.py needs_brief): its task's title, a Paused cause or an
// item without a task by its line, with the line as its tooltip and the word for what it needs. A team's card counts the items its bots are behind, which
// the held items do not say: the safety read.
const dashAct = (it: NeedsEntry) => ({ok: 'ok', question: 'answer', yours: 'done', stuck: 'fix', fix: 'fix', mention: 'reply'} as Record<string, string>)[decideKind(it)] || '';
function dashNeedsLive(): void {
  const items = S.v2?.needs || [], count = S.v2?.needsCount ?? items.length;
  const brief = (n: number): DashNeedsItem[] => items.slice(0, n).map(it => ({title: (it.kind !== 'paused' && it.task?.title) || it.line || '', line: it.line || '', act: dashAct(it),
    task_id: (it.task_id as string | undefined) || (it.kind === 'task' || it.kind === 'ask' ? String(it.id) : null)}));
  for (const [template, n] of [['needs_attention', 5], ['needs_you', 3]] as const)
    dashCardsPatch(template, (d, card) => {
      if (card.settings?.team) return null;
      const next = {...d, count, items: brief(n)};
      return sameData(next, d) ? null : next;
    });
}
// Unread threads: the reader's own read takes its row off once Threads (threads.ts threadsReadLive) has the root quiet,
// and sets its count while some is left (a read of one sub-thread, or up to a mark short of the newest); a root Threads
// does not hold keeps its row until the safety read. A new unread message on a listed thread counts on its row. A
// thread not listed waits for the safety read (an activity event carries no title or audience to draw it with).
function dashThreadsLive(d: ThreadsEvent): void {
  const root = d.root_id || d.thread_id;
  if (!root || (d.kind !== 'read' && d.kind !== 'activity' && !d.gone)) return;
  // Threads takes the same event after this list: its row says what the read left.
  if (d.kind === 'read') { queueMicrotask(() => dashThreadsRead(root)); return; }
  dashCardsPatch('unread_threads', data => {
    const rows = data.threads || [], at = rows.findIndex(t => t.id === root);
    if (at < 0) return null;
    if (d.gone) return {...data, threads: rows.filter((_, i) => i !== at), count: Math.max(0, (Number(data.count) || 0) - 1)};
    if (d.unread !== true) return null;
    const held = rows[at]!;
    const row = {...held, unread: (Number(held.unread) || 0) + Math.max(1, Number(d.unread_count) || 1), updated_at: new Date().toISOString()};
    return {...data, threads: [row, ...rows.filter((_, i) => i !== at)]};
  });
}
function dashThreadsRead(root: string): void {
  const rail = typeof threadsRow === 'function' ? threadsRow(root) : null;
  if (!rail) return;
  const left = threadUnread(rail);
  dashCardsPatch('unread_threads', data => {
    const rows = data.threads || [], held = rows.find(t => t.id === root);
    if (!held) return null;
    if (!left) return {...data, threads: rows.filter(t => t !== held), count: Math.max(0, (Number(data.count) || 0) - 1)};
    return Number(held.unread) === left ? null : {...data, threads: rows.map(t => t === held ? {...t, unread: left} : t)};
  });
}
async function dashReread(state: DashState | null) {
  if (!state || DASH !== state || state.place !== 'person:me' || state.reading) return;
  if (!state.root) return;
  if (state.menu || state.root.querySelector<HTMLDialogElement>('#dash-dialog')?.open || state.root.querySelector('.dash-pin.dragging')) return;
  state.reading = true;
  const ticket = taskFeedRead(state.feed);
  try {
    const view = await get<DashView>(dashApi(state.place), {fresh: true});
    // What came while the read was out was drawn already and goes over the answer again.
    const held = DASH === state ? taskFeedLanded(state.feed, ticket) : null;
    if (!held || !Array.isArray(view?.pins) || sameData(view, state.view)) return;
    state.view = view;
    dashDraw(state);
    dashTasksPut(held);
  } catch (e) { taskFeedFailed(state.feed, ticket); /* the next change or tick reads again */ }
  finally { state.reading = false; state.readAt = Date.now(); }
}
setInterval(() => {
  const state = DASH;
  if (!state || document.hidden || !state.view) return;
  const every = typeof liveConnected === 'function' && liveConnected() ? DASH_LIVE_POLL_MS : DASH_POLL_MS;
  if (Date.now() - (state.readAt || 0) >= every) void dashReread(state);
}, 30e3);
// Edit mode on or off: the grips and ⋯ menus (dashboard.css, .dash-editing) and the header's buttons. A redraw keeps it.
function dashEditing(state: DashState, on: boolean, {focus = false}: {focus?: boolean} = {}) {
  const root = state.root;
  state.editing = on = !!(on && state.view?.can_edit);
  if (!on) dashMenuClose(state);
  if (!root) return;
  root.classList.toggle('dash-editing', on);
  root.querySelectorAll<HTMLElement>('[data-dash-add], [data-dash-mode="done"]').forEach(b => { b.hidden = !on; });
  const edit = root.querySelector<HTMLElement>('[data-dash-mode="edit"]');
  if (!edit) return;
  edit.hidden = on;
  if (focus) root.querySelector<HTMLElement>(`[data-dash-mode="${on ? 'done' : 'edit'}"]`)?.focus();
}
// Escape leaves edit mode, unless a menu or a dialog has it, or it is typed into a field.
document.addEventListener('keydown', ev => {
  const state = DASH;
  if (ev.key !== 'Escape' || ev.defaultPrevented || !state?.editing || state.menu || document.querySelector('dialog[open]')) return;
  if (eventElement(ev)?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
  if (!state.root) return;
  dashEditing(state, false, {focus: state.root.contains(document.activeElement)});
});
function dashDraw(state: DashState) {
  const v = state.view, root = state.root;
  if (!v || !root || !S.lastRead.places) return;
  S.lastRead.places[String(state.key)] = v;
  if (v.kind === 'team') { const h1 = root.querySelector('h1'); if (!h1) return; h1.textContent = v.name ?? ''; }
  const actions = root.querySelector<HTMLElement>('.dash-actions');
  if (!actions) return;
  actions.hidden = !v.can_edit;
  dashEditing(state, state.editing);
  const aud = root.querySelector('[data-dash-audience]');
  if (aud) {
    aud.innerHTML = `<span class="nav-icon" aria-hidden="true">groups</span>Seen by ${esc(dashAudienceWords(v.audience))}`;
    aud.setAttribute('aria-haspopup', 'dialog');
  }
  const grid = root.querySelector('#dash-grid');
  if (!grid) return;
  grid.innerHTML = v.pins.length ? '' : `<p class="empty">${v.can_edit ? 'Add a card or a page' : 'Nothing pinned'}</p>`;
  v.pins.forEach((pin, i) => {
    const el = document.createElement('article');
    el.className = 'dash-pin' + (pin.card ? '' : ' dash-pin-page'); el.dataset.size = pin.size || 'medium'; el.dataset.at = String(i);
    el.innerHTML = `<header class="dash-pin-head">
        ${v.can_edit ? `<button type="button" class="dash-grip" data-dash-grip aria-label="Move ${esc(dashPinTitle(pin, v))}" title="Move">${BOT_SVG.grip}</button>` : ''}
        <h2>${pin.card ? esc(dashHeading(pin.card)) : ''}${pin.card && dashTeamShown(pin.card, v) ? ` <span class="muted">· ${esc(pin.card.team_name)}</span>` : ''}</h2>${pin.card ? dashHeadN(pin.card) : ''}
        <span class="spacer"></span>
        ${v.can_edit ? `<button type="button" class="dash-more" data-dash-menu aria-haspopup="menu" aria-expanded="false" aria-label="${esc(dashPinTitle(pin, v))} menu" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>` : ''}</header>
      <div class="dash-pin-body"></div>`;
    const body = el.querySelector('.dash-pin-body');
    if (!body) return;
    if (pin.card) body.innerHTML = dashCardBody(pin.card);
    else body.append(dashPageEl(pin.page, pin.size));
    grid.append(el);
  });
  DASH_PLACES[state.place]?.drawn?.(state);
}
// A place nobody saved shows its default cards without ids; the first change saves them, then finds the pin again.
async function dashSaved(state: DashState): Promise<DashView> {
  // A change is made only on a place already drawn.
  const shown = state.view!;
  if (shown.saved) return shown;
  const view: DashView = await put(dashApi(state.place), {});
  if (DASH === state) state.view = view;
  return view;
}
async function dashWrite(state: DashState, work: () => Promise<DashView | null | undefined>) {
  try {
    const view = await work();
    if (DASH !== state) return;
    // An answer for another month than the one picked (a write's) is read again for it, the newest read winning.
    if (!view?.pins || (state.month && view.month !== state.month)) return void await dashRefresh(state);
    state.refreshSeq = (state.refreshSeq || 0) + 1;   // a read still out is older than this answer
    state.view = view;
    dashDraw(state);
  } catch (e) { if (DASH === state) dashToast(errorMessage(e, '')); }
}
function dashToast(text: string) {
  const grid = $('#dash-grid'); if (!grid) return;
  let el = $('#dash-error');
  if (!el) { el = document.createElement('p'); el.id = 'dash-error'; el.className = 'err dash-error'; el.setAttribute('role', 'alert'); grid.before(el); }
  el.textContent = text;
}
const dashPinAt = (state: DashState, at: number) => dashSaved(state).then(view => view.pins[at]);

// ---- menu, drag and keys
function dashMenuClose(state: DashState) { state.menu?.remove(); state.menu = null; if (!state.root) return; state.root.querySelector('[data-dash-menu][aria-expanded="true"]')?.setAttribute('aria-expanded', 'false'); }
function dashMenuOpen(state: DashState, button: HTMLElement) {
  const pinEl = button.closest<HTMLElement>('.dash-pin');
  if (!pinEl || !state.view) return;
  const at = Number(pinEl.dataset.at), pin = state.view.pins[at];
  dashMenuClose(state);
  if (!pin) return;
  const menu = document.createElement('div');
  menu.className = 'dash-menu'; menu.setAttribute('role', 'menu');
  menu.innerHTML = `${pin.card ? '<button type="button" role="menuitem" data-dash-edit>Edit</button>' : ''}
    ${DASH_SIZES.map(([id, label]) => `<button type="button" role="menuitemradio" aria-checked="${pin.size === id}" data-dash-size="${id}">${label}</button>`).join('')}
    <button type="button" role="menuitem" data-dash-remove class="danger">Remove</button>`;
  const head = pinEl.querySelector('.dash-pin-head');
  if (!head) return;
  head.append(menu);
  button.setAttribute('aria-expanded', 'true');
  state.menu = menu;
  menu.querySelector('button')?.focus();
  menu.addEventListener('click', ev => {
    const b = eventElement(ev)?.closest('button'); if (!b) return;
    dashMenuClose(state);
    if (b.hasAttribute('data-dash-edit')) return void dashCardForm(state, at);
    if (b.dataset.dashSize) return void dashWrite(state, async () => { const target = await dashPinAt(state, at); if (!target) return null; return patch(`/v2/pins/${encodeURIComponent(String(target.id))}`, {size: b.dataset.dashSize}); });
    if (b.hasAttribute('data-dash-remove')) return void dashWrite(state, async () => { const target = await dashPinAt(state, at); if (!target) return null; return writeRequest('DELETE', `/v2/pins/${encodeURIComponent(String(target.id))}`); });
  });
  menu.addEventListener('keydown', ev => {
    const items = [...menu.querySelectorAll('button')], i = document.activeElement instanceof HTMLButtonElement ? items.indexOf(document.activeElement) : -1;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); items[(i + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus(); }
    if (ev.key === 'Escape') { ev.preventDefault(); dashMenuClose(state); button.focus(); }
  });
}
// The new order goes to the server as the pins' ids; until then the grid shows it as dropped.
function dashOrder(state: DashState, order: number[]) {
  void dashWrite(state, async () => {
    const view = await dashSaved(state);
    return post(dashApi(state.place) + '/order', {ids: order.map(at => view.pins[at]!.id)});
  });
}
function dashSortable(state: DashState, grid: HTMLElement | null) {
  if (!grid) return;
  grid.addEventListener('pointerdown', ev => {
    const grip = eventElement(ev)?.closest('[data-dash-grip]');
    if (!grip || ev.button) return;
    const item = grip.closest('.dash-pin');
    if (!item) return;
    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    const move = (e: PointerEvent) => {
      if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) < 5) return;
      if (!moved) { moved = true; item.classList.add('dragging'); }
      e.preventDefault();
      const over = [...grid.children].find(el => el !== item && el.matches('.dash-pin') && (r => e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom)(el.getBoundingClientRect()));
      if (!over) return;
      const r = over.getBoundingClientRect(), after = r.width > r.height * 1.5 ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
      grid.insertBefore(item, after ? over.nextElementSibling : over);
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);
      if (!moved) return;
      item.classList.remove('dragging');
      const order = [...grid.querySelectorAll<HTMLElement>(':scope > .dash-pin')].map(el => Number(el.dataset.at));
      if (order.some((at, i) => at !== i)) dashOrder(state, order);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
  });
}
function dashWire(state: DashState) {
  const root = state.root;
  if (!root) return;
  dashSortable(state, root.querySelector<HTMLElement>('#dash-grid'));
  root.addEventListener('click', ev => {
    const more = eventElement(ev)?.closest<HTMLElement>('[data-dash-menu]');
    if (more) return more.getAttribute('aria-expanded') === 'true' ? dashMenuClose(state) : dashMenuOpen(state, more);
    if (state.menu && !eventElement(ev)?.closest('.dash-menu')) dashMenuClose(state);
    const mode = eventElement(ev)?.closest<HTMLElement>('[data-dash-mode]');
    if (mode) return void dashEditing(state, mode.dataset.dashMode === 'edit', {focus: true});
    const add = eventElement(ev)?.closest<HTMLElement>('[data-dash-add]');
    if (add) return void (add.dataset.dashAdd === 'card' ? dashCardForm(state, -1) : dashPageForm(state));
    if (eventElement(ev)?.closest('[data-dash-audience]')) return void dashAccessEdit(state);
  });
  root.addEventListener('keydown', ev => {
    const grip = eventElement(ev)?.closest('[data-dash-grip]'); if (!grip) return;
    const by = ({ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1} as Record<string, number | undefined>)[ev.key]; if (!by) return;
    ev.preventDefault();
    const pinEl = grip.closest<HTMLElement>('.dash-pin');
    if (!pinEl || !state.view) return;
    const at = Number(pinEl.dataset.at), to = at + by, n = state.view.pins.length;
    if (to < 0 || to >= n) return;
    const order = [...Array(n).keys()];
    [order[at], order[to]] = [order[to]!, order[at]!];
    void dashWrite(state, async () => {
      const view = await dashSaved(state);
      return post(dashApi(state.place) + '/order', {ids: order.map(i => view.pins[i]!.id)});
    }).then(() => DASH === state && $(`#dash-grid > .dash-pin[data-at="${to}"] [data-dash-grip]`)?.focus());
  });
}

// ---- forms: a card's settings, a page to pin
// The cards a place offers: the Growth page's own templates only there (?place=growth).
// Bumped when what the card form offers changes (a Growth channel added or retired): the next form reads it again.
let DASH_CHOICES_AT = 0;
const dashChoicesStale = () => { DASH_CHOICES_AT += 1; };
async function dashChoices(state: DashState, team: string | undefined = ''): Promise<DashChoices> {
  const query = new URLSearchParams();
  if (team) query.set('team', team);
  if (DASH_PLACES[state.place]) query.set('place', state.place);
  const text = query.toString(), key = 'c:' + text;
  if (!state.choicesBy || state.choicesAt !== DASH_CHOICES_AT) state.choicesBy = {}, state.choicesAt = DASH_CHOICES_AT;
  return state.choicesBy[key] ||= get<DashChoices>('/v2/cards/choices' + (text ? '?' + text : ''));
}
const dashTeamOptions = (teams: DashChoices['teams'], value: unknown) => `<option value="">Company</option>${teams.map(t => `<option value="${esc(t.id)}"${t.id === value ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}`;
// count: [label, max, default] by template; the rest count pages.
const DASH_COUNT: Record<string, [string, number, number]> = {unread_threads: ['Threads', 10, 5], my_tasks: ['Tasks', 10, 5], updates: ['Updates', 10, 5], growth_channels: ['Channels', 20, 10], growth_board: ['Tasks per lane', 5, 3]};
const dashOptions = (pairs: [string, string][], value: unknown) => pairs.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('');
function dashFieldsHTML(template: string, choices: DashChoices, settings: Record<string, unknown>) {
  const fields = choices.templates.find(t => t.id === template)?.fields || [];
  const [countLabel, countMax, countDefault] = DASH_COUNT[template] || ['Pages', 10, 5];
  return fields.map(f => f === 'team' ? `<label>Team<select name="team">${dashTeamOptions(choices.teams, settings.team || '')}</select></label>`
    : f === 'weeks' ? `<label>Weeks<input type="number" name="weeks" min="4" max="26" value="${esc(settings.weeks ?? 8)}" required></label>`
    : f === 'count' ? `<label>${countLabel}<input type="number" name="count" min="1" max="${countMax}" value="${esc(settings.count ?? countDefault)}" required></label>`
    : f === 'months' ? `<label>Months<input type="number" name="months" min="3" max="12" value="${esc(settings.months ?? 6)}" required></label>`
    : f === 'metric' ? `<label>Number<select name="metric" required>${dashOptions((choices.metrics || []).map(m => [m.id, m.label]), settings.metric || '')}</select></label>`
    : f === 'channel' ? `<label>Channel<select name="channel"><option value="">All channels</option>${dashOptions((choices.channels || []).map(c => [c.slug, c.name]), settings.channel || '')}</select></label>`
    : f === 'kpi' ? `<label>KPI<select name="kpi" required>${choices.kpis.length ? choices.kpis.map(k => `<option value="${esc(k.id)}"${k.id === settings.kpi ? ' selected' : ''}>${esc(k.name)}</option>`).join('') : '<option value="">No KPIs</option>'}</select></label>`
    : '').join('');
}
// A card's title field suggests the name it shows without one: a Number's is its metric's.
function dashTitleHint(form: DashForm, choices: DashChoices, template: string) {
  const metric = template === 'growth_number' && form.querySelector<HTMLSelectElement>('[name="metric"]');
  form.elements.namedItem('title').placeholder = metric ? (choices.metrics || []).find(m => m.id === metric.value)?.label || ''
    : choices.templates.find(t => t.id === template)?.name || '';
}
function dashSettingsOf(form: ParentNode) {
  const out: Record<string, string | number> = {};
  for (const el of form.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[name]')) {
    if (['title', 'template', 'size'].includes(el.name)) continue;
    out[el.name] = el.type === 'number' ? Number(el.value) : el.value;
  }
  return out;
}
function dashDialog(state: DashState, title: string, inner: string, submit: (form: DashForm) => Promise<unknown>): DashForm | null {
  if (!state.root) return null;
  const dlg = state.root.querySelector<HTMLDialogElement>('#dash-dialog');
  if (!dlg) return null;
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<form><header class="tmodal-head"><h2>${esc(title)}</h2><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
    <div class="bot-editor-body dash-form">${inner}</div>
    <footer class="dash-form-foot"><span class="err" role="status" data-dash-status></span><span class="spacer"></span>
      <button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">Save</button></footer></form>`;
  const form = dlg.querySelector('form') as DashForm | null;
  dlg.querySelectorAll<HTMLElement>('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  if (!form) return null;
  form.onsubmit = async ev => {
    ev.preventDefault();
    const status = form.querySelector('[data-dash-status]'), button = form.querySelector<HTMLButtonElement>('[type="submit"]');
    if (!status || !button) return;
    button.disabled = true; status.textContent = '';
    try { await submit(form); if (dlg.open) dlg.close(); }
    catch (e) { status.textContent = errorMessage(e, ''); }
    finally { button.disabled = false; }
  };
  if (!dlg.open) dlg.showModal();
  return form;
}
// A new card's size: a Number is a tile, the Funnel and Channels want the row; the rest a half.
const DASH_SIZE: Record<string, string> = {growth_number: 'small', growth_funnel: 'large', growth_channels: 'large'};
const dashSizeOf = (template: string) => DASH_SIZE[template] || 'medium';
// at < 0: a new card for this place; else the card pinned at that position.
async function dashCardForm(state: DashState, at: number) {
  let choices: DashChoices;
  try { choices = await dashChoices(state); } catch (e) { return dashToast(errorMessage(e, '')); }
  if (DASH !== state || !state.view) return;
  const card = at >= 0 ? state.view.pins[at]!.card : null;
  const team = state.view.kind === 'team' ? state.view.group : '';
  const settings = card ? card.settings : {team};
  // A new card starts on the place's own first template (a Number on the Growth page), else the first offered.
  const first = DASH_PLACES[state.place]?.first, template = card ? card.template : (choices.templates.find(t => t.id === first) || choices.templates[0]).id;
  const form = dashDialog(state, card ? dashHeading(card) : 'Add card', `
    ${card ? '' : `<label>Card<select name="template">${dashOptions(choices.templates.map(t => [t.id, t.name]), template)}</select></label>`}
    <label>Title<input name="title" maxlength="80" value="${esc(card?.title || '')}"></label>
    <div class="dash-fields">${dashFieldsHTML(template, choices, settings)}</div>
    ${card ? '' : `<label>Size<select name="size">${DASH_SIZES.map(([id, label]) => `<option value="${id}"${id === dashSizeOf(template) ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`}`,
  async f => {
    const el = f.elements, body = {title: el.namedItem('title').value.trim(), settings: dashSettingsOf(f)};
    if (card) {
      await dashWrite(state, async (): Promise<undefined> => {
        const pin = await dashPinAt(state, at);
        if (!pin?.card) return;
        await patch(`/v2/cards/${encodeURIComponent(String(pin.card.id))}`, body);
      });
    } else {
      await post('/v2/cards', {...body, template: el.namedItem('template').value, place: state.place, size: el.namedItem('size').value});
      await dashWrite(state, async () => null);
    }
  });
  if (!form) return;
  const pick = form.querySelector<HTMLSelectElement>('[name="template"]'), chosen = () => pick ? pick.value : template;
  if (pick) pick.onchange = () => {
    const fields = form.querySelector('.dash-fields');
    if (!fields) return;
    fields.innerHTML = dashFieldsHTML(pick.value, choices, settings);
    form.elements.namedItem('size').value = dashSizeOf(pick.value);
    dashTitleHint(form, choices, pick.value);
  };
  const box = form.querySelector('.dash-fields');
  if (!box) return;
  box.addEventListener('change', ev => { if (ev.target instanceof HTMLSelectElement && ev.target.name === 'metric') dashTitleHint(form, choices, chosen()); });
  dashTitleHint(form, choices, template);
}
async function dashPageForm(state: DashState) {
  let choices: DashChoices;
  if (!state.view) return;
  const team = state.view.kind === 'team' ? state.view.group : '';
  try { choices = await dashChoices(state, team); } catch (e) { return dashToast(errorMessage(e, '')); }
  if (DASH !== state) return;
  const pinned = new Set(state.view.pins.map(p => p.page?.file_id).filter(Boolean));
  const pages = choices.pages.filter(p => !pinned.has(p.file_id));
  dashDialog(state, 'Add page', pages.length
    ? `<fieldset class="dash-pick"><legend class="sr-only">Page</legend>${pages.map((p, i) => `<label class="dash-pick-row"><input type="radio" name="page" value="${esc(p.file_id)}"${i ? '' : ' checked'} required>
        <span><strong>${esc(p.title || 'Untitled')}</strong> <span class="muted">${esc(dashBot(p.bot))} · ${agoSpan(p.updated_at)}</span></span></label>`).join('')}</fieldset>
      <label>Size<select name="size">${DASH_SIZES.map(([id, label]) => `<option value="${id}"${id === 'medium' ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`
    : '<p class="muted">No pages to add</p>',
  async f => {
    const value = f.querySelector<HTMLInputElement>('[name="page"]:checked')?.value;
    if (!value) return;
    await dashWrite(state, async () => { await dashSaved(state); return post('/v2/pins', {place: state.place, item: 'page:' + value, size: f.elements.namedItem('size').value}); });
  });
}
