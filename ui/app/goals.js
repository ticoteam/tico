/* ui/app/goals.js — Goals page shell and the goal editor
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- goals
// The Goals page is one tree: the team, then every human and bot as the team chart nests them,
// one line each with their goal (ui/goals-kpis.js draws it and the panel a tap opens). A goal's level
// comes from its owner: `company`, a human or a bot. Its parent is optional ("Supports"); a goal with
// none is simply not linked. A bot reads its own with `hub goal list`; nothing here is pushed into a run.
let GOALS_ST = null;
const goalBuiltInOwner = actor => String(actor || '').startsWith('bot:') && isBuiltInBot(actor.slice(4));
const goalLive = g => !['done', 'dropped'].includes(g.status);
const goalRank = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9);
const GOAL_COMPANY = 'company';
function goalOwnerInfo(actor) {
  if (actor === GOAL_COMPANY) return {kind: 'company', id: GOAL_COMPANY, name: S.config?.company_name || 'Team', avatar: '', href: ''};
  const [kind, id] = String(actor || '').split(':');
  if (kind === 'bot') {
    const e = S.emps.find(x => x.name === id);
    return {kind, id, name: e?.display_name || id, avatar: avatar(id, 20, stateOf(id)), href: `#/bot/${encodeURIComponent(id)}`};
  }
  const p = (S.people || []).find(x => x.id === id);
  return {kind: 'person', id, name: p?.name || id, avatar: personAvatar(p || {id, name: id}, 20), href: `#/person/${encodeURIComponent(id)}`};
}
// Where an owner sits on the org chart: its node key and the chain of keys above it. Who is above whom is who reports
// to whom, not the groups people are in.
function goalOrgPlace(owner) {
  const up = {};
  for (const p of S.people || []) up['p:' + p.id] = orgBossKey({kind: 'person', person: p});
  for (const e of S.emps || []) up['b:' + e.name] = orgBossKey({kind: 'bot', ...e});
  const key = String(owner || '').startsWith('human:') ? 'p:' + owner.slice(6) : String(owner || '').startsWith('bot:') ? 'b:' + owner.slice(4) : '';
  const above = new Set();
  for (let at = up[key]; at && !above.has(at); at = up[at]) above.add(at);
  return {key, parent: up[key] || '', above};
}
// Mirrors the hub's rule: the goal's owner, the owner of the goal it supports, or anyone above the
// owner on the org chart; the environment's owner edits every goal.
function goalMayEdit(g, goals = []) {
  if (S.me?.role === 'owner') return true;
  const me = mePerson(), actor = me ? 'human:' + me.id : '';
  if (!actor) return false;
  const parent = goals.find(x => x.id === g.parent_id);
  return g.owner === actor || parent?.owner === actor || goalOrgPlace(g.owner).above.has('p:' + me.id);
}
// The options of a "Supports" select: Nothing first (a goal needs no parent), then team goals,
// goals above the owner on the org chart, and goals alongside it. Never the goal itself or anything
// under it (that would loop).
function goalSupportOptions(g, owner, goals) {
  const below = new Set(g.id ? [g.id] : []);
  for (let grew = Boolean(g.id); grew;) {
    grew = false;
    for (const x of goals) if (x.parent_id && below.has(x.parent_id) && !below.has(x.id)) { below.add(x.id); grew = true; }
  }
  const me = goalOrgPlace(owner);
  const rows = {company: [], above: [], alongside: []};
  for (const x of goals) {
    if (!goalLive(x) || below.has(x.id)) continue;
    const them = goalOrgPlace(x.owner);
    const where = x.owner === GOAL_COMPANY ? 'company' : me.above.has(them.key) ? 'above'
      : them.key && (them.key === me.key || (them.parent && them.parent === me.parent)) ? 'alongside' : '';
    if (where || x.id === g.parent_id) rows[where || 'above'].push(x);
  }
  const option = x => `<option value="${esc(x.id)}"${x.id === g.parent_id ? ' selected' : ''}>${esc(x.title)}${x.owner === GOAL_COMPANY ? '' : ' · ' + esc(goalOwnerInfo(x.owner).name)}</option>`;
  const group = (label, list) => list.length ? `<optgroup label="${label}">${list.sort(goalRank).map(option).join('')}</optgroup>` : '';
  return `<option value=""${g.parent_id ? '' : ' selected'}>Nothing</option>`
    + group('Team', rows.company) + group('Above on the team chart', rows.above) + group('Alongside', rows.alongside);
}
const goalLinkOptions = (g, goals) => goalSupportOptions(g, g.owner, goals);
// One goal, editable in place (the bot page's Goals card): the words, and what it supports.
function goalEditorHtml(g, goals) {
  const may = goalMayEdit(g, goals), parent = goals.find(x => x.id === g.parent_id);
  return `<div class="goal-ed" data-goal-ed="${esc(g.id)}">
      <div class="goal-ed-row"><input class="goal-ed-text" type="text" maxlength="300" value="${esc(g.title)}" aria-label="Goal"${may ? '' : ' readonly'}>
        ${may ? `<button class="ghost goal-ed-link" type="button" title="Change the goal this supports" aria-label="Change the goal this supports"><span class="nav-icon" aria-hidden="true">link</span></button>` : ''}</div>
      ${may ? `<button type="button" class="goal-ed-supports goal-ed-change" aria-label="Change the goal this supports">Supports <u>${esc(parent ? parent.title : 'nothing')}</u></button>`
        : parent ? `<div class="goal-ed-supports">Supports <a href="${GOALS}/${encodeURIComponent(parent.id)}" data-goal-go="${esc(parent.id)}">${esc(parent.title)}</a></div>` : ''}
      ${may ? `<select class="goal-ed-pick" hidden aria-label="The goal this supports">${goalLinkOptions(g, goals)}</select>` : ''}
    </div>`;
}
// Enter or leaving the field saves; Escape puts the words back. An emptied goal is removed.
function bindGoalEditor(el, g, done) {
  const input = el.querySelector('.goal-ed-text'), pick = el.querySelector('.goal-ed-pick');
  let saving = false;
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
    if (ev.key === 'Escape') { input.value = g.title; input.blur(); }
  };
  input.onblur = async () => {
    const text = input.value.trim();
    if (saving || input.readOnly || text === g.title) return;
    saving = true;
    try {
      if (text) await post('/v2/goals/' + encodeURIComponent(g.id), {title: text});
      else { await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: 'dropped', note: ''}); toast('Goal removed'); }
      g.title = text || g.title;
      await goalsRefresh();
      done?.(!text);
    } catch (e) { toast(e.message || 'Could not save the goal', true); input.value = g.title; }
    saving = false;
  };
  const toggle = () => { pick.hidden = !pick.hidden; if (!pick.hidden) { pick.focus(); pick.showPicker?.(); } };
  const link = el.querySelector('.goal-ed-link');
  if (link) link.onclick = toggle;
  const change = el.querySelector('.goal-ed-change');
  if (change) change.onclick = toggle;
  if (pick) pick.onchange = async () => {
    try {
      await post('/v2/goals/' + encodeURIComponent(g.id), {parent_id: pick.value});
      await goalsRefresh();
      done?.(false);
    } catch (e) { toast(e.message || 'Could not link the goal', true); }
  };
}
// "Add goal" becomes an empty line; Enter or leaving it with words saves it. No parent: it is simply not linked.
function goalAddInline(host, owner, done) {
  host.innerHTML = `<input class="goal-ed-text goal-add-text" type="text" maxlength="300" aria-label="New goal" placeholder="Plain English: what they are going for">`;
  const input = host.querySelector('input');
  input.focus();
  let saving = false;
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
    if (ev.key === 'Escape') { input.value = ''; input.blur(); }
  };
  input.onblur = async () => {
    const title = input.value.trim();
    if (saving) return;
    if (!title) { done?.(null); return; }
    saving = true;
    try {
      const out = await post('/v2/goals', {title, owner});
      await goalsRefresh();
      done?.(out.goal);
    } catch (e) { toast(e.message || 'Could not add the goal', true); saving = false; input.focus(); }
  };
}

// The Goals page's editor: one form for a new goal and an existing one. Goal, Owner (new goals),
// Supports (optional, Nothing by default) and one Save.
function goalOwnerChoices(mine) {
  if (S.me?.role !== 'owner') return mine ? [[mine, 'Me']] : [];
  return [...(S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id]),
          ...shownEmps().filter(e => !isBuiltInBot(e.name)).map(e => ['bot:' + e.name, e.display_name || e.name])];
}
// The colour a person sets by hand sticks until they let the Goal Manager set it again.
const GOAL_COLOURS = [['green', 'On track'], ['yellow', 'At risk'], ['red', 'Off track'], ['done', 'Done']];
function goalColourHtml(g) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  return `<div class="goal-f goal-colour"><span>Colour</span><div class="goal-colour-row">
      <select name="status" aria-label="Colour"><option value="">${byHand ? 'Set by hand' : 'Automatic'}</option>${GOAL_COLOURS.map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select>
      <input name="note" type="text" maxlength="2000" autocomplete="off" placeholder="One sentence: why" aria-label="Why"></div>
      ${byHand ? '<button class="linkish goal-auto" type="button" data-goal-auto>Let Goal Manager set it</button>' : ''}</div>`;
}
function goalNoteHtml(g, may) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  const said = byHand ? `Set by ${goalActorName(g.status_by)}${g.status_note ? ': ' + g.status_note : ''}` : g.status_note || '';
  const suggest = byHand && g.suggest_status
    ? `<div class="goal-note goal-suggest">Goal Manager suggests <b>${esc(g.suggest_status)}</b>${g.suggest_note ? ': ' + esc(g.suggest_note) : ''}${may ? ' <button class="linkish" type="button" data-goal-auto="' + esc(g.id) + '">Let Goal Manager set it</button>' : ''}</div>` : '';
  return (said ? `<div class="goal-note">${esc(said)}</div>` : '') + suggest;
}
function goalFormHtml(g, goals, {owner = '', company = false, fixed = false} = {}) {
  company = company || g.owner === GOAL_COMPANY;      // a team goal supports nothing
  const may = !g.id || goalMayEdit(g, goals);
  const me = mePerson(), mine = me ? 'human:' + me.id : '';
  const choices = g.id || company || fixed ? [] : goalOwnerChoices(mine);   // fixed: the owner is whoever was tapped
  const who = owner || mine || choices[0]?.[0] || '';
  const kids = g.id ? goals.filter(x => goalLive(x) && x.parent_id === g.id).sort(goalRank) : [];
  return `<form class="goal-form goal-ed" data-goal-form="${esc(g.id || (company ? 'company' : 'new'))}" novalidate>
      <label class="goal-f"><span>Goal</span><input type="text" name="title" maxlength="300" autocomplete="off" value="${esc(g.title || '')}"${may ? '' : ' readonly'} placeholder="${company ? 'Reach 100 paying customers by June' : 'Answer every ticket the same day'}"></label>
      ${choices.length ? `<div class="goal-fs"><label class="goal-f"><span>Owner</span><select name="owner">${choices.map(([v, name]) => `<option value="${esc(v)}"${v === who ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></label>
        <label class="goal-f"><span>Supports</span><select name="parent">${goalSupportOptions(g, who, goals)}</select></label></div>`
      : company ? '' : `<label class="goal-f"><span>Supports</span><select name="parent"${may ? '' : ' disabled'}>${goalSupportOptions(g, g.owner || who, goals)}</select></label>`}
      ${kids.length ? `<div class="goal-ed-kids"><span>Supported by</span>${kids.map(k =>
        `<a href="${GOALS}/${encodeURIComponent(k.id)}" data-goal-go="${esc(k.id)}">${esc(k.title)} <span class="muted">· ${esc(goalOwnerInfo(k.owner).name)}</span></a>`).join('')}</div>` : ''}
      ${may && g.id ? goalColourHtml(g) : ''}
      <div class="goal-actions">
        ${may ? '<button class="primary" type="submit">Save</button>' : ''}<button class="ghost" type="button" data-goal-cancel>${may ? 'Cancel' : 'Close'}</button>
        <span class="spacer"></span>${may && g.id ? '<button class="ghost danger" type="button" data-goal-remove>Remove</button>' : ''}
      </div>
    </form>`;
}
function bindGoalForm(form, g, goals, {company = false, owner = ''} = {}, done) {
  const title = form.elements.title, ownerSel = form.elements.owner, parentSel = form.elements.parent;
  const save = form.querySelector('[type=submit]');
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  form.querySelector('[data-goal-cancel]').onclick = () => done();
  if (ownerSel) ownerSel.onchange = () => { parentSel.innerHTML = goalSupportOptions(g, ownerSel.value, goals); };
  const auto = form.querySelector('[data-goal-auto]');
  if (auto) auto.onclick = async () => {
    auto.disabled = true;
    try { await post('/v2/goals/' + encodeURIComponent(g.id) + '/status/auto', {}); await goalsRefresh(); done(true); }
    catch (e) { toast(e.message || 'Could not hand the colour back', true); auto.disabled = false; }
  };
  const remove = form.querySelector('[data-goal-remove]');
  if (remove) remove.onclick = async () => {
    remove.disabled = true;
    try { await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: 'dropped', note: ''}); toast('Goal removed'); await goalsRefresh(); done(true); }
    catch (e) { toast(e.message || 'Could not remove the goal', true); remove.disabled = false; }
  };
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!save) return;
    const words = title.value.trim();
    if (!words) { title.focus(); return; }
    const colour = g.id && form.elements.status ? form.elements.status.value : '';
    const why = g.id && form.elements.note ? form.elements.note.value.trim() : '';
    if (colour && colour !== 'done' && !why) { form.elements.note.focus(); return; }
    save.disabled = true;
    try {
      if (colour) await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: colour, note: why});
      if (g.id) {
        const body = {};
        if (words !== g.title) body.title = words;
        if (parentSel && parentSel.value !== (g.parent_id || '')) body.parent_id = parentSel.value;
        if (Object.keys(body).length) await post('/v2/goals/' + encodeURIComponent(g.id), body);
      } else {
        const body = {title: words, owner: company ? GOAL_COMPANY : ownerSel ? ownerSel.value : owner || 'me'};
        if (parentSel?.value) body.parent_id = parentSel.value;
        await post('/v2/goals', body);
      }
      await goalsRefresh();
      done(true);
    } catch (e) { toast(e.message || 'Could not save the goal', true); save.disabled = false; }
  };
  title.focus();
}
function pageGoals() {
  const open = S.route.startsWith(GOALS + '/') ? decodeURIComponent(S.route.slice(GOALS.length + 1)) : '';
  const was = document.getElementById('goal-panel');
  if (was) { was._state = null; if (was.open) was.close(); }
  const state = GOALS_ST = {goals: [], other: [], proposals: [], needs: [], owners: {}, loaded: false, open, panel: null};
  // The tree is the page; the Goal Manager is its right rail (under the tree on a narrow window). The sidebar
  // says where you are, so the title is for screen readers only.
  $('#main').classList.add('goals-layout');
  $('#main').innerHTML = `<div class="goals-main"><h1 class="sr-only">Goals</h1>
      <div id="goal-body"><p class="muted">Loading…</p></div></div>
    <aside class="gm gm-rail" id="goal-manager-panel" aria-label="Goal Manager"></aside>`;
  goalManagerMount(state);
  goalsLoad(state);
}
function goalsApply(state, data, needs) {
  state.goals = (data.goals || []).filter(g => !goalBuiltInOwner(g.owner));
  state.other = (data.other_kpis || []).filter(k => !goalBuiltInOwner(k.owner));
  state.proposals = data.proposals || [];
  state.owners = data.owners || {};
  if (needs !== undefined) state.needs = (needs?.items || []).filter(n => !goalBuiltInOwner(n.goal_owner) && !goalBuiltInOwner(n.owner) && !(data.goals || []).some(g => g.id === (n.goal_id || n.proposal?.goal_id) && goalBuiltInOwner(g.owner)));
}
// Two requests: the tree and what needs you. The roster (people, bots, departments) is already in S.
async function goalsLoad(state) {
  let data, needs;
  try { [data, needs] = await Promise.all([get('/v2/goals/tree'), v2Get('/v2/goals/needs-you')]); }
  catch (e) { $('#goal-body').innerHTML = `<p class="err">${esc(e.message || 'Could not load goals')}</p>`; return; }
  if (GOALS_ST !== state) return;
  goalsApply(state, data, needs);
  state.loaded = true;
  goalsRender(state);
  const g = state.open && state.goals.find(x => x.id === state.open);
  if (g) goalPanelOpen(state, g.owner, {goal: g.id});
}
// The goals of whoever's page this is, at the top under their name: one line each. Tapping one
// opens it on the Goals page.
let HEAD_GOALS = '';
async function headGoalsLoad(actor) {
  HEAD_GOALS = actor;
  const el = $('#head-goals'); if (!el) return;
  const r = await v2Get(`/v2/goals?owner=${encodeURIComponent(actor)}`);
  if (HEAD_GOALS !== actor || !$('#head-goals')) return;
  const mine = (r?.goals || []).filter(goalLive);
  el.innerHTML = mine.map(g => `<li data-goal="${esc(g.id)}"><span>${esc(g.title)}</span></li>`).join('');
  el.hidden = !mine.length;
  const standing = $('#head-standing'); if (standing) standing.hidden = Boolean(mine.length);
  for (const li of el.querySelectorAll('[data-goal]')) li.onclick = () => openGoal(li.dataset.goal);
}
function headGoalsReload() {
  if (HEAD_GOALS && $('#head-goals')) void headGoalsLoad(HEAD_GOALS);
  if (BOT && $('#bot-goal')) void botGoalLoad(BOT.slug);
  if (BOT && $('#bot-goals-list')) void botGoalsCardLoad(BOT.slug);
}
function openGoal(id) { location.hash = GOALS + '/' + encodeURIComponent(id); }
async function goalsRefresh() {
  GOAL_OPTIONS = null;
  if (GOALS_ST && $('#goal-body')) {
    const [r, needs] = await Promise.all([v2Get('/v2/goals/tree'), v2Get('/v2/goals/needs-you')]);
    if (r) goalsApply(GOALS_ST, r, needs);
  }
  headGoalsReload();
}

// The Goal Manager in the right rail of Goals (docs/goals-and-kpis.md): what it does, its routines from the server (when
// each next runs), its last run, and the viewer's chat with it: the room its bot page shows, loaded with the page. A reply
// redraws the tree, since the Goal Manager may have just edited a goal. Leaving the page stops its stream.
let GOAL_MANAGER_STOP = null;
const GM = 'goal-manager';
const GM_STATE = {paused: 'Paused', planned: 'Not set up', archived: 'Off', retired: 'Off', quarantined: 'Paused'};
async function goalManagerMount(pageState) {
  GOAL_MANAGER_STOP?.();
  const host = $('#goal-manager-panel');
  if (!host) return;
  const bot = (S.emps || []).find(e => e.name === GM);
  const active = !!bot && !GM_STATE[bot.status];
  let stopped = false, es = null, poll = null, conversation = null, messages = [], execution = null, sending = false, lastReply, composer = null;
  const current = () => !stopped && GOALS_ST === pageState && host.isConnected;
  // `es` is the release of this chat's live events (ui/app/live.js).
  const stopStream = () => { try { es?.(); } catch { /* released */ } es = null; clearInterval(poll); poll = null; };
  GOAL_MANAGER_STOP = () => { stopped = true; stopStream(); if (composer) PILLS.delete(composer); };
  const off = !bot ? 'Not set up' : GM_STATE[bot.status] || '';
  host.innerHTML = `<section class="rail-sec gm-info">
      <header class="gm-head"><h2>Goal Manager</h2>${off ? `<span class="pill">${esc(off)}</span>` : ''}<a href="#/bot/${GM}">Open bot</a></header>
      <p class="gm-what">Keeps KPIs current and each goal green, yellow or red. Humans set the goals.</p></section>
    ${bot ? `<section class="rail-sec" aria-labelledby="gm-routines-h"><h3 class="rail-h" id="gm-routines-h">Routines</h3>
      <ul class="gm-routines" data-gm-routines><li class="muted">Loading…</li></ul><p class="gm-last" data-gm-result></p></section>` : ''}
    <section class="rail-sec gm-chat" data-gm-chat aria-labelledby="gm-chat-h"><h3 class="rail-h" id="gm-chat-h">Chat</h3></section>`;
  const chat = host.querySelector('[data-gm-chat]');
  if (!active) {
    chat.innerHTML = '<h3 class="rail-h" id="gm-chat-h">Chat</h3>' + (S.me?.role === 'owner'
      ? '<div class="gm-on"><button class="primary" type="button" data-gm-on>Turn on</button><span class="muted" data-gm-status role="status"></span></div>'
      : '<p class="muted gm-on">The owner can turn it on.</p>');
    const on = chat.querySelector('[data-gm-on]');
    if (on) on.onclick = async () => {
      on.disabled = true;
      const status = chat.querySelector('[data-gm-status]');
      status.textContent = '';
      try {
        const result = await post('/v2/goal-manager/turn-on', {});
        if (!current()) return;
        if (result.state === 'active') { await refresh(true); if (current()) goalManagerMount(pageState); return; }
        status.textContent = 'Needs a computer with a model. See Settings.';
      } catch (e) { if (current()) status.textContent = e.message; }
      if (current()) on.disabled = false;
    };
  } else {
    chat.innerHTML = `<h3 class="rail-h" id="gm-chat-h">Chat</h3>
      <div class="gm-thread" data-gm-thread role="log" aria-label="Goal Manager conversation" tabindex="0"><p class="muted">Loading conversation…</p></div>
      <div class="gm-form"></div>
      <p class="err" data-gm-error role="alert" hidden></p>`;
    const error = chat.querySelector('[data-gm-error]');
    const fail = text => { if (!current()) return; error.textContent = text || ''; error.hidden = !text; };
    const isReply = m => m.from_actor === 'bot:' + GM;
    // With no AI provider nothing can run: say so rather than wait.
    const noProvider = () => execution?.state === 'queued' && execution.readiness_reason === 'missing_provider';
    const thread = chat.querySelector('[data-gm-thread]');
    let rendered = '';
    const draw = () => {
      if (!current()) return;
      // Run inspection stays on the full bot page.
      const rows = messages.map(m => v2MessageHTML({...m, run:null}));
      const live = execution && execution.state !== 'completed' ? execution.text : '';
      if (live) rows.push(`<div data-gm-live>${v2MessageHTML({from_actor:'bot:' + GM, body:live})}</div>`);
      else if (noProvider()) rows.push('<p class="muted" data-gm-provider>The Goal Manager needs an AI provider. <a href="#/settings" data-gs-tab="providers">Settings &gt; AI providers</a></p>');
      else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status">${esc(execution?.label || 'Waiting for reply…')}</p>`);
      const html = rows.join('') || '<p class="muted">Ask the Goal Manager about your goals.</p>';
      if (html === rendered) return;
      const atEnd = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
      const wasTop = thread.scrollTop;
      thread.innerHTML = html; rendered = html;
      const reload = snapshot;
      if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload});
      if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast, reload});
      // Keep an older message in view while new replies arrive.
      thread.scrollTop = atEnd ? thread.scrollHeight : wasTop;
    };
    const apply = async data => {
      if (!current()) return;
      messages = data.messages || [];
      execution = data.execution || null;
      draw();
      const reply = messages.filter(isReply).at(-1)?.id;
      // A new reply after the first look may come with an edited goal: draw the tree again.
      if (lastReply !== undefined && reply !== lastReply) await goalsReload(pageState);
      lastReply = reply ?? null;
    };
    const snapshot = async () => {
      if (!conversation || !current()) return;
      try { await apply(await get(`/v2/conversations/${encodeURIComponent(conversation.id)}/snapshot`)); fail(''); }
      catch (e) { fail(e.message); }
    };
    // The page's live events, as the bot page's chat uses them: a change to this conversation reads its snapshot
    // again. Without EventSource, a poll.
    const watch = () => {
      stopStream();
      if (!conversation || !current()) return;
      const fallback = liveFallback(snapshot, 5000);
      if (fallback) { poll = fallback; return; }
      const cid = conversation.id, mine = d => d.conversation_id === cid && current();
      const soon = () => liveSoon('gm:' + cid, () => void snapshot(), 150);
      const offs = [liveFollow(cid), liveOn('messages', d => { if (mine(d)) soon(); }),
                    liveOn('runs', d => { if (mine(d)) soon(); }), liveOn('reset', () => { if (current()) soon(); })];
      es = () => offs.forEach(off => off());
    };
    const send = async (P, text) => {
      if (!text || sending) return;
      sending = P.sending = true; pillButtons(P); fail('');
      try {
        const out = await cloudCompose('/v2/chat/' + GM, {text, refs: {}});
        if (!current()) return;
        const fresh = out.conversation && out.conversation.id !== conversation?.id;
        conversation = out.conversation || conversation;
        if (out.message && !messages.some(m => m.id === out.message.id)) messages.push(out.message);
        if (lastReply === undefined) lastReply = messages.filter(isReply).at(-1)?.id ?? null;
        pillAcknowledge(P, text, []); draw();
        if (fresh || !es) watch();
        await snapshot();
      } catch (e) { fail(e.message); }
      finally { sending = P.sending = false; if (current()) pillButtons(P); }
    };
    composer = makePill({mode:'chat', files:false, label:'Message to the Goal Manager', placeholder:'Ask it to change a goal…', send});
    pq(composer, '.p-text').maxLength = 4000;
    chat.querySelector('.gm-form').append(composer.el);
    void (async () => {
      try {
        const list = await get('/v2/conversations?chat_with=' + GM);
        if (!current() || conversation) return;
        conversation = v2ChatRoom(list.conversations, GM);
        if (conversation) { await snapshot(); watch(); }
        else draw();
      } catch (e) { fail(e.message); }
    })();
  }
  if (!bot) return;
  // The routines are the server's rows; the times are the viewer's own, with the routine's zone when it differs.
  const routinesEl = host.querySelector('[data-gm-routines]'), resultEl = host.querySelector('[data-gm-result]');
  try {
    const rows = (await get(`/v2/bots/${GM}/routines`)).routines || [];
    const runs = await Promise.all(rows.map(r => get(`/v2/routines/${encodeURIComponent(r.id)}/occurrences`).then(o => o.occurrences?.[0] || null, () => null)));
    if (!current()) return;
    routinesEl.innerHTML = rows.length ? rows.map(r => {
      const when = r.enabled === false || !r.active ? 'paused' : r.on ? '' : r.next ? 'next ' + fmt(r.next) : '';
      return `<li><span class="nav-icon" aria-hidden="true">schedule</span><span><b>${esc(r.title)}</b> · ${esc(cadenceZoned(r))}${when ? ' · ' + esc(when) : ''}</span></li>`;
    }).join('') : '<li class="muted">No routines.</li>';
    // The last run: what the bot last reported, else the newest routine firing.
    const status = v2StatusOf(GM);
    const fired = runs.filter(Boolean).map(o => ({at: o.started || o.occurrence, what: o.outcome || o.status || ''}))
      .concat(rows.filter(r => r.last_fired).map(r => ({at: r.last_fired, what: ''})))
      .sort((a, b) => String(b.at).localeCompare(String(a.at)))[0];
    const at = status?.last_turn_at || fired?.at, said = String(status?.last_result || fired?.what || '').split('\n')[0].slice(0, 240);
    resultEl.textContent = at ? `Last run ${fmt(at) || at}${said ? ' · ' + said : ''}` : 'Not run yet.';
  } catch (e) {
    if (!current()) return;
    routinesEl.innerHTML = `<li class="err">Routines did not load. ${esc(e.message || '')}</li>`;
    resultEl.textContent = '';
  }
}
