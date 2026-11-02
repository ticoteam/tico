/* ui/app/strategy-budget.js — Strategy > Budget (redesign): the org chart as one tree of budgets. The company on top, then
   the groups nested as on Org, people and bots under their group or manager, and the helpers in their own branch. A row
   is a name, its spend against the limit that binds ("$5.70 / $6 · 30m", a thin bar) and a chip when it has task caps;
   a group shows its pool against what everyone under it has spent. Groups fold (folded by default at 200 bots), a search
   keeps the rows that match and what they sit in, and Near limit keeps what is near or over a limit, or paused.
   Tapping a row edits it in a panel (a sheet on a phone): a bot's limits, a group's or the company's pool (and the
   default for each bot), and a teammate's task caps both ways.
   Reads: GET /api/v2/usage/limits (every bot's and pool's limits and `spent` in each window, in one read) and
   GET /api/v2/task-caps. Writes: PUT /api/v2/usage/limits[/{bot}], PUT /api/v2/usage/pools/{company|groups/{id}}, and
   PUT/DELETE /api/v2/task-caps (redesign CONTRACT §1). A save sends only what changed, so a limit the panel did not
   show (or did not touch) is never cleared. The hub decides who may change what; rows it says are not ours read only.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let BUDGET = null;          // the open tab: {load, limits, caps, tree, q, hot}
const BUDGET_FOLD = new Map();   // node key -> open (true) or folded (false), as the person left it this visit
// [limit field, label, spent field, short window name, the hub's period name] (CONTRACT §1 Spend names).
const BUDGET_PERIODS = [['per_30m_usd', '30 min', '30m_spent', '30m', 'per_30m'], ['daily_usd', 'Day', 'day_spent', 'day', 'daily'],
  ['weekly_usd', 'Week', 'week_spent', 'week', 'weekly'], ['monthly_usd', 'Month', 'month_spent', 'month', 'monthly']];
// The hour window installs had before 30 minutes replaced it (CONTRACT §1): still enforced where it is set, so it shows,
// binds and is edited like the others, and it is never sent unless it shows.
const BUDGET_HOURLY = ['hourly_usd', 'Hour', 'hour_spent', 'hour', 'hourly'];
const budgetPeriods = row => row?.hourly_usd != null || row?.own_hourly_usd != null
  ? [BUDGET_PERIODS[0], BUDGET_HOURLY, ...BUDGET_PERIODS.slice(1)] : BUDGET_PERIODS;
const BUDGET_BIG = 30;      // more teammates than this and the groups start folded
// "$27.38", "$80", "$5,000": cents only when there are any.
const budgetMoney = n => {
  const v = Math.round(Number(n || 0) * 100) / 100;
  return '$' + v.toLocaleString('en-US', {minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2});
};
const budgetPlain = n => { const v = Math.round(Number(n) * 100) / 100; return Number.isInteger(v) ? String(v) : v.toFixed(2); };
// Teammate refs (CONTRACT): bot:<slug>, person:<id>.
const budgetName = ref => {
  const [kind, ...rest] = String(ref || '').split(':'), id = rest.join(':');
  if (kind === 'bot') return botDisplayName(id);
  if (kind === 'person' || kind === 'human') return personDisplay(id);
  return String(ref || '');
};

// ----------------------------------------------------------------- limits
// The window that binds: the one held (the hub names it `per_30m`, `daily`...), else the set one nearest its limit.
function budgetBinding(row) {
  const set = budgetPeriods(row).filter(([k]) => row?.[k] != null);
  if (!set.length) return null;
  const held = [row.blocked, row.blocked_by?.period].filter(Boolean);
  const hit = set.find(p => held.includes(p[3]) || held.includes(p[4]));
  if (hit) return hit;
  const ratio = ([k, , spent]) => row[k] > 0 ? (row[spent] || 0) / row[k] : Infinity;
  return set.reduce((best, p) => ratio(p) > ratio(best) ? p : best);
}
// What a row reads: {text, pct (null: no bar), tone, hot}. With no limit, today's spend.
function budgetFigure(row, rollup) {
  const bind = row ? budgetBinding(row) : null;
  if (!bind) {
    const day = rollup?.day;
    return {text: day == null ? '' : `${budgetMoney(day)} · day`, pct: null, tone: '', hot: !!row?.blocked};
  }
  const cap = row[bind[0]], spent = row[bind[2]] ?? rollup?.[bind[3]] ?? 0;
  const pct = cap ? Math.min(100, Math.round(spent / cap * 100)) : 100;
  const tone = row.blocked || pct >= 100 ? 'bad' : pct >= 80 ? 'warn' : '';
  return {text: `${budgetMoney(spent)} / ${budgetMoney(cap)} · ${bind[3]}`, pct, tone, hot: !!tone};
}

// ----------------------------------------------------------------- the tree
// Org's chart (orgTreeByParent: groups, reports_to) without recruiting seats, as nodes {key, kind, id, ref, name, kids,
// parent, depth}. Helpers (the Strategy Bot too) are off the chart but spend, so they get a branch of their own; a bot
// the hub lists that the chart leaves out (a branch, a bot this person hides) goes under Other.
function budgetTree(limits) {
  const by = orgTreeWithHelpers(orgTreeByParent()), nodes = new Map();
  const groupOf = id => (S.orgGroups || []).find(g => g.id === id);
  const make = (n, parent) => {
    const kind = n.kind, id = kind === 'person' ? n.id : kind === 'bot' ? n.name : n.id;
    const key = kind === 'group' ? 'group:' + id : kind === 'person' ? 'person:' + id : 'bot:' + id;
    if (nodes.has(key) || (parent?.depth || 0) > 20) return null;
    const node = {key, kind, id, ref: kind === 'group' ? '' : key, parent, depth: parent ? parent.depth + 1 : 0, kids: [],
      name: kind === 'group' ? n.name || groupOf(id)?.name || id : kind === 'person' ? n.person?.name || id : botDisplayName(id),
      person: n.person, order: n.order || 0};
    nodes.set(key, node);
    const at = kind === 'group' ? 'g:' + id : kind === 'person' ? 'p:' + id : 'b:' + id;
    const kids = (by[at] || []).filter(k => k.kind !== 'seat' && !(k.kind === 'group' && k.helpers));
    const lead = kind === 'group' ? budgetGroupLead(id) : '';
    // Teammates first (in a group its lead first), then the groups by their order.
    const rank = k => k.kind === 'group' ? 2 : lead && (k.kind === 'person' ? 'person:' + k.id : 'bot:' + k.name) === lead ? 0 : 1;
    kids.map((k, i) => [k, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || (a[0].kind === 'group' && b[0].kind === 'group'
      ? (a[0].order || 0) - (b[0].order || 0) : 0) || a[1] - b[1])
      .forEach(([k]) => { const kid = make(k, node); if (kid) node.kids.push(kid); });
    return node;
  };
  const root = {key: 'company', kind: 'company', id: '', ref: '', name: companyName(), parent: null, depth: 0, kids: []};
  nodes.set('company', root);
  const tops = (by[''] || []).filter(k => k.kind !== 'seat' && !(k.kind === 'group' && k.helpers));
  const owner = tops.find(n => n.kind === 'person' && n.id === S.me?.owner_id) || tops.find(n => n.kind === 'person' && n.person?.role === 'owner');
  const rank = k => k === owner ? 0 : k.kind === 'group' ? 2 : 1;
  tops.map((k, i) => [k, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || (a[0].kind === 'group' && b[0].kind === 'group'
    ? (a[0].order || 0) - (b[0].order || 0) : 0) || a[1] - b[1]).forEach(([k]) => { const kid = make(k, root); if (kid) root.kids.push(kid); });
  // Helpers and the bots the chart leaves out, each in a branch of its own at the end.
  const live = e => !['archived', 'planned'].includes(e.status);
  const helpers = (S.emps || []).filter(e => live(e) && isHelperBot(e) && !nodes.has('bot:' + e.id));
  const other = Object.keys(limits?.bots || {}).filter(slug => !nodes.has('bot:' + slug) && !helpers.some(e => e.id === slug));
  for (const [key, name, slugs] of [['helpers', 'Helpers', helpers.map(e => e.id)], ['other', 'Other', other]]) {
    if (!slugs.length) continue;
    const node = {key, kind: 'branch', id: key, ref: '', name, parent: root, depth: 1, kids: []};
    nodes.set(key, node);
    slugs.sort((a, b) => botDisplayName(a).localeCompare(botDisplayName(b))).forEach(slug => {
      const kid = {key: 'bot:' + slug, kind: 'bot', id: slug, ref: 'bot:' + slug, name: botDisplayName(slug), parent: node, depth: 2, kids: []};
      nodes.set(kid.key, kid); node.kids.push(kid);
    });
    root.kids.push(node);
  }
  budgetRollup(root, limits);
  return {root, nodes};
}
const budgetGroupLead = id => (BUDGET?.groups || []).find(g => g.id === id)?.lead || '';
// Each node's spend in each window, its own and everyone's under it, and how many teammates are under it.
function budgetRollup(node, limits) {
  const own = node.kind === 'bot' ? limits?.bots?.[node.id]?.spent || {} : {};
  node.rollup = {...own};
  node.count = 0;
  for (const kid of node.kids) {
    budgetRollup(kid, limits);
    for (const [w, v] of Object.entries(kid.rollup)) node.rollup[w] = (node.rollup[w] || 0) + v;
    node.count += kid.count + (kid.kind === 'person' || kid.kind === 'bot' ? 1 : 0);
  }
}
// A node's limit row: a bot's own, a group's pool, the company's pool.
function budgetRow(node) {
  const limits = BUDGET?.limits;
  if (node.kind === 'bot') return limits?.bots?.[node.id] || null;
  if (node.kind === 'group') return limits?.pools?.groups?.[node.id] || null;
  if (node.kind === 'company') return limits?.pools?.company || null;
  return null;
}
// A bot's own spend; a pool's is the hub's (everyone in it, seen or not); without one, what this person can see adds up.
const budgetSpent = node => node.kind === 'bot' ? budgetRow(node)?.spent || {} : budgetRow(node)?.spent || node.rollup;
const budgetCapsOf = ref => (BUDGET?.caps || []).filter(c => c.sender === ref || c.receiver === ref);
const budgetCapFull = c => c.per_day > 0 && (c.count || 0) >= c.per_day;
function budgetFigureOf(node) {
  if (node.memo) return node.memo;
  const row = budgetRow(node), fig = budgetFigure(row, budgetSpent(node));
  if (node.kind === 'person' && !node.kids.length) fig.text = '';
  const caps = node.ref ? budgetCapsOf(node.ref) : [];
  fig.caps = caps.length;
  fig.capsFull = caps.some(c => c.sender === node.ref && budgetCapFull(c));
  fig.paused = !!row?.blocked;
  fig.hot = fig.hot || fig.capsFull;
  return (node.memo = fig);
}

// ----------------------------------------------------------------- the page
function pageBudget() {
  const load = (BUDGET?.load || 0) + 1;
  const route = (S.route || '').split('?')[0], open = route.startsWith(RD_BUDGET + '/') ? decodeURIComponent(route.slice(RD_BUDGET.length + 1)) : '';
  BUDGET = {load, limits: null, caps: [], groups: null, tree: null, q: BUDGET?.q || '', hot: !!BUDGET?.hot, open};
  const main = $('#main');
  main.classList.add('strategy-layout', 'budget-layout');
  main.innerHTML = strategyHeadHtml('budget') + `<div class="bt-top">
      <input type="search" class="bt-q" id="bt-q" placeholder="Search" aria-label="Search the budget" autocomplete="off" value="${esc(BUDGET.q)}">
      <button type="button" class="ghost bt-hot" data-bt-hot aria-pressed="${BUDGET.hot}">Near limit<span class="bt-hot-n" id="bt-hot-n"></span></button>
      <button type="button" class="ghost bt-all" data-bt-all>Expand all</button>
      ${helpBubble('Each bar is the window nearest its limit. A group adds up everyone under it.')}</div>
    <div id="bt-body" class="bt-body">${skelRows(6, {dot: false})}</div>`;
  let typing = null;
  $('#bt-q').oninput = ev => { clearTimeout(typing); typing = setTimeout(() => { BUDGET.q = ev.target.value.trim(); budgetDraw(); }, 80); };
  main.querySelector('[data-bt-hot]').onclick = ev => {
    BUDGET.hot = !BUDGET.hot; ev.currentTarget.setAttribute('aria-pressed', String(BUDGET.hot)); budgetDraw();
  };
  main.querySelector('[data-bt-all]').onclick = () => {
    const open = !budgetAllOpen();
    for (const node of BUDGET.tree?.nodes.values() || []) if (node.kids.length && node.kind !== 'company') BUDGET_FOLD.set(node.key, open);
    budgetDraw();
  };
  $('#bt-body').onclick = ev => {
    const fold = ev.target.closest('[data-bt-fold]');
    if (fold) { budgetToggle(fold.dataset.btFold); return; }
    const row = ev.target.closest('[data-bt-open]');
    if (row) budgetEdit(row.dataset.btOpen);
  };
  void budgetLoad(BUDGET);
}

async function budgetLoad(state) {
  const [limits, caps, groups] = await Promise.allSettled([get('/v2/usage/limits', {fresh: true}),
    get('/v2/task-caps', {fresh: true}), get('/v2/groups', {fresh: true})]);
  if (BUDGET !== state || !$('#bt-body')) return;
  state.limits = limits.status === 'fulfilled' ? limits.value : null;
  state.limitsError = limits.status === 'rejected' ? limits.reason?.message || 'Spend did not load.' : '';
  state.caps = caps.status === 'fulfilled' ? (caps.value?.caps || []) : [];
  state.capsError = caps.status === 'rejected' ? caps.reason?.message || 'Caps did not load.' : '';
  if (groups.status === 'fulfilled') state.groups = Array.isArray(groups.value) ? groups.value : groups.value?.groups || [];
  state.tree = budgetTree(state.limits);
  budgetDraw();
  if (state.open) { const key = state.open; state.open = ''; budgetReveal(key); budgetEdit(key); }
}
const budgetReload = () => BUDGET && $('#bt-body') ? budgetLoad(BUDGET) : null;

// Open: as the person left it, else the company and every teammate with reports open, the groups too in a small team.
function budgetIsOpen(node) {
  if (node.kind === 'company') return true;
  if (BUDGET_FOLD.has(node.key)) return BUDGET_FOLD.get(node.key);
  return !(node.kind === 'group' || node.kind === 'branch') || (BUDGET.tree?.root.count || 0) <= BUDGET_BIG;
}
const budgetAllOpen = () => [...(BUDGET.tree?.nodes.values() || [])].every(n => !n.kids.length || budgetIsOpen(n));
function budgetToggle(key) {
  const node = BUDGET.tree?.nodes.get(key); if (!node) return;
  BUDGET_FOLD.set(key, !budgetIsOpen(node));
  budgetDraw();
  $(`#bt-body [data-bt-fold="${CSS.escape(key)}"]`)?.focus();
}
// Everything above a row opens, so a deep link or a search result shows where it sits.
function budgetReveal(key) {
  for (let n = BUDGET.tree?.nodes.get(key)?.parent; n; n = n.parent) BUDGET_FOLD.set(n.key, true);
  budgetDraw();
  $(`#bt-body [data-bt-open="${CSS.escape(key)}"]`)?.scrollIntoView({block: 'center'});
}

// The rows to draw, in order: with a search or Near limit on, the rows that pass and what they sit in (opened); under a
// row the search matched, its rows as folded or opened (Near limit keeps only what is near).
function budgetRows() {
  const tree = BUDGET.tree, q = BUDGET.q.toLowerCase(), hot = BUDGET.hot, filtering = !!q || hot;
  const pass = n => n.kind !== 'company' && (!q || n.name.toLowerCase().includes(q)) && (!hot || budgetFigureOf(n).hot);
  const under = new Map();         // node -> whether something under it passes
  const mark = n => { let any = false; for (const k of n.kids) any = mark(k) || pass(k) || any; under.set(n, any); return any; };
  if (filtering) mark(tree.root);
  const out = [];
  const walk = (n, shown) => {
    out.push(n);
    const open = filtering && under.get(n) ? true : budgetIsOpen(n);
    if (!open) return;
    for (const k of n.kids) {
      const keep = !filtering || (shown && !hot) || pass(k) || under.get(k);
      if (keep) walk(k, shown || pass(k));
    }
  };
  walk(tree.root, false);
  return out;
}

function budgetDraw() {
  const body = $('#bt-body'); if (!body || !BUDGET) return;
  const tree = BUDGET.tree;
  if (!tree) return;
  for (const n of tree.nodes.values()) n.memo = null;
  const hotN = [...tree.nodes.values()].filter(n => n.kind !== 'company' && budgetFigureOf(n).hot).length;
  $('#bt-hot-n').textContent = hotN ? ' ' + hotN : '';
  const all = $('[data-bt-all]'); if (all) all.textContent = budgetAllOpen() ? 'Collapse all' : 'Expand all';
  const rows = budgetRows();
  const note = BUDGET.limitsError ? `<p class="muted bt-note">${esc(BUDGET.limitsError)}</p>` : '';
  const empty = rows.length <= 1 && (BUDGET.q || BUDGET.hot) ? `<p class="muted bt-note">${BUDGET.hot && !BUDGET.q ? 'Nothing near a limit' : 'No matches'}</p>` : '';
  body.innerHTML = note + `<ul class="bt" aria-label="Budget">${rows.map(budgetRowHtml).join('')}</ul>` + empty;
}
function budgetIcon(node) {
  if (node.kind === 'bot') return avatar(node.id, 18);
  if (node.kind === 'person') return personAvatar(node.person || (S.people || []).find(p => p.id === node.id) || {name: node.name}, 18);
  const icon = node.kind === 'company' ? 'account_balance' : node.kind === 'branch' ? 'smart_toy' : 'groups';
  return `<span class="nav-icon bt-gi" aria-hidden="true">${icon}</span>`;
}
function budgetRowHtml(node) {
  const fig = budgetFigureOf(node), open = budgetIsOpen(node) || (BUDGET.q || BUDGET.hot);
  const fold = node.kids.length && node.kind !== 'company'
    ? `<button type="button" class="bt-fold" data-bt-fold="${esc(node.key)}" aria-expanded="${open ? 'true' : 'false'}" aria-label="${open ? 'Fold' : 'Open'} ${esc(node.name)}"><span class="nav-icon" aria-hidden="true">chevron_right</span></button>`
    : '<span class="bt-fold" aria-hidden="true"></span>';
  const group = node.kind === 'group' || node.kind === 'branch' || node.kind === 'company';
  const count = group && node.count ? `<span class="bt-n">${node.count}</span>` : '';
  const chips = (fig.paused ? '<span class="pill fail bt-paused">Paused</span>' : '')
    + (fig.caps ? `<span class="pill bt-chip${fig.capsFull ? ' waiting' : ''}" title="Task caps">${fig.caps} ${fig.caps === 1 ? 'cap' : 'caps'}</span>` : '');
  const bar = fig.pct == null ? '' : `<span class="bt-bar" aria-hidden="true"><i style="width:${fig.pct}%"></i></span>`;
  const editable = node.kind !== 'branch';
  const inner = `${budgetIcon(node)}<span class="bt-name">${esc(node.name)}</span>${count}${chips}<span class="bt-amt">${esc(fig.text)}</span>${bar}`;
  return `<li class="bt-r ${group ? 'is-g ' : ''}${fig.tone}" style="--d:${node.depth}">${fold}${editable
    ? `<button type="button" class="bt-main" data-bt-open="${esc(node.key)}">${inner}</button>`
    : `<div class="bt-main">${inner}</div>`}</li>`;
}

// ----------------------------------------------------------------- who may edit
const budgetAdmin = () => !!BUDGET?.limits?.may_edit_default || S.me?.role === 'owner';
// A teammate's home group, and whether this person leads it or a group above it (the hub's rule for caps).
function budgetHome(ref) {
  const id = ref.slice(ref.indexOf(':') + 1);
  return (ref.startsWith('person:') ? (S.people || []).find(p => p.id === id)?.team : botRow(id)?.team) || '';
}
const budgetMayCap = sender => budgetAdmin() || (BUDGET?.limits?.leads || []).includes(budgetHome(sender));
function budgetMaySpend(node) {
  const row = budgetRow(node);
  if (node.kind === 'bot') return !!row?.may_edit;
  if (node.kind === 'group') return budgetAdmin() || !!row?.may_edit;
  if (node.kind === 'company') return budgetAdmin();
  return false;
}

// ----------------------------------------------------------------- the editor
// Who may be a cap's receiver: a manager above the sender on the chart (the hub refuses anyone else).
function budgetManagers(sender) {
  const ref = String(sender).replace(/^person:/, 'human:');
  return [...goalOrgPlace(ref).above].map(k => k.startsWith('p:') ? 'person:' + k.slice(2) : 'bot:' + k.slice(2));
}
// Who may send `receiver` capped tasks: every teammate it is a manager above.
function budgetBelow(receiver) {
  const key = receiver.startsWith('person:') ? 'p:' + receiver.slice(7) : 'b:' + receiver.slice(4);
  const refs = [...(S.emps || []).filter(e => !['archived', 'planned'].includes(e.status) && !isHelperBot(e)).map(e => 'bot:' + e.id),
    ...(S.people || []).filter(p => !p.hidden).map(p => 'person:' + p.id)];
  return refs.filter(ref => ref !== receiver && goalOrgPlace(ref.replace(/^person:/, 'human:')).above.has(key))
    .sort((a, b) => budgetName(a).localeCompare(budgetName(b)));
}
// One window: its label, what was spent in it, and the limit (empty: none, or the company default for a bot).
function budgetWindowHtml(scope, [k, label, , w], value, spent, hint, may) {
  const amount = may
    ? `<span class="bd-money">$<input name="${scope}.${k}" inputmode="decimal" autocomplete="off" value="${value == null ? '' : budgetPlain(value)}" placeholder="${esc(hint)}" aria-label="${label} limit"></span>`
    : `<span class="bt-val">${value == null ? esc(hint) : budgetMoney(value)}</span>`;
  return `<div class="bt-win"><span>${label}</span><span class="bt-spent">${spent?.[w] == null ? '' : budgetMoney(spent[w])}</span>${amount}</div>`;
}
// The windows of one limit row: what is stored (own for a bot), the PUT it goes to, and the hint an empty field shows.
function budgetSpendScopes(node) {
  const limits = BUDGET.limits || {}, row = budgetRow(node), def = limits.default || {};
  if (node.kind === 'bot') {
    if (!row) return [];
    return [{scope: 'bot', title: 'Spend', path: '/v2/usage/limits/' + encodeURIComponent(node.id), row, spent: row.spent,
      value: k => row['own_' + k] !== undefined ? row['own_' + k] : row[k], hint: k => def[k] != null ? 'Default ' + budgetMoney(def[k]) : 'No limit'}];
  }
  if (node.kind === 'group') {
    if (!row && !budgetAdmin()) return [];
    return [{scope: 'pool', title: 'Pool', path: '/v2/usage/pools/groups/' + encodeURIComponent(node.id), row: row || {},
      spent: row?.spent || node.rollup, value: k => row?.[k] ?? null, hint: () => 'No limit'}];
  }
  if (node.kind === 'company') {
    const out = [];
    if (limits.pools?.company || budgetAdmin()) out.push({scope: 'pool', title: 'Company', path: '/v2/usage/pools/company',
      row: limits.pools?.company || {}, spent: limits.pools?.company?.spent || node.rollup, value: k => limits.pools?.company?.[k] ?? null, hint: () => 'No limit'});
    out.push({scope: 'default', title: 'Each bot', path: '/v2/usage/limits', row: def, spent: null, value: k => def[k] ?? null,
      hint: () => 'No limit', may: budgetAdmin()});
    return out;
  }
  return [];
}
function budgetCapRowHtml(c, dir, other) {
  const may = budgetMayCap(c.sender), id = `${c.sender}>${c.receiver}`;
  const today = c.per_day == null ? '' : c.per_day === 0 ? 'blocked' : `${c.count || 0} today`;
  const who = `<span class="bt-who">${other.startsWith('bot:') ? avatar(other.slice(4), 16) : personAvatar((S.people || []).find(p => p.id === other.slice(7)) || {name: budgetName(other)}, 16)}<span>${esc(budgetName(other))}</span></span>`;
  return `<div class="bt-cap${budgetCapFull(c) ? ' full' : ''}" data-cap="${esc(id)}" data-dir="${dir}">${who}<span class="bt-today">${today}</span>${may
    ? `<input name="cap" inputmode="numeric" autocomplete="off" value="${c.per_day ?? ''}" aria-label="Tasks a day from ${esc(budgetName(c.sender))} to ${esc(budgetName(c.receiver))}">
       <button type="button" class="ghost bt-x" data-cap-remove aria-label="Remove cap" title="Remove"><span aria-hidden="true">✕</span></button>`
    : `<span class="bt-val">${c.per_day ?? ''}/day</span>`}</div>`;
}
function budgetCapsHtml(node) {
  const ref = node.ref, caps = budgetCapsOf(ref);
  const out = caps.filter(c => c.sender === ref), inn = caps.filter(c => c.receiver === ref);
  const addOut = budgetMayCap(ref) ? budgetManagers(ref).filter(r => !out.some(c => c.receiver === r)) : [];
  const addIn = budgetBelow(ref).filter(r => budgetMayCap(r) && !inn.some(c => c.sender === r));
  const add = (dir, refs) => refs.length ? `<select class="bt-add" data-cap-add="${dir}" aria-label="Add a cap"><option value="">+ Add</option>${refs.map(r =>
    `<option value="${esc(r)}">${esc(budgetName(r))}</option>`).join('')}</select>` : '';
  const list = (dir, rows, refs, title) => !rows.length && !refs.length ? '' : `<h4>${title}</h4><div class="bt-caps" data-caps="${dir}">${rows.map(c =>
    budgetCapRowHtml(c, dir, dir === 'out' ? c.receiver : c.sender)).join('')}</div>${add(dir, refs)}`;
  const body = list('out', out, addOut, 'Sends to') + list('in', inn, addIn, 'Takes from');
  if (!body) return '';
  return `<section class="bt-sec"><h3>Task caps ${helpBubble('Tasks a day. 0 means go through the manager in between.')}</h3>${body}
    ${BUDGET.capsError ? `<p class="muted">${esc(BUDGET.capsError)}</p>` : ''}</section>`;
}
// A bot held by a pool says so: raising its own limit does not lift that.
function budgetHeldHtml(node) {
  const row = node.kind === 'bot' ? budgetRow(node) : null, by = row?.blocked_by;
  if (!by) return '';
  const word = {per_30m: '30-minute', hourly: 'hourly', daily: 'daily', weekly: 'weekly', monthly: 'monthly', '30m': '30-minute'}[by.period] || by.period;
  return `<p class="bt-held">${by.scope === 'bot' || !by.scope ? `Paused: over its ${esc(word)} limit` : `Paused: ${esc(by.name || 'a pool')} is over its ${esc(word)} limit`}</p>`;
}

function budgetEdit(key) {
  const node = BUDGET?.tree?.nodes.get(key); if (!node || node.kind === 'branch') return;
  document.querySelectorAll('dialog.bt-panel').forEach(d => d.close());
  const scopes = budgetSpendScopes(node);
  const spendHtml = scopes.map(s => {
    const may = s.may ?? budgetMaySpend(node);
    return `<section class="bt-sec" data-scope="${s.scope}"><h3>${s.title}</h3>${budgetPeriods(s.row).map(p =>
      budgetWindowHtml(s.scope, p, s.value(p[0]), s.spent, s.hint(p[0]), may)).join('')}</section>`;
  }).join('');
  const capsHtml = node.ref ? budgetCapsHtml(node) : '';
  const editable = scopes.some(s => s.may ?? budgetMaySpend(node)) || /data-cap-add|name="cap"/.test(capsHtml);
  const body = budgetHeldHtml(node) + spendHtml + capsHtml || '<p class="muted">Nothing to set here.</p>';
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal bt-panel';
  dlg.setAttribute('aria-label', node.name);
  dlg.innerHTML = `<div class="tmodal-head"><span class="bt-ph">${budgetIcon(node)}<b>${esc(node.name)}</b></span><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body" novalidate>${body}<p class="err" data-error hidden></p></form>
    ${editable ? '<div class="bt-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="button" class="primary" data-save>Save</button></div>' : ''}`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector('[data-error]');
  form.onsubmit = ev => { ev.preventDefault(); dlg.querySelector('[data-save]')?.click(); };
  form.addEventListener('change', ev => {
    const pick = ev.target.closest('[data-cap-add]');
    if (!pick || !pick.value) return;
    const dir = pick.dataset.capAdd, other = pick.value, c = dir === 'out'
      ? {sender: node.ref, receiver: other, per_day: 5, count: 0} : {sender: other, receiver: node.ref, per_day: 5, count: 0};
    pick.querySelector(`option[value="${CSS.escape(other)}"]`)?.remove();
    pick.value = '';
    const list = form.querySelector(`[data-caps="${dir}"]`);
    list.insertAdjacentHTML('beforeend', budgetCapRowHtml(c, dir, other));
    list.lastElementChild.dataset.added = '1';
    list.lastElementChild.querySelector('input')?.select();
  });
  form.addEventListener('click', ev => {
    const x = ev.target.closest('[data-cap-remove]'); if (!x) return;
    const row = x.closest('[data-cap]');
    if (row.dataset.added) { row.remove(); return; }
    row.classList.toggle('removed');
    row.querySelector('input').disabled = row.classList.contains('removed');
  });
  const save = dlg.querySelector('[data-save]');
  if (save) save.onclick = async () => {
    err.hidden = true;
    let writes;
    try { writes = budgetChanges(node, scopes, form); } catch (e) { err.textContent = e.message; err.hidden = false; return; }
    if (!writes.length) { dlg.close(); return; }
    save.disabled = true;
    let done = 0;
    try {
      for (const [method, path, payload] of writes) { await writeRequest(method, path, payload); done++; }
      dlg.close(); toast('Saved');
      void budgetReload();
    } catch (e) {
      err.textContent = e.message; err.hidden = false; save.disabled = false;
      if (done) void budgetReload();
    }
  };
  dlg.showModal();
}
// The requests a save makes: for each limit row, only the windows whose value changed (empty clears one); for caps,
// a PUT for each added or changed and a DELETE for each removed. Nothing else is sent, so nothing else changes.
function budgetChanges(node, scopes, form) {
  const writes = [];
  for (const s of scopes) {
    const body = {};
    for (const [k, label] of budgetPeriods(s.row)) {
      const input = form.elements[`${s.scope}.${k}`]; if (!input) continue;
      const t = String(input.value).trim().replace(/^\$\s*/, '').replace(/,/g, '');
      if (t && !/^\d+(\.\d{1,2})?$/.test(t)) throw new Error(`${label}: a dollar amount, like 4.00, or empty for none`);
      if (t && Number(t) <= 0) throw new Error(`${label}: above $0, or empty for none`);
      const now = s.value(k) ?? null, want = t ? Number(t) : null;
      if (now === null ? want !== null : want === null || Math.abs(want - now) > 0.004) body[k] = want;
    }
    if (Object.keys(body).length) writes.push(['PUT', s.path, body]);
  }
  for (const row of form.querySelectorAll('[data-cap]')) {
    const input = row.querySelector('input[name="cap"]'); if (!input) continue;
    const [sender, receiver] = row.dataset.cap.split('>');
    const was = BUDGET.caps.find(c => c.sender === sender && c.receiver === receiver);
    if (row.classList.contains('removed')) {
      if (was) writes.push(['DELETE', `/v2/task-caps?sender=${encodeURIComponent(sender)}&receiver=${encodeURIComponent(receiver)}`, {}]);
      continue;
    }
    const t = String(input.value).trim();
    if (!/^\d+$/.test(t)) throw new Error(`${budgetName(sender)} → ${budgetName(receiver)}: a whole number of tasks a day`);
    if (!was || was.per_day !== Number(t)) writes.push(['PUT', '/v2/task-caps', {sender, receiver, per_day: Number(t)}]);
  }
  return writes;
}
