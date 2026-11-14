/* ui/app/bot-tasks.ts — Tasks on a bot's page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A bot's task tab as the sidebar layout keeps it (BotSidebarTab, bot-sidebar.ts), with its key: a built-in's or c0, c1….
interface BotTaskTabEntry extends BotSidebarTab {
  key: string | undefined;
}
// One filter tab as the server counted it for the person looking (GET /v2/bots/{bot}/task-tabs/tasks).
interface BotFilterTab {
  key: string;
  name: string;
  ids: string[];
  count?: number;
  next?: string | null;
  matched_ids?: string[];
}
interface BotFilterRead {
  tabs: Record<string, BotFilterTab>;
  tasks: Map<string, TaskAny>;
  extras: NeedsEntry[];
}
// A task as GET /v2/tasks?brief=true lists it: the task without its text, so with its times.
type BotListTask = TaskBriefV3 & {created_at?: string | null; updated_at?: string | null; done_at?: string | null; closed_at?: string | null};
interface BotTaskList {
  tasks?: BotListTask[];
  cursor?: string;
}
// A bot's own tasks as its page last had them (BOT_TASKS_CACHE): the rows, and where their catch-up resumes.
interface BotTasksSeen {
  owned: {tasks: BotListTask[]};
  cursor: string;
}
// The pill's state for a bot (ui/app/pill.ts): what is typed, the files on it and which bot it goes to.
interface BotPillState {
  sending?: boolean | undefined;
  files: File[];
  slug: string | null;
}

// ---- tasks on a bot's page (S14): each row is the title, a Late tag, and its stage dimmed at the right
const V2_ACTIVE = ['open'];
const V2_ENDED = ['done', 'closed'];
async function botStageNamesLoad() {
  try {
    for (const p of await pipelinesAll()) for (const st of p.stages) if (st.id) BOT_STAGE_NAMES.set(st.id, st.name);
  } catch { /* rows show without their stage until the next read */ }
}
// The task a Needs-you item is about: an ask's or a task's own, a stuck stretch's first.
const botNeedTaskId = (it: NeedsEntry) => String(it.task_id || it.task_ids?.[0] || it.task?.id || '');
// Each bot's reads against its live events (task-api.ts TaskFeed): its own tasks, and its filter tabs.
const BOT_TASKS_FEED = new Map<string, {owned: TaskFeed; tabs: TaskFeed}>();
function botTasksFeed(slug: string) {
  const feed = BOT_TASKS_FEED.get(slug) || {owned: taskFeedNew(), tabs: taskFeedNew()};
  BOT_TASKS_FEED.set(slug, feed);
  return feed;
}
// Done recently needs only what changed since the window opened. A failed task load is retried, never shown as an
// empty list. What came while it was out goes over it.
async function loadBotTasksV2(slug: string) {
  const pane = $('#t-open'); if (!pane) return;
  const seen = BOT_TASKS_CACHE.get(slug), feed = botTasksFeed(slug).owned, ticket = taskFeedRead(feed);
  if (seen) botTasksRender(slug, seen.owned);   // last time's list at once; the fresh one follows
  let open: BotTaskList | null = null, ended: BotTaskList | null = null;
  // The day is the cut, so an unchanged list stays a 304.
  const since = new Date(Date.now() - (BOT_DONE_RECENT_DAYS + 1) * 86400000).toISOString().slice(0, 10) + 'T00:00:00Z';
  const who = encodeURIComponent(slug);
  const names = BOT_STAGE_NAMES.size ? null : botStageNamesLoad();
  const filtered = botTaskFilterRead(slug);
  for (const wait of [0, 600, 2000]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (!$('#t-open') || BOT?.slug !== slug) { taskFeedFailed(feed, ticket); return; }
    [open, ended] = await Promise.all([
      open || v2Get(`/v2/tasks?owner=${who}&status=${V2_ACTIVE.join(',')}&snoozed=all&brief=true`),
      ended || v2Get(`/v2/tasks?owner=${who}&status=${V2_ENDED.join(',')}&snoozed=all&brief=true&updated_since=${since}`)]);
    if (open && ended) break;
  }
  await names;
  if (!$('#t-open') || BOT?.slug !== slug) { taskFeedFailed(feed, ticket); return; }
  // A task that finished between the two reads is in both: the later copy wins.
  const byId = new Map<string, BotListTask>();
  for (const t of [...(open?.tasks || []), ...(ended?.tasks || [])]) {
    const was = byId.get(t.id);
    if (!was || String(t.updated_at || '') >= String(was.updated_at || '')) byId.set(t.id, t);
  }
  const owned: {tasks: BotListTask[]} | null = open && ended ? {tasks: [...byId.values()]} : null;
  if (!owned) {
    taskFeedFailed(feed, ticket);
    if (!seen) $('#t-open').innerHTML = '<div class="err">Could not load the tasks. They will show on the next refresh.</div>';
    return;
  }
  // The open read's cursor: the ended read may be the browser's kept copy (304), its cursor older, never newer.
  const cursor = open?.cursor || '', held = taskFeedLanded(feed, ticket, cursor);
  if (!held) { await filtered; return; }   // a newer read is out
  const bulk = taskRowsLive(owned.tasks, held, botOwns(slug)) === 'bulk';
  BOT_TASKS_CACHE.set(slug, {owned, cursor});
  botTasksRender(slug, owned);
  await filtered;
  if (bulk) void botTasksCatchUp(slug);
}

// ---- live: the stream's task events in place (refresh.ts liveTasksApply); nothing is read for one
// The bot's own tasks: the ones it holds, open or finished (Done recently keeps the last two weeks of them).
const botOwns = (slug: string) => (t: BotListTask) => t.owner_id === 'bot:' + slug;
function botTasksLive(slug: string, all: LiveTaskEvent[]): void {
  const feed = botTasksFeed(slug), events = taskFeedEvents(feed.owned, all), tabbed = taskFeedEvents(feed.tabs, all);
  const seen = BOT_TASKS_CACHE.get(slug);
  if (!seen || !BOT?.loaded.has('tasks')) return;
  const moved = taskRowsLive(seen.owned.tasks, events, botOwns(slug));
  if (moved === 'bulk') { void botTasksCatchUp(slug); return; }
  const filtered = botTaskFilterLive(slug, tabbed);
  if (moved) botTasksRender(slug, seen.owned);
  else if (filtered) botTaskFilterPaint(slug);
}
// The 2-minute refresh, this page's own writes and a finished turn: the bot's tasks from their cursor
// (GET /v2/tasks?changed_after), a full read only when there is none or the server says it is too old. The filter tabs
// are read once with it: Needs you and "Waiting on me" turn on every wait of a task, which a list row does not carry.
async function botTasksCatchUp(slug: string): Promise<void> {
  const seen = BOT_TASKS_CACHE.get(slug);
  if (!seen || !$('#t-open') || BOT?.slug !== slug) { if (BOT?.slug === slug) void loadBotTasksV2(slug); return; }
  try {
    const out = await taskRowsCatchUp(seen.owned.tasks, seen.cursor, {owner: slug, status: [...V2_ACTIVE, ...V2_ENDED].join(','), snoozed: 'all'},
      botOwns(slug), () => BOT_TASKS_CACHE.get(slug) === seen && BOT?.slug === slug, botTasksFeed(slug).owned);
    if (!out) return;
    if (out === 'reset') { void loadBotTasksV2(slug); return; }
    seen.cursor = out.cursor;
    if (out.moved) botTasksRender(slug, seen.owned);
    if (out.again) { void botTasksCatchUp(slug); return; }
  } catch { /* the next refresh tries again */ }
  await botTaskFilterRead(slug);
}
// Whether a task belongs in a filter tab, by its rows (backend/bot_task_tabs.py tab_sql), as the viewer reads it now:
// true, false, or null when a row turns on what a list row does not carry (every wait of the task: Needs you, waiting
// on me), which the refresh's read of the tabs then settles.
function botTabHas(tab: BotTaskTabEntry, t: TaskAny, slug: string): boolean | null {
  const me = myActor(), it = 'bot:' + slug, row = t as TaskAny & {created_at?: string | null; updated_at?: string | null; turn?: TaskWaitV3 | null;
    done_at?: string | null; closed_at?: string | null; labels?: string[]; routine_id?: string | null; body?: string | null};
  if (row.owner_id !== it && row.requester_id !== it) return false;
  const ago = (days: number) => new Date(Date.now() - days * 86400e3).toISOString().slice(0, 19);
  const party = (actor: string | null | undefined, value: BotSidebarValue) =>
    value === 'this_bot' ? actor === it : value === 'me' ? actor === me : value === 'others' ? ![me, it].includes(actor || '') : actor === value;
  const list = (value: BotSidebarValue) => (Array.isArray(value) ? value : [value]).map(String);
  const finished = row.status === 'done' || row.status === 'closed';
  let window: number | null = BOT_DONE_RECENT_DAYS;
  for (const {field, value} of tab.conditions || []) {
    let hit: boolean;
    if (field === 'status') hit = list(value).includes(row.status);
    else if (field === 'stage') hit = list(value).includes(String(row.stage_id ?? ''));
    else if (field === 'pipeline') hit = list(value).includes(String(row.pipeline_id ?? ''));
    else if (field === 'assigned_to') hit = party(row.owner_id, value);
    else if (field === 'asked_by') hit = party(row.requester_id, value);
    else if (field === 'needs' || field === 'waiting_on' && value === 'me') return null;
    else if (field === 'waiting_on') {
      // The engine's headline wait (the row's `turn`), on the bot's open tasks only.
      const head = row.turn, who = head?.who ?? null;
      hit = row.status === 'open' && !!head && (value === 'person' ? head.state === 'person' && who !== it
        : value === 'bot' ? (head.state === 'bot' || head.state === 'paused') && !!who && who !== it
        : value === 'any' ? who !== it : ![it, me].includes(who || ''));
    }
    else if (field === 'created_within') hit = String(row.created_at || '') >= ago(Number(value));
    else if (field === 'updated_within') hit = String(row.updated_at || '') >= ago(Number(value));
    else if (field === 'done_within') { window = null; hit = String(row.done_at || row.closed_at || '') >= ago(Number(value)); }
    else if (field === 'due') {
      const day = String(row.due_at || '').slice(0, 10), today = new Date().toISOString().slice(0, 10);
      hit = !!day && (value === 'overdue' ? day < today && row.status === 'open'
        : day >= today && day <= new Date(Date.now() + Number(value) * 86400e3).toISOString().slice(0, 10));
    }
    else if (field === 'label') hit = (row.labels || []).includes(String(value));
    else return null;
    if (!hit) return false;
  }
  if (finished && window && String(row.done_at || row.closed_at || row.updated_at || '') < ago(window)) return false;
  // A routine's finished runs are on no tab.
  return !(finished && (row.routine_id || /from schedule/.test(String(row.body || ''))));
}
// The filter tabs as the server counted them, mended from the events: a held task's row in place, a task joining or
// leaving a tab its rows decide, and a gone or unreadable one out of every tab. True when anything moved.
function botTaskFilterLive(slug: string, events: LiveTaskEvent[]): boolean {
  const got = BOT_TASK_FILTERED.get(slug);
  if (!got) return false;
  const tabs = botTaskTabList(slug);
  let moved = false;
  for (const d of events) {
    const id = taskEventId(d), t = taskOfEvent<TaskAny & {created_at?: string | null}>(d);
    if (t) {
      if (got.tasks.has(id)) { got.tasks.set(id, t); moved = true; }
    }
    for (const tab of Object.values(got.tabs)) {
      const layout = tabs.find(x => x.key === tab.key && x.name === tab.name);
      const has = !t ? false : layout ? botTabHas(layout, t, slug) : null;
      const at = tab.ids.indexOf(id);
      if (has === null) continue;
      if (!has && at >= 0) { tab.ids.splice(at, 1); tab.count = Math.max(0, Number(tab.count) - 1); moved = true; }
      else if (has && at < 0 && t) {
        // Newest first, as the server pages it; one older than the last held row is on a later page.
        const created = String(t.created_at || ''), byCreated = (x: string) => String((got.tasks.get(x) as {created_at?: string} | undefined)?.created_at || '');
        const before = tab.ids.findIndex(x => byCreated(x) < created);
        if (before >= 0 || !tab.next) { tab.ids.splice(before >= 0 ? before : tab.ids.length, 0, id); got.tasks.set(id, t); }
        // Counted only when it is surely new to the tab: every row is held, it sorts among them, or it was just made. One
        // that may already be on a later page leaves the count to the next read.
        if (before >= 0 || !tab.next || (Array.isArray(d.kinds) && d.kinds.includes('insert'))) tab.count = Number(tab.count) + 1;
        moved = true;
      }
    }
  }
  return moved;
}
// Default tab identities and Done recently. [key, label, count id]; the ids predate the tabs. A bot that is not
// on Tico's task list (Issues) shows these four.
const BOT_TASK_TABS: [string, string, string][] = [['needs', 'Needs you', 'cnt-needs'], ['active', 'Open', 'cnt-active'],
                       ['blocked', 'Waiting', 'cnt-blocked'], ['done', 'Done recently', 'cnt-done']];
const BOT_DONE_RECENT_DAYS = 14;
const BOT_TASK_DEFAULT_CONDITIONS: Record<string, BotSidebarCondition[]> = {
  needs: [{field: 'needs', value: 'you'}],
  active: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['open']}],
  blocked: [{field: 'assigned_to', value: 'this_bot'}, {field: 'waiting_on', value: 'not_me'}],
};
// Upgrade browser caches too, keeping the key, name, order and count setting of old layouts.
function botTaskTabDefaults(tab: BotSidebarTab): BotSidebarTab {
  const rows = BOT_TASK_DEFAULT_CONDITIONS[tab.builtin ?? ''];
  return rows && !Array.isArray(tab.conditions) ? {...tab, conditions: JSON.parse(JSON.stringify(rows))} : tab;
}
// The tab a person picked on each bot's page, for this visit; until they pick, Needs you leads while it has rows.
const BOT_TASK_TAB = new Map<string, string | undefined>();
// The bot's tabs in order, each with its key: a built-in's own, or c0, c1… for its filters by place. They come from
// the bot's sidebar layout (ui/app/bot-sidebar.ts); while it edits, from the draft.
function botTaskTabList(slug: string): BotTaskTabEntry[] {
  const tabs: BotSidebarTab[] = (BOT_EDIT?.slug === slug ? BOT_EDIT.tabs : botSidebarLayout(slug)?.tabs) || BOT_TASK_TABS.map(([builtin, name]) => ({builtin, name}));
  return tabs.map((tab, at) => ({...botTaskTabDefaults(tab), key: tab.builtin || 'c' + at}));
}
// [key, label, count id], as Part 1 and 3 read them.
function botTaskTabs(slug: string): [string | undefined, string | undefined, string][] {
  return botTaskTabList(slug).map(({key, name}) => [key, name, 'cnt-' + key]);
}
// A tab's count; a tab the bot does not show has none to set.
function botTaskCount(id: string, n: number | undefined) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(['cnt-needs', 'cnt-active', 'cnt-blocked'].includes(id) ? n ?? '' : n || '');
}
// A tab whose count is off keeps it in the page, unseen, so a redraw has one place to write it.
function botTaskTabsBarHTML(slug: string): string {
  return botTaskTabList(slug).map(({key, name, show_count: count}) =>
    `<button type="button" role="tab" id="bot-tab-t-${key}" data-bot-task-tab="${key}" aria-controls="bot-${key}" aria-selected="false"${count === false ? ' class="no-cnt"' : ''}>${esc(name)} <span class="cnt" id="cnt-${key}"></span></button>`).join('')
    + (BOT_EDIT?.slug === slug ? '<button type="button" class="bot-tab-add" data-bot-tab-add data-sort-tail aria-label="Add a filter" title="Add a filter">+</button>' : '');
}
// The built-in panels stay on the page whether or not their tab is shown; the bot's filters get one each.
function botTaskCustomPanelsHTML(slug: string): string {
  return botTaskTabList(slug).filter(tab => !tab.builtin).map(({key}) =>
    `<div class="bot-custom-tab" id="bot-${key}" role="tabpanel" aria-labelledby="bot-tab-t-${key}" hidden><div id="t-${key}" class="tpane"><div class="rail-empty">Loading…</div></div></div>`).join('');
}
function botTaskTabsPaint(slug: string) {
  const bar = $('#pane-tasks .bot-task-tabs'), host = $('#bot-task-custom');
  if (!bar || !host || BOT?.slug !== slug) return;
  bar.innerHTML = botTaskTabsBarHTML(slug);
  host.innerHTML = botTaskCustomPanelsHTML(slug);
  const seen = BOT_TASKS_CACHE.get(slug);
  if (seen) botTasksRender(slug, seen.owned);
  else botTaskTabShow(slug, botTaskTabFirst(slug, needsMeCount(slug)));
}
// Until a person picks: Needs you while it has rows, else Open, else the first tab there is.
function botTaskTabFirst(slug: string, needed: number): string | undefined {
  const keys = botTaskTabList(slug).map(tab => tab.key), picked = BOT_TASK_TAB.get(slug);
  if (picked !== undefined && keys.includes(picked)) return picked;
  return (needed && keys.includes('needs') && 'needs') || (keys.includes('active') && 'active') || keys[0];
}
// A redraw (the 30 s refresh, a live event) only re-applies the tab; a person's pick also brings the tab into view, sideways
// within the tab bar, so neither moves the rail away from what someone is reading. A key the bot has no tab for shows
// its first. One tab is one list: no tab bar over it, except while editing.
function botTaskTabShow(slug: string, key: string | undefined, picked = false) {
  const rail = $('#pane-tasks'), keys = botTaskTabs(slug).map(([k]) => k);
  if (!rail || BOT?.slug !== slug || !keys.length) return;
  if (!keys.includes(key)) key = keys[0];
  const bar = rail.querySelector('.bot-task-tabs');
  if (bar) bar.hidden = keys.length < 2 && BOT_EDIT?.slug !== slug;
  for (const k of new Set([...BOT_TASK_TABS.map(([k]) => k), ...keys])) {
    const button = $('#bot-tab-t-' + k), panel = $('#bot-' + k);
    if (panel) panel.hidden = k !== key;
    if (!button) continue;
    button.classList.toggle('cur', k === key);
    button.setAttribute('aria-selected', String(k === key));
    button.tabIndex = k === key ? 0 : -1;
  }
  if (key === 'done') botDonePaint(slug);
  const tab = $('#bot-tab-t-' + key);
  if (!picked || !bar || bar.hidden || !tab) return;
  // By what is on screen: the bar is not the tabs' offset parent, so their offsetLeft counts from further out.
  const at = tab.getBoundingClientRect(), shown = bar.getBoundingClientRect();
  if (at.left < shown.left) bar.scrollLeft -= shown.left - at.left;
  else if (at.right > shown.right) bar.scrollLeft += at.right - shown.right;
}
// Arrow keys, Home and End move between the tabs, as in any tab list.
function botTaskTabKey(slug: string, ev: KeyboardEvent) {
  if (BOT_EDIT) return;
  const keys = botTaskTabs(slug).map(([k]) => k), target = ev.target instanceof Element ? ev.target : null;
  const at = keys.indexOf(target?.closest<HTMLElement>('[data-bot-task-tab]')?.dataset.botTaskTab ?? '');
  if (at < 0) return;
  const moves: Record<string, number> = {ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: keys.length - 1};
  const to = moves[ev.key];
  if (to === undefined) return;
  ev.preventDefault();
  const key = keys[(to + keys.length) % keys.length];
  BOT_TASK_TAB.set(slug, key);
  botTaskTabShow(slug, key, true);
  $('#bot-tab-t-' + key)?.focus({preventScroll: true});
}
// There is no cap on tabs, so the bar scrolls sideways; a mouse wheel over it scrolls it too, not only a trackpad.
function botTaskTabWheel(ev: WheelEvent) {
  const bar = ev.currentTarget instanceof HTMLElement ? ev.currentTarget : null;
  if (!bar || bar.scrollWidth <= bar.clientWidth || Math.abs(ev.deltaX) >= Math.abs(ev.deltaY)) return;
  ev.preventDefault();
  bar.scrollLeft += ev.deltaY;
}
// What each filter tab shows the person looking, as the server counted it: slug -> {tabs: {key: {count, ids, next}},
// tasks}. A tab with more than one page has `next`, read on Show more.
const BOT_TASK_FILTERED = new Map<string, BotFilterRead>();
const botTaskFilterPath = (slug: string) => `/v2/bots/${encodeURIComponent(slug)}/task-tabs/tasks`;
// A refresh replaces a bot's filter lists and Show more adds to them, so they take turns: a page read beside a refresh
// would land on the lists the refresh threw away, and a refresh read beside a page would not know to keep it.
const BOT_TASK_FILTER_TURN = new Map<string, Promise<unknown>>();
function botTaskFilterTurn<T>(slug: string, job: () => Promise<T>): Promise<T> {
  const run = (BOT_TASK_FILTER_TURN.get(slug) || Promise.resolve()).then(job);
  const next = run.catch(() => {});
  BOT_TASK_FILTER_TURN.set(slug, next);
  next.then(() => { if (BOT_TASK_FILTER_TURN.get(slug) === next) BOT_TASK_FILTER_TURN.delete(slug); });
  return run;
}
const botTaskFilterRead = (slug: string) => botTaskFilterTurn(slug, () => botTaskFilterReadNow(slug));
async function botTaskFilterReadNow(slug: string) {
  // Keep each URL below common proxy limits even after many Needs you pages have been read.
  const extras = botNeeds(slug), chunks: string[] = [];
  let query = '';
  for (const id of new Set(extras.map(botNeedTaskId).filter(Boolean))) {
    const encoded = encodeURIComponent(id);
    if (query && query.length + encoded.length + 3 > 4000) { chunks.push(query); query = ''; }
    query += (query ? '%2C' : '') + encoded;
  }
  if (query) chunks.push(query);
  const first = chunks.shift(), feed = botTasksFeed(slug).tabs, ticket = taskFeedRead(feed);
  const got = await v2Get(botTaskFilterPath(slug) + (first ? '?match_ids=' + first : '')).catch((e: unknown) => { taskFeedFailed(feed, ticket); throw e; });
  if (!got || BOT?.slug !== slug) { taskFeedFailed(feed, ticket); return; }
  const needs: BotFilterTab | undefined = got.tabs?.find((tab: BotFilterTab) => tab.key === 'needs');
  if (needs) for (const chunk of chunks) {
    const more = await v2Get(botTaskFilterPath(slug) + '?tab=needs&match_ids=' + chunk);
    const matched: BotFilterTab | undefined = more?.tabs?.find((tab: BotFilterTab) => tab.key === 'needs');
    if (!matched || BOT?.slug !== slug) { taskFeedFailed(feed, ticket); return; }
    needs.matched_ids = [...new Set([...(needs.matched_ids || []), ...(matched.matched_ids || [])])];
  }
  const was: Record<string, BotFilterTab> = BOT_TASK_FILTERED.get(slug)?.tabs || {};
  const read: BotFilterRead = {tabs: Object.fromEntries((got.tabs || []).map((tab: BotFilterTab): [string, BotFilterTab] => [tab.key, tab])),
                tasks: new Map((got.tasks || []).map((t: TaskV3): [string, TaskAny] => [String(t.id), t])), extras};
  // A tab someone read further into stays that far on a refresh: its later pages are read again after the fresh first.
  for (const tab of Object.values(read.tabs)) {
    const before = was[tab.key];
    while (before?.name === tab.name && tab.next && tab.ids.length < before.ids.length)
      if (!await botTaskFilterPage(slug, tab, read.tasks)) break;
  }
  const held = BOT?.slug === slug ? taskFeedLanded(feed, ticket) : null;
  if (!held) { taskFeedFailed(feed, ticket); return; }
  BOT_TASK_FILTERED.set(slug, read);
  // The tabs' answer is as of when the server counted them: what came while it was out goes over it.
  botTaskFilterLive(slug, held.filter(d => !d.bulk));
  botTaskFilterPaint(slug);
  // The initial Needs you read or a live event may finish while this request is in flight.
  const latest = botNeeds(slug).map(it => String(it.id)).sort().join(',');
  if (latest !== extras.map(it => String(it.id)).sort().join(',')) void botTaskFilterRead(slug);
}
// The tab's next page onto the rows it holds; false when it could not be read or the tab is no longer the one asked.
async function botTaskFilterPage(slug: string, tab: BotFilterTab, tasks: Map<string, TaskAny>): Promise<boolean> {
  const got = await v2Get(`${botTaskFilterPath(slug)}?tab=${tab.key}&before=${encodeURIComponent(tab.next ?? '')}`);
  const page = got?.tabs?.[0];
  if (!page || page.key !== tab.key || page.name !== tab.name) return false;
  const held = new Set(tab.ids);
  tab.ids = [...tab.ids, ...page.ids.filter((id: string) => !held.has(id))];
  tab.next = page.next;
  tab.count = page.count;
  for (const t of got.tasks || []) tasks.set(String(t.id), t);
  return true;
}
// Each filter's list and count. A filter the server has not counted yet (just added) keeps its Loading line.
function botTaskFilterPaint(slug: string) {
  const got = BOT_TASK_FILTERED.get(slug);
  if (!got || BOT?.slug !== slug) return;
  for (const tab of botTaskTabList(slug).filter(tab => tab.key !== 'done' && tab.key !== 'needs')) {
    const counted = got.tabs[tab.key ?? ''], list = $(tab.key === 'active' ? '#t-open' : '#t-' + tab.key);
    if (!counted || counted.name !== tab.name) continue;
    botTaskCount('cnt-' + tab.key, counted.count);
    const rows = counted.ids.map(id => got.tasks.get(String(id))).filter((t): t is TaskAny => !!t);
    if (list) botTaskList(list, rows.map(botTaskRowHTML), '<div class="rail-empty">None</div>',
      counted.next ? `<button type="button" class="ghost bot-done-more" data-bot-filter-more="${tab.key}">Show more</button>` : '');
  }
  const needed = botNeedsPaint(slug);
  botTaskTabShow(slug, botTaskTabFirst(slug, needed.size));
}
document.addEventListener('click', async ev => {
  const target = ev.target instanceof Element ? ev.target : null;
  const button = target?.closest<HTMLButtonElement>('[data-bot-filter-more]'), slug = BOT?.slug, key = button?.dataset.botFilterMore ?? '';
  if (!button || !slug || button.disabled || !BOT_TASK_FILTERED.get(slug)?.tabs[key]?.next) return;
  button.disabled = true;
  // The lists as they are when its turn comes, not as they were at the click.
  const read = await botTaskFilterTurn(slug, async () => {
    const got = BOT_TASK_FILTERED.get(slug), tab = got?.tabs[key];
    return BOT?.slug !== slug || !tab?.next || !got || await botTaskFilterPage(slug, tab, got.tasks);
  }).catch(() => false);
  button.disabled = false;
  if (read) botTaskFilterPaint(slug);
  else toast('Could not load more. Try again.', true);
});
// Needs you: the tasks the tab counts, and what else of this bot's is in the person's Needs you (a stuck stretch, an ask
// on a task the tab does not hold), each once. Needs you is the list the app holds now, not as it was when the tab was
// read: an ask answered leaves at once, a new one shows. A task the tab held because it was in Needs you then, and no
// longer is, leaves too, when the whole list is here to say so.
function botNeedsPaint(slug: string): Set<string> {
  const host = $('#bot-needs'), filtered = BOT_TASK_FILTERED.get(slug), counted = filtered?.tabs.needs;
  const extras = botNeeds(slug), now = new Set(extras.map(botNeedTaskId).filter(Boolean));
  const left = new Set(counted && filtered && needsAll() ? filtered.extras.map(botNeedTaskId).filter(id => id && !now.has(id)) : []);
  const tasks: TaskAny[] = counted && filtered ? counted.ids.filter(id => !left.has(String(id))).map(id => filtered.tasks.get(String(id)))
    .filter((t): t is TaskAny => !!t) : [];
  const gone = new Set([...(counted?.ids || []), ...(counted?.matched_ids || [])].map(String).filter(id => left.has(id)));
  const held = new Set([...tasks.map(t => String(t.id)), ...(counted?.matched_ids || []).filter(id => !left.has(String(id)))]);
  const rest = new Map<string, TaskAny>();
  for (const it of extras) {
    const id = botNeedTaskId(it);
    if (id && it.task && !held.has(id) && !rest.has(id)) rest.set(id, it.task);
  }
  const items = [...tasks, ...rest.values()];
  if (!host || BOT?.slug !== slug) return new Set();
  botTaskCount('cnt-needs', counted ? Math.max(0, Number(counted.count) - gone.size) + rest.size : Math.max(items.length, needsMeCount(slug)));
  const list = $('#t-needs');
  const more = (counted?.next ? '<button type="button" class="ghost bot-done-more" data-bot-filter-more="needs">Show more</button>' : '');
  list.innerHTML = items.length || more ? `<div class="bot-task-list">${items.map(botTaskRowHTML).join('')}</div>${more}` : '<div class="rail-empty">Nothing needs you.</div>';
  return new Set(items.map(t => String(t.id)));
}
// Needs you changed: the Needs you tab is drawn again from the list the app holds (botNeedsPaint adds this bot's items
// it does not show yet). Nothing is read for it (Nov 3): which tasks the tab's own rule takes turns on every wait of a
// task, which no row carries, so the 2-minute refresh's tab read settles that (botTasksCatchUp).
function botNeedsChanged(slug: string) {
  const seen = BOT_TASKS_CACHE.get(slug);
  if (seen && BOT?.loaded?.has('tasks')) botTasksRender(slug, seen.owned);
}
function botTasksRender(slug: string, owned: {tasks: BotListTask[]}) {
  if (!$('#t-open') || BOT?.slug !== slug) return;
  const needed = botNeedsPaint(slug);
  const mine = owned.tasks || [];
  const newest = (a: BotListTask, b: BotListTask) => String(b.created_at || '').localeCompare(String(a.created_at || ''));
  // Until the filters are read; saved filters are painted from the server's lists below. Waiting: its turn is someone
  // else's, not this bot's and not yours.
  const active = mine.filter(t => t.status === 'open').sort(newest);
  const waiting = mine.filter(t => t.status === 'open' && t.turn?.who && ![`bot:${slug}`, myActor()].includes(t.turn.who)).sort(newest);
  // Done recently: the bot's own work finished in the last two weeks, most recently done first.
  const doneAt = (t: BotListTask) => String(t.done_at || t.closed_at || t.updated_at || '');
  const since = new Date(Date.now() - BOT_DONE_RECENT_DAYS * 86400000).toISOString();
  const finished = mine.filter(t => t.status !== 'open' && doneAt(t) >= since)
    .sort((a, b) => doneAt(b).localeCompare(doneAt(a)));
  botTaskCount('cnt-active', active.length);
  botTaskCount('cnt-blocked', waiting.length);
  botTaskCount('cnt-done', finished.length);
  botTaskList($('#t-open'), active.map(botTaskRowHTML), '<div class="rail-empty">None</div>');
  botTaskList($('#t-blocked'), waiting.map(botTaskRowHTML), '<div class="rail-empty">None</div>');
  const done = BOT_DONE.get(slug);
  BOT_DONE.set(slug, {rows: finished, shown: done?.shown || BOT_DONE_PAGE});
  botTaskFilterPaint(slug);
  botTaskTabShow(slug, botTaskTabFirst(slug, needed.size));
}
// A pane's list, patched row by row (patchHTML on the list itself): a poll that moved one task redraws that row, not the
// pane. `empty` stands in for no rows; `tail` follows the list (Done's Show more).
function botTaskList(host: Element, rows: string[], empty: string, tail = '') {
  if (!rows.length) return void patchHTML(host, empty);
  let list = host.querySelector<HTMLElement>(':scope > .bot-task-list');
  if (!list) {
    patchHTML(host, '<div class="bot-task-list"></div>');
    const first = host.firstElementChild;
    list = first instanceof HTMLElement ? first : null;
  }
  if (!list) return;
  patchHTML(list, rows.join(''));
  const end = host.querySelector<HTMLElement>(':scope > .bot-task-tail');
  if ((end?.dataset.src || '') === tail) return;
  end?.remove();
  if (!tail) return;
  list.insertAdjacentHTML('afterend', `<div class="bot-task-tail">${tail}</div>`);
  const added = host.querySelector<HTMLElement>(':scope > .bot-task-tail');
  if (added) added.dataset.src = tail;
}
// Done recently is drawn only while its tab is open, a page at a time: a busy bot finishes hundreds of tasks in two
// weeks, and every row (an avatar each) was built on every refresh behind a folded tab.
const BOT_DONE = new Map<string, {rows: TaskAny[]; shown: number}>(), BOT_DONE_PAGE = 50;
function botDonePaint(slug: string) {
  const host = $('#t-done'), done = BOT_DONE.get(slug);
  if (!host || !done || BOT?.slug !== slug || $('#bot-done')?.hidden) return;
  const rows = done.rows.slice(0, done.shown), more = done.rows.length - rows.length;
  botTaskList(host, rows.map(botTaskRowHTML), '<div class="rail-empty">Nothing done in the last two weeks.</div>',
    more > 0 ? `<button type="button" class="ghost bot-done-more" data-bot-done-more>Show ${Math.min(more, BOT_DONE_PAGE)} more</button>` : '');
}
document.addEventListener('click', ev => {
  const target = ev.target instanceof Element ? ev.target : null;
  if (!target?.closest('[data-bot-done-more]') || !BOT) return;
  const done = BOT_DONE.get(BOT.slug);
  if (!done) return;
  done.shown += BOT_DONE_PAGE;
  botDonePaint(BOT.slug);
});

// the pill's "Send as task": a task for the bot
async function v2PillTask(P: BotPillState, text: string) {
  if (P.sending) return;
  P.sending = true;
  const files = P.files.slice(), scope = sessionScope(pq(P, '.p-text'));
  const btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Creating…');
  try {
    await cloudCompose('/v2/tasks', {title: (text.split('\n')[0] ?? '').trim().slice(0, 80), body: text, owner: P.slug}, files);
    pillAcknowledge(P, text, files, scope);
    toast(`Task for ${empName(P.slug)} created`);
    await v2Refresh();
    if (typeof P.slug === 'string' && BOT?.slug === P.slug) {   // N: the bot's slug is a string here
      void botTasksCatchUp(P.slug);
    }
  } catch (e) { toast(errorMessage(e, ''), true); }
  P.sending = false;
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
