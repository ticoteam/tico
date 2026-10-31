/* ui/app/team-build.js — Build a team: a briefing suggests the roles a team needs (POST /v2/team-build/suggest) and the
   picks are hired into that team in one step (POST /v2/team-build/hire). teamBuildOpen(group) opens it. The server
   decides every role; this only shows them and sends the picks back. */
'use strict';

const TB_DEBOUNCE = 350;       // ms after the last keystroke before the briefing goes to the server
const TB_HOLD = 1600;          // ms the header dot stays done before it settles back to idle
let TEAM_BUILD = null;         // the open dialog: {dialog, team, teamName, rows, seq, busy, timer, hold, dot, opener}

function teamBuildOpen(group) {
  const team = String(group || '');
  if (!team || !S.me?.id || !settingsCanCreateBots()) return;
  if (TEAM_BUILD) TEAM_BUILD.dialog.close('next');
  const teamName = (S.orgGroups || []).find(g => g.id === team)?.name || team;
  const dialog = document.createElement('dialog');
  dialog.id = 'team-build-dialog';
  dialog.className = 'tb-dialog';
  dialog.tabIndex = -1;
  dialog.setAttribute('aria-labelledby', 'team-build-title');
  dialog.innerHTML = `<div class="tb-head">${ldotHTML({state: 'idle', size: 22, cls: 'tb-dot'})}
    <h2 id="team-build-title">Build a team</h2><span class="tb-team">${esc(teamName)}</span><span class="tb-spacer"></span>
    <button class="ghost" type="button" data-tb-close aria-label="Close">✕</button></div>
    <div class="tb-body">
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
    if (request) return tbRequest(state.rows[Number(request.dataset.tbRequest)].pick);
    if (t.closest('.tb-hire')) void tbHire(state);
  });
  dialog.addEventListener('change', event => {
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
  // Desktop: the brief is where you start. Phone: the dialog takes focus, so the keyboard stays down until a tap.
  if (matchMedia('(max-width: 600px)').matches) dialog.focus();
  else dialog.querySelector('.tb-brief input').focus();
  void tbSuggest(state);
}

// The newest request wins: an answer that comes back after a newer one was sent, or after the dialog closed, is dropped.
async function tbSuggest(state) {
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

// A new briefing keeps what the person chose for a role: its checkbox, and its pick when that template still stands for
// the role (the pick and the alternatives swap back). A role no longer suggested is dropped.
function tbRows(roles, prev) {
  const before = new Map(prev.map(row => [row.role, row]));
  return roles.map(role => {
    const old = before.get(role.role), cloud = role.pick.source === 'cloud';
    const pool = [role.pick, ...role.alternatives];
    const kept = old?.pick.template && pool.find(item => item.template === old.pick.template);
    const pick = kept || role.pick;
    return {role: role.role, pick, cloud, on_team: role.on_team, bot: role.bot ?? null,
      alternatives: kept && kept !== role.pick ? pool.filter(item => item !== kept) : role.alternatives,
      checked: role.on_team || cloud ? false : old ? old.checked : role.checked,
      open: !!old?.open && !role.on_team};
  });
}

function tbGrid(state) {
  const grid = state.dialog.querySelector('[data-tb-grid]');
  grid.innerHTML = state.rows.map((row, i) => tbCard(row, i, false)).join('');
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
  const action = team ? '<span class="tb-state">On the team</span>'
    : cloud ? `<button class="tb-link" type="button" data-tb-request="${i}">Request</button>`
    : row.alternatives.length ? `<button class="tb-link" type="button" data-tb-change="${i}" aria-expanded="${row.open}" aria-controls="tb-alts-${i}">Change</button>`
    : '';
  const alts = team || cloud || !row.alternatives.length ? '' : `<ul class="tb-alts" id="tb-alts-${i}"${row.open ? '' : ' hidden'}>${row.alternatives.map((alt, j) =>
    `<li><button class="tb-alt" type="button" data-tb-alt="${i}:${j}">${botAvatar({slug: alt.template || alt.name, icon: alt.icon}, 22)}`
    + `<span class="tb-alt-name">${esc(alt.name)}</span>${tbMatch(alt)}</button></li>`).join('')}</ul>`;
  return `<article class="tb-card${team ? ' is-team' : ''}${still ? ' tb-still' : ''}" data-card="${i}" style="--i:${i}">
    <div class="tb-top"><span class="tb-role" title="${esc(role)}">${esc(role)}</span>
      <input class="tb-check" type="checkbox" data-tb-check="${i}" aria-label="Include ${esc(role)}"${row.checked ? ' checked' : ''}${team || cloud ? ' disabled' : ''}></div>
    <div class="tb-pick">${onBot ? botAvatar({slug: bot.slug, icon: bot.icon}, 30) : botAvatar({slug: pick.template || pick.name, icon: pick.icon}, 30)}
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

// Swaps a role's pick for one of its alternatives; the old pick takes that alternative's place and the role is picked.
function tbPick(state, i, j) {
  const row = state.rows[i], alt = row.alternatives[j];
  if (!alt) return;
  row.alternatives = row.alternatives.map((item, k) => k === j ? row.pick : item);
  row.pick = alt; row.checked = true; row.open = false;
  const card = state.dialog.querySelector(`[data-card="${i}"]`), next = document.createElement('template');
  next.innerHTML = tbCard(row, i, true).trim();
  card.replaceWith(next.content.firstElementChild);
  tbFoot(state);
  state.dialog.querySelector(`[data-card="${i}"] [data-tb-change]`)?.focus();
}

// A cloud agent is requested from the Cloud agents page. Until that page can take the request here, the toast says where.
function tbRequest(pick) {
  if (typeof window.cloudAgentRequest === 'function') return window.cloudAgentRequest(pick);
  toast(`Request ${pick.name} from Cloud agents.`, false, null, {host: TEAM_BUILD?.dialog});
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
