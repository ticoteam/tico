/* ui/app/org-rules.js — Rules (docs/rules-plan.md, Screens 1, 2, 4–8): the Rules page with its Internal, External, Due
   and Suggested tabs, the rule and due-date side panels, the Add rule and Add document dialogs, and a bot's Rules tab.
   Everything is read from and written to the org rules API (backend/org_rules.py): GET /api/v2/rules (paged; every
   page is read, a team has dozens of rules), /rules/counts, /rules/due, /rule-sources and /bots/<slug>/rules. A rule
   says whether the caller may change it (`can_edit`), so the panels show its controls only then; the server checks
   again, and a refusal shows as one line. Saving sends the rule's `version`: a 409 reloads it and says so. Add document
   starts a Counsel review (POST /rules/read, backend/counsel.py); its suggestions wait in Suggested, and each one's
   quote shows in its side panel, never on its row.
   Addresses: #/rules[/external|/due|/suggested][?rule=<id> | ?due=<id> | ?due=new&rule=<id>]. The filters stay on
   the page, not in the address. On a phone the lists are stacked rows and a panel is a full-screen sheet.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RULES_ROUTE = '#/rules';
const RU_TABS = [['internal', 'Internal'], ['external', 'External'], ['due', 'Due'], ['suggested', 'Suggested']];
const RU_REPEATS = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly']];
const RU_STATUS = {overdue: ['Overdue', 'fail'], due_soon: ['Due soon', 'ready'], upcoming: ['Upcoming', ''], satisfied: ['Satisfied', 'ok']};
const RU_KINDS = [['vendor', 'Vendor'], ['customer', 'Customer'], ['government', 'Government'], ['other', 'Other']];
const RU_BROKEN = [['block', 'Block'], ['escalate', 'Escalate'], ['warn', 'Warn']];
const RU_PAGES = 50;        // pages read for one list at most (a page stops at 100 rows or at its byte budget)
const RU_SETTINGS_BATCH = 40;   // bots whose settings "Settings too" reads at a time (More reads the next ones)
let RU = null;   // {root, tab, counts, rules: {internal, external}, suggested, due, sources, settings, filter, seq, loaded}

const ruPhone = () => matchMedia('(max-width:760px)').matches;
const ruQuery = () => new URLSearchParams((S.route || '').split('?')[1] || '');
const ruTabOf = r => { const t = (r || '').split('?')[0].slice(RULES_ROUTE.length + 1); return RU_TABS.some(([k]) => k === t) ? t : 'internal'; };
const ruHref = (tab, q = {}) => {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v)).toString();
  return RULES_ROUTE + (tab && tab !== 'internal' ? '/' + tab : '') + (qs ? '?' + qs : '');
};

// ----------------------------------------------------------------- names
const ruGroups = () => (S.orgGroups || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
// The Assistant and its helpers (Counsel among them) are in no bot picker (ui/app/sidebar.js isHiddenBot).
const ruBots = () => (S.emps || []).filter(e => e.status !== 'archived' && !isHiddenBot(e.id)).map(e => ({id: e.id, name: botDisplayName(e.id)}))
  .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
const ruPeople = () => (S.people || []).filter(p => !p.hidden && !p.archived).map(p => ({id: p.id, name: p.name || p.email || p.id}))
  .sort((a, b) => a.name.localeCompare(b.name));
const ruGroupName = id => (S.orgGroups || []).find(g => g.id === id)?.name || id;
const ruPersonName = id => (S.people || []).find(p => p.id === id)?.name || id;
function ruActorName(actor) {
  const [kind, id] = String(actor || '').split(/:(.*)/);
  if (kind === 'bot') return botDisplayName(id);
  if (kind === 'human' || kind === 'person') return ruPersonName(id);
  return actor ? 'Tico' : '';
}
// A Who or With as the API shows it ({everyone, all_bots, groups: [{id, name}], ...}) in words.
function ruScopeWords(scope, empty = '—') {
  if (!scope) return empty;
  if (scope.everyone) return 'Everyone';
  const words = [...(scope.all_bots ? ['All bots'] : []), ...['groups', 'bots', 'people'].flatMap(k => (scope[k] || []).map(x => x.name || x.id))];
  return words.length ? words.join(', ') : empty;
}
// Its ids only, as the API takes it and as the scope editor keeps it.
const ruScopeIds = scope => ({everyone: !!scope?.everyone, all_bots: !!scope?.all_bots,
  groups: (scope?.groups || []).map(x => x.id ?? x), bots: (scope?.bots || []).map(x => x.id ?? x), people: (scope?.people || []).map(x => x.id ?? x)});
const ruSameScope = (a, b) => JSON.stringify(ruScopeIds(a)) === JSON.stringify(ruScopeIds(b));

// "Jan 31", or "Jan 31, 2027" outside this year.
function ruDay(iso, year = false) {
  if (!iso) return '';
  const d = new Date(String(iso).slice(0, 10) + 'T12:00:00');
  if (isNaN(d)) return String(iso);
  const opts = {month: 'short', day: 'numeric'};
  if (year || d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString(undefined, opts);
}
const ruRepeatWord = r => (RU_REPEATS.find(([k]) => k === r) || [, r || ''])[1];
const ruPill = status => { const [label, tone] = RU_STATUS[status] || [status, '']; return `<span class="pill ru-pill ${tone}">${esc(label)}</span>`; };
// A rule's due dates, the most urgent first: overdue, then due soon, upcoming, satisfied; the nearest within each.
const RU_URGENCY = {overdue: 0, due_soon: 1, upcoming: 2, satisfied: 3};
const ruDueSorted = rule => (rule.due || []).slice().sort((a, b) => (RU_URGENCY[a.status] ?? 9) - (RU_URGENCY[b.status] ?? 9) || a.due_on.localeCompare(b.due_on));
// The most urgent one, and "+N" for the rest (those the view counts but leaves out too).
// A routine's lead time: "14 days before"; null with no routine.
const ruLead = days => days == null ? null : `${days} day${days === 1 ? '' : 's'} before`;
const ruDueCell = rule => {
  const [d, ...rest] = ruDueSorted(rule), more = rest.length + (rule.due_more || 0);
  return d ? `<span class="ru-due">${esc(ruDay(d.due_on))}${d.repeats && d.repeats !== 'once' ? ` <span class="muted">· ${esc(ruRepeatWord(d.repeats).toLowerCase())}</span>` : ''}${more ? ` <span class="ru-plus">+${more}</span>` : ''}</span>` : '<span class="ru-none">—</span>';
};
// A refusal in one line: a 403 is about permission, the rest say what the server said.
const ruError = e => e?.status === 403 ? "You can't change this." : e?.message || 'Something went wrong.';

// ----------------------------------------------------------------- reads
async function ruAll(path) {
  const out = [];
  let cursor = '';
  for (let i = 0; i < RU_PAGES; i++) {
    const page = await get(path + (path.includes('?') ? '&' : '?') + 'limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
    out.push(...(page.rules || page.sources || page.due || []));
    cursor = page.next_cursor;
    if (!cursor) break;
  }
  return out;
}
async function ruLoad(tab) {
  const state = RU, seq = ++state.seq;
  const counts = get('/v2/rules/counts', {fresh: true}).catch(() => null);
  try {
    if (tab === 'internal') {
      state.rules.internal = await ruAll('/v2/rules?kind=internal');
      if (state.filter.settings) await ruLoadSettings(state);
    } else if (tab === 'external') {
      [state.rules.external, state.sources] = await Promise.all([ruAll('/v2/rules?kind=external'), ruAll('/v2/rule-sources')]);
    } else if (tab === 'due') {
      state.due = await ruAll('/v2/rules/due?next=3660d');
    } else {
      state.suggested = await ruAll('/v2/rules?status=suggested');
    }
    state.error = '';
  } catch (e) { state.error = e.message; }
  const c = await counts;
  if (RU !== state || seq !== state.seq) return false;
  if (c) state.counts = c;
  state.loaded.add(tab);
  return true;
}
// The bots' settings (Settings too), a batch of bots at a time: each bot's own sentences, and the fixed limits once for
// all bots. `all` reads every bot still unread (a search or a bot filter needs them all); `bot` reads that one.
async function ruLoadSettings(state, {all = false, bot = ''} = {}) {
  const unread = ruBots().filter(b => !state.settingsBy.has(b.id));
  const want = bot ? unread.filter(b => b.id === bot) : all ? unread : unread.slice(0, RU_SETTINGS_BATCH);
  for (let i = 0; i < want.length; i += RU_SETTINGS_BATCH) {
    const batch = want.slice(i, i + RU_SETTINGS_BATCH);
    const read = await Promise.all(batch.map(b => get('/v2/bots/' + encodeURIComponent(b.id) + '/rules').catch(() => null)));
    if (RU !== state) return;
    read.forEach((r, j) => { if (r) state.settingsBy.set(batch[j].id, r.settings || []); });
  }
  const rows = [], fixed = new Map();
  for (const b of ruBots()) {
    for (const s of state.settingsBy.get(b.id) || []) {
      if (!s.edit) { if (!fixed.has(s.text)) fixed.set(s.text, {...s, who: 'All bots', bot: ''}); }
      else rows.push({...s, who: b.name, bot: b.id});
    }
  }
  state.settings = [...rows, ...fixed.values()];
  state.settingsLeft = ruBots().filter(b => !state.settingsBy.has(b.id)).length;
}
// A search or a bot filter looks through every bot's settings, so the rest are read first.
async function ruSettingsReach(state) {
  const f = state.filter;
  if (!f.settings || !state.settingsLeft) return;
  if (f.q) await ruLoadSettings(state, {all: true});
  else if (f.who.startsWith('bots:') && !state.settingsBy.has(f.who.slice(5))) await ruLoadSettings(state, {bot: f.who.slice(5)});
  else return;
  if (RU === state && state.tab === 'internal') ruDrawKeepSearch();
}

// ----------------------------------------------------------------- the page
async function pageRules() {
  const tab = ruTabOf(S.route);
  $('#main').classList.add('ru-layout');
  if (!RU || !document.body.contains(RU.root)) {
    $('#main').innerHTML = `<div class="ru" id="ru-root">
      <header class="ru-ph"><h1>Rules</h1><nav class="ru-tabs" aria-label="Rules"></nav><span class="spacer"></span><span class="ru-acts"></span></header>
      <div class="ru-body"><aside class="ru-filters" aria-label="Filters"></aside><section class="ru-list" aria-live="polite">${skelRows(6)}</section>
        <aside class="ru-side" id="ru-side" hidden></aside></div></div>`;
    RU = {root: $('#ru-root'), tab: null, counts: null, rules: {internal: [], external: []}, suggested: [], due: [], sources: [],
          settings: [], settingsBy: new Map(), settingsLeft: 0, filter: {q: '', who: '', with: '', from: '', due: false, status: '', routine: '', settings: false, moreFrom: false},
          seq: 0, loaded: new Set(), error: ''};
    ruWire();
    void ruleChecksMount(RU.root.querySelector('.ru-ph'));
  }
  const state = RU;
  if (state.tab !== tab) {
    state.tab = tab;
    state.filter = {...state.filter, who: '', with: '', from: '', due: false, status: '', routine: '', q: ''};
    ruDrawHead();
    if (!state.loaded.has(tab)) state.root.querySelector('.ru-list').innerHTML = skelRows(6);
    else ruDraw();
    if (!await ruLoad(tab) || RU !== state || state.tab !== tab) return;
  } else if (!state.loaded.has(tab)) {   // a write elsewhere on the page marked it stale
    if (!await ruLoad(tab) || RU !== state || state.tab !== tab) return;
  }
  ruDraw();
  ruDrawPanel();
}
function rulesStop() { RU = null; }

function ruDrawHead() {
  const c = RU.counts || {};
  RU.root.querySelector('.ru-tabs').innerHTML = RU_TABS.map(([k, label]) =>
    `<a href="${ruHref(k)}" data-ru-tab="${k}"${RU.tab === k ? ' class="cur" aria-current="page"' : ''}>${label}${c[k] != null ? ` <span class="ru-cnt">· ${c[k]}</span>` : ''}</a>`).join('');
  const human = S.me?.role !== 'bot';
  const acts = {
    internal: human ? '<button class="primary small" type="button" data-ru-add>+ Add rule</button>' : '',
    external: human ? '<button class="ghost small" type="button" data-ru-add>+ Add rule</button><button class="primary small" type="button" data-ru-add-doc>+ Add document</button>' : '',
    due: human ? '<button class="primary small" type="button" data-ru-add-due>+ Add due date</button>' : '',
    suggested: '',
  }[RU.tab];
  RU.root.querySelector('.ru-acts').innerHTML = acts;
}
function ruDraw() {
  ruDrawHead();
  const root = RU.root, list = root.querySelector('.ru-list'), filters = root.querySelector('.ru-filters');
  root.dataset.tab = RU.tab;
  if (RU.error) { filters.innerHTML = ''; list.innerHTML = `<p class="err ru-pad">${esc(RU.error)}</p>`; return; }
  const draw = {internal: ruDrawInternal, external: ruDrawExternal, due: ruDrawDue, suggested: ruDrawSuggested}[RU.tab];
  draw(filters, list);
}

// Draws again, the search box keeping its focus and caret when it had them.
function ruDrawKeepSearch() {
  const box = RU.root.querySelector('.ru-q'), had = document.activeElement === box, at = box?.selectionStart;
  ruDraw();
  if (!had) return;
  const q = RU.root.querySelector('.ru-q'); q?.focus(); q?.setSelectionRange(at, at);
}
// One facet row: a label, its count, pressed when it is the filter.
const ruFacet = (key, value, label, n, on) => `<button type="button" class="ru-f" data-ru-f="${esc(key)}" data-v="${esc(value)}" aria-pressed="${on}"><span>${esc(label)}</span><span class="ru-n">${n}</span></button>`;
// The Who (or With) facets of a list of rules, by how many rules name each.
function ruScopeFacets(rules, key, field) {
  const counts = new Map(), label = new Map();
  const add = (k, l) => { counts.set(k, (counts.get(k) || 0) + 1); label.set(k, l); };
  for (const r of rules) {
    const s = r[field]; if (!s) continue;
    if (s.everyone) add('everyone', 'Everyone');
    if (s.all_bots) add('all_bots', 'All bots');
    for (const kind of ['groups', 'bots', 'people']) for (const x of s[kind] || []) add(kind + ':' + x.id, x.name || x.id);
  }
  const cur = RU.filter[key];
  return [...counts].sort((a, b) => b[1] - a[1] || label.get(a[0]).localeCompare(label.get(b[0])))
    .map(([k, n]) => ruFacet(key, k, label.get(k), n, cur === k)).join('');
}
const ruScopeHas = (scope, key) => {
  if (!key) return true;
  if (!scope) return false;
  if (key === 'everyone') return !!scope.everyone;
  if (key === 'all_bots') return !!scope.all_bots;
  const [kind, id] = key.split(/:(.*)/);
  return (scope[kind] || []).some(x => x.id === id);
};
const ruMatches = (text, q) => !q || q.toLowerCase().split(/\s+/).filter(Boolean).every(w => text.toLowerCase().includes(w));
const ruSearch = () => `<input class="ru-q" type="search" placeholder="Search" aria-label="Search rules" autocomplete="off" value="${esc(RU.filter.q)}">`;
const ruSel = id => ruQuery().get('rule') === id;

function ruDrawInternal(filters, list) {
  const all = RU.rules.internal, f = RU.filter;
  filters.innerHTML = `${ruSearch()}
    <div class="ru-fh">Who</div>${ruScopeFacets(all, 'who', 'who') || '<div class="ru-fnone">—</div>'}
    <div class="ru-fh">With</div>${ruScopeFacets(all, 'with', 'with') || '<div class="ru-fnone">—</div>'}
    <div class="ru-fh">Show</div><label class="ru-f ru-toggle"><span>Settings too</span><input type="checkbox" data-ru-settings${f.settings ? ' checked' : ''}></label>`;
  const rows = all.filter(r => ruScopeHas(r.who, f.who) && ruScopeHas(r.with, f.with) && ruMatches(r.text + ' ' + r.description, f.q));
  const head = '<div class="ru-row ru-head" role="row"><span>Rule</span><span>Who</span><span>With</span><span>Due</span></div>';
  const body = rows.map(r => `<a class="ru-row${ruSel(r.id) ? ' sel' : ''}" role="row" href="${ruHref('internal', {rule: r.id})}" data-rule="${esc(r.id)}">
      <span class="ru-txt">${esc(r.text)}</span><span class="ru-c" data-l="Who">${esc(ruScopeWords(r.who))}</span>
      <span class="ru-c${ruScopeWords(r.with) === '—' ? ' ru-none' : ''}" data-l="With">${esc(ruScopeWords(r.with))}</span><span class="ru-c" data-l="Due">${ruDueCell(r)}</span></a>`).join('');
  const settings = f.settings && !f.with ? RU.settings.filter(s => ruMatches(s.text, f.q) && (!f.who || f.who === 'bots:' + s.bot || (f.who === 'all_bots' && !s.bot)))
    .map(s => `<div class="ru-row ru-setting" role="row"><span class="ru-txt">${esc(s.text)}</span><span class="ru-c" data-l="Who">${esc(s.who)}</span><span class="ru-c ru-none">—</span>
      <span class="ru-c">${ruSettingLink(s, s.bot)}</span></div>`).join('') : '';
  const more = f.settings && !f.with && RU.settingsLeft
    ? `<button type="button" class="ru-row ru-more-row" data-ru-settings-more>More bots' settings · ${RU.settingsLeft}</button>` : '';
  list.innerHTML = `<div class="ru-table ru-int" role="table">${head}${body}${settings}${more}</div>${body || settings ? '' : `<p class="muted ru-pad">${all.length ? 'Nothing matches.' : 'No internal rules yet.'}</p>`}`;
}
// A setting opens where it is changed; a fixed limit has no editor and no link. Spend limits and task caps are edited on
// Strategy › Budget (a bot's node, or the company's or a group's pool, named by the descriptor's path); access and run
// budgets on the bot's Settings tab, scrolled to their row; mail sending in its own dialog (ruSettingGo).
function ruSettingTarget(s, bot) {
  const e = s.edit, slug = encodeURIComponent(bot), botSettings = '#/bot/' + slug + '/settings';
  if (!e) return null;
  if (e.page === 'strategy') {
    const pool = /\/usage\/pools\/groups\/(.+)$/.exec(e.path || '');
    return {href: RD_BUDGET + '/' + (pool ? 'group:' + pool[1] : 'company'), kind: 'budget'};
  }
  if (e.anchor === 'spend' || e.anchor === 'tasks') return {href: RD_BUDGET + '/bot:' + slug, kind: 'budget'};
  if (s.source === 'mail_settings') return {href: botSettings, kind: 'mail'};
  if (e.anchor === 'access') return {href: botSettings, kind: 'access'};
  if (e.anchor === 'turns') return {href: botSettings, kind: 'turns'};
  return {href: e.page === 'settings' ? '#/settings/' + encodeURIComponent(e.anchor || '') : botSettings, kind: ''};
}
function ruSettingLink(s, bot) {
  const to = ruSettingTarget(s, bot);
  if (!to) return '<span class="ru-none">—</span>';
  return `<a class="ru-set" href="${esc(to.href)}" data-ru-setting="${to.kind}" data-bot="${esc(bot)}">Setting ›</a>`;
}

function ruDrawExternal(filters, list) {
  const all = RU.rules.external, f = RU.filter;
  const from = new Map();
  for (const r of all) if (r.source) { const was = from.get(r.source.id); from.set(r.source.id, {name: r.source.name, n: (was?.n || 0) + 1}); }
  const sorted = [...from].sort((a, b) => b[1].n - a[1].n || a[1].name.localeCompare(b[1].name));
  const shown = f.moreFrom ? sorted : sorted.slice(0, 5);
  const withDue = all.filter(r => (r.due || []).length).length;
  filters.innerHTML = `${ruSearch()}
    <div class="ru-fh">From</div>${shown.map(([id, s]) => ruFacet('from', id, s.name, s.n, f.from === id)).join('') || '<div class="ru-fnone">—</div>'}
    ${sorted.length > 5 && !f.moreFrom ? `<button type="button" class="ru-f ru-more" data-ru-more>${sorted.length - 5} more</button>` : ''}
    <div class="ru-fh">Who</div>${ruScopeFacets(all, 'who', 'who') || '<div class="ru-fnone">—</div>'}
    <div class="ru-fh">Due</div>${ruFacet('due', '1', 'Has a due date', withDue, !!f.due)}`;
  const rows = all.filter(r => (!f.from || r.source?.id === f.from) && ruScopeHas(r.who, f.who) && (!f.due || (r.due || []).length)
    && ruMatches(r.text + ' ' + r.description + ' ' + (r.source?.name || ''), f.q));
  const head = '<div class="ru-row ru-head" role="row"><span>Rule</span><span>From</span><span>Who</span><span>Due</span></div>';
  const body = rows.map(r => `<a class="ru-row${ruSel(r.id) ? ' sel' : ''}" role="row" href="${ruHref('external', {rule: r.id})}" data-rule="${esc(r.id)}">
      <span class="ru-txt">${esc(r.text)}</span><span class="ru-c" data-l="From">${r.source ? `<span class="ru-src ${esc(r.source.kind)}">${esc(r.source.name)}</span>` : '—'}</span>
      <span class="ru-c" data-l="Who">${esc(ruScopeWords(r.who))}</span><span class="ru-c" data-l="Due">${ruDueCell(r)}</span></a>`).join('');
  list.innerHTML = `<div class="ru-table ru-ext" role="table">${head}${body}</div>${body ? '' : `<p class="muted ru-pad">${all.length ? 'Nothing matches.' : 'No external rules yet.'}</p>`}`;
}

function ruDrawDue(filters, list) {
  const all = RU.due, f = RU.filter;
  const by = k => all.filter(d => d.status === k).length;
  filters.innerHTML = `<div class="ru-fh">Status</div>${Object.entries(RU_STATUS).map(([k, [label]]) => ruFacet('status', k, label, by(k), f.status === k)).join('')}
    <div class="ru-fh">Routine</div>${ruFacet('routine', 'on', 'Task', all.filter(d => d.task_days != null).length, f.routine === 'on')}${ruFacet('routine', 'none', 'None', all.filter(d => d.task_days == null).length, f.routine === 'none')}`;
  const rows = all.filter(d => (!f.status || d.status === f.status) && (!f.routine || (f.routine === 'on') === (d.task_days != null)));
  const open = ruQuery().get('due');
  const head = '<div class="ru-row ru-head" role="row"><span>Due</span><span>Rule</span><span>Repeats</span><span>Routine</span><span>Owner</span><span>Status</span></div>';
  const body = rows.map(d => `<a class="ru-row${open === d.id ? ' sel' : ''}" role="row" href="${ruHref('due', {due: d.id})}" data-due="${esc(d.id)}">
      <span class="ru-c ru-day">${esc(ruDay(d.due_on))}</span><span class="ru-txt">${esc(d.rule.text)}</span>
      <span class="ru-c" data-l="Repeats">${esc(ruRepeatWord(d.repeats))}</span>
      <span class="ru-c${d.task_days != null ? '' : ' ru-none'}" data-l="Routine">${esc(ruLead(d.task_days) || '—')}</span>
      <span class="ru-c ru-owner" data-l="Owner">${ruOwnerHTML(d.owner)}</span><span class="ru-c">${ruPill(d.status)}</span></a>`).join('');
  list.innerHTML = `<div class="ru-table ru-duet" role="table">${head}${body}</div>${body ? '' : `<p class="muted ru-pad">${all.length ? 'Nothing matches.' : 'No due dates yet.'}</p>`}`;
}
function ruOwnerHTML(actor) {
  const [kind, id] = String(actor || '').split(/:(.*)/);
  const face = kind === 'bot' ? avatar(id, 18) : personAvatar((S.people || []).find(p => p.id === id) || {id, name: id}, 18);
  return `${face}<span>${esc(ruActorName(actor))}</span>`;
}

// Suggested: grouped by From, each row the sentence, a Who picker, and Skip / Add. The sentence opens the rule's side
// panel, where its quote is; the row names no document and shows no quote.
function ruDrawSuggested(filters, list) {
  filters.innerHTML = '';
  const groups = new Map();
  for (const r of RU.suggested) {
    const key = r.source?.id || '', name = r.source?.name || 'Internal';
    if (!groups.has(key)) groups.set(key, {name, rows: []});
    groups.get(key).rows.push(r);
  }
  // A suggestion with no Who yet (Counsel may leave it open) is added only once its picker names one; Add all adds the
  // rows that have one and leaves the rest waiting.
  list.innerHTML = [...groups].map(([key, g]) => `<section class="ru-sg" data-group="${esc(key)}"><header><h2>${esc(g.name)}</h2><span class="spacer"></span>
        <button class="primary small" type="button" data-ru-add-all="${esc(key)}" hidden></button></header>
      ${g.rows.map(r => { const who = ruScopeWords(r.who, ''); return `<div class="ru-prop${ruSel(r.id) ? ' sel' : ''}" data-rule="${esc(r.id)}"${who ? ' data-has-who' : ''}><a class="ru-pt" href="${ruHref('suggested', {rule: r.id})}">${esc(r.text)}</a>
        <select class="ru-pwho" aria-label="Who" data-ru-who${r.can_edit ? '' : ' disabled'}><option value="">${esc(who || 'Pick who')}</option>${ruWhoOptions()}</select>
        <span class="ru-pacts">${r.can_edit ? `<button class="ghost small" type="button" data-ru-skip>Skip</button><button class="primary small" type="button" data-ru-accept${who ? '' : ' disabled'}>Add</button>` : ''}</span></div>`; }).join('')}</section>`)
    .join('') || '<p class="muted ru-pad">No suggestions.</p>';
  list.querySelectorAll('.ru-sg').forEach(ruAddAllSync);
}
// The rows of a group that can be added now: the viewer may, and a Who is set.
const ruReady = group => [...group.querySelectorAll('.ru-prop')].filter(p => {
  const add = p.querySelector('[data-ru-accept]');
  return add && (p.hasAttribute('data-has-who') || p.querySelector('[data-ru-who]').value);
});
function ruAddAllSync(group) {
  const n = ruReady(group).length, button = group.querySelector('[data-ru-add-all]');
  button.hidden = n < 2;
  button.textContent = `Add all ${n}`;
}
// A single Who, as the Suggested picker offers it.
function ruWhoOptions() {
  const opt = (v, l) => `<option value="${esc(v)}">${esc(l)}</option>`;
  const groups = ruGroups(), bots = ruBots(), people = ruPeople();
  return (settingsIsAdmin() ? opt('everyone', 'Everyone') : '') + opt('all_bots', 'All bots')
    + (groups.length ? `<optgroup label="Departments">${groups.map(g => opt('groups:' + g.id, g.name || g.id)).join('')}</optgroup>` : '')
    + (bots.length ? `<optgroup label="Bots">${bots.map(b => opt('bots:' + b.id, b.name)).join('')}</optgroup>` : '')
    + (people.length ? `<optgroup label="People">${people.map(p => opt('people:' + p.id, p.name)).join('')}</optgroup>` : '');
}
function ruWhoFromOption(v) {
  if (!v) return null;
  if (v === 'everyone') return {everyone: true};
  if (v === 'all_bots') return {all_bots: true};
  const [kind, id] = v.split(/:(.*)/);
  return {[kind]: [id]};
}

// ----------------------------------------------------------------- events
function ruWire() {
  const root = RU.root;
  root.addEventListener('input', ev => {
    if (ev.target.matches('.ru-q')) {
      RU.filter.q = ev.target.value.trim();
      ruDrawKeepSearch();
      void ruSettingsReach(RU);
    }
  });
  root.addEventListener('change', async ev => {
    if (ev.target.matches('[data-ru-settings]')) {
      RU.filter.settings = ev.target.checked;
      if (RU.filter.settings && !RU.settingsBy.size) await ruLoadSettings(RU);
      ruDraw();
      void ruSettingsReach(RU);
    }
    const pick = ev.target.closest('.ru-prop [data-ru-who]');
    if (pick) {
      const prop = pick.closest('.ru-prop'), add = prop.querySelector('[data-ru-accept]');
      if (add) add.disabled = !prop.hasAttribute('data-has-who') && !pick.value;
      ruAddAllSync(prop.closest('.ru-sg'));
    }
  });
  root.addEventListener('click', ev => {
    const t = ev.target;
    const facet = t.closest('[data-ru-f]');
    if (facet) {
      const key = facet.dataset.ruF, v = facet.dataset.v;
      RU.filter[key] = key === 'due' ? !RU.filter.due : RU.filter[key] === v ? '' : v;
      ruDraw(); void ruSettingsReach(RU); return;
    }
    if (t.closest('[data-ru-more]')) { RU.filter.moreFrom = true; ruDraw(); return; }
    const moreSet = t.closest('[data-ru-settings-more]');
    if (moreSet) { moreSet.disabled = true; const state = RU; void ruLoadSettings(state).then(() => { if (RU === state) ruDraw(); }); return; }
    if (t.closest('[data-ru-add]')) { ruAddOpen({kind: RU.tab === 'external' ? 'external' : 'internal'}); return; }
    if (t.closest('[data-ru-add-due]')) { location.hash = ruHref('due', {due: 'new'}); return; }
    if (t.closest('[data-ru-add-doc]')) { void ruDocOpen(); return; }
    const prop = t.closest('.ru-prop');
    if (prop && t.closest('[data-ru-accept]')) { void ruAccept([prop]); return; }
    if (prop && t.closest('[data-ru-skip]')) { void ruSkip(prop); return; }
    const all = t.closest('[data-ru-add-all]');
    if (all) { void ruAccept(ruReady(all.closest('.ru-sg'))); return; }
    const set = t.closest('[data-ru-setting]');
    if (set) ruSettingGo(set, ev);
  });
}
// After a bot setting's link lands on the bot's Settings tab: its row in view, or (mail) its dialog open.
function ruSettingGo(link, ev) {
  const kind = link.dataset.ruSetting, slug = link.dataset.bot;
  if (ev.metaKey || ev.ctrlKey || !['access', 'turns', 'mail'].includes(kind)) return;
  const sel = kind === 'access' ? '#bot-access-card' : kind === 'turns' ? `[data-bot-budget="${CSS.escape(slug)}"]` : '';
  const started = Date.now();
  // The Settings tab draws after the route; look for the row until it shows (or give up after a few seconds).
  const look = () => {
    if (!location.hash.startsWith('#/bot/' + encodeURIComponent(slug) + '/settings')) return;
    if (kind === 'mail') { if (typeof settingsEditMail === 'function' && $('#bot-editor .sb-row')) { void settingsEditMail(slug); return; } }
    else { const el = $(sel); if (el) { (el.closest('.sb-row,section') || el).scrollIntoView({block: 'center'}); return; } }
    if (Date.now() - started < 5000) setTimeout(look, 150);
  };
  setTimeout(look, 150);
}
async function ruAccept(props) {
  const written = [];
  for (const prop of props) {
    const r = RU.suggested.find(x => x.id === prop.dataset.rule);
    if (!r) continue;
    const who = ruWhoFromOption(prop.querySelector('[data-ru-who]').value);
    prop.querySelectorAll('button').forEach(b => { b.disabled = true; });
    written.push(r.id);
    try { await post('/v2/rules/' + encodeURIComponent(r.id) + '/accept', {version: r.version, ...(who ? {who} : {})}); }
    catch (e) { toast(ruError(e), true); break; }
  }
  await ruReload(written);
}
async function ruSkip(prop) {
  const r = RU.suggested.find(x => x.id === prop.dataset.rule);
  if (!r) return;
  try { await post('/v2/rules/' + encodeURIComponent(r.id) + '/skip', {version: r.version}); }
  catch (e) { toast(ruError(e), true); }
  await ruReload([r.id]);
}
// After a write: the open tab and the counts read again; the other tabs read again when shown. `written` names rules
// a list row wrote: when the open panel shows one of them, it reads that rule again, and on Suggested it closes once
// the rule is no longer a suggestion (a row's Add, Skip or Add all leaves the panel's copy stale otherwise).
async function ruReload(written = []) {
  if (!RU) return;
  RU.loaded = new Set([RU.tab]);
  RU.settings = []; RU.settingsBy = new Map(); RU.settingsLeft = 0;
  if (!await ruLoad(RU.tab)) return;
  const open = RU.tab === 'due' ? '' : ruQuery().get('rule'), side = $('#ru-side');
  if (open && written.includes(open)) {
    if (side) side.dataset.open = '';
    if (RU.tab === 'suggested' && !RU.suggested.some(r => r.id === open)) { location.hash = ruHref(RU.tab); return; }
  }
  ruDraw(); ruDrawPanel();
}
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || !RU || !document.body.contains(RU.root) || $('dialog[open]')) return;
  const side = $('#ru-side');
  if (side && !side.hidden) { ev.preventDefault(); location.hash = ruHref(RU.tab); }
});

// ----------------------------------------------------------------- the scope editor (Who, With)
// Segments pick what "+ add" offers; the chips are everything chosen. Everyone (Who) is the owner's and admins' alone;
// Anyone (With) clears it. Its state is {everyone, all_bots, groups, bots, people, seg}, ids only.
function ruScopeEditor(host, scope, {flags, disabled = false, onChange = () => {}}) {
  const s = {...ruScopeIds(scope)};
  s.seg = s.everyone ? 'everyone' : s.all_bots || s.bots.length ? 'bots' : s.groups.length ? 'groups' : s.people.length ? 'people' : flags ? 'groups' : 'anyone';
  const draw = () => {
    const segs = flags ? [['everyone', 'Everyone'], ['groups', 'Departments'], ['bots', 'Bots'], ['people', 'People']]
      : [['anyone', 'Anyone'], ['groups', 'Departments'], ['bots', 'Bots'], ['people', 'People']];
    const chips = [
      ...(s.all_bots ? [['all_bots', '', 'All bots']] : []),
      ...s.groups.map(id => ['groups', id, ruGroupName(id)]), ...s.bots.map(id => ['bots', id, botDisplayName(id)]),
      ...s.people.map(id => ['people', id, ruPersonName(id)])];
    const pool = {groups: ruGroups().map(g => ({id: g.id, name: g.name || g.id})), bots: ruBots(), people: ruPeople()}[s.seg] || [];
    const options = [...(s.seg === 'bots' && flags && !s.all_bots ? [['all_bots', 'All bots']] : []),
      ...pool.filter(x => !s[s.seg].includes(x.id)).map(x => [x.id, x.name])];
    const adding = !disabled && !s.everyone && s.seg !== 'anyone' && options.length;
    host.innerHTML = `<div class="ru-seg" role="group">${segs.map(([k, label]) => {
        const on = k === 'everyone' ? s.everyone : k === 'anyone' ? s.seg === 'anyone' : !s.everyone && s.seg === k;
        const off = disabled || (k === 'everyone' && !settingsIsAdmin());
        return `<button type="button" data-seg="${k}" aria-pressed="${on}"${off ? ' disabled' : ''}>${label}</button>`; }).join('')}</div>
      ${s.everyone || (s.seg === 'anyone' && !chips.length) ? '' : `<div class="ru-chips">${chips.map(([k, id, label]) =>
        `<span class="ru-chip">${esc(label)}${disabled ? '' : `<button type="button" data-unchip="${k}" data-id="${esc(id)}" aria-label="Remove ${esc(label)}">✕</button>`}</span>`).join('')}
        ${adding ? `<select class="ru-addsel" aria-label="Add"><option value="">+ add</option>${options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select>` : ''}</div>`}`;
  };
  host.onclick = ev => {
    const seg = ev.target.closest('[data-seg]');
    if (seg && !seg.disabled) {
      const k = seg.dataset.seg;
      if (k === 'everyone') Object.assign(s, {everyone: true, all_bots: false, groups: [], bots: [], people: []});
      else if (k === 'anyone') Object.assign(s, {groups: [], bots: [], people: []});
      else s.everyone = false;
      s.seg = k; draw(); onChange(); return;
    }
    const un = ev.target.closest('[data-unchip]');
    if (un) {
      if (un.dataset.unchip === 'all_bots') s.all_bots = false;
      else s[un.dataset.unchip] = s[un.dataset.unchip].filter(x => x !== un.dataset.id);
      draw(); onChange();
    }
  };
  host.onchange = ev => {
    if (!ev.target.matches('.ru-addsel') || !ev.target.value) return;
    if (ev.target.value === 'all_bots') s.all_bots = true;
    else s[s.seg].push(ev.target.value);
    draw(); onChange();
    host.querySelector('.ru-addsel')?.focus();
  };
  draw();
  return {value: () => flags ? {everyone: s.everyone, all_bots: s.all_bots, groups: s.groups, bots: s.bots, people: s.people}
    : {groups: s.groups, bots: s.bots, people: s.people}};
}
const ruSegHTML = (name, options, value, disabled) => `<div class="ru-seg" role="group" data-ru-seg="${name}">${options.map(([k, label]) =>
  `<button type="button" data-v="${k}" aria-pressed="${k === value}"${disabled ? ' disabled' : ''}>${label}</button>`).join('')}</div>`;
function ruSegWire(host, onChange = () => {}) {
  host.addEventListener('click', ev => {
    const b = ev.target.closest('[data-ru-seg] [data-v]');
    if (!b || b.disabled) return;
    b.parentElement.querySelectorAll('[data-v]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    onChange(b.parentElement.dataset.ruSeg, b.dataset.v);
  });
}
const ruSegValue = (host, name) => host.querySelector(`[data-ru-seg="${name}"] [aria-pressed="true"]`)?.dataset.v || '';
const ruSourceOptions = (sources, value) => sources.map(s => `<option value="${esc(s.id)}"${s.id === value ? ' selected' : ''}>${esc(s.name)}</option>`).join('');

// ----------------------------------------------------------------- the side panels
function ruDrawPanel() {
  const side = $('#ru-side'), q = ruQuery();
  if (!side) return;
  const ruleId = RU.tab === 'due' ? '' : q.get('rule'), dueId = RU.tab === 'due' ? q.get('due') : '';
  const want = ruleId ? 'rule:' + ruleId : dueId ? 'due:' + dueId + ':' + (q.get('rule') || '') : '';
  RU.root.classList.toggle('ru-open', !!want);
  if (!want) { side.hidden = true; side.innerHTML = ''; side.dataset.open = ''; return; }
  side.hidden = false;
  if (side.dataset.open === want) return;
  side.dataset.open = want;
  // A note ("Saved", "Changed elsewhere") left for this panel shows in its status line, inside the sheet on a phone.
  const key = ruleId ? 'rule:' + ruleId : 'due:' + dueId, note = RU.note?.key === key ? RU.note.text : '';
  RU.note = null;
  if (ruleId) void ruRulePanel(side, ruleId, note);
  else void ruDuePanel(side, dueId, q.get('rule') || '', note);
}
// Reload, then draw `key`'s panel again with `text` only if the address still names it.
async function ruRedraw(side, key, text) {
  RU.note = {key, text};
  side.dataset.open = '';
  await ruReload();
}
// The cited section and quote, where the viewer may read its file (`hidden` otherwise: nothing shows). The section
// opens that exact file version in the viewer (ui/app/viewer.js reads /api/v2/files/<id>?v=<n> links).
function ruQuoteHTML(cite) {
  if (!cite || cite.hidden || !cite.quote) return '';
  const label = esc(cite.section || 'Document');
  const src = cite.file_id
    ? `<a class="ru-qsrc" href="${API}/v2/files/${encodeURIComponent(cite.file_id)}${cite.version ? '?v=' + encodeURIComponent(cite.version) : ''}" data-name="${label}">${label}</a>`
    : `<b>${label}</b>`;
  return `<blockquote class="ru-quote">${src} “${esc(cite.quote)}”</blockquote>`;
}
const ruPanelHead = (title, close) => `<div class="ru-side-h"><h2>${esc(title)}</h2><span class="spacer"></span><a class="ru-x" href="${close}" aria-label="Close">✕</a></div>`;

async function ruRulePanel(side, id, note = '') {
  const want = side.dataset.open;
  side.innerHTML = ruPanelHead('Rule', ruHref(RU.tab)) + skelRows(4);
  let r;
  try { r = await get('/v2/rules/' + encodeURIComponent(id), {fresh: true}); }
  catch (e) { if (side.dataset.open === want) side.innerHTML = ruPanelHead('Rule', ruHref(RU.tab)) + `<p class="err ru-pad">${esc(e.message)}</p>`; return; }
  if (side.dataset.open !== want) return;
  const edit = !!r.can_edit, dis = edit ? '' : ' disabled', internal = r.kind === 'internal';
  if (!internal && !RU.sources.length) RU.sources = await ruAll('/v2/rule-sources').catch(() => []);
  const dues = ruDueSorted(r);
  const quote = ruQuoteHTML(r.cite);
  const suggested = r.status === 'suggested';
  side.innerHTML = `${ruPanelHead('Rule', ruHref(RU.tab))}
    <form class="ru-form" data-rule="${esc(r.id)}">
      <label class="ru-fl"><span class="ru-lbl">Rule</span><textarea name="text" rows="2" maxlength="1000" required${dis}>${esc(r.text)}</textarea></label>
      <label class="ru-fl"><span class="ru-lbl">Description</span><textarea name="description" rows="2" maxlength="2000"${dis}>${esc(r.description || '')}</textarea></label>
      <div class="ru-fl"><span class="ru-lbl">Who</span><div data-scope="who"></div></div>
      ${internal ? '<div class="ru-fl"><span class="ru-lbl">With <span class="ru-opt">(optional)</span></span><div data-scope="with"></div></div>'
        : `<label class="ru-fl"><span class="ru-lbl">From</span><select name="source"${dis}>${ruSourceOptions(RU.sources, r.source?.id)}</select></label>`}
      <div class="ru-fl"><span class="ru-lbl">If broken</span>${ruSegHTML('if_broken', RU_BROKEN, r.if_broken, !edit)}
        <label class="ru-reviewer"${r.if_broken === 'escalate' ? '' : ' hidden'}><span class="ru-lbl">Reviewer <span class="ru-opt">(optional)</span></span>
          <select name="reviewer"${dis}><option value="">Manager</option>${ruPeople().map(p => `<option value="${esc(p.id)}"${r.reviewer?.id === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label></div>
      <div class="ru-fl"><span class="ru-lbl">Due</span>${dues.map(due =>
          `<a class="ru-duebox" href="${ruHref('due', {due: due.id})}" data-due="${esc(due.id)}"><span>${esc(ruDay(due.due_on, true))}${due.repeats && due.repeats !== 'once' ? ' · ' + esc(ruRepeatWord(due.repeats).toLowerCase()) : ''}${due.task_days != null ? ' · task ' + esc(ruLead(due.task_days)) : ''}</span>${ruPill(due.status)}<span class="spacer"></span><span class="muted">›</span></a>`).join('')}
        ${r.due_more ? `<a class="ru-adddue" href="${ruHref('due')}">${r.due_more} more</a>` : ''}
        ${edit ? `<a class="ru-adddue" href="${ruHref('due', {due: 'new', rule: r.id})}">+ Add due date</a>` : dues.length ? '' : '<span class="ru-none">—</span>'}</div>
      ${quote}
      <p class="ru-status" role="status">${esc(note)}</p>
      ${!edit ? '' : suggested
        ? `<div class="ru-foot"><button class="ghost" type="button" data-ru-skip-one>Skip</button><span class="spacer"></span><button class="ghost" type="submit">Save</button>
            <button class="primary" type="button" data-ru-accept-one>Add</button></div>`
        : '<div class="ru-foot"><button class="ghost" type="button" data-ru-retire>Retire</button><span class="spacer"></span><button class="primary" type="submit">Save</button></div>'}
    </form>`;
  const form = side.querySelector('form'), status = form.querySelector('.ru-status');
  // A suggestion is added only with a Who: Add waits until the editor names one.
  const addOne = form.querySelector('[data-ru-accept-one]');
  const whoSet = scope => scope.everyone || scope.all_bots || ['groups', 'bots', 'people'].some(k => scope[k].length);
  const who = ruScopeEditor(form.querySelector('[data-scope="who"]'), r.who, {flags: true, disabled: !edit,
    onChange: () => { if (addOne) addOne.disabled = !whoSet(who.value()); }});
  if (addOne) addOne.disabled = !whoSet(who.value());
  const withEd = internal ? ruScopeEditor(form.querySelector('[data-scope="with"]'), r.with, {flags: false, disabled: !edit}) : null;
  ruSegWire(form, (name, v) => { if (name === 'if_broken') form.querySelector('.ru-reviewer').hidden = v !== 'escalate'; });
  // The fields changed in the panel, as an edit sends them (`version` alone when nothing changed).
  const changes = () => {
    const body = {version: r.version};
    const text = form.elements.text.value.trim(), description = form.elements.description.value.trim();
    if (text !== r.text) body.text = text;
    if (description !== (r.description || '')) body.description = description;
    if (!ruSameScope(who.value(), r.who)) body.who = who.value();
    if (withEd && !ruSameScope(withEd.value(), r.with)) body.with = withEd.value();
    if (!internal && form.elements.source.value !== r.source?.id) body.source_id = form.elements.source.value;
    const broken = ruSegValue(form, 'if_broken');
    if (broken !== r.if_broken) body.if_broken = broken;
    // The reviewer shows only under Escalate; a stored one stays unless the person picks another.
    const reviewer = form.elements.reviewer.value;
    if (broken === 'escalate' && reviewer !== (r.reviewer?.id || '')) body.reviewer = reviewer;
    return body;
  };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = changes();
    if (Object.keys(body).length === 1) { status.textContent = 'Saved'; return; }
    const button = form.querySelector('[type=submit]'); button.disabled = true; status.textContent = 'Saving…';
    try {
      await post('/v2/rules/' + encodeURIComponent(r.id), body);
      if (side.dataset.open === want) await ruRedraw(side, 'rule:' + r.id, 'Saved');
      else await ruReload();
    } catch (e) {
      button.disabled = false;
      if (e.status === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  };
  // A suggestion: Add saves what was changed here, then accepts it; Skip sets it aside.
  addOne?.addEventListener('click', async () => {
    addOne.disabled = true; status.textContent = 'Saving…';
    try {
      const body = changes();
      let version = r.version;
      if (Object.keys(body).length > 1) version = (await post('/v2/rules/' + encodeURIComponent(r.id), body)).version;
      await post('/v2/rules/' + encodeURIComponent(r.id) + '/accept', {version});
      location.hash = ruHref(RU.tab);
      await ruReload();
      toast('Added');
    } catch (e) {
      addOne.disabled = false;
      if (e.status === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  });
  form.querySelector('[data-ru-skip-one]')?.addEventListener('click', async () => {
    try {
      await post('/v2/rules/' + encodeURIComponent(r.id) + '/skip', {version: r.version});
      location.hash = ruHref(RU.tab);
      await ruReload();
    } catch (e) { status.textContent = ruError(e); }
  });
  form.querySelector('[data-ru-retire]')?.addEventListener('click', async () => {
    if (!confirm('Retire this rule?')) return;
    try {
      const out = await post('/v2/rules/' + encodeURIComponent(r.id) + '/retire', {version: r.version});
      const dues = (out.due || []).filter(d => !d.retired_at);
      if (dues.length && confirm(dues.length === 1 ? 'Remove its due date too?' : `Remove its ${dues.length} due dates too?`))
        for (const d of dues) await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/remove', {});
      location.hash = ruHref(RU.tab);
      await ruReload();
      toast('Retired');
    } catch (e) {
      if (e.status === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  });
}

// One due date, or a new one (`id` "new"; `ruleId` when it comes from a rule's panel, else a rule is picked).
async function ruDuePanel(side, id, ruleId, note = '') {
  const want = side.dataset.open, close = ruHref('due');
  const fresh = id === 'new';
  // An existing one: GET /rule-due/<id> holds its history (edits included) and what the viewer may do.
  let d = null;
  if (!fresh) {
    const gone = () => { side.innerHTML = ruPanelHead('Due date', close) + '<p class="muted ru-pad">This due date is gone.</p>'; };
    side.innerHTML = ruPanelHead('Due date', close) + skelRows(3);
    try { d = await get('/v2/rule-due/' + encodeURIComponent(id), {fresh: true}); }
    catch (e) {
      if (side.dataset.open !== want) return;
      if (e.status === 404) gone(); else side.innerHTML = ruPanelHead('Due date', close) + `<p class="err ru-pad">${esc(e.message)}</p>`;
      return;
    }
    if (side.dataset.open !== want) return;
    // An edit ended this row and started another: open the live one.
    if (d.ended_on && d.replaced_by) { location.replace(ruHref('due', {due: d.replaced_by})); return; }
    if (d.retired_at || d.ended_on) { gone(); return; }
  }
  let rules = [];
  if (fresh && !ruleId) {
    side.innerHTML = ruPanelHead('Add due date', close) + skelRows(3);
    rules = (await ruAll('/v2/rules?status=active').catch(() => [])).filter(r => r.can_edit);
    if (side.dataset.open !== want) return;
  }
  let rule = null;
  if (fresh && ruleId) rule = await get('/v2/rules/' + encodeURIComponent(ruleId)).catch(() => null);
  if (side.dataset.open !== want) return;
  const edit = fresh ? true : !!d.can_edit, dis = edit ? '' : ' disabled';
  const opt = (v, l, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  const owner = d?.owner || '';
  const owners = `<optgroup label="Bots">${ruBots().map(b => opt('bot:' + b.id, b.name, owner)).join('')}</optgroup>
    <optgroup label="People">${ruPeople().map(p => opt('human:' + p.id, p.name, owner || myActor())).join('')}</optgroup>`;
  // Routine: none, or a task opened for the owner N days before each due day (an automation, linked by routine_id).
  const lead = d ? d.task_days ?? null : null;
  const marks = d ? (d.chain || []).flatMap(link => link.satisfied || []).sort((a, b) => b.due_on.localeCompare(a.due_on)) : [];
  const history = d ? [
    ...(d.status !== 'satisfied' ? [`<div class="ru-h"><span>${esc(ruDay(d.due_on, true))}</span><span class="spacer"></span>${ruPill(d.status)}</div>`] : []),
    ...marks.map(m => `<div class="ru-h ru-mark"><span>${esc(ruDay(m.due_on, true))}</span><span class="muted">${m.via === 'routine'
      ? 'task closed ' + esc(ruDay(m.at)) : 'marked by ' + esc(ruActorName(m.satisfied_by))}</span><span class="spacer"></span>${ruPill('satisfied')}</div>`)].join('') : '';
  const openDays = d?.open_days || [];
  const title = fresh ? 'Add due date' : d.rule.text;
  side.innerHTML = `${ruPanelHead(title, close)}
    <form class="ru-form">
      ${fresh ? `<label class="ru-fl"><span class="ru-lbl">Rule</span>${rule ? `<span class="ru-ruletext">${esc(rule.text)}</span>`
        : `<select name="rule" required><option value="">Pick a rule</option>${rules.map(r => opt(r.id, r.text, '')).join('')}</select>`}</label>` : ''}
      <div class="ru-two">
        <label class="ru-fl"><span class="ru-lbl">Due</span><input type="date" name="due" required value="${esc(d?.due_on || '')}"${dis}></label>
        <label class="ru-fl"><span class="ru-lbl">Repeats</span><select name="repeats"${dis}>${RU_REPEATS.map(([k, l]) => opt(k, l, d?.repeats || 'yearly')).join('')}</select></label>
        <label class="ru-fl"><span class="ru-lbl">Owner</span><select name="owner" required${dis}>${owners}</select></label>
        <div class="ru-fl"><span class="ru-lbl">Routine</span>
          <select name="routine" aria-label="Routine"${dis}>${opt('', 'None', lead == null ? '' : 'task')}${opt('task', 'Task', lead == null ? '' : 'task')}</select>
          <span class="ru-lead"${lead == null ? ' hidden' : ''}><input type="number" name="task_days" min="0" max="366" step="1" required value="${lead ?? 14}" aria-label="Days before"${lead == null || !edit ? ' disabled' : ''}><span>days before</span></span></div>
      </div>
      ${d?.routine_id && lead != null ? `<a class="ru-routine" href="#/automations/${encodeURIComponent(d.routine_id)}">Open routine ›</a>` : ''}
      ${d ? `<div class="ru-stat"><span class="ru-lbl">Status</span>${ruPill(d.status)}<span class="spacer"></span>
        ${d.can_satisfy && d.status !== 'satisfied' ? '<button class="ghost small" type="button" data-ru-satisfy>Mark satisfied</button>' : ''}</div>
        <div class="ru-satisfy-on" hidden><select name="due_on" aria-label="Which due date">${openDays.map(day => opt(day, ruDay(day, true), d.due_on)).join('')}</select><button class="ghost small" type="button" data-ru-satisfy-on>Mark</button></div>
        <div class="ru-fl"><span class="ru-lbl">History</span><div class="ru-hist">${history || '<span class="ru-none">—</span>'}</div></div>` : ''}
      <p class="ru-status" role="status">${esc(note)}</p>
      ${edit ? `<div class="ru-foot">${fresh ? '' : '<button class="ghost" type="button" data-ru-remove>Remove</button>'}<span class="spacer"></span><button class="primary" type="submit">Save</button></div>` : ''}
    </form>`;
  const form = side.querySelector('form'), status = form.querySelector('.ru-status');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = form.elements, button = form.querySelector('[type=submit]');
    button.disabled = true; status.textContent = 'Saving…';
    try {
      let saved;
      if (fresh) {
        const target = rule?.id || f.rule.value;
        const body = {first_due: f.due.value, repeats: f.repeats.value, owner: f.owner.value};
        if (f.routine.value === 'task') body.task_days = Number(f.task_days.value);
        saved = await post('/v2/rules/' + encodeURIComponent(target) + '/due', body);
      } else {
        // A new date or repeat starts again from the date shown (the next one due), not the first date ever set, so
        // occurrences already satisfied stay behind. An owner change alone keeps the anchor.
        const body = {};
        if (f.due.value !== d.due_on || f.repeats.value !== d.repeats) { body.first_due = f.due.value; body.repeats = f.repeats.value; }
        if (f.owner.value !== d.owner) body.owner = f.owner.value;
        const days = f.routine.value === 'task' ? Number(f.task_days.value) : null;
        if (days !== lead) body.task_days = days;
        saved = Object.keys(body).length ? await post('/v2/rule-due/' + encodeURIComponent(d.id), body) : d;
      }
      RU.loaded.delete('due');
      if (ruQuery().get('due') !== saved.id) { RU.note = {key: 'due:' + saved.id, text: 'Saved'}; location.hash = ruHref('due', {due: saved.id}); }
      else await ruRedraw(side, 'due:' + saved.id, 'Saved');
    } catch (e) { button.disabled = false; status.textContent = ruError(e); }
  };
  form.elements.routine.onchange = () => {
    const on = form.elements.routine.value === 'task', box = form.querySelector('.ru-lead');
    box.hidden = !on; form.elements.task_days.disabled = !on;
  };
  const satisfy = async dueOn => {
    status.textContent = 'Saving…';
    try {
      await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/satisfied', dueOn ? {due_on: dueOn} : {});
      await ruRedraw(side, 'due:' + d.id, 'Saved');
    } catch (e) { status.textContent = ruError(e); }
  };
  // More than one open occurrence: the server needs to know which one.
  form.querySelector('[data-ru-satisfy]')?.addEventListener('click', () => {
    if (openDays.length > 1) { form.querySelector('.ru-satisfy-on').hidden = false; return; }
    void satisfy('');
  });
  form.querySelector('[data-ru-satisfy-on]')?.addEventListener('click', () => void satisfy(form.elements.due_on.value));
  form.querySelector('[data-ru-remove]')?.addEventListener('click', async () => {
    if (!confirm('Remove this due date?')) return;
    try {
      await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/remove', {});
      location.hash = ruHref('due');
      await ruReload();
    } catch (e) { status.textContent = ruError(e); }
  });
}

// ----------------------------------------------------------------- Add rule
// Internal or External first; Internal asks Rule, Who and With, External asks Rule, Who and From (an existing one, or
// a new name with its kind). A person who can't manage what they wrote adds a suggestion, which the server decides.
async function ruAddOpen({kind = 'internal', who = null, after = null} = {}) {
  let dlg = document.getElementById('ru-add');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'ru-add'; dlg.className = 'ru-modal'; dlg.setAttribute('aria-label', 'Add rule');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
  }
  const sources = await ruAll('/v2/rule-sources').catch(() => []);
  dlg.innerHTML = `<form class="ru-form" method="dialog">
      <div class="ru-side-h"><h2>Add rule</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-ru-close aria-label="Close">✕</button></div>
      ${ruSegHTML('kind', [['internal', 'Internal'], ['external', 'External']], kind)}
      <label class="ru-fl"><span class="ru-lbl">Rule</span><textarea name="text" rows="2" maxlength="1000" required></textarea></label>
      <div class="ru-fl"><span class="ru-lbl">Who</span><div data-scope="who"></div></div>
      <div class="ru-fl" data-for="internal"><span class="ru-lbl">With <span class="ru-opt">(optional)</span></span><div data-scope="with"></div></div>
      <div class="ru-fl" data-for="external"><span class="ru-lbl">From</span>
        <select name="source"><option value="">Pick one</option>${ruSourceOptions(sources, '')}<option value="new">New…</option></select>
        <div class="ru-newsrc" hidden><input name="source_name" maxlength="200" placeholder="Name" aria-label="Name">
          <select name="source_kind" aria-label="Kind">${RU_KINDS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></div></div>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-ru-close>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`;
  const form = dlg.querySelector('form'), status = form.querySelector('.ru-status');
  const whoEd = ruScopeEditor(form.querySelector('[data-scope="who"]'), who || {}, {flags: true});
  const withEd = ruScopeEditor(form.querySelector('[data-scope="with"]'), {}, {flags: false});
  const show = k => form.querySelectorAll('[data-for]').forEach(el => { el.hidden = el.dataset.for !== k; });
  show(kind);
  ruSegWire(form, (name, v) => { if (name === 'kind') show(v); });
  form.elements.source.onchange = () => { form.querySelector('.ru-newsrc').hidden = form.elements.source.value !== 'new'; };
  form.querySelectorAll('[data-ru-close]').forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const k = ruSegValue(form, 'kind'), f = form.elements, button = form.querySelector('[type=submit]');
    const body = {kind: k, text: f.text.value.trim(), who: whoEd.value()};
    if (k === 'internal') body.with = withEd.value();
    button.disabled = true; status.textContent = 'Saving…';
    try {
      if (k === 'external') {
        if (f.source.value === 'new') {
          if (!f.source_name.value.trim()) throw new Error('Name the source.');
          body.source_id = (await post('/v2/rule-sources', {name: f.source_name.value.trim(), kind: f.source_kind.value})).source.id;
        } else if (f.source.value) body.source_id = f.source.value;
        else throw new Error('Pick who it is from.');
      }
      const made = await post('/v2/rules', body);
      dlg.close();
      toast(made.status === 'suggested' ? 'Added to Suggested' : 'Added');
      if (after) { after(made); return; }
      if (RU && document.body.contains(RU.root)) {
        RU.loaded.clear();
        const tab = made.status === 'suggested' ? 'suggested' : made.kind;
        if (RU.tab === tab) { await ruReload(); if (made.status === 'active') location.hash = ruHref(tab, {rule: made.id}); }
        else location.hash = ruHref(tab, made.status === 'active' ? {rule: made.id} : {});
      }
    } catch (e) { button.disabled = false; status.textContent = ruError(e); }
  };
  dlg.showModal();
  form.elements.text.focus();
}

// ----------------------------------------------------------------- Add document
// One file from Files (one the viewer can read) or a public link, From (an existing one, or a new name with its kind),
// and an optional description. POST /rules/read opens Counsel's task; the dialog then says Counsel is reading it and
// links the task. Asked again while that task is open, the server returns it (`existing`), and the dialog says so. When
// screening holds the document (`screening: "hold"`), Counsel is given none of its text, and the dialog says that instead.
const RU_DOC_SKIP = new Set(['image', 'media']);   // Files kinds Counsel reads no text from
const RU_DOC_PICKS = 6;
const ruIsLink = v => /^https?:\/\/\S+$/i.test(v);
// A refusal as one line: Counsel off, or the server's own reason (a file Counsel can't read and why, a link that didn't
// open and why, a file or From that isn't there), which is already one line for the person.
function ruDocError(e) {
  if (e?.body?.error?.code === 'counsel_off') return 'Counsel is off.';
  return ruError(e);
}
async function ruDocOpen() {
  let dlg = document.getElementById('ru-doc');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'ru-doc'; dlg.className = 'ru-modal'; dlg.setAttribute('aria-label', 'Add document');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
  }
  const [sources, recent] = await Promise.all([ruAll('/v2/rule-sources').catch(() => []),
    get('/v2/files?limit=50', {fresh: true}).then(r => r.files || []).catch(() => [])]);
  const readable = f => f.locator !== 'remote_link' && !f.url && !RU_DOC_SKIP.has(f.library_kind) && !f.archived;
  const files = recent.filter(readable);
  const head = '<div class="ru-side-h"><h2>Add document</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-ru-close aria-label="Close">✕</button></div>';
  dlg.innerHTML = `<form class="ru-form" method="dialog">${head}
      <div class="ru-fl"><span class="ru-lbl">Document</span>
        <div class="ru-doc" hidden><span class="nav-icon" aria-hidden="true">description</span><span class="ru-doc-t"></span>
          <button type="button" class="ru-doc-x" data-ru-unpick aria-label="Remove">✕</button></div>
        <input name="find" type="search" placeholder="Pick from Files or paste a link" aria-label="Pick from Files or paste a link" autocomplete="off">
        <div class="ru-picks" role="listbox" aria-label="Files"></div></div>
      <label class="ru-fl"><span class="ru-lbl">Description <span class="ru-opt">(optional)</span></span><textarea name="note" rows="2" maxlength="2000"></textarea></label>
      <div class="ru-fl"><span class="ru-lbl">From</span>
        <select name="source"><option value="">Pick one</option>${ruSourceOptions(sources, '')}<option value="new">New…</option></select>
        <div class="ru-newdoc" hidden><input name="source_name" maxlength="200" placeholder="Name" aria-label="Name">${ruSegHTML('source_kind', RU_KINDS, 'vendor')}</div></div>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-ru-close>Cancel</button><button class="primary" type="submit">Read and suggest rules</button></div>
    </form>`;
  const form = dlg.querySelector('form'), status = form.querySelector('.ru-status'), find = form.elements.find;
  const picks = form.querySelector('.ru-picks'), chosen = form.querySelector('.ru-doc');
  let pick = null, found = [], seq = 0, soon = 0;
  const pickRow = f => `<button type="button" class="ru-pick" role="option" data-id="${esc(f.id)}"><span class="nav-icon" aria-hidden="true">description</span>
      <span class="ru-pick-t">${esc(f.title || f.name || f.id)}</span><span class="ru-pick-m">${esc(pageBotName(f))}</span></button>`;
  // The list under the box: recent readable files whose title has every word typed, then search hits by title and text.
  const drawPicks = () => {
    const q = find.value.trim();
    if (pick || ruIsLink(q)) { picks.innerHTML = ''; picks.hidden = true; return; }
    const local = files.filter(f => ruMatches(f.title || f.name || '', q));
    const rows = [...local, ...found.filter(f => !local.some(x => x.id === f.id))].slice(0, RU_DOC_PICKS);
    picks.hidden = false;
    picks.innerHTML = rows.map(pickRow).join('') || `<p class="ru-pick-none">${q ? 'No files match.' : 'No files yet.'}</p>`;
  };
  const choose = f => {
    pick = f;
    chosen.hidden = !f;
    find.hidden = !!f;
    if (f) chosen.querySelector('.ru-doc-t').textContent = f.title || f.name || f.id;
    else { find.value = ''; find.focus(); }
    drawPicks();
  };
  find.oninput = () => {
    status.textContent = '';
    found = [];
    drawPicks();
    clearTimeout(soon);
    const q = find.value.trim(), mine = ++seq;
    if (!q || ruIsLink(q)) return;
    soon = setTimeout(async () => {
      const r = await get('/v2/files/search?q=' + encodeURIComponent(q) + '&limit=' + RU_DOC_PICKS).catch(() => null);
      if (mine !== seq || !dlg.open) return;
      found = (r?.results || []).filter(readable);
      drawPicks();
    }, 250);
  };
  picks.onclick = ev => {
    const b = ev.target.closest('.ru-pick');
    if (b) choose([...files, ...found].find(f => f.id === b.dataset.id) || null);
  };
  chosen.querySelector('[data-ru-unpick]').onclick = () => choose(null);
  form.elements.source.onchange = () => { form.querySelector('.ru-newdoc').hidden = form.elements.source.value !== 'new'; };
  ruSegWire(form);
  form.querySelectorAll('[data-ru-close]').forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = form.elements, button = form.querySelector('[type=submit]'), link = find.value.trim();
    const body = {note: f.note.value.trim()};
    if (pick) body.file_id = pick.id;
    else if (ruIsLink(link)) body.url = link;
    else { status.textContent = 'Pick a file or paste a link.'; return; }
    if (f.source.value === 'new') {
      if (!f.source_name.value.trim()) { status.textContent = 'Name the source.'; return; }
      body.source_id = f.source_name.value.trim();
      body.kind = ruSegValue(form, 'source_kind');
    } else if (f.source.value) body.source_id = f.source.value;
    else { status.textContent = 'Pick who it is from.'; return; }
    button.disabled = true; status.textContent = 'Reading…';
    let out;
    try { out = await post('/v2/rules/read', body); }
    catch (e) { button.disabled = false; status.textContent = ruDocError(e); return; }
    if (RU) RU.sources = [];
    const title = esc(out.file?.title || pick?.title || link);
    form.innerHTML = `${head}
      ${out.screening === 'hold'
        ? `<p class="ru-held" data-ru-read="${esc(out.task_id)}">Screening held <b>${title}</b>. Counsel won't suggest rules from it.</p>`
        : `<p class="ru-done" data-ru-read="${esc(out.task_id)}">${out.existing ? `Counsel is already reading <b>${title}</b>.` : `Counsel is reading <b>${title}</b>.`}</p>
      ${out.truncated ? '<p class="ru-warn">Long document: Counsel reads only the start.</p>' : ''}`}
      <div class="ru-foot"><a href="#/task/${encodeURIComponent(out.task_id)}" data-ru-task>Open task ›</a><span class="spacer"></span><button class="primary" type="button" data-ru-close>Done</button></div>`;
    form.querySelectorAll('[data-ru-close], [data-ru-task]').forEach(b => { b.addEventListener('click', () => dlg.close()); });
  };
  drawPicks();
  dlg.showModal();
  find.focus();
}

// ----------------------------------------------------------------- the bot's Rules tab
// Internal first (the written rules whose Who or With names the bot, then its settings as sentences, each opening the
// setting), then External. Every teammate reads it. Each kind is paged on its own: a More row reads its next page.
const ruBotRow = r => `<a class="ru-row" role="row" href="${ruHref(r.kind, {rule: r.id})}" data-rule="${esc(r.id)}"><span class="ru-txt">${esc(r.text)}</span>${r.kind === 'internal'
  ? `<span class="ru-c${ruScopeWords(r.with, 'Anyone') === 'Anyone' ? ' ru-none' : ''}" data-l="With">${esc(ruScopeWords(r.with, 'Anyone'))}</span><span class="ru-c" data-l="Who">${esc(ruScopeWords(r.who))}</span>`
  : `<span class="ru-c" data-l="From">${r.source ? `<span class="ru-src ${esc(r.source.kind)}">${esc(r.source.name)}</span>` : '—'}</span><span class="ru-c" data-l="Due">${ruDueCell(r)}</span>`}</a>`;
const ruBotMore = (kind, cursor) => cursor ? `<button type="button" class="ru-row ru-more-row" data-ru-bot-more="${kind}" data-cursor="${esc(cursor)}">More</button>` : '';
let RU_BOT_GEN = 0;   // each read of the tab; an older response (or its More) never paints over a newer one
async function botRulesLoad(slug) {
  const host = $('#bot-rules');
  if (!host) return;
  const gen = ++RU_BOT_GEN, current = () => gen === RU_BOT_GEN && BOT?.slug === slug && host.isConnected;
  if (!host.dataset.loaded) host.innerHTML = skelRows(4, {card: true});
  let data;
  try { data = await get('/v2/bots/' + encodeURIComponent(slug) + '/rules', {fresh: true}); }
  catch (e) { if (current()) host.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  if (!current()) return;
  host.dataset.loaded = '1';
  const internal = data.internal || {rules: []}, external = data.external || {rules: []}, settings = data.settings || [];
  const count = (page, extra = 0) => `${page.rules.length + extra}${page.next_cursor ? '+' : ''}`;
  host.innerHTML = `<section class="card ru-bot-card"><header><h2>Internal</h2><span class="muted" data-ru-n="internal">${count(internal, settings.length)}</span><span class="spacer"></span>
        ${S.me?.role !== 'bot' ? '<button class="ghost small" type="button" data-ru-bot-add>+ Add rule</button>' : ''}</header>
      <div class="ru-table ru-bot-int" role="table"><div class="ru-row ru-head" role="row"><span>Rule</span><span>With</span><span>Who</span></div>
        ${internal.rules.map(ruBotRow).join('')}${ruBotMore('internal', internal.next_cursor)}
        ${/* the settings follow every written rule: More adds its page above itself, so above these */ ''}
        ${settings.map(s => `<div class="ru-row ru-setting" role="row"><span class="ru-txt">${esc(s.text)}</span><span class="ru-c ru-none">—</span><span class="ru-c">${ruSettingLink(s, slug)}</span></div>`).join('')}
      </div></section>
    <section class="card ru-bot-card"><header><h2>External</h2><span class="muted">${count(external)}</span></header>
      ${external.rules.length ? `<div class="ru-table ru-bot-ext" role="table"><div class="ru-row ru-head" role="row"><span>Rule</span><span>From</span><span>Due</span></div>
        ${external.rules.map(ruBotRow).join('')}${ruBotMore('external', external.next_cursor)}</div>`
        : '<p class="empty">None.</p>'}</section>`;
  host.querySelector('[data-ru-bot-add]')?.addEventListener('click', () =>
    ruAddOpen({kind: 'internal', who: {bots: [slug]}, after: () => botRulesLoad(slug)}));
  host.onclick = async ev => {
    const set = ev.target.closest('[data-ru-setting]');
    if (set) { ruSettingGo(set, ev); return; }
    const more = ev.target.closest('[data-ru-bot-more]');
    if (!more || more.disabled) return;
    more.disabled = true;
    const kind = more.dataset.ruBotMore;
    try {
      const page = (await get(`/v2/bots/${encodeURIComponent(slug)}/rules?${kind}_cursor=${encodeURIComponent(more.dataset.cursor)}`, {fresh: true}))[kind];
      if (!current() || !more.isConnected) return;
      more.insertAdjacentHTML('beforebegin', page.rules.map(ruBotRow).join(''));
      if (page.next_cursor) { more.dataset.cursor = page.next_cursor; more.disabled = false; } else more.remove();
    } catch (e) { more.disabled = false; toast(e.message, true); }
  };
}
