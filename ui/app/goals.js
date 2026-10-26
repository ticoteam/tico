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
      else { await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: 'dropped', note: ''}); goalRemovedToast(g); }
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
// The colour a person sets by hand sticks until they let the CEO Bot set it again.
const GOAL_COLOURS = [['green', 'On track'], ['yellow', 'At risk'], ['red', 'Off track'], ['done', 'Done']];
function goalColourHtml(g) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  return `<div class="goal-f goal-colour"><span>Colour</span><div class="goal-colour-row">
      <select name="status" aria-label="Colour"><option value="">${byHand ? 'Set by hand' : 'Automatic'}</option>${GOAL_COLOURS.map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select>
      <input name="note" type="text" maxlength="2000" autocomplete="off" placeholder="One sentence: why" aria-label="Why"></div>
      ${byHand ? `<button class="linkish goal-auto" type="button" data-goal-auto>Let ${esc(ceoBotName())} set it</button>` : ''}</div>`;
}
function goalNoteHtml(g, may) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  const said = byHand ? `Set by ${goalActorName(g.status_by)}${g.status_note ? ': ' + g.status_note : ''}` : g.status_note || '';
  const suggest = byHand && g.suggest_status
    ? `<div class="goal-note goal-suggest">${esc(ceoBotName())} suggests <b>${esc(g.suggest_status)}</b>${g.suggest_note ? ': ' + esc(g.suggest_note) : ''}${may ? ' <button class="linkish" type="button" data-goal-auto="' + esc(g.id) + '">Let ' + esc(ceoBotName()) + ' set it</button>' : ''}</div>` : '';
  return (said ? `<div class="goal-note">${esc(said)}</div>` : '') + suggest;
}
// The bets a goal may serve (ui/strategy-page.js): the active ones, and the one it serves now whatever its status, so
// saving never drops it, even one this person can't see (retired or not yet published). Its caption is the strategy's
// word for one ("Bet").
function goalBetHtml(g, may) {
  const st = GOALS_ST?.strategy, all = st?.bets || [];
  const options = all.filter(c => c.status === 'active' || c.id === g.bet_id).sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
  if (!options.length) return '';
  const unseen = g.bet_id && !options.some(c => c.id === g.bet_id)
    ? `<option value="${esc(g.bet_id)}" selected>Retired or unpublished</option>` : '';
  return `<label class="goal-f goal-bet-f"><span>${esc(st.label_one || 'Bet')}</span><select name="bet"${may ? '' : ' disabled'}>
      <option value=""${g.bet_id ? '' : ' selected'}>None</option>${unseen}${options.map(c =>
        `<option value="${esc(c.id)}"${c.id === g.bet_id ? ' selected' : ''}>${esc(c.statement)}</option>`).join('')}</select></label>`;
}
// A done or dropped goal comes back (backend/goals.py `reopen`): with a colour that sticks, or `auto`, the colour it had,
// worked out again by the CEO Bot. The server checks who may, as it does for a colour.
const goalReopenPost = (id, status, note) => post('/v2/goals/' + encodeURIComponent(id) + '/reopen', {status, note});
// After a removal: the redraw the page needs (the Goals page's tree and panel, else the bot page's cards).
const goalsAfterChange = () => GOALS_ST && $('#goal-body') ? goalsReload(GOALS_ST) : goalsRefresh();
// "Goal removed" offers Undo: the server puts back the colour it had from its history (by hand with its note and
// setter, else automatic). The goals that moved up when it was dropped stay where they are.
function goalRemovedToast(g) {
  const panel = document.getElementById('goal-panel'), host = panel?.open ? panel : null;
  toast('Goal removed', false, {label: 'Undo', run: async () => {
    await post('/v2/goals/' + encodeURIComponent(g.id) + '/reopen', {undo: true});
    toast('Goal restored', false, null, {host});
    await goalsAfterChange();
  }}, {host});
}
// "Finished (N)" under an owner's live goals: their done and dropped goals, closed until opened, newest first, the
// latest 20 until "Show all". A row opens read only (its KPIs and its updates); whoever may set its colour may reopen
// it, with a colour (or "Let CEO Bot set it") and one line why. The open state is kept per owner, so a redraw keeps it.
const GOAL_FINISHED_CAP = 20;
const GOAL_FINISHED = {};
const goalFinishedAt = g => g.status_at || g.updated || '';
const goalFinishedOf = (goals, owner) => goals.filter(g => g.owner === owner && !goalLive(g))
  .sort((a, b) => String(goalFinishedAt(b)).localeCompare(String(goalFinishedAt(a))));
function goalFinishedDetailHtml(d) {
  if (!d) return '<p class="muted">Loading…</p>';
  if (d.error) return '<p class="err">Could not load the goal.</p>';
  const kpis = d.kpis || [], checkins = (d.checkins || []).slice().sort((x, y) => String(y.ts).localeCompare(String(x.ts)));
  const history = (d.events || []).filter(e => e.field === 'status').slice().reverse();
  const said = e => `${esc(goalActorName(e.actor))} · ${esc(e.new || 'no colour')}${e.note ? ': ' + esc(e.note) : ''} · ${esc(kpiDay(e.ts))}`;
  return `<div class="goal-fin-detail">
      <h4 class="kpi-h">Measures</h4>${kpis.length ? `<ul class="goal-fin-kpis">${kpis.map(k => `<li>${gdot(k.status)}<span>${esc(k.name)}</span>${kpiFresh(k) ? ` <span class="muted tnum">${esc(kpiNum(k.latest.value, k.unit))}</span>` : ''}</li>`).join('')}</ul>` : '<p class="muted">No KPIs.</p>'}
      <h4 class="kpi-h">Updates</h4>${checkins.length ? checkins.map(goalCheckinHtml).join('') : '<p class="muted">No check-ins.</p>'}
      ${history.length ? `<ul class="goal-fin-history">${history.map(e => `<li class="muted">${said(e)}</li>`).join('')}</ul>` : ''}
    </div>`;
}
function goalReopenAutoOnly(g, goals) {
  const me = mePerson(), actor = me ? 'human:' + me.id : '';
  if (S.me?.role === 'owner' || !actor || g.status_source != null || !g.parent_id || g.owner !== actor) return false;
  const parent = goals.find(x => x.id === g.parent_id);
  return Boolean(parent && parent.owner !== actor);
}
function goalReopenFormHtml(g, goals) {
  const autoOnly = goalReopenAutoOnly(g, goals);
  const choices = autoOnly ? [] : GOAL_COLOURS.filter(([v]) => v !== 'done').map(([v, label]) => `<option value="${v}">${label}</option>`).join('');
  return `<form class="goal-form goal-reopen" data-fin-reopen-form="${esc(g.id)}" novalidate>
      <div class="goal-f goal-colour"><span>Colour</span><div class="goal-colour-row">
        <select name="status" aria-label="Colour"><option value="">Choose…</option>${choices}<option value="auto">Let ${esc(ceoBotName())} set it</option></select>
        <input name="note" type="text" maxlength="300" autocomplete="off" placeholder="One line: why it is back" aria-label="Why it is back"></div></div>
      <div class="goal-actions"><button class="primary" type="submit">Reopen</button><button class="ghost" type="button" data-fin-reopen-cancel>Cancel</button></div>
    </form>`;
}
function goalFinishedHtml(list, goals, F) {
  const shown = F.all ? list : list.slice(0, GOAL_FINISHED_CAP);
  const row = g => {
    const open = F.row === g.id;
    return `<li class="goal-fin-row" data-fin="${esc(g.id)}">
        <div class="goal-fin-head"><button class="goal-fin-line" type="button" data-fin-open="${esc(g.id)}" aria-expanded="${open}">
          <span class="goal-fin-title">${esc(g.title)}</span><span class="goal-fin-what">${g.status === 'done' ? 'Done' : 'Dropped'}</span>
          <span class="muted goal-fin-when">${esc(kpiDay(goalFinishedAt(g)))}${g.status_by ? ' · ' + esc(goalActorName(g.status_by)) : ''}</span></button>
          ${goalMayEdit(g, goals) && F.reopen !== g.id ? `<button class="linkish" type="button" data-fin-reopen="${esc(g.id)}">Reopen</button>` : ''}</div>
        ${F.reopen === g.id ? goalReopenFormHtml(g, goals) : ''}${open ? goalFinishedDetailHtml(F.detail[g.id]) : ''}</li>`;
  };
  return `<section class="goal-fin" aria-label="Finished goals">
      <button class="linkish goal-fin-toggle" type="button" data-fin-toggle aria-expanded="${F.open}">Finished (${list.length})</button>
      ${F.open ? `<ul class="goal-fin-list">${shown.map(row).join('')}</ul>
        ${list.length > shown.length ? `<button class="linkish" type="button" data-fin-all>Show all (${list.length})</button>` : ''}` : ''}
    </section>`;
}
// Draws the list into `host` (nothing when the owner has no finished goal). `changed` runs after a reopen.
function goalFinishedMount(host, owner, goals, changed) {
  if (!host) return;
  const list = goalFinishedOf(goals, owner);
  const F = GOAL_FINISHED[owner] ||= {open: false, all: false, row: '', reopen: '', detail: {}};
  const draw = () => {
    host.innerHTML = list.length ? goalFinishedHtml(list, goals, F) : '';
    host.querySelector('[data-fin-toggle]')?.addEventListener('click', () => { F.open = !F.open; draw(); });
    host.querySelector('[data-fin-all]')?.addEventListener('click', () => { F.all = true; draw(); });
    for (const b of host.querySelectorAll('[data-fin-open]')) b.onclick = async () => {
      const id = b.dataset.finOpen;
      F.row = F.row === id ? '' : id;
      draw();
      if (!F.row || F.detail[id]) return;
      const r = await v2Get('/v2/goals/' + encodeURIComponent(id));
      F.detail[id] = r?.goal || {error: true};
      if (F.row === id) draw();
    };
    for (const b of host.querySelectorAll('[data-fin-reopen]')) b.onclick = () => { F.reopen = b.dataset.finReopen; draw(); host.querySelector('[data-fin-reopen-form] select')?.focus(); };
    const form = host.querySelector('[data-fin-reopen-form]');
    if (!form) return;
    form.querySelector('[data-fin-reopen-cancel]').onclick = () => { F.reopen = ''; draw(); };
    form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); F.reopen = ''; draw(); } };
    form.onsubmit = async ev => {
      ev.preventDefault();
      const status = form.elements.status.value, note = form.elements.note.value.trim();
      if (!status) { form.elements.status.focus(); return; }
      if (!note) { form.elements.note.focus(); return; }
      const save = form.querySelector('[type=submit]');
      save.disabled = true;
      try {
        await goalReopenPost(form.dataset.finReopenForm, status, note);
        F.reopen = ''; delete F.detail[form.dataset.finReopenForm];
        toast('Goal reopened');
        await changed?.();
      } catch (e) { toast(e.message || 'Could not reopen the goal', true); save.disabled = false; }
    };
  };
  draw();
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
      ${goalBetHtml(g, may)}
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
  const title = form.elements.title, ownerSel = form.elements.owner, parentSel = form.elements.parent, betSel = form.elements.bet;
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
    try { await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: 'dropped', note: ''}); goalRemovedToast(g); await goalsRefresh(); done(true); }
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
        if (betSel && betSel.value !== (g.bet_id || '')) body.bet_id = betSel.value;   // "" unlinks it
        if (Object.keys(body).length) await post('/v2/goals/' + encodeURIComponent(g.id), body);
      } else {
        const body = {title: words, owner: company ? GOAL_COMPANY : ownerSel ? ownerSel.value : owner || 'me'};
        if (parentSel?.value) body.parent_id = parentSel.value;
        if (betSel?.value) body.bet_id = betSel.value;
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
  const state = GOALS_ST = {goals: [], other: [], proposals: [], needs: [], owners: {}, loaded: false, open, panel: null, strategy: null};
  // The tree is the page; the CEO Bot is its right rail (under the tree on a narrow window). The sidebar
  // says where you are, so the title is for screen readers only.
  $('#main').classList.add('goals-layout');
  $('#main').innerHTML = `<div class="goals-main"><h1 class="sr-only">Goals</h1>
      <div id="goal-body"><p class="muted">Loading…</p></div></div>
    <aside class="gm gm-rail" id="goal-manager-panel" aria-label="${esc(ceoBotName())}"></aside>`;
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
// The strategy comes with them, for its one line at the top (ui/goals-kpis.js goalStrategyHtml) and the bets a goal
// may serve. The goals never wait on it: if it cannot be read, the line and the picker are left out.
const goalStrategyGet = () => v2Get('/v2/strategy').catch(() => null);
async function goalsLoad(state) {
  let data, needs, strategy;
  try { [data, needs, strategy] = await Promise.all([get('/v2/goals/tree'), v2Get('/v2/goals/needs-you'), goalStrategyGet()]); }
  catch (e) { $('#goal-body').innerHTML = `<p class="err">${esc(e.message || 'Could not load goals')}</p>`; return; }
  if (GOALS_ST !== state) return;
  state.strategy = Array.isArray(strategy?.bets) ? strategy : null;
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
    const st = GOALS_ST;
    const [r, needs, strategy] = await Promise.all([v2Get('/v2/goals/tree'), v2Get('/v2/goals/needs-you'), goalStrategyGet()]);
    if (r && st === GOALS_ST) { goalsApply(st, r, needs); if (Array.isArray(strategy?.bets)) st.strategy = strategy; }
  }
  headGoalsReload();
}

// The CEO Bot can be renamed while Goals is open (the owner, in another tab; `refresh` calls this): the rail's names
// follow and the tree is drawn again, keeping the composer's draft, the chat's stream and a field being typed in.
function goalsFollowRoster() {
  const state = GOALS_ST, host = $('#goal-manager-panel');
  if (!state || !host?.isConnected) return;
  const name = ceoBotName();
  host.setAttribute('aria-label', name);
  const title = host.querySelector('.gm-head h2'); if (title) title.textContent = name;
  host.querySelector('[data-gm-thread]')?.setAttribute('aria-label', name + ' conversation');
  host.querySelector('.gm-form .p-text')?.setAttribute('aria-label', 'Message to the ' + name);
  host._gmDraw?.();
  const body = $('#goal-body'), typing = document.activeElement?.matches?.('input, textarea, select') && body?.contains(document.activeElement);
  if (state.loaded && !typing && state.shape !== goalTreeKey()) goalsRender(state);
}
// The CEO Bot in the right rail of Goals (docs/goals-and-kpis.md): what it does, its routines from the server (when
// each next runs), its last run, and the viewer's chat with it: the room its bot page shows, loaded with the page. A reply
// redraws the tree, since the CEO Bot may have just edited a goal. Leaving the page stops its stream.
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
      <header class="gm-head"><h2>${esc(ceoBotName())}</h2>${off ? `<span class="pill">${esc(off)}</span>` : ''}<a href="#/bot/${GM}">Open bot</a></header>
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
  } else if (!meCeo()) {
    // Of people, only the owner talks to the CEO Bot (backend/assistant.py); everyone else asks their Assistant.
    chat.innerHTML = `<h3 class="rail-h" id="gm-chat-h">Chat</h3>
      <p class="muted gm-on" data-gm-assistant>Only the company's owner talks to ${esc(ceoBotName())}. Ask <a href="${ASSISTANT}">your Assistant</a> instead.</p>`;
  } else {
    chat.innerHTML = `<h3 class="rail-h" id="gm-chat-h">Chat</h3>
      <div class="gm-thread" data-gm-thread role="log" aria-label="${esc(ceoBotName())} conversation" tabindex="0"><p class="muted">Loading conversation…</p></div>
      <div class="gm-form"></div>
      <p class="err" data-gm-error role="alert" hidden></p>`;
    const error = chat.querySelector('[data-gm-error]');
    const fail = text => { if (!current()) return; error.textContent = text || ''; error.hidden = !text; };
    const isReply = m => m.from_actor === 'bot:' + GM;
    // With no AI provider nothing can run: say so rather than wait.
    const noProvider = () => execution?.state === 'queued' && execution.readiness_reason === 'missing_provider';
    const thread = chat.querySelector('[data-gm-thread]');
    let rendered = '';
    host._gmDraw = () => { if (rendered) draw(); };          // a new name, once the thread is drawn (goalsFollowRoster)
    const draw = () => {
      if (!current()) return;
      // Run inspection stays on the full bot page.
      const rows = messages.map(m => v2MessageHTML({...m, run:null}));
      const live = execution && execution.state !== 'completed' ? execution.text : '';
      if (live) rows.push(`<div data-gm-live>${v2MessageHTML({from_actor:'bot:' + GM, body:live})}</div>`);
      else if (noProvider()) rows.push('<p class="muted" data-gm-provider>The ' + esc(ceoBotName()) + ' needs an AI provider. <a href="#/settings" data-gs-tab="models">Settings &gt; Computers</a></p>');
      else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status">${esc(execution?.label || 'Waiting for reply…')}</p>`);
      const html = rows.join('') || '<p class="muted">Ask the ' + esc(ceoBotName()) + ' about your goals.</p>';
      if (html === rendered) return;
      // Keep an older message in view while new replies arrive (ui/app/stick-scroll.js).
      stickScroll(thread).update(() => {
        thread.innerHTML = html;
        const reload = snapshot;
        if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload});
        if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast, reload});
      }, {fresh: !!rendered});
      rendered = html;
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
      const scope = sessionScope(pq(P, '.p-text'));
      try {
        const out = await cloudCompose('/v2/chat/' + GM, {text, refs: {}});
        pillAcknowledge(P, text, [], scope);
        if (!current()) return;
        const fresh = out.conversation && out.conversation.id !== conversation?.id;
        conversation = out.conversation || conversation;
        if (out.message && !messages.some(m => m.id === out.message.id)) messages.push(out.message);
        if (lastReply === undefined) lastReply = messages.filter(isReply).at(-1)?.id ?? null;
        stickScroll(thread).toBottom(); draw();
        if (fresh || !es) watch();
        await snapshot();
      } catch (e) { fail(e.message); }
      finally { sending = P.sending = false; if (current()) pillButtons(P); }
    };
    composer = makePill({mode:'chat', files:false, label:'Message to the ' + ceoBotName(), placeholder:'Ask it to change a goal…', send});
    pq(composer, '.p-text').maxLength = 4000;
    chat.querySelector('.gm-form').append(composer.el);
    void (async () => {
      try {
        const list = await get('/v2/conversations?chat_with=' + GM);
        if (!current() || conversation) return;
        // The owner's one chat with it, the same as their menu tab's (room_key "ceo", backend/rooms.py).
        conversation = (list.conversations || []).find(c => c.room_key === 'ceo' && !c.closed_at) || null;
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
