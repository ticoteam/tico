/* ui/setup-strategy.ts — Setup's first two steps (#/welcome, ui/app/welcome.js): What are you building, then Your bets.
   Next on the first saves the website and the line (PUT /api/v2/setup) and starts the research
   (POST /api/v2/strategy/draft-from, backend/strategy_setup.py). The second polls it, then shows the bets it proposed as
   claims to keep, edit or strike, with the draft filling in beside them. The bet form and its rules are the Strategy
   page's (ui/strategy-page.js). Nothing here publishes: the steps write the draft only.
   Classic script: its globals are shared with the other files under ui/, loaded in the order index.html lists them. */
'use strict';
const SS_POLL_MS = 1500;
// The research proposes a text Wrong if; one the owner adds from a gave-up run gets the same check-by the server gives.

// ---------------------------------------------------------------- what the server sends and what the wizard keeps

/** A bet on the strategy as GET /api/v2/strategy lists it (backend/strategy.py). */
interface SsBet {
  id: string;
  statement?: string;
  why?: string;
  rules_out?: string;
  wrong_if?: Record<string, unknown> | null;
  status: 'proposed' | 'active' | 'retired';
  rank?: number | null;
  [key: string]: unknown;
}

/** The draft (`draft` in GET /api/v2/strategy): the summary and vision, and the sections by key. */
interface SsDraft {
  revision?: number;
  summary?: string | null;
  vision?: string | null;
  sections?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** The strategy as the page reads it (GET /api/v2/strategy): the bets, the draft, and the labels the team gave its bets. */
interface SsStrategy {
  bets?: SsBet[];
  draft?: SsDraft | null;
  label?: string;
  label_one?: string;
  [key: string]: unknown;
}

/** A bet a run found (`found.bets`), in the research's words, before it is taken into the strategy. */
interface SsFoundBet {
  statement?: string;
  why?: string;
  rules_out?: string;
  wrong_if?: unknown;
}

/** What a run found, by key: the summary, vision and sections it read, and the bets it proposed. */
interface SsFound {
  bets?: SsFoundBet[];
  [key: string]: unknown;
}

/** A draft run (POST /api/v2/strategy/draft-from, backend/strategy_setup.py): its state, what it read, what it found or wrote. */
interface SsJob {
  id?: string;
  url?: string;
  text?: string;
  state: string;
  step?: string | null;
  error?: {code?: string; message?: string} | null;
  found?: SsFound | null;
  written?: {bets?: string[]; sections?: string[]; revision?: number} | null;
  [key: string]: unknown;
}

/** What Setup keeps about its strategy step (ssBlank): the draft and the newest run, and what is typed but not saved yet. */
interface SsStrat {
  data: SsStrategy | null;
  job: SsJob | null;
  checked: boolean;
  asked: string;
  edit: string;
  keep: string;
  say: string;
  shut: boolean;
  /** What we won't do as typed and not saved yet; null when untouched. */
  not: string | null;
  used: string[];
  timer: number;
  saving: Promise<boolean> | null;
}

/** The Setup wizard's state (welcome.js pageWelcome): the step, the record it saves, and this step's state under `strat`. */
interface SsWizard {
  step: number;
  steps: string[];
  busy: boolean;
  record: {names: {website?: string}; answers: {building?: string}};
  strat: SsStrat;
}

/** The bet form's named controls (strategyBetFormHtml, ui/strategy-page.js): TypeScript does not type a form's named fields. */
interface SsBetFormFields extends HTMLFormControlsCollection {
  statement: HTMLInputElement;
  why: HTMLTextAreaElement;
  rules_out: HTMLTextAreaElement;
  wrong_kind: HTMLSelectElement;
  status: HTMLSelectElement;
}

// A lookup the step's own markup must satisfy: a missing element throws, as the null's first use threw before.
function ssEl<T extends Element>(selector: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new TypeError(`${selector} is not on the page`);
  return el;
}

// welcome.js's open wizard: `let ONB`, null until the page opens. welcome.js types it as any (see its JSDoc on that line),
// so this one place gives it the wizard shape.
const ssOpen = (): SsWizard | null => ONB;

// `not` is What we won't do as typed and not yet saved (null: untouched); `asked` the website and words the last run
// was started with, so Back and Next again never starts a second one.
const ssBlank = (): SsStrat => ({data: null, job: null, checked: false, asked: '', edit: '', keep: '', say: '', shut: false,
                         not: null, used: [], timer: 0, saving: null});
const ssLabel = (data: SsStrategy | null): {label: string; one: string} => ({label: data?.label || 'Bets', one: data?.label_one || 'Bet'});
const ssLive = (state: SsWizard): SsBet[] => (state.strat.data?.bets || []).filter(b => b.status !== 'retired').sort(stratRank);
const ssNot = (state: SsWizard): string => state.strat.not ?? String(state.strat.data?.draft?.sections?.not || '');
const ssRunning = (state: SsWizard): boolean => state.strat.job?.state === 'running';
const ssSource = (job: SsJob | null): string => {
  if (!job?.url) return 'your description';
  try { return new URL(job.url).hostname.replace(/^www\./, ''); } catch { return job.url; }
};
// The HTTP status of a failed answer (apiFailure, ui/app/api.ts), or undefined for any other thrown value.
const ssStatus = (error: unknown): unknown => (typeof error === 'object' && error !== null && 'status' in error ? error.status : undefined);
const ssOnStep = (state: SsWizard): boolean => ssOpen() === state && state.steps[state.step] === 'bets' && !!document.querySelector('#ss');
// Why the step cannot be left yet, in the Strategy page's words for Publish, or ''.
function ssRefusal(state: SsWizard): string {
  const data = state.strat.data;
  if (!data) return 'Loading';
  const draft: SsDraft = data.draft || {};
  return strategyPublishRefusal({...data, draft: {...draft, sections: {...(draft.sections || {}), not: ssNot(state)}}}, ssLabel(data));
}

// ---------------------------------------------------------------- What are you building
function ssBuildingHTML(state: SsWizard, actions: (label: string) => string): string {
  const r = state.record;
  return `<label class="onb-field"><span class="k">Website</span>
      <input type="text" id="onb-website" maxlength="300" value="${esc(r.names.website || '')}" placeholder="Optional, such as acme.example" autocomplete="url" inputmode="url"></label>
    <label class="onb-field"><span class="k">What you're building</span>
      <textarea id="onb-building" maxlength="4000" placeholder="One line, or a new idea in a few sentences">${esc(r.answers.building || '')}</textarea></label>
    ${actions('Next')}`;
}
function ssCollectBuilding(state: SsWizard): void {
  const r = state.record;
  r.names = {...r.names, website: ssEl<HTMLInputElement>('#onb-website').value.trim()};
  r.answers = {...r.answers, building: ssEl<HTMLTextAreaElement>('#onb-building').value.trim()};
}
// Starts the research when there is something to read and it is not what the last run read. Answers the server's
// refusal of the address (an internal one), which keeps the owner on the step; any other failure leaves the next step
// a plain form.
async function ssResearch(state: SsWizard): Promise<string> {
  const url = state.record.names.website || '', text = (state.record.answers.building || '').trim(), key = url + '\n' + text;
  if (state.strat.asked === key) return '';
  // Both cleared: nothing to draft from, and the run the server had is stopped (PUT /setup), so the step is a form.
  if (!url && !text) { Object.assign(state.strat, {job: null, checked: true, asked: key}); return ''; }
  // A start this page never heard back from may have committed: the server's newest run says.
  const last: SsJob | null = state.strat.job?.id ? state.strat.job : await v2Get('/v2/strategy/draft-from/latest', {fresh: true});
  if (ssOpen() !== state) return '';
  state.strat.checked = true;
  if (last && (last.url || '') === url && (last.text || '') === text && last.state !== 'failed') {
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

// ---------------------------------------------------------------- Your bets
function ssBetsHTML(state: SsWizard): string {
  const why = ssRefusal(state), not = STRAT_SECTIONS.find(sec => sec.key === 'not');
  return `<div class="ss" id="ss">
      <div class="ss-main">
        <div id="ss-job">${ssJobHTML(state)}</div>
        <div id="ss-bets">${ssListHTML(state)}</div>
        <label class="onb-field ss-not"><span class="k">${esc(not?.title)}</span>
          <textarea id="ss-not" maxlength="4000" rows="3" placeholder="${esc(not?.prompt)}">${esc(ssNot(state))}</textarea></label>
      </div>
      <aside class="ss-side" id="ss-side" aria-label="Draft">${ssSideHTML(state)}</aside></div>
    <div class="onb-actions"><button class="ghost" type="button" id="onb-back">Back</button><button class="primary" type="button" id="onb-next"${why ? ' disabled' : ''}>Next</button>
      <span class="spacer"></span><span class="muted" id="onb-status">${esc(why === 'Loading' ? '' : why)}</span><button class="ghost" type="button" data-setup-help="wizard">Want help setting up?</button></div>`;
}
const ssPlain = (message: unknown): string => esc(String(message || '').replace(/[.\s]+$/, ''));
function ssJobHTML(state: SsWizard): string {
  const job = state.strat.job, from = esc(ssSource(job));
  if (!job) return '';
  if (job.state === 'running') return `<p class="ss-job" role="status"><span class="ss-dot" aria-hidden="true"></span>${job.step === 'reading' ? `Reading ${from}…` : `Drafting from ${from}…`}</p>`;
  if (job.state === 'no_model') return '<p class="ss-job muted" role="status">No AI provider yet, so write them yourself.</p>';
  if (job.state === 'failed') return `<p class="ss-job muted" role="status">${ssPlain(job.error?.message || 'The research stopped')}. Write them yourself.</p>`;
  if (job.state === 'gave_up') return ssFoundHTML(state, job);
  return (job.written?.bets || []).length ? `<p class="ss-job muted" role="status">Drafted from ${from}. Keep, edit or strike each.</p>` : '';
}
// What a run drafts besides bets (backend/strategy_prompt.py): the summary and vision, then the sections.
const SS_LINES: Record<string, string> = {summary: 'Summary', vision: 'Vision'};
const ssFoundKeys = (): string[] => [...Object.keys(SS_LINES), ...STRAT_SECTIONS.map(sec => sec.key)];
const ssFoundTitle = (k: string): string | undefined => SS_LINES[k] || STRAT_SECTIONS.find(sec => sec.key === k)?.title;
// The draft's own text for a found key: the summary and vision are on the draft, the rest are its sections.
const ssDraftText = (draft: SsDraft | null | undefined, k: string): unknown => (k in SS_LINES ? draft?.[k] : draft?.sections?.[k]);
// A run that gave up wrote nothing (the draft changed under it): what it found, to take by hand.
function ssFoundHTML(state: SsWizard, job: SsJob): string {
  const found: SsFound = job.found || {}, draft = state.strat.data?.draft, {one} = ssLabel(state.strat.data);
  const have = new Set(ssLive(state).map(b => stratText(b.statement).toLowerCase()));
  const row = (k: unknown, text: unknown, attr: string): string => `<li class="ss-found-row"><span class="strat-bet-k">${esc(k)}</span><span>${esc(text)}</span>
      <button class="ghost strat-mini" type="button" ${attr}>Add</button></li>`;
  const rows = [
    ...ssFoundKeys().filter(k => stratText(found[k]) && !stratText(ssDraftText(draft, k)) && !state.strat.used.includes(k))
      .map(k => row(ssFoundTitle(k), found[k], `data-ss-use="${k}"`)),
    ...(found.bets || []).map((b, i): [SsFoundBet, number] => [b, i]).filter(([b, i]) => stratText(b.statement) && !have.has(stratText(b.statement).toLowerCase()) && !state.strat.used.includes('bet' + i))
      .map(([b, i]) => row(one, b.statement, `data-ss-take="${i}"`))];
  return `<div class="ss-found" id="ss-found"><p class="ss-job muted" role="status">The draft changed while this ran, so nothing was written${rows.length ? '. It found:' : '.'}</p>
    ${rows.length ? `<ul class="ss-found-list">${rows.join('')}</ul>` : ''}</div>`;
}
function ssListHTML(state: SsWizard): string {
  const data = state.strat.data, {label, one} = ssLabel(data);
  const head = (add: boolean): string => `<div class="strat-sec-head"><h2 class="strat-k">${esc(label)}</h2>${add ? `<button class="ghost strat-mini" type="button" data-ss-add-bet aria-label="Add ${esc(one)}">Add</button>` : ''}</div>`;
  if (!data) return head(false) + skelRows(3, {dot: false});
  const live = ssLive(state), running = ssRunning(state);
  // With nothing to confirm (no model, a failed run, nothing found) the step is a plain form: the new bet's fields are
  // open. A run that gave up lists what it found to add instead.
  const plain = !live.length && !running && state.strat.job?.state !== 'gave_up';
  const adding = state.strat.edit === 'new' || (plain && !state.strat.edit && !state.strat.shut);
  const rows = live.map((b, n) => ssBetHTML(state, b, n)).join('');
  return `${head(!adding)}
    ${rows ? `<ol class="strat-bets ss-bets">${rows}</ol>` : running ? skelRows(3, {dot: false}) : ''}
    ${adding ? `<div class="strat-bet strat-bet-new">${strategyBetFormHtml({status: 'active'})}</div>` : ''}`;
}
function ssBetHTML(state: SsWizard, b: SsBet, n: number): string {
  if (state.strat.edit === b.id) return `<li class="strat-bet" data-ss-bet="${esc(b.id)}">${strategyBetFormHtml(state.strat.keep === b.id ? {...b, status: 'active'} : b)}</li>`;
  const kept = b.status === 'active', name = esc(b.statement);
  const line = (k: string, html: string): string => `<p class="strat-bet-line"><span class="strat-bet-k">${k}</span><span>${html}</span></p>`;
  return `<li class="strat-bet ss-bet" data-ss-bet="${esc(b.id)}">
      <div class="strat-bet-top"><span class="strat-bet-n tnum" aria-hidden="true">${n + 1}</span><span class="strat-bet-st">${name}</span>${kept ? '<span class="pill ok">Kept</span>' : ''}</div>
      <div class="strat-bet-lines">
        ${stratText(b.why) ? line('Why', esc(b.why)) : ''}
        ${stratText(b.rules_out) ? line('Rules out', esc(b.rules_out)) : ''}
        ${stratWrongSet(b.wrong_if) ? line('Wrong if', strategyWrongIfHtml(b.wrong_if)) : ''}
      </div>
      <div class="ss-bet-actions">${kept ? '' : `<button class="ghost strat-mini" type="button" data-ss-keep aria-label="Keep: ${name}">Keep</button>`}
        <button class="ghost strat-mini" type="button" data-ss-edit aria-label="Edit: ${name}">Edit</button>
        <button class="ghost strat-mini" type="button" data-ss-strike aria-label="Strike: ${name}">Strike</button></div></li>`;
}
function ssSideHTML(state: SsWizard): string {
  const data = state.strat.data, sections: Record<string, unknown> = data?.draft?.sections || {}, {label} = ssLabel(data), waiting = !data || ssRunning(state);
  const block = (title: unknown, inner: string): string => `<section class="ss-side-sec"><h3 class="strat-k">${esc(title)}</h3>${inner}</section>`;
  const none = '<p class="muted strat-empty">Not written</p>';
  const text = (key: string): string => stratText(sections[key]) ? `<div class="md strat-md">${safeMd(sections[key])}</div>` : waiting ? skelRows(2, {dot: false}) : none;
  const kept = ssLive(state).filter(b => b.status === 'active'), not = stratText(ssNot(state));
  return `<p class="ss-side-head"><b>Draft</b> <span class="muted">· not published</span></p>
    ${block(STRAT_SECTIONS[0]?.title, text('serve'))}${block(STRAT_SECTIONS[1]?.title, text('sell'))}
    ${block(label, kept.length ? `<ol class="ss-side-bets">${kept.map(b => `<li>${esc(b.statement)}</li>`).join('')}</ol>` : '<p class="muted strat-empty">None kept yet</p>')}
    ${block(STRAT_SECTIONS.find(sec => sec.key === 'not')?.title, not ? `<p class="strat-text">${esc(not)}</p>` : none)}`;
}

// Entering the step reads the draft and the newest run, then polls a run still going.
async function ssEnter(state: SsWizard): Promise<void> {
  if (!state.strat.job && !state.strat.checked) {
    state.strat.checked = true;
    state.strat.job = await v2Get('/v2/strategy/draft-from/latest', {fresh: true});
  }
  await ssReload(state);
  ssPoll(state);
}
async function ssReload(state: SsWizard): Promise<void> {
  const data: SsStrategy | null = await v2Get('/v2/strategy', {fresh: true});
  if (ssOpen() !== state) return;
  if (data) state.strat.data = data;
  ssDraw(state);
}
// Redraws the step's parts. A bet form with words typed in it stays as it is unless `force` (the owner's own click).
function ssDraw(state: SsWizard, force = false): void {
  if (!ssOnStep(state)) return;
  ssEl('#ss-job').innerHTML = ssJobHTML(state);
  const list = ssEl<HTMLElement>('#ss-bets');
  if (force || !formBusy(list)) list.innerHTML = ssListHTML(state);
  const not = document.querySelector<HTMLTextAreaElement>('#ss-not');
  if (not && state.strat.not === null && document.activeElement !== not) not.value = ssNot(state);
  ssEl('#ss-side').innerHTML = ssSideHTML(state);
  ssGate(state);
  ssBind(state);
}
function ssGate(state: SsWizard): void {
  const next = document.querySelector<HTMLButtonElement>('#onb-next'), status = document.querySelector<HTMLElement>('#onb-status');
  if (!next || state.busy || !ssOnStep(state)) return;
  const why = ssRefusal(state);
  next.disabled = !!why;
  if (status) status.textContent = why === 'Loading' ? '' : why;
}
function ssPoll(state: SsWizard): void {
  clearTimeout(state.strat.timer);
  const job = state.strat.job;
  if (!job?.id || job.state !== 'running') return;
  const id = job.id;
  state.strat.timer = setTimeout(async () => {
    if (!ssOnStep(state)) return;                       // entering the step again polls again
    const next: SsJob | null = await v2Get(`/v2/strategy/draft-from/${encodeURIComponent(id)}`, {fresh: true});
    if (ssOpen() !== state || state.strat.job?.id !== id) return;    // a newer job (Back, a new website) replaced it
    if (next) state.strat.job = next;
    if (next && next.state !== 'running') {
      await ssReload(state);
      // What we won't do typed during the run is saved now: saving it earlier would have made the run give up.
      if (state.strat.not !== null && document.activeElement !== document.querySelector('#ss-not')) void ssSaveNot(state);
      return;
    }
    const line = document.querySelector<HTMLElement>('#ss-job');
    if (line) line.innerHTML = ssJobHTML(state);
    ssPoll(state);
  }, SS_POLL_MS);
}

// A write, then the draft read again. Bet writes send no revision, so only What we won't do can meet a 409.
async function ssWrite(state: SsWizard, run: () => Promise<unknown>): Promise<boolean> {
  let ok = true;
  try { await run(); } catch (error) { ok = false; toast(errorMessage(error, 'Could not save'), true); }
  await ssReload(state);
  return ok;
}
// One save at a time: leaving the field and pressing Next both save it, and the second finds it saved.
async function ssSaveNot(state: SsWizard): Promise<boolean> {
  while (state.strat.saving) await state.strat.saving;
  state.strat.saving = ssSaveNotNow(state);
  try { return await state.strat.saving; } finally { state.strat.saving = null; }
}
async function ssSaveNotNow(state: SsWizard): Promise<boolean> {
  const typed = state.strat.not;
  if (typed === null) return true;
  const text = stratText(typed), saved = (): boolean => text === stratText(state.strat.data?.draft?.sections?.not);
  if (saved()) { state.strat.not = null; return true; }
  for (let attempt = 0; ; attempt++) {
    try {
      await put('/v2/strategy/draft', {sections: {not: text}, revision: state.strat.data?.draft?.revision ?? null});
      break;
    } catch (error) {
      // Changed elsewhere (the research finishing, another tab): read the revision again and save this one field.
      if (ssStatus(error) !== 409 || attempt) { toast(errorMessage(error, 'Could not save'), true); return false; }
      const data: SsStrategy | null = await v2Get('/v2/strategy', {fresh: true});
      if (ssOpen() !== state) return false;
      if (data) state.strat.data = data;
      if (saved()) break;
    }
  }
  if (state.strat.not === typed) state.strat.not = null;
  await ssReload(state);
  return true;
}
// Next: What we won't do saved, then the same rule as Publish.
async function ssLeave(state: SsWizard): Promise<boolean> {
  clearTimeout(state.strat.timer);
  if (!await ssSaveNot(state)) return false;
  return !ssRefusal(state);
}

function ssBind(state: SsWizard): void {
  const root = document.querySelector<HTMLElement>('#ss');
  if (!root) return;
  const data = state.strat.data;
  const not = ssEl<HTMLTextAreaElement>('#ss-not');
  not.oninput = () => { state.strat.not = not.value; ssEl('#ss-side').innerHTML = ssSideHTML(state); ssGate(state); };
  not.onblur = () => { if (!ssRunning(state)) void ssSaveNot(state); };
  const focus = (sel: string): void => { const el = root.querySelector<HTMLElement>(sel); if (el) el.focus(); };
  for (const li of root.querySelectorAll<HTMLLIElement>('li[data-ss-bet]')) {
    const id = li.dataset.ssBet ?? '', bet = ssLive(state).find(b => b.id === id);
    if (!bet) continue;
    const keep = li.querySelector<HTMLButtonElement>('[data-ss-keep]'), edit = li.querySelector<HTMLButtonElement>('[data-ss-edit]'), strike = li.querySelector<HTMLButtonElement>('[data-ss-strike]');
    // Keep makes it active; past the most that can be active, its form opens saying so.
    if (keep) keep.onclick = () => {
      const refusal = stratActiveRefusal(bet, data?.bets);
      if (refusal) {
        Object.assign(state.strat, {edit: id, keep: id, say: refusal});
        ssDraw(state, true);
        focus(`[data-bet-form="${CSS.escape(id)}"] [name="status"]`);
        return;
      }
      keep.disabled = true;
      void ssWrite(state, () => post(`/v2/strategy/bets/${encodeURIComponent(id)}`, {status: 'active'}));
    };
    if (edit) edit.onclick = () => {
      Object.assign(state.strat, {edit: id, keep: '', say: ''});
      ssDraw(state, true);
      focus(`[data-bet-form="${CSS.escape(id)}"] input`);
    };
    // Struck is retired: the Strategy page keeps it under Retired.
    if (strike) strike.onclick = () => {
      strike.disabled = true;
      void ssWrite(state, () => post(`/v2/strategy/bets/${encodeURIComponent(id)}`, {status: 'retired'}));
    };
  }
  const add = root.querySelector<HTMLButtonElement>('[data-ss-add-bet]');
  if (add) add.onclick = () => { Object.assign(state.strat, {edit: 'new', keep: '', say: ''}); ssDraw(state, true); focus('[data-bet-form="new"] input'); };
  const form = root.querySelector<HTMLFormElement>('[data-bet-form]');
  if (form) ssBindForm(state, form);
  const found: SsFound = state.strat.job?.found || {};
  const take = (button: HTMLButtonElement, key: string, run: () => Promise<unknown>): void => {
    button.onclick = async () => {
      button.disabled = true;
      state.strat.used.push(key);
      if (!await ssWrite(state, run)) state.strat.used = state.strat.used.filter(k => k !== key);
    };
  };
  for (const b of root.querySelectorAll<HTMLButtonElement>('[data-ss-use]')) {
    const key = b.dataset.ssUse ?? '';
    const text = stratText(found[key]);
    take(b, key, () => put('/v2/strategy/draft', {...(key in SS_LINES ? {[key]: text} : {sections: {[key]: text}}),
      revision: data?.draft?.revision ?? null}));
  }
  for (const b of root.querySelectorAll<HTMLButtonElement>('[data-ss-take]')) {
    const i = Number(b.dataset.ssTake), bet: SsFoundBet = (found.bets || [])[i] || {};
    const check_by = stratCheckBy();
    take(b, 'bet' + i, () => post('/v2/strategy/bets', {statement: stratText(bet.statement), why: stratText(bet.why),
      rules_out: stratText(bet.rules_out), status: 'proposed',
      wrong_if: stratText(bet.wrong_if) ? {kind: 'text', text: stratText(bet.wrong_if), check_by} : null}));
  }
}
// The bet form (strategyBetFormHtml) with the Strategy page's rule: at most so many active. Here the choice stays as
// picked and the form says why not.
function ssBindForm(state: SsWizard, form: HTMLFormElement): void {
  // The cast names the form's own controls (statement, why, rules_out, wrong_kind, status), as strategy-page.js renders them.
  const id = form.dataset.betForm ?? '', el = form.elements as SsBetFormFields, data = state.strat.data;
  const say = (text: string): void => { const whyNot = form.querySelector<HTMLElement>('[data-bet-why-not]'); if (!whyNot) throw new TypeError('no bet form message'); whyNot.textContent = text; whyNot.hidden = !text; };
  if (state.strat.say) say(state.strat.say);
  const done = (): void => {
    Object.assign(state.strat, {edit: '', keep: '', say: ''});
    if (id === 'new') state.strat.shut = true;
    ssDraw(state, true);
  };
  const cancel = form.querySelector<HTMLButtonElement>('[data-bet-cancel]');
  if (!cancel) throw new TypeError('no bet form cancel');
  cancel.onclick = done;
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  el.wrong_kind.onchange = () => {
    for (const box of form.querySelectorAll<HTMLElement>('[data-wrong-kind]')) box.hidden = box.dataset.wrongKind !== el.wrong_kind.value;
  };
  const fields = () => ({statement: el.statement.value.trim(), why: el.why.value.trim(), rules_out: el.rules_out.value.trim(),
    wrong_if: strategyWrongFromForm(el), status: el.status.value});
  const refusal = () => (el.status.value === 'active' ? stratActiveRefusal({id, ...fields()}, data?.bets) : '');
  el.status.onchange = () => say(refusal());
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!el.statement.value.trim()) { el.statement.focus(); return; }
    const why = refusal();
    if (why) { say(why); return; }
    const was = (data?.bets || []).find(b => b.id === id), now = fields();
    const same = (k: string, v: unknown): boolean => (k === 'wrong_if' ? JSON.stringify(v || null) === JSON.stringify(was?.wrong_if || null) : v === (was?.[k] || ''));
    const change = was ? Object.fromEntries(Object.entries(now).filter(([k, v]) => !same(k, v))) : now;
    if (was && !Object.keys(change).length) { done(); return; }
    form.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = true; });
    state.strat.say = '';
    if (await ssWrite(state, () => post(was ? `/v2/strategy/bets/${encodeURIComponent(id)}` : '/v2/strategy/bets', change))) {
      Object.assign(state.strat, {edit: '', keep: ''});
      ssDraw(state, true);
    } else if (form.isConnected) form.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = false; });
  };
}
