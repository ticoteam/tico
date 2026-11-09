/* ui/app/settings-tasks.ts — Settings > Tasks: what bots may put on you
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';
// What bots may put on you (backend/ask_ingest.py): your own Off or On, how many open items you take before
// the check tightens and your criteria; an owner or admin also sets the team default everyone starts
// from. A change applies to the next item; nothing already on a plate moves.
const ASK_FILTER_MODES: [string, string, string][] = [['off', 'Off', 'Bots put things on you as before.'],
  ['on', 'On', 'Asks that don’t meet your criteria are refused.']];
// One set of rules: Off or On, the open items before the check tightens, and the criteria (up to 10 lines).
interface SettingsAskRules { mode: string; budget: number; criteria: string[]; own?: boolean; }
// GET /v2/ask-ingest/settings: the person's own rules, the team default, and whether this person may change the default.
interface SettingsAskServer { settings?: SettingsAskRules | null; team?: SettingsAskRules; may_edit_team?: boolean; }
// The loaded answer, with the page's own choice of whose rules it shows: 'mine', or 'team' for an owner or admin.
interface SettingsAskState extends SettingsAskServer {
  settings: SettingsAskRules;
  editing: string | undefined;
}
let ASK_FILTER: SettingsAskState | null = null;
async function askFilterSettingsLoad(): Promise<void> {
  const el = document.querySelector<HTMLElement>('#set-ask-filter'); if (!el) return;
  const r: SettingsAskServer | null = await v2Get('/v2/ask-ingest/settings');
  // Settings may have been left while this was read.
  if (!r?.settings) { const card = document.querySelector<HTMLElement>('#set-ask-filter-card'); if (card) card.hidden = true; return; }
  ASK_FILTER = {...r, settings: r.settings, editing: ASK_FILTER?.editing || 'mine'};
  askFilterSettingsPaint();
}
function askFilterSettingsPaint(): void {
  const el = document.querySelector<HTMLElement>('#set-ask-filter'); if (!el || !ASK_FILTER) return;
  const team = ASK_FILTER.editing === 'team', v = team && ASK_FILTER.team ? ASK_FILTER.team : ASK_FILTER.settings;
  const mine = ASK_FILTER.settings;
  el.innerHTML = `<form class="ask-filter">
    ${ASK_FILTER.may_edit_team ? `<div class="theme-seg" role="radiogroup" aria-label="Whose settings">
      <button type="button" role="radio" aria-checked="${!team}" data-ask-filter-edit="mine">Yours</button>
      <button type="button" role="radio" aria-checked="${team}" data-ask-filter-edit="team">Team default</button></div>` : ''}
    <p class="settings-cell-note">${team ? 'Everyone without their own settings uses these.'
      : mine.own ? 'Your own settings.' : 'You use the team default until you save your own.'}</p>
    <fieldset class="ask-filter-modes"><legend>Mode</legend>${ASK_FILTER_MODES.map(([k, label, help]) =>
      `<label><input type="radio" name="mode" value="${k}"${v.mode === k ? ' checked' : ''}> <b>${esc(label)}</b> <span class="muted">${esc(help)}</span></label>`).join('')}</fieldset>
    <label>Open items before it tightens <input type="number" name="budget" min="1" max="200" value="${esc(v.budget)}" required></label>
    <label>What you want to be asked about, one per line (up to 10)
      <textarea name="criteria" rows="6">${esc((v.criteria || []).join('\n'))}</textarea></label>
    <div class="row"><button class="primary" type="submit">Save</button>
      ${!team && mine.own ? '<button type="button" class="ghost" data-ask-filter-reset>Use the team default</button>' : ''}
      <span class="muted" data-ask-filter-msg></span></div></form>
    ${team ? '' : `<form class="ask-filter-replay" aria-label="Replay a day">
      <p class="settings-cell-note">Before you turn it On: see what On would have done with one day of bots’ items, with these settings. Nothing moves.</p>
      <div class="row"><input type="date" name="day" required value="${esc(new Date().toISOString().slice(0, 10))}" aria-label="Day">
        <button type="submit" class="ghost">Replay a day</button><span class="muted" data-ask-replay-msg></span></div>
      <div class="ask-filter-replay-rows" role="list"></div></form>`}`;
  // The form, its message line and the mode buttons are drawn just above, so each lookup is present.
  const form = el.querySelector<HTMLFormElement>('form.ask-filter')!;
  el.querySelectorAll<HTMLElement>('[data-ask-filter-edit]').forEach(b => {
    b.onclick = () => { if (ASK_FILTER) ASK_FILTER.editing = b.dataset.askFilterEdit; askFilterSettingsPaint(); };
  });
  const msg = el.querySelector<HTMLElement>('[data-ask-filter-msg]')!;
  const reset = el.querySelector<HTMLButtonElement>('[data-ask-filter-reset]');
  if (reset) reset.onclick = async () => {
    try { await writeRequest('DELETE', '/v2/ask-ingest/settings', undefined); toast('Using the team default'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; }
  };
  const replayForm = el.querySelector<HTMLFormElement>('.ask-filter-replay');
  if (replayForm) replayForm.onsubmit = ev => { ev.preventDefault(); void askFilterReplay(replayForm); };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = new FormData(form);
    const body = {mode: f.get('mode'), budget: Number(f.get('budget')),
      criteria: String(f.get('criteria') || '').split('\n').map(s => s.trim()).filter(Boolean), team};
    msg.textContent = 'Saving…';
    try { await put('/v2/ask-ingest/settings', body); toast('Saved; the next item uses it'); await askFilterSettingsLoad(); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; }
  };
}
const ASK_REPLAY_WHY: Record<string, string> = {
  status: 'A progress note: post it as an update', covered: 'Same as one you already have open',
  unclear: 'Doesn’t say what to decide', own_work: 'Work a bot can do itself',
  criteria: 'Over your budget and meets none of your lines',
  recommends: 'Needs one of your criteria and a recommendation',
  urgent_only: 'Your plate is full and it isn’t urgent',
};
const ASK_REPLAY_WOULD: Record<string, string> = {through: 'Reaches you', fyi: 'Refused: post an update', sent_back: 'Refused', merged: 'Merged', failed_open: 'Reaches you (no answer)'};
// One item the replay judged (POST /v2/ask-ingest/replay): who asked, what it was, and what On would have done with it.
interface SettingsAskReplayRow {
  asker?: string;
  asker_name?: string;
  private?: boolean;
  title?: string;
  reason: string;
  load: number;
  would: string;
}
interface SettingsAskReplayPage { items: SettingsAskReplayRow[]; next_offset?: number | null; }
// One day replayed a page at a time (POST /v2/ask-ingest/replay); what would stay off the plate leads.
async function askFilterReplay(form: HTMLFormElement): Promise<void> {
  // The replay's message line, list and button are drawn with the form.
  const msg = form.querySelector<HTMLElement>('[data-ask-replay-msg]')!;
  const list = form.querySelector<HTMLElement>('.ask-filter-replay-rows')!;
  const button = form.querySelector<HTMLButtonElement>('button')!;
  const day = new FormData(form).get('day');
  const rows: SettingsAskReplayRow[] = [];
  button.disabled = true; list.innerHTML = '';
  try {
    let offset: number | null | undefined = 0;
    do {
      msg.textContent = rows.length ? `${rows.length} judged…` : 'Judging…';
      const page: SettingsAskReplayPage = await post('/v2/ask-ingest/replay', {person: myActor(), date: day, offset});
      rows.push(...page.items);
      offset = page.next_offset;
    } while (offset != null);
    const off = rows.filter(r => ['sent_back', 'fyi', 'merged'].includes(r.would));
    msg.textContent = `${rows.length} items; On would have kept ${off.length} off your plate`;
    list.innerHTML = [...off, ...rows.filter(r => !off.includes(r))].map(r => `<div class="ask-replay-row" role="listitem">
      <span class="ask-replay-text"><span class="req-from">${esc(r.asker_name || actorLabel(r.asker))}:</span> ${esc(r.private ? 'A private item' : r.title || 'Untitled')}
        <span class="muted">${esc(ASK_REPLAY_WHY[r.reason] || '')} · ${esc(r.load)} open then</span></span>
      <span class="ask-replay-act">${esc(ASK_REPLAY_WOULD[r.would] || r.would)}</span></div>`).join('');
  } catch (e) { msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; }
  button.disabled = false;
}
