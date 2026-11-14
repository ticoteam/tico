/* ui/app/welcome.ts — First run: setup as one modal over the app (#/welcome, docs/onboarding.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The setup record (GET/PUT /api/v2/setup, backend/onboarding.py) as this wizard keeps it.
interface OnbNames { company_name: string; app_name: string; assistant_name: string; owner_name: string; team_domain: string; website: string }
interface OnbAnswers {
  building: string; what_we_do: string; customers: string; team_size: string; work_arrives: string[]; repetitive_work: string;
  software_product: string; departments: string[]; briefings: Record<string, string>; teams_off: string[];
  /** An earlier release's answer: dropped when the record loads. */
  never_without_person?: unknown;
}
/** A picked bot as the record saves it (`selected`, keyed by its card's slug). */
interface OnbPick { display_name?: string; reports_to?: string; instructions?: unknown }
/** A computer on the record (backend/onboarding.py `_machine`). */
interface OnbRunner { id?: string; label?: string | null; online?: boolean }
/** One team of the default chart (GET /api/v2/setup `default_chart`): its seats in chart order, each with the seat it
 *  reports to inside the team (null for the team's top seats); `locked`: built-ins only, always on. The chart's teams
 *  and titles are the server's. */
interface OnbTeam { team: string; on: boolean; locked?: boolean; roles: {id?: string; title: string; live: boolean; reports_to?: string | null}[] }
/** The company file the team was set up from. */
interface OnbImported { seats?: number; deferred?: string[]; people?: {invited_at?: string | null}[] }
interface OnbRecord {
  names: OnbNames;
  answers: OnbAnswers;
  selected: Record<string, OnbPick | null>;
  /** When setup was completed; null until then. */
  completed: string | null;
  home: string;
  bots: FrBot[];
  machine: {runners: OnbRunner[]; enrolled: boolean; cloud?: boolean};
  needed: boolean;
  default_chart?: OnbTeam[];
  company_import_failed?: {reason?: string} | null;
  company_import?: OnbImported | null;
}
/** A company file's preview (POST /api/v2/setup/company/preview). */
interface OnbPreview { company_name: string; fingerprint: string; employees: {title: string}[]; seats?: {title: string}[]; deferred: string[] }
/** The open setup (ONB): the step, the record it saves, the catalog cards, the org chart and the research run. */
interface OnbState {
  step: number;
  steps: string[];
  providers: SettingsProvidersView | null;
  models: SettingsProviderModel[];
  record: OnbRecord;
  catalog: CatalogState;
  busy: boolean;
  completing: boolean;
  poll: number | null;
  org: ReturnType<typeof obBlank>;
  /** The Your team step: the default chart, the detailed builder (Change), or a company file's team. */
  mode: 'default' | 'custom' | 'file';
  enrollment: string;
  kind: string;
  strat: SsStrat;
  /** Getting started's `subscription` item (backend/getting_started.py), once read; null when the server has none. */
  subscription: GsItem | null;
  dialog: HTMLDialogElement | null;
  /** Opened on a team that already exists (Finish setup in the sidebar): the last step, which may be hidden for good. */
  reopened?: boolean;
  code?: string;
  otherOpen?: boolean;
  computerOpen?: boolean;
  companyImport?: {text: string; preview: OnbPreview} | null;
  companyImportError?: string;
  companyImportGeneration?: number;
}
/** [label, command, other]: `other` marks a line that is instead of one above. */
type OnbCommand = [label: string, command: string, other?: boolean];
/** GET /api/v2/models: the models on offer and the harnesses that run them. */
interface OnbModels { models?: SettingsProviderModel[]; harnesses?: HarnessRow[] }
/** A model sign-in the computer lines list. */
interface OnbSignIn { runtime: string | undefined; label: string }

// ----------------------------------------------------------------- first run (#/welcome)
// Three steps in a modal over the app: what the team is building (the research drafts the strategy from it in the
// background), the team (the default chart, laid out and picked; Create makes it), and what the bots run on. Every
// Next saves a draft, so a closed tab loses nothing.
const ONB_STEPS = ['building', 'team', 'run'];
const ONB_TITLES: Record<string, string> = {building: 'What are you building?', team: 'Your team', run: 'What your bots run on'};
const ONB_BLANK: OnbRecord = {
  names: {company_name: '', app_name: '', assistant_name: '', owner_name: '', team_domain: '', website: ''},
  answers: {building: '', what_we_do: '', customers: '', team_size: '', work_arrives: [], repetitive_work: '',
            software_product: '', departments: [], briefings: {}, teams_off: []},
  selected: {}, completed: null, home: '', bots: [], machine: {runners: [], enrolled: false}, needed: true};
// The open setup; null until the page opens. ui/setup-strategy.ts, ui/first-run.ts and ui/org-builder.ts read it.
let ONB: OnbState | null = null;
// A server that is a container is the usual install, and the computer that runs bots is a Linux one beside it.
const onbDefaultKind = (): string => (S.config?.in_docker ? 'linux' : 'mac');
// Their own name: the roster's when it is not just an address, else the sign-in's display name when the proxy sends one.
const onbOwnerGuess = (): string => (S.me?.name && !/@/.test(S.me.name) ? S.me.name : S.me?.sign_in_name || '');
// A team whose owner signs in with public mail (Gmail) and has no company address on the roster is asked its email domain, once.
const onbAsksDomain = (): boolean => S.me?.role === 'owner' && !(S.me.company_domains || []).length;
const onbKey = (state: OnbState): string => String(state.steps[state.step]);
function onbStop() {
  const state = ONB;
  if (!state) return;
  ONB = null;
  clearInterval(state.poll ?? undefined);
  state.dialog?.remove();
}
function pageWelcome() {
  // Only the owner may set a team up; everyone else has no reason to see the address either.
  if (S.me && S.me.role !== 'owner') { location.hash = TASKS; return; }
  // The app is behind the modal, as it will be after: Tasks, where the owner lands.
  pageTasks();
  const state: OnbState = ONB = {step: 0, steps: ONB_STEPS, providers: null, models: [], record: JSON.parse(JSON.stringify(ONB_BLANK)),
    catalog: catalogState([]), busy: false, completing: false, poll: null, org: obBlank(), mode: 'default',
    enrollment: '', kind: onbDefaultKind(), strat: ssBlank(), subscription: null, dialog: null};
  onbOpen(state);
  void onbLoad(state);
}
// The modal: closed only once the team exists (Escape and ✕ then land on Tasks); before that it stays, and Finish setup
// in the sidebar opens it again.
function onbOpen(state: OnbState) {
  $('#onb')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'onb'; dialog.className = 'tmodal tmodal-shell onb-dlg';
  dialog.setAttribute('aria-labelledby', 'onb-title');
  dialog.innerHTML = `<div class="tmodal-head onb-head"><h2 id="onb-title">${esc(onbHeading(state))}</h2>
      <span class="onb-count tnum" id="onb-count"></span>
      <button class="ghost tmodal-x" type="button" id="onb-close" aria-label="Close" hidden>✕</button></div>
    <div class="tmodal-body" id="onb-step" aria-live="polite"><div class="empty">Loading…</div></div>`;
  document.body.append(dialog);
  state.dialog = dialog;
  dialog.addEventListener('cancel', event => { if (!state.record.completed) event.preventDefault(); });
  // A browser may close a modal on a second Escape whatever the page says: before Create it opens again.
  dialog.addEventListener('close', () => {
    if (ONB !== state) return;
    if (!state.record.completed) { dialog.showModal(); return; }
    onbStop();
    // The welcome replaces the tour, so closing it is the tour seen; it opened over whatever page the owner came to.
    if (onbKey(state) === 'welcome') void gsState({tour: true});
    else location.hash = TASKS;
    // Finish setup stays while a row of the last step is open; hidden for good, it goes now (a fresh read could land
    // before the dismissal is saved).
    if (GS?.dismissed) renderOnboardingNav(); else void gsRefresh();
  });
  dialog.querySelector<HTMLButtonElement>('#onb-close')!.onclick = () => dialog.close();
  dialog.showModal();
}
const onbCompany = (state: OnbState): string => state.record.names.company_name || (S.config.company_name_default ? '' : S.config.company_name || '');
const onbHeading = (state: OnbState): string => `Set up ${onbCompany(state) || appName()}`;
async function onbLoad(state: OnbState) {
  try {
    const [record, catalog, roster, providers, models] = await Promise.all([
      get<Partial<OnbRecord> | null>('/v2/setup'), get('/v2/templates'), get('/humans').catch(() => ({people: []})),
      get<SettingsProvidersView | null>('/v2/providers').catch(() => null),
      get<OnbModels>('/v2/models').catch((): OnbModels => ({models: []}))]);
    if (ONB !== state) return;
    state.providers = providers && Array.isArray(providers.providers) ? providers : null;
    state.models = models.models || [];
    if (models.harnesses) HARNESS_ROWS = models.harnesses;
    if ((roster.people || []).length) SETTINGS_DATA.people = roster.people;
    state.catalog = catalogState(catalogCards(catalog));
    onbAdopt(state, record);
    // A returning draft that picked roles by hand opens on its chart.
    if (Object.keys(state.record.selected).some(slug => !catalogCard(state.catalog, slug)?.required)) state.mode = 'custom';
    if (state.record.completed) { state.step = ONB_STEPS.indexOf('run'); state.reopened = true; }
    onbRender(state);
    if (state.record.completed) onbRunStarted(state);
  } catch (error) {
    if (ONB !== state) return;
    $('#onb-step').innerHTML = `<p class="err">${esc(errorMessage(error, ''))}</p>`;
  }
}
function onbAdopt(state: OnbState, record: Partial<OnbRecord> | null | undefined) {
  const blank: OnbRecord = JSON.parse(JSON.stringify(ONB_BLANK)), row: Partial<OnbRecord> = record && typeof record === 'object' ? record : {};
  state.record = {...blank, ...row,
    names: {...blank.names, ...(row.names || {})},
    answers: {...blank.answers, ...(row.answers || {})},
    selected: {...(row.selected || {})}, home: row.home || '',
    bots: row.bots || [],
    machine: {...blank.machine, ...(row.machine || {})}};
  delete state.record.answers.never_without_person;
  const names = state.record.names;
  // The server's own "Company" (no TICO_TEAM_NAME) is no one's name: the field starts empty.
  if (!names.company_name && !S.config.company_name_default) names.company_name = S.config.company_name || '';
  if (!names.app_name) names.app_name = appName();
  if (!names.owner_name) names.owner_name = S.config.owner_name || onbOwnerGuess();
  if (!names.assistant_name || names.assistant_name.trim().toLowerCase() === (names.company_name || '').trim().toLowerCase()) names.assistant_name = assistantName();
  onbSeedPicks(state);
}
function onbSeedPicks(state: OnbState) {
  const {picked, edits} = state.catalog, saved = Object.entries(state.record.selected || {});
  for (const [slug, entry] of saved) {
    if (!catalogCard(state.catalog, slug)) continue;
    picked.add(slug);
    edits[slug] ||= {};
    if (entry?.display_name) { edits[slug].display_name = entry.display_name; state.catalog.touched.add(slug); }
    if (entry?.reports_to) edits[slug].reports_to = entry.reports_to;
    if (typeof entry?.instructions === 'string') {
      edits[slug].instructions = entry.instructions;
      const match = entry.instructions.match(/^Mailbox:\s*(\S+@\S+)\s*$/m);
      const person = match && catalogPeople().find(row => (row.email || '').toLowerCase() === match[1]!.toLowerCase());
      if (person) edits[slug].person = person.id;
    }
  }
  // The required cards are not a choice: the built-ins are always made.
  for (const card of state.catalog.cards) if (card.required) picked.add(card.slug);
}
function onbRender(state: OnbState) {
  if (ONB !== state || !state.dialog) return;
  state.busy = false;                       // a fresh screen is never mid-save
  const key = onbKey(state), dialog = state.dialog, host = $('#onb-step');
  dialog.classList.toggle('onb-wide', key === 'team' && state.mode === 'custom');
  const welcome = key === 'welcome';
  $('#onb-title').textContent = welcome ? `Welcome to ${onbCompany(state) || appName()}` : onbHeading(state);
  $('#onb-count').textContent = welcome ? '' : `Step ${state.step + 1} of ${state.steps.length}`;
  $('#onb-close').hidden = !state.record.completed;
  host.dataset.onbStep = key;
  host.innerHTML = welcome ? onbWelcomeHTML(state) : `<h3 class="onb-step-title">${esc(ONB_TITLES[key])}</h3>${onbStepHTML(state, key)}`;
  onbWire(state, key);
}
// A step's one row of buttons: Back when there is a step to go back to, the one primary, and the status between.
function onbActions(state: OnbState, label: string, back = state.step > 0 && !state.record.completed, extra = ''): string {
  return `<div class="onb-actions">${back ? '<button class="ghost" type="button" id="onb-back">Back</button>' : ''}${extra}
    <span class="spacer"></span><span class="muted" id="onb-status" role="status"></span><button class="primary" type="button" id="onb-next">${esc(label)}</button></div>`;
}
function onbStepHTML(state: OnbState, key: string): string {
  if (key === 'building') return onbBuildingHTML(state);
  if (key === 'team') return onbTeamHTML(state);
  return onbRunHTML(state);
}

// ----------------------------------------------------------------- What are you building?
// The company's name and the owner's are asked only when the server does not know them.
function onbBuildingHTML(state: OnbState): string {
  const names = state.record.names;
  const owner = !onbOwnerGuess() && !S.config.owner_name;
  return `${S.config.company_name_default ? `<label class="onb-field"><span class="k">Company name</span>
      <input type="text" id="onb-company" maxlength="100" value="${esc(names.company_name)}" placeholder="Acme" autocomplete="organization"></label>` : ''}
    ${owner ? `<label class="onb-field"><span class="k">Your name</span>
      <input type="text" id="onb-owner" maxlength="100" value="${esc(names.owner_name)}" autocomplete="name"></label>` : ''}
    ${ssBuildingHTML(state)}
    ${onbAsksDomain() ? `<label class="onb-field"><span class="k">Team email domain</span>
      <input type="text" id="onb-domain" maxlength="100" value="${esc(names.team_domain)}" placeholder="Optional, such as acme.example" autocomplete="off" inputmode="url"></label>` : ''}
    ${onbActions(state, 'Next')}`;
}

// ----------------------------------------------------------------- Your team
// The default chart, every team on, a switch each; this is also the review, so Create is here. Change opens the
// detailed builder (ui/org-builder.ts); a company file replaces the list with its own team. A file chosen at install
// that did not import is one line on top, in the server's words.
function onbTeamHTML(state: OnbState): string {
  const failed = state.record.company_import_failed && !state.record.completed
    ? `<p class="onb-failed" id="onb-import-failed" role="alert">Your company file wasn't imported: ${esc(String(state.record.company_import_failed.reason || 'it could not be applied').replace(/\.?$/, '.'))}</p>` : '';
  if (state.mode === 'custom') return failed + obStepHTML(state);
  if (state.mode === 'file' && state.companyImport) return failed + onbFileTeamHTML(state.companyImport.preview) + onbActions(state, 'Create team');
  const teams = state.record.default_chart || [];
  return `${failed}<div class="onb-teams" role="group" aria-label="Teams">${teams.map(team => `<label class="onb-team" data-onb-team-row="${esc(team.team)}">
      <input type="checkbox" class="people-switch" data-onb-team="${esc(team.team)}" aria-label="${esc(team.team)}"${team.on || team.locked ? ' checked' : ''}${team.locked ? ' disabled' : ''}>
      <span class="onb-team-text"><strong>${esc(team.team)}</strong>${onbTeamRolesHTML(team)}</span>
      <span class="onb-team-n tnum" aria-hidden="true">${team.roles.length}</span></label>`).join('')}</div>
    <p class="onb-team-more"><button class="linkish" type="button" id="onb-change">Change</button>
      <span aria-hidden="true">·</span><button class="linkish" type="button" id="onb-file-pick">I have a company file</button>
      <input type="file" id="onb-company-file" accept=".yaml,.yml" hidden></p>
    ${onbActions(state, 'Create team')}`;
}
// A team's seats in the chart's own order: each top seat (its manager) first, then everyone under it, depth first.
// A Recruiting seat (no bot yet) is muted.
function onbTeamRolesHTML(team: OnbTeam): string {
  const ids = new Set(team.roles.map(role => role.id));
  const under = (id: string | null) => team.roles.filter(role => (role.reports_to && ids.has(role.reports_to) ? role.reports_to : null) === id);
  const tree = (id: string | null): OnbTeam['roles'] => under(id).flatMap(role => [role, ...(role.id ? tree(role.id) : [])]);
  const title = (role: OnbTeam['roles'][number]) => `<span${role.live ? '' : ' class="onb-team-open" title="Recruiting"'}>${esc(role.title)}</span>`;
  // The head (the first role at the top, live or not) stays first; after it the team that works today, then the open
  // seats, each in chart order.
  const [head, ...rest] = tree(null);
  return `<span class="onb-team-roles">${[...(head ? [head] : []), ...rest.filter(role => role.live), ...rest.filter(role => !role.live)].map(title).join(' · ')}</span>`;
}
const onbImportPending = (fields: string[] | null | undefined): string => (fields || []).map(field => ({goals: 'goals', tool_connections: 'tool connections',
  first_jobs: 'first jobs', model_choices: 'model choices', routines: 'automations',
  open_seats: 'open seats', coworkers: 'coworkers'} as Record<string, string>)[field] || field).join(', ');
function onbFileTeamHTML(preview: OnbPreview): string {
  const rows = [...preview.employees.map(bot => `<li>${esc(bot.title)}</li>`),
    ...(preview.seats || []).map(seat => `<li class="muted">${esc(seat.title)} <span class="pill waiting">Recruiting</span></li>`)].join('');
  return `<div class="onb-file" id="onb-company-file-box"><p><strong>${esc(preview.company_name)}</strong></p><ul class="onb-file-team">${rows}</ul>
    ${preview.deferred.length ? `<p class="muted">Saved for later: ${esc(onbImportPending(preview.deferred))}.</p>` : ''}</div>`;
}
async function onbReadCompanyFile(state: OnbState, file: File | null | undefined) {
  if (!file || state.busy || state.completing) return;
  const generation = state.companyImportGeneration = (state.companyImportGeneration || 0) + 1;
  state.companyImport = null;
  state.companyImportError = '';
  onbBusy(state, true, 'Reading…');
  try {
    if (file.size > 524288) throw new Error('Choose a company file smaller than 512 KB.');
    const text = await file.text();
    const preview = await post('/v2/setup/company/preview', {company_yaml: text});
    if (ONB !== state || generation !== state.companyImportGeneration) return;
    state.companyImport = {text, preview};
    state.mode = 'file';
    onbRender(state);
  } catch (error) {
    if (ONB !== state || generation !== state.companyImportGeneration) return;
    onbBusy(state, false, `<span class="err">${esc(errorMessage(error, ''))}</span>`);
  }
}
async function onbUseCompanyFile(state: OnbState) {
  if (state.busy || state.completing || !state.companyImport) return;
  state.completing = true;
  onbBusy(state, true, 'Creating your team…');
  try {
    await onbSave(state);
    const {text, preview} = state.companyImport;
    const record = await post('/v2/setup/company/import', {company_yaml: text, fingerprint: preview.fingerprint});
    if (ONB !== state) return;
    await onbCompleted(state, record);
  } catch (error) {
    state.completing = false;
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
function onbTeamMode(state: OnbState, mode: OnbState['mode']) {
  state.mode = mode;
  if (mode === 'default') { state.companyImport = null; state.companyImportError = ''; }
  onbRender(state);
}

// ----------------------------------------------------------------- What your bots run on
// The models choice while none is made, the computer in one line, and the Claude or ChatGPT subscription Getting started
// recommends (backend/getting_started.py `subscription`) until one is signed in or skipped; on Tico Cloud Done waits for
// one. Done lands on Tasks.
function onbRunHTML(state: OnbState): string {
  const hide = state.reopened ? '<button class="ghost" type="button" id="onb-hide">Don\'t remind me</button>' : '';
  return `${onbRunLinesHTML(state)}${onbActions(state, 'Done', false, hide)}`;
}
function onbRunLinesHTML(state: OnbState): string {
  const sub = state.subscription;
  const subscription = sub && (sub.done || sub.skipped) ? '' : `<div class="onb-line" id="onb-subscription">
      <span class="onb-mark" aria-hidden="true"></span><span class="onb-line-text">
      <button type="button" class="linkish" data-onb-subscription>Add a Claude or ChatGPT subscription</button>
      ${sub?.optional === false ? '' : '<small class="muted">Recommended</small>'}</span></div>`;
  return `<div class="onb-lines" id="onb-run-lines">${onbComputerHTML(state)}${subscription}</div>`;
}
const ONB_CARET = '<span class="nav-icon onb-caret" aria-hidden="true">expand_more</span>';
function onbComputerHTML(state: OnbState): string {
  const online = (state.record.machine.runners || []).filter(runner => runner?.online);
  if (online.length) return `<div class="onb-line" id="onb-machine-status" data-onb-machine="online"><span class="onb-mark ok" aria-hidden="true">✓</span>
    <span class="onb-line-text">${esc(online.map(runner => runner.label || 'This computer').join(', '))}</span></div>`;
  if (state.record.machine.cloud || S.config?.hosted) return `<div class="onb-line" id="onb-machine-status" data-onb-machine="cloud"><span class="onb-mark ok" aria-hidden="true">✓</span>
    <span class="onb-line-text">Tico Cloud computers</span></div>`;
  return `<details class="onb-line onb-computer" id="onb-computer"${state.computerOpen || state.code || state.enrollment ? ' open' : ''}>
    <summary id="onb-machine-status" data-onb-machine="none"><span class="onb-mark" aria-hidden="true"></span><span class="onb-line-text">${esc(onbMachineStatus(state.record))}</span>${ONB_CARET}</summary>
    ${onbEnrollHTML(state)}</details>`;
}
function onbMachineStatus(record: {machine?: {runners?: OnbRunner[]} | null}): string {
  const runners = (record.machine?.runners || []).filter(Boolean);
  if (runners.length) return `${runners[0]!.label || 'This computer'}: waiting for it to answer`;
  return 'Add the computer your bots run on';
}
// One path, numbered: make the code or file, then the lines that use it. Anything that is instead of a line sits
// under Other ways, closed until asked for.
function onbEnrollHTML(state: OnbState): string {
  const linux = state.kind === 'linux', issued = linux ? !!state.code : !!state.enrollment;
  return `<div class="onb-choices" id="onb-kind" role="radiogroup" aria-label="Kind of computer">
      <label><input type="radio" name="onb-kind" data-onb-kind value="mac" ${linux ? '' : 'checked'}>A Mac</label>
      <label><input type="radio" name="onb-kind" data-onb-kind value="linux" ${linux ? 'checked' : ''}>A Linux or cloud server (Docker)</label></div>
    <ol class="onb-steps">
      <li><span class="k">${linux ? 'Create a one-time code for this computer' : "Download this computer's setup file"}</span>
        <div class="onb-enroll-row"><button class="ghost" type="button" id="onb-enroll">${issued ? (linux ? 'New code' : 'Download again') : 'Add computer'}</button></div>
        ${onbIssuedHTML(state)}</li>
      ${onbCommands(state).map((row, i) => row[2] ? '' : onbCommandHTML(row, i)).join('')}
    </ol>
    ${onbOtherWaysHTML(state)}`;
}
// The UI never learns the environment slug the Mac was created with, so it stays literal here.
// The label is the one the setup file was written with, so the command and the file agree.
const onbMachineLabel = (state: {kind?: string} | null | undefined): string => state?.kind === 'linux' && S.config?.local ? 'This computer'
  : `${firstName(S.me?.name) || 'This'}'s ${state?.kind === 'linux' ? 'server' : 'Mac'}`;
function onbSignIns(state: OnbState): OnbSignIn[] {
  const view: SettingsProvidersView = state.providers || {}, preset = view.preset;
  if (preset === 'gateway') return [];
  if (PRESET_PROVIDERS[preset ?? '']) return [{runtime: 'claude', label: 'Claude'}, {runtime: 'codex', label: 'ChatGPT through Codex'}];
  if (preset === 'own-keys') {
    return (view.providers || []).filter(row => !row.gateway && (view.enabled || []).includes(row.id))
      .filter((row, i, rows) => rows.findIndex(other => other.runtime === row.runtime) === i)
      .map(row => ({runtime: row.runtime, label: row.label}));
  }
  const runtime = view.default?.runtime || S.config?.default_runtime || '';
  return runtime ? [{runtime, label: harnessWords(runtime)}] : [];
}
function onbCommands(state: OnbState): OnbCommand[] {
  // The preset can use several subscriptions, independently of the default model. Key-only harnesses have no login.
  const signIns = onbSignIns(state).filter(row => state.kind === 'linux'
    ? harnessCaps(row.runtime).container_login : harnessCaps(row.runtime).terminal_login || harnessCaps(row.runtime).browser_sign_in);
  if (state.kind === 'linux') {
    const [run, , plain] = dockerRunnerCommands(state.code || '<code>', onbMachineLabel(state), '');
    const logins = signIns.map((row): OnbCommand => [`Sign in to ${row.label} (use Sign in in Settings > Computers, or run this after the runner installs the model CLI)`,
      dockerRunnerCommands(state.code || '<code>', onbMachineLabel(state), row.runtime)[1]![1]]);
    return [run, ...logins, ['With plain Docker instead of the installer line. It has no updater, so it will not follow the server\'s releases', plain[1], true]];
  }
  const file = state.enrollment || '<setup-file>.json';
  return [
    ['Register this Mac for the team',
     `scripts/tico-computer -e <slug> enroll --code-file "$HOME/Downloads/${file}" --label "${onbMachineLabel(state)}"`],
    ...signIns.map((row, i): OnbCommand => [`Add your ${row.label} subscription`,
     `${i === 0 ? 'scripts/tico-computer -e <slug> profile add default\n' : ''}scripts/tico-computer -e <slug> profile login default ${row.runtime}`]),
    ['Start the bot service', 'scripts/tico-computer -e <slug> install bot'],
  ];
}
// A label's parenthetical is a hint in small type under it, so each step reads as one short line. The command wraps
// inside its box, with Copy in the box; Copy takes the exact command, line breaks and all.
function onbCommandHTML([label, command]: OnbCommand, i: number, tag = 'li'): string {
  const [, title, hint] = /^(.*?) \((.*)\)$/.exec(label) || [label, label, ''];
  return `<${tag} class="onb-cmd-step"><span class="k">${esc(title)}</span>${hint ? `<small class="onb-hint">${esc(hint)}</small>` : ''}
    <div class="onb-cmd"><code>${esc(command)}</code><button class="ghost" type="button" data-onb-copy="${i}" aria-label="Copy: ${esc(title)}">Copy</button></div></${tag}>`;
}
function onbOtherWaysHTML(state: OnbState): string {
  const other = onbCommands(state).map((row, i) => row[2] ? onbCommandHTML(row, i, 'div') : '').join('');
  const slug = state.kind === 'linux' ? '' : '<p class="muted onb-hint">Replace <code class="mono">&lt;slug&gt;</code> with your environment slug.</p>';
  return slug + (other ? `<details class="onb-other" id="onb-other"${state.otherOpen ? ' open' : ''}><summary>Other ways</summary>${other}</details>` : '');
}
// After Add computer the code is on screen in large type (a toast alone goes before it can be read); a Mac's
// setup file is named where it was saved.
function onbIssuedHTML(state: OnbState): string {
  if (state.kind === 'linux' && state.code) return `<div class="onb-code" id="onb-code" role="status"><span class="k">Your one-time code</span>
    <div class="onb-code-row"><strong class="onb-code-value" id="onb-code-value">${esc(state.code)}</strong><button class="ghost" type="button" id="onb-code-copy">Copy</button></div>
    <small>Works once, for 15 minutes. It is already in the command below.</small></div>`;
  if (state.kind !== 'linux' && state.enrollment) return `<p class="onb-saved" id="onb-code" role="status">Saved to Downloads: <code class="mono">${esc(state.enrollment)}</code></p>`;
  return '';
}
// The run step reads the subscription item and follows the computer coming online.
function onbRunStarted(state: OnbState) {
  void v2Get<GsAnswer>('/v2/setup/getting-started', {fresh: true}).then(answer => {
    if (ONB !== state) return;
    const item = answer?.items?.find(row => row.id === 'subscription') || null;
    if (JSON.stringify(item) === JSON.stringify(state.subscription)) return;
    state.subscription = item;
    if ((onbKey(state) === 'run' || onbKey(state) === 'welcome') && !state.busy) onbRedrawLines(state);
  });
  clearInterval(state.poll ?? undefined);
  state.poll = setInterval(() => { if (!document.hidden) void onbPoll(state); }, 5000);
}
// The lines only: an open enrollment keeps what was picked or typed.
function onbRedrawLines(state: OnbState) {
  const lines = state.dialog?.querySelector('#onb-run-lines');
  // An open enrollment keeps what was picked, until a computer it set up comes online.
  if (!lines || (lines.querySelector('#onb-computer[open]') && !(state.record.machine.runners || []).some(runner => runner?.online))) return;
  lines.innerHTML = onbComputerHTML(state) + (state.subscription && (state.subscription.done || state.subscription.skipped) ? ''
    : lines.querySelector('#onb-subscription')?.outerHTML || '');
  onbWireComputer(state);
}
async function onbPoll(state: OnbState) {
  if (ONB !== state || state.busy) return;
  const record = await v2Get<Partial<OnbRecord>>('/v2/setup');
  if (ONB !== state || !record?.machine) return;
  const was = JSON.stringify(state.record.machine);
  state.record.machine = {...state.record.machine, ...record.machine};
  if (JSON.stringify(state.record.machine) !== was) onbRedrawLines(state);
}

// ----------------------------------------------------------------- wiring
function onbWire(state: OnbState, key: string) {
  const back = $('#onb-back');
  if (back) back.onclick = () => { onbCollect(state, key); state.step = Math.max(0, state.step - 1); onbRender(state); };
  const next = $('#onb-next');
  if (next) next.onclick = () => void onbAdvance(state, key);
  if (key === 'building') ($('#onb-company') || $('#onb-website'))?.focus();
  if (key === 'team') {
    // Back from the builder's first screen or from a file's team is back to the default chart.
    if (state.mode !== 'default' && back) back.onclick = () => onbTeamMode(state, 'default');
    if (state.mode === 'custom') { obWire(state); return; }
    state.dialog?.querySelectorAll<HTMLInputElement>('[data-onb-team]').forEach(input => input.onchange = () => {
      const team = state.record.default_chart?.find(row => row.team === input.dataset.onbTeam);
      if (team) team.on = input.checked;
    });
    const change = $('#onb-change');
    if (change) change.onclick = () => onbTeamMode(state, 'custom');
    const file = $('#onb-company-file'), pick = $('#onb-file-pick');
    if (pick && file) pick.onclick = () => file.click();
    if (file) file.onchange = () => void onbReadCompanyFile(state, file.files?.[0]);
  }
  if (key === 'run' || key === 'welcome') onbWireComputer(state);
  if (key === 'welcome') next?.focus({preventScroll: true});   // the team reads from the top; Start is a key press away
  const hide = $('#onb-hide');
  // For good: Finish setup leaves the sidebar now and stays away (Getting started's dismissal, per person).
  if (hide) hide.onclick = () => { if (GS) GS = {...GS, dismissed: true}; void gsState({checklist: true}); state.dialog?.close(); };
}
function onbWireComputer(state: OnbState) {
  const commands = onbCommands(state);
  state.dialog?.querySelectorAll<HTMLButtonElement>('[data-onb-copy]').forEach(button => button.onclick = () =>
    void copyText(commands[Number(button.dataset.onbCopy)]?.[1] || '').then(() => toast('Command copied')));
  // The same sheet as Settings > Models, over this one; once it has saved, the line reads the item again and goes.
  const plan = $('[data-onb-subscription]');
  if (plan) plan.onclick = () => { planSignIn({onDone: () => onbRunStarted(state)}); };
  const codeCopy = $('#onb-code-copy');
  if (codeCopy) codeCopy.onclick = () => void copyText(state.code || '').then(() => toast('Code copied'));
  const box = $('#onb-computer');
  if (box) box.ontoggle = () => { state.computerOpen = box.open; };
  const other = $('#onb-other');
  if (other) other.ontoggle = () => { state.otherOpen = other.open; };
  state.dialog?.querySelectorAll<HTMLInputElement>('[data-onb-kind]').forEach(input => input.onchange = () => {
    state.kind = input.value; state.enrollment = ''; state.code = ''; onbRedrawComputer(state);
  });
  const enroll = $('#onb-enroll');
  if (enroll) enroll.onclick = async () => {
    enroll.disabled = true;
    try {
      if (state.kind === 'linux') state.code = (await enrollmentCode(S.me?.id || 'owner')).code;
      else state.enrollment = await enrollmentDownload(S.me?.id || 'owner', onbMachineLabel(state));
      if (ONB !== state) return;
      onbRedrawComputer(state);
      toast(state.kind === 'linux' ? 'One-time code created' : 'Setup file downloaded');
    } catch (error) { toast(errorMessage(error, ''), true); enroll.disabled = false; }
  };
}
function onbRedrawComputer(state: OnbState) {
  const box = $('#onb-computer');
  if (!box) return;
  box.outerHTML = onbComputerHTML(state);
  onbWireComputer(state);
}
function onbCollect(state: OnbState, key: string) {
  const r = state.record;
  if (key === 'building' && $('#onb-website')) {
    ssCollectBuilding(state);
    r.names = {...r.names,
      ...($('#onb-company') ? {company_name: $('#onb-company').value.trim()} : {}),
      ...($('#onb-owner') ? {owner_name: $('#onb-owner').value.trim()} : {}),
      ...($('#onb-domain') ? {team_domain: $('#onb-domain').value.trim()} : {})};
  }
  if (key === 'team') {
    if (state.mode === 'custom') obCollect(state);
    else {
      // The default chart: no roles picked (the server lays it out, company_import.default_chart), less the teams off.
      r.selected = {};
      r.answers = {...r.answers, departments: [], briefings: {},
        teams_off: (r.default_chart || []).filter(team => !team.on && !team.locked).map(team => team.team)};
    }
  }
}
function onbBusy(state: OnbState, busy: boolean, message?: string) {
  state.busy = busy;
  state.dialog?.querySelectorAll<HTMLButtonElement | HTMLInputElement>('#onb-next, #onb-back, #onb-change, #onb-file-pick').forEach(el => { el.disabled = busy; });
  const status = $('#onb-status');
  if (status) status.innerHTML = message === undefined ? '' : message;
}
// A save the server refuses for its shape (a field over its limit, a value it does not know) is never the owner's
// to fix, so they read what happened and what to do, not the field path the API names for developers.
const ONB_REFUSED = 'Tico could not save this step. Press the button again to retry; if it keeps happening, reload the page.';
const onbSaveError = (error: unknown): string => `<span class="err">${esc(errorCode(error) === 'validation' ? ONB_REFUSED : errorMessage(error, ''))}</span>`;
async function onbSave(state: OnbState) {
  const saved = await put('/v2/setup', {names: state.record.names, answers: state.record.answers, selected: state.record.selected});
  if (ONB !== state || !saved || typeof saved !== 'object') return saved;
  if (saved.machine) state.record.machine = {...state.record.machine, ...saved.machine};
  // The server stores the website as an https:// address; the research reads that one.
  if (typeof saved.names?.website === 'string') state.record.names.website = saved.names.website;
  if (typeof saved.home === 'string') state.record.home = saved.home;
  if (Array.isArray(saved.default_chart) && state.mode !== 'default') state.record.default_chart = saved.default_chart;
  return saved;
}
// The names typed on the first step are the app's names from the moment they are saved.
function onbApplyNames(state: OnbState) {
  const names = state.record.names;
  applyConfig({...S.config, company_name: names.company_name || S.config.company_name,
               app_name: names.app_name || S.config.app_name,
               assistant_name: names.assistant_name || S.config.assistant_name});
  S.emps = namedRoster(S.emps);
  renderTree();
}
// The owner's name is on the roster from the save: the org chart, the sidebar and the computer's label use it.
async function onbRefreshOwner(state: OnbState) {
  const name = state.record.names.owner_name;
  if (!name || !$('#onb-owner')) return;
  S.me = {...S.me, name} as SelfPerson;
  const roster = await get('/humans').catch(() => null);
  if (ONB !== state || !roster?.people) return;
  setPeople(roster);
  renderTree();
}
async function onbAdvance(state: OnbState, key: string) {
  if (state.busy) return;
  onbCollect(state, key);
  if (key === 'building') {
    // The field is empty when the server has no name, and an empty one would quietly name the team "Company".
    if ($('#onb-company') && !state.record.names.company_name) {
      onbBusy(state, false, '<span class="err">Add your company\'s name.</span>');
      $('#onb-company').focus();
      return;
    }
    onbBusy(state, true, 'Saving…');
    try {
      await onbSave(state);
      if (ONB !== state) return;
      // The research drafts the strategy in the background; only its refusal of the address keeps the owner here.
      const refused = await ssResearch(state);
      if (ONB !== state) return;
      if (refused) { onbBusy(state, false, `<span class="err">${esc(refused)}</span>`); $('#onb-website')?.focus(); return; }
      onbApplyNames(state); void onbRefreshOwner(state);
      state.step += 1;
      onbRender(state);
    } catch (error) {
      if (ONB !== state) return;
      onbBusy(state, false, onbSaveError(error));
    }
    return;
  }
  if (key === 'team') {
    if (state.mode === 'file') { void onbUseCompanyFile(state); return; }
    if (state.mode === 'custom' && !obCanAdvance(state)) return;
    void onbFinish(state);
    return;
  }
  // On Tico Cloud the bots need a plan or key of the company's own (getting_started `subscription`, not optional there).
  if (state.subscription?.optional === false && !state.subscription.done) { onbBusy(state, false, 'Add a plan first'); return; }
  onbDone(state);
}
// Create: the team as chosen, then the last step.
async function onbFinish(state: OnbState) {
  if (state.busy || state.completing) return;
  state.completing = true;
  onbBusy(state, true, 'Creating your team…');
  try {
    await onbSave(state);
    const record = await post('/v2/setup/complete', {});
    if (ONB !== state) return;
    await onbCompleted(state, record);
  } catch (error) {
    state.completing = false;
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
async function onbCompleted(state: OnbState, record: Partial<OnbRecord> | null | undefined) {
  onbAdopt(state, record);
  onbApplyNames(state);
  // The owner who set the team up has seen it made: the tour does not start over the app next visit (account menu has it).
  void gsState({tour: true});
  // Completing setup marks What's new read for the owner: take the server's count before the full nav comes back. A
  // slow or failed read never holds the nav back; a late answer still lands.
  const done = (config: AppConfig) => ({...config, onboarding_needed: !!state.record.needed});
  const fresh = get('/v2/config').catch(() => null);
  const config = await Promise.race([fresh, new Promise(resolve => setTimeout(resolve, 2500, null))]);
  if (ONB !== state) return;
  applyConfig(done(config || S.config));
  if (!config) void fresh.then(late => { if (late && ONB === state) applyConfig(done(late)); });
  state.completing = false;
  state.step = ONB_STEPS.indexOf('run');
  onbRender(state);
  onbRunStarted(state);
  await refresh(true);                      // the new bots in the sidebar, behind the modal
}
// Done: Tasks. The model plan is a default (Settings > Models), not a step.
function onbDone(state: OnbState) {
  state.dialog?.close();
}

// ----------------------------------------------------------------- Welcome (a team from the company file)
// A company whose team came in from the company file at install (hosted signup) has nothing to set up: the owner's first
// visit is one screen in the same modal, in place of the tour: the team as it is, what the bots run on, Start.
// `held`: the setup record the caller already read (ui/getting-started.ts reads it before loading this part).
async function onbWelcome(held?: Partial<OnbRecord> | null): Promise<boolean> {
  if (S.me?.role !== 'owner' || ONB) return false;
  const record = held !== undefined ? held : await get<Partial<OnbRecord> | null>('/v2/setup').catch(() => null);
  if (!record?.completed || !record.company_import || ONB || S.route === WELCOME) return false;
  const state: OnbState = {step: 0, steps: ['welcome'], providers: null, models: [], record: JSON.parse(JSON.stringify(ONB_BLANK)),
    catalog: catalogState([]), busy: false, completing: false, poll: null, org: obBlank(), mode: 'default',
    enrollment: '', kind: onbDefaultKind(), strat: ssBlank(), subscription: null, dialog: null};
  ONB = state;
  onbAdopt(state, record);
  onbOpen(state);
  onbRender(state);
  onbRunStarted(state);
  return true;
}
function onbWelcomeHTML(state: OnbState): string {
  const teams = onbLiveTeams();
  return `${teams.length ? `<h3 class="onb-step-title">${esc(ONB_TITLES.team)}</h3>
    <div class="onb-teams" role="list" aria-label="Teams">${teams.map(team => `<div class="onb-team" role="listitem" data-onb-team-row="${esc(team.team)}">
      <span class="onb-team-text"><strong>${esc(team.team)}</strong>${onbTeamRolesHTML(team)}</span>
      <span class="onb-team-n tnum" aria-hidden="true">${team.roles.length}</span></div>`).join('')}</div>` : ''}
    <h3 class="onb-step-title">${esc(ONB_TITLES.run)}</h3>${onbRunLinesHTML(state)}${onbActions(state, 'Start', false)}`;
}
// The chart as the app has it (S.orgGroups, S.emps, S.seats), team by team in the chart's order: the team's lead first,
// then who reports to whom; an open seat is muted. The built-ins' own group comes last, under the name the server gives
// it (setup's team step shows it the same way); a group with no name is not shown, so its id never reaches the screen.
function onbLiveTeams(): OnbTeam[] {
  // A line may name a bot by id or by slug (`bot:` or bare; a company file's bots have ids apart from their slugs):
  // every role is keyed by its id, so a line finds it either way.
  const ids = new Map((S.emps || []).flatMap(bot => [[bot.id, bot.id], [bot.slug || bot.id, bot.id]] as [string, string][]));
  const idOf = (ref: string | null | undefined) => { const key = String(ref || '').replace(/^bot:/, ''); return ids.get(key) || key; };
  const last = (group: {id: string}) => Number(group.id === BUILT_IN_GROUP);
  const groups = [...(S.orgGroups || [])].filter(group => group.name || group.id !== BUILT_IN_GROUP)
    .sort((a, b) => last(a) - last(b) || (a.order ?? 1e9) - (b.order ?? 1e9));
  return groups.map(group => {
    const bots = (S.emps || []).filter(bot => bot.team === group.id)
      .map(bot => ({id: bot.id, title: botDisplayName(bot.id), live: true, reports_to: idOf(bot.reports_to) || null}));
    const seats = (S.seats || []).filter(seat => seat.team === group.id)
      .map(seat => ({id: 'seat:' + seat.id, title: seat.title, live: false, reports_to: idOf(seat.reports_to) || null}));
    const all = [...bots, ...seats];
    // A group with no lead (General: no bot leads it) still reads from its head: the role most of the team reports to.
    const reports = (id: string) => all.filter(role => role.reports_to === id).length;
    const lead = idOf(group.lead) || [...all].sort((a, b) => reports(b.id) - reports(a.id))[0]?.id;
    const roles = all.sort((a, b) => Number(b.id === lead) - Number(a.id === lead));
    return {team: group.name || group.id, on: true, roles};
  }).filter(team => team.roles.length);
}
