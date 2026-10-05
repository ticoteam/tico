/* ui/app/bot-tasks.js — Tasks on a keeper bot's page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- tasks on a keeper bot's page: hub.db instead of Issues ----
const V2_ACTIVE = ['open', 'doing', 'waiting'];
const V2_GROUPS = [['doing', 'Doing'], ['waiting', 'Waiting'],
                   ['done', 'Done'], ['declined', 'Declined'], ['closed', 'Closed']];
// Keep titles compact, with status text only when the section does not already name the state.
// Show the avatar of whoever holds the task (your own photo for you).
function taskStateLabel(t) {
  if (taskNeedsMe(t) || t.status === 'declined') return 'Needs you';
  if (t.status === 'waiting') return 'Waiting';
  if (t.status === 'doing' || (t.status === 'open' && !actorPerson(t.owner))) return 'Doing';
  return 'To do';
}
function actorAvatarOnly(a) {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && S.emps.some(e => e.name === slug))
    return `<a class="who-av" href="#/bot/${esc(slug)}" title="${esc(empName(slug))}">${avatar(slug, 22)}</a>`;
  const person = pid === S.me?.id ? (mePerson() || {id: pid, name: S.me?.name}) : (S.people || []).find(p => p.id === pid) || {id: pid, name: actorLabel(a)};
  return `<span class="who-av" title="${esc(pid === S.me?.id ? 'You' : actorLabel(a))}">${personAvatar(person, 22)}</span>`;
}
function v2TaskRow(t, slug, showStatus = true) {
  // On a bot's page the other party is whoever is not the bot; on a person's page (no slug) it is the requester.
  const other = slug && actorSlug(taskRequester(t)) === slug ? t.owner : taskRequester(t);
  return `<div class="trow${showStatus ? '' : ' status-in-heading'}" data-task-detail="${esc(t.id)}" data-task-version="${esc(t.version || '')}"><div class="trow-head">
      ${showStatus ? `<span class="pill ${V2_PILL[t.status] ?? ''}">${esc({open: !actorPerson(t.owner) ? 'Doing · starting' : actorPerson(t.owner) === S.me?.id ? 'Needs you' : 'To do', waiting: 'Waiting', doing: 'Doing'}[t.status] || t.status || '')}</span>${taskStatusText(t)}` : ''}
      <a class="ttl" data-task-detail-open href="#/task/${encodeURIComponent(t.id)}" title="${esc(`${t.title || ''} · ${taskStateLabel(t)} · ${ago(t.updated || t.created)}`)}">${esc(t.title || '')}</a>${prStateBadge(t.pr_state)}
      <span class="tags">${actorChip(other)}</span>${actorAvatarOnly(t.owner)}
      <span class="muted tnum">${esc(ago(t.updated || t.created))}</span></div></div>`;
}
// The rail and person rows open the same complete task as the manager; actor links keep their own destination.
document.addEventListener('click', ev => {
  const row = ev.target.closest('[data-task-detail]');
  if (!row || ev.defaultPrevented || ev.button || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
  const link = ev.target.closest('a');
  if (link && !link.hasAttribute('data-task-detail-open')) return;
  ev.preventDefault();
  void taskModalOpen('t' + row.dataset.taskDetail);
});
function v2TaskGroups(list, slug, statuses) {
  const out = V2_GROUPS.filter(([k]) => statuses.includes(k)).map(([k, label]) => {
    const rows = list.filter(t => (t.status === 'open' ? 'doing' : String(t.status)) === k);
    return rows.length ? `<div class="v2-group"><h3>${esc(label)} <span class="muted">${rows.length}</span></h3>${
      rows.map(t => v2TaskRow(t, slug, false)).join('')}</div>` : '';
  }).filter(Boolean).join('');
  return out;
}
// The Chat tab is where a human works through one bot's requests. Keep every active team task
// that needs the signed-in person directly above that conversation. Attention order leads, then
// the bot's queue rank. Tasks that do not need this person stay out of the way; Done stays in Tasks.
const BOT_CHAT_ACTIVE = new Set(['open', 'doing', 'waiting', 'declined']);
let BOT_CHAT_TASKS = new Map(), BOT_CHAT_TASK_LOAD = 0;
function taskNeedsMe(task) {
  const me = myActor();
  return !!me && (task?.owner === me || task?.ask?.to_actor === me || taskWaitingOn(task) === me);
}
function botChatTasksRender(slug, rows) {
  const host = $('#bot-chat-tasks');
  if (!host || BOT?.slug !== slug) return;
  const needOrder = new Map((S.v2.needs || []).map((item, index) => [String(item.id), index]));
  const needById = new Map((S.v2.needs || []).map(item => [String(item.id), item]));
  BOT_CHAT_TASKS = new Map();
  const ordered = rows.map(task => ({task, need: needById.get(String(task.id))})).sort((a, b) =>
    (needOrder.get(String(a.task.id)) ?? Infinity) - (needOrder.get(String(b.task.id)) ?? Infinity)
    || (a.task.rank == null ? Infinity : Number(a.task.rank)) - (b.task.rank == null ? Infinity : Number(b.task.rank))
    || String(a.task.created || '').localeCompare(String(b.task.created || '')));
  for (const {task} of ordered) BOT_CHAT_TASKS.set(String(task.id), task);
  // Just what needs you, about two lines, scrolling inside, and an X. No heading,
  // pill, age or "all tasks" link. Dismissed stays dismissed for this bot until the items change.
  const key = 'tico.needs.dismissed.' + slug, items = ordered.map(({task}) => String(task.id)).sort().join(',');
  let dismissed = '';
  try { dismissed = localStorage.getItem(key) || ''; } catch {}
  if (!ordered.length || dismissed === items) { host.hidden = true; host.innerHTML = ''; return; }
  host.setAttribute('aria-label', 'What needs you');
  host.innerHTML = `<div class="bot-chat-task-groups">${ordered.map(({task}) =>
      `<button class="bot-chat-task-row" type="button" data-bot-chat-task="${esc(task.id)}">${esc(task.title || 'Untitled task')}</button>`).join('')}</div>
    <button class="bot-chat-tasks-x" type="button" aria-label="Dismiss" title="Dismiss">×</button>`;
  host.hidden = false;
  host.onclick = event => {
    if (event.target.closest('.bot-chat-tasks-x')) {
      try { localStorage.setItem(key, items); } catch {}
      host.hidden = true;
      return;
    }
    const button = event.target.closest('[data-bot-chat-task]');
    if (!button) return;
    const task = BOT_CHAT_TASKS.get(button.dataset.botChatTask);
    if (task) taskModalShow(task);
  };
}
async function loadBotChatTasks(slug) {
  const host = $('#bot-chat-tasks');
  if (!host) return;
  const load = ++BOT_CHAT_TASK_LOAD;
  const [owned, asked] = await Promise.all([
    v2Get(`/v2/tasks?owner=${encodeURIComponent(slug)}&status=all`),
    v2Get(`/v2/tasks?requester=${encodeURIComponent(slug)}&status=all`)]);
  if (load !== BOT_CHAT_TASK_LOAD || BOT?.slug !== slug || !$('#bot-chat-tasks')) return;
  const unique = new Map();
  for (const task of [...(owned?.tasks || []), ...(asked?.tasks || [])]) {
    if (BOT_CHAT_ACTIVE.has(String(task.status)) && (task.lane || 'company') === 'company' && taskNeedsMe(task)) unique.set(String(task.id), task);
  }
  botChatTasksRender(slug, [...unique.values()]);
}
// Active is the bot's own work; "Assigned to others" is what it asked of people
// and other bots (a task it filed for you shows here, and its number in the org tree). Both have
// a count; Recurring comes last. A failed load is retried, never shown as an empty list.
async function loadBotTasksV2(slug) {
  const pane = $('#t-open'); if (!pane) return;
  const seen = BOT_TASKS_CACHE.get(slug);
  if (seen) botTasksRender(slug, seen.owned, seen.asked);   // last time's list at once; the fresh one follows
  let owned = null, asked = null;
  for (const wait of [0, 600, 2000]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (!$('#t-open') || BOT?.slug !== slug) return;
    [owned, asked] = await Promise.all([
      owned || v2Get(`/v2/tasks?owner=${encodeURIComponent(slug)}&status=all`),
      asked || v2Get(`/v2/tasks?requester=${encodeURIComponent(slug)}&status=all`)]);
    if (owned && asked) break;
  }
  if (!$('#t-open')) return;
  if (!owned || !asked) {
    if (!seen) $('#t-open').innerHTML = '<div class="err">Could not load the tasks. They will show on the next refresh.</div>';
    return;
  }
  BOT_TASKS_CACHE.set(slug, {owned, asked});
  botTasksRender(slug, owned, asked);
}
function botTasksRender(slug, owned, asked) {
  if (!$('#t-open') || BOT?.slug !== slug) return;
  const mine = owned.tasks || [], theirs = (asked.tasks || []).filter(t => actorSlug(t.owner) !== slug);
  const latest = (a, b) => String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || ''));
  const active = mine.filter(t => V2_ACTIVE.includes(String(t.status))).sort(latest);
  // Done: the bot's own work, most recently done first. A routine's runs (a sweep every 30
  // minutes) are not work a person asked for; they live under Recurring.
  const doneAt = t => String(t.done_at || t.closed_at || t.updated || '');
  const finished = mine.filter(t => !V2_ACTIVE.includes(String(t.status)) && !isRecurringTask(t))
    .sort((a, b) => doneAt(b).localeCompare(doneAt(a)));
  $('#cnt-done').textContent = finished.length || '';
  // What it asked of others: whatever waits on a person first, then by the latest change.
  const needsPerson = t => actorPerson(t.owner) ? 0 : 1;
  const outward = theirs.filter(t => V2_ACTIVE.includes(String(t.status))).sort((a, b) => needsPerson(a) - needsPerson(b) || latest(a, b));
  $('#t-open').innerHTML = active.length ? `<div class="bot-task-list">${active.map(t => v2TaskRow(t, slug)).join('')}</div>`
    : '<div class="rail-empty">None</div>';
  // Folded at the foot of the rail; it opens by itself while something in it waits on a person.
  const assigned = $('#bot-assigned');
  if (assigned) {
    assigned.hidden = !outward.length;
    $('#cnt-assigned').textContent = outward.length || '';
    $('#t-assigned').innerHTML = `<div class="bot-task-list">${outward.map(t => v2TaskRow(t, slug)).join('')}</div>`;
    if (!assigned.dataset.seen && outward.length) { assigned.dataset.seen = '1'; assigned.open = outward.some(t => actorPerson(t.owner)); }
  }
  $('#t-done').innerHTML = finished.length
    ? `<div class="bot-task-list">${finished.map(t => v2TaskRow(t, slug, false)).join('')}</div>`
    : '<div class="rail-empty">None</div>';
}

// the pill's "Send as task" on a keeper bot: a hub task, not an Issue
async function v2PillTask(P, text) {
  if (P.sending) return;
  P.sending = true;
  const files = P.files.slice();
  const btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Creating…');
  try {
    await cloudCompose('/v2/tasks', {title: text.split('\n')[0].trim().slice(0, 80), body: text, owner: P.slug}, files);
    pillAcknowledge(P, text, files);
    toast(`Task for ${empName(P.slug)} created`);
    await v2Refresh();
    if (BOT?.slug === P.slug) {
      void loadBotTasksV2(P.slug);
      void loadBotChatTasks(P.slug);
    }
  } catch (e) { toast(e.message, true); }
  P.sending = false;
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
