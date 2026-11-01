/* ui/app/goals.js — Goals page shell and the goal editor; the KPI views under the tree are ui/app/goals-kpi-views.js
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
    const e = botRow(id);
    return {kind, id, name: e?.display_name || e?.slug || id, avatar: avatar(id, 20, stateOf(id)), href: `#/bot/${encodeURIComponent(id)}`};
  }
  const p = (S.people || []).find(x => x.id === id);
  return {kind: 'person', id, name: p?.name || id, avatar: personAvatar(p || {id, name: id}, 20), href: `#/person/${encodeURIComponent(id)}`};
}
// Where an owner sits on the org chart: its node key and the chain of keys above it. Who is above whom is who reports
// to whom, not the groups people are in.
function goalOrgPlace(owner) {
  const up = {};
  for (const p of S.people || []) up['p:' + p.id] = orgBossKey({kind: 'person', person: p});
  for (const e of S.emps || []) up['b:' + e.id] = orgBossKey({kind: 'bot', ...e});
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
  return g.owner_id === actor || parent?.owner_id === actor || goalOrgPlace(g.owner_id).above.has('p:' + me.id);
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
    const them = goalOrgPlace(x.owner_id);
    const where = x.owner_id === GOAL_COMPANY ? 'company' : me.above.has(them.key) ? 'above'
      : them.key && (them.key === me.key || (them.parent && them.parent === me.parent)) ? 'alongside' : '';
    if (where || x.id === g.parent_id) rows[where || 'above'].push(x);
  }
  const option = x => `<option value="${esc(x.id)}"${x.id === g.parent_id ? ' selected' : ''}>${esc(x.title)}${x.owner_id === GOAL_COMPANY ? '' : ' · ' + esc(goalOwnerInfo(x.owner_id).name)}</option>`;
  const group = (label, list) => list.length ? `<optgroup label="${label}">${list.sort(goalRank).map(option).join('')}</optgroup>` : '';
  return `<option value=""${g.parent_id ? '' : ' selected'}>Nothing</option>`
    + group('Team', rows.company) + group('Above on the team chart', rows.above) + group('Alongside', rows.alongside);
}
const goalLinkOptions = (g, goals) => goalSupportOptions(g, g.owner_id, goals);
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
const goalEveryone = () => [...(S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id]),
  ...shownEmps().filter(e => !isBuiltInBot(e.id)).map(e => ['bot:' + e.id, e.display_name || e.slug])];
function goalOwnerChoices(mine) {
  if (S.me?.role !== 'owner') return mine ? [[mine, 'Me']] : [];
  return goalEveryone();
}
// The sheet's owners: the environment's owner may pick the company or anyone; anyone else themselves or someone below
// them on the org chart (backend/goal_routes.py goal_create). The goal's current owner always stays a choice.
function goalSheetOwners(current) {
  const me = mePerson(), mine = me ? 'human:' + me.id : '';
  const all = goalEveryone();
  const out = S.me?.role === 'owner' ? [[GOAL_COMPANY, goalOwnerInfo(GOAL_COMPANY).name], ...all]
    : all.filter(([v]) => v === mine || (me && goalOrgPlace(v).above.has('p:' + me.id)));
  if (mine && !out.some(([v]) => v === mine)) out.unshift([mine, me.name || 'Me']);
  if (current && !out.some(([v]) => v === current)) out.unshift([current, goalOwnerInfo(current).name]);
  return out;
}
// Handing a goal to someone else: the owner of the goal it supports, or someone above its owner (goal_update).
function goalMayMove(g, goals = []) {
  if (S.me?.role === 'owner') return true;
  const me = mePerson();
  if (!me || g.owner_id === GOAL_COMPANY) return false;
  return goals.find(x => x.id === g.parent_id)?.owner_id === 'human:' + me.id || goalOrgPlace(g.owner_id).above.has('p:' + me.id);
}
// A group's goals are its lead's: the "+" on a group's line adds one for the lead, when this person may.
function goalGroupOwner(gid) {
  const g = (GOALS_ST?.groups || []).find(x => x.id === gid) || (S.orgGroups || []).find(x => x.id === gid);
  const lead = String(g?.lead || ''), owner = lead.startsWith('person:') ? 'human:' + lead.slice(7) : lead;
  return owner && goalMayEdit({owner}, GOALS_ST?.goals || []) ? owner : '';
}
// A long press on a phone line adds a goal, as "+" does under a pointer; the tap that ends it opens nothing.
function goalLongPress(el, fire) {
  let timer = 0;
  const stop = () => { clearTimeout(timer); timer = 0; if (el._pressed) setTimeout(() => { el._pressed = false; }, 400); };
  el.addEventListener('touchstart', ev => {
    if (ev.touches.length !== 1) return;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = 0; el._pressed = true; fire(); }, 500);
  }, {passive: true});
  for (const t of ['touchend', 'touchmove', 'touchcancel']) el.addEventListener(t, stop, {passive: true});
  el.addEventListener('contextmenu', ev => { if (el._pressed) ev.preventDefault(); });
}
function goalPressed(el) { if (!el._pressed) return false; el._pressed = false; return true; }
// One goal on its own: tap a goal on the Goals tab, or "+" for a new one. A bottom sheet on a phone, a side panel on a
// wider window (the owner's panel's frame, ui/styles/goals.css .gpanel). Details opens the owner's whole panel.
function goalSheetOpen(state, {goal = '', owner = ''} = {}) {
  const g = goal ? state.goals.find(x => x.id === goal) : null;
  if (goal && !g) return;
  if (!g) { const me = mePerson(); owner = owner || (me ? 'human:' + me.id : S.me?.role === 'owner' ? GOAL_COMPANY : ''); }
  let dlg = document.getElementById('goal-sheet');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'goal-sheet'; dlg.className = 'tmodal kpi-panel gpanel gsheet';
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
  }
  dlg.setAttribute('aria-label', g ? 'Goal' : 'New goal');
  dlg.innerHTML = `<div class="tmodal-head"><h2 class="gs-h">${g ? 'Goal' : 'New goal'}</h2><span class="spacer"></span>
      ${g ? '<button class="linkish" type="button" data-gs-details>Details</button>' : ''}
      <button class="ghost tmodal-x" type="button" data-gs-close aria-label="Close">✕</button></div>
    <div class="tmodal-body gs-body">${goalFormHtml(g || {}, state.goals, {owner, sheet: true})}</div>`;
  if (!dlg.open) dlg.showModal();
  dlg.querySelector('[data-gs-close]').onclick = () => dlg.close();
  const details = dlg.querySelector('[data-gs-details]');
  if (details) details.onclick = () => { dlg.close(); goalPanelOpen(state, g.owner_id, {goal: g.id}); };
  for (const a of dlg.querySelectorAll('[data-goal-go]')) a.onclick = ev => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    ev.preventDefault();
    goalSheetOpen(state, {goal: a.dataset.goalGo});
  };
  bindGoalForm(dlg.querySelector('[data-goal-form]'), g || {}, state.goals, {owner}, ok => {
    dlg.close();
    if (ok && GOALS_ST === state) goalsRender(state);
  });
}
window.addEventListener('hashchange', () => {
  const dlg = document.getElementById('goal-sheet');
  if (dlg?.open && !location.hash.startsWith(goalsBase())) dlg.close();
});
// The colour a person sets by hand sticks until they let it be set automatically again.
const GOAL_COLOURS = [['green', 'On track'], ['yellow', 'At risk'], ['red', 'Off track'], ['done', 'Done']];
function goalColourHtml(g) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  return `<div class="goal-f goal-colour"><span>Colour</span><div class="goal-colour-row">
      <select name="status" aria-label="Colour"><option value="">${byHand ? 'Set by hand' : 'Automatic'}</option>${GOAL_COLOURS.map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select>
      <input name="note" type="text" maxlength="2000" autocomplete="off" placeholder="One sentence: why" aria-label="Why"></div>
      ${byHand ? '<button class="linkish goal-auto" type="button" data-goal-auto>Let the Assistant set it</button>' : ''}</div>`;
}
function goalNoteHtml(g, may) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  const said = byHand ? `Set by ${goalActorName(g.status_by_id)}${g.status_note ? ': ' + g.status_note : ''}` : g.status_note || '';
  const suggest = byHand && g.suggest_status
    ? `<div class="goal-note goal-suggest">Suggested: <b>${esc(g.suggest_status)}</b>${g.suggest_note ? ', ' + esc(g.suggest_note) : ''}${may ? ' <button class="linkish" type="button" data-goal-auto="' + esc(g.id) + '">Let the Assistant set it</button>' : ''}</div>` : '';
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
// worked out again automatically. The server checks who may, as it does for a colour.
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
// it, with a colour (or "Let the Assistant set it") and one line why. The open state is kept per owner, so a redraw
// keeps it.
const GOAL_FINISHED_CAP = 20;
const GOAL_FINISHED = {};
const goalFinishedAt = g => g.status_at || g.updated_at || '';
const goalFinishedOf = (goals, owner) => goals.filter(g => g.owner_id === owner && !goalLive(g))
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
  if (S.me?.role === 'owner' || !actor || g.status_source != null || !g.parent_id || g.owner_id !== actor) return false;
  const parent = goals.find(x => x.id === g.parent_id);
  return Boolean(parent && parent.owner_id !== actor);
}
function goalReopenFormHtml(g, goals) {
  const autoOnly = goalReopenAutoOnly(g, goals);
  const choices = autoOnly ? [] : GOAL_COLOURS.filter(([v]) => v !== 'done').map(([v, label]) => `<option value="${v}">${label}</option>`).join('');
  return `<form class="goal-form goal-reopen" data-fin-reopen-form="${esc(g.id)}" novalidate>
      <div class="goal-f goal-colour"><span>Colour</span><div class="goal-colour-row">
        <select name="status" aria-label="Colour"><option value="">Choose…</option>${choices}<option value="auto">Let the Assistant set it</option></select>
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
          <span class="muted goal-fin-when">${esc(kpiDay(goalFinishedAt(g)))}${g.status_by_id ? ' · ' + esc(goalActorName(g.status_by_id)) : ''}</span></button>
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
function goalFormHtml(g, goals, {owner = '', company = false, fixed = false, sheet = false} = {}) {
  company = company || g.owner_id === GOAL_COMPANY || (sheet && !g.id && owner === GOAL_COMPANY);   // a team goal supports nothing
  const may = !g.id || goalMayEdit(g, goals);
  const me = mePerson(), mine = me ? 'human:' + me.id : '';
  // The sheet always names the owner: a select while they may hand the goal to someone else, else just its one name.
  const choices = sheet ? (g.id && !goalMayMove(g, goals) ? [[g.owner_id, goalOwnerInfo(g.owner_id).name]] : goalSheetOwners(g.owner_id || owner))
    : g.id || company || fixed ? [] : goalOwnerChoices(mine);   // fixed: the owner is whoever was tapped
  const who = g.owner_id || owner || mine || choices[0]?.[0] || '';
  const kids = g.id ? goals.filter(x => goalLive(x) && x.parent_id === g.id).sort(goalRank) : [];
  const ownerSel = `<label class="goal-f"><span>Owner</span><select name="owner"${sheet && choices.length < 2 ? ' disabled' : ''}>${choices.map(([v, name]) => `<option value="${esc(v)}"${v === who ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></label>`;
  const supports = (off, whose, hide = false) => `<label class="goal-f"${hide ? ' hidden' : ''}><span>Supports</span><select name="parent"${off ? ' disabled' : ''}>${goalSupportOptions(g, whose, goals)}</select></label>`;
  return `<form class="goal-form goal-ed${sheet ? ' goal-sheet-f' : ''}" data-goal-form="${esc(g.id || (company && !sheet ? 'company' : 'new'))}" novalidate>
      <label class="goal-f"><span>Goal</span><input type="text" name="title" maxlength="300" autocomplete="off" value="${esc(g.title || '')}"${may ? '' : ' readonly'} placeholder="${company ? 'Reach 100 paying customers by June' : 'Answer every ticket the same day'}"></label>
      ${sheet ? `<div class="goal-fs">${ownerSel}${supports(!may || company, who, company)}</div>`
      : choices.length ? `<div class="goal-fs">${ownerSel}${supports(false, who)}</div>`
      : company ? '' : supports(!may, g.owner_id || who)}
      ${goalBetHtml(g, may)}
      ${sheet ? goalMeasureHtml(g, may) : ''}
      ${kids.length ? `<div class="goal-ed-kids"><span>Supported by</span>${kids.map(k =>
        `<a href="${GOALS}/${encodeURIComponent(k.id)}" data-goal-go="${esc(k.id)}">${esc(k.title)} <span class="muted">· ${esc(goalOwnerInfo(k.owner_id).name)}</span></a>`).join('')}</div>` : ''}
      ${may && g.id ? goalColourHtml(g) : ''}
      <div class="goal-actions">
        ${may ? '<button class="primary" type="submit">Save</button>' : ''}<button class="ghost" type="button" data-goal-cancel>${may ? 'Cancel' : 'Close'}</button>
        <span class="spacer"></span>${may && g.id ? '<button class="ghost danger" type="button" data-goal-remove>Remove</button>' : ''}
      </div>
    </form>`;
}
// The goal's measure in the sheet: its first KPI, the number it should reach and the date it is due (an "improve"
// target on the link, backend/kpis.py validate_target); a goal with none names a new one. A range is set in Details.
const goalMeasure = g => (g.kpis || []).find(k => !k.archived_at) || null;
function goalMeasureHtml(g, may) {
  const k = goalMeasure(g), link = k?.link || {}, range = link.kind === 'maintain', off = !may || range ? ' disabled' : '';
  const n = v => v == null ? '' : String(v);
  return `<div class="goal-fs goal-measure">
      <label class="goal-f goal-metric"><span>Metric</span><input type="text" name="metric" maxlength="300" autocomplete="off" value="${esc(k?.name || '')}"${k || !may ? ' readonly' : ''} placeholder="Paying customers"></label>
      <label class="goal-f"><span>Target</span><input type="number" name="target" step="any" inputmode="decimal" value="${esc(range ? '' : n(link.target))}"${off}${range ? ' placeholder="Range"' : ''}></label>
      <label class="goal-f"><span>Due</span><input type="date" name="due" value="${esc(range ? '' : link.deadline || '')}"${off}></label></div>`;
}
// What saving the measure posts, or the field to fill first: a target and its date go together, and a new one needs a name.
function goalMeasurePlan(form, g) {
  if (!form.elements.metric) return {};
  const k = g.id ? goalMeasure(g) : null, link = k?.link || {};
  const name = form.elements.metric.value.trim(), target = form.elements.target.value.trim(), due = form.elements.due_at.value;
  if (form.elements.target.disabled) return {};
  if (Boolean(target) !== Boolean(due)) return {missing: target ? form.elements.due_at : form.elements.target};
  const aim = target ? {kind: 'improve', target: Number(target), deadline: due} : {kind: 'none'};
  if (k) {
    if (n2(link.target) === (target ? Number(target) : null) && (link.deadline || '') === due) return {};
    if (target && link.baseline != null) Object.assign(aim, {baseline: link.baseline, baseline_at: link.baseline_at});
    return {post: gid => post(`/v2/goals/${encodeURIComponent(gid)}/kpis/${encodeURIComponent(k.id)}`, aim)};
  }
  if (!name) return target ? {missing: form.elements.metric} : {};
  return {post: gid => post(`/v2/goals/${encodeURIComponent(gid)}/kpis`, {name, ...aim})};
}
const n2 = v => v == null || v === '' ? null : Number(v);
function bindGoalForm(form, g, goals, {company = false, owner = ''} = {}, done) {
  const title = form.elements.title, ownerSel = form.elements.owner_id, parentSel = form.elements.parent, betSel = form.elements.bet;
  const save = form.querySelector('[type=submit]');
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  form.querySelector('[data-goal-cancel]').onclick = () => done();
  if (ownerSel) ownerSel.onchange = () => {
    parentSel.innerHTML = goalSupportOptions(g, ownerSel.value, goals);
    if (!form.classList.contains('goal-sheet-f')) return;
    parentSel.disabled = ownerSel.value === GOAL_COMPANY || Boolean(g.id && !goalMayEdit(g, goals));
    parentSel.closest('label').hidden = ownerSel.value === GOAL_COMPANY;
  };
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
    const measure = goalMeasurePlan(form, g);
    if (measure.missing) { measure.missing.focus(); return; }
    save.disabled = true;
    try {
      if (colour) await post('/v2/goals/' + encodeURIComponent(g.id) + '/status', {status: colour, note: why});
      let gid = g.id;
      if (g.id) {
        const body = {};
        if (words !== g.title) body.title = words;
        if (ownerSel && !ownerSel.disabled && ownerSel.value !== g.owner_id) body.owner_id = ownerSel.value;
        if (parentSel && !parentSel.disabled && parentSel.value !== (g.parent_id || '')) body.parent_id = parentSel.value;
        else if (body.owner_id === GOAL_COMPANY && g.parent_id) body.parent_id = '';   // a team goal supports nothing
        if (betSel && betSel.value !== (g.bet_id || '')) body.bet_id = betSel.value;   // "" unlinks it
        if (Object.keys(body).length) await post('/v2/goals/' + encodeURIComponent(g.id), body);
      } else {
        const body = {title: words, owner: company && !ownerSel ? GOAL_COMPANY : ownerSel ? ownerSel.value : owner || 'me'};
        if (parentSel?.value && !parentSel.disabled) body.parent_id = parentSel.value;
        if (betSel?.value) body.bet_id = betSel.value;
        gid = (await post('/v2/goals', body))?.goal?.id;
      }
      if (measure.post && gid) await measure.post(gid);
      await goalsRefresh();
      done(true);
    } catch (e) { toast(e.message || 'Could not save the goal', true); save.disabled = false; }
  };
  title.focus();
}
function pageGoals() {
  const base = goalsBase(), open = S.route.startsWith(base + '/') ? decodeURIComponent(S.route.slice(base.length + 1)) : '';
  const was = document.getElementById('goal-panel');
  if (was) { was._state = null; if (was.open) was.close(); }
  const sheet = document.getElementById('goal-sheet'); if (sheet?.open) sheet.close();
  const state = GOALS_ST = {goals: [], other: [], proposals: [], needs: [], owners: {}, groups: [], loaded: false, open, panel: null, strategy: null};
  // Goals is a Strategy tab: the tree is the page and "+ Goal" heads it. Talking about goals is the Ask panel's.
  $('#main').classList.add('strategy-layout', 'rd-goals');
  $('#main').innerHTML = strategyHeadHtml('goals') + `<div class="goals-main"><div id="goal-body"><p class="muted">Loading…</p></div>
    <section class="card gkv" id="goal-kpis" aria-label="KPIs" hidden></section></div>`;
  const actions = $('#strat-actions');
  if (actions && (mePerson() || S.me?.role === 'owner')) {
    actions.innerHTML = '<button class="primary" type="button" id="goal-new">+ Goal</button>';
    actions.querySelector('#goal-new').onclick = () => { if (state.loaded) goalSheetOpen(state); };
  }
  goalsLoad(state);
  goalsKpiViews($('#goal-kpis'));   // Tree · Loops · Journey (ui/app/goals-kpi-views.js)
}
function goalsApply(state, data, needs) {
  state.goals = (data.goals || []).filter(g => !goalBuiltInOwner(g.owner_id));
  state.other = (data.other_kpis || []).filter(k => !goalBuiltInOwner(k.owner_id));
  state.proposals = data.proposals || [];
  state.owners = data.owners || {};
  if (needs !== undefined) state.needs = (needs?.items || []).filter(n => !goalBuiltInOwner(n.goal_owner) && !goalBuiltInOwner(n.owner_id) && !(data.goals || []).some(g => g.id === (n.goal_id || n.proposal?.goal_id) && goalBuiltInOwner(g.owner_id)));
}
// Two requests: the tree and what needs you. The roster (people, bots, departments) is already in S.
// The strategy comes with them, for the bets a goal may serve, and the groups, for their leads (a group's "+"). The
// goals never wait on either: if one cannot be read, the picker or the group's "+" is left out.
const goalStrategyGet = () => v2Get('/v2/strategy').catch(() => null);
async function goalsLoad(state) {
  let data, needs, strategy, groups;
  try { [data, needs, strategy, groups] = await Promise.all([get('/v2/goals/tree'), v2Get('/v2/goals/needs-you'), goalStrategyGet(), get('/v2/groups').catch(() => null)]); }
  catch (e) { const body = GOALS_ST === state && $('#goal-body'); if (body) body.innerHTML = `<p class="err">${esc(e.message || 'Could not load goals')}</p>`; return; }
  // Left Goals before the tree came: opening a goal now would cover the next page and rewrite its address.
  if (GOALS_ST !== state || !$('#goal-body')) return;
  state.strategy = Array.isArray(strategy?.bets) ? strategy : null;
  state.groups = Array.isArray(groups) ? groups : groups?.groups || [];
  goalsApply(state, data, needs);
  state.loaded = true;
  goalsRender(state);
  const g = state.open && state.goals.find(x => x.id === state.open);
  if (g) goalPanelOpen(state, g.owner_id, {goal: g.id});
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
function openGoal(id) { location.hash = goalsBase() + '/' + encodeURIComponent(id); }
async function goalsRefresh() {
  GOAL_OPTIONS = null;
  if (GOALS_ST && $('#goal-body')) {
    const st = GOALS_ST;
    const [r, needs, strategy] = await Promise.all([v2Get('/v2/goals/tree'), v2Get('/v2/goals/needs-you'), goalStrategyGet()]);
    if (r && st === GOALS_ST) { goalsApply(st, r, needs); if (Array.isArray(strategy?.bets)) st.strategy = strategy; }
  }
  headGoalsReload();
}

// The roster can change while Goals is open (`refresh` calls this): the tree is drawn again when what it is drawn from
// changed, keeping a field being typed in.
function goalsFollowRoster() {
  const state = GOALS_ST, body = $('#goal-body');
  if (!state || !body) return;
  const typing = document.activeElement?.matches?.('input, textarea, select') && body?.contains(document.activeElement);
  if (state.loaded && !typing && state.shape !== goalTreeKey()) goalsRender(state);
}
