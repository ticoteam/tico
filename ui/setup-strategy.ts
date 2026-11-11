/* ui/setup-strategy.ts — Setup's first step (#/welcome, ui/app/welcome.ts): What are you building.
   Next saves the website and the line (PUT /api/v2/setup) and starts the research
   (POST /api/v2/strategy/draft-from, backend/strategy_setup.py), which writes the strategy draft in the background: its
   proposed bets and sections wait on the Strategy page. Nothing here publishes.
   Classic script: its globals are shared with the other files under ui/, loaded in the order index.html lists them. */
'use strict';

/** A draft run (POST /api/v2/strategy/draft-from, backend/strategy_setup.py): its state and what it read. */
interface SsJob {
  id?: string;
  url?: string;
  text?: string;
  state: string;
  error?: {code?: string; message?: string} | null;
  [key: string]: unknown;
}

/** What setup keeps about the research: the newest run, and the website and words it was started with (`asked`), so
    Back and Next again never starts a second one. */
interface SsStrat {
  job: SsJob | null;
  asked: string;
}

/** The setup's state (welcome.ts pageWelcome), as this step reads it. */
interface SsWizard {
  record: {names: {website?: string}; answers: {building?: string}};
  strat: SsStrat;
}

// welcome.ts's open setup: `let ONB`, null until the page opens.
const ssOpen = (): SsWizard | null => ONB;
const ssBlank = (): SsStrat => ({job: null, asked: ''});
// The HTTP status of a failed answer (apiFailure, ui/app/api.ts), or undefined for any other thrown value.
const ssStatus = (error: unknown): unknown => (typeof error === 'object' && error !== null && 'status' in error ? error.status : undefined);

function ssBuildingHTML(state: SsWizard): string {
  const r = state.record;
  return `<label class="onb-field"><span class="k">Website</span>
      <input type="text" id="onb-website" maxlength="300" value="${esc(r.names.website || '')}" placeholder="Optional, such as acme.example" autocomplete="url" inputmode="url"></label>
    <label class="onb-field"><span class="k">What you're building</span>
      <textarea id="onb-building" maxlength="4000" rows="3" placeholder="One line, or a new idea in a few sentences">${esc(r.answers.building || '')}</textarea></label>`;
}
function ssCollectBuilding(state: SsWizard): void {
  const r = state.record, website = document.querySelector<HTMLInputElement>('#onb-website'), building = document.querySelector<HTMLTextAreaElement>('#onb-building');
  if (!website || !building) return;
  r.names = {...r.names, website: website.value.trim()};
  r.answers = {...r.answers, building: building.value.trim()};
}
// Starts the research when there is something to read and it is not what the last run read. Answers the server's
// refusal of the address (an internal one), which keeps the owner on the step; any other failure is the Strategy
// page's to show.
async function ssResearch(state: SsWizard): Promise<string> {
  const url = state.record.names.website || '', text = (state.record.answers.building || '').trim(), key = url + '\n' + text;
  if (state.strat.asked === key) return '';
  // Both cleared: nothing to draft from, and the run the server had is stopped (PUT /setup).
  if (!url && !text) { Object.assign(state.strat, {job: null, asked: key}); return ''; }
  // A start this page never heard back from may have committed: the server's newest run says. One that failed, or had
  // no model to run on, starts again.
  const last: SsJob | null = state.strat.job?.id ? state.strat.job : await v2Get('/v2/strategy/draft-from/latest', {fresh: true});
  if (ssOpen() !== state) return '';
  if (last && (last.url || '') === url && (last.text || '') === text && !['failed', 'no_model'].includes(last.state)) {
    state.strat.job = last; state.strat.asked = key; return '';
  }
  try {
    state.strat.job = await post('/v2/strategy/draft-from', {url, text});
  } catch (error) {
    if (ssStatus(error) === 422) return errorMessage(error, '');
    state.strat.job = {state: 'failed', url, text, error: {code: 'error', message: errorMessage(error, '')}};
    return '';               // unconfirmed: the same Next asks again
  }
  state.strat.asked = key;
  return '';
}
