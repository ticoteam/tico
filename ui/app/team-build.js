/* ui/app/team-build.js — Build a team: a briefing suggests the roles a team needs (POST /v2/team-build/suggest) and the
   picks are hired into that team in one step (POST /v2/team-build/hire). teamBuildOpen(group) opens it. The server
   decides every role; this only shows them and sends the picks back. */
'use strict';

const TB_DEBOUNCE = 350;       // ms after the last keystroke before the briefing goes to the server
const TB_HOLD = 1600;          // ms the header dot stays done before it settles back to idle
let TEAM_BUILD = null;         // the open dialog: {dialog, team, teamName, rows, seq, busy, timer, hold, dot, opener}

// With no team (Team > + > Build a team) the dialog asks which team first, then suggests for it.
function teamBuildOpen(group) {
  const team = String(group || '');
  if (!S.me?.id || !settingsCanCreateBots()) return;
  if (TEAM_BUILD) TEAM_BUILD.dialog.close('next');
  const teams = S.orgGroups || [];
  const teamName = teams.find(g => g.id === team)?.name || team;
  const picker = team ? '' : `<label class="tb-team-pick"><span class="tb-lbl">Team</span>
      <select data-tb-team><option value="">Choose a team</option>${teams.map(g => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('')}</select></label>`;
  const dialog = document.createElement('dialog');
  dialog.id = 'team-build-dialog';
  dialog.className = 'tb-dialog';
  dialog.tabIndex = -1;
  dialog.setAttribute('aria-labelledby', 'team-build-title');
  dialog.innerHTML = `<div class="tb-head">${ldotHTML({state: 'idle', size: 22, cls: 'tb-dot'})}
    <h2 id="team-build-title">Build a team</h2><span class="tb-team"${team ? '' : ' hidden'}>${esc(teamName)}</span><span class="tb-spacer"></span>
    <button class="ghost" type="button" data-tb-close aria-label="Close">✕</button></div>
    <div class="tb-body">
      ${picker}
      <label class="tb-brief"><span class="tb-lbl">What are you building?</span>
        <input type="text" maxlength="500" autocomplete="off" spellcheck="false"></label>
      <div class="tb-grid" data-tb-grid aria-busy="true"></div>
      <p class="tb-none muted" hidden>Nothing to suggest for this team.</p>
    </div>
    <div class="tb-foot"><span class="tb-count muted" aria-live="polite">0 picked</span>
      <button class="primary tb-hire" type="button" disabled>Hire 0</button></div>`;
  const opener = document.activeElement;
  document.body.append(dialog);
  const state = TEAM_BUILD = {dialog, team, teamName, rows: [], seq: 0, busy: false, timer: 0, hold: 0,
    dot: dialog.querySelector('.tb-dot'), opener};
  const leave = () => dialog.close('next');
  window.addEventListener('hashchange', leave);
  dialog.addEventListener('close', () => {
    window.removeEventListener('hashchange', leave);
    clearTimeout(state.timer); clearTimeout(state.hold);
    dialog.remove();
    if (TEAM_BUILD === state) TEAM_BUILD = null;
    if (state.opener?.isConnected) state.opener.focus();
  });
  dialog.addEventListener('click', event => {
    const t = event.target;
    if (t.closest('[data-tb-close]')) return dialog.close();
    const change = t.closest('[data-tb-change]');
    if (change) return tbToggle(state, Number(change.dataset.tbChange), change);
    const alt = t.closest('[data-tb-alt]');
    if (alt) { const [i, j] = alt.dataset.tbAlt.split(':').map(Number); return tbPick(state, i, j); }
    const request = t.closest('[data-tb-request]');
    if (request) return tbRequest(state, state.rows[Number(request.dataset.tbRequest)].pick);
    if (t.closest('.tb-hire')) void tbHire(state);
  });
  dialog.addEventListener('change', event => {
    const choice = event.target.closest?.('[data-tb-team]');
    if (choice) return tbChooseTeam(state, choice.value);
    const box = event.target.closest?.('[data-tb-check]');
    if (!box) return;
    state.rows[Number(box.dataset.tbCheck)].checked = box.checked;
    tbFoot(state);
  });
  dialog.addEventListener('input', event => {
    if (!event.target.matches('.tb-brief input')) return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => void tbSuggest(state), TB_DEBOUNCE);
  });
  // Any click ends the motion where it is: the cards that arrive after it show their final state.
  dialog.addEventListener('pointerdown', () => dialog.classList.add('tb-still'));
  dialog.showModal();
  // Desktop: the brief is where you start, or the team picker when there is no team yet. Phone: the dialog takes focus,
  // so the keyboard stays down until a tap.
  if (matchMedia('(max-width: 600px)').matches) dialog.focus();
  else (team ? dialog.querySelector('.tb-brief input') : dialog.querySelector('[data-tb-team]')).focus();
  if (team) void tbSuggest(state);
}

// The newest request wins: an answer that comes back after a newer one was sent, or after the dialog closed, is dropped.
async function tbSuggest(state) {
  if (!state.team) return;
  const seq = ++state.seq;
  const briefing = state.dialog.querySelector('.tb-brief input').value.trim();
  clearTimeout(state.hold);
  ldotSet(state.dot, 'thinking');
  let found;
  try { found = await post('/v2/team-build/suggest', {team: state.team, briefing}); }
  catch (error) {
    if (TEAM_BUILD !== state || seq !== state.seq) return;
    ldotSet(state.dot, 'idle');
    state.dialog.querySelector('[data-tb-grid]').setAttribute('aria-busy', 'false');
    toast(error.message, true, null, {host: state.dialog});
    return;
  }
  if (TEAM_BUILD !== state || seq !== state.seq) return;
  state.rows = tbRows(found.roles || [], state.rows);
  tbGrid(state);
  ldotSet(state.dot, 'done');
  state.hold = setTimeout(() => { if (state.dot.dataset.state === 'done') ldotSet(state.dot, 'idle'); }, TB_HOLD);
}

// What a candidate is, across roles: a template, or a cloud agent's id (its template is "", so two agents never match each
// other or a template).
const tbKey = item => item.source === 'cloud' ? `cloud:${item.id}` : item.template;

// A new briefing keeps what the person chose for a role: its checkbox, and its pick when that candidate still stands for
// the role (the pick and the offers swap back). A role no longer suggested is dropped. `offers` is what the role can take
// instead; `alternatives` is what it shows, from tbLayout.
function tbRows(roles, prev) {
  const before = new Map(prev.map(row => [row.role, row]));
  return tbUnique(roles.map(role => {
    const old = before.get(role.role), cloud = role.pick.source === 'cloud';
    const pool = [role.pick, ...role.alternatives];
    const kept = old && tbKey(old.pick) && pool.find(item => tbKey(item) === tbKey(old.pick));
    const pick = kept || role.pick;
    return {role: role.role, pick, cloud, on_team: role.on_team, bot: role.bot ?? null,
      offers: kept && kept !== role.pick ? pool.filter(item => item !== kept) : role.alternatives,
      alternatives: [],
      checked: role.on_team || cloud ? false : old ? old.checked : role.checked,
      open: !!old?.open && !role.on_team};
  }));
}

// A role's alternatives are its offers less what the other roles pick: one template is never two roles' picks.
function tbLayout(state) {
  state.rows.forEach((row, i) => {
    const taken = new Set(state.rows.filter((_, k) => k !== i).map(other => tbKey(other.pick)));
    row.alternatives = row.on_team ? [] : row.offers.filter(item => !taken.has(tbKey(item)));
  });
}

// A new briefing can suggest a template a role before it already holds: that role takes its first free offer, or waits
// unchecked when it has none, so the hire never sends one template twice.
function tbUnique(rows) {
  const held = new Set();
  for (const row of rows) {
    if (!row.cloud && held.has(row.pick.template)) {
      const free = row.offers.find(item => !held.has(item.template));
      if (free) { row.offers = row.offers.map(item => item === free ? row.pick : item); row.pick = free; }
      else row.checked = false;
    }
    if (!row.cloud) held.add(row.pick.template);
  }
  return rows;
}

// Every card is drawn from the state, so a role's alternatives always leave out the templates the other roles pick.
function tbGrid(state, still = false) {
  tbLayout(state);
  const grid = state.dialog.querySelector('[data-tb-grid]');
  grid.innerHTML = state.rows.map((row, i) => tbCard(row, i, still)).join('');
  grid.setAttribute('aria-busy', 'false');
  state.dialog.querySelector('.tb-none').hidden = state.rows.length > 0;
  tbFoot(state);
}

const tbMatch = item => item.match === 'strong' ? '<span class="tb-match">Strong</span>'
  : item.match === 'good' ? '<span class="tb-match">Good</span>' : '';

// A role card. `still` marks a card that replaces one already on screen, so it does not fade in again.
function tbCard(row, i, still) {
  const {pick, role, cloud, bot} = row, team = row.on_team;
  // On the team, the card names the bot that fills the role, with that bot's avatar.
  const onBot = team && bot;
  const change = row.alternatives.length ? `<button class="tb-link" type="button" data-tb-change="${i}" aria-expanded="${row.open}" aria-controls="tb-alts-${i}">Change</button>` : '';
  // A cloud agent Tico connects is added as a bot; any other is requested. Both open the Cloud agents dialog.
  const action = team ? '<span class="tb-state">On the team</span>'
    : cloud ? `<button class="tb-link" type="button" data-tb-request="${i}">${pick.supported ? 'Add' : 'Request'}</button>${change}`
    : change;
  const alts = team || !row.alternatives.length ? '' : `<ul class="tb-alts" id="tb-alts-${i}"${row.open ? '' : ' hidden'}>${row.alternatives.map((alt, j) =>
    `<li><button class="tb-alt" type="button" data-tb-alt="${i}:${j}">${botAvatar({slug: alt.template || alt.name, icon: alt.icon}, 22)}`
    + `<span class="tb-alt-name">${esc(alt.name)}</span>${tbMatch(alt)}</button></li>`).join('')}</ul>`;
  return `<article class="tb-card${team ? ' is-team' : ''}${still ? ' tb-still' : ''}" data-card="${i}" style="--i:${i}">
    <div class="tb-top"><span class="tb-role" title="${esc(role)}">${esc(role)}</span>
      <input class="tb-check" type="checkbox" data-tb-check="${i}" aria-label="Include ${esc(role)}"${row.checked ? ' checked' : ''}${team || cloud ? ' disabled' : ''}></div>
    <div class="tb-pick">${onBot ? botAvatar({id: bot.id, slug: bot.slug, icon: bot.icon}, 30) : botAvatar({slug: pick.template || pick.name, icon: pick.icon}, 30)}
      <div class="tb-pick-text"><span class="tb-pick-name">${esc(onBot ? bot.name : pick.name)}</span>${cloud ? '<span class="tb-pick-sub">Cloud agent</span>' : ''}</div></div>
    <div class="tb-card-foot">${action}</div>${alts}</article>`;
}

function tbFoot(state) {
  const n = state.rows.filter(row => row.checked && !row.on_team && !row.cloud).length;
  state.dialog.querySelector('.tb-count').textContent = `${n} picked`;
  const hire = state.dialog.querySelector('.tb-hire');
  hire.textContent = `Hire ${n}`;
  hire.disabled = !n || state.busy;
}

function tbToggle(state, i, button) {
  const row = state.rows[i];
  row.open = !row.open;
  button.setAttribute('aria-expanded', String(row.open));
  const list = state.dialog.querySelector(`#tb-alts-${i}`);
  if (list) list.hidden = !row.open;
}

// Swaps a role's pick for one of its alternatives: the old pick takes that alternative's place and the role is picked
// (a cloud agent is never hired here, so it stays unchecked). Every card is drawn again, still, so the other roles stop
// offering the template that is now picked.
function tbPick(state, i, j) {
  const row = state.rows[i], alt = row.alternatives[j];
  if (!alt) return;
  row.offers = row.offers.map(item => item === alt ? row.pick : item);
  row.pick = alt; row.checked = !row.cloud; row.open = false;
  tbGrid(state, true);
  state.dialog.querySelector(`[data-card="${i}"] [data-tb-change]`)?.focus();
}

// The team picked in the dialog that opened without one: the suggestions are for that team, from its first answer.
function tbChooseTeam(state, team) {
  if (!team) return;
  state.team = team;
  state.teamName = (S.orgGroups || []).find(g => g.id === team)?.name || team;
  state.rows = [];
  const head = state.dialog.querySelector('.tb-team');
  head.textContent = state.teamName;
  head.hidden = false;
  state.dialog.querySelector('[data-tb-grid]').innerHTML = '';
  void tbSuggest(state);
}

// A cloud agent is added or requested from the Cloud agents page, into this team: the bot or seat lands here, not at the
// top of the chart. Until that page is there, the toast says where to go.
function tbRequest(state, pick) {
  if (typeof window.cloudAgentRequest === 'function') return window.cloudAgentRequest(pick, {team: state.team});
  toast(`${pick.supported ? 'Add' : 'Request'} ${pick.name} from Cloud agents.`, false, null, {host: TEAM_BUILD?.dialog});
}

async function tbHire(state) {
  if (state.busy) return;
  const picks = state.rows.filter(row => row.checked && !row.on_team && !row.cloud)
    .map(row => ({role: row.role, template: row.pick.template}));
  if (!picks.length) return;
  state.busy = true; tbFoot(state); ldotSet(state.dot, 'thinking');
  try {
    const hired = await post('/v2/team-build/hire', {team: state.team, reports_to: '', picks});
    toast(`${hired.bots?.length ?? picks.length} joined ${state.teamName}.`, false);
    void refresh(true);
    state.dialog.close('next');
  } catch (error) {
    if (TEAM_BUILD !== state) return;
    state.busy = false; tbFoot(state); ldotSet(state.dot, 'idle');
    toast(error.message, true, null, {host: state.dialog});
  }
}
