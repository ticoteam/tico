/* Task settings is a projection of the Rules and system routine listings, never an editor. */
'use strict';
let TASK_SETTINGS = null, TASK_SETTINGS_TYPE = '', TASK_SETTINGS_LOADING = null;
let TASK_SETTINGS_VERSION = 0;
let RULES_OPEN = null;
const TASK_SETTING_SECTIONS = [
  ['created', "When it's created", 'Tico does these the moment a {type} is filed.'],
  ['step', 'When it enters a step', 'Tico does these when a {type} moves into a step.'],
  ['finished', "When it's finished", 'Tico does these when a {type} is done, declined or closed.'],
  ['open', "While it's open", 'Tico checks open {type}s on a schedule. Changes and messages also act as they happen.'],
  ['after', "After it's finished", 'Tico tidies finished {type}s on a schedule.']
];
function taskSettingsLink(values) {
  return '#/settings/rules?' + new URLSearchParams(Object.entries(values).filter(([,v]) => v != null && v !== '')).toString();
}
const taskSettingsMayAdd = () => TASK_SETTINGS_TYPE ? canMove() : settingsIsAdmin();
function taskSettingsSource(r) {
  if (r.kind === 'task_rule') return 'Department: ' + r.department.name;
  if (r.scope_kind === 'bot') return 'Bot: ' + empName(r.scope_id);
  if (r.scope_kind === 'department') return 'Department: ' + (TASK_SETTINGS.jobs.task_rule_departments || []).find(d => d.id === r.scope_id)?.name;
  if (r.scope_kind === 'type') return TASK_SETTINGS_TYPE ? 'This type' : 'Type: ' + (TASK_TYPES.find(t => t.id === r.scope_id)?.name || 'another type');
  if (r.scope_kind === 'team') return 'Team';
  return r.built_in || r.key ? 'Built-in' : 'This task';
}
function taskSettingsTrigger(r) {
  // Older servers still offer their normal rule list; unknown task events remain visible under open tasks.
  if ('task_trigger' in r) return r.task_trigger;
  if (['routine', 'schedule', 'next_run', 'background_done', '*', 'digest', 'feed', 'backlog', 'release_now'].includes(r.event) || r.event?.includes('.')) return null;
  if (r.event === 'step_entered') return 'step';
  return ['task_done', 'task_declined', 'batch_completion'].includes(r.event) ? 'finished' : 'open';
}
// A rule row borrows its event's words from the general built-in, never from a filter or a narrower case of it.
const TASK_SETTINGS_EVENT_WORDS = {pr_checks_done: 'every check on a pull request head finished'};
function taskSettingsEventWords(event) {
  if (TASK_SETTINGS_EVENT_WORDS[event]) return TASK_SETTINGS_EVENT_WORDS[event];
  const rank = {wake: 0, note: 1, always: 2, batch: 3, awaited: 4, pr_checks: 5};
  const found = (TASK_SETTINGS?.rules.rules || []).filter(row => row.scope_kind === 'builtin' && row.event === event && row.group in rank)
    .sort((a, b) => rank[a.group] - rank[b.group])[0];
  return found?.words || event.replaceAll('_', ' ');
}
function taskSettingsSentence(r, type) {
  if (r.task_action) return r.task_action;
  if (r.scope_kind === 'type') type = TASK_TYPES.find(t => t.id === r.scope_id);
  const bot = r.scope_kind === 'bot' ? empName(r.scope_id) : '';
  const subject = bot ? `a task of ${bot}'s` : type ? `a ${type.name} task` : 'a task';
  const role = ruleList(r.when?.role);
  let action = ruleThenWords(r);
  if (r.then === 'wake' && !r.params?.bot && role.length) action = 'wake ' + role.map(k => ({owner: 'its owner', requester: 'its requester', qa: 'QA', release_manager: 'the Release Manager'}[k] || 'the bot')).join(' or ');
  if (r.then === 'note' && role.includes('release_manager')) action = 'tell the Release Manager';
  if (bot && ['wake', 'note', 'batch'].includes(r.then)) action = r.then === 'wake' ? `wake ${bot}` : `leave ${bot} a note for its next run`;
  const moment = {assignment: `${subject} is assigned, reassigned or handed back`, due: `${subject} is due`, snooze: `${subject}'s snooze ends`,
    step_entered: `${subject} enters ${ruleList(r.when?.step).join(' or ') || 'a step'}`} [r.event];
  const conditions = r.built_in ? [] : ruleWhenWords({...r.when, ...(moment ? {step: undefined} : {})});
  const words = r.words || taskSettingsEventWords(r.event);
  return `When ${moment || words}${conditions.length ? ', if ' + conditions.join(' and ') : ''}, ${action}.`;
}
function taskSettingsRow(r, type) {
  const isJob = !!r.key, source = taskSettingsSource(r);
  const change = !!r.can_change, on = isJob ? r.on : r.enabled;
  const quiet = r.then !== 'wake' && !(r.then === 'action' && r.params?.action === 'wake_bot');
  const link = taskSettingsLink(isJob ? {job: r.key} : {rule: r.id, bot: r.scope_kind === 'bot' ? r.scope_id : '', type: r.scope_kind === 'type' ? r.scope_id : ''});
  const state = (r.built_in && !change) || (isJob && !r.switch) ? 'Always on' : on === false ? 'Off' : 'On';
  const profile = r.profile || (r.created_by === 'profile' ? r.note?.match(/^From the ([^:]+) profile:/i)?.[1] : '');
  const usage = isJob || r.usage_available === false ? 'Run count unavailable' : `${r.runs_7d || 0} runs, ${r.no_op_7d ?? Math.round((r.no_op_rate_7d || 0) * (r.runs_7d || 0))} idle`;
  return `<article class="task-setting-row${on === false ? ' wr-off' : ''}" data-task-setting="${esc(r.id || r.key)}">
    <div><p class="task-setting-action"><b>Action</b> ${esc(taskSettingsSentence(r, type))}</p>
    ${isJob ? `<p class="settings-cell-note">Trigger: ${esc(r.schedule)}${r.note ? ' · ' + esc(r.note) : ''}</p>` : ''}
    ${profile ? `<p class="settings-cell-note">From its ${esc(profile)} profile</p>` : ''}</div>
    <div class="task-setting-meta"><span class="pill">${esc(source)}</span><span class="pill">${esc(r.task_effect || (quiet ? 'Quiet' : 'Wakes'))}</span>
      <span title="Last 7 days">${esc(usage)}</span><span>${state}</span>
      ${change ? `<a class="ghost" aria-label="${on === false ? 'Switch on' : 'Switch off'} in the editor" href="${esc(link)}">${on === false ? 'Switch on' : 'Switch off'}</a><a class="ghost" href="${esc(link)}">Edit</a>` : ''}</div></article>`;
}
function taskSettingsAdd(section, step = '') {
  if (!taskSettingsMayAdd()) return '';
  return `<a class="ghost" href="${esc(taskSettingsLink({add: '1', type: TASK_SETTINGS_TYPE, trigger: section === 'step' ? 'step_entered' : {created: 'task.created', finished: 'task.done', open: 'stall_check', after: 'task.done'}[section], step}))}">Add action</a>`;
}
function taskSettingsPaint() {
  const el = $('#set-task-settings'); if (!el || !TASK_SETTINGS) return;
  const type = TASK_TYPES.find(t => t.id === TASK_SETTINGS_TYPE), data = TASK_SETTINGS;
  const rows = [...(data.rules.task_builtins || []), ...data.rules.rules, ...data.jobs.jobs.filter(j => j.task_trigger)];
  const relevant = rows.filter(r => r.scope_kind !== 'task' && (!TASK_SETTINGS_TYPE || !r.task_types || r.task_types.includes(TASK_SETTINGS_TYPE)) && (r.scope_kind !== 'type' || !TASK_SETTINGS_TYPE || r.scope_id === TASK_SETTINGS_TYPE));
  const rank = {builtin: 0, team: 1, type: 2, department: 3, bot: 4};
  relevant.sort((a,b) => (rank[a.scope_kind] ?? 0) - (rank[b.scope_kind] ?? 0));
  const sectionHTML = ([key, title, intro]) => {
    if (key === 'step' && !type) return '';
    const mine = relevant.filter(r => taskSettingsTrigger(r) === key);
    const render = list => list.map(r => taskSettingsRow(r, type)).join('') || '<p class="muted">Nothing happens</p>';
    let body = render(mine);
    if (key === 'step') body = mine.filter(r => r.task_action && !r.when?.step).map(r => taskSettingsRow(r, type)).join('') + (type.steps || []).map(step => `<div class="task-setting-step" data-task-step="${esc(step.name)}"><h4>${esc(step.name)}</h4>${render(mine.filter(r => (!r.task_action || r.when?.step) && (!r.when?.step || ruleList(r.when.step).includes(step.name))))}${taskSettingsAdd(key, step.name)}</div>`).join('');
    // Built-in effects with no event run on all three, so they show once above the blocks.
    if (key === 'finished') body = mine.filter(r => !r.event).map(r => taskSettingsRow(r, type)).join('') + ['Done', 'Declined', 'Closed'].map(name => `<div class="task-setting-step"><h4>${name}</h4>${render(mine.filter(r => (name === 'Declined' ? ['task_declined'] : ['task_done', 'batch_completion']).includes(r.event)))}</div>`).join('');
    return `<section class="task-setting-section" data-task-trigger="${key}"><h3><span class="muted">Trigger</span> ${esc(title)}</h3><p>${esc(intro.replace('{type}', type ? type.name + ' task' : 'task'))}</p>${body}${key === 'step' ? '' : taskSettingsAdd(key)}</section>`;
  };
  const footer = new Map();
  for (const r of data.rules.rules.filter(r => !r.built_in && !taskSettingsTrigger(r))) { const bot = r.scope_kind === 'bot' ? r.scope_id : ''; footer.set(bot, (footer.get(bot) || 0) + 1); }
  const mayChange = taskSettingsMayAdd() || relevant.some(r => r.can_change);
  el.innerHTML = `<label class="task-setting-picker">Type <select aria-label="Task settings type"><option value="">All tasks</option>${TASK_TYPES.map(t => `<option value="${esc(t.id)}"${t.id === TASK_SETTINGS_TYPE ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label>
    ${mayChange ? '' : `<p class="settings-cell-note">Ask ${esc(settingsOwnerName() || 'the team owner')} to change these.</p>`}
    ${TASK_SETTING_SECTIONS.map(sectionHTML).join('')}
    ${[...footer].map(([bot,n]) => `<p class="settings-cell-note">${esc(bot ? empName(bot) + ' has' : 'There are')} ${n} more wake rules that are not about tasks. <a href="${esc(taskSettingsLink(bot ? {bot} : {}))}">See them in Rules.</a></p>`).join('')}`;
  el.querySelector('select').onchange = e => { TASK_SETTINGS_TYPE = e.target.value; taskSettingsPaint(); };
}
async function taskSettingsLoad() {
  if (TASK_SETTINGS_LOADING) return TASK_SETTINGS_LOADING;
  const version = TASK_SETTINGS_VERSION;
  TASK_SETTINGS_LOADING = (async () => {
    const el = $('#set-task-settings'); if (!el) return;
    try {
      const [jobs] = await Promise.all([get('/v2/system-routines', {fresh: true}), taskTypesLoad({cached: true})]);
      let listing, offset = 0;
      do {
        const page = await get(`/v2/rules?limit=200&offset=${offset}`, {fresh: true});
        if (!listing) listing = {...page, rules: []};
        listing.rules.push(...page.rules);
        if (!page.more) break;
        if (!(page.next_offset > offset)) throw new Error('Could not read the complete rules list');
        offset = page.next_offset;
      } while (true);
      if (version === TASK_SETTINGS_VERSION) { TASK_SETTINGS = {rules: listing, jobs}; taskSettingsPaint(); }
    } catch (e) { el.innerHTML = `<p class="err" role="status">${esc(e.message || 'Could not load task settings')}</p><button type="button">Try again</button>`; el.querySelector('button').onclick = () => taskSettingsLoad(); }
  })().finally(() => {
    TASK_SETTINGS_LOADING = null;
    if (version !== TASK_SETTINGS_VERSION) void taskSettingsLoad();
  });
  return TASK_SETTINGS_LOADING;
}
function taskSettingsChanged() {
  const loaded = TASK_SETTINGS || TASK_SETTINGS_LOADING;
  TASK_SETTINGS_VERSION++;
  TASK_SETTINGS = null;
  // The card remains mounted while Rules is open; update it before a person returns to Tasks.
  if (loaded) void taskSettingsLoad();
}
async function taskSettingsOpenEditor() {
  if (!RULES_OPEN || !RULES) return;
  const request = RULES_OPEN; RULES_OPEN = null;
  if (request.job) {
    await renderSystemRoutines(true);
    const job = SYSTEM_ROUTINES?.jobs.find(j => j.key === request.job);
    if (job?.can_change) { if (job.kind === 'task_rule') taskRuleEditor(job); else { SETTINGS_REVEAL = '#set-system-card'; settingsReveal('#set-system-card'); document.querySelector(`[data-system-routine="${CSS.escape(job.key)}"]`)?.scrollIntoView({block: 'center'}); } }
    return;
  }
  if (request.rule) { const rule = RULES.rules.find(r => r.id === request.rule); if (rule?.can_change) await ruleEditor(rule); return; }
  if (request.add && (request.type ? canMove() : settingsIsAdmin())) await ruleEditor(null, {scope_kind: request.type ? 'type' : 'team', scope_id: request.type || '', event: request.trigger || 'comment', when: request.step ? {step: request.step} : {}});
}
