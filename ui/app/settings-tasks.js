/* ui/app/settings-tasks.js — Settings > Tasks: what bots may put on you
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';
// What bots may put on you (backend/ask_ingest.py): your own Off / Rehearsal / On, how many open items you take before
// the filter tightens, the chores budget and your lines; an owner or admin also sets the team default everyone starts
// from. A change applies to the next item; nothing already on a plate moves.
const ASK_FILTER_MODES = [['off', 'Off', 'Bots put things on you as before.'],
  ['rehearsal', 'Rehearsal', 'Nothing changes; Filtered for you lists what On would have kept off.'],
  ['on', 'On', 'Over your budget, bots keep what doesn’t meet your lines; you can let any of it through.']];
let ASK_FILTER = null;
async function askFilterSettingsLoad() {
  const el = $('#set-ask-filter'); if (!el) return;
  const r = await v2Get('/v2/ask-ingest/settings');
  if (!r?.settings) { $('#set-ask-filter-card').hidden = true; return; }
  ASK_FILTER = {...r, editing: ASK_FILTER?.editing || 'mine'};
  askFilterSettingsPaint();
}
function askFilterSettingsPaint() {
  const el = $('#set-ask-filter'); if (!el || !ASK_FILTER) return;
  const team = ASK_FILTER.editing === 'team', v = team ? ASK_FILTER.team : ASK_FILTER.settings;
  const mine = ASK_FILTER.settings;
  el.innerHTML = `<form class="ask-filter">
    ${ASK_FILTER.may_edit_team ? `<div class="theme-seg" role="radiogroup" aria-label="Whose settings">
      <button type="button" role="radio" aria-checked="${!team}" data-ask-filter-edit="mine">Yours</button>
      <button type="button" role="radio" aria-checked="${team}" data-ask-filter-edit="team">Team default</button></div>` : ''}
    <p class="settings-cell-note">${team ? 'Everyone without their own settings uses these.'
      : mine.own ? 'Your own settings.' : 'You use the team default until you save your own.'}</p>
    <fieldset class="ask-filter-modes"><legend>Filter</legend>${ASK_FILTER_MODES.map(([k, label, help]) =>
      `<label><input type="radio" name="mode" value="${k}"${v.mode === k ? ' checked' : ''}> <b>${esc(label)}</b> <span class="muted">${esc(help)}</span></label>`).join('')}</fieldset>
    <label>Open items before it tightens <input type="number" name="budget" min="1" max="200" value="${esc(v.budget)}" required></label>
    <label>Hands-only chores at a time <input type="number" name="chores_budget" min="1" max="100" value="${esc(v.chores_budget)}" required></label>
    <label>What you want to be asked about, one per line (up to 10)
      <textarea name="criteria" rows="6">${esc((v.criteria || []).join('\n'))}</textarea></label>
    <div class="row"><button class="primary" type="submit">Save</button>
      ${!team && mine.own ? '<button type="button" class="ghost" data-ask-filter-reset>Use the team default</button>' : ''}
      <span class="muted" data-ask-filter-msg></span></div></form>
    ${team ? '' : `<form class="ask-filter-replay" aria-label="Replay a day">
      <p class="settings-cell-note">Before you turn it On: see what On would have done with one day of bots\u2019 items, with these settings. Nothing moves.</p>
      <div class="row"><input type="date" name="day" required value="${esc(new Date().toISOString().slice(0, 10))}" aria-label="Day">
        <button type="submit" class="ghost">Replay a day</button><span class="muted" data-ask-replay-msg></span></div>
      <div class="ask-filter-replay-rows" role="list"></div></form>`}`;
  const form = $('form.ask-filter', el);
  el.querySelectorAll('[data-ask-filter-edit]').forEach(b => b.onclick = () => { ASK_FILTER.editing = b.dataset.askFilterEdit; askFilterSettingsPaint(); });
  const msg = $('[data-ask-filter-msg]', el);
  const reset = $('[data-ask-filter-reset]', el);
  if (reset) reset.onclick = async () => {
    try { await writeRequest('DELETE', '/v2/ask-ingest/settings'); toast('Using the team default'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  const replayForm = $('.ask-filter-replay', el);
  if (replayForm) replayForm.onsubmit = ev => { ev.preventDefault(); void askFilterReplay(replayForm); };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = new FormData(form);
    const body = {mode: f.get('mode'), budget: Number(f.get('budget')), chores_budget: Number(f.get('chores_budget')),
      criteria: String(f.get('criteria') || '').split('\n').map(s => s.trim()).filter(Boolean), team};
    msg.textContent = 'Saving…';
    try { await put('/v2/ask-ingest/settings', body); toast('Saved; the next item uses it'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}
const ASK_REPLAY_WOULD = {through: 'Reaches you', hands: 'Hands needed', fyi: 'FYI', sent_back: 'Sent back', merged: 'Merged', failed_open: 'Reaches you (no answer)'};
// One day replayed a page at a time (POST /v2/ask-ingest/replay); what would stay off the plate leads.
async function askFilterReplay(form) {
  const msg = $('[data-ask-replay-msg]', form), list = $('.ask-filter-replay-rows', form), button = $('button', form);
  const day = new FormData(form).get('day');
  const rows = [];
  button.disabled = true; list.innerHTML = '';
  try {
    let offset = 0;
    do {
      msg.textContent = rows.length ? `${rows.length} judged…` : 'Judging…';
      const page = await post('/v2/ask-ingest/replay', {person: myActor(), date: day, offset});
      rows.push(...page.items);
      offset = page.next_offset;
    } while (offset != null);
    const off = rows.filter(r => ['sent_back', 'fyi', 'merged'].includes(r.would));
    msg.textContent = `${rows.length} items; On would have kept ${off.length} off your plate`;
    list.innerHTML = [...off, ...rows.filter(r => !off.includes(r))].map(r => `<div class="tl-filtered-row" role="listitem">
      <span class="tl-filtered-text"><span class="req-from">${esc(r.asker_name || actorLabel(r.asker))}:</span> ${esc(r.private ? 'A private item' : r.title || 'Untitled')}
        <span class="muted">${esc(ASK_FILTER_WHY[r.reason] || '')} · ${esc(r.load)} open then</span></span>
      <span class="tl-filtered-act">${esc(ASK_REPLAY_WOULD[r.would] || r.would)}</span></div>`).join('');
  } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  button.disabled = false;
}
