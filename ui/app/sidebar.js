/* ui/app/sidebar.js — The org tree, message-bot nav, org history, collapsible sections
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- sidebar + heartbeat
const NAV_COLLAPSED_KEY = 'hub.nav.collapsed';
let navCollapsed = new Set(); try { navCollapsed = new Set(JSON.parse(localStorage.getItem(NAV_COLLAPSED_KEY) || '[]')); } catch {}
function renderNavSections() {
  document.querySelectorAll('[data-section-toggle]').forEach(button => {
    const key = button.dataset.sectionToggle, isCollapsed = navCollapsed.has(key);
    button.setAttribute('aria-expanded', String(!isCollapsed));
    const body = document.querySelector(`[data-section-body="${key}"]`);
    if (body) body.hidden = isCollapsed;
  });
}
document.querySelectorAll('[data-section-toggle]').forEach(button => button.onclick = () => {
  const key = button.dataset.sectionToggle;
  navCollapsed.has(key) ? navCollapsed.delete(key) : navCollapsed.add(key);
  try { localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify([...navCollapsed])); } catch {}
  renderNavSections();
});
renderNavSections();
// The built-ins stay out of the team chart. The Assistant and BotOps have their own rows in the main rail (each
// person's private Assistant is its own page, ui/app/assistant-page.js); the Goal Manager is on Goals and the
// Librarian on Docs and Market. Search, bot pickers and a person's page do not list the Assistant or the Librarian;
// Settings > Bots manages all four, and #/bot/<slug> opens each.
const isHiddenBot = slug => slug === assistantBot() || slug === 'librarian';
const shownEmps = () => (S.emps || []).filter(e => !isHiddenBot(e.name));
// Groups first: a human or a bot hangs in the group its `team` names (backend/groups.py); groups nest by `parent`. Inside
// a group a teammate hangs under whoever it reports to when that one is in the same group, else at the top of the group.
// Outside groups it hangs under whoever it reports to, when that one is outside groups too. `reports_to` itself is not
// touched by any of this.
// Branches are personal workspaces, not additional teammates. Admin access still belongs in the branch picker.
const orgBranchVisible = e => !(e.shared_from || e.is_branch) || (!!S.me?.id && e.operator === S.me.id);
const orgBranchMark = e => (e.shared_from || e.is_branch) ? '<span class="org-branch">Your branch</span>' : '';
const orgGroupIds = () => new Set((S.orgGroups || []).map(g => g.id));
const orgCanGroups = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Who a human or bot reports to, as a key (`p:<id>` or `b:<slug>`); '' when nobody is on the chart above it.
function orgBossKey(n) {
  const parent = (n.kind === 'person' ? n.person?.org_parent : n.org_parent) || '';
  if (!parent.startsWith('g:')) return parent;
  const boss = (S.orgGroups || []).find(g => g.id === parent.slice(2))?.reports_to;    // a group from before groups hung under a person
  return boss ? 'p:' + boss : '';
}
function orgHang(key, boss, groupOf) {
  const group = groupOf[key] || '';
  if (group) return boss && groupOf[boss] === group ? boss : 'g:' + group;
  return boss && !groupOf[boss] ? boss : '';
}
function orgGroupOf(people, bots) {
  const groups = orgGroupIds(), out = {};
  for (const p of people) if (groups.has(p.team)) out['p:' + p.id] = p.team;
  for (const e of bots) if (groups.has(e.team) && !isHelperBot(e)) out['b:' + e.name] = e.team;
  return out;
}
function orgTreeByParent() {
  const byParent = {}, groups = orgGroupIds();
  const people = (S.people || []).filter(p => !p.hidden);
  // "Only bots I can read or write": a bot the caller may merely see leaves the chart, and the
  // bots under it hang from the next thing that is left.
  // Built-in and message bots sit apart, as on Goals; a bot that reports to one hangs where it would have.
  const bots = (S.emps || []).filter(e => orgBranchVisible(e) && orgMineKeep(e));
  const empIds = new Set(bots.map(e => e.name));
  const groupOf = orgGroupOf(people, bots);
  for (const g of (S.orgGroups || [])) {
    (byParent[g.parent && groups.has(g.parent) ? 'g:' + g.parent : ''] ||= []).push({kind: 'group', id: g.id, name: g.name, order: g.order || 0});
  }
  for (const p of people) {
    (byParent[orgHang('p:' + p.id, orgBossKey({kind: 'person', person: p}), groupOf)] ||= []).push({kind: 'person', id: p.id, person: p});
  }
  for (const e of bots) {
    let boss = orgBossKey({kind: 'bot', ...e});
    if (boss.startsWith('b:') && !empIds.has(boss.slice(2))) {
      const owner = e.operator || e.users?.[0]?.id;
      boss = owner ? 'p:' + owner : '';
    }
    (byParent[orgHang('b:' + e.name, boss, groupOf)] ||= []).push({kind: 'bot', ...e});
  }
  return byParent;
}
function mePerson() {
  const id = S.me?.id, email = (S.me?.email || '').toLowerCase();
  return (S.people || []).find(p => p.id === id) ||
         (S.people || []).find(p => (p.email || '').toLowerCase() === email) || null;
}
function inboxNavPeople() {
  const bots = new Set((S.emps || []).map(e => e.name));
  return (S.people || []).filter(p => p.email && p.inbox_bot && !p.hidden && bots.has(p.inbox_bot))
    .filter(p => S.me?.role === 'owner' || mailPersonVisible(p))
    .sort((a, b) => String(a.email).localeCompare(String(b.email)));
}
let INBOX_NAV = null;
const SLACK_ICON = `<svg class="inbox-slack" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zm-1.268 0a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zm0-1.268a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"/></svg>`;
function inboxNavRows() {
  const mail = inboxNavPeople().map(p => ({
    kind: 'email', label: p.email, title: p.name ? `${p.name} · ${p.email}` : p.email,
    bot: p.inbox_bot, source: 'email:' + p.email,
  }));
  const slack = [];
  for (const bot of INBOX_NAV?.bots || []) {
    for (const source of bot.sources || []) {
      if (source.kind !== 'slack') continue;
      // A chart bot's channels are one of its Tools (its page lists them); only a message bot's channels are its inboxes.
      const shown = S.emps.find(e => e.name === bot.bot);
      if (shown && !(isHelperBot(shown) && !isBuiltInBot(shown.name))) continue;
      slack.push({kind: 'slack', label: source.name, title: `${bot.name} · ${source.name}`,
                  bot: bot.bot, source: source.id});
    }
  }
  slack.sort((a, b) => a.label.localeCompare(b.label));
  return [...mail, ...slack];
}
// The mailboxes and Slack channels a message bot works hang under that bot in the team list (renderTree), in the
// Message bots group; a chart bot's Slack channels are listed under Slack in its Tools instead. The section below the team list holds only the ones
// whose bot is not shown there (the recently-viewed list, or "Only bots I can read or write").
let INBOX_PLACED = new Set();
function inboxNavLink(row) {
  const here = messagingParams();
  const href = `${MESSAGING}?bot=${encodeURIComponent(row.bot)}&source=${encodeURIComponent(row.source)}`;
  const on = S.route.startsWith(MESSAGING) && here.bot === row.bot && here.source === row.source;
  const icon = row.kind === 'slack' ? SLACK_ICON : `<span class="nav-icon inbox-kind" aria-hidden="true">mail</span>`;
  return `<li><a href="${href}" title="${esc(row.title)}"${on ? ' class="cur" aria-current="page"' : ''}>${icon}<span>${esc(row.label)}</span></a></li>`;
}
function renderInboxNav() {
  const section = $('#nav-inboxes-section'), list = $('#inbox-list');
  if (!section || !list) return;
  if (INBOX_NAV === null) {
    INBOX_NAV = false;
    get('/v2/messaging/bots').then(data => { INBOX_NAV = data; renderTree(); })
      .catch(() => { INBOX_NAV = {bots: []}; renderTree(); });
  }
  const rows = inboxNavRows().filter(row => !INBOX_PLACED.has(row.bot));
  section.hidden = !rows.length;
  list.innerHTML = rows.map(inboxNavLink).join('');
}
function renderLibrarians() {
  for (const [id, slug, href] of [
    ['nav-assistant', assistantBot(), mePerson() ? ASSISTANT : `#/bot/${encodeURIComponent(assistantBot())}`],
    ['nav-botops', 'botops', '#/bot/botops'],
  ]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.href = href;
    el.hidden = !(S.emps || []).some(e => e.name === slug);
  }
  for (const [id, slug] of [['nav-docs-librarian', 'librarian'], ['nav-market-librarian', 'market-analyst']]) {
    const el = document.getElementById(id);
    if (!el) continue;
    const bot = (S.emps || []).find(e => e.name === slug && e.status !== 'archived' && e.status !== 'retired');
    el.hidden = !bot;
    if (!bot) continue;
    const name = bot.display_name || slug;
    el.href = `#/bot/${encodeURIComponent(slug)}/more`;
    el.title = `${name} settings`;
    el.setAttribute('aria-label', `${name} settings`);
    const on = S.route.startsWith('#/bot/' + slug);
    el.classList.toggle('cur', on);
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  }
}
function mailPersonVisible(person) {
  if (!S.me?.mail_access || !person?.email || !person.inbox_bot ||
      !(S.emps || []).some(e => e.name === person.inbox_bot)) return false;
  if (S.me.role === 'owner') return true;
  const me = mePerson();
  if (!me) return (person.email || '').toLowerCase() === (S.me.email || '').toLowerCase();
  let current = person;
  const seen = new Set();
  while (current && !seen.has(current.id)) {
    if (current.id === me.id) return true;
    seen.add(current.id);
    current = (S.people || []).find(p => p.id === current.reports_to);
  }
  return false;
}
function defaultCollapsed() {
  const by = orgTreeByParent(), keys = new Set();
  const founder = (by[''] || []).find(n => n.kind === 'person' && !n.person?.reports_to);
  for (const k of Object.keys(by)) {
    if (!k || !by[k].length) continue;
    if (k.startsWith('g:')) continue;
    if (founder && k === 'p:' + founder.id) continue;
    keys.add(k);
  }
  return keys;
}
let collapsed = new Set(), collapsedFor = null, collapsedSaved = false;
function collapsedStorageKey() {
  return 'hub.collapsed.v3.' + (mePerson()?.id || S.me?.id || '');
}
function ensureCollapsed() {
  const id = mePerson()?.id || S.me?.id || '';
  if (id !== collapsedFor) {
    collapsedFor = id;
    collapsedSaved = false;
    try {
      const saved = id && localStorage.getItem(collapsedStorageKey());
      if (saved) { collapsed = new Set(JSON.parse(saved)); collapsedSaved = true; return; }
    } catch {}
    collapsed = defaultCollapsed();
    return;
  }
  if (!collapsedSaved) collapsed = defaultCollapsed();
}
function saveCollapsed() {
  collapsedSaved = true;
  const id = mePerson()?.id || S.me?.id || '';
  if (!id) return;
  try { localStorage.setItem(collapsedStorageKey(), JSON.stringify([...collapsed])); } catch {}
}
function setPeople(data) {
  if (!data) return;
  if (Array.isArray(data.people)) S.people = data.people;
  if (data.org_groups) S.orgGroups = data.org_groups;
}
// Rearranging the org chart: drag a person or a bot onto another person or bot. The owner may
// move anyone; anyone else may move what reports up to them, and only under themselves or their
// own reports — the server holds that rule (backend/people.py `manages`), the client just offers
// the handles for what it knows is yours.
function orgMine() {
  const mine = new Set();
  if (S.me?.role === 'owner') return null;                       // everything
  const me = mePerson()?.id || S.me?.id; if (!me) return mine;
  const under = {};                                              // who reports to each one, whatever group they are in
  const add = (key, n) => { const boss = orgBossKey(n); if (boss) (under[boss] ||= []).push(key); };
  for (const p of S.people || []) add('p:' + p.id, {kind: 'person', person: p});
  for (const e of S.emps || []) add('b:' + e.name, {kind: 'bot', ...e});
  const walk = key => { for (const k of under[key] || []) if (!mine.has(k)) { mine.add(k); walk(k); } };
  mine.add('p:' + me); walk('p:' + me);
  return mine;
}
function orgMayMove(key) { const mine = orgMine(); return mine === null || mine.has(key); }
// Owners and admins also drag a human or a bot into a group (onto its row, or onto "No group" to take it out), and a
// group into another group to nest it (backend/groups.py holds the rule).
const orgMayDrag = key => key.startsWith('g:') ? orgCanGroups() : orgMayMove(key) || orgCanGroups();
async function orgDrop(from, to) {
  const isGroup = to.startsWith('g:');
  if (from.startsWith('g:')) {
    if (!isGroup) return;
    await patch(`/v2/groups/${encodeURIComponent(from.slice(2))}`, {parent: to.slice(2)});
  } else if (isGroup) {
    const kind = from.startsWith('p:') ? 'people' : 'bots', id = from.slice(2), group = to.slice(2);
    const at = kind === 'people' ? (S.people || []).find(p => p.id === id)?.team : (S.emps || []).find(e => e.name === id)?.team;
    if (group) await patch(`/v2/groups/${encodeURIComponent(group)}`, {add: {[kind]: [id]}});
    else if (at) await patch(`/v2/groups/${encodeURIComponent(at)}`, {remove: {[kind]: [id]}});
    else return;
  } else if (from.startsWith('p:')) {
    if (!to.startsWith('p:')) return;
    await post(`/v2/humans/${encodeURIComponent(from.slice(2))}`, {reports_to: to.slice(2)});
  } else {
    const slug = from.slice(2), e = S.emps.find(x => x.name === slug);
    if (!e) return;
    const reports_to = to.startsWith('p:') ? 'human:' + to.slice(2) : to.slice(2);
    await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {reports_to, expected_revision: e.revision});
  }
  await refresh(true);
  renderTree();
}
function orgDragWire(tree) {
  if (!tree || tree.dataset.orgDrag) return;              // the tree is redrawn often; its listeners are added once
  tree.dataset.orgDrag = '1';
  let dragging = null;
  const clear = () => tree.querySelectorAll('.drop-target').forEach(n => n.classList.remove('drop-target'));
  tree.addEventListener('dragstart', ev => {
    const node = ev.target.closest('[data-org][draggable="true"]'); if (!node) return;
    dragging = node.dataset.org; ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', dragging);
    node.classList.add('dragging'); tree.classList.add('org-dragging');
  });
  tree.addEventListener('dragend', ev => { ev.target.closest?.('[data-org]')?.classList.remove('dragging'); tree.classList.remove('org-dragging'); clear(); dragging = null; });
  tree.addEventListener('dragover', ev => {
    const target = ev.target.closest('[data-org]'); if (!target || !dragging || target.dataset.org === dragging) return;
    const to = target.dataset.org;
    if (target.hasAttribute('data-helper')) return;       // built-in and message bots are not on the chart
    if (to.startsWith('g:') ? !orgCanGroups() : dragging.startsWith('g:') || !orgMayMove(dragging)) return;
    if (dragging.startsWith('p:') && !to.startsWith('p:') && !to.startsWith('g:')) return;   // a person reports to a person
    ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
    clear(); target.classList.add('drop-target');
  });
  tree.addEventListener('dragleave', ev => { ev.target.closest?.('[data-org]')?.classList.remove('drop-target'); });
  tree.addEventListener('drop', async ev => {
    const target = ev.target.closest('[data-org]'); if (!target || !dragging || target.hasAttribute('data-helper')) return;
    ev.preventDefault();
    const from = dragging, to = target.dataset.org; dragging = null;
    clear(); tree.classList.remove('org-dragging');
    if (from === to) return;
    try { await orgDrop(from, to); } catch (e) { toast(e.message, true); }
  });
}
// Adding and renaming a group happen in the tree: a one-line field where the group goes or is. Enter saves, Escape or
// leaving the field cancels.
let ORG_EDIT = null;                       // {add: parent group id or ''} or {rename: group id}
const ORG_DELETING = new Set();
async function orgGroupDelete(id) {
  const group = (S.orgGroups || []).find(g => g.id === id);
  if (!orgCanGroups() || !group || ORG_DELETING.has(id)) return;
  const parent = (S.orgGroups || []).find(g => g.id === group.parent);
  const destination = parent ? `the parent group "${parent.name}"` : 'No group';
  if (!confirm(`Delete group "${group.name}"?\n\nIts people, bots and child groups move to ${destination}. Teammates, their histories and reporting lines stay. Subscriptions assigned to this group will be unassigned.`)) return;
  ORG_DELETING.add(id);
  renderTree();
  try {
    await writeRequest('DELETE', `/v2/groups/${encodeURIComponent(id)}`);
    collapsed.delete('g:' + id); saveCollapsed();
    await refresh(true);
    toast(S.overviewRosterFresh ? 'Group deleted' : 'Group deleted. Refresh the chart to see the updated team.');
  } catch (e) {
    await refresh(true);                         // a stale group or changed permission needs fresh chart state
    toast(e.message, true);
  } finally {
    ORG_DELETING.delete(id);
    renderTree();
  }
}
function orgGroupEdit(edit) { ORG_EDIT = edit; if (edit.add) { collapsed.delete('g:' + edit.add); saveCollapsed(); } renderTree(); }
async function orgGroupSave(input) {
  const edit = ORG_EDIT, name = input.value.trim(); ORG_EDIT = null;
  if (!edit || !name) return renderTree();
  try {
    if (edit.rename) await patch(`/v2/groups/${encodeURIComponent(edit.rename)}`, {name});
    else await post('/v2/groups', {name, ...(edit.add ? {parent: edit.add} : {})});
    await refresh(true);
  } catch (e) { toast(e.message, true); }
  renderTree();
}
function orgGroupFieldHTML(value = '') {
  return `<input class="org-name" type="text" maxlength="60" value="${esc(value)}" aria-label="Group name" autocomplete="off" spellcheck="false">`;
}
function orgGroupFieldWire() {
  const input = $('#tree .org-name'); if (!input) return;
  input.focus(); input.select();
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); input.onblur = null; void orgGroupSave(input); }
    else if (ev.key === 'Escape') { input.onblur = null; ORG_EDIT = null; renderTree(); }
  };
  input.onblur = () => { ORG_EDIT = null; renderTree(); };
}
// The org list has no hover card on bots. What a bot's row says is its badge:
// a subtle ring spins around it while the bot works, and it looks like a quiet alert when the bot
// needs you — the count of tasks waiting on you, or a small ! when the need is not a task.
function treeBadge(n, st) {
  const working = st === 'running';
  if (n) return `<span class="cnt needs${working ? ' working' : ''}" role="img" aria-label="${n} need${n === 1 ? 's' : ''} you${working ? ', working' : ''}">${n}</span>`;
  if (st === 'needs') return `<span class="tree-alert${working ? ' working' : ''}" role="img" aria-label="Needs you">!</span>`;
  return working ? '<span class="tree-spin" role="img" aria-label="Working"></span>' : '';
}
// "next to org see a history icon that when pressed, sorts my bots and people by
// most recently viewed. With most recent on top" (he works with a couple of bots at a time). Every
// bot and person page opened is remembered on this device; the toggle stays as he left it.
// The list follows him between desktop and phone. It is saved with his account
// (preference org.history, stamped with when it last changed) and cached on each device; the newer
// copy wins when a page loads or a tab comes back to the front.
const ORG_HISTORY_KEY = 'tico.org.history', ORG_HISTORY_ON_KEY = 'tico.org.history.on';
const ORG_HISTORY_AT_KEY = 'tico.org.history.at', ORG_HISTORY_PREF = 'org.history';
function orgHistory() {
  try { return JSON.parse(localStorage.getItem(ORG_HISTORY_KEY) || '[]').filter(x => typeof x === 'string'); } catch { return []; }
}
function orgHistoryAt() { try { return Number(localStorage.getItem(ORG_HISTORY_AT_KEY)) || 0; } catch { return 0; } }
function orgHistoryStore(items, at) {
  try { localStorage.setItem(ORG_HISTORY_KEY, JSON.stringify(items.slice(0, 100))); localStorage.setItem(ORG_HISTORY_AT_KEY, String(at)); } catch {}
}
let orgHistoryTimer = null;
function orgHistorySave() {
  if (!S.me?.cloud) return;
  clearTimeout(orgHistoryTimer);
  orgHistoryTimer = setTimeout(() => { void post('/v2/preferences/' + ORG_HISTORY_PREF,
    {value: {items: orgHistory(), at: orgHistoryAt()}}).catch(() => {}); }, 800);
}
// "Only bots I can read or write" (the person icon beside the clock): hides the bots the caller may only
// see. Kept like the history: on this device at once, and with the account as preference org.mine,
// stamped with when it last changed, the newer copy winning.
const ORG_MINE_KEY = 'tico.org.mine', ORG_MINE_AT_KEY = 'tico.org.mine.at', ORG_MINE_PREF = 'org.mine';
function orgMineOn() { try { return localStorage.getItem(ORG_MINE_KEY) === '1'; } catch { return false; } }
function orgMineAt() { try { return Number(localStorage.getItem(ORG_MINE_AT_KEY)) || 0; } catch { return 0; } }
function orgMineStore(on, at) { try { localStorage.setItem(ORG_MINE_KEY, on ? '1' : '0'); localStorage.setItem(ORG_MINE_AT_KEY, String(at)); } catch {} }
const orgMineKeep = e => !orgMineOn() || !e.my_access || e.my_access.read || e.my_access.write;
let orgMineTimer = null;
function orgMineSave() {
  if (!S.me?.cloud) return;
  clearTimeout(orgMineTimer);
  orgMineTimer = setTimeout(() => { void post('/v2/preferences/' + ORG_MINE_PREF,
    {value: {on: orgMineOn(), at: orgMineAt()}}).catch(() => {}); }, 800);
}
async function orgMineSync() {
  if (!S.me?.cloud) return;
  const v = (await v2Get('/v2/preferences/' + ORG_MINE_PREF))?.value;
  const at = Number(v?.at) || 0;
  if (v && typeof v.on === 'boolean' && at > orgMineAt()) { orgMineStore(v.on, at); renderTree(); }
  else if (at < orgMineAt()) orgMineSave();
}
async function orgHistorySync() {
  void orgMineSync();
  if (!S.me?.cloud) return;
  const v = (await v2Get('/v2/preferences/' + ORG_HISTORY_PREF))?.value;
  const items = Array.isArray(v?.items) ? v.items.filter(x => typeof x === 'string') : null;
  if (items && (Number(v.at) || 0) > orgHistoryAt()) {
    const local = orgHistory();
    orgHistoryStore([...items, ...local.filter(k => !items.includes(k))], Number(v.at));
    renderTree();
  } else if (orgHistory().length && (Number(v?.at) || 0) < orgHistoryAt()) orgHistorySave();
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) void orgHistorySync(); });
function orgHistoryVisit(key) {
  const seen = orgHistory(); if (seen[0] === key) return;
  orgHistoryStore([key, ...seen.filter(x => x !== key)], Date.now());
  orgHistorySave();
  if (orgHistoryOn()) renderTree();                      // the one just opened moves to the top
}
function orgHistoryOn() { try { return localStorage.getItem(ORG_HISTORY_ON_KEY) === '1'; } catch { return false; } }
$('#org-mine').onclick = () => {
  orgMineStore(!orgMineOn(), Date.now());
  orgMineSave();
  renderTree();
};
$('#org-add-group').onclick = () => orgGroupEdit({add: ''});
$('#org-history').onclick = () => {
  try { localStorage.setItem(ORG_HISTORY_ON_KEY, orgHistoryOn() ? '0' : '1'); } catch {}
  if (orgHistoryOn() && navCollapsed.delete('organisation')) {
    try { localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify([...navCollapsed])); } catch {}
    renderNavSections();
  }
  renderTree();
};
// Message bots sit in their own group after the team chart; built-ins have separate entry points. `reports_to` is untouched,
// and a bot that reports to one hangs where it would have.
const MESSAGE_BOTS_GROUP = '__message_bots';
const isHelperBot = e => isBuiltInBot(e.name) || !!e.helper || (S.people || []).some(p => p.inbox_bot === e.name);
// The order here and on Goals: the Assistant, then the other built-ins, then message bots.
const HELPER_ORDER = ['botops', 'librarian', 'goal-manager', 'inbox'];
const helperRank = e => e.name === assistantBot() ? -1 : HELPER_ORDER.includes(e.name) ? HELPER_ORDER.indexOf(e.name) : 99;
function orgTreeWithHelpers(byParent) {
  const out = {}, helpers = [], bot = {}, parentOf = {};
  for (const [parent, kids] of Object.entries(byParent)) for (const n of kids) if (n.kind === 'bot') { bot['b:' + n.name] = n; parentOf['b:' + n.name] = parent; }
  const lift = parent => {
    for (let hops = 0; bot[parent] && isHelperBot(bot[parent]) && hops < 5; hops++) parent = parentOf[parent] || '';
    return parent;
  };
  for (const [parent, kids] of Object.entries(byParent)) for (const n of kids) {
    if (n.kind === 'bot' && isHelperBot(n)) helpers.push(n);
    else (out[lift(parent)] ||= []).push(n);
  }
  for (const [id, name, members, order] of [
    [MESSAGE_BOTS_GROUP, 'Message bots', helpers.filter(e => !isBuiltInBot(e.name)), 1],
  ]) {
    if (!members.length) continue;
    (out[''] ||= []).push({kind: 'group', id, name, order, helpers: true});
    out['g:' + id] = members;
  }
  return out;
}
function renderTree() {
  ensureCollapsed();
  const byParent = orgTreeWithHelpers(orgTreeByParent());
  const curBot = S.route.startsWith('#/bot/') ? S.route.slice(6).split('/')[0] : null;
  const curPerson = S.route.startsWith('#/person/') ? decodeURIComponent(S.route.slice(9).split('/')[0]) : null;
  // only "needs you" surfaces through a collapsed parent; running is not something to chase
  const subtreeNeeds = key => (byParent[key] || []).some(c =>
    (c.kind === 'bot' && (stateOf(c.name) === 'needs' || needsMeCount(c.name) || subtreeNeeds('b:' + c.name))) ||
    (c.kind === 'person' && subtreeNeeds('p:' + c.id)) ||
    (c.kind === 'group' && subtreeNeeds('g:' + c.id)));
  const isTemp = e => e.kind === 'bot' && isTempBot(e);
  const nameOf = n => n.kind === 'person' ? (n.person.name || n.id) : (n.display_name || '').replace(TEMP_RE, '');
  const sources = {};
  for (const r of inboxNavRows()) (sources[r.bot] ||= []).push(r);
  INBOX_PLACED = new Set();
  const rec = (parent, depth) => (byParent[parent] || []).slice()
    .sort((a, b) => {
      if (!!a.helpers !== !!b.helpers) return a.helpers ? 1 : -1;        // built-in and message groups come last
      // A group's own humans and bots come first, then the groups nested in it.
      if ((a.kind === 'group') !== (b.kind === 'group')) return a.kind === 'group' ? 1 : -1;
      if (a.kind === 'group' && b.kind === 'group') return (a.order || 0) - (b.order || 0);
      if ((a.kind === 'person') !== (b.kind === 'person')) return a.kind === 'person' ? -1 : 1;
      if (isTemp(a) !== isTemp(b)) return isTemp(a) - isTemp(b);
      if (a.kind === 'bot' && b.kind === 'bot' && isHelperBot(a) && isHelperBot(b) && helperRank(a) !== helperRank(b)) return helperRank(a) - helperRank(b);
      if (a.kind === 'bot' && b.kind === 'bot' && byBotOrder(a, b)) return byBotOrder(a, b);
      return nameOf(a).localeCompare(nameOf(b));
    })
    .map(node => row(node, depth)).join('');
  // flat: the history list, one row per bot and person, no children and nothing to drag
  const row = (node, depth, flat = false) => {
    if (node.kind === 'group') {
      const key = 'g:' + node.id, manage = !node.helpers && orgCanGroups() && !flat, adding = manage && ORG_EDIT?.add === node.id;
      const kids = byParent[key], isCol = kids && collapsed.has(key) && !adding;
      if (!kids && !manage) return '';
      const renaming = manage && ORG_EDIT?.rename === node.id;
      return `<li class="dept org-group"><div class="noderow" data-org="${esc(key)}"${manage && !renaming ? ' draggable="true"' : ''}${node.helpers ? ' data-helper' : ''}>
        <button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(node.name)}">›</button>
        ${renaming ? orgGroupFieldHTML(node.name) : `<span class="dept-label" data-toggle="${esc(key)}" role="button" tabindex="0">${esc(node.name)}</span>`}
        ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}
        ${manage && !renaming ? `<span class="org-group-tools"><button type="button" class="org-tool" data-group-add="${esc(node.id)}" title="Add group" aria-label="Add a group in ${esc(node.name)}"><span class="org-plus" aria-hidden="true">+</span></button><button type="button" class="org-tool" data-group-rename="${esc(node.id)}" title="Rename" aria-label="Rename ${esc(node.name)}"><span class="nav-icon" aria-hidden="true">edit</span></button><button type="button" class="org-tool" data-group-delete="${esc(node.id)}" title="Delete group" aria-label="Delete group ${esc(node.name)}"${ORG_DELETING.has(node.id) ? ' disabled' : ''}><span class="nav-icon" aria-hidden="true">delete</span></button></span>` : ''}</div>
        <ul ${isCol ? 'hidden' : ''}>${adding ? `<li class="org-new">${orgGroupFieldHTML()}</li>` : ''}${rec(key, depth + 1)}</ul></li>`;
    }
    if (node.kind === 'person') {
      const p = node.person, key = 'p:' + p.id, kids = !flat && byParent[key], isCol = kids && collapsed.has(key);
      return `<li class="${kids ? 'dept' : ''}"><div class="noderow">
        ${kids ? `<button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(p.name || p.id)}">›</button>` : ''}
        <a class="node person ${curPerson === p.id ? 'cur' : ''}" href="#/person/${encodeURIComponent(p.id)}" title="${esc(personTitle(p))}"${curPerson === p.id ? ' aria-current="page"' : ''} data-org="p:${esc(p.id)}"${!flat && orgMayDrag(key) ? ' draggable="true"' : ''}>
          ${personAvatar(p, depth ? 16 : 20)}<span class="nm">${esc(firstName(p.name) || p.id)}</span>
          ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}</a>
        ${mailPersonVisible(p) ? `<a class="person-mail-link${S.route.startsWith(MESSAGING) && messagingParams().bot === p.inbox_bot ? ' cur' : ''}" href="${MESSAGING}?bot=${encodeURIComponent(p.inbox_bot)}&source=${encodeURIComponent('email:' + p.email)}" title="${esc(p.name || p.id)} has a message bot" aria-label="Open ${esc(p.name || p.id)}'s message bot">forum</a>` : ''}</div>
        ${kids ? `<ul ${isCol ? 'hidden' : ''}>${rec(key, depth + 1)}</ul>` : ''}</li>`;
    }
    const e = node, key = 'b:' + e.name, st = stateOf(e.name), n = needsMeCount(e.name), kids = !flat && byParent[key], isCol = kids && collapsed.has(key);
    const helper = isHelperBot(e);                 // not on the chart: nothing is dragged onto or out of it
    const own = !flat && sources[e.name];
    if (own) INBOX_PLACED.add(e.name);
    return `<li class="${kids ? 'dept' : ''}"><div class="noderow">
      ${kids ? `<button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(e.display_name)}">›</button>` : ''}
      <a class="node ${e.status} ${curBot === e.name ? 'cur' : ''} ${st}" href="#/bot/${e.name}"${curBot === e.name ? ' aria-current="page"' : ''} data-org="b:${esc(e.name)}"${helper ? ' data-helper' : ''}${!flat && !helper && orgMayDrag(key) ? ' draggable="true"' : ''}>
        ${avatar(e.name, depth ? 16 : 20, st)}<span class="nm">${shownName(e)}</span>${orgBranchMark(e)}${runtimeTag(e)}${frTreeMark(e)}
        ${e.goal_active ? '<span class="nav-icon tree-goal" role="img" aria-label="Goal" title="Goal">target</span>' : ''}
        ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}
        ${treeBadge(n, st)}</a></div>
      ${own ? `<ul class="inbox-list node-sources" aria-label="${esc(e.display_name || e.name)}: mailboxes and channels">${own.map(inboxNavLink).join('')}</ul>` : ''}
      ${kids ? `<ul ${isCol ? 'hidden' : ''}>${rec(key, depth + 1)}</ul>` : ''}</li>`;
  };
  // Every bot and person, the one you opened last on top; ones you never opened follow by name.
  const history = () => {
    const seen = orgHistory(), at = n => { const i = seen.indexOf(n.kind === 'person' ? 'p:' + n.id : 'b:' + n.name); return i < 0 ? Infinity : i; };
    return Object.values(byParent).flat().filter(n => n.kind !== 'group')
      .sort((a, b) => (at(a) - at(b)) || nameOf(a).localeCompare(nameOf(b)))
      .map(node => row(node, 0, true)).join('');
  };
  renderOnboardingNav();
  renderLibrarians();
  const historyOn = orgHistoryOn();
  $('#org-history').setAttribute('aria-pressed', String(historyOn));
  $('#org-mine').setAttribute('aria-pressed', String(orgMineOn()));
  $('#tree').classList.toggle('history', historyOn);
  const manage = orgCanGroups() && !historyOn;
  $('#org-add-group').hidden = !manage;
  $('#tree').innerHTML = historyOn ? history()
    : (manage && ORG_EDIT?.add === '' ? `<li class="org-new">${orgGroupFieldHTML()}</li>` : '') + rec('', 0)
      + (manage ? '<li class="org-no-group" data-org="g:">No group</li>' : '');
  renderInboxNav();                                   // after the tree, which places what it can
  orgDragWire($('#tree'));
  orgGroupFieldWire();
  $('#tree').onclick = ev => {
    const remove = ev.target.closest('[data-group-delete]');
    if (remove) { ev.preventDefault(); void orgGroupDelete(remove.dataset.groupDelete); return; }
    const add = ev.target.closest('[data-group-add]'), rename = ev.target.closest('[data-group-rename]');
    if (add || rename) { ev.preventDefault(); orgGroupEdit(add ? {add: add.dataset.groupAdd} : {rename: rename.dataset.groupRename}); return; }
    const b = ev.target.closest('[data-toggle]'); if (!b) return;
    ev.preventDefault(); const k = b.dataset.toggle; collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
    saveCollapsed();
    renderTree();
  };
  document.querySelectorAll('[data-nav]').forEach(a => {
    const mobilePrimary = S.route === OVERVIEW || S.route === UPDATES || S.route.startsWith(UPDATES + '?');   // Tasks lives in More on a phone now
    const isCurrent = (a.dataset.nav === 'overview' && S.route === OVERVIEW) ||
      (a.dataset.nav === 'welcome' && S.route === WELCOME) ||
      (a.dataset.nav === 'meetings' && (S.route === MEETINGS || S.route.startsWith(MEETINGS + '?'))) ||
      (a.dataset.nav === 'mail' && (S.route === MAIL || S.route.startsWith(MAIL + '?') || S.route.startsWith(MESSAGING))) ||
      (a.dataset.nav === 'tasks' && (isTasksRoute(S.route) || S.route.startsWith('#/task/'))) ||
      (a.dataset.nav === 'settings' && S.route === SETTINGS) ||
      (a.dataset.nav === 'help' && S.route === HELP) ||
      (a.dataset.nav === 'credentials' && S.route === CREDENTIALS) ||
      (a.dataset.nav === 'docs' && (S.route === DOCS || S.route.startsWith(DOCS + '/') || S.route.startsWith(DOCS + '?'))) ||
      (a.dataset.nav === 'market' && (S.route === '#/market' || S.route.startsWith('#/market?') || S.route.startsWith('#/market/'))) ||
      (a.dataset.nav === 'integrations' && (S.route === INTEGRATIONS || S.route.startsWith(INTEGRATIONS + '/'))) ||
      (a.dataset.nav === 'changelog' && S.route === '#/changelog') ||
      (a.dataset.nav === 'runs' && S.route === '#/runs') ||
      (a.dataset.nav === 'usage' && S.route === '#/usage') ||
      (a.dataset.nav === 'updates' && (S.route === UPDATES || S.route.startsWith(UPDATES + '?'))) ||
      (a.dataset.nav === 'assistant' && S.route === $('#nav-assistant')?.getAttribute('href')) ||
      (a.dataset.nav === 'botops' && /^#\/bot\/botops(?:\/|$)/.test(S.route)) ||
      (a.dataset.nav === 'goals' && (S.route === GOALS || S.route.startsWith(GOALS + '/') || S.route.startsWith(GOALS + '?'))) ||
      (a.dataset.nav === 'more' && !mobilePrimary);
    a.classList.toggle('cur', isCurrent);
    if (isCurrent) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  $('#account')?.classList.toggle('cur', !!document.querySelector('#account-menu .cur'));   // the page you are on is behind it
}
