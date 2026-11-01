/* ui/app/computer-detail.js — Settings > Computers: each computer's AI connections (the chips on its row) and the
   pop-up a row opens: Connections, Screen, Bots on this computer, Versions and updates, Settings.
   A connection is a named subscription signed in on the computer, or the computer's own login for a tool; a chip
   exists only for one that was added (signed in, or bots run on it), never for a tool a login was not meant for.
   Usage always reads as what is left. Classic script: its globals are shared with the other files under ui/app/,
   loaded in the order index.html lists them. */
'use strict';

const machineOnline = machine => !machine.revoked_at && !!machine.last_seen_at && (SETTINGS_PRESENCE.at || Date.now()) - new Date(machine.last_seen_at) < 60000;
const machinePresenceStale = () => SETTINGS_PRESENCE.checking || SETTINGS_PRESENCE.failed
  || !!SETTINGS_PRESENCE.at && Date.now() - SETTINGS_PRESENCE.at > SETTINGS_PRESENCE.interval;
const machinePresenceWords = machine => machine.revoked_at ? 'revoked'
  : `${machineOnline(machine) ? 'online' : `offline${machine.last_seen_at ? ` · last seen ${ago(machine.last_seen_at)}` : ''}`}${machinePresenceStale() ? ' · checking…' : ''}`;
// A presence paint can change the focused row too; restore its control by stable data attributes,
// keeping positional selectors only for the unkeyed containers between them.
function computerFocusRestore(root, active = document.activeElement) {
  if (!root?.contains(active)) return () => {};
  const path = [];
  for (let node = active; node !== root; node = node.parentElement) {
    const keys = [...node.attributes].filter(attr => attr.name === 'id' || attr.name.startsWith('data-'));
    path.unshift(node.localName + (keys.length ? keys.map(attr => `[${attr.name}="${CSS.escape(attr.value)}"]`).join('')
      : `:nth-of-type(${[...node.parentElement.children].filter(peer => peer.localName === node.localName).indexOf(node) + 1})`));
  }
  const caret = [active.selectionStart, active.selectionEnd, active.selectionDirection];
  return () => {
    const target = active.isConnected ? active : root.querySelector(path.join(' > '));
    target?.focus({preventScroll: true});
    if (target && typeof caret[0] === 'number') try { target.setSelectionRange(...caret); } catch { /* not a text field */ }
  };
}
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

// The Tico release a computer runs ("0.5.19"), from its update check; what the runner itself reports (`version`) only
// when there is none, since that constant is not bumped every release.
const machineRelease = machine => machine.release || machine.update?.release || machine.version || '';
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
// On a Docker runner, whether this tool's turns each run as their own user or share the bot user (readiness `turn_user`).
const machineTurnUserWords = conn => ({own: 'Each turn runs as its own user', bot: "Turns share the bot user (not isolated from other bots' checkouts)"})[conn.value?.turn_user] || '';
// The state in a word or two, as the chip under the tool's own column says it: a problem first, then signed in or not.
function machineChipWords(machine, conn) {
  if (conn.blocked) return 'Needs fix';
  if (conn.auth === 'rejected') return 'Expired';
  if (conn.keyCleared) return 'No key';
  if (conn.state.signed_in === true) return 'Signed in';
  if (conn.state.signed_in === false) return machineCanSignIn(machine) && !machine.revoked_at ? 'Sign in' : 'Signed out';
  return 'Checking';
}
// One chip per connection under its tool's column: the state, a thin bar and the % left when there is a reading; a red
// dot only when it needs attention. A click opens the computer's pop-up, where its actions are.
function machineChipHTML(machine, conn) {
  const left = machineLeft(conn.state.weekly), warn = machineAttentionWords(conn);
  const title = [`${conn.tool} · ${conn.name}`, conn.shared ? machineSharedWords(conn.shared) : '', warn, machineLeftWords(conn.state.weekly), machineTurnUserWords(conn)].filter(Boolean).join(' · ');
  return `<button type="button" class="machine-chip${conn.attention ? ' attention' : ''}" data-computer-open="${esc(machine.id)}" data-chip="${esc(conn.key)}" title="${esc(title)}" aria-label="${esc(title)}">`
    + `${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<span class="machine-chip-words">${esc(machineChipWords(machine, conn))}</span>`
    + `${left !== null ? `<meter class="machine-chip-bar" min="0" max="100" low="20" optimum="100" value="${left}" aria-hidden="true"></meter><span class="machine-chip-pct">${left}%</span>` : ''}</button>`;
}
// ---- the pop-up
function machineConnectionHTML(machine, conn, online, manage) {
  const kind = conn.shared ? `API key${conn.shared.name ? ` · ${conn.shared.name}` : ''}` : '';
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
    <div class="computer-conn-head">${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<strong>${esc(conn.tool)}</strong>${kind ? `<span>${esc(kind)}</span>` : ''}${tags}</div>
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
// ---- the Add connection pop-up: 1 the tool, 2 subscription or API key, then the provider's sign-in of the computer's
// one login for that tool in this same pop-up (ui/model-login.js open `into`), and the chip appears once the computer
// reports it. A different account for the same tool means another computer: a bot has no sign-in of its own.
function computerAddTools(machine) {
  const runtimes = machine.readiness?.runtimes || {};
  return Object.entries(runtimes).filter(([, value]) => value?.installed).map(([runtime]) => ({runtime, name: machineToolName(machine, runtime)}))
    .sort((a, b) => a.name.localeCompare(b.name));
}
function computerAddStepHTML(machine, step) {
  const head = (title, back) => `<header class="tmodal-head"><h2 id="computer-add-title">${title}</h2>
      ${back ? `<button class="ghost" type="button" data-add-back="${back}">Back</button>` : ''}
      <button class="ghost tmodal-x" type="button" data-add-close aria-label="Close">✕</button></header>
    <div class="tmodal-body"><p class="muted computer-add-where">${esc(machine.label)}</p>`;
  if (step.at === 'tool') {
    const tools = computerAddTools(machine).map(tool => `<button class="ghost computer-add-choice" type="button" data-add-tool="${esc(tool.runtime)}">${esc(tool.name)}</button>`).join('');
    // Installing is the owner's (Versions and updates > Installation and updates).
    return `${head('Add connection')}<p class="model-login-body">Which AI tool?</p><div class="computer-add-choices" data-add-step="tool">${tools || '<p class="muted">No AI tools reported on this computer yet.</p>'}
      <button class="ghost computer-add-choice" type="button" data-add-install>Install another tool</button></div></div>`;
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
      ${terminal ? `<p class="muted computer-add-terminal" data-add-terminal="${esc(step.runtime)}">Or sign the computer's login in from a terminal there:${terminal}</p>` : ''}</div>`;
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
      <div class="model-login-actions"><button class="ghost" type="button" data-add-close>Close</button>${go}</div></div></div>`;
  }
  return '';
}
function computerAddOpen(id) {
  const machine = () => (SETTINGS_DATA?.machines || []).find(m => m.id === id);
  if (!machine()) return;
  const root = $('#set-machines dialog.computer-detail[open]') || $('#set-machines');
  const opener = root?.querySelector(`[data-add-connection="${CSS.escape(id)}"]`);
  const restoreFocus = computerFocusRestore(root, opener);
  // Mouse clicks need not focus buttons; give the native dialog the same stable opener.
  opener?.focus({preventScroll: true});
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal tmodal-shell model-login computer-add-dialog';
  dialog.setAttribute('aria-labelledby', 'computer-add-title');
  document.body.appendChild(dialog);
  let step = {at: 'tool'}, signingIn = false;
  const paint = () => { dialog.innerHTML = computerAddStepHTML(machine(), step); dialog.querySelector('button:not([disabled]), input')?.focus(); };
  const signIn = () => {
    signingIn = true;     // the sign-in owns the pop-up from here (its own Close cancels a sign-in still running)
    window.TicoModelLogin?.open({runnerId: id, runtime: step.runtime, machine: machine().label, profile: '', into: dialog});
  };
  dialog.addEventListener('close', () => {
    // A queued close must not take focus back from an action already opened elsewhere.
    const returnFocus = document.activeElement === document.body || dialog.contains(document.activeElement);
    dialog.remove();
    if (returnFocus) restoreFocus();
  });
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
  void settingsMachinesRefresh();
  return dialog;
}
function machineBotsHTML(machine) {
  const links = new Map((machine.bot_connections || []).map(line => [line.bot, line]));
  const rows = (machine.bots || []).map(bot => {
    const line = links.get(bot);
    // The server's own words when it has them (a runner too old to say).
    const warn = line?.problem || (line?.signed_in === false ? 'Signed out' : '');
    return `<li data-detail-bot="${esc(bot)}"><strong>${esc(settingsBotName(bot))}</strong>${warn ? ` <span class="pill fail">${esc(warn)}</span>` : ''}</li>`;
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
  const release = machineRelease(machine);
  const facts = [`Tico ${release || 'version not reported'}`, machine.platform || '',
    engine?.name ? `<span title="${esc(engine.note || engine.build || '')}">${engine.name === 'rust' ? 'Rust' : 'Python'} runner</span>` : ''].filter(Boolean);
  return `<p class="computer-detail-facts">${facts.map((fact, i) => i === 2 ? fact : esc(fact)).join(' · ')}</p>${window.runnerUpdateHtml?.(machine.update, release) || ''}
    ${table}${machineAppsHtml(machine)}${harnesses ? `<details data-machine-details="${esc(machine.id)}:tools"><summary>Installation and updates</summary><div class="machine-harnesses">${harnesses}</div></details>` : ''}`;
}
function machineScreenSectionHTML(machine, online) {
  return screenControlHTML(machine, online);
}
// What the line under Turns at once says. Empty is automatic, so it gives the count the computer works out. A setting
// above turn_limit is one an older runner cannot take, so the runner keeps its own limit and that is what is shown.
function machineTurnsNote(machine) {
  if (machine.turns_at_once == null) return machine.capacity != null ? `Automatic (${machine.capacity})` : 'Automatic';
  return machine.turn_limit != null && machine.turns_at_once > machine.turn_limit
    ? `This computer runs at most ${machine.turn_limit}` : '';
}
// Internet sharing (backend/browser_exit.py): only the person whose computer it is turns it on and picks the bots and
// the daily cap; an admin sees a switch that only turns it off.
const exitBytes = n => n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.round((n || 0) / 1e3)} KB`;
function machineBrowserExitHTML(machine) {
  const x = machine.browser_exit;
  if (!x || machine.revoked_at || !(x.may_change || x.may_turn_off || x.sharing)) return '';
  const id = esc(machine.id);
  const toggle = `<label class="computer-detail-check"><input type="checkbox" role="switch" class="people-switch" data-browser-exit="${id}"${x.sharing ? ' checked' : ''}${x.may_change || (x.may_turn_off && x.sharing) ? '' : ' disabled'}> Share internet with bots</label>`;
  if (!x.sharing) return toggle;
  const names = new Map((S.emps || []).filter(e => !e.agent && (e.status === 'active' || x.bots.includes(e.id)))
    .map(e => [e.id, e.display_name || botDisplayName(e.id)]));
  for (const bot of x.bots) if (!names.has(bot)) names.set(bot, botDisplayName(bot));
  const bots = x.may_change
    ? `<fieldset class="computer-exit-bots" data-browser-exit-bots="${id}"><legend>Bots</legend>${[...names].sort((a, b) => a[1].localeCompare(b[1]))
      .map(([slug, name]) => `<label><input type="checkbox" value="${esc(slug)}"${x.bots.includes(slug) ? ' checked' : ''}> ${esc(name)}</label>`).join('')}</fieldset>`
    : `<p>${x.bots.length ? esc(x.bots.map(b => names.get(b)).join(', ')) : 'No bots'}</p>`;
  const cap = x.may_change
    ? `<label class="computer-detail-check">Daily cap (GB) <input type="number" class="settings-inline-select" min="0.1" max="10000" step="0.1"
      data-browser-exit-cap="${id}" value="${esc(String(x.daily_cap_bytes / 1e9))}" aria-label="Daily cap in GB for ${esc(machine.label)}"></label>`
    : `<p>Daily cap ${esc(exitBytes(x.daily_cap_bytes))}</p>`;
  const used = x.used_today_bytes == null ? '' : `<details class="computer-exit-usage" data-machine-details="browser-exit-usage" data-browser-exit-usage="${id}"><summary>Today ${esc(exitBytes(x.used_today_bytes))} of ${esc(exitBytes(x.daily_cap_bytes))}</summary><div data-browser-exit-usage-rows></div></details>`;
  return toggle + bots + cap + used;
}
async function machineBrowserExitUsageLoad(details) {
  const box = details.querySelector('[data-browser-exit-usage-rows]');
  if (!box || details.dataset.loaded) return;
  details.dataset.loaded = '1';
  box.innerHTML = '<p class="muted">Loading</p>';
  try {
    const seen = await get(`/v2/computers/${encodeURIComponent(details.dataset.browserExitUsage)}/browser-exit/usage?days=7&limit=200`, {fresh: true});
    box.innerHTML = seen.rows.length ? `<table class="computer-exit-table"><thead><tr><th>Day</th><th>Bot</th><th>Site</th><th class="num">Data</th></tr></thead><tbody>${seen.rows
      .map(r => `<tr><td>${esc(r.day.slice(5))}</td><td>${esc(botDisplayName(r.bot))}</td><td>${esc(r.host)}</td><td class="num tnum">${esc(exitBytes(r.bytes))}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">Nothing yet</p>';
  } catch (error) { delete details.dataset.loaded; box.innerHTML = `<p class="muted">${esc(error.message)}</p>`; }
}
// One save for the switch, the bots and the cap: what the pop-up shows now.
async function machineBrowserExitSave(control) {
  const id = control.dataset.browserExit || control.closest('[data-browser-exit-bots]')?.dataset.browserExitBots || control.dataset.browserExitCap;
  const machine = (SETTINGS_DATA.machines || []).find(row => row.id === id);
  if (!machine?.browser_exit) return;
  const scope = control.closest('.computer-detail-section') || document;
  const sharing = scope.querySelector(`[data-browser-exit="${CSS.escape(id)}"]`)?.checked ?? machine.browser_exit.sharing;
  const body = {sharing};
  if (machine.browser_exit.may_change) {
    const picked = scope.querySelector(`[data-browser-exit-bots="${CSS.escape(id)}"]`);
    if (picked) body.bots = [...picked.querySelectorAll('input:checked')].map(box => box.value);
    const cap = scope.querySelector(`[data-browser-exit-cap="${CSS.escape(id)}"]`);
    if (cap) {
      const gb = Number(cap.value);
      if (!cap.checkValidity() || !(gb >= 0.1 && gb <= 10000)) { settingsFieldError(cap, 'Enter 0.1 to 10000 GB.'); return; }
      settingsFieldClear(cap);
      body.daily_cap_bytes = Math.round(gb * 1e9);
    }
  }
  control.disabled = true;
  try {
    await post(`/v2/computers/${encodeURIComponent(id)}/browser-exit`, body);
    const cap = scope.querySelector(`[data-browser-exit-cap="${CSS.escape(id)}"]`);
    if (cap) cap.defaultValue = cap.value;           // saved: no longer a draft that holds the pop-up's redraw back
    if (control.dataset.browserExit) toast(sharing ? 'Sharing internet with bots' : 'Stopped sharing internet');
    await loadSettings();
  } catch (error) {
    toast(error.message, true);
    if (control.type === 'checkbox') control.checked = !control.checked;
    control.disabled = false;
  }
}
function machineSettingsHTML(machine) {
  const admin = settingsIsAdmin(), manage = !machine.revoked_at && machineCanSignIn(machine);
  const turns = machine.turns_at_once == null ? '' : String(machine.turns_at_once);
  const rows = machine.revoked_at ? [] : [
    manage ? `<label class="computer-detail-check"><input type="checkbox" data-inbox-only="${esc(machine.id)}"${machine.inbox_only ? ' checked' : ''}> Keep for inbox bots only <span class="muted">Their mail key can open every mailbox, so no other bot goes here.</span></label>`
      : machine.inbox_only ? '<p>Kept for inbox bots only.</p>' : '',
    admin ? `<label class="computer-detail-check">Turns at once <input type="number" class="settings-inline-select"
      min="1" max="256" step="1" data-turns-at-once="${esc(machine.id)}" data-current="${esc(turns)}"
      value="${esc(turns)}" aria-label="Turns at once for ${esc(machine.label)}">
      <span class="muted" data-turns-note>${esc(machineTurnsNote(machine))}</span></label>` : '',
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
  const status = machinePresenceWords(machine);
  const notes = [!machine.revoked_at && !online ? '<p class="computer-detail-note" role="status" data-computer-offline>Computer is offline. Sign-in, usage refresh, screen and updates wait until it is back.</p>' : '',
    machineWaiting(machine) ? `<p class="computer-detail-note" role="status" data-machine-waiting>Waiting for ${esc(machine.label)} to report.</p>` : ''].join('');
  const empty = machineReported(machine) && !conns.length && !machine.revoked_at
    ? `<p class="computer-detail-empty"><span class="machine-chip-dot" aria-hidden="true"></span>Bots here can't run until a connection is added${manage ? ' · Add connection below' : ''}.</p>` : '';
  const section = (key, title, body) => `<section class="computer-detail-section" data-detail-section="${key}"><h3>${title}</h3>${body}</section>`;
  return `<header class="computer-detail-head"><div><h2 id="computer-detail-title">${esc(machine.label)}</h2><span class="muted">${esc(settingsPersonName(machine.owner_id))} · <span data-computer-status>${esc(status)}</span></span></div>
      <button class="ghost tmodal-x" type="button" data-detail-close aria-label="Close">✕</button></header>
    <div class="computer-detail-body">${notes}
      ${machine.turn_summary ? `<p class="computer-detail-note" data-turn-counts>${esc(machine.turn_summary)}</p><p class="muted">A turn has up to 3 minutes to start. Waiting turns leave room for other work.</p>` : ''}
      ${section('connections', 'Connections', empty + conns.map(conn => machineConnectionHTML(machine, conn, online, manage)).join('') + machineAddHTML(machine, online, manage))}
      ${section('screen', 'Screen', machineScreenSectionHTML(machine, online))}
      ${machineBrowserExitHTML(machine) ? section('internet', 'Internet', machineBrowserExitHTML(machine)) : ''}
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
    dialog.onclose = () => {
      if (dialog.open) return;
      dialog.dataset.computer = '';
      if (document.activeElement === document.body || dialog.contains(document.activeElement)) dialog.restoreOpener?.();
    };
    // Today's sites load when someone opens them (toggle doesn't bubble, so listen while it passes down).
    dialog.addEventListener('toggle', event => {
      if (event.target.matches?.('[data-browser-exit-usage]') && event.target.open) void machineBrowserExitUsageLoad(event.target);
    }, true);
    el.appendChild(dialog);
  }
  // Presence refreshes can replace the opener while this modal is open.
  if (!dialog.open) {
    const opener = el.querySelector(`[data-computer-open="${CSS.escape(id)}"]`);
    dialog.restoreOpener = computerFocusRestore(el, opener);
    opener?.focus({preventScroll: true});
  }
  dialog.dataset.computer = id;
  computerDetailPaint(true);
  if (!dialog.open) dialog.showModal();
  void settingsMachinesRefresh();
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
  const restoreFocus = force ? () => {} : computerFocusRestore(dialog);
  const next = document.createElement('div');
  next.innerHTML = computerDetailHTML(machine);
  next.querySelectorAll('details').forEach(node => { node.open = open.has(node.dataset.machineDetails || node.dataset.profile || ''); });
  // A connection that is gone keeps no draft.
  for (const node of kept) next.querySelector(computerDetailKey(node))?.replaceWith(node);
  dialog.replaceChildren(...next.childNodes);
  dialog.querySelectorAll('details[data-browser-exit-usage][open]').forEach(node => void machineBrowserExitUsageLoad(node));
  restoreFocus();
  if (!force) dialog.querySelector('.computer-detail-body').scrollTop = scroll;
}
