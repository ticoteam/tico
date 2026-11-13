/* ui/app/refresh.ts — The refresh loop
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- refresh loop + router
// Recruiting seats change only by a recruit flow here (each refreshes with force) or another person's: a poll reads
// them every 5th time, or when the last answer is 10 minutes old.
let SEATS_POLLS = 0, SEATS_AT = 0;
// Newest wins, per value: each refresh is numbered as it starts, and publishes status, the roster or seats only when no
// refresh that started later has already published that value. An older poll answering last cannot put back the roster
// from before a change, and a newer poll that did not read seats does not discard an older forced refresh's seats.
let REFRESH_STARTED = 0;
const REFRESH_SHOWN = {status: 0, roster: 0, seats: 0};
async function refresh(force = false): Promise<void> {
  const mine = ++REFRESH_STARTED;
  // Every read at once: hub.db status and needs-you (docs/history/hub-v2.md) and the campus's computers (only while it
  // is open) do not wait for the roster.
  const v2 = v2Refresh(), computers = overviewLoadComputers();
  try {
    // BotOps and other sessions can change the chart. Publish a complete roster together so groups and members agree.
    const seatsDue = force || S.seats === undefined || ++SEATS_POLLS % 5 === 0 || Date.now() - SEATS_AT > 600e3;
    // A forced refresh follows a change: an in-flight read (including startup's notification preference read of
    // /humans) may already hold the old roster. Ask again instead of joining that read.
    const options = {fresh: !!force};
    const [st, emps, people, seats] = await Promise.allSettled([get('/status', options), get('/employees', options), get('/humans', options),
      seatsDue ? get('/v2/seats', options) : null]);
    if (mine > REFRESH_SHOWN.status) {
      REFRESH_SHOWN.status = mine;
      S.status = st.status === 'fulfilled' ? st.value : null;
      S.statusPending = false;
      cloudAccessFromStatus();
    }
    // A failed read publishes nothing, so an older refresh's good roster can still be shown.
    if (mine > REFRESH_SHOWN.roster) {
      if (emps.status === 'fulfilled' && people.status === 'fulfilled' && people.value?.people) {
        REFRESH_SHOWN.roster = mine;
        S.emps = namedRoster(emps.value);
        // /humans carries recruiting seats too: they never replace seats a later refresh already showed.
        setPeople(mine > REFRESH_SHOWN.seats ? people.value : {...people.value, seats: undefined});
        S.overviewRosterFresh = true;
      } else S.overviewRosterFresh = false;
    }
    if (seatsDue && seats.status === 'fulfilled' && mine > REFRESH_SHOWN.seats) { REFRESH_SHOWN.seats = mine; S.seats = seats.value?.seats || []; SEATS_AT = Date.now(); }   // Recruiting seats (backend/lifecycle.py)
  } catch (e) { S.overviewRosterFresh = false; }
  await Promise.all([v2, computers]);
  overviewRefresh();
  if (S.me?.cloud) void updUnreadRefresh();
  renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync(); goalsFollowRoster();
  tipRefresh();
  botAlertDraw();
  if (BOT) botScreenRefresh(BOT.slug);   // the Screen row and header button follow the computer (online, screen offered)
  if (BOT && $('#bot-onboard-host') && ($('#bot-onboard') ? '1' : '') !== (frNeedsSetup(botRow(BOT.slug)) ? '1' : '')) frBotRefresh(BOT.slug);   // the mark clears when the bot says it is set up
  // Poll data without destroying an expanded document or a comment being typed (one inside a closed card is not being
  // written in).
  const reviewing = $('#main .req[open]') || [...document.querySelectorAll('#main .issue-compose:not([hidden])')].some(form => !form.closest('details:not([open])'));
  if (force || !reviewing) {
    if (TASKS_ST && isTasksRoute(S.route)) void (force ? tasksLoad(TASKS_ST) : tasksCatchUp(TASKS_ST));   // a delta from the list's cursor
    // A bot's tasks catch up from their cursor too (bot-tasks.ts); a forced refresh reads them whole.
    if (BOT && BOT.loaded.has('tasks')) void (force ? loadBotTasksV2(BOT.slug) : botTasksCatchUp(BOT.slug));
    if (typeof PERSON_TASKS !== 'undefined' && PERSON_TASKS) void personTasksReload();
  }
}

// ----------------------------------------------------------------- live events (ui/app/live.ts)
// What the 30 s refresh used to find by reading everything again now arrives as it changes: a task moved (the open
// board, the bot's columns and ticker, a person's tasks, the open task), a bot's status line (the tree, the
// heartbeat, the paused notice, the Overview's desks), and Needs you (the tree's counts). Each surface redraws once
// per burst.
// The same JSON value, whatever order its keys come in (a live event's copy of a task is built apart from the list's).
// A JSON object read by key: sameData checks it is one before it reads a key.
const fieldsOf = (v: object): Record<string, unknown> => v as Record<string, unknown>;
function sameData(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  const fa = fieldsOf(a), fb = fieldsOf(b);
  return ka.every(k => Object.hasOwn(b, k) && sameData(fa[k], fb[k]));
}
/** One live task event: the task it names (task_id or id), whether it is gone or a bulk change, and the task's copy. */
interface LiveTaskEvent {
  /** The change's number (backend/events.py _event): a list read as of a later one already holds it. */
  seq?: unknown;
  id?: unknown;
  task_id?: unknown;
  gone?: unknown;
  bulk?: unknown;
  kinds?: unknown;
  task?: {id?: unknown; lane?: unknown; links?: unknown; owner_id?: unknown; requester_id?: unknown; type_id?: unknown} | null;
}
function liveTasksApply(events: LiveTaskEvent[]): void {
  events = events.map(d => d.task_id && !d.id ? {...d, id: d.task_id} : d);
  const chat = TASK_CHAT;   // the open conversation as it is now (taskChatStop below clears the global)
  if (chat && events.some(d => d.gone && String(d.id) === String(chat.id))) {
    if (chat.dialog.taskLive) taskLiveGone(chat.dialog, chat.dialog.taskLive);
    else chat.dialog.querySelector<HTMLElement>('[data-modal-close]')?.click();
    taskChatStop();
  }
  // The Tasks page puts each event's task in place (tasks-page.ts); it reads nothing.
  if (events.length) tasksLiveApply(events);
  // A bot's page puts each in place too (bot-tasks.ts); it reads nothing.
  if (BOT) botTasksLive(BOT.slug, events);
  // And a person's page (person-page.ts).
  if (typeof PERSON_TASKS !== 'undefined' && PERSON_TASKS) personTasksLive(events);
  // And My dashboard's My tasks (dashboard.ts).
  dashTasksLive(events);
  const open = TASK_CHAT;   // read again: a stop above may have cleared it
  if (open && events.some(d => d.bulk || String(d.id) === String(open.id))) taskChatLive(open);
}
/** A bot's live status line: the bot, and its status (absent when the bot has none). */
interface LiveBotEvent { bot: string; status?: V2BotState; }
function liveWire(): void {
  if (!S.me?.cloud || !liveAvailable()) return;
  let tasks: LiveTaskEvent[] = [];
  liveOn('tasks', (d: LiveTaskEvent) => { tasks.push(d); liveSoon('tasks', () => { const batch = tasks; tasks = []; liveTasksApply(batch); }, 200); });
  liveOn('bots', (d: LiveBotEvent) => {
    if (d.status) S.v2.status[d.bot] = d.status; else delete S.v2.status[d.bot];
    tipRefresh(d.bot);
    liveSoon('bots', () => {
      renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync(); goalsFollowRoster();
      tipRefresh();
      botAlertDraw();
      overviewRefresh();                // the campus desks too, not only at the next refresh (no read: the model is local)
    }, 200);
  });
  // Needs you comes as the ids that changed; the list is mended from them (v2NeedsChanged), then `needs-list` says so.
  liveOn('needs', (d: unknown) => void v2NeedsChanged(d).then(moved => {
    // A live change reaches the browser notifications too, not only the refresh (hub-v2.ts); one memory serves both.
    if (typeof notifyNewThreadNeeds === 'function') notifyNewThreadNeeds(S.v2.needs || []);
    if (!moved) return;
    liveEmit('needs-list', S.v2.needs || []);
    liveSoon('needs', () => {
      renderTree();
      if (OVERVIEW_PAGE) overviewNeedsDraw();
      tasksNeedsLive();
      if (BOT) botNeedsChanged(BOT.slug);
    }, 200);
  }).catch(() => {}));
  // My dashboard puts each change in its cards (ui/app/dashboard.ts); it reads nothing for them.
  liveOn('needs-list', dashNeedsLive);
  liveOn('threads', dashThreadsLive);
  // Away longer than the server keeps: read everything once.
  liveOn('reset', () => { pagesAccessRefresh(); liveSoon('refresh', () => refresh(true), 100); });
  liveStart();
}
// A tab back from sleep or from a dropped network reads the Tasks page's view again, and reconnects the stream after a
// long time away (a phone can drop it while the app is suspended without the page hearing of it).
const TASKS_AWAY_MS = 10 * 60e3;
let tasksHiddenAt = 0;
function tasksBack(away: number): void {
  if (away > TASKS_AWAY_MS) liveResume();
  if (away > 30000 && TASKS_ST && isTasksRoute(S.route)) void tasksCatchUp(TASKS_ST);
}
window.addEventListener('online', () => tasksBack(Infinity));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { tasksHiddenAt = Date.now(); return; }
  if (tasksHiddenAt) tasksBack(Date.now() - tasksHiddenAt);
});
// Back from the browser's page cache: nothing ran meanwhile.
window.addEventListener('pageshow', ev => { if (ev.persisted) tasksBack(Infinity); });
