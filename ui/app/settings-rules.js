/* ui/app/settings-rules.js — Settings > Rules: what wakes a bot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Every wake rule (backend/rules.py): the built-ins, read-only, and the table rows by scope. Each row reads
// WHEN <event> [AND <conditions>] THEN <outcome>, with its 7-day runs and no-op rate. A row the viewer may change
// (`can_change`, which the server also enforces) has a switch and Edit. Writes carry the row's revision, so a row
// someone else changed answers 409 instead of being overwritten.
let RULES = null, RULES_AT = 0, RULES_Q = '', RULES_SCOPE = '';
// One task's or one bot's rules (#/settings/rules?task=<id> or ?bot=<slug>): 'task:<id>', 'bot:<slug>' or '' for all.
let RULES_FOR = '', RULES_FOR_TITLE = '';
const rulesFor = () => { const at = RULES_FOR.indexOf(':'); return at < 0 ? {kind: '', id: ''} : {kind: RULES_FOR.slice(0, at), id: RULES_FOR.slice(at + 1)}; };
const RULES_BUSY = new Set();
// Suggest quieter rules (backend/rule_suggestions.py): open ones and the ones applied lately, only those the viewer may act on.
let RULE_SUGGESTIONS = [];
const RULE_SCOPES = [['builtin', 'Built-in'], ['team', 'Team'], ['type', 'Type'], ['department', 'Department'], ['bot', 'Bot'], ['task', 'Task']];
const RULE_BUILTIN_GROUPS = {always: 'Always wakes', awaited: 'Awaited', filter: 'Waits', note: 'Notes', batch: 'Batched', wake: 'Wakes',
  pr_checks: 'Pull requests', system: 'System'};
const RULE_OUTCOMES = {wake: 'wake the bot', note: 'leave a note for its next run', batch: 'batch it', ignore: 'drop it', ask: 'ask a person', action: 'do an action'};
const RULE_ACTIONS = {close: 'close the task', move_step: 'move the step', set_wait: 'set a wait', relate: 'relate tasks', note: 'add a note'};
const RULE_ACTORS = {human: 'a person', bot: 'a bot', keeper: 'Tico'};
const RULE_ROLES = {owner: 'owns the task', requester: 'requested the task', qa: "is the task's QA", release_manager: 'is the Release Manager',
  other: 'neither owns nor requested it'};
const RULE_ROLE_LABELS = {owner: 'Owner', requester: 'Requester', qa: 'QA', release_manager: 'Release Manager', other: 'Other'};
// The always-wake events (backend/rules.py FLOOR_EVENTS): the server refuses a row that makes one wait.
const RULE_FLOOR = new Set(['person_message', 'ask', 'answer', 'approval', 'needs_action', 'reply', 'verdict']);

// An event's words come from its built-in row, so a new event the backend adds reads right with no UI change.
function ruleEventWords(event) {
  if (event === '*') return 'anything else';
  const rank = {always: 0, wake: 1, batch: 2, awaited: 3, note: 4, pr_checks: 5};
  const found = (RULES?.rules || []).filter(r => r.built_in && r.event === event && r.group in rank)
    .sort((a, b) => rank[a.group] - rank[b.group])[0];
  return found?.words || event.replace(/_/g, ' ');
}
const ruleList = v => (Array.isArray(v) ? v : [v]).filter(Boolean);
function ruleWhenWords(when) {
  const out = [];
  if (when.actor) out.push('it comes from ' + ruleList(when.actor).map(a => RULE_ACTORS[a] || a).join(' or '));
  if (when.role) out.push('the bot ' + ruleList(when.role).map(r => RULE_ROLES[r] || r).join(' or '));
  if (when.step) out.push('the task is at ' + ruleList(when.step).join(' or '));
  if (when.state) out.push('the task is ' + ruleList(when.state).join(' or '));
  if (when.tag) out.push('the task is tagged ' + ruleList(when.tag).join(' and '));
  return out;
}
function ruleThenWords(r) {
  if (r.then !== 'action') return RULE_OUTCOMES[r.then] || r.then;
  const p = r.params || {}, what = RULE_ACTIONS[p.action] || 'do an action';
  return p.action === 'move_step' && p.step ? `move it to ${p.step}` : p.action === 'set_wait' && p.waiting_on ? `set it waiting on ${p.waiting_on}` : what;
}
function ruleScopeWords(r) {
  const id = r.scope_id;
  if (r.scope_kind === 'team') return 'Team';
  if (r.scope_kind === 'bot') return 'Bot · ' + empName(id);
  if (r.scope_kind === 'type') return 'Type · ' + (TASK_TYPES.find(t => t.id === id)?.name || id);
  if (r.scope_kind === 'department') return 'Department · ' + ((SYSTEM_ROUTINES?.task_rule_departments || []).find(d => d.id === id)?.name || id);
  if (r.scope_kind === 'task') return 'Task · ' + (RULES_FOR === 'task:' + id && RULES_FOR_TITLE ? RULES_FOR_TITLE : id.slice(0, 8));
  return '';
}
function ruleUsageHTML(r) {
  const rate = r.no_op_rate_7d == null ? '' : ` · <span data-rule-noop>${Math.round(r.no_op_rate_7d * 100)}% no-op</span>`;
  return `<span class="tnum${r.runs_7d ? '' : ' muted'}" title="Last 7 days" data-rule-usage>${r.runs_7d} run${r.runs_7d === 1 ? '' : 's'}${rate}</span>`;
}
function ruleRowHTML(r) {
  const busy = RULES_BUSY.has(r.id);
  const and = r.built_in ? [] : ruleWhenWords(r.when || {});
  const when = r.built_in ? r.words : ruleEventWords(r.event);
  const text = `<b>WHEN</b> ${esc(when)}${and.map(w => ` <b class="sysr-and">AND</b> ${esc(w)}`).join('')} <b>THEN</b> ${esc(ruleThenWords(r))}`;
  const ctl = r.can_change ? `<label class="sysr-switch">${peopleSwitch('data-rule-on', r.enabled, busy, 'Rule on')}<span>${r.enabled ? 'On' : 'Off'}</span></label>
      <button type="button" class="ghost" data-rule-edit${busy ? ' disabled' : ''}>Edit</button>`
    : r.built_in ? '' : `<span class="sysr-state">${r.enabled ? 'On' : 'Off'}</span>`;
  return `<div class="wr-row${r.enabled === false ? ' wr-off' : ''}" data-rule="${esc(r.id)}">
    <div class="wr-text" data-rule-text>${text}${r.note ? `<span class="muted wr-note">${esc(r.note)}</span>` : ''}</div>
    ${r.built_in ? '' : `<span class="pill wr-scope" data-rule-scope>${esc(ruleScopeWords(r))}</span>`}
    <span class="wr-usage">${ruleUsageHTML(r)}</span>
    <div class="wr-ctl">${ctl}<p class="settings-cell-note" role="status" data-rule-status></p></div>
  </div>`;
}
function ruleMatches(r) {
  if (RULES_FOR && r.scope_kind === 'builtin') return false;   // read for their words only
  if (RULES_SCOPE && r.scope_kind !== RULES_SCOPE) return false;
  if (!RULES_Q) return true;
  return searchIncludes([r.words, r.event, ruleEventWords(r.event), ruleThenWords(r), ruleScopeWords(r), r.note, ...ruleWhenWords(r.when || {})], RULES_Q);
}
function ruleGroupsHTML() {
  const rows = (RULES?.rules || []).filter(ruleMatches);
  const none = {task: 'No rules for this task. The team, type, bot and built-in rules decide.', bot: 'No rules for this bot.'}[rulesFor().kind];
  if (!rows.length) return `<div class="empty" data-rules-none>${none || 'No rule matches.'}</div>`;
  return RULE_SCOPES.map(([kind, label]) => {
    const mine = rows.filter(r => r.scope_kind === kind);
    if (!mine.length) return '';
    const body = kind === 'builtin'
      ? Object.entries(RULE_BUILTIN_GROUPS).map(([g, name]) => {
        const sub = mine.filter(r => r.group === g);
        return sub.length ? `<h4 class="wr-sub">${esc(name)}</h4>${sub.map(ruleRowHTML).join('')}` : '';
      }).join('')
      : kind === 'bot' ? ruleBotRowsHTML(mine) : mine.map(ruleRowHTML).join('');
    // The built-ins are long and fixed, so they start folded unless a search is narrowing them.
    const open = kind !== 'builtin' || RULES_Q || RULES_SCOPE === 'builtin';
    return `<details class="wr-group" data-rule-group="${kind}"${open ? ' open' : ''}><summary><span>${esc(label)}</span><span class="cnt">${mine.length}</span></summary>${body}</details>`;
  }).join('');
}
// A bot's profile rows (backend/rules.py profile_listing) come only with that bot's own listing: read-only, under the
// profile's name, above the rows the bot has of its own.
function ruleBotRowsHTML(rows) {
  const profile = rows.filter(r => r.group === 'profile'), own = rows.filter(r => r.group !== 'profile');
  if (!profile.length) return own.map(ruleRowHTML).join('');
  const name = profile[0].profile || '';
  return `<h4 class="wr-sub" data-rule-profile="${esc(name)}">Profile · ${esc(name[0]?.toUpperCase() + name.slice(1))}</h4>${profile.map(ruleRowHTML).join('')}
    ${own.length ? `<h4 class="wr-sub">Its own</h4>${own.map(ruleRowHTML).join('')}` : ''}`;
}
// A suggestion reads as its rule's row would, then what it would change. On an applied one Dismiss puts the rule back,
// so it says Undo there.
function ruleSuggestionShown(s) {
  const f = rulesFor();
  if (!['open', 'applied'].includes(s.status)) return false;
  return !f.kind || (s.scope_kind === f.kind && s.scope_id === f.id);
}
function ruleSuggestionHTML(s) {
  const busy = RULES_BUSY.has('suggestion:' + s.id), r = (RULES?.rules || []).find(x => x.id === s.rule_id);
  const when = r?.built_in ? r.words : ruleEventWords(s.event);
  const and = r && !r.built_in ? ruleWhenWords(r.when || {}) : [];
  const text = `<b>WHEN</b> ${esc(when)}${and.map(w => ` <b class="sysr-and">AND</b> ${esc(w)}`).join('')}${r ? ` <b>THEN</b> ${esc(ruleThenWords(r))}` : ''}`;
  const rate = s.no_op_rate_7d == null ? '' : ` · <span data-suggest-noop>${Math.round(s.no_op_rate_7d * 100)}% no-op</span>`;
  const off = busy || !s.can_change ? ' disabled' : '';
  const ctl = s.status === 'applied' ? `<span class="sysr-state">Applied</span><button type="button" class="ghost" data-suggest-dismiss${off}>Undo</button>`
    : `<button type="button" class="primary" data-suggest-apply${off}>Apply</button><button type="button" class="ghost" data-suggest-dismiss${off}>Dismiss</button>`;
  return `<div class="wr-row" data-rule-suggestion="${esc(s.id)}" data-suggest-status="${esc(s.status)}">
    <div class="wr-text">${text}<span class="wr-note" data-suggest-words>${esc(s.suggestion)}</span></div>
    <span class="pill wr-scope">${esc(ruleScopeWords(s))}</span>
    <span class="wr-usage tnum" title="Last 7 days" data-suggest-usage>${s.runs_7d} run${s.runs_7d === 1 ? '' : 's'}${rate}</span>
    <div class="wr-ctl">${ctl}<p class="settings-cell-note" role="status" data-suggest-status-note></p></div>
  </div>`;
}
function ruleSuggestionsHTML() {
  const shown = RULE_SUGGESTIONS.filter(ruleSuggestionShown);
  return shown.length ? `<h4 class="wr-sub">Suggestions</h4>${shown.map(ruleSuggestionHTML).join('')}` : '';
}
function ruleSuggestionsPaint(el) {
  const box = el.querySelector('[data-rules-suggestions]'); if (!box) return;
  box.innerHTML = ruleSuggestionsHTML();
  box.hidden = !box.innerHTML;
}
async function ruleSuggestionsLoad() {
  // An older server has no suggestions route; the block just stays hidden.
  try { RULE_SUGGESTIONS = (await get('/v2/rules/suggestions?status=all&limit=50')).suggestions || []; } catch { RULE_SUGGESTIONS = []; }
}
async function ruleSuggestionAct(id, verb) {
  const key = 'suggestion:' + id, el = $('#set-rules');
  RULES_BUSY.add(key); if (el) ruleSuggestionsPaint(el);
  let failed = '';
  try {
    const out = await post(`/v2/rules/suggestions/${encodeURIComponent(id)}/${verb}`, {});
    RULE_SUGGESTIONS = RULE_SUGGESTIONS.map(s => s.id === id ? out.suggestion : s);
  } catch (e) { failed = e.message || 'Try again'; if (e.status === 409) await ruleSuggestionsLoad(); }
  finally { RULES_BUSY.delete(key); }
  // The rule itself changed (or a team row came or went), so the list below is read again.
  if (!failed) await renderSettingsRules(true);
  else if (el) ruleSuggestionsPaint(el);
  const note = $(`[data-rule-suggestion="${CSS.escape(id)}"] [data-suggest-status-note]`);
  if (note && failed) { note.textContent = failed; note.classList.add('err'); }
}
function rulesMayAdd() {
  const f = rulesFor();
  if (f.kind === 'task') return true;
  if (f.kind === 'bot') return settingsCanManageBot((S.emps || []).find(e => e.name === f.id));
  return settingsIsAdmin() || canMove() || (S.emps || []).some(settingsCanManageBot);
}
function rulesHTML() {
  const opt = (v, label) => `<option value="${esc(v)}"${v === RULES_SCOPE ? ' selected' : ''}>${esc(label)}</option>`;
  const f = rulesFor();
  const name = f.kind === 'task' ? RULES_FOR_TITLE || f.id.slice(0, 8) : empName(f.id);
  const open = f.kind === 'task' ? `<a href="#/task/${encodeURIComponent(f.id)}">Open task</a>` : `<a href="#/bot/${encodeURIComponent(f.id)}/settings">Open bot</a>`;
  const task = f.kind ? `<div class="wr-filter" data-rules-filter="${esc(f.kind)}"><span>Rules for <strong>${esc(name)}</strong></span>
      ${open}<a href="#/settings/rules" data-rules-all>All rules</a></div>` : '';
  return `${task}<div class="settings-bots-filters">${rulesMayAdd() ? '<button type="button" class="primary" data-rule-new>Add rule</button>' : ''}
      <input class="settings-filter-search" type="search" autocomplete="off" data-rules-search value="${esc(RULES_Q)}" placeholder="Search rules" aria-label="Search rules">
      ${RULES_FOR ? '' : `<select class="settings-inline-select" data-rules-scope aria-label="Scope">${opt('', 'All scopes')}${RULE_SCOPES.map(([k, v]) => opt(k, v)).join('')}</select>`}</div>
    <div class="wr-suggest" data-rules-suggestions${RULE_SUGGESTIONS.some(ruleSuggestionShown) ? '' : ' hidden'}>${ruleSuggestionsHTML()}</div>
    <div class="wr-list" data-rules-list>${ruleGroupsHTML()}</div>`;
}
function rulesPaint() {
  const el = $('#set-rules'); if (!el || !RULES) return;
  const list = el.querySelector('[data-rules-list]');
  if (list && el.dataset.rulesFor === RULES_FOR) { list.innerHTML = ruleGroupsHTML(); ruleSuggestionsPaint(el); }
  else { el.innerHTML = rulesHTML(); el.dataset.rulesFor = RULES_FOR; }
  rulesBind(el);
}
// The listing is paged (`more`); the built-ins come on the first page only, so later pages add table rows.
async function rulesLoad() {
  const f = rulesFor(), scope = f.kind ? `&scope_kind=${f.kind}&scope_id=${encodeURIComponent(f.id)}` : '';
  let offset = 0, out = null;
  for (let page = 0; page < 5; page++) {
    const data = await get(`/v2/rules?limit=200&offset=${offset}${scope}`);
    data.rules ||= [];
    if (!out) out = data; else out.rules.push(...data.rules);
    if (!data.more) break;
    offset += 200;
  }
  // A task's or bot's own listing has no built-ins, and the event words come from them; read them once.
  if (f.kind && !out.rules.some(r => r.scope_kind === 'builtin')) {
    const known = RULES?.rules?.filter(r => r.scope_kind === 'builtin');
    const builtins = known?.length ? known : (await get('/v2/rules?scope_kind=builtin')).rules || [];
    out.rules = [...builtins, ...out.rules];
  }
  return out;
}
async function renderSettingsRules(force) {
  const el = $('#set-rules'); if (!el) return;
  const task = RULES_FOR, f = rulesFor();
  if (RULES && el.dataset.rulesFor === task && !force && Date.now() - RULES_AT < 60000) { rulesPaint(); return; }
  RULES_AT = Date.now();
  if (f.kind === 'task' && !RULES_FOR_TITLE) get('/v2/tasks/' + encodeURIComponent(f.id)).then(t => {
    RULES_FOR_TITLE = (t.task || t).title || '';
    if (RULES_FOR === task && RULES) { delete el.dataset.rulesFor; rulesPaint(); }   // the heading names it too
  }).catch(() => {});
  // The type names are read alongside, so a type row never shows a bare id; the department names come with the
  // routines above (renderSystemRoutines repaints this list when they land).
  const types = taskTypesLoad({cached: true}).catch(() => {});
  const suggestions = ruleSuggestionsLoad();
  try { const data = await rulesLoad(); await types; await suggestions; if (RULES_FOR !== task) return; RULES = data; }
  catch (e) { if (!RULES) el.innerHTML = `<div class="empty">Couldn't load the rules: ${esc(e.message || 'try again')}</div>`; return; }
  if (RULES_BUSY.size || formBusy(el.querySelector('[data-rules-list]'))) return;
  rulesPaint();
  settingsReveal('#set-rules-card');
}
function ruleReplace(rule) {
  if (!RULES || !rule) return;
  const at = RULES.rules.findIndex(r => r.id === rule.id);
  // A write answers the row without its 7-day figures; keep the ones read last.
  const kept = at >= 0 ? RULES.rules[at] : {runs_7d: 0, no_op_rate_7d: null, can_change: true};
  const next = {...kept, ...rule, runs_7d: kept.runs_7d, no_op_7d: kept.no_op_7d, no_op_rate_7d: kept.no_op_rate_7d, can_change: kept.can_change ?? true};
  if (at >= 0) RULES.rules[at] = next; else RULES.rules.push(next);
}
async function ruleToggle(id, on) {
  const r = RULES.rules.find(x => x.id === id); if (!r) return;
  RULES_BUSY.add(id); rulesPaint();
  let note = '', failed = false;
  try { ruleReplace((await post('/v2/rules/' + encodeURIComponent(id), {expected_revision: r.revision, on})).rule); note = 'Saved'; }
  catch (e) { note = e.message || 'Try again'; failed = true; if (e.status === 409) void renderSettingsRules(true); }
  finally { RULES_BUSY.delete(id); }
  rulesPaint();
  const box = $(`[data-rule="${CSS.escape(id)}"] [data-rule-status]`);
  if (box) { box.textContent = note; box.classList.toggle('err', failed); }
}
function rulesBind(el) {
  const add = el.querySelector('[data-rule-new]');
  if (add) add.onclick = () => ruleEditor(null);
  const search = el.querySelector('[data-rules-search]');
  if (search) search.oninput = () => { RULES_Q = search.value; el.querySelector('[data-rules-list]').innerHTML = ruleGroupsHTML(); };
  const scope = el.querySelector('[data-rules-scope]');
  if (scope) scope.onchange = () => { RULES_SCOPE = scope.value; el.querySelector('[data-rules-list]').innerHTML = ruleGroupsHTML(); };
  const suggest = el.querySelector('[data-rules-suggestions]');
  if (suggest) suggest.onclick = ev => {
    const button = ev.target.closest('[data-suggest-apply], [data-suggest-dismiss]'); if (!button || button.disabled) return;
    void ruleSuggestionAct(button.closest('[data-rule-suggestion]').dataset.ruleSuggestion, button.matches('[data-suggest-apply]') ? 'apply' : 'dismiss');
  };
  const list = el.querySelector('[data-rules-list]');
  list.onchange = ev => {
    const row = ev.target.closest('[data-rule]');
    if (row && ev.target.matches('[data-rule-on]')) void ruleToggle(row.dataset.rule, ev.target.checked);
  };
  list.onclick = ev => {
    const edit = ev.target.closest('[data-rule-edit]'); if (!edit || edit.disabled) return;
    ruleEditor(RULES.rules.find(r => r.id === edit.closest('[data-rule]').dataset.rule));
  };
}

// The scopes a viewer may add a row in, as far as the page knows; the server has the last word and its refusal
// shows in the form.
function ruleScopeChoices() {
  const out = [];
  if (settingsIsAdmin()) out.push(['team', 'Team']);
  if (canMove()) out.push(['type', 'Task type']);
  if ((SYSTEM_ROUTINES?.task_rule_departments || []).length) out.push(['department', 'Department']);
  if ((S.emps || []).some(settingsCanManageBot)) out.push(['bot', 'Bot']);
  out.push(['task', 'Task']);
  return out;
}
function ruleTargetHTML(kind, value) {
  const opt = (v, label) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(label)}</option>`;
  const pick = options => `<label>Which<select name="scope_id" required><option value="">Choose</option>${options}</select></label>`;
  if (kind === 'type') return pick(TASK_TYPES.map(t => opt(t.id, t.name)).join(''));
  if (kind === 'department') return pick((SYSTEM_ROUTINES?.task_rule_departments || []).map(d => opt(d.id, d.name)).join(''));
  if (kind === 'bot') return pick((S.emps || []).filter(settingsCanManageBot).map(e => opt(e.name, empName(e.name))).sort().join(''));
  if (kind === 'task') return `<label>Task ID<input name="scope_id" required maxlength="120" value="${esc(value || (rulesFor().kind === 'task' ? rulesFor().id : ''))}"></label>`;
  return '';
}
// A type row's step is picked from that type's steps (matched by name, as the server matches); any other scope types it.
function ruleStepsFor(kind, id) {
  const steps = kind === 'type' && id ? TASK_TYPES.find(t => t.id === id)?.steps : null;
  return steps?.length ? steps.map(s => s.name) : null;
}
function ruleStepHTML(field, label, value, steps, extra = '') {
  if (!steps) return `<label>${label}<input name="${field}" maxlength="80" value="${esc(value)}"${extra}></label>`;
  const list = value && !steps.includes(value) ? [value, ...steps] : steps;
  const any = field === 'step' ? `<option value="">Any</option>` : `<option value="">Choose</option>`;
  return `<label>${label}<select name="${field}"${extra}>${any}${list.map(n => `<option value="${esc(n)}"${n === value ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>`;
}
// Only an action row reads params; any other outcome clears what an earlier action left. A field this form does not
// show (a wait's blocked_by, a relation's kind) is kept while the action stays the same.
function ruleParams(v, before) {
  const action = String(v.get('action')), out = before.action === action ? {...before} : {};
  out.action = action;
  for (const [field, key] of [['param_step', 'step'], ['param_waiting_on', 'waiting_on'], ['param_task', 'task'], ['param_note', 'note']]) {
    const text = String(v.get(field) || '').trim();
    if (text) out[key] = text; else delete out[key];
  }
  if (action !== 'move_step') delete out.step;
  if (action !== 'set_wait') delete out.waiting_on;
  if (action !== 'relate') delete out.task;
  return out;
}
function ruleEditor(rule) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal task-rule-dialog';
  const when = rule?.when || {}, first = v => ruleList(v)[0] || '';
  const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(label)}</option>`;
  const scopes = ruleScopeChoices();
  const f = rulesFor();
  const kind = rule?.scope_kind || f.kind || scopes[0]?.[0] || 'task';
  const params = rule?.params || {};
  // Until the form exists, the steps come from the rule's own scope (or the filtered one a new rule starts in).
  let steps = () => ruleStepsFor(rule?.scope_kind || kind, rule?.scope_id ?? (kind === f.kind ? f.id : ''));
  const events = ['*', ...(RULES?.events || [])].map(e => opt(e, (RULE_FLOOR.has(e) ? 'Always: ' : '') + ruleEventWords(e), rule?.event || 'comment')).join('');
  dialog.innerHTML = `<form class="task-rule-form" data-rule-form><header><h2>${rule ? 'Edit rule' : 'Add rule'}</h2><button type="button" class="ghost" data-close>Cancel</button></header>
    ${rule ? `<p class="muted">${esc(ruleScopeWords(rule))}</p>` : `<label>Scope<select name="scope_kind" data-rule-kind>${scopes.map(([k, v]) => opt(k, v, kind)).join('')}</select></label>
    <div data-rule-target>${ruleTargetHTML(kind, kind === f.kind ? f.id : '')}</div>`}
    <label>WHEN<select name="event">${events}</select></label>
    <div class="wr-when">
      <label>From<select name="actor">${opt('', 'Anyone', first(when.actor))}${Object.entries(RULE_ACTORS).map(([k, v]) => opt(k, v, first(when.actor))).join('')}</select></label>
      <label>Bot's role<select name="role">${opt('', 'Any', first(when.role))}${Object.entries(RULE_ROLE_LABELS).map(([k, v]) => opt(k, v, first(when.role))).join('')}</select></label>
      <label>Status<select name="state">${opt('', 'Any', first(when.state))}${PIPELINE_STATUSES.map(s => opt(s, s[0].toUpperCase() + s.slice(1), first(when.state))).join('')}</select></label>
      <span class="wr-field" data-rule-step>${ruleStepHTML('step', 'Step', first(when.step), steps())}</span>
      <label>Tag<input name="tag" maxlength="80" value="${esc(first(when.tag))}"></label>
    </div>
    <label>THEN<select name="then" data-rule-then>${Object.entries(RULE_OUTCOMES).map(([k, v]) => opt(k, v[0].toUpperCase() + v.slice(1), rule?.then || 'note')).join('')}</select></label>
    <div class="wr-when" data-rule-action${(rule?.then || 'note') === 'action' ? '' : ' hidden'}>
      <label>Action<select name="action">${Object.entries(RULE_ACTIONS).map(([k, v]) => opt(k, v[0].toUpperCase() + v.slice(1), params.action || 'close')).join('')}</select></label>
      <span class="wr-field" data-rule-param="move_step">${ruleStepHTML('param_step', 'To step', params.step || '', steps(), ' required')}</span>
      <label data-rule-param="set_wait">Waits on<input name="param_waiting_on" maxlength="120" ${params.blocked_by ? '' : ' required'} value="${esc(params.waiting_on || '')}"></label>
      <label data-rule-param="relate">Task ID<input name="param_task" maxlength="120" required value="${esc(params.task || '')}"></label>
      <label>Task note<input name="param_note" maxlength="1000" value="${esc(params.note || '')}"></label>
    </div>
    <label>Note<input name="note" maxlength="500" value="${esc(rule?.note || '')}"></label>
    <label><input type="checkbox" name="on"${rule?.enabled === false ? '' : ' checked'}> On</label>
    <p class="err" role="status" data-rule-error></p>
    <footer>${rule ? '<button type="button" class="ghost danger" data-rule-delete>Delete</button>' : ''}<button type="submit" class="primary">Save rule</button></footer></form>`;
  const form = dialog.querySelector('form'), error = dialog.querySelector('[data-rule-error]');
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  // Only the chosen action's own fields show; a hidden one is disabled too, so its `required` does not block Save.
  const shown = () => {
    const action = form.elements.then.value === 'action';
    dialog.querySelector('[data-rule-action]').hidden = !action;
    dialog.querySelectorAll('[data-rule-param]').forEach(el => {
      const on = action && el.dataset.ruleParam === form.elements.action.value;
      el.hidden = !on; el.querySelectorAll('input, select').forEach(input => { input.disabled = !on; });
    });
  };
  // A new rule's scope or type changes which steps there are to pick.
  const restep = () => {
    if (rule) return;
    steps = () => ruleStepsFor(form.elements.scope_kind?.value, String(form.elements.scope_id?.value || ''));
    const keep = name => String(form.elements[name]?.value || '');
    dialog.querySelector('[data-rule-step]').innerHTML = ruleStepHTML('step', 'Step', keep('step'), steps());
    dialog.querySelector('[data-rule-param="move_step"]').innerHTML = ruleStepHTML('param_step', 'To step', keep('param_step'), steps(), ' required');
    shown();
  };
  form.elements.then.onchange = shown; form.elements.action.onchange = shown; shown();
  if (form.elements.scope_kind) {
    form.elements.scope_kind.onchange = () => { dialog.querySelector('[data-rule-target]').innerHTML = ruleTargetHTML(form.elements.scope_kind.value, ''); restep(); };
    dialog.querySelector('[data-rule-target]').onchange = restep;
    restep();
  }
  const done = () => { dialog.close(); rulesPaint(); };
  form.onsubmit = async ev => {
    ev.preventDefault(); const button = form.querySelector('[type="submit"]'); button.disabled = true; error.textContent = '';
    const v = new FormData(form), cond = {};
    for (const key of ['actor', 'role', 'state', 'step', 'tag']) if (String(v.get(key) || '').trim()) cond[key] = String(v.get(key)).trim();
    const then = v.get('then');
    const body = {event: v.get('event'), then, when: cond, params: then === 'action' ? ruleParams(v, params) : {}, note: v.get('note') || '', on: v.has('on')};
    try {
      if (rule) ruleReplace((await post('/v2/rules/' + encodeURIComponent(rule.id), {...body, expected_revision: rule.revision})).rule);
      else {
        const scope_kind = v.get('scope_kind') || 'task';
        ruleReplace({...(await post('/v2/rules', {...body, scope_kind, scope_id: scope_kind === 'team' ? '' : String(v.get('scope_id') || '').trim()})).rule, can_change: true});
      }
      done();
    } catch (e) { error.textContent = e.message || 'Could not save the rule'; }
    finally { button.disabled = false; }
  };
  const del = dialog.querySelector('[data-rule-delete]');
  if (del) del.onclick = async () => {
    if (!confirm('Delete this rule?')) return;
    del.disabled = true; error.textContent = '';
    try {
      await post('/v2/rules/' + encodeURIComponent(rule.id) + '/delete', {expected_revision: rule.revision});
      RULES.rules = RULES.rules.filter(r => r.id !== rule.id); done();
    } catch (e) { error.textContent = e.message || 'Could not delete the rule'; del.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); form.elements.event.focus();
}
