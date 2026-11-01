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
// The built-ins stay out of the team chart. Each person's one chat is their Assistant (Ask, and its own page,
// ui/app/assistant-page.js); BotOps, the Strategy Bot, the Librarian and Counsel are its helpers (state.js ASSISTANT_HELPERS),
// which nobody chats with. Search, bot pickers and a person's page list none of the four; Settings > Bots manages them,
// and #/bot/<id> opens each for its settings, runs and logs.
const isHiddenBot = slug => slug === assistantBot() || isAssistantHelper(slug);
const shownEmps = () => (S.emps || []).filter(e => !isHiddenBot(e.id));
// Groups first: a human or a bot hangs in the group its `team` names (backend/groups.py); groups nest by `parent`. Inside
// a group a teammate hangs under whoever it reports to when that one is in the same group, else at the top of the group.
// Outside groups it hangs under whoever it reports to, when that one is outside groups too. `reports_to` itself is not
// touched by any of this.
// Branches are personal workspaces, not additional teammates. Admin access still belongs in the branch picker.
const orgBranchVisible = e => !(e.shared_from || e.is_branch) || (!!S.me?.id && e.owner_id === S.me.id);
const orgBranchMark = e => (e.shared_from || e.is_branch) ? '<span class="org-branch">Your branch</span>' : '';
const orgGroupIds = () => new Set((S.orgGroups || []).map(g => g.id));
const orgCanGroups = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Who a human or bot reports to, as a key (`p:<id>` or `b:<bot id>`); '' when nobody is on the chart above it.
function orgBossKey(n) {
  const parent = (n.kind === 'person' ? n.person?.org_parent : n.org_parent) || '';
  if (!parent.startsWith('g:')) return parent;
  const boss = (S.orgGroups || []).find(g => g.id === parent.slice(2))?.reports_to;    // a group from before groups hung under a person
  return boss ? 'p:' + boss : '';
}
function orgHang(key, boss, groupOf) {
  const group = groupOf[key] || '';
  // The Strategy Bot hangs under the owner wherever the owner sits; it is a helper, off the chart, so what hangs under it
  // shows under the owner (orgTreeWithHelpers). A bot the server hangs under the Strategy Bot (backend/strategy_bot.py)
  // stays
  // in its group only when that group hangs under the Strategy Bot too, else it leaves the group for the owner's branch:
  // the owner's own group never moves there, or the chart would loop.
  if (boss && (key === 'b:goal-manager' || (boss === 'b:goal-manager' && group && !orgGroupUnderStrategyBot(group)))) return boss;
  if (group) return boss && groupOf[boss] === group ? boss : 'g:' + group;
  return boss && !groupOf[boss] ? boss : '';
}
// Whether this group, through its top-level group, hangs under the Strategy Bot (orgGroupParent).
function orgGroupUnderStrategyBot(id) {
  const byId = new Map((S.orgGroups || []).map(g => [g.id, g]));
  let g = byId.get(id);
  for (let hops = 0; g?.parent && byId.has(g.parent) && hops < 20; hops++) g = byId.get(g.parent);
  return g?.org_parent === 'b:goal-manager';
}
// Where a group hangs: in its parent group, else at the top. The server hangs the top-level group of a Head it puts under
// the Strategy Bot there too (`org_parent`, backend/strategy_bot.py), and the chart lifts it to the owner with the
// Head.
const orgGroupParent = (g, keys) => g.parent && keys.has('g:' + g.parent) ? 'g:' + g.parent
  : g.org_parent === 'b:goal-manager' && keys.has('b:goal-manager') ? 'b:goal-manager' : '';
function orgGroupOf(people, bots, seats = []) {
  const groups = orgGroupIds(), out = {};
  for (const p of people) if (groups.has(p.team)) out['p:' + p.id] = p.team;
  for (const e of bots) if (groups.has(e.team) && !isHelperBot(e)) out['b:' + e.id] = e.team;
  for (const seat of seats) if (groups.has(seat.team)) out['s:' + seat.id] = seat.team;
  return out;
}
function orgTreeByParent() {
  const byParent = {}, groups = orgGroupIds();
  const people = (S.people || []).filter(p => !p.hidden);
  // "Only bots I can read or write": a bot the caller may merely see leaves the chart, and the
  // bots under it hang from the next thing that is left.
  // Built-in and message bots sit apart, as on Goals; a bot that reports to one hangs where it would have.
  const bots = (S.emps || []).filter(e => orgBranchVisible(e) && orgMineKeep(e));
  const empIds = new Set(bots.map(e => e.id));
  const seats = S.seats || [];
  const groupOf = orgGroupOf(people, bots, seats);
  const keys = new Set([...[...groups].map(id => 'g:' + id), ...bots.map(e => 'b:' + e.id)]);
  for (const g of (S.orgGroups || [])) {
    (byParent[orgGroupParent(g, keys)] ||= []).push({kind: 'group', id: g.id, name: g.name, order: g.order || 0});
  }
  for (const p of people) {
    (byParent[orgHang('p:' + p.id, orgBossKey({kind: 'person', person: p}), groupOf)] ||= []).push({kind: 'person', id: p.id, person: p});
  }
  for (const e of bots) {
    let boss = orgBossKey({kind: 'bot', ...e});
    if (boss.startsWith('b:') && !empIds.has(boss.slice(2))) {
      const owner = e.owner_id || e.users?.[0]?.id;
      boss = owner ? 'p:' + owner : '';
    }
    (byParent[orgHang('b:' + e.id, boss, groupOf)] ||= []).push({kind: 'bot', ...e});
  }
  // Recruiting seats (backend/lifecycle.py): a role with no bot yet, where a bot in that place would hang, or under the
  // seat it reports to. Under a bot this view leaves out, it hangs from the next thing up that is left; with nothing
  // left above it (say "Only bots I can read" on), it is not shown.
  const seatIds = new Set(seats.map(seat => seat.id)), personIds = new Set(people.map(p => p.id));
  for (const seat of seats) {
    const to = String(seat.reports_to || '');
    let boss = seat.org_parent || (to.startsWith('seat:') ? 's:' + to.slice(5)
      : to.startsWith('human:') ? 'p:' + to.slice(6) : empIds.has(to) ? 'b:' + to : '');
    for (let hops = 0; boss.startsWith('b:') && !empIds.has(boss.slice(2)) && hops < 20; hops++)
      boss = orgBossKey({kind: 'bot', ...(botRow(boss.slice(2)) || {})});
    if (boss.startsWith('b:') && !empIds.has(boss.slice(2))) continue;
    if (boss.startsWith('s:') && !seatIds.has(boss.slice(2))) continue;
    if (boss.startsWith('p:') && !personIds.has(boss.slice(2))) continue;
    (byParent[orgHang('s:' + seat.id, boss, groupOf)] ||= []).push({kind: 'seat', ...seat, seat});
  }
  return byParent;
}
function mePerson() {
  const id = S.me?.id, email = (S.me?.email || '').toLowerCase();
  return (S.people || []).find(p => p.id === id) ||
         (S.people || []).find(p => (p.email || '').toLowerCase() === email) || null;
}
function inboxNavPeople() {
  const bots = new Set((S.emps || []).map(e => e.id));
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
      const shown = botRow(bot.bot);
      if (shown && !(isHelperBot(shown) && !isBuiltInBot(shown.id))) continue;
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
  const href = `${MESSAGING}?bot=${encodeURIComponent(row.bot)}&source=${encodeURIComponent(row.source)}`;
  const icon = row.kind === 'slack' ? SLACK_ICON : `<span class="nav-icon inbox-kind" aria-hidden="true">mail</span>`;
  return `<li><a href="${href}" title="${esc(row.title)}">${icon}<span>${esc(row.label)}</span></a></li>`;
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
  patchHTML(list, rows.map(inboxNavLink).join(''));
  navCurSync(list);
}
// Which row is the page you are on is marked in place: the list's markup never depends on the route, so a page change
// replaces no row (patchHTML keeps them all), and a whole group never redraws because one of its rows became current.
function navCurSync(root) {
  const bot = S.route.startsWith('#/bot/') ? decodeURIComponent(S.route.slice(6).split('/')[0]) : null;
  const person = S.route.startsWith('#/person/') ? decodeURIComponent(S.route.slice(9).split('/')[0]) : null;
  const seat = S.route.startsWith('#/seat/') ? decodeURIComponent(S.route.slice(7).split(/[/?]/)[0]) : null;
  const here = S.route.startsWith(MESSAGING) ? messagingParams() : null;
  for (const a of root.querySelectorAll('a.dept-label, a.node, a.person-mail-link, .inbox-list a')) {
    const href = a.getAttribute('href') || '', org = a.dataset.org || '';
    const q = here && href.startsWith(MESSAGING) ? new URLSearchParams(href.split('?')[1] || '') : null;
    const on = a.matches('.dept-label') ? S.route === href
      : a.matches('.person-mail-link') ? !!q && here.bot === q.get('bot')
      : q ? here.bot === q.get('bot') && here.source === q.get('source')
      : org.startsWith('p:') ? person === org.slice(2) : org.startsWith('b:') ? bot === org.slice(2)
      : a.dataset.seat ? seat === a.dataset.seat : false;
    a.classList.toggle('cur', on);
    // The mail link beside a person marks its bot's mailbox; the person's own row is the page.
    if (on && !a.matches('.person-mail-link')) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
}
function mailPersonVisible(person) {
  if (!S.me?.mail_access || !person?.email || !person.inbox_bot ||
      !botRow(person.inbox_bot)) return false;
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
  if (Array.isArray(data.seats)) S.seats = data.seats;
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
  for (const e of S.emps || []) add('b:' + e.id, {kind: 'bot', ...e});
  const walk = key => { for (const k of under[key] || []) if (!mine.has(k)) { mine.add(k); walk(k); } };
  mine.add('p:' + me); walk('p:' + me);
  return mine;
}
function orgMayMove(key) { const mine = orgMine(); return mine === null || mine.has(key); }
// Owners and admins also drag a human or a bot into a group (onto its row, or onto "No group" to take it out), and a
// group into another group to nest it (backend/groups.py holds the rule).
const orgMayDrag = key => key.startsWith('g:') ? orgCanGroups() : orgMayMove(key) || orgCanGroups();
// A manager's reports keep the order they are put in (backend/report_order.py). Whoever may put reports under that
// manager orders them: the owner, the manager or someone it reports up to, and the person who runs a bot manager.
const orgMeId = () => mePerson()?.id || S.me?.id || '';
const orgBot = key => key.startsWith('b:') ? botRow(key.slice(2)) : null;
const orgName = key => key.startsWith('p:') ? ((S.people || []).find(p => p.id === key.slice(2))?.name || key.slice(2))
  : (orgBot(key)?.display_name || key.slice(2));
const orgMayOrder = manager => /^[pb]:/.test(manager) &&
  (S.me?.role === 'owner' || orgMayMove(manager) || (!!orgMeId() && orgBot(manager)?.owner_id === orgMeId()));
// Dropping onto another human or bot changes whom it reports to, under the reports_to rules (app.person_update,
// SettingsAdmin.update_bot): a human is moved by someone they report up to, and only under that person or their
// reports; a bot by whoever may manage it, under any human, or a bot they manage or run. The reason when not.
function orgMoveRefusal(from, to) {
  if (from.startsWith('p:') && to.startsWith('b:')) return 'A human reports to a human, not to a bot.';
  const mayChange = from.startsWith('p:') ? orgMayMove(from) : S.me?.role === 'owner' || !!orgBot(from)?.can_manage || orgMayMove(from);
  if (!mayChange) return `You can't change who ${orgName(from)} reports to.`;
  const mayPut = S.me?.role === 'owner' || (to.startsWith('p:') ? from.startsWith('b:') || orgMayMove(to)
    : orgMayMove(to) || (!!orgMeId() && orgBot(to)?.owner_id === orgMeId()));
  return mayPut ? '' : `You can't put ${orgName(from)} under ${orgName(to)}: only under yourself or someone who reports up to you.`;
}
const orgOrderId = key => (key.startsWith('p:') ? 'human:' : 'bot:') + key.slice(2);
// The reports in the order the tree shows them, as the server names them; the ones not in the tree keep their places.
const orgOrderKeys = parent => [...$('#tree').querySelectorAll('[data-org-place]')].filter(n => n.dataset.orgIn === parent).map(n => n.dataset.org);
// A drop is not saved at once: moving someone is rare and a mis-drop is costly. The tree shows the new place at once and a
// strip under the moved row asks to confirm it; Move (Enter, as it has focus) saves, Cancel, Escape or a press anywhere
// else puts the chart back with no request. Reordering is by drag only, with a mouse or a long press on touch; by
// keyboard, a teammate is moved with the Org chart's "Move to…" sheet (ui/strategy-page.js), which stays as it is.
let ORG_PENDING = null;        // {from, to, where, parent, keys, text, anchor, set, was, saving, sent}
const orgObj = (kind, id) => kind === 'p' ? (S.people || []).find(p => p.id === id)
  : kind === 'b' ? botRow(id) : (S.orgGroups || []).find(g => g.id === id);
const orgGroupName = id => (S.orgGroups || []).find(g => g.id === id)?.name || id;
const orgTeamOf = key => orgObj(key[0], key.slice(2))?.team || '';
// What a drop would change, as field writes on the chart's own rows (S.people, S.emps, S.orgGroups), and the sentence
// that asks for it; null when it changes nothing. The same cases as orgDrop, which saves it.
function orgDropPlan(from, to, where, parent) {
  const kind = from[0], id = from.slice(2), set = [];
  let text;
  if (where) {
    const keys = orgOrderKeys(parent).filter(k => k !== from), i = keys.indexOf(to), was = orgOrderKeys(parent);
    if (i < 0) return null;
    keys.splice(where === 'after' ? i + 1 : i, 0, from);
    if (keys.join() === was.join()) return null;
    keys.forEach((k, n) => set.push([k[0], k.slice(2), 'report_position', n]));
    text = `Move ${orgName(from)} ${where === 'after' ? 'below' : 'above'} ${orgName(to)}?`;
    return {keys, set, text};
  }
  if (kind === 'g') {
    if (!to.startsWith('g:')) return null;
    const into = to.slice(2), group = orgObj('g', id);
    if (!group || (group.parent || '') === into) return null;
    for (let g = into, hops = 0; g && hops < 50; g = orgObj('g', g)?.parent || '', hops++)
      if (g === id) { toast(`${group.name} can't go inside itself.`, true); return null; }
    set.push(['g', id, 'parent', into]);
    text = into ? `Move ${group.name} into ${orgGroupName(into)}?` : `Move ${group.name} out of ${orgGroupName(group.parent)}?`;
  } else if (to.startsWith('g:')) {
    const into = to.slice(2), at = orgTeamOf(from);
    if (into === at || (!into && !at)) return null;
    set.push([kind, id, 'team', into]);
    text = into ? `Move ${orgName(from)} into ${orgGroupName(into)}?` : `Move ${orgName(from)} out of ${orgGroupName(at)}?`;
  } else {
    if (kind === 'p' && !to.startsWith('p:')) return null;
    const row = orgObj(kind, id); if (!row) return null;
    const reports_to = kind === 'p' ? to.slice(2) : to.startsWith('p:') ? 'human:' + to.slice(2) : to.slice(2);
    if ((row.reports_to || '') === reports_to) return null;
    set.push([kind, id, 'reports_to', reports_to], [kind, id, 'org_parent', to], [kind, id, 'report_position', undefined]);
    text = `Move ${orgName(from)} under ${orgName(to)}?`;
  }
  return {keys: [], set, text};
}
// The preview is written onto the rows each time the tree is drawn, as a refresh brings fresh ones; undone, the rows
// read as they did at the drop.
function orgPendingApply(p) {
  for (const [kind, id, field, value] of p.set) {
    const row = orgObj(kind, id); if (!row) continue;
    if (value === undefined) delete row[field]; else row[field] = value;
  }
}
function orgPendingUndo(p) {
  for (const [kind, id, field, value] of p.was) {
    const row = orgObj(kind, id); if (!row) continue;
    if (value === undefined) delete row[field]; else row[field] = value;
  }
}
function orgDropAsk(from, to, where, parent) {
  if (from === to || ORG_PENDING?.saving) return;
  // A second drag while one waits replaces it: the chart goes back first, so the new move is read from what is saved.
  if (ORG_PENDING) { orgPendingUndo(ORG_PENDING); ORG_PENDING = null; renderTree(); }
  if (!where) {
    const refused = !to.startsWith('g:') && orgMoveRefusal(from, to);
    if (refused) { toast(refused, true); return; }
  }
  const plan = orgDropPlan(from, to, where, parent); if (!plan) return;
  const was = plan.set.map(([kind, id, field]) => { const row = orgObj(kind, id); return [kind, id, field, row && Object.hasOwn(row, field) ? row[field] : undefined]; });
  ORG_PENDING = {from, to, where, parent, ...plan, was, anchor: from, saving: false, sent: false};
  renderTree();
  const go = $('#tree [data-org-confirm="go"]');
  go?.focus({preventScroll: true});
  go?.closest('.org-confirm')?.scrollIntoView({block: 'nearest'});
}
// The strip under the anchor row (the moved one; its new manager's row when that is folded).
const orgConfirmHTML = key => {
  const p = ORG_PENDING; if (!p || p.anchor !== key) return '';
  const off = p.saving ? ' disabled' : '';
  return `<div class="org-confirm" role="group" aria-labelledby="org-confirm-q"><span class="org-confirm-q" id="org-confirm-q">${esc(p.text)}</span>`
    + `<span class="org-confirm-do"><button type="button" class="primary" data-org-confirm="go"${off}>Move</button><button type="button" class="ghost" data-org-confirm="cancel"${off}>Cancel</button></span></div>`;
};
// Focus back on the moved row, or on the one it went under when that is folded.
function orgFocusRow(...keys) {
  for (const key of keys) {
    const n = $('#tree').querySelector(`[data-org="${CSS.escape(key)}"]`), a = n?.matches('a') ? n : n?.querySelector('a.dept-label');
    if (a?.offsetParent) { a.focus({preventScroll: true}); return; }
  }
}
// After each draw: the moved row is marked, the strip spans the rail from its left edge, and when the moved row is not
// shown (under a folded manager) the strip goes under the row it was dropped on.
function orgPendingMark(tree) {
  const p = ORG_PENDING;
  for (const n of tree.querySelectorAll('.org-moved')) if (!p || n.dataset.org !== p.from) n.classList.remove('org-moved');
  if (!p) return;
  tree.querySelector(`[data-org="${CSS.escape(p.from)}"]`)?.classList.add('org-moved');
  const strip = tree.querySelector('.org-confirm');
  if ((!strip || !strip.offsetParent) && p.anchor !== p.to) { p.anchor = p.to; renderTree(); return; }
  if (strip) strip.style.setProperty('--org-confirm-in', Math.max(0, strip.parentElement.getBoundingClientRect().left - tree.getBoundingClientRect().left) + 'px');
}
function orgPendingCancel(refocus = true) {
  const p = ORG_PENDING; if (!p || p.saving) return;
  orgPendingUndo(p); ORG_PENDING = null; renderTree();
  if (refocus) orgFocusRow(p.from);
}
async function orgPendingSave() {
  const p = ORG_PENDING; if (!p || p.saving) return;
  p.saving = true;
  // The request is built from the chart as it was: orgDrop reads a teammate's group and a bot's revision first thing.
  orgPendingUndo(p);
  const sent = p.where ? put(`/v2/org/order/${encodeURIComponent(orgOrderId(p.parent))}`, {reports: p.keys.map(orgOrderId)}) : orgDrop(p.from, p.to);
  renderTree();
  try { await sent; p.sent = true; await refresh(true); }
  catch (e) { p.sent = true; orgPendingUndo(p); toast(e.message, true); }
  ORG_PENDING = null; renderTree();
  orgFocusRow(p.from, p.to);
}
document.addEventListener('keydown', ev => {
  if (!ORG_PENDING || ev.key !== 'Escape') return;
  ev.preventDefault(); ev.stopPropagation(); orgPendingCancel();
}, true);
// A press elsewhere cancels, except on a row that may be dragged: putting the chart back would redraw that row under the
// pointer and lose the drag. A click there (no drag) still cancels, and a drop asks again.
document.addEventListener('pointerdown', ev => {
  if (ORG_PENDING && !ev.target.closest?.('#tree .org-confirm, #tree [draggable="true"]')) orgPendingCancel(false);
}, true);
document.addEventListener('click', ev => {
  if (ORG_PENDING && !ev.target.closest?.('#tree .org-confirm')) orgPendingCancel(false);
}, true);
async function orgDrop(from, to) {
  const isGroup = to.startsWith('g:');
  if (from.startsWith('g:')) {
    if (!isGroup) return;
    await patch(`/v2/groups/${encodeURIComponent(from.slice(2))}`, {parent: to.slice(2)});
  } else if (isGroup) {
    const kind = from.startsWith('p:') ? 'people' : 'bots', id = from.slice(2), group = to.slice(2);
    const at = kind === 'people' ? (S.people || []).find(p => p.id === id)?.team : botRow(id)?.team;
    if (group) await patch(`/v2/groups/${encodeURIComponent(group)}`, {add: {[kind]: [id]}});
    else if (at) await patch(`/v2/groups/${encodeURIComponent(at)}`, {remove: {[kind]: [id]}});
  } else if (from.startsWith('p:')) {
    if (!to.startsWith('p:')) return;
    await post(`/v2/humans/${encodeURIComponent(from.slice(2))}`, {reports_to: to.slice(2)});
  } else {
    const slug = from.slice(2), e = botRow(slug);
    if (!e) return;
    const reports_to = to.startsWith('p:') ? 'human:' + to.slice(2) : to.slice(2);
    await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {reports_to, expected_revision: e.revision});
  }
}
function orgDragWire(tree) {
  if (!tree || tree.dataset.orgDrag) return;              // the tree is redrawn often; its listeners are added once
  tree.dataset.orgDrag = '1';
  let dragging = null, draggingIn = '';
  const marks = ['drop-target', 'drop-before', 'drop-after', 'drop-refused'];
  const clear = () => tree.querySelectorAll(marks.map(m => '.' + m).join(',')).forEach(n => n.classList.remove(...marks));
  // The top or bottom third of a fellow report puts the dragged one before or after it; the middle, as anywhere
  // else, makes it report there.
  const zone = (target, y) => {
    if (!draggingIn || target.dataset.orgIn !== draggingIn || !target.hasAttribute('data-org-place')) return '';
    const box = target.getBoundingClientRect(), at = (y - box.top) / (box.height || 1);
    return at < 1 / 3 ? 'before' : at > 2 / 3 ? 'after' : '';
  };
  // The mark a row shows with the dragged one over it, '' when it takes no drop. A move the viewer may not make still
  // drops, to say why; nothing moves.
  const markFor = (target, y) => {
    if (!target || !dragging || target.dataset.org === dragging || target.hasAttribute('data-helper')) return '';
    const to = target.dataset.org, where = zone(target, y);
    if (!where && (to.startsWith('g:') ? !orgCanGroups() : dragging.startsWith('g:'))) return '';
    return where ? 'drop-' + where : !to.startsWith('g:') && orgMoveRefusal(dragging, to) ? 'drop-refused' : 'drop-target';
  };
  const begin = node => {
    dragging = node.dataset.org; draggingIn = node.hasAttribute('data-org-place') ? node.dataset.orgIn : '';
    node.classList.add('dragging'); tree.classList.add('org-dragging');
  };
  const end = () => { tree.querySelectorAll('.dragging').forEach(n => n.classList.remove('dragging')); tree.classList.remove('org-dragging'); clear(); dragging = null; };
  const finish = (target, y) => {
    const from = dragging, to = target.dataset.org, where = zone(target, y), parent = draggingIn;
    end(); orgDropAsk(from, to, where, parent);
  };
  let touch = null;                                       // a long press on a row: {node, x, y, timer, active, scroller}
  tree.addEventListener('dragstart', ev => {
    if (touch) { ev.preventDefault(); return; }           // a finger drags with the long press below, not the browser's drag
    const node = ev.target.closest('[data-org][draggable="true"]'); if (!node) return;
    begin(node);
    ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', dragging);
  });
  tree.addEventListener('dragend', end);
  tree.addEventListener('dragover', ev => {
    const target = ev.target.closest('[data-org]'), mark = markFor(target, ev.clientY); if (!mark) return;
    ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
    clear(); target.classList.add(mark);
  });
  tree.addEventListener('dragleave', ev => { ev.target.closest?.('[data-org]')?.classList.remove(...marks); });
  tree.addEventListener('drop', ev => {
    const target = ev.target.closest('[data-org]'); if (!target || !dragging || target.hasAttribute('data-helper')) return;
    ev.preventDefault();
    finish(target, ev.clientY);
  });
  // Touch: a phone starts no HTML drag from a finger, so a long press (400ms, holding still) picks the row up, the
  // finger carries it over the same drop targets, and lifting it drops it there. Moving before then scrolls as usual.
  const scrollerOf = el => { for (; el && el !== document.body; el = el.parentElement) { const o = getComputedStyle(el).overflowY; if ((o === 'auto' || o === 'scroll') && el.scrollHeight > el.clientHeight) return el; } return null; };
  const touchOff = () => { if (touch) clearTimeout(touch.timer); if (touch?.active) end(); touch = null; };
  const touchAt = (x, y) => document.elementFromPoint(x, y)?.closest('#tree [data-org]') || null;
  tree.addEventListener('touchstart', ev => {
    touchOff();
    const node = ev.touches.length === 1 && ev.target.closest('[data-org][draggable="true"]'); if (!node) return;
    const t = ev.touches[0];
    touch = {node, x: t.clientX, y: t.clientY, active: false, timer: setTimeout(() => {
      if (!touch || touch.node !== node) return;
      touch.active = true; touch.scroller = scrollerOf(tree); begin(node);
    }, 400)};
  }, {passive: true});
  tree.addEventListener('touchmove', ev => {
    if (!touch) return;
    const t = ev.touches[0];
    if (!touch.active) { if (Math.hypot(t.clientX - touch.x, t.clientY - touch.y) > 8) touchOff(); return; }
    ev.preventDefault();
    touch.x = t.clientX; touch.y = t.clientY;
    const s = touch.scroller, box = s?.getBoundingClientRect();
    if (box && t.clientY < box.top + 40) s.scrollTop -= 8; else if (box && t.clientY > box.bottom - 40) s.scrollTop += 8;
    const target = touchAt(t.clientX, t.clientY), mark = markFor(target, t.clientY);
    clear(); if (mark) target.classList.add(mark);
  }, {passive: false});
  tree.addEventListener('touchend', ev => {
    if (!touch) return;
    const {active, x, y} = touch;
    clearTimeout(touch.timer); touch = null;
    if (!active) return;
    ev.preventDefault();                                  // no click on the row it was lifted from
    const target = touchAt(x, y);
    if (markFor(target, y)) finish(target, y); else end();
  });
  tree.addEventListener('touchcancel', touchOff);
  tree.addEventListener('contextmenu', ev => { if (touch) ev.preventDefault(); });
}
// Adding and renaming a group happen in the tree: a one-line field where the group goes or is. Enter saves, Escape or
// leaving the field cancels.
let ORG_EDIT = null;                       // {add: parent group id or ''} or {rename: group id}
const ORG_DELETING = new Set();
async function orgGroupDelete(id) {
  const group = (S.orgGroups || []).find(g => g.id === id);
  if (!orgCanGroups() || !group || ORG_DELETING.has(id)) return;
  const parent = (S.orgGroups || []).find(g => g.id === group.parent);
  const destination = parent ? parent.name : 'No group';
  if (!confirm(`Delete ${group.name}? Its teammates and groups move to ${destination}.`)) return;
  ORG_DELETING.add(id);
  renderTree();
  try {
    await writeRequest('DELETE', `/v2/groups/${encodeURIComponent(id)}`);
    collapsed.delete('g:' + id); saveCollapsed();
    await refresh(true);
    toast('Group deleted');
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
  const input = $('#tree .org-name'); if (!input || input.onkeydown) return;   // a field kept through a redraw keeps what is typed
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
// A bot over its spend limit takes no new work until a person raises the limit or the period turns over.
function treeLimitMark(slug) {
  const limit = overLimit(slug); if (!limit) return '';
  const word = esc(overLimitWord(limit));
  return `<span class="tree-limit" role="img" aria-label="${word}" title="${word}">!</span>`;
}
function treeBadge(n, st, slug) {
  const working = st === 'running';
  if (n) return `<span class="cnt needs${working ? ' working' : ''}" role="img" aria-label="${n} things ${esc(empName(slug))} needs from you" title="${n} things ${esc(empName(slug))} needs from you">${n}</span>`;
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
$('#org-history').onclick = () => {
  try { localStorage.setItem(ORG_HISTORY_ON_KEY, orgHistoryOn() ? '0' : '1'); } catch {}
  if (orgHistoryOn() && navCollapsed.delete('organisation')) {
    try { localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify([...navCollapsed])); } catch {}
    renderNavSections();
  }
  renderTree();
};
// Helpers (built-ins and message bots) are off the Team list; they have their own entry points. `reports_to` is untouched,
// and a bot that reports to one hangs where it would have. The Strategy Bot is one too: the Assistant asks it.
const isHelperBot = e => isBuiltInBot(e.id) || !!e.helper
  || (S.people || []).some(p => p.inbox_bot === e.id);
// The order here and on Goals: the Assistant, then the other built-ins, then message bots.
const HELPER_ORDER = ['botops', 'librarian', 'goal-manager', 'task-manager', 'bookkeeper', COUNSEL_BOT, 'inbox'];
const helperRank = e => e.id === assistantBot() ? -1 : HELPER_ORDER.includes(e.id) ? HELPER_ORDER.indexOf(e.id) : 99;
function orgTreeWithHelpers(byParent) {
  const out = {}, bot = {}, parentOf = {};
  for (const [parent, kids] of Object.entries(byParent)) for (const n of kids) if (n.kind === 'bot') { bot['b:' + n.id] = n; parentOf['b:' + n.id] = parent; }
  const lift = parent => {
    for (let hops = 0; bot[parent] && isHelperBot(bot[parent]) && hops < 5; hops++) parent = parentOf[parent] || '';
    return parent;
  };
  for (const [parent, kids] of Object.entries(byParent)) for (const n of kids)
    if (!(n.kind === 'bot' && isHelperBot(n))) (out[lift(parent)] ||= []).push(n);
  return out;
}
// One parent's rows in the team chart's order. The screen after Create (ui/first-run.js) lists its groups the same way.
function orgSorted(parent, kids) {
  const placed = byReportOrder(parent);
  return (kids || []).slice().sort((a, b) => {
    // A group's own humans and bots come first, then the groups nested in it.
    if ((a.kind === 'group') !== (b.kind === 'group')) return a.kind === 'group' ? 1 : -1;
    if (a.kind === 'group' && b.kind === 'group') return (a.order || 0) - (b.order || 0);
    // Recruiting seats follow the people and bots there; use chart order when present, then title.
    if ((a.kind === 'seat') !== (b.kind === 'seat')) return a.kind === 'seat' ? 1 : -1;
    if (a.kind === 'seat') return chartNameKey(a.title).localeCompare(chartNameKey(b.title)) || String(a.id).localeCompare(String(b.id));
    if (placed(a, b)) return placed(a, b);               // a manager's reports in the order someone put them
    if (a.kind === 'bot' && b.kind === 'bot' && isHelperBot(a) && isHelperBot(b) && helperRank(a) !== helperRank(b)) return helperRank(a) - helperRank(b);
    return chartFallback(a, b);                          // then the chart's own order, as on the server
  });
}
// The person at the top of the team chart: the install's owner when nobody is above them and they are in no group, even
// beside other such people (a test account, say); else the only human at the top that nobody is above. None otherwise,
// so the chart never picks one of two others.
function orgTopPerson(byParent) {
  const tops = (byParent[''] || []).filter(n => n.kind === 'person' && !orgBossKey(n));
  const me = S.me?.role === 'owner' ? mePerson()?.id : '', roles = tops.filter(n => n.person?.role === 'owner');
  return tops.find(n => S.me?.owner_id && n.id === S.me.owner_id) || tops.find(n => me && n.id === me)
    || (roles.length === 1 ? roles[0] : tops.length === 1 ? tops[0] : null);
}
function renderTree() {
  if (ORG_PENDING && !ORG_PENDING.sent) orgPendingApply(ORG_PENDING);   // a drop waiting for Move shows its new place
  ensureCollapsed();
  const byParent = orgTreeWithHelpers(orgTreeByParent());
  let needCounts;                                   // counted once per render, on first use
  const needN = slug => (needCounts ||= needsMeCounts()).get('bot:' + slug) || 0;
  // only "needs you" surfaces through a collapsed parent; running is not something to chase
  const subtreeNeeds = key => (byParent[key] || []).some(c =>
    (c.kind === 'bot' && (stateOf(c.id) === 'needs' || needN(c.id) || subtreeNeeds('b:' + c.id))) ||
    (c.kind === 'person' && subtreeNeeds('p:' + c.id)) ||
    (c.kind === 'seat' && subtreeNeeds('s:' + c.id)) ||
    (c.kind === 'group' && subtreeNeeds('g:' + c.id)));
  const nameOf = n => n.kind === 'person' ? (n.person.name || n.id) : n.kind === 'seat' ? n.title || ''
    : (n.display_name || '').replace(TEMP_RE, '');
  const sources = {};
  for (const r of inboxNavRows()) (sources[r.bot] ||= []).push(r);
  INBOX_PLACED = new Set();
  const rec = (parent, depth) => rows(parent, orgSorted(parent, byParent[parent]), depth);
  const rows = (parent, sorted, depth) => {
    // The reports here that the viewer may put in order, per manager: a manager's own rows, or in a group the members
    // that share one. Each gets a place to drop before or after.
    const lists = {};
    for (const n of sorted) {
      if (n.kind !== 'person' && (n.kind !== 'bot' || isHelperBot(n))) continue;
      const manager = orgReportsTo(n);
      if (manager && (manager === parent || parent.startsWith('g:')) && orgMayOrder(manager))
        (lists[manager] ||= []).push(n.kind === 'person' ? 'p:' + n.id : 'b:' + n.id);
    }
    const at = n => { const manager = orgReportsTo(n), list = lists[manager] || []; return {parent: manager, order: list.length > 1 ? list : []}; };
    return sorted.map(node => row(node, depth, false, at(node))).join('');
  };
  // The top of the chart is not drawn as one more level: its top person (orgTopPerson) is a plain row, and the groups,
  // then the humans and bots, that report to them follow at the same level, then the rest of the top, where any other
  // person nobody is above nests their own reports as usual. The top person's reports keep their order and drop zones,
  // and a drop on the top row still reports there. With no top person the chart nests as before.
  const top = orgTopPerson(byParent), topKey = top ? 'p:' + top.id : '';
  const recTop = () => {
    if (!top) return rec('', 0);
    const isGroup = n => n.kind === 'group', under = orgSorted(topKey, byParent[topKey]);
    const rest = orgSorted('', byParent[''].filter(n => n !== top));
    return rows('', [top], 0) + rows(topKey, under.filter(isGroup), 1) + rows('', rest.filter(isGroup), 1)
      + rows(topKey, under.filter(n => !isGroup(n)), 1) + rows('', rest.filter(n => !isGroup(n)), 1);
  };
  // A report's place among its manager's reports: its drop zones above and below.
  const placeAttrs = (key, at) => at.order.includes(key) ? ` data-org-in="${esc(at.parent)}" data-org-place` : '';
  // flat: the history list, one row per bot and person, no children and nothing to drag
  const row = (node, depth, flat = false, at = {parent: '', order: []}) => {
    if (node.kind === 'group') {
      const key = 'g:' + node.id, manage = orgCanGroups() && !flat, adding = manage && ORG_EDIT?.add === node.id;
      // The plus opens the add menu for this group (ui/app/team-add.js): recruit a bot, an existing one, or a group.
      const plus = !flat && teamAddAny(node.id), menuOpen = plus && teamAddOpenFor(node.id);
      const kids = byParent[key], isCol = kids && collapsed.has(key) && !adding;
      if (!kids && !manage) return '';
      const renaming = manage && ORG_EDIT?.rename === node.id;
      return `<li class="dept org-group"><div class="noderow" data-org="${esc(key)}"${manage && !renaming ? ' draggable="true"' : ''}>
        <button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(node.name)}">›</button>
        ${renaming ? orgGroupFieldHTML(node.name) : `<a class="dept-label" href="${TEAM_PAGE}${esc(encodeURIComponent(node.id))}" draggable="false"><span class="dept-name">${esc(node.name)}</span></a>`}
        ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}
        ${plus && !renaming ? `<span class="org-group-tools"><button type="button" class="org-tool" data-group-plus="${esc(node.id)}" title="Add" aria-label="Add to ${esc(node.name)}" aria-haspopup="menu" aria-expanded="${menuOpen}" aria-controls="team-add-menu"><span class="org-plus" aria-hidden="true">+</span></button>${manage ? `<button type="button" class="org-tool" data-group-rename="${esc(node.id)}" title="Rename" aria-label="Rename ${esc(node.name)}"><span class="nav-icon" aria-hidden="true">edit</span></button><button type="button" class="org-tool" data-group-delete="${esc(node.id)}" title="Delete group" aria-label="Delete group ${esc(node.name)}"${ORG_DELETING.has(node.id) ? ' disabled' : ''}><span class="nav-icon" aria-hidden="true">delete</span></button>` : ''}</span>` : ''}</div>${orgConfirmHTML(key)}
        <ul ${isCol ? 'hidden' : ''}>${adding ? `<li class="org-new">${orgGroupFieldHTML()}</li>` : ''}${rec(key, depth + 1)}</ul></li>`;
    }
    if (node.kind === 'seat') {
      // A role still being recruited: its title, marked Recruiting, with the job description on hover. Not a drop
      // target or dragged here (data-seat, not data-org): it is opened and moved from its own page.
      const key = 's:' + node.id, kids = !flat && byParent[key], isCol = kids && collapsed.has(key);
      const cloud = !!node.agent;                    // its cloud agent is requested: Connecting (ui/app/cloud-agents.js)
      const tip = node.title + (cloud ? ' (Connecting)' : ' (Recruiting)') + (node.description ? '\n\n' + node.description : '');
      const applicants = recruitSeatCount(node.id);    // known once its Applicants page listed them
      return `<li class="${kids ? 'dept' : ''}"><div class="noderow">
        ${kids ? `<button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(node.title)}">›</button>` : ''}
        <a class="node seat" href="${esc(recruitAddress(node.id))}" data-seat="${esc(node.id)}" data-seat-open="${esc(node.id)}" title="${esc(tip)}">
          <span class="nav-icon seat-icon" aria-hidden="true">person_search</span><span class="nm">${esc(node.title)}</span>${cloud
            ? '<span class="pill waiting seat-pill connecting">Connecting</span>' : applicants === undefined
            ? '<span class="pill waiting seat-pill">Recruiting</span>' : `<span class="cnt" title="Applicants">${applicants}</span>`}</a></div>
        ${kids ? `<ul ${isCol ? 'hidden' : ''}>${rec(key, depth + 1)}</ul>` : ''}</li>`;
    }
    if (node.kind === 'person') {
      const p = node.person, key = 'p:' + p.id, isTop = !flat && key === topKey;
      const kids = !flat && !isTop && byParent[key], isCol = kids && collapsed.has(key);
      return `<li class="${kids ? 'dept' : isTop ? 'org-top' : ''}"><div class="noderow">
        ${kids ? `<button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(p.name || p.id)}">›</button>` : ''}
        <a class="node person" href="#/person/${encodeURIComponent(p.id)}" title="${esc(personTitle(p))}" data-org="p:${esc(p.id)}"${placeAttrs(key, at)}${!flat && (orgMayDrag(key) || at.order.includes(key)) ? ' draggable="true"' : ''}>
          ${personAvatar(p, depth ? 16 : 20)}<span class="nm">${esc(firstName(p.name) || p.id)}</span>
          ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}</a>
        ${mailPersonVisible(p) ? `<a class="person-mail-link" href="${MESSAGING}?bot=${encodeURIComponent(p.inbox_bot)}&source=${encodeURIComponent('email:' + p.email)}" title="${esc(p.name || p.id)} has a message bot" aria-label="Open ${esc(p.name || p.id)}'s message bot">forum</a>` : ''}</div>${orgConfirmHTML(key)}
        ${kids ? `<ul ${isCol ? 'hidden' : ''}>${rec(key, depth + 1)}</ul>` : ''}</li>`;
    }
    const e = node, key = 'b:' + e.id, st = stateOf(e.id), n = needN(e.id), kids = !flat && byParent[key], isCol = kids && collapsed.has(key);
    const helper = isHelperBot(e);                 // not on the chart: nothing is dragged onto or out of it
    const own = !flat && sources[e.id];
    if (own) INBOX_PLACED.add(e.id);
    return `<li class="${kids ? 'dept' : ''}"><div class="noderow">
      ${kids ? `<button class="chev ${isCol ? 'col' : ''}" data-toggle="${esc(key)}" aria-label="${isCol ? 'Expand' : 'Collapse'} ${esc(e.display_name)}">›</button>` : ''}
      <a class="node ${e.status} ${st}" href="#/bot/${encodeURIComponent(e.id)}" title="${esc(e.display_name || e.slug)}" data-org="b:${esc(e.id)}"${helper ? ' data-helper' : placeAttrs(key, at)}${!flat && !helper && (orgMayDrag(key) || at.order.includes(key)) ? ' draggable="true"' : ''}>
        ${avatar(e.id, depth ? 16 : 20, st)}<span class="nm">${shownName(e)}</span>${orgBranchMark(e)}${runtimeTag(e)}${frTreeMark(e)}
        ${e.goal_active ? '<span class="nav-icon tree-goal" role="img" aria-label="Goal" title="Goal">target</span>' : ''}
        ${isCol && subtreeNeeds(key) ? '<span class="dot needs" title="something inside needs attention"></span>' : ''}
        ${botPausedBadge(e.id)}${treeLimitMark(e.id)}${treeBadge(n, st, e.id)}</a></div>${orgConfirmHTML(key)}
      ${own ? `<ul class="inbox-list node-sources" aria-label="${esc(e.display_name || e.slug)}: mailboxes and channels">${own.map(inboxNavLink).join('')}</ul>` : ''}
      ${kids ? `<ul ${isCol ? 'hidden' : ''}>${rec(key, depth + 1)}</ul>` : ''}</li>`;
  };
  // Every bot and person, the one you opened last on top; ones you never opened follow by name.
  const history = () => {
    const seen = orgHistory(), at = n => { const i = seen.indexOf(n.kind === 'person' ? 'p:' + n.id : 'b:' + n.id); return i < 0 ? Infinity : i; };
    return Object.values(byParent).flat().filter(n => n.kind !== 'group' && n.kind !== 'seat')
      .sort((a, b) => (at(a) - at(b)) || nameOf(a).localeCompare(nameOf(b)))
      .map(node => row(node, 0, true)).join('');
  };
  renderOnboardingNav();
  const historyOn = orgHistoryOn();
  $('#org-history').setAttribute('aria-pressed', String(historyOn));
  $('#org-mine').setAttribute('aria-pressed', String(orgMineOn()));
  $('#tree').classList.toggle('history', historyOn);
  const manage = orgCanGroups() && !historyOn;
  teamAddRefresh();
  // Bot status events redraw the tree several times a minute: only the rows that changed are replaced (patchHTML). A
  // redrawn strip keeps the button that had focus.
  const confirmFocus = document.activeElement?.closest?.('#tree [data-org-confirm]')?.dataset.orgConfirm;
  patchHTML($('#tree'), historyOn ? history()
    : (manage && ORG_EDIT?.add === '' ? `<li class="org-new">${orgGroupFieldHTML()}</li>` : '') + recTop()
      + (manage ? '<li class="org-no-group" data-org="g:">No group</li>' : ''));
  if (confirmFocus && !document.activeElement?.closest?.('#tree [data-org-confirm]')) $(`#tree [data-org-confirm="${confirmFocus}"]`)?.focus({preventScroll: true});
  navCurSync($('#tree'));
  orgPendingMark($('#tree'));
  renderInboxNav();                                   // after the tree, which places what it can
  orgDragWire($('#tree'));
  orgGroupFieldWire();
  $('#tree').onclick = ev => {
    const seat = ev.target.closest('[data-seat-open]');
    if (seat) { setDrawer(false); return; }        // its Applicants page (ui/app/recruit.js)
    const confirm = ev.target.closest('[data-org-confirm]');
    if (confirm) { ev.preventDefault(); if (confirm.dataset.orgConfirm === 'go') void orgPendingSave(); else orgPendingCancel(); return; }
    if (ev.target.closest('.org-confirm')) return;
    const remove = ev.target.closest('[data-group-delete]');
    if (remove) { ev.preventDefault(); void orgGroupDelete(remove.dataset.groupDelete); return; }
    const plus = ev.target.closest('[data-group-plus]');
    if (plus) { ev.preventDefault(); teamAddToggle(plus.dataset.groupPlus); return; }
    const rename = ev.target.closest('[data-group-rename]');
    if (rename) { ev.preventDefault(); orgGroupEdit({rename: rename.dataset.groupRename}); return; }
    // A group's name opens its page and unfolds it; only the arrow folds it again.
    const label = ev.target.closest('a.dept-label');
    if (label) {
      const k = label.closest('[data-org]')?.dataset.org;
      if (k && collapsed.delete(k)) { saveCollapsed(); renderTree(); }
      return;
    }
    const b = ev.target.closest('[data-toggle]'); if (!b) return;
    ev.preventDefault(); const k = b.dataset.toggle; collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
    saveCollapsed();
    renderTree();
  };
  document.querySelectorAll('[data-nav]').forEach(a => {
    // More is current only for pages the bottom bar does not have its own button for.
    const mobilePrimary = S.route === OVERVIEW || S.route === OVERVIEW_CAMPUS || S.route === UPDATES || S.route.startsWith(UPDATES + '?') || isTasksRoute(S.route) || S.route.startsWith('#/task/') || S.route === '#/roadmap';
    const isCurrent = (a.dataset.nav === 'overview' && (S.route === OVERVIEW || S.route === OVERVIEW_CAMPUS)) ||
      (a.dataset.nav === 'welcome' && S.route === WELCOME) ||
      (a.dataset.nav === 'meetings' && (S.route === MEETINGS || S.route.startsWith(MEETINGS + '?'))) ||
      (a.dataset.nav === 'mail' && (S.route === MAIL || S.route.startsWith(MAIL + '?') || S.route.startsWith(MESSAGING))) ||
      (a.dataset.nav === 'tasks' && (isTasksRoute(S.route) || S.route.startsWith('#/task/') || S.route === '#/roadmap')) ||
      (a.dataset.nav === 'settings' && S.route === SETTINGS) ||
      (a.dataset.nav === 'credentials' && S.route === CREDENTIALS) ||
      (a.dataset.nav === 'docs' && (S.route === DOCS || S.route.startsWith(DOCS + '/') || S.route.startsWith(DOCS + '?'))) ||
      (a.dataset.nav === 'strategy' && (isStrategyRoute(S.route) || isLensesRoute(S.route) || isMarketRoute(S.route))) ||
      (a.dataset.nav === 'integrations' && (S.route === INTEGRATIONS || S.route.startsWith(INTEGRATIONS + '/'))) ||
      (a.dataset.nav === 'changelog' && S.route === '#/changelog') ||
      (a.dataset.nav === 'learnings' && (S.route === LEARNINGS || S.route.startsWith(LEARNINGS + '?'))) ||
      (a.dataset.nav === 'usage' && S.route === '#/usage') ||
      (a.dataset.nav === 'replay' && S.route.startsWith('#/replay')) ||
      (a.dataset.nav === 'updates' && (S.route === UPDATES || S.route.startsWith(UPDATES + '?'))) ||
      (a.dataset.nav === 'more' && !mobilePrimary) ||
      redesignNavCurrent(a.dataset.nav);
    a.classList.toggle('cur', isCurrent);
    if (isCurrent) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  $('#account')?.classList.toggle('cur', !!document.querySelector('#account-menu .cur'));   // the page you are on is behind it
}
