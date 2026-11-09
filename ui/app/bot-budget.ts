/* ui/app/bot-budget.ts — Run budgets: a bot's runs per hour and per day, and the team defaults
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A bot's run budget (backend/turn_budgets.py): blank follows the team, 0 is no limit. A bot over it is stopped
// until someone who manages it releases it (botRelease, hub-v2.ts) or raises the budget, which releases it too.
// Read for the people who manage the bot; anyone else gets no row.
type BotBudgetKey = 'turns_per_hour' | 'turns_per_day';
// The fields one save sends: a number, or null for blank (follow the team). A field that is not sent keeps its value.
type BotBudgetPatch = Record<string, number | null>;
// The bot editor's stage(key, label, run): a held change calls `run` with the revision to save at when Save bot is pressed.
type BotBudgetStage = (key: string, label: string, run: ((revision: number) => Promise<number>) | null) => void;
const BUDGET_KEYS: [BotBudgetKey, string][] = [['turns_per_hour', 'per hour'], ['turns_per_day', 'per day']];
const budgetNumber = (n: unknown) => Number(n || 0).toLocaleString();
function budgetRowHTML(e: Employee): string {
  if (e.agent || !settingsCanManageBot(e)) return '';
  return `<div class="sb-row" data-bot-budget-row="${esc(e.id)}" hidden><span>Runs</span><div class="rel-line" data-bot-budget="${esc(e.id)}"></div><span></span></div>
    <div class="sb-sub" data-bot-budget-sub hidden></div>`;
}
function budgetFieldsHTML(slug: string, b: import('../types/api').BotBudgetState): string {
  const field = ([key, label]: [BotBudgetKey, string]) => {
    const own = b.own?.[key], team = b.team?.[key];
    return `<label class="budget-field"><input type="number" min="0" max="100000" step="1" class="budget-input" data-budget-key="${key}"
      data-current="${esc(own ?? '')}" value="${esc(own ?? '')}" placeholder="${team ? budgetNumber(team) : 'No limit'}"
      aria-label="Runs ${label} for ${esc(empName(slug))}"><span class="muted">${label}</span></label>`;
  };
  const owned = BUDGET_KEYS.some(([key]) => b.own?.[key] != null);
  return BUDGET_KEYS.map(field).join('') + (owned ? '<button type="button" class="ghost" data-budget-team>Follow team</button>' : '');
}
function budgetSubHTML(slug: string, b: import('../types/api').BotBudgetState): string {
  const held = b.held?.words || b.run_hold?.words;
  const runs = `<span class="tnum" data-budget-runs>${budgetNumber(b.runs?.hour)} this hour · ${budgetNumber(b.runs?.day)} today</span>`;
  return held ? `<span class="sysr-note" data-budget-held>${esc(held)}</span>${botReleaseButton(slug, 'ghost')}` : runs;
}
// In the bot editor (`stage`), a change is held until Save bot; anywhere else it saves when the box is left.
async function budgetMount(rows: ParentNode | null | undefined, slug: string, stage?: BotBudgetStage): Promise<void> {
  const box = rows?.querySelector<HTMLElement>(`[data-bot-budget="${CSS.escape(slug)}"]`); if (!box) return;
  let b: import('../types/api').BotBudgetState;
  try { b = await get(`/v2/bots/${encodeURIComponent(slug)}/budget`); } catch { return; }   // not a manager: no row
  if (!box.isConnected) return;
  const row = box.closest<HTMLElement>('[data-bot-budget-row]'), sub = row?.nextElementSibling;
  if (!row || !(sub instanceof HTMLElement)) return;
  box.innerHTML = budgetFieldsHTML(slug, b);
  sub.innerHTML = budgetSubHTML(slug, b);
  row.hidden = sub.hidden = false;
  const save = async (body: BotBudgetPatch) => {
    box.querySelectorAll<HTMLInputElement | HTMLButtonElement>('input,button').forEach(el => { el.disabled = true; });
    try {
      const out = await post(`/v2/bots/${encodeURIComponent(slug)}/budget`, {...body, expected_revision: b.revision});
      toast(out.released ? `${empName(slug)} released` : 'Saved');
      if (out.released) void v2Refresh();
      await loadSettings();      // the bot's revision moved; its rows redraw and read the budget again
    } catch (error) { await budgetMount(rows, slug); settingsFieldError(box, errorMessage(error, '')); }
  };
  // `stage` is only ever called from held(), which runs only when stage is set (see the checks below).
  const held = (body: BotBudgetPatch | null) => stage?.('budget', 'runs', !body ? null : async (rev: number) => {
    const out = await post(`/v2/bots/${encodeURIComponent(slug)}/budget`, {...body, expected_revision: rev});
    if (out.released) void v2Refresh();
    return out.revision ?? rev + 1;
  });
  const fields = (): BotBudgetPatch | null => {
    const body: BotBudgetPatch = {};
    box.querySelectorAll<HTMLInputElement>('[data-budget-key]').forEach(input => {
      if (input.value !== input.dataset.current && input.checkValidity()) body[input.dataset.budgetKey ?? ''] = input.value === '' ? null : Number(input.value);
    });
    return Object.keys(body).length ? body : null;
  };
  // Saved when the box is left, as Keep N tasks going, so a number is never saved half typed.
  box.onchange = ev => {
    const input = eventElement(ev)?.closest<HTMLInputElement>('[data-budget-key]');
    if (!input) return;
    settingsFieldClear(box);
    if (!input.checkValidity()) { settingsFieldError(box, input.validationMessage || 'Enter a whole number, or leave it blank.'); return; }
    if (stage) { held(fields()); return; }
    if (input.value === input.dataset.current) return;
    void save({[input.dataset.budgetKey ?? '']: input.value === '' ? null : Number(input.value)});
  };
  box.onclick = ev => {
    if (!eventElement(ev)?.closest('[data-budget-team]')) return;
    if (!stage) { void save({turns_per_hour: null, turns_per_day: null}); return; }
    box.querySelectorAll<HTMLInputElement>('[data-budget-key]').forEach(input => { input.value = ''; });
    held({turns_per_hour: null, turns_per_day: null});
  };
}

// The team defaults, for owners and admins, in Settings > Bots.
async function renderTeamBudgets(): Promise<void> {
  const el: HTMLElement | null = $('#set-turn-budgets'), card: HTMLElement | null = $('#settings-turn-budgets'); if (!el || !settingsIsAdmin()) return;
  let t: import('../types/api').TeamBudgetState;
  try { t = await get('/v2/settings/turn-budgets'); } catch { return; }
  if (t?.turns_per_hour === undefined) return;
  if (formBusy(el)) return;
  if (!card) return;
  card.hidden = false;
  el.innerHTML = `<div class="sb-rows"><div class="sb-row"><span>Each bot</span><div class="rel-line">${BUDGET_KEYS.map(([key, label]) => `<label class="budget-field">
      <input type="number" min="0" max="100000" step="1" class="budget-input" data-team-budget="${key}" data-current="${esc(t[key])}" value="${esc(t[key])}"
        placeholder="${budgetNumber(t.defaults?.[key])}" aria-label="Runs ${label} for each bot"><span class="muted">${label}</span></label>`).join('')}</div><span></span></div>
    <div class="sb-sub">0: no limit. A bot's own budget overrides these.</div></div>`;
  el.onchange = async ev => {
    const input = eventElement(ev)?.closest<HTMLInputElement>('[data-team-budget]');
    if (!input || input.value === '' || input.value === input.dataset.current || !input.checkValidity()) return;
    input.disabled = true;
    try { await post('/v2/settings/turn-budgets', {[input.dataset.teamBudget ?? '']: Number(input.value)}); toast('Saved'); void renderTeamBudgets(); }
    catch (error) { input.disabled = false; settingsFieldError(input, errorMessage(error, '')); }
  };
}
