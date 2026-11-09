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
  tasks: Map<string, TaskRow>;
  extras: NeedsItem[];
}
// The task list a v2 read returns (GET /v2/tasks with brief=true).
interface BotTaskList {
  tasks?: TaskRow[];
}
// The pill's state for a keeper bot (ui/app/pill.ts): what is typed, the files on it and which bot it goes to.
interface BotPillState {
  sending?: boolean;
  files: File[];
  slug: string;
}

// The text a need row shows (the rows carry these fields beside what NeedsItem names).
interface NeedsItem {
  first_line?: string;
  title?: string;
}

// ---- tasks on a keeper bot's page: hub.db instead of Issues ----
const V2_ACTIVE = ['open', 'doing', 'waiting'];
const V2_ENDED = ['review', 'ready', 'done', 'closed', 'declined'];   // the rest of backend/hubdb.py TASK_STATUSES
const V2_GROUPS: [string, string][] = [['doing', 'Doing'], ['waiting', 'Waiting'],
                   ['done', 'Done'], ['declined', 'Declined'], ['closed', 'Closed']];
// Keep titles compact, with status text only when the section does not already name the state.
// Show the avatar of whoever holds the task (your own photo for you).
function taskStateLabel(t: TaskRow): string {
  if (taskNeedsMe(t) || t.status === 'declined') return 'Needs you';
  if (t.status === 'waiting') return 'Waiting';
  if (t.status === 'doing' || (t.status === 'open' && !actorPerson(t.owner_id))) return 'Doing';
  return 'To do';
}
function actorAvatarOnly(a: string): string {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && botRow(slug))
    return `<a class="who-av" href="#/bot/${encodeURIComponent(slug)}" title="${esc(empName(slug))}">${avatar(slug, 22)}</a>`;
  const person = pid === S.me?.id ? (mePerson() || {id: pid, name: S.me?.name}) : (S.people || []).find(p => p.id === pid) || {id: pid, name: actorLabel(a)};
  return `<span class="who-av" title="${esc(pid === S.me?.id ? 'You' : actorLabel(a))}">${personAvatar(person, 22)}</span>`;
}
// `below` is one more line under the title, as a Blocked row's "Waits on …". `card` is the Needs you card the row
// opens instead of the task (decide.js), when it is answered on a card.
function v2TaskRow(t: TaskRow, slug?: string | null, showStatus = true, below = '', card = ''): string {
  // On a bot's page the other party is whoever is not the bot; on a person's page (no slug) it is the requester.
  const other = slug && actorSlug(taskRequester(t)) === slug ? t.owner_id : taskRequester(t);
  const age = taskAge(t);
  // On a bot's page a row says only what its tab doesn't: no "Open" on your own task under Needs you, no "Doing" under
  // Doing, and no avatar when the holder is you or this bot.
  const ownerIsMe = actorPerson(t.owner_id) === S.me?.id, mineOpen = t.status === 'open' && ownerIsMe;
  const statusShown = !slug || !(mineOpen || taskStatusLabel(t) === 'Doing');
  const holderShown = !slug || !(actorSlug(t.owner_id) === slug || ownerIsMe);
  return `<div class="trow${showStatus ? '' : ' status-in-heading'}" data-task-detail="${esc(t.id)}" data-task-version="${esc(t.version || '')}"${card ? ` data-need-card="${esc(card)}"` : ''}><div class="trow-head">
      ${showStatus ? `<span class="pill ${(V2_PILL as Record<string, string | undefined>)[t.status] ?? ''}">${esc(({open: !actorPerson(t.owner_id) ? 'Doing · starting' : ownerIsMe ? 'Needs you' : 'To do', waiting: taskBlockedHold(t) ? 'Blocked' : 'Waiting', doing: 'Doing'} as Record<string, string | undefined>)[t.status] || t.status || '')}</span>${statusShown ? taskStatusText(t) : ''}` : ''}
      <a class="ttl" data-task-detail-open href="#/task/${encodeURIComponent(t.id)}" title="${esc(`${t.title || ''} · ${taskStateLabel(t)} · ${age}`)}">${esc(t.title || '')}</a>${prStateBadge(t.pr_state)}
      <span class="tags">${actorChip(other)}</span>${holderShown ? actorAvatarOnly(t.owner_id) : ''}
      <span class="muted tnum" title="${esc(taskAgeTitle(t))}">${esc(age)}</span></div>${below ? `<div class="trow-waits muted">${esc(below)}</div>` : ''}</div>`;
}
// The rail and person rows open the same complete task as the manager; actor links keep their own destination.
document.addEventListener('click', ev => {
  const from = ev.target instanceof Element ? ev.target : null;
  const row = from?.closest<HTMLElement>('[data-task-detail]');
  if (!from || !row || ev.defaultPrevented || ev.button || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
  const link = from.closest('a');
  if (link && !link.hasAttribute('data-task-detail-open')) return;
  ev.preventDefault();
  void taskModalOpen('t' + (row.dataset.taskDetail ?? ''));
});
function v2TaskGroups(list: TaskRow[], slug: string | null | undefined, statuses: string[]): string {
  const out = V2_GROUPS.filter(([k]) => statuses.includes(k)).map(([k, label]) => {
    const rows = list.filter(t => (t.status === 'open' ? 'doing' : String(t.status)) === k);
    return rows.length ? `<div class="v2-group"><h3>${esc(label)} <span class="muted">${rows.length}</span></h3>${
      rows.map(t => v2TaskRow(t, slug, false)).join('')}</div>` : '';
  }).filter(Boolean).join('');
  return out;
}
function taskNeedsMe(task: TaskRow | NeedsItem | null | undefined): boolean {
  // A need of kind task is listed as a NeedsItem but is read here as the task row it is.
  const row = task as TaskRow | null | undefined;
  const me = myActor();
  return !!me && (row?.owner_id === me || (row?.ask?.recipient_id === me && !row?.ask?.older) || taskWaitingOn(row) === me);
}
// The server counts editable filters for the viewer; Needs you also keeps questions and approvals.
// Done recently uses the bot’s finished work. A failed task load is retried, never shown as an empty list.
async function loadBotTasksV2(slug: string) {
  const pane = $('#t-open'); if (!pane) return;
  const seen = BOT_TASKS_CACHE.get(slug);
  if (seen) botTasksRender(slug, seen.owned);   // last time's list at once; the fresh one follows
  let open: BotTaskList | null = null, ended: BotTaskList | null = null;
  // Done recently needs only what changed since the window opened: the bot's whole history was read on every refresh,
  // and past the server's 500-row page it pushed open tasks out. The day is the cut, so an unchanged list stays a 304.
  const since = new Date(Date.now() - (BOT_DONE_RECENT_DAYS + 1) * 86400000).toISOString().slice(0, 10) + 'T00:00:00Z';
  const who = encodeURIComponent(slug);
  const filtered = botTaskFilterRead(slug);
  for (const wait of [0, 600, 2000]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (!$('#t-open') || BOT?.slug !== slug) return;
    [open, ended] = await Promise.all([
      open || v2Get(`/v2/tasks?owner=${who}&status=${V2_ACTIVE.join(',')}&snoozed=all&brief=true`),     // snoozed show with their chip
      ended || v2Get(`/v2/tasks?owner=${who}&status=${V2_ENDED.join(',')}&snoozed=all&brief=true&updated_since=${since}`)]);
    if (open && ended) break;
  }
  if (!$('#t-open')) return;
  // A task that finished between the two reads is in both: the later copy wins.
  const byId = new Map<string, TaskRow>();
  for (const t of [...(open?.tasks || []), ...(ended?.tasks || [])]) {
    const was = byId.get(t.id);
    if (!was || String(t.updated_at || '') >= String(was.updated_at || '')) byId.set(t.id, t);
  }
  const owned: {tasks: TaskRow[]} | null = open && ended ? {tasks: [...byId.values()]} : null;
  if (!owned) {
    if (!seen) $('#t-open').innerHTML = '<div class="err">Could not load the tasks. They will show on the next refresh.</div>';
    return;
  }
  BOT_TASKS_CACHE.set(slug, {owned});
  botTasksRender(slug, owned);
  await filtered;
}
// Default tab identities (also used by non-Keeper bots) and Done recently. [key, label, count id]; the ids predate the tabs. A bot that is not
// on Tico's task list (Issues) shows these four.
const BOT_TASK_TABS: [string, string, string][] = [['needs', 'Needs you', 'cnt-needs'], ['active', 'Doing', 'cnt-active'],
                       ['blocked', 'Blocked', 'cnt-blocked'], ['done', 'Done recently', 'cnt-done']];
const BOT_DONE_RECENT_DAYS = 14;
const BOT_TASK_DEFAULT_CONDITIONS: Record<string, BotSidebarCondition[]> = {
  needs: [{field: 'needs', value: 'you'}],
  active: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['open', 'doing']}],
  blocked: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['waiting']}, {field: 'waiting_on', value: 'not_me'}],
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
// Until a person picks: Needs you while it has rows, else Doing, else the first tab there is.
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
  const extras = botNeedsExtras(slug), chunks: string[] = [];
  let query = '';
  for (const id of new Set(extras.map(it => String(it.id)))) {
    const encoded = encodeURIComponent(id);
    if (query && query.length + encoded.length + 3 > 4000) { chunks.push(query); query = ''; }
    query += (query ? '%2C' : '') + encoded;
  }
  if (query) chunks.push(query);
  const first = chunks.shift();
  const got = await v2Get(botTaskFilterPath(slug) + (first ? '?match_ids=' + first : ''));
  if (!got || BOT?.slug !== slug) return;
  const needs: BotFilterTab | undefined = got.tabs?.find((tab: BotFilterTab) => tab.key === 'needs');
  if (needs) for (const chunk of chunks) {
    const more = await v2Get(botTaskFilterPath(slug) + '?tab=needs&match_ids=' + chunk);
    const matched: BotFilterTab | undefined = more?.tabs?.find((tab: BotFilterTab) => tab.key === 'needs');
    if (!matched || BOT?.slug !== slug) return;
    needs.matched_ids = [...new Set([...(needs.matched_ids || []), ...(matched.matched_ids || [])])];
  }
  const was: Record<string, BotFilterTab> = BOT_TASK_FILTERED.get(slug)?.tabs || {};
  const read: BotFilterRead = {tabs: Object.fromEntries((got.tabs || []).map((tab: BotFilterTab): [string, BotFilterTab] => [tab.key, tab])),
                tasks: new Map((got.tasks || []).map((t: TaskRow): [string, TaskRow] => [String(t.id), t])), extras};
  // A tab someone read further into stays that far on a refresh: its later pages are read again after the fresh first.
  for (const tab of Object.values(read.tabs)) {
    const before = was[tab.key];
    while (before?.name === tab.name && tab.next && tab.ids.length < before.ids.length)
      if (!await botTaskFilterPage(slug, tab, read.tasks)) break;
  }
  if (BOT?.slug !== slug) return;
  BOT_TASK_FILTERED.set(slug, read);
  botTaskFilterPaint(slug);
  // The initial Needs you read or a live event may finish while this request is in flight.
  const latest = botNeedsExtras(slug).map(it => String(it.id)).sort().join(',');
  if (latest !== extras.map(it => String(it.id)).sort().join(',')) void botTaskFilterRead(slug);
}
// The tab's next page onto the rows it holds; false when it could not be read or the tab is no longer the one asked.
async function botTaskFilterPage(slug: string, tab: BotFilterTab, tasks: Map<string, TaskRow>): Promise<boolean> {
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
    const rows = counted.ids.map(id => got.tasks.get(String(id))).filter((t): t is TaskRow => !!t);
    if (list) botTaskList(list, rows.map(t => v2TaskRow(t, slug, true, t.status === 'waiting' ? botTaskWaitsOn(t) : '')), '<div class="rail-empty">None</div>',
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
// What a blocked task waits on: the person or bot it names, else the first line of its note.
function botTaskWaitsOn(t: TaskRow): string {
  if (taskBotWait(t) || taskBlockedHold(t)) return taskWaitLine(t);
  if (t.waiting_on) return `Waits on ${actorPerson(t.waiting_on) === S.me?.id ? 'you' : actorLabel(t.waiting_on)}`;
  const line = String(t.note || '').split('\n').map(s => s.trim()).find(Boolean);
  return line ? (line.length > 140 ? line.slice(0, 139) + '…' : line) : 'Waits on: not said';
}
// Questions and approvals remain in Needs you even when its task filter is changed.
function botNeedsExtras(slug: string): NeedsItem[] {
  return botNeeds(slug).filter(it => ['question', 'approval'].includes(it.kind) || it.ask?.recipient_id === myActor());
}
// An approval or a question asked outside any task, one line in the rail: a tap opens its card (decide.js).
function botNeedRow(it: NeedsItem): string {
  const from = it.requester_id || '', title = approvalTitle(it) || it.first_line || it.title || '';
  return `<div class="trow" data-need-card="${esc(decideItemKey(it))}"><div class="trow-head">
      <span class="pill needs">${it.kind === 'approval' ? 'Approval' : 'Question'}</span>
      <a class="ttl" href="${esc(NEEDS_YOU)}" aria-haspopup="dialog" title="${esc(plainActors(title))}">${esc(plainActors(title))}</a>
      <span class="tags">${actorChip(from)}</span>
      <span class="muted tnum" title="${esc(fmt(it.created_at))}">${esc(ageShort(it.created_at))}</span></div></div>`;
}
// Keep questions, approvals and matching tasks once each; one answered on a card leaves at once.
function botNeedsPaint(slug: string): Set<string> {
  const host = $('#bot-needs'), filtered = BOT_TASK_FILTERED.get(slug), counted = filtered?.tabs.needs;
  // Supplemental rows and their overlap count belong to the same completed filter read.
  // New questions wait for that read instead of being added again to its task count.
  const extras = counted && filtered?.extras ? filtered.extras : botNeedsExtras(slug);
  const tasks: TaskRow[] = counted && filtered ? counted.ids.map(id => filtered.tasks.get(String(id))).filter((t): t is TaskRow => !!t) : [];
  const items = counted ? [...extras, ...tasks.filter(t => !extras.some(it => String(it.id) === String(t.id)))]
    : [...extras, ...botNeeds(slug).filter(it => it.kind === 'waiting' && !extras.some(extra => extra.id === it.id))];
  if (!host || BOT?.slug !== slug) return new Set();
  botTaskCount('cnt-needs', counted ? Number(counted.count) + extras.filter(it => !(counted.matched_ids || counted.ids).includes(String(it.id))).length : Math.max(items.length, needsMeCount(slug)));
  const list = $('#t-needs'), shown = items.filter(it => !decideGone('kind' in it && it.kind === 'approval' ? decideItemKey(it) : 'task:' + it.id));
  // Some of this bot's items may be on Needs you pages not read yet.
  const more = needsMoreButton()
    + (counted?.next ? '<button type="button" class="ghost bot-done-more" data-bot-filter-more="needs">Show more</button>' : '');
  list.innerHTML = shown.length || more ? `<div class="bot-task-list">${shown.map(it =>
    'kind' in it && it.kind === 'approval' ? botNeedRow(it) : v2TaskRow(it as TaskRow, slug, true, '', decideCardKey(needsItem(it.id)))).join('')}</div>${more}` : '<div class="rail-empty">Nothing needs you.</div>';
  return new Set(items.map(it => String(it.id)));
}
// Needs you changed: refresh filter membership and supplement deduplication.
function botNeedsChanged(slug: string) {
  const seen = BOT_TASKS_CACHE.get(slug);
  if (seen && BOT?.loaded?.has('tasks')) {
    botTasksRender(slug, seen.owned);
    void botTaskFilterRead(slug);
  }
}
function botTasksRender(slug: string, owned: {tasks: TaskRow[]}) {
  if (!$('#t-open') || BOT?.slug !== slug) return;
  const needed = botNeedsPaint(slug);
  const mine = owned.tasks || [];
  const newest = (a: TaskRow, b: TaskRow) => String(b.created_at || '').localeCompare(String(a.created_at || ''));
  // Fallback while filters load (and for non-Keeper bots); saved filters are painted from server results below.
  const active = mine.filter(t => ['open', 'doing'].includes(String(t.status))).sort(newest);
  const blocked = mine.filter(t => t.status === 'waiting' && t.waiting_on !== 'human:' + S.me?.id).sort(newest);
  // Done recently: the bot's own work finished in the last two weeks, most recently done first. A schedule's runs (a
  // sweep every 30 minutes) are not work a person asked for; they live under Automations › Runs.
  const doneAt = (t: TaskRow) => String(t.done_at || t.closed_at || t.updated_at || '');
  const since = new Date(Date.now() - BOT_DONE_RECENT_DAYS * 86400000).toISOString();
  const finished = mine.filter(t => !V2_ACTIVE.includes(String(t.status)) && !isRecurringTask(t) && doneAt(t) >= since)
    .sort((a, b) => doneAt(b).localeCompare(doneAt(a)));
  botTaskCount('cnt-active', active.length);
  botTaskCount('cnt-blocked', blocked.length);
  botTaskCount('cnt-done', finished.length);
  botTaskList($('#t-open'), active.map(t => v2TaskRow(t, slug)), '<div class="rail-empty">None</div>');
  botTaskList($('#t-blocked'), blocked.map(t => v2TaskRow(t, slug, true, botTaskWaitsOn(t))), '<div class="rail-empty">Nothing is blocked.</div>');
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
const BOT_DONE = new Map<string, {rows: TaskRow[]; shown: number}>(), BOT_DONE_PAGE = 50;
function botDonePaint(slug: string) {
  const host = $('#t-done'), done = BOT_DONE.get(slug);
  if (!host || !done || BOT?.slug !== slug || $('#bot-done')?.hidden) return;
  const rows = done.rows.slice(0, done.shown), more = done.rows.length - rows.length;
  botTaskList(host, rows.map(t => v2TaskRow(t, slug, false)), '<div class="rail-empty">Nothing done in the last two weeks.</div>',
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

// the pill's "Send as task" on a keeper bot: a hub task, not an Issue
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
    if (BOT?.slug === P.slug) {
      void loadBotTasksV2(P.slug);
    }
  } catch (e) { toast(errorMessage(e, ''), true); }
  P.sending = false;
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
