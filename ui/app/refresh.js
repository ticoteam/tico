/* ui/app/refresh.js — The refresh loop
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- refresh loop + router
async function refresh(force) {
  try {
    // BotOps and other sessions can change the chart. Publish a complete roster together so groups and members agree.
    const [st, issues, emps, people] = await Promise.allSettled([get('/status'), get('/issues'), get('/employees'), get('/humans')]);
    S.status = st.status === 'fulfilled' ? st.value : null;
    S.statusPending = false;
    if (issues.status === 'fulfilled') S.issues = issues.value.map(i => {
      if (!pendingClosedIssues.has(i.number)) return i;
      if (i.state === 'CLOSED') pendingClosedIssues.delete(i.number);
      return {...i, state: 'CLOSED', needs_human: false};
    });
    if (emps.status === 'fulfilled' && people.status === 'fulfilled' && people.value?.people) {
      S.emps = namedRoster(emps.value);
      setPeople(people.value);
      S.overviewRosterFresh = true;
    } else S.overviewRosterFresh = false;
  } catch (e) { S.overviewRosterFresh = false; }
  await v2Refresh();                    // hub.db status and needs-you (docs/history/hub-v2.md)
  await overviewLoadComputers();        // Only while the campus is open; share this refresh clock.
  overviewRefresh();
  if (S.me?.cloud) void updUnreadRefresh();
  renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync();
  if (BOT?.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
  if (BOT && $('#bot-alert')) $('#bot-alert').innerHTML = botAlertHTML(BOT.slug);   // an alert comes and goes with the poll
  if (BOT && $('#bot-onboard-host') && ($('#bot-onboard') ? '1' : '') !== (frNeedsSetup(S.emps.find(x => x.name === BOT.slug)) ? '1' : '')) frBotRefresh(BOT.slug);   // the mark clears when the bot says it is set up
  // Poll data without destroying an expanded document or a comment being typed.
  const reviewing = $('#main .req[open], #main .issue-compose:not([hidden])');
  if (force || !reviewing) {
    if (TASKS_ST && isTasksRoute(S.route)) void tasksLoad(TASKS_ST, {poll: true});   // only what changed since the list's cursor (not Done's pages); only changed rows redraw
    // The Active column beside the chat was loaded once and never again, so a task the bot closed
    // stayed listed until a reload. The poll redraws it too.
    if (BOT && isKeeper(BOT.slug) && BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
  }
}

// ----------------------------------------------------------------- live events (ui/app/live.js)
// What the 30 s refresh used to find by reading everything again now arrives as it changes: a task moved (the open
// board, the bot's columns and ticker, a person's tasks, the open task), a bot's status line (the tree, the
// heartbeat, the paused notice), and Needs you (the tree's counts). Each surface redraws once per burst.
function liveTasksApply(events) {
  const state = TASKS_ST;
  // One change to many tasks (a type or tag edit) comes as one `bulk` event: the open list reads in full once (its
  // cursor resets), and the other lists below reload as for any change.
  const bulk = events.some(d => d.bulk);
  if (bulk && state && isTasksRoute(S.route)) liveThrottle('tasks-bulk', () => { if (TASKS_ST === state) void tasksLoad(state); }, 5000);
  if (state && isTasksRoute(S.route) && state.tasks) {
    const known = new Map(state.tasks.map((t, i) => [String(t.id), i]));
    let changed = false;
    for (const d of events) {
      const i = known.get(String(d.id));
      if (d.gone) { if (i != null) { state.tasks = state.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); state.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      if (!d.task) continue;
      if ((d.task.lane || 'company') !== 'company') { if (i != null) { state.tasks = state.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); state.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      if (i != null) state.tasks = state.tasks.map((t, j) => j === i ? d.task : t);
      else { state.tasks = [...state.tasks, d.task]; known.set(String(d.id), state.tasks.length - 1); }   // a new task arrives whole
      changed = true;
    }
    // Never the whole list again: every open tab gets every change, and a busy team's bots change tasks every few
    // seconds. The 2-minute refresh catches up from the list's cursor.
    if (changed) { tasksTools(state); tasksRender(state); }
  }
  // These read whole lists, so at most every 15 s and not in a hidden tab (liveThrottle).
  if (BOT && isKeeper(BOT.slug)) {
    const me = 'bot:' + BOT.slug;
    if (events.some(d => d.bulk || d.gone || d.task?.owner === me || d.task?.requester === me)) liveThrottle('bot-tasks', () => {
      if (!BOT || BOT.slug !== me.slice(4)) return;
      if (BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
      if (BOT.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
    });
  }
  if (typeof PERSON_TASKS !== 'undefined' && PERSON_TASKS) {
    const me = 'human:' + PERSON_TASKS;
    if (events.some(d => d.bulk || d.gone || d.task?.owner === me)) liveThrottle('person-tasks', personTasksReload);
  }
  if (TASK_CHAT && events.some(d => d.bulk || String(d.id) === String(TASK_CHAT.id))) taskChatLive(TASK_CHAT);
}
function liveWire() {
  if (!S.me?.cloud || !liveAvailable()) return;
  let tasks = [];
  liveOn('tasks', d => { tasks.push(d); liveSoon('tasks', () => { const batch = tasks; tasks = []; liveTasksApply(batch); }, 200); });
  liveOn('bots', d => {
    if (d.status) S.v2.status[d.bot] = d.status; else delete S.v2.status[d.bot];
    liveSoon('bots', () => {
      renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync();
      if (BOT && $('#bot-alert')) $('#bot-alert').innerHTML = botAlertHTML(BOT.slug);
    }, 200);
  });
  liveOn('needs', d => {
    S.v2.needs = d.items || [];
    liveSoon('needs', () => {
      renderTree();
      if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
      if (BOT && isKeeper(BOT.slug) && BOT.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
    }, 200);
  });
  // Away longer than the server keeps: read everything once.
  liveOn('reset', () => { if (TASKS_ST) TASKS_ST.cursor = null; liveSoon('refresh', () => refresh(true), 100); });
  liveStart();
}
// A tab back from sleep or from a dropped network catches its task list up at once: a delta read from the list's
// cursor (tasksLoad), which costs only what changed while it was away.
let tasksHiddenAt = 0;
function tasksCatchUp() {
  if (TASKS_ST?.cursor && isTasksRoute(S.route)) liveSoon('tasks-catch-up', () => { if (TASKS_ST) void tasksLoad(TASKS_ST, {poll: true}); }, 300);
}
window.addEventListener('online', tasksCatchUp);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { tasksHiddenAt = Date.now(); return; }
  if (tasksHiddenAt && Date.now() - tasksHiddenAt > 30000) tasksCatchUp();
});
