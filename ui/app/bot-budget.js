/* ui/app/bot-budget.js — Run budgets: a bot's runs per hour and per day, and the team defaults
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A bot's run budget (backend/turn_budgets.py): blank follows the team, 0 is no limit. A bot over it is stopped
// until someone who manages it releases it (botRelease, hub-v2.js) or raises the budget, which releases it too.
// Read for the people who manage the bot; anyone else gets no row.
const BUDGET_KEYS = [['turns_per_hour', 'per hour'], ['turns_per_day', 'per day']];
const budgetNumber = n => Number(n || 0).toLocaleString();
function budgetRowHTML(e) {
  if (e.agent || !settingsCanManageBot(e)) return '';
  return `<div class="sb-row" data-bot-budget-row="${esc(e.name)}" hidden><span>Runs</span><div class="rel-line" data-bot-budget="${esc(e.name)}"></div><span></span></div>
    <div class="sb-sub" data-bot-budget-sub hidden></div>`;
}
function budgetFieldsHTML(slug, b) {
  const field = ([key, label]) => {
    const own = b.own?.[key], team = b.team?.[key];
    return `<label class="budget-field"><input type="number" min="0" max="100000" step="1" class="budget-input" data-budget-key="${key}"
      data-current="${esc(own ?? '')}" value="${esc(own ?? '')}" placeholder="${team ? budgetNumber(team) : 'No limit'}"
      aria-label="Runs ${label} for ${esc(empName(slug))}"><span class="muted">${label}</span></label>`;
  };
  const owned = BUDGET_KEYS.some(([key]) => b.own?.[key] != null);
  return BUDGET_KEYS.map(field).join('') + (owned ? '<button type="button" class="ghost" data-budget-team>Follow team</button>' : '');
}
function budgetSubHTML(slug, b) {
  const held = b.held?.words || b.run_hold?.words;
  const runs = `<span class="tnum" data-budget-runs>${budgetNumber(b.runs?.hour)} this hour · ${budgetNumber(b.runs?.day)} today</span>`;
  return held ? `<span class="sysr-note" data-budget-held>${esc(held)}</span>${botReleaseButton(slug, 'ghost')}` : runs;
}
async function budgetMount(rows, slug) {
  const box = rows?.querySelector(`[data-bot-budget="${CSS.escape(slug)}"]`); if (!box) return;
  let b;
  try { b = await get(`/v2/bots/${encodeURIComponent(slug)}/budget`); } catch { return; }   // not a manager: no row
  if (!box.isConnected) return;
  const row = box.closest('[data-bot-budget-row]'), sub = row.nextElementSibling;
  box.innerHTML = budgetFieldsHTML(slug, b);
  sub.innerHTML = budgetSubHTML(slug, b);
  row.hidden = sub.hidden = false;
  const save = async body => {
    box.querySelectorAll('input,button').forEach(el => { el.disabled = true; });
    try {
      const out = await post(`/v2/bots/${encodeURIComponent(slug)}/budget`, {...body, expected_revision: b.revision});
      toast(out.released ? `${empName(slug)} released` : 'Run budget updated');
      if (out.released) void v2Refresh();
      await loadSettings();      // the bot's revision moved; its rows redraw and read the budget again
    } catch (error) { toast(error.message, true); void budgetMount(rows, slug); }
  };
  // Saved when the box is left, as Keep N tasks going, so a number is never saved half typed.
  box.onchange = ev => {
    const input = ev.target.closest('[data-budget-key]');
    if (!input || input.value === input.dataset.current || !input.checkValidity()) return;
    void save({[input.dataset.budgetKey]: input.value === '' ? null : Number(input.value)});
  };
  box.onclick = ev => { if (ev.target.closest('[data-budget-team]')) void save({turns_per_hour: null, turns_per_day: null}); };
}

// The team defaults, for owners and admins, in Settings > Bots.
async function renderTeamBudgets() {
  const el = $('#set-turn-budgets'), card = $('#settings-turn-budgets'); if (!el || !settingsIsAdmin()) return;
  let t;
  try { t = await get('/v2/settings/turn-budgets'); } catch { return; }
  if (t?.turns_per_hour === undefined) return;
  if (formBusy(el)) return;
  card.hidden = false;
  el.innerHTML = `<div class="sb-rows"><div class="sb-row"><span>Each bot</span><div class="rel-line">${BUDGET_KEYS.map(([key, label]) => `<label class="budget-field">
      <input type="number" min="0" max="100000" step="1" class="budget-input" data-team-budget="${key}" data-current="${esc(t[key])}" value="${esc(t[key])}"
        placeholder="${budgetNumber(t.defaults?.[key])}" aria-label="Runs ${label} for each bot"><span class="muted">${label}</span></label>`).join('')}</div><span></span></div>
    <div class="sb-sub">0: no limit. A bot's own budget overrides these.</div></div>`;
  el.onchange = async ev => {
    const input = ev.target.closest('[data-team-budget]');
    if (!input || input.value === '' || input.value === input.dataset.current || !input.checkValidity()) return;
    input.disabled = true;
    try { await post('/v2/settings/turn-budgets', {[input.dataset.teamBudget]: Number(input.value)}); toast('Run budget updated'); }
    catch (error) { toast(error.message, true); }
    void renderTeamBudgets();
  };
}
