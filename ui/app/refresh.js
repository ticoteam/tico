/* ui/app/refresh.js — The refresh loop
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- refresh loop + router
async function refresh(force) {
  try {
    // BotOps and other sessions can change the chart. Publish a complete roster together so groups and members agree.
    const [st, issues, emps, people] = await Promise.all([get('/status'), get('/issues'), get('/employees'), get('/humans')]);
    S.status = st;
    S.issues = issues.map(i => {
      if (!pendingClosedIssues.has(i.number)) return i;
      if (i.state === 'CLOSED') pendingClosedIssues.delete(i.number);
      return {...i, state: 'CLOSED', needs_human: false};
    });
    S.emps = namedRoster(emps);
    if (people?.people) setPeople(people);
    S.overviewRosterFresh = true;
  } catch (e) { S.status = null; S.overviewRosterFresh = false; }
  await v2Refresh();                    // hub.db status and needs-you (docs/history/hub-v2.md)
  await overviewLoadComputers();        // Only while the campus is open; share this refresh clock.
  overviewRefresh();
  if (S.me?.cloud) void updUnreadRefresh();
  renderTree(); renderHeartbeat(); pausedRender(); botAvatarsSync();
  if (BOT?.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
  if (BOT && $('#bot-alert')) $('#bot-alert').innerHTML = botAlertHTML(BOT.slug);   // an alert comes and goes with the poll
  if (BOT && $('#bot-onboard-host') && ($('#bot-onboard') ? '1' : '') !== (frNeedsSetup(S.emps.find(x => x.name === BOT.slug)) ? '1' : '')) frBotRefresh(BOT.slug);   // the mark clears when the bot says it is set up
  if (BOT && $('#bot-ticker') && isKeeper(BOT.slug)) void botTickerLoad(BOT.slug);
  // Poll data without destroying an expanded document or a comment being typed.
  const reviewing = $('#main .req[open], #main .issue-compose:not([hidden])');
  if (force || !reviewing) {
    if (TASKS_ST && isTasksRoute(S.route)) void tasksLoad(TASKS_ST, {poll: true});   // the open list reloads (not Done's pages); only changed rows redraw
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
  if (state && isTasksRoute(S.route) && state.tasks) {
    const known = new Map(state.tasks.map((t, i) => [String(t.id), i]));
    let unknown = false, changed = false;
    for (const d of events) {
      const i = known.get(String(d.id));
      if (d.gone) { if (i != null) { state.tasks = state.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); state.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      if (!d.task) continue;
      if ((d.task.lane || 'company') !== 'company') { if (i != null) { state.tasks = state.tasks.filter(t => String(t.id) !== String(d.id)); known.clear(); state.tasks.forEach((t, j) => known.set(String(t.id), j)); changed = true; } continue; }
      if (i != null) { state.tasks = state.tasks.map((t, j) => j === i ? d.task : t); changed = true; }
      else unknown = true;                // a new task, or one this page has not loaded: the list reads again
    }
    if (unknown) void tasksLoad(state, {poll: true});
    else if (changed) { tasksTools(state); tasksRender(state); }
  }
  if (BOT && isKeeper(BOT.slug)) {
    const me = 'bot:' + BOT.slug;
    if (events.some(d => d.gone || d.task?.owner === me || d.task?.requester === me)) {
      if (BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
      if (BOT.tab === 'chat' && !BOT.split) void loadBotChatTasks(BOT.slug);
      if ($('#bot-ticker')) void botTickerLoad(BOT.slug);
    }
  }
  if (typeof PERSON_TASKS !== 'undefined' && PERSON_TASKS) personTasksReload();
  if (TASK_CHAT && events.some(d => String(d.id) === String(TASK_CHAT.id))) taskChatLive(TASK_CHAT);
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
  liveOn('reset', () => liveSoon('refresh', () => refresh(true), 100));
  liveStart();
}
