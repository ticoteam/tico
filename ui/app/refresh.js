/* ui/app/refresh.js — The refresh loop
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- refresh loop + router
// Recruiting seats change only by a recruit flow here (each refreshes with force) or another person's: a poll reads
// them every 5th time, or when the last answer is 10 minutes old.
let SEATS_POLLS = 0, SEATS_AT = 0;
async function refresh(force) {
  // Every read at once: hub.db status and needs-you (docs/history/hub-v2.md) and the campus's computers (only while it
  // is open) do not wait for the roster.
  const v2 = v2Refresh(), computers = overviewLoadComputers();
  try {
    // BotOps and other sessions can change the chart. Publish a complete roster together so groups and members agree.
    // GitHub Issues are retired: /api/issues always answers [], so S.issues stays empty and is not read.
    const seatsDue = force || S.seats === undefined || ++SEATS_POLLS % 5 === 0 || Date.now() - SEATS_AT > 600e3;
    const [st, emps, people, seats] = await Promise.allSettled([get('/status'), get('/employees'), get('/humans'),
      seatsDue ? get('/v2/seats') : null]);
    S.status = st.status === 'fulfilled' ? st.value : null;
    S.statusPending = false;
    cloudAccessFromStatus();
    if (emps.status === 'fulfilled' && people.status === 'fulfilled' && people.value?.people) {
      S.emps = namedRoster(emps.value);
      setPeople(people.value);
      S.overviewRosterFresh = true;
    } else S.overviewRosterFresh = false;
    if (seatsDue && seats.status === 'fulfilled') { S.seats = seats.value?.seats || []; SEATS_AT = Date.now(); }   // Recruiting seats (backend/lifecycle.py)
  } catch (e) { S.overviewRosterFresh = false; }
  await Promise.all([v2, computers]);
  overviewRefresh();
  if (S.me?.cloud) void updUnreadRefresh();
  renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync(); goalsFollowRoster();
  tipRefresh();
  taskBuilderNoticesRefresh();
  if (BOT?.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
  botAlertDraw();
  if (BOT) botScreenRefresh(BOT.slug);   // the Screen row and header button follow the computer (online, screen offered)
  if (BOT && $('#bot-onboard-host') && ($('#bot-onboard') ? '1' : '') !== (frNeedsSetup(botRow(BOT.slug)) ? '1' : '')) frBotRefresh(BOT.slug);   // the mark clears when the bot says it is set up
  // Poll data without destroying an expanded document or a comment being typed (one inside a closed card is not being
  // written in).
  const reviewing = $('#main .req[open]') || [...document.querySelectorAll('#main .issue-compose:not([hidden])')].some(form => !form.closest('details:not([open])'));
  if (force || !reviewing) {
    // Only what changed since the list's cursor (not Done's pages); only changed rows redraw. A full read on its way
    // (tasksReadFull) brings all of it.
    if (TASKS_ST && isTasksRoute(S.route) && !TASKS_ST.fullRead) void tasksLoad(TASKS_ST, {poll: true});
    // The Active column beside the chat was loaded once and never again, so a task the bot closed
    // stayed listed until a reload. The poll redraws it too.
    if (BOT && isKeeper(BOT.slug) && BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
  }
}

// ----------------------------------------------------------------- live events (ui/app/live.js)
// What the 30 s refresh used to find by reading everything again now arrives as it changes: a task moved (the open
// board, the bot's columns and ticker, a person's tasks, the open task), a bot's status line (the tree, the
// heartbeat, the paused notice, the Overview's desks), and Needs you (the tree's counts). Each surface redraws once
// per burst.
// The same JSON value, whatever order its keys come in (a live event's copy of a task is built apart from the list's).
function sameData(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every(k => Object.hasOwn(b, k) && sameData(a[k], b[k]));
}
// What a list shows of a task: a link's own write time is not on any row, and a computer's heartbeat on a worktree
// changes only that, once a minute per tree.
const taskListCopy = t => Array.isArray(t?.links) ? {...t, links: t.links.map(({updated_at: updated, ...link}) => link)} : t;
function liveTasksApply(events) {
  const state = TASKS_ST;
  // One change to many tasks (a type or tag edit) comes as one `bulk` event: the open list reads in full once (its
  // cursor resets), and the other lists below reload as for any change.
  const bulk = events.some(d => d.bulk);
  const unchanged = new Set();
  if (bulk && state && isTasksRoute(S.route)) liveThrottle('tasks-bulk', () => { if (TASKS_ST === state) void tasksLoad(state); }, 5000);
  // The open list, or away from it the list kept for the next visit (S.lastRead.tasks): kept current the same way, so a
  // return paints what is true now. A bulk change drops the kept list; the next visit reads it.
  if (bulk && !state) S.lastRead.tasks = null;
  const list = state && isTasksRoute(S.route) && state.tasks ? state : !state ? S.lastRead.tasks : null;
  if (list) {
    const known = new Map(list.tasks.map((t, i) => [String(t.id), i]));
    let changed = false;
    for (const d of events) {
      const i = known.get(String(d.id));
      if (d.gone) { if (i != null) { list.tasks = list.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); list.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      if (!d.task) continue;
      if ((d.task.lane || 'company') !== 'company') { if (i != null) { list.tasks = list.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); list.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      // A write that shows nothing new on the list: no redraw. The open task follows any change (a worktree's
      // progress shows there), so only an identical copy is left out of that.
      if (i != null && sameData(taskListCopy(list.tasks[i]), taskListCopy(d.task))) {
        if (sameData(list.tasks[i], d.task)) unchanged.add(String(d.id));
        else list.tasks = list.tasks.map((t, j) => j === i ? d.task : t);
        continue;
      }
      if (i != null) list.tasks = list.tasks.map((t, j) => j === i ? d.task : t);
      else { list.tasks = [...list.tasks, d.task]; known.set(String(d.id), list.tasks.length - 1); }   // a new task arrives whole
      changed = true;
    }
    // Never the whole list again: every open tab gets every change, and a busy team's bots change tasks every few
    // seconds. The 2-minute refresh catches up from the list's cursor.
    if (changed && list === state) { tasksTools(state); tasksRender(state); }
  }
  // These read whole lists, so at most every 15 s and not in a hidden tab (liveThrottle).
  if (BOT && isKeeper(BOT.slug)) {
    const me = 'bot:' + BOT.slug;
    if (events.some(d => d.bulk || d.gone || d.task?.owner_id === me || d.task?.requester_id === me)) liveThrottle('bot-tasks', () => {
      if (!BOT || BOT.slug !== me.slice(4)) return;
      if (BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
      if (BOT.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
    });
  }
  if (typeof PERSON_TASKS !== 'undefined' && PERSON_TASKS) {
    const me = 'human:' + PERSON_TASKS;
    if (events.some(d => d.bulk || d.gone || d.task?.owner_id === me)) liveThrottle('person-tasks', personTasksReload);
  }
  if (TASK_CHAT && events.some(d => d.bulk || String(d.id) === String(TASK_CHAT.id) && !unchanged.has(String(d.id)))) taskChatLive(TASK_CHAT);
}
function liveWire() {
  if (!S.me?.cloud || !liveAvailable()) return;
  let tasks = [];
  liveOn('tasks', d => { tasks.push(d); liveSoon('tasks', () => { const batch = tasks; tasks = []; liveTasksApply(batch); }, 200); });
  liveOn('bots', d => {
    if (d.status) S.v2.status[d.bot] = d.status; else delete S.v2.status[d.bot];
    tipRefresh(d.bot);
    liveSoon('bots', () => {
      renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync(); goalsFollowRoster();
      tipRefresh();
      taskBuilderNoticesRefresh();
      botAlertDraw();
      overviewRefresh();                // the campus desks too, not only at the next refresh (no read: the model is local)
    }, 200);
  });
  // Needs you comes as the ids that changed; the list is mended from them (v2NeedsChanged), then `needs-list` says so.
  liveOn('needs', d => void v2NeedsChanged(d).then(moved => {
    if (!moved) return;
    liveEmit('needs-list', S.v2.needs || []);
    liveSoon('needs', () => {
      renderTree();
      if (OVERVIEW_PAGE) overviewNeedsDraw();
      if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
      if (BOT && isKeeper(BOT.slug) && BOT.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
      if (BOT && isKeeper(BOT.slug)) botNeedsChanged(BOT.slug);
    }, 200);
  }).catch(() => {}));
  // Away longer than the server keeps: read everything once.
  liveOn('reset', () => { if (TASKS_ST) TASKS_ST.since = null; liveSoon('refresh', () => refresh(true), 100); });
  liveStart();
}
// A tab back from sleep or from a dropped network catches its task list up at once: a delta read from the list's
// cursor (tasksLoad), which costs only what changed while it was away, or a full read when the list has no cursor (its
// paging stopped part-way, or its last read failed).
let tasksHiddenAt = 0;
function tasksCatchUp() {
  if (TASKS_ST && isTasksRoute(S.route)) liveSoon('tasks-catch-up', () => { if (TASKS_ST && !TASKS_ST.fullRead) void tasksLoad(TASKS_ST, {poll: true}); }, 300);
}
// The open list never drifts for long: it is read in full (tasksReadFull) at least every 15 minutes while it is on
// screen, and at once when the page comes back after more than 10 minutes away. A delta read and the live stream bring
// only what the change log names, and a phone can drop the stream while the app is suspended without the page hearing
// of it, so it is reconnected then too. "Away" is hidden, or asleep: a suspended app's timers stop without the page
// being hidden first, so a gap between two ticks of the 30-second clock counts as well.
const TASKS_FULL_MS = 15 * 60e3, TASKS_AWAY_MS = 10 * 60e3;
let tasksTickAt = Date.now();
function tasksFreshen() {
  const state = TASKS_ST;
  if (!state || !isTasksRoute(S.route) || document.hidden || state.fullRead) return;
  if (!state.stale && Date.now() - (state.fullAt || 0) < TASKS_FULL_MS) return;
  if (formBusy($('#task-body'))) return;     // someone writing in the list: the next tick looks again
  void tasksReadFull(state, {poll: true});
}
// The stream is reconnected once per return, not on each tick a failing full read leaves the list stale: that would
// undo the stream's own backoff while the server is down.
function tasksBack(away) {
  if (away > TASKS_AWAY_MS) { liveResume(); if (TASKS_ST) TASKS_ST.stale = true; tasksFreshen(); }
  else if (away > 30000) tasksCatchUp();
}
function tasksTick() {
  const now = Date.now(), slept = now - tasksTickAt;
  tasksTickAt = now;
  if (document.hidden) return;
  if (slept > TASKS_AWAY_MS) tasksBack(slept); else tasksFreshen();
}
setInterval(tasksTick, 30000);
window.addEventListener('online', tasksCatchUp);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { tasksHiddenAt = Date.now(); return; }
  tasksTickAt = Date.now();
  if (tasksHiddenAt) tasksBack(Date.now() - tasksHiddenAt);
});
// Back from the browser's page cache: nothing ran meanwhile.
window.addEventListener('pageshow', ev => { if (ev.persisted) tasksBack(Infinity); });
