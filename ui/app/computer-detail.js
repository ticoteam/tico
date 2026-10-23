/* ui/app/computer-detail.js — Settings > Computers: each computer's AI connections (the chips on its row) and the
   pop-up a row opens: Connections, Screen, Bots on this computer, Versions and updates, Settings.
   A connection is a named subscription signed in on the computer, or the computer's own login for a tool; a chip
   exists only for one that was added (signed in, or bots run on it), never for a tool a login was not meant for.
   Usage always reads as what is left. Classic script: its globals are shared with the other files under ui/app/,
   loaded in the order index.html lists them. */
'use strict';

const machineOnline = machine => !machine.revoked_at && !!machine.last_seen && Date.now() - new Date(machine.last_seen) < 60000;
const machineToolName = (machine, runtime) => Object.values(machine.readiness?.harnesses || {}).find(tool => tool.runtime === runtime)?.name
  || harnessRow(runtime)?.name || harnessWords(runtime);
// Whether the computer has reported its tools yet: before its first report the server sends empty `runtimes`.
const machineReported = machine => Object.keys(machine.readiness?.runtimes || {}).length > 0 || Object.keys(machine.readiness?.harnesses || {}).length > 0;
// A computer that has not reported what it has yet, or a sign-in it has not reported back (ui/model-login.js).
const machineWaiting = machine => !machine.revoked_at && (!machineReported(machine) || (window.machineLoginPending?.get(machine.id) || 0) > Date.now());
const machineReset = reset => new Date(reset).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'});
const machineEmail = account => String(account?.email || '').trim().toLowerCase();
const machineNewer = (a, b) => (Date.parse(a?.reported_at) || 0) > (Date.parse(b?.reported_at) || 0);

// The computer's connections, one per AI tool: [{key, runtime, name, own, state, bots, blocked, attention}]. Every bot
// runs each tool on the computer's one login for it; a different account means another computer. `state` carries the
// reading shown ({signed_in, account, weekly, five_hour}); `auth` and `value` the login's readiness. `blocked` is a
// login whose turns can't save a session (readiness `data_problem`, Muse Code's data folder): signed in, but no turn
// runs until that folder is fixed.
function machineConnections(machine) {
  const runtimes = machine.readiness?.runtimes || {};
  const links = machine.bot_connections || [];
  const rows = [];
  // A tool a shared key serves is listed even when the computer has not reported that tool (or anything) yet.
  for (const runtime of [...new Set([...Object.keys(runtimes), ...Object.keys(machine.shared_with || {})])]) {
    const reported = runtimes[runtime];
    // A retained named default is the computer's login too; older reports may carry its state below `profiles`.
    const own = machine.readiness?.computer_profile;
    const selected = reported?.profiles?.[own ?? ''];
    const value = selected ? {...reported, ...selected} : reported;
    const bots = links.filter(b => b.runtime === runtime).map(b => b.bot);
    const auth = value?.authenticated;
    // A shared key that serves this computer (`shared_with`, backend/computer_keys.py serving) is a connection here
    // even before the tool reports it signed in; until it does, the line says so and claims no reading.
    const shared = machine.shared_with?.[runtime] || null;
    const blocked = value?.data_problem || reported?.data_problem || '';
    const warning = links.find(b => b.runtime === runtime && b.warning)?.warning || '';
    // Signed out from Tico (Sign out) stays a line, so it can sign in again or go back to the shared key.
    if (!(auth === 'ready' || auth === 'rejected' || bots.length || machineSignedOut(machine, runtime) || shared)) continue;
    const reading = {signed_in: auth === 'ready' ? true : auth === 'rejected' || (auth === 'missing' && !shared) ? false : null,
      account: value?.account, weekly: value?.weekly, five_hour: value?.five_hour};
    rows.push({key: `own:${runtime}`, runtime, name: shared ? 'Shared key' : "Computer's login", own: true, auth, value, state: reading, bots, shared, blocked, warning});
  }
  for (const r of rows) {
    // Signed out from Tico is a red dot even with no bots on it yet: nothing signs it back in by itself.
    const cleared = r.state.signed_in === false && machineSignedOut(machine, r.runtime, '');
    r.keyCleared = cleared && machineKeyCleared(machine, r.runtime);
    r.attention = !!((r.bots.length && r.state.signed_in === false) || cleared || r.auth === 'rejected' || r.blocked);
    r.tool = machineToolName(machine, r.runtime);
  }
  return rows.sort((a, b) => a.tool.localeCompare(b.tool));
}
// The connections to show: before the computer's first report, only the shared keys that serve it (nothing else is
// known yet, and a bot's subscription it has not reported is not missing).
const machineShown = machine => machineReported(machine) ? machineConnections(machine) : machineConnections(machine).filter(conn => conn.shared);

// What is left of the weekly allowance, as a whole percent; null with no current reading.
function machineLeft(weekly) {
  const reset = Date.parse(weekly?.resets_at);
  if (!Number.isFinite(weekly?.used_percent) || (Number.isFinite(reset) && reset <= Date.now())) return null;
  return Math.round(100 - Math.min(100, Math.max(0, weekly.used_percent)));
}
// "33% left · resets Thu 12:06 AM", or what is known when there is no reading.
function machineLeftWords(weekly) {
  const left = machineLeft(weekly), reset = Date.parse(weekly?.resets_at);
  if (left === null) return weekly ? 'Previous week\'s reading; refresh needed' : 'No usage reading yet';
  return [`${left}% left`, Number.isFinite(reset) ? `resets ${machineReset(reset)}` : ''].filter(Boolean).join(' · ');
}
function machineAttentionWords(conn) {
  if (conn.blocked) return conn.blocked;
  if (conn.auth === 'rejected') return 'Login expired or rejected';
  if (!conn.attention) return '';
  const what = conn.keyCleared ? 'No key' : 'Signed out';
  return conn.bots.length ? `${what}; bots use it` : what;
}
// "Shared with Alpha, Bravo", or "Shared with every computer (…)" for the "Every computer" key in Credentials.
function machineSharedWords(shared) {
  const names = (shared.computers || []).map(c => c.label).join(', ');
  return shared.every ? `Shared with every computer${names ? ` (${names})` : ''}` : `Shared with ${names}`;
}
// One chip per connection: the tool, a thin bar and the % left; a red dot only when it needs attention.
function machineChipHTML(machine, conn) {
  const left = machineLeft(conn.state.weekly), warn = machineAttentionWords(conn);
  const title = [`${conn.tool} · ${conn.name}`, conn.shared ? machineSharedWords(conn.shared) : '', warn, machineLeftWords(conn.state.weekly)].filter(Boolean).join(' · ');
  return `<button type="button" class="machine-chip${conn.attention ? ' attention' : ''}" data-computer-open="${esc(machine.id)}" data-chip="${esc(conn.key)}" title="${esc(title)}" aria-label="${esc(title)}">`
    + `${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<span>${esc(conn.tool)}</span>`
    + `${left !== null ? `<meter class="machine-chip-bar" min="0" max="100" low="20" optimum="100" value="${left}" aria-hidden="true"></meter><span class="machine-chip-pct">${left}%</span>` : ''}</button>`;
}
function machineChipsHTML(machine) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  const waiting = machineWaiting(machine) ? `<span class="machine-chip waiting" data-machine-waiting>Waiting for ${esc(machine.label)}</span>` : '';
  const conns = machineShown(machine);
  if (!machineReported(machine)) return conns.length ? `<div class="machine-chips">${conns.map(conn => machineChipHTML(machine, conn)).join('')}${waiting}</div>` : waiting;
  const chips = conns.map(conn => machineChipHTML(machine, conn)).join('')
    || `<button type="button" class="machine-chip attention" data-computer-open="${esc(machine.id)}" data-no-connection><span class="machine-chip-dot" aria-hidden="true"></span><span>No AI connection</span></button>`;
  return `<div class="machine-chips">${chips}${waiting}${machineRowAddHTML(machine)}</div>`;
}
// One Add per computer, after its chips: the same Add connection pop-up as in the computer's own.
function machineRowAddHTML(machine) {
  if (machine.revoked_at || !machineCanSignIn(machine)) return '';
  const online = machineOnline(machine);
  return `<button type="button" class="machine-chip machine-chip-add" data-add-connection="${esc(machine.id)}" aria-label="Add connection to ${esc(machine.label)}"${online ? '' : ' disabled title="Computer is offline"'}>+ Add</button>`;
}

// ---- the pop-up
function machineConnectionHTML(machine, conn, online, manage) {
  const kind = conn.shared ? `API key${conn.shared.name ? ` · ${conn.shared.name}` : ''}` : "Computer's login";
  const tags = conn.shared ? '<span class="pill computer-conn-shared" data-shared>Shared</span>' : '';
  let signIn;
  // Signed in, but its turns can't save a session: the folder is named on screen, and no sign-in is offered for it.
  if (conn.blocked) signIn = `<span class="err machine-data-problem" data-data-problem="${esc(conn.runtime)}">${esc(conn.blocked)}</span>`;
  // A shared key the tool has not reported signing in with yet: nothing is claimed until it has.
  else if (conn.shared && conn.auth !== 'ready' && conn.auth !== 'rejected') signIn = `<span class="muted" data-shared-waiting>Not checked on ${esc(machine.label)} yet; it is used from the computer's next key check</span>`;
  else signIn = machineSignInHTML(machine, online, conn.runtime, conn.auth, conn.value)
    + (conn.auth === 'ready' ? machineKeyHTML(machine, conn.runtime, conn.value?.key) : '');
  const offline = online ? '' : ' disabled title="Computer is offline"';
  const sep = '<span class="computer-conn-sep" aria-hidden="true"> · </span>';
  // "No key · Add key" for a key cleared from here: adding one is the way back (a sign-in or the shared key also are).
  // Not on a shared key's line: Replace key / Clear key act on it there.
  const addKey = manage && !conn.blocked && !conn.shared && conn.state.signed_in !== true;
  let action = addKey && conn.keyCleared ? sep + machineKeyActions(machine, online, conn.runtime, null, false).trimStart() : '';
  if (manage && conn.state.signed_in !== true && !conn.blocked) {
    // "Signed out · Sign in": the way back sits next to what is wrong.
    if (harnessCaps(conn.runtime).browser_sign_in) action += `${conn.state.signed_in === false && !conn.keyCleared ? sep : ' '}<button class="ghost machine-signin" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(conn.runtime)}" data-machine="${esc(machine.label)}"${offline}>Sign in</button>`;
    else action += machineTerminalSignInHTML(machine, conn.runtime);
  }
  // Switch account / Sign out (or Use the shared key) per login, from ui/model-login.js; disabled offline. On the
  // computer's own login a key signs in, Replace key / Clear key stand in for them.
  const key = (conn.value?.key || conn.shared) && harnessCaps(conn.runtime).api_keys === 'computer';
  const signedIn = conn.auth === 'ready' || !!conn.blocked;
  action += !manage ? ''
    : conn.shared && key ? machineKeyActions(machine, online, conn.runtime, conn.value?.key || {name: '', source: 'shared'}, true)
    : signedIn && key ? machineKeyActions(machine, online, conn.runtime, conn.value.key, true)
    : machineLoginActions(machine, online, conn.runtime, '', signedIn);
  // Add key: a key of the computer's own, for a tool that takes one (Dev 97).
  if (addKey && !conn.keyCleared) action += machineKeyActions(machine, online, conn.runtime, null, false);
  const value = conn.value || {};
  const rejected = conn.auth === 'rejected'
    ? `<span class="err machine-rejected" data-rejected="${esc(conn.runtime)}">${value.rejected_at ? esc(ago(value.rejected_at)) + '. ' : ''}${value.rejected_reason ? esc(value.rejected_reason) + '. ' : ''}${hlCredentialFix(value.credential_source)}. It takes no work that needs ${esc(conn.runtime)} until then.</span>` : '';
  const usage = conn.state.signed_in === false ? '' : machineUsageHTML(conn.runtime, conn.state, `${conn.tool} ${conn.name}`, 'this computer');
  const bots = conn.bots.length ? `Bots: ${conn.bots.map(settingsBotName).join(', ')}` : 'No bots run on it';
  // Bots that take turns on one sign-in say so.
  const shared = machineSharedLoginHTML(machine, conn.runtime);
  return `<div class="computer-conn${conn.attention ? ' attention' : ''}" data-connection="${esc(conn.key)}" data-machine-tool="${esc(conn.runtime)}">
    <div class="computer-conn-head">${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<strong>${esc(conn.tool)}</strong><span>${esc(kind)}</span>${tags}</div>
    <div class="computer-conn-signin">${signIn}${action}${rejected}</div>
    ${usage ? `<div class="computer-conn-usage">${usage}</div>` : ''}
    ${conn.warning ? `<p class="computer-conn-warning" role="note" data-login-warning="${esc(conn.runtime)}">${esc(conn.warning)}</p>` : ''}
    ${conn.shared ? `<div class="computer-conn-shared-with muted" data-shared-with>${esc(machineSharedWords(conn.shared))}</div>` : ''}
    <div class="computer-conn-bots muted">${esc(bots)}</div>${shared}
  </div>`;
}
// A tool with no sign-in in the browser: the command to run in a terminal on the computer (Tico need not reach it), or
// where its keys go.
function machineTerminalSignInHTML(machine, runtime) {
  const command = machineLoginCommand(machine, runtime);
  if (command) return ` <span class="machine-login-command" title="Run on the computer${command.includes('<container>') ? '; docker ps shows the container name' : ''}"><code>${esc(command)}</code><button class="ghost machine-signin" type="button" data-machine-copy-login="${esc(command)}">Copy</button></span>`;
  return harnessCaps(runtime).api_keys === 'bot' ? ' <a class="machine-signin-link" href="#/credentials">Add key</a>' : '';
}
// Add connection: one button at the end of Connections, opening its own pop-up (computerAddOpen).
function machineAddHTML(machine, online, manage) {
  if (!manage || machine.revoked_at) return '';
  return `<div class="computer-add"><button class="ghost" type="button" data-add-connection="${esc(machine.id)}"${online ? '' : ' disabled title="Computer is offline"'}>Add connection</button></div>`;
}
// The Bots page, showing this computer's bots: where a bot is moved to another computer.
function computerBotsPage(id) {
  $('#set-machines dialog.computer-detail')?.close();
  SETTINGS_BOTS_VIEW.computer = id; settingsBotsRemember(); settingsShow('bots'); renderSettingsBots();
}
// ---- the Add connection pop-up: 1 the tool, 2 subscription or API key, then the provider's sign-in of the computer's
// one login for that tool in this same pop-up (ui/model-login.js open `into`), and the chip appears once the computer
// reports it. A different account for the same tool means another computer: a bot has no sign-in of its own.
function computerAddTools(machine) {
  const runtimes = machine.readiness?.runtimes || {};
  return Object.entries(runtimes).filter(([, value]) => value?.installed).map(([runtime]) => ({runtime, name: machineToolName(machine, runtime)}))
    .sort((a, b) => a.name.localeCompare(b.name));
}
function computerAddStepHTML(machine, step) {
  const head = (title, back) => `<header><h2 id="computer-add-title">${title}</h2>
      <button class="ghost tmodal-x" type="button" data-add-close aria-label="Close">✕</button></header>
    <p class="muted computer-add-where">${esc(machine.label)}${back ? ` · <button class="ghost machine-signin" type="button" data-add-back="${back}">Back</button>` : ''}</p>`;
  if (step.at === 'tool') {
    const tools = computerAddTools(machine).map(tool => `<button class="ghost computer-add-choice" type="button" data-add-tool="${esc(tool.runtime)}">${esc(tool.name)}</button>`).join('');
    // Installing is the owner's (Versions and updates > Installation and updates).
    return `${head('Add connection')}<p class="model-login-body">Which AI tool?</p><div class="computer-add-choices" data-add-step="tool">${tools || '<p class="muted">No AI tools reported on this computer yet.</p>'}
      <button class="ghost computer-add-choice" type="button" data-add-install>Install another tool</button></div>`;
  }
  const tool = machineToolName(machine, step.runtime), caps = harnessCaps(step.runtime);
  const own = caps.browser_sign_in && machine.readiness?.runtimes?.[step.runtime]?.authenticated !== 'ready';
  // A tool that signs in only from a terminal: its command, to run on the computer (Tico need not reach it).
  const terminal = !caps.browser_sign_in && machineLoginCommand(machine, step.runtime) ? machineTerminalSignInHTML(machine, step.runtime) : '';
  if (step.at === 'kind') {
    // The computer has one login per tool, used by all its bots: Subscription signs that login in, once.
    const why = !caps.browser_sign_in ? `${esc(tool)} has no subscription sign-in from here`
      : own ? 'Sign in with a ChatGPT, Claude or other plan account, as this computer\'s login'
      : `${esc(tool)} is already signed in here; for a different account, add another computer`;
    return `${head(`Add ${esc(tool)}`, 'tool')}<p class="model-login-body">How does ${esc(tool)} sign in here?</p><div class="computer-add-choices" data-add-step="kind">
      <button class="ghost computer-add-choice" type="button" data-add-own${own ? '' : ' disabled'}><strong>Subscription</strong><span class="muted">${why}</span></button>
      <button class="ghost computer-add-choice" type="button" data-add-kind="api_key"><strong>API key</strong><span class="muted">Paste a key from the provider</span></button></div>
      ${terminal ? `<p class="muted computer-add-terminal" data-add-terminal="${esc(step.runtime)}">Or sign the computer's login in from a terminal there:${terminal}</p>` : ''}`;
  }
  if (step.at === 'api_key') {
    // A key of this computer's own (Dev 97, ui/model-login.js replaceKey), for a tool that takes one; a login its key
    // already signs in keeps Replace key on its line. Any other tool's key goes in Credentials.
    const line = machineConnections(machine).find(conn => conn.runtime === step.runtime && conn.own);
    const keyed = line?.auth === 'ready' && line.value?.key;
    const add = keyed ? '' : machineKeyActions(machine, true, step.runtime, null, false);
    const body = keyed ? `${esc(tool)} already signs in with a key on ${esc(machine.label)}: Replace key is on its line.`
      : add ? `Add a key that ${esc(machine.label)} uses for ${esc(tool)}.`
      : `${esc(tool)} takes its key from Credentials; it is used on the computers it is shared with.`;
    const go = add ? `<span class="computer-add-terminal" data-add-key="${esc(step.runtime)}">${add}</span>`
      : keyed ? '' : '<a class="primary" href="#/credentials" data-add-credentials>Open Credentials</a>';
    return `${head(`Add ${esc(tool)} with an API key`, 'kind')}<div data-add-step="api_key"><p class="model-login-body">${body}</p>
      <div class="model-login-actions"><button class="ghost" type="button" data-add-close>Close</button>${go}</div></div>`;
  }
  return '';
}
function computerAddOpen(id) {
  const machine = () => (SETTINGS_DATA?.machines || []).find(m => m.id === id);
  if (!machine()) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal model-login computer-add-dialog';
  dialog.setAttribute('aria-labelledby', 'computer-add-title');
  document.body.appendChild(dialog);
  let step = {at: 'tool'}, signingIn = false;
  const paint = () => { dialog.innerHTML = computerAddStepHTML(machine(), step); dialog.querySelector('button:not([disabled]), input')?.focus(); };
  const signIn = () => {
    signingIn = true;     // the sign-in owns the pop-up from here (its own Close cancels a sign-in still running)
    window.TicoModelLogin?.open({runnerId: id, runtime: step.runtime, machine: machine().label, profile: '', into: dialog});
  };
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('cancel', event => { if (signingIn) return; event.preventDefault(); dialog.close(); });
  dialog.addEventListener('click', event => {
    if (signingIn) return;
    if (event.target === dialog || event.target.closest('[data-add-close]')) { dialog.close(); return; }
    if (event.target.closest('[data-add-credentials]')) { dialog.close(); $('#set-machines dialog.computer-detail')?.close(); return; }
    // Add key opens its own pop-up (ui/model-login.js listens on the document); this one gives way to it.
    if (event.target.closest('[data-model-key-add]')) { dialog.close(); return; }
    const copy = event.target.closest('[data-machine-copy-login]');
    if (copy) { void copyText(copy.dataset.machineCopyLogin).then(() => toast('Command copied')); return; }
    const back = event.target.closest('[data-add-back]');
    if (back) { step = {...step, at: back.dataset.addBack}; paint(); return; }
    const tool = event.target.closest('[data-add-tool]');
    if (tool) { step = {at: 'kind', runtime: tool.dataset.addTool}; paint(); return; }
    if (event.target.closest('[data-add-install]')) {
      dialog.close();
      const versions = $('#set-machines dialog.computer-detail [data-detail-section="versions"]');
      const details = versions?.querySelector('details[data-machine-details]');
      if (details) details.open = true;
      versions?.scrollIntoView({block: 'start'});
      if (S.me?.role !== 'owner') toast('The owner installs AI tools, under Versions and updates', false);
      return;
    }
    const kind = event.target.closest('[data-add-kind]');
    if (kind && !kind.disabled) { step = {...step, at: 'api_key'}; paint(); return; }
    const own = event.target.closest('[data-add-own]');
    if (own && !own.disabled) signIn();
  });
  paint();
  dialog.showModal();
  return dialog;
}
function machineBotsHTML(machine) {
  const links = new Map((machine.bot_connections || []).map(line => [line.bot, line]));
  const rows = (machine.bots || []).map(bot => {
    const line = links.get(bot);
    return `<li data-detail-bot="${esc(bot)}"><strong>${esc(settingsBotName(bot))}</strong>${line ? ` <span class="muted">${subsBotLineHTML(line, true)}</span>` : ''}</li>`;
  }).join('');
  return `${rows ? `<ul class="computer-detail-bots">${rows}</ul>` : '<p class="muted">No bots on this computer.</p>'}
    <a href="#/settings" data-computer-bots="${esc(machine.id)}">Assign on the Bots page</a>`;
}
// Each AI tool's version, then the owner's install and update actions.
function machineVersionsHTML(machine, online) {
  const runtimes = machine.readiness?.runtimes || {};
  const tools = Object.entries(machine.readiness?.harnesses || {}).map(([id, tool]) => ({...tool, id}));
  const toolFor = name => tools.find(tool => tool.runtime === name && tool.installed) || tools.find(tool => tool.runtime === name) || {};
  const needed = Array.isArray(machine.needed_runtimes) ? new Set(machine.needed_runtimes) : null;
  const names = [...new Set([...Object.keys(runtimes), ...tools.filter(tool => tool.installed).map(tool => tool.runtime)])]
    .filter(name => !needed || needed.has(name) || runtimes[name]?.installed || toolFor(name).installed);
  const choices = machine.harness_choices || {};
  // What its bots run on, fallbacks included, stays on (the server refuses an off for it); an older server sends
  // no bot_runtimes, only its bots' own runtimes.
  const botRuntimes = new Set(machine.bot_runtimes || ((machine.bots || []).length ? machine.needed_runtimes || [] : []));
  const rows = names.map(name => {
    const tool = toolFor(name), value = runtimes[name] || {};
    const installed = value.installed || tool.installed, version = installed ? (tool.version || value.version || '') : '';
    return `<div class="machine-tool" role="row" data-machine-tool="${esc(name)}">
      <span role="cell" class="machine-tool-name">${esc(tool.name || harnessWords(name))}</span>
      <span role="cell" class="machine-tool-version"${version ? ` title="${esc(version)}"` : ''}>${installed ? esc(version || 'Installed') : '<span class="muted">Not installed</span>'}</span></div>`;
  }).join('');
  // Whether a tool is kept installed is the owner's toggle under Installation and updates, not a column here.
  const table = rows ? `<div class="machine-tools machine-tools-2" role="table" aria-label="AI tools on ${esc(machine.label)}"><div class="machine-tool machine-tool-head" role="row">
    <span role="columnheader">Tool</span><span role="columnheader">Version</span></div>${rows}</div>` : '<p class="muted">No AI tools reported.</p>';
  // The owner's Update / Pin actions and on/off per tool; the computer applies them between turns
  // (backend/harness_actions.py, runner-rs/src/harness_tools.rs).
  const asked = new Set((machine.harness_actions || []).filter(row => ['requested', 'running'].includes(row.state)).map(row => `${row.harness}:${row.action}`));
  const harnessAction = (id, action, label) => {
    const waiting = asked.has(`${id}:${action}`);
    return ` <button class="ghost" type="button" data-harness-action="${esc(action)}" data-runner="${esc(machine.id)}" data-harness="${esc(id)}"${waiting ? ' disabled' : ''}>${waiting ? 'Requested' : esc(label)}</button>`;
  };
  const harnessChip = ([id, value]) => {
    const owner = S.me?.role === 'owner' && online && value.managed;
    const busy = value.state !== 'idle';
    const text = `${value.name || id} · ${value.installed ? `Installed${value.version ? ' ' + value.version : ''}` : 'Not installed'}${value.pinned ? ' · pinned' : ''}${value.update_available ? ` · ${value.latest || 'update'} available` : ''}${busy ? ` · ${value.state}` : ''}`;
    const tone = value.state === 'failed' ? 'fail' : !value.installed || busy || value.update_available ? 'waiting' : '';
    const buttons = owner && value.installed && !busy ? [
      value.update_available ? harnessAction(id, 'update', 'Update') : '',
      value.pinned ? harnessAction(id, 'unpin', 'Resume updates') : (value.version ? harnessAction(id, 'pin', 'Stop updates') : '')].join('') : '';
    const title = [value.detail, value.managed ? '' : (value.installed ? 'Installed outside Tico; update it where it was installed' : '')].filter(Boolean).join(' · ');
    const needed = botRuntimes.has(value.runtime);
    const on = needed || (choices[id] ?? (value.wanted || value.installed));
    const toggle = S.me?.role === 'owner' && !machine.revoked_at
      ? ` <label class="machine-harness-toggle"${needed ? ' title="Its bots use it"' : ''}><input type="checkbox" aria-label="Keep ${esc(value.name || id)} installed on ${esc(machine.label)}" data-harness-choice="${esc(id)}" data-runner="${esc(machine.id)}"${on ? ' checked' : ''}${needed || !online ? ' disabled' : ''}> Keep installed</label>` : '';
    return `<span class="machine-harness-item" data-harness-chip="${esc(id)}"><span class="pill ${tone}" title="${esc(title)}">${esc(text)}</span>${toggle}${buttons}</span>`;
  };
  // The owner sees every model CLI, to turn one on; everyone else what is there.
  const harnesses = Object.entries(machine.readiness?.harnesses || {}).filter(([, value]) => S.me?.role === 'owner' || value.installed || value.wanted || value.state !== 'idle').map(harnessChip).join('');
  // Which runner engine it runs (runner/engine.py); hovering says the build, or why it is on Python.
  const engine = machine.readiness?.engine;
  const facts = [`Tico ${machine.version || 'version not reported'}`, machine.platform || '',
    engine?.name ? `<span title="${esc(engine.note || engine.build || '')}">${engine.name === 'rust' ? 'Rust' : 'Python'} runner</span>` : ''].filter(Boolean);
  return `<p class="computer-detail-facts">${facts.map((fact, i) => i === 2 ? fact : esc(fact)).join(' · ')}</p>${window.runnerUpdateHtml?.(machine.update, machine.version) || ''}
    ${table}${machineAppsHtml(machine)}${harnesses ? `<details data-machine-details="${esc(machine.id)}:tools"><summary>Installation and updates</summary><div class="machine-harnesses">${harnesses}</div></details>` : ''}`;
}
function machineScreenSectionHTML(machine, online) {
  return screenControlHTML(machine, online);
}
function machineSettingsHTML(machine) {
  const admin = settingsIsAdmin(), manage = !machine.revoked_at && machineCanSignIn(machine);
  const rows = machine.revoked_at ? [] : [
    manage ? `<label class="computer-detail-check"><input type="checkbox" data-inbox-only="${esc(machine.id)}"${machine.inbox_only ? ' checked' : ''}> Keep for inbox bots only <span class="muted">Their mail key can open every mailbox, so no other bot goes here.</span></label>`
      : machine.inbox_only ? '<p>Kept for inbox bots only.</p>' : '',
    admin ? `<label class="computer-detail-check"><input type="checkbox" data-member-bots="${esc(machine.id)}"${machine.accepts_member_bots ? ' checked' : ''}> Accepts members' bots</label>`
      : machine.accepts_member_bots ? '<p>Accepts members\' bots.</p>' : '',
    screenMayOpen(machine) ? `<label class="computer-detail-check"><input type="checkbox" data-performance-mode="${esc(machine.id)}"${machine.performance !== 'off' ? ' checked' : ''}> Performance diagnostics</label>` : '',
    `<button type="button" class="ghost" data-computer-performance="${esc(machine.id)}">Performance${betaPill('replay_performance')}</button>`];
  const revoke = manage ? `<button type="button" class="ghost danger-text computer-detail-revoke" data-computer-remove="${esc(machine.id)}">Revoke computer</button>` : '';
  const body = rows.filter(Boolean).join('') + revoke;
  return body || `<p class="muted">${machine.revoked_at ? `Revoked ${esc(ago(machine.revoked_at))}.` : 'Nothing to change here.'}</p>`;
}
function computerDetailHTML(machine) {
  const online = machineOnline(machine), manage = !machine.revoked_at && machineCanSignIn(machine);
  const conns = machineShown(machine);
  const status = machine.revoked_at ? 'revoked' : online ? 'online' : `offline${machine.last_seen ? ` · last seen ${ago(machine.last_seen)}` : ''}`;
  const notes = [!machine.revoked_at && !online ? '<p class="computer-detail-note" role="status" data-computer-offline>Computer is offline. Sign-in, usage refresh, screen and updates wait until it is back.</p>' : '',
    machineWaiting(machine) ? `<p class="computer-detail-note" role="status" data-machine-waiting>Waiting for ${esc(machine.label)} to report.</p>` : ''].join('');
  const empty = machineReported(machine) && !conns.length && !machine.revoked_at
    ? `<p class="computer-detail-empty"><span class="machine-chip-dot" aria-hidden="true"></span>Bots here can't run until a connection is added${manage ? ' · Add connection below' : ''}.</p>` : '';
  const section = (key, title, body) => `<section class="computer-detail-section" data-detail-section="${key}"><h3>${title}</h3>${body}</section>`;
  return `<header class="computer-detail-head"><div><h2 id="computer-detail-title">${esc(machine.label)}</h2><span class="muted">${esc(settingsPersonName(machine.operator))} · ${esc(status)}</span></div>
      <button class="ghost tmodal-x" type="button" data-detail-close aria-label="Close">✕</button></header>
    <div class="computer-detail-body">${notes}
      ${section('connections', 'Connections', empty + conns.map(conn => machineConnectionHTML(machine, conn, online, manage)).join('') + machineAddHTML(machine, online, manage))}
      ${section('screen', 'Screen', machineScreenSectionHTML(machine, online))}
      ${section('bots', 'Bots on this computer', machineBotsHTML(machine))}
      ${section('versions', 'Versions and updates', machineVersionsHTML(machine, online))}
      ${section('settings', 'Settings', machineSettingsHTML(machine))}
    </div>`;
}
// The pop-up lives inside #set-machines, so the list's own handlers take its clicks and changes.
function computerDetailOpen(id) {
  const el = $('#set-machines'); if (!el) return;
  let dialog = el.querySelector('dialog.computer-detail');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'tmodal computer-detail';
    dialog.setAttribute('aria-labelledby', 'computer-detail-title');
    dialog.onclose = () => { dialog.dataset.computer = ''; };
    el.appendChild(dialog);
  }
  dialog.dataset.computer = id;
  computerDetailPaint(true);
  if (!dialog.open) dialog.showModal();
}
// Redraw the open pop-up after Settings loads again. A connection, or Add connection, that someone is typing in is
// carried over as it is (draft, focus and caret), and everything around it is redrawn; a save settles its own form
// first (subsSettle), so its connection is redrawn too.
const COMPUTER_DETAIL_KEPT = '[data-connection], [data-add-connection]';
const computerDetailKey = node => node.matches('[data-add-connection]') ? '[data-add-connection]'
  : `[data-connection="${CSS.escape(node.dataset.connection)}"]`;
function computerDetailPaint(force = false) {
  const dialog = $('#set-machines dialog.computer-detail');
  if (!dialog?.dataset.computer) return;
  const machine = (SETTINGS_DATA?.machines || []).find(m => m.id === dialog.dataset.computer);
  if (!machine) { dialog.close(); return; }
  const kept = force ? [] : [...dialog.querySelectorAll(COMPUTER_DETAIL_KEPT)].filter(formBusy);
  // A draft anywhere else (none today) holds the whole pop-up back, as before.
  if (!force && [...dialog.querySelectorAll('textarea, input')].some(field => !field.closest(COMPUTER_DETAIL_KEPT)
    && formBusy({querySelectorAll: selector => [field].filter(f => f.matches(selector))}))) return;
  const body = dialog.querySelector('.computer-detail-body'), scroll = body?.scrollTop || 0;
  const open = new Set([...dialog.querySelectorAll('details[open]')].map(node => node.dataset.machineDetails || node.dataset.profile || ''));
  const active = document.activeElement, caret = [active?.selectionStart, active?.selectionEnd];
  const next = document.createElement('div');
  next.innerHTML = computerDetailHTML(machine);
  next.querySelectorAll('details').forEach(node => { node.open = open.has(node.dataset.machineDetails || node.dataset.profile || ''); });
  // A connection that is gone keeps no draft.
  for (const node of kept) next.querySelector(computerDetailKey(node))?.replaceWith(node);
  dialog.replaceChildren(...next.childNodes);
  if (active && kept.some(node => node.contains(active))) {
    active.focus({preventScroll: true});
    if (typeof caret[0] === 'number') try { active.setSelectionRange(caret[0], caret[1]); } catch { /* not a text field */ }
  }
  if (!force) dialog.querySelector('.computer-detail-body').scrollTop = scroll;
}
