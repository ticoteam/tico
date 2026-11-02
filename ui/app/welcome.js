/* ui/app/welcome.js — First run: the #/welcome wizard
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- first run (#/welcome)
// A team's first hour is its own flow, not a settings page. Say what the team is building and confirm the bets the
// research proposes (ui/setup-strategy.js); name the app and the team; say what the team does; build the team chart
// group by group (ui/org-builder.js); enroll this Mac; connect your own agent if you have one; finish.
// Every Next saves a draft, so a closed tab loses nothing. There is no assistant
// to name in the wizard: humans talk to it in the Assistant tab on their own page, act through their own agent, or use a bot's Chat tab.
const ONB_STEPS = ['building', 'bets', 'names', 'about', 'org', 'machine', 'agent', 'review'];
// A team that has not chosen its AI providers starts there (the research needs a model); state.steps is fixed at load, so the
// step does not vanish from the list the moment it is saved.
const onbStepList = needsProviders => needsProviders ? ['providers', ...ONB_STEPS] : ONB_STEPS;
const ONB_TITLES = {providers: 'AI providers', building: 'What are you building', bets: 'Your bets', names: 'Names', about: 'About the team',
                    org: 'Your team chart', machine: 'Add the computer that runs your bots', agent: 'Connect an external agent', review: 'Review and create'};
// The progress row's words: one each, so the row stays on one line (a phone shows the current one only).
const ONB_SHORT = {providers: 'Providers', building: 'Building', bets: 'Bets', names: 'Names', about: 'About', org: 'Team', machine: 'Computer', agent: 'Agent', review: 'Review'};
// At most one short hint line under a step's title; most steps have none.
const ONB_BLURB = {providers: 'Choose the subscriptions or API keys your bots use, with Tico Gateway if you want it.',
                   bets: 'A draft: nothing is published.', agent: 'Optional.'};
const ONB_CUSTOMERS = [['businesses', 'Businesses'], ['consumers', 'Consumers'], ['both', 'Both']];
// The four the server accepts (models.WorkArrival). Adding one here needs the same word there.
const ONB_ARRIVES = [['email', 'Email'], ['slack', 'Slack'], ['crm', 'CRM'], ['tickets', 'Tickets']];
// Kept with a number in them: a human reads the answer, and an older record's chooser read the largest number.
const ONB_SIZES = ['1 (just me)', '2-10', '11-50', '51-200', '201+'];
const ONB_BLANK = {
  names: {company_name: '', app_name: '', assistant_name: '', owner_name: '', team_domain: '', website: ''},
  answers: {building: '', what_we_do: '', customers: '', team_size: '', work_arrives: [], repetitive_work: '',
            software_product: '', departments: [], briefings: {}},
  selected: {}, completed: null, home: '', bots: [], machine: {runners: [], enrolled: false}, needed: true};
let ONB = null;
// A server that is a container is the usual install, and the computer that runs bots is a Linux one beside it.
const onbDefaultKind = () => (S.config?.in_docker ? 'linux' : 'mac');
// Their own name: the roster's when it is not just an address, else the sign-in's display name when the proxy sends one.
const onbOwnerGuess = () => (S.me?.name && !/@/.test(S.me.name) ? S.me.name : S.me?.sign_in_name || '');
// A team whose owner signs in with public mail (Gmail) and has no company address on the roster is asked its email domain, once.
const onbAsksDomain = () => S.me?.role === 'owner' && !(S.me.company_domains || []).length;
function onbStop() { if (ONB) { clearInterval(ONB.poll); clearTimeout(ONB.strat.timer); } ONB = null; }
function pageWelcome() {
  // Only the owner may set a team up; everyone else has no reason to see the address either.
  if (S.me && S.me.role !== 'owner') { location.hash = TASKS; return; }
  const state = ONB = {step: 0, steps: ONB_STEPS, providers: null, models: [], record: JSON.parse(JSON.stringify(ONB_BLANK)), catalog: catalogState([]),
                       revisions: {}, busy: false, completing: false, poll: null, org: obBlank(),
                       enrollment: '', error: '', kind: onbDefaultKind(), strat: ssBlank()};
  $('#main').innerHTML = '<div class="onb" id="onb"><div class="empty">Loading…</div></div>';
  void onbLoad(state);
}
async function onbLoad(state) {
  try {
    const [record, catalog, roster, providers, models] = await Promise.all([
      get('/v2/setup'), get('/v2/templates'), get('/humans').catch(() => ({people: []})),
      get('/v2/providers').catch(() => null), get('/v2/models').catch(() => ({models: []}))]);
    if (ONB !== state) return;
    state.providers = providers && Array.isArray(providers.providers) ? providers : null;
    state.models = models.models || [];
    if (models.harnesses) HARNESS_ROWS = models.harnesses;
    state.steps = onbStepList(!!state.providers && !state.providers.configured);
    if ((roster.people || []).length) SETTINGS_DATA.people = roster.people;
    state.catalog = catalogState(catalogCards(catalog));
    onbAdopt(state, record);
    if (state.record.completed) { state.step = state.steps.length; await onbRevisions(state); }
    if (ONB !== state) return;
    onbRender(state);
    state.poll = setInterval(() => { if (!document.hidden) void onbPoll(state); }, state.record.completed ? 4000 : 10000);
  } catch (error) {
    if (ONB !== state) return;
    const host = $('#onb');
    if (host) host.innerHTML = `<section class="card"><h2>Setup could not be loaded</h2><p class="err">${esc(error.message)}</p></section>`;
  }
}
function onbAdopt(state, record) {
  const blank = JSON.parse(JSON.stringify(ONB_BLANK)), row = record && typeof record === 'object' ? record : {};
  state.record = {...blank, ...row,
    names: {...blank.names, ...(row.names || {})},
    answers: {...blank.answers, ...(row.answers || {})},
    selected: {...(row.selected || {})}, home: row.home || '',
    bots: row.bots || [],
    machine: {...blank.machine, ...(row.machine || {})}};
  delete state.record.answers.never_without_person;
  // The question shows once this server's setup record carries the answer, so an older server never refuses the save.
  state.asksBusinessType = 'business_type' in row;
  const names = state.record.names;
  // The server's own "Company" (no TICO_TEAM_NAME) is no one's name: the field starts empty, with a placeholder.
  if (!names.company_name && !S.config.company_name_default) names.company_name = S.config.company_name || '';
  if (!names.app_name) names.app_name = appName();
  if (!names.owner_name) names.owner_name = S.config.owner_name || onbOwnerGuess();
  // Never the team's name: that was an earlier release's default, and it made the Review step list the assistant as the team.
  if (!names.assistant_name || names.assistant_name.trim().toLowerCase() === (names.company_name || '').trim().toLowerCase()) names.assistant_name = assistantName();
  onbSeedPicks(state);
}
function onbSeedPicks(state) {
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
      const person = match && catalogPeople().find(row => (row.email || '').toLowerCase() === match[1].toLowerCase());
      if (person) edits[slug].person = person.id;
    }
  }
  onbApplyRecommended(state);
}
// The required cards are not a choice. Everything else on the team is checked on the org chart (ui/org-builder.js).
function onbApplyRecommended(state) {
  for (const card of state.catalog.cards) if (card.required) state.catalog.picked.add(card.slug);
}
function onbRender(state) {
  if (ONB !== state) return;
  const host = $('#onb'); if (!host) return;
  state.busy = false;                       // a fresh screen is never mid-save

  const done = state.step >= state.steps.length, key = done ? 'done' : state.steps[state.step];
  host.classList.toggle('onb-org', key === 'org');
  host.classList.toggle('onb-wide', key === 'bets');
  const heading = done ? 'Your team is created' : ONB_TITLES[key];
  host.innerHTML = `<div class="onb-head"><div class="onb-brand">${brandLogo()}</div><h1>${esc(heading)}</h1>${ONB_BLURB[key] ? `<p data-onb-hint>${esc(ONB_BLURB[key])}</p>` : ''}</div>
    ${done ? '' : `<ol class="onb-progress" aria-label="Setup steps">${state.steps.map((step, i) =>
      `<li class="${i < state.step ? 'done' : i === state.step ? 'cur' : ''}"${i === state.step ? ' aria-current="step"' : ''}><span>${esc(ONB_SHORT[step])}</span></li>`).join('')}</ol>
      <p class="onb-count sr-only" id="onb-count">Step ${state.step + 1} of ${state.steps.length}</p>`}
    ${key === 'providers' ? '' : onbSampleHTML(state, done) + onbCompanyFileHTML(state, done)}
    <section class="card${key === 'org' ? ' ob-host' : ''}" id="onb-step" data-onb-step="${key}">${onbStepHTML(state, key)}</section>
    ${key === 'providers' ? onbSampleHTML(state, done) + onbCompanyFileHTML(state, done) : ''}`;
  onbWire(state, key);
}
function onbStepHTML(state, key) {
  const r = state.record, names = r.names, a = r.answers;
  const back = state.step > 0 ? '<button class="ghost" type="button" id="onb-back">Back</button>' : '';
  const actions = label => `<div class="onb-actions">${back}<button class="primary" type="button" id="onb-next">${label}</button>
    <span class="spacer"></span><span class="muted" id="onb-status"></span><button class="ghost" type="button" data-setup-help="wizard">Want help setting up?</button></div>`;
  if (key === 'providers') return `${providersFormHTML(state.providers, state.models, 'onb-prov')}
    ${actions(state.providers.enabled?.length ? 'Next' : 'Skip for now')}`;
  if (key === 'building') return ssBuildingHTML(state, actions);
  if (key === 'bets') return ssBetsHTML(state);
  if (key === 'names') return `
    <label class="onb-field"><span class="k">Company name</span>
      <input type="text" id="onb-company" maxlength="120" value="${esc(names.company_name)}" placeholder="Acme Inc." autocomplete="organization"></label>
    <label class="onb-field"><span class="k">App name</span>
      <input type="text" id="onb-app" maxlength="60" value="${esc(names.app_name)}"></label>
    <label class="onb-field"><span class="k">Your name</span>
      <input type="text" id="onb-owner" maxlength="100" value="${esc(names.owner_name)}" placeholder="Optional" autocomplete="name"></label>
    ${onbAsksDomain() ? `<label class="onb-field"><span class="k">Team email domain</span>
      <input type="text" id="onb-domain" maxlength="100" value="${esc(names.team_domain)}" placeholder="Optional, such as acme.com" autocomplete="off" inputmode="url"></label>` : ''}
    ${actions('Next')}`;
  if (key === 'about') return `
    <label class="onb-field"><span class="k">What you do</span>
      <textarea id="onb-what" maxlength="2000" placeholder="We rent out short-stay apartments and sell the software that runs them.">${esc(a.what_we_do || a.building.slice(0, 2000))}</textarea></label>
    <div class="onb-field"><span class="k">Who you sell to</span>
      <div class="onb-choices">${ONB_CUSTOMERS.map(([value, label]) =>
        `<label><input type="radio" name="onb-customers" value="${value}" ${a.customers === value ? 'checked' : ''}>${label}</label>`).join('')}</div></div>
    <div class="onb-field"><span class="k">Is software your product?</span>
      <div class="onb-choices">${[['yes', 'Yes'], ['no', 'No']].map(([value, label]) =>
        `<label><input type="radio" name="onb-software" value="${value}" ${a.software_product === value ? 'checked' : ''}>${label}</label>`).join('')}</div></div>
    ${state.asksBusinessType ? `<label class="onb-field"><span class="k">Business type</span>
      <select id="onb-business-type">${grOpts(SB_BUSINESS_TYPES, r.business_type || '', 'Not saying')}</select></label>` : ''}
    <label class="onb-field"><span class="k">Team size</span>
      <select id="onb-size"><option value="">Not saying</option>${ONB_SIZES.map(size =>
        `<option value="${esc(size)}" ${a.team_size === size ? 'selected' : ''}>${esc(size)}</option>`).join('')}</select></label>
    ${actions('Next')}`;
  if (key === 'org') return obStepHTML(state);
  if (key === 'machine') {
    // A runner that is already online (the server's own, installed with Tico) needs no setup: the step is one line.
    const online = (state.record.machine?.runners || []).find(runner => runner && runner.online);
    const linux = state.kind === 'linux';
    const issued = linux ? !!state.code : !!state.enrollment;
    // One path, numbered: make the code or file, then the lines that use it. Anything that is instead of a line sits
    // under Other ways, closed until asked for.
    const setup = `
    <div class="onb-choices" id="onb-kind" role="radiogroup" aria-label="Kind of computer">
      <label><input type="radio" name="onb-kind" data-onb-kind value="mac" ${linux ? '' : 'checked'}>A Mac</label>
      <label><input type="radio" name="onb-kind" data-onb-kind value="linux" ${linux ? 'checked' : ''}>A Linux or cloud server (Docker)</label></div>
    <ol class="onb-steps">
      <li><span class="k">${linux ? 'Create a one-time code for this computer' : "Download this computer's setup file"}</span>
        <div class="onb-enroll-row"><button class="${issued ? 'ghost' : 'primary'}" type="button" id="onb-enroll">${issued ? (linux ? 'New code' : 'Download again') : 'Add computer'}</button>
          ${issued ? '' : `<small id="onb-enroll-note">${linux ? 'A 15-minute code.' : 'A 15-minute setup file.'}</small>`}</div>
        ${onbIssuedHTML(state)}</li>
      ${onbCommandsHTML(state)}
    </ol>
    ${onbOtherWaysHTML(state)}`;
    const status = `<p class="onb-machine" id="onb-machine-status" role="status">${esc(onbMachineStatus(state.record))}</p>`;
    return online
      ? `${status}
    ${actions('Next')}`
      : `${setup}
    ${status}
    ${actions('Next')}`;
  }
  if (key === 'agent') return `
    <p class="onb-lead">Already use Claude, ChatGPT or another MCP agent? Connect it to talk to your team from there. You can also do this later from the top bar.</p>
    <p><button class="ghost" type="button" id="onb-connect">Connect an external agent</button></p>
    ${actions('Next')}`;
  if (key === 'review') return `${onbSummaryHTML(state)}
    <div class="onb-actions">${back}<button class="primary" type="button" id="onb-finish">Create my team</button>
      <span class="spacer"></span><span class="muted" id="onb-status"></span></div>`;
  return onbDoneHTML(state);
}
// Before the first answer is saved, the owner may skip the wizard and start as Tico's sample company instead
// (POST /v2/setup/sample, backend/sample_company.py): five bots with short first tasks, on the first screen only.
function onbSampleHTML(state, done) {
  if (done || state.step !== 0 || state.record.completed) return '';
  return `<div class="onb-sample" id="onb-sample-box"><p><strong>Just trying Tico?</strong>
    <span class="muted">Start with a made-up company: five bots plus the built-in helpers. Each of the five has a short first task that ends by asking you to approve it. Nothing is sent outside.</span></p>
    <button class="ghost" type="button" id="onb-sample">Start with a sample company</button></div>`;
}
const onbImportPending = fields => (fields || []).map(field => ({goals: 'goals', tool_connections: 'tool connections',
  first_jobs: 'first jobs', model_choices: 'model choices', routines: 'automations',
  open_seats: 'open seats', coworkers: 'coworkers'}[field] || field)).join(', ');
// The company file chosen at install that could not be imported (backend/company_import.py `start`): said in words,
// on every step, until an import works. No sample team took its place.
function onbImportFailedHTML(state) {
  const failed = state.record.company_import_failed;
  return failed ? `<section class="card onb-import-failed" id="onb-import-failed" role="alert"><h2>Your chosen org chart wasn't imported</h2>
    <p>${esc(failed.reason || '')}</p><p class="muted">No sample team was set up in its place.${state.record.completed
      ? ' This company is already set up, so the file can\'t replace its team.'
      : ' Import the company file again below, or pick your team here.'}</p>
    <button class="ghost" type="button" id="onb-import-failed-dismiss">Dismiss</button></section>` : '';
}
// Dismiss is the way out when the owner won't fix the file by importing it (Health links here for it).
async function onbDismissImportFailure(state, button) {
  button.disabled = true;
  try {
    const record = await post('/v2/setup/company/import-failure/dismiss', {});
    if (ONB !== state) return;
    state.record.company_import_failed = record.company_import_failed;
    onbRender(state);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}
function onbCompanyFileHTML(state, done) {
  if (done) {
    const imported = state.record.company_import, seats = imported?.seats || 0;
    return onbImportFailedHTML(state) + (imported ? `<section class="card"><p>Your chosen org chart is laid out${seats
      ? `: ${seats} ${seats === 1 ? 'role' : 'roles'} marked Recruiting, each waiting for a bot` : ''}.</p>${imported.deferred?.length
      ? `<p class="muted">Saved for later setup: ${esc(onbImportPending(imported.deferred))}.</p>` : ''}${(imported.people || []).some(p => !p.invited_at)
      ? '<p class="muted">Invite the people it names from <a href="#/settings/people">Settings › Humans</a>.</p>' : ''}</section>` : '');
  }
  if (state.step !== 0 || state.record.completed) return onbImportFailedHTML(state);
  const preview = state.companyImport?.preview;
  return `${onbImportFailedHTML(state)}<section class="onb-sample" id="onb-company-file-box"><p><strong>Already chose your team?</strong>
    Import the company YAML you downloaded from Tico setup.</p>
    <label class="onb-field"><span class="k">Company YAML file</span><input type="file" id="onb-company-file" accept=".yaml,.yml"></label>
    <p id="onb-company-file-status" role="status">${esc(state.companyImportError || '')}</p>
    ${preview ? `<h3>${esc(preview.company_name)}</h3><ul>${preview.employees.map(bot =>
      `<li>${esc(bot.title)} — works from day one</li>`).join('')}${(preview.seats || []).map(seat =>
      `<li>${esc(seat.title)} — <span class="pill waiting">Recruiting</span></li>`).join('')}</ul>
      <p class="muted">Tico's built-in bots work from day one. Every other role is a Recruiting seat until you pick a bot for it.</p>
      ${preview.deferred.length ? `<p>These choices are saved for later setup: ${esc(onbImportPending(preview.deferred))}.</p>` : ''}
      <button class="primary" type="button" id="onb-company-file-use">Use this team</button>` : ''}</section>`;
}
async function onbReadCompanyFile(state, file) {
  if (!file || state.busy || state.completing) return;
  const generation = state.companyImportGeneration = (state.companyImportGeneration || 0) + 1;
  state.companyImport = null;
  state.companyImportError = '';
  try {
    if (file.size > 524288) throw new Error('Choose a company YAML file smaller than 512 KB.');
    const text = await file.text();
    const preview = await post('/v2/setup/company/preview', {company_yaml: text});
    if (ONB !== state || generation !== state.companyImportGeneration) return;
    state.companyImport = {text, preview};
  } catch (error) {
    if (ONB !== state || generation !== state.companyImportGeneration) return;
    state.companyImportError = error.message;
  }
  onbRender(state);
}
async function onbUseCompanyFile(state) {
  if (state.busy || state.completing || !state.companyImport) return;
  state.completing = true;
  onbBusy(state, true, 'Creating your selected team…');
  try {
    const {text, preview} = state.companyImport;
    const record = await post('/v2/setup/company/import', {company_yaml: text, fingerprint: preview.fingerprint});
    if (ONB !== state) return;
    onbAdopt(state, record);
    onbApplyNames(state);
    await onbCompleted(state, record);
  } catch (error) {
    state.completing = false;
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
// The UI never learns the environment slug the Mac was created with, so it stays literal here.
// The label is the one the setup file was written with, so the command and the file agree.
const onbMachineLabel = state => state?.kind === 'linux' && S.config?.local ? 'This computer'
  : `${firstName(S.me?.name) || 'This'}'s ${state?.kind === 'linux' ? 'server' : 'Mac'}`;
// [label, command, other]: `other` marks a line that is instead of one above, shown under Other ways.
function onbSignIns(state) {
  const view = state.providers || {}, preset = view.preset;
  if (preset === 'gateway') return [];
  if (PRESET_PROVIDERS[preset]) return [{runtime: 'claude', label: 'Claude'}, {runtime: 'codex', label: 'ChatGPT through Codex'}];
  if (preset === 'own-keys') {
    return (view.providers || []).filter(row => !row.gateway && (view.enabled || []).includes(row.id))
      .filter((row, i, rows) => rows.findIndex(other => other.runtime === row.runtime) === i)
      .map(row => ({runtime: row.runtime, label: row.label}));
  }
  const runtime = view.default?.runtime || S.config?.default_runtime || '';
  return runtime ? [{runtime, label: harnessWords(runtime)}] : [];
}
function onbCommands(state) {
  // The preset can use several subscriptions, independently of the default model. Key-only harnesses have no login.
  const signIns = onbSignIns(state).filter(row => state.kind === 'linux'
    ? harnessCaps(row.runtime).container_login : harnessCaps(row.runtime).terminal_login || harnessCaps(row.runtime).browser_sign_in);
  if (state.kind === 'linux') {
    const [run, , plain] = dockerRunnerCommands(state.code || '<code>', onbMachineLabel(state), '');
    const logins = signIns.map(row => [`Sign in to ${row.label} (use Sign in in Settings > Computers, or run this after the runner installs the model CLI)`,
      dockerRunnerCommands(state.code || '<code>', onbMachineLabel(state), row.runtime)[1][1]]);
    return [run, ...logins, ['With plain Docker instead of the installer line. It has no updater, so it will not follow the server\'s releases', plain[1], true]];
  }
  const file = state.enrollment || '<setup-file>.json';
  return [
    ['Register this Mac for the team',
     `scripts/tico -e <slug> enroll --code-file "$HOME/Downloads/${file}" --label "${onbMachineLabel(state)}"`],
    ...signIns.map((row, i) => [`Add your ${row.label} subscription`,
     `${i === 0 ? 'scripts/tico -e <slug> profile add default\n' : ''}scripts/tico -e <slug> profile login default ${row.runtime}`]),
    ['Start the bot service', 'scripts/tico -e <slug> install bot'],
  ].filter(Boolean);
}
// A label's parenthetical is a hint in small type under it, so each step reads as one short line. The command wraps
// inside its box (never past the card), with Copy in the box; Copy takes the exact command, line breaks and all.
function onbCommandHTML([label, command], i, tag = 'li') {
  const [, title, hint] = /^(.*?) \((.*)\)$/.exec(label) || [label, label, ''];
  return `<${tag} class="onb-cmd-step"><span class="k">${esc(title)}</span>${hint ? `<small class="onb-hint">${esc(hint)}</small>` : ''}
    <div class="onb-cmd"><code>${esc(command)}</code><button class="ghost" type="button" data-onb-copy="${i}" aria-label="Copy: ${esc(title)}">Copy</button></div></${tag}>`;
}
function onbCommandsHTML(state) {
  return onbCommands(state).map((row, i) => row[2] ? '' : onbCommandHTML(row, i)).join('');
}
function onbOtherWaysHTML(state) {
  const other = onbCommands(state).map((row, i) => row[2] ? onbCommandHTML(row, i, 'div') : '').join('');
  const slug = state.kind === 'linux' ? '' : '<p class="muted" style="font-size:12.5px">Replace <code class="mono">&lt;slug&gt;</code> with your environment slug.</p>';
  return slug + (other ? `<details class="onb-other" id="onb-other"${state.otherOpen ? ' open' : ''}><summary>Other ways</summary>${other}</details>` : '');
}
// After Add computer the code is on screen in large type (a toast alone goes before it can be read); a Mac's
// setup file is named where it was saved.
function onbIssuedHTML(state) {
  if (state.kind === 'linux' && state.code) return `<div class="onb-code" id="onb-code" role="status"><span class="k">Your one-time code</span>
    <div class="onb-code-row"><strong class="onb-code-value" id="onb-code-value">${esc(state.code)}</strong><button class="ghost" type="button" id="onb-code-copy">Copy</button></div>
    <small>Works once, for 15 minutes. It is already in the command below.</small></div>`;
  if (state.kind !== 'linux' && state.enrollment) return `<p class="onb-file" id="onb-code" role="status">Saved to Downloads: <code class="mono">${esc(state.enrollment)}</code></p>`;
  return '';
}
function onbMachineStatus(record) {
  const runners = (record.machine?.runners || []).filter(Boolean);
  const online = runners.filter(runner => runner.online);
  if (online.length) return `${online.map(runner => runner.label || 'This computer').join(', ')} online`;
  if (runners.length) return `${runners[0].label || 'This computer'} enrolled · waiting for it to answer`;
  return 'No computer enrolled yet';
}
function onbSummaryHTML(state) {
  const r = state.record, a = r.answers;
  const label = (key, value) => `<div><span class="k">${esc(key)}</span><span>${esc(value || '—')}</span></div>`;
  return `<div class="onb-summary">
    ${label('Building', a.building)}${r.names.website ? label('Website', r.names.website) : ''}
    ${label('Team', r.names.company_name)}${label('App', r.names.app_name)}
    ${label('What you do', a.what_we_do)}
    ${label('Sells to', ONB_CUSTOMERS.find(([value]) => value === a.customers)?.[1] || '')}
    ${label('Software is the product', a.software_product ? (a.software_product === 'yes' ? 'Yes' : 'No') : '')}
    ${label('Business type', SB_BUSINESS_TYPES.find(([value]) => value === r.business_type)?.[1] || '')}
    ${label('Team size', a.team_size)}
    ${label('AI providers', PROVIDER_PRESETS.find(row => row.id === state.providers?.preset)?.label
      || (state.providers?.providers || []).filter(row => (state.providers.enabled || []).includes(row.id)).map(row => row.label).join(', ')
      || 'Choose later in Settings > Computers')}
    ${label('Computers', onbMachineStatus(r))}
    ${S.config?.visuals === 'media' ? label('Pages', 'Open cards. Tico file hosting or a visual address shows them inline.') : ''}
    ${frSummaryTeamHTML(state)}
  </div>`;
}
// The tour waits for this screen to be seen: Show me around starts it, and otherwise the next visit does (ui/getting-started.js).
function onbDoneHTML(state) {
  return `<div class="onb-next" id="onb-next-step">${frNextStepHTML(state)}</div>
    <div id="onb-bots">${onbBotRowsHTML(state)}</div>
    <div id="onb-after">${frNextHTML(state)}</div>
    <div class="onb-actions"><button class="${frWaiting(state) ? 'ghost' : 'primary'}" type="button" id="onb-tasks">Open Tasks</button>
      <span class="spacer"></span><span class="muted" id="onb-status"></span><button class="ghost" type="button" id="onb-tour" data-gs-tour>Show me around</button></div>`;
}
function onbBotRowsHTML(state) {
  const chosen = new Set(Object.keys(state.record.selected || {}));
  const groups = frBotGroups((state.record.bots || []).filter(bot => !chosen.size || chosen.has(bot.slug)));
  const sample = state.record.sample;
  const tasks = sample?.tasks || [];
  const starter = sample ? `<p class="fr-sample-created">Five bots plus the built-in helpers.</p>${tasks.length ?
    `<details class="fr-starter-tasks"><summary>${tasks.length} starter ${tasks.length === 1 ? 'task' : 'tasks'} created</summary><ul>${tasks.map((id, i) =>
      `<li><a href="#/task/${encodeURIComponent(id)}">Starter task ${i + 1}</a></li>`).join('')}</ul></details>` : ''}` : '';
  const rows = groups.map(group => {
    const content = group.bots.map(bot => frBotRowHTML(state, bot)).join('');
    return group.id === 'built-in' ? `<details class="onb-group onb-builtins" data-onb-group="built-in"><summary>Built-in helpers: ${group.bots.map(bot => esc(bot.display_name || bot.slug)).join(', ')}</summary>${content}</details>`
      : `<section class="onb-group"${group.id ? ` data-onb-group="${esc(group.id)}"` : ''}>${group.name ? `<h2>${esc(group.name)}</h2>` : ''}${content}</section>`;
  }).join('');
  return starter + (rows || '<div class="empty">No bots were created.</div>');
}
// The poll redraws only the progress rows: the forms below them keep what was typed.
function onbRenderDone(state) {
  const host = $('#onb-step');
  if (!host || host.dataset.onbStep !== 'done') return;
  const rows = $('#onb-bots');
  if (!rows) { host.innerHTML = onbDoneHTML(state); onbWire(state, 'done'); return; }
  const open = [...rows.querySelectorAll('details')].map(el => el.open);
  rows.innerHTML = onbBotRowsHTML(state);
  rows.querySelectorAll('details').forEach((el, i) => { el.open = open[i] || false; });
  // The next step is redrawn only when it changes, so keyboard focus on its button survives a poll.
  const next = $('#onb-next-step'), step = document.createElement('div');
  step.innerHTML = frNextStepHTML(state);
  if (next && next.innerHTML !== step.innerHTML) next.innerHTML = step.innerHTML;
  const tasks = $('#onb-tasks'); if (tasks) tasks.className = frWaiting(state) ? 'ghost' : 'primary';
  document.querySelectorAll('[data-onb-activate]').forEach(button => button.onclick = () =>
    void onbActivate(state, button.dataset.onbActivate, button));
  frWireDone(state);
}
function onbWire(state, key) {
  const back = $('#onb-back');
  if (back) back.onclick = () => { onbCollect(state, key); state.step = Math.max(0, state.step - 1); onbRender(state); };
  const next = $('#onb-next');
  if (next) next.onclick = () => void onbAdvance(state, key);
  if (key === 'providers') {
    const root = $('#onb-prov');
    providersWire(root, state.providers, state.models);
    root.addEventListener('change', () => { $('#onb-next').textContent = providersCollect(root).enabled.length ? 'Next' : 'Skip for now'; });
  }
  if (key === 'org') obWire(state);
  if (key === 'bets') void ssEnter(state);
  if (key === 'machine') {
    const commands = onbCommands(state);
    document.querySelectorAll('[data-onb-copy]').forEach(button => button.onclick = () =>
      void copyText(commands[Number(button.dataset.onbCopy)]?.[1] || '').then(() => toast('Command copied')));
    const codeCopy = $('#onb-code-copy');
    if (codeCopy) codeCopy.onclick = () => void copyText(state.code || '').then(() => toast('Code copied'));
    const other = $('#onb-other');
    if (other) other.ontoggle = () => { state.otherOpen = other.open; };
    document.querySelectorAll('[data-onb-kind]').forEach(input => input.onchange = () => {
      state.kind = input.value; state.enrollment = ''; state.code = ''; onbRender(state);
    });
    const enroll = $('#onb-enroll');
    if (enroll) enroll.onclick = async () => {
      enroll.disabled = true;
      try {
        if (state.kind === 'linux') state.code = (await enrollmentCode(S.me?.id || 'owner')).code;
        else state.enrollment = await enrollmentDownload(S.me?.id || 'owner', onbMachineLabel(state));
        if (ONB !== state) return;
        onbRender(state);
        toast(state.kind === 'linux' ? 'One-time code created' : 'Setup file downloaded');
      } catch (error) { toast(error.message, true); }
      finally { if ($('#onb-enroll')) $('#onb-enroll').disabled = false; }
    };
  }
  const finish = $('#onb-finish');
  if (finish) finish.onclick = () => void onbFinish(state);
  const sample = $('#onb-sample');
  if (sample) sample.onclick = () => void onbSample(state);
  const companyFile = $('#onb-company-file');
  if (companyFile) companyFile.onchange = () => void onbReadCompanyFile(state, companyFile.files?.[0]);
  const useCompanyFile = $('#onb-company-file-use');
  if (useCompanyFile) useCompanyFile.onclick = () => void onbUseCompanyFile(state);
  const dismissFailure = $('#onb-import-failed-dismiss');
  if (dismissFailure) dismissFailure.onclick = () => void onbDismissImportFailure(state, dismissFailure);
  const tasks = $('#onb-tasks');
  if (tasks) tasks.onclick = () => { location.hash = TASKS; };
  const connect = $('#onb-connect');
  if (connect) connect.onclick = () => window.connectAgent?.();
  document.querySelectorAll('[data-onb-activate]').forEach(button => button.onclick = () =>
    void onbActivate(state, button.dataset.onbActivate, button));
  if (key === 'done') { frWireDone(state); frWireNext(state); }
}
function onbCollect(state, key) {
  const r = state.record;
  if (key === 'building' && $('#onb-website')) ssCollectBuilding(state);
  // Back from the bets saves What we won't do as Next would, unless the research is still running.
  if (key === 'bets' && !ssRunning(state)) void ssSaveNot(state);
  if (key === 'names' && $('#onb-company')) r.names = {
    ...r.names, company_name: $('#onb-company').value.trim(), app_name: $('#onb-app').value.trim(),
    owner_name: ($('#onb-owner')?.value || '').trim(),
    ...($('#onb-domain') ? {team_domain: $('#onb-domain').value.trim()} : {})};
  // The answers this page no longer asks (where work arrives, repetitive work) are kept as they were saved.
  if (key === 'about' && $('#onb-what')) r.answers = {...r.answers,
    what_we_do: $('#onb-what').value.trim(),
    customers: $('input[name=onb-customers]:checked')?.value || '',
    software_product: $('input[name=onb-software]:checked')?.value || '',
    team_size: $('#onb-size').value};
  if (key === 'about' && $('#onb-business-type')) r.business_type = $('#onb-business-type').value || null;
  frCollect(state, key);
}
function onbBusy(state, busy, message) {
  state.busy = busy;
  for (const id of ['#onb-next', '#onb-back', '#onb-finish', '#onb-sample', '#onb-company-file', '#onb-company-file-use']) { const el = $(id); if (el) el.disabled = busy; }
  const status = $('#onb-status');
  if (status) status.innerHTML = message === undefined ? '' : message;
}
// A save the server refuses for its shape (a field over its limit, a value it does not know) is never the owner's
// to fix, so they read what happened and what to do, not the field path the API names for developers.
const ONB_REFUSED = 'Tico could not save this step, so it has not moved on. Your choices are still here: press the button again to retry. If it keeps happening, reload the page and tell Tico support what you picked.';
const onbSaveError = error => `<span class="err">${esc(error?.body?.error?.code === 'validation' ? ONB_REFUSED : error.message)}</span>`;
async function onbSave(state) {
  const saved = await put('/v2/setup', {names: state.record.names, answers: state.record.answers, selected: state.record.selected,
    ...(state.record.business_type ? {business_type: state.record.business_type} : {})});
  if (ONB !== state || !saved || typeof saved !== 'object') return saved;
  if (saved.machine) state.record.machine = {...state.record.machine, ...saved.machine};
  // The server stores the website as an https:// address; the research reads that one.
  if (typeof saved.names?.website === 'string') state.record.names.website = saved.names.website;
  if (typeof saved.home === 'string') state.record.home = saved.home;
  return saved;
}
// The names typed on the first screen are the app's names from the moment they are saved.
function onbApplyNames(state) {
  const names = state.record.names;
  applyConfig({...S.config, company_name: names.company_name || S.config.company_name,
               app_name: names.app_name || S.config.app_name,
               assistant_name: names.assistant_name || S.config.assistant_name});
  S.emps = namedRoster(S.emps);
  renderTree();
}
// The owner's name is on the roster from the save: the org chart, the sidebar and the computer's label use it.
async function onbRefreshOwner(state) {
  const name = state.record.names.owner_name;
  if (!name) return;
  S.me = {...S.me, name};
  const roster = await get('/humans').catch(() => null);
  if (ONB !== state || !roster?.people) return;
  setPeople(roster);
  renderTree();
}
async function onbAdvance(state, key) {
  if (state.busy) return;
  onbCollect(state, key);
  // The field starts empty now, and an empty one would quietly name the team "Company".
  if (key === 'names' && !state.record.names.company_name) {
    $('#onb-status').innerHTML = `<span class="err">Add your company's name.</span>`;
    $('#onb-company').focus();
    return;
  }
  if (key === 'providers') {
    const chosen = providersCollect($('#onb-prov'));
    // Nothing ticked is fine: the team is created first and its bots run once a provider is added (Settings > Computers).
    if (!chosen.enabled.length && !state.providers.enabled?.length) {
      state.providers = {...state.providers, enabled: [], default: {runtime: '', model: ''}};
      state.step += 1; onbRender(state); return;
    }
    onbBusy(state, true, 'Saving…');
    try {
      state.providers = await providersSave(state.providers, chosen);
      if (ONB !== state) return;
      applyConfig({...S.config, providers_configured: !!state.providers.enabled?.length});
      state.step += 1;
      onbRender(state);
    } catch (error) {
      if (ONB !== state) return;
      onbBusy(state, false, onbSaveError(error));
    }
    return;
  }
  if (key === 'names' && !state.record.names.app_name) { onbBusy(state, false, '<span class="err">Give the app a name.</span>'); return; }
  if (key === 'org' && !obCanAdvance(state)) { onbBusy(state, false, ''); return; }
  if (key === 'bets') {
    onbBusy(state, true, 'Saving…');
    const ok = await ssLeave(state);
    if (ONB !== state) return;
    if (!ok) { onbBusy(state, false, ''); ssGate(state); ssPoll(state); return; }
  }
  onbBusy(state, true, 'Saving…');
  try {
    await onbSave(state);
    if (ONB !== state) return;
    if (key === 'building') {
      const refused = await ssResearch(state);
      if (ONB !== state) return;
      if (refused) { onbBusy(state, false, `<span class="err">${esc(refused)}</span>`); $('#onb-website')?.focus(); return; }
    }
    if (key === 'names') { onbApplyNames(state); void onbRefreshOwner(state); }
    state.step = Math.min(state.steps.length - 1, state.step + 1);
    onbRender(state);
  } catch (error) {
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
async function onbFinish(state) {
  if (state.busy || state.completing) return;
  state.completing = true;
  onbCollect(state, 'review');
  onbBusy(state, true, 'Creating your team…');
  try {
    await onbSave(state);
    const record = await post('/v2/setup/complete', {});
    if (ONB !== state) return;
    onbApplyNames(state);
    await onbCompleted(state, record);
  } catch (error) {
    state.completing = false;
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
// The sample company is the whole first run done at once: its names, providers and bots come back from the server.
async function onbSample(state) {
  if (state.busy || state.completing) return;
  state.completing = true;
  onbBusy(state, true, 'Starting the sample company…');
  try {
    const record = await post('/v2/setup/sample', {});
    if (ONB !== state) return;
    onbAdopt(state, record);
    onbApplyNames(state);
    applyConfig({...S.config, providers_configured: true});
    await onbCompleted(state, record);
  } catch (error) {
    state.completing = false;
    if (ONB !== state) return;
    onbBusy(state, false, onbSaveError(error));
  }
}
async function onbCompleted(state, record) {
  onbAdopt(state, record);
  // Completing setup marks What's new read for the owner: take the server's count before the full nav comes back. A
  // slow or failed read never holds the nav back; a late answer still lands.
  const done = config => ({...config, onboarding_needed: !!state.record.needed});
  const fresh = get('/v2/config').catch(() => null);
  const config = await Promise.race([fresh, new Promise(resolve => setTimeout(resolve, 2500, null))]);
  if (ONB !== state) return;
  applyConfig(done(config || S.config));
  if (!config) void fresh.then(late => { if (late && ONB === state) applyConfig(done(late)); });
  const providers = await get('/v2/providers').catch(() => null);
  if (providers && Array.isArray(providers.providers)) state.providers = providers;
  state.step = state.steps.length;
  await onbRevisions(state);
  if (ONB !== state) return;
  clearInterval(state.poll); state.poll = setInterval(() => { if (!document.hidden) void onbPoll(state); }, 4000);   // repositories appear within seconds
  await refresh(true);                      // the new bots on the chart first: the screen lists them in its groups
  if (ONB !== state) return;
  onbRender(state);
}
async function onbRevisions(state) {
  const bots = await v2Get('/v2/bots?include_archived=1');
  if (ONB !== state || !Array.isArray(bots)) return;
  state.revisions = Object.fromEntries(bots.map(bot => [bot.slug, bot.revision]));
}
async function onbPoll(state) {
  if (ONB !== state || state.busy) return;
  const record = await v2Get('/v2/setup');
  if (ONB !== state || !record) return;
  if (record.machine) state.record.machine = {...state.record.machine, ...record.machine};
  if (Array.isArray(record.bots)) state.record.bots = record.bots;
  const status = $('#onb-machine-status');
  if (status) status.textContent = onbMachineStatus(state.record);
  if (state.step >= state.steps.length) { await onbRevisions(state); onbRenderDone(state); }
}
async function onbActivate(state, slug, button) {
  button.disabled = true;
  const bot = (state.record.bots || []).find(row => row.slug === slug);
  try {
    await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {status: 'active', expected_revision: state.revisions[slug]});
    if (bot) bot.status = 'active';
    toast(`${bot?.display_name || slug} is active`);
    await onbRevisions(state);
    if (ONB !== state) return;
    onbRenderDone(state);
    await refresh(true);
  } catch (error) { toast(error.message, true); button.disabled = false; }
}
