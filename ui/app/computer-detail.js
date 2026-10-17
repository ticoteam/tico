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

// The computer's connections: [{key, runtime, name, profile, own, state, bots, problem, attention}]. `state` carries the
// reading shown ({signed_in, account, weekly, five_hour, refresh}); `auth` and `value` the own login's readiness.
function machineConnections(machine) {
  const runtimes = machine.readiness?.runtimes || {};
  const links = machine.bot_connections || [];
  const rows = [];
  for (const p of Array.isArray(machine.profiles) ? machine.profiles : []) {
    for (const [runtime, state] of Object.entries(p.runtimes || {})) {
      const bots = links.filter(b => b.profile === p.name && b.runtime === runtime).map(b => b.bot);
      // A login folder has a slot for every tool; only a tool it is signed in to, or that bots run on, is a connection.
      if (!(state?.signed_in === true || bots.length || machineSignedOut(machine, runtime, p.name)
        || (state?.signed_in == null && (state?.account || state?.weekly)))) continue;
      rows.push({key: `profile:${p.name}:${runtime}`, runtime, name: p.display_name || p.name, profile: p, own: false, state: {...state}, bots});
    }
  }
  for (const [runtime, value] of Object.entries(runtimes)) {
    const bots = links.filter(b => !b.profile && b.runtime === runtime).map(b => b.bot);
    const auth = value?.authenticated;
    // Signed out from Tico (Sign out) stays a line, so it can sign in again or go back to the shared key.
    if (!(auth === 'ready' || auth === 'rejected' || bots.length || machineSignedOut(machine, runtime))) continue;
    const reading = {signed_in: auth === 'ready' ? true : ['missing', 'rejected'].includes(auth) ? false : null,
      account: value.account, weekly: value.weekly, five_hour: value.five_hour};
    // The same provider account as a named subscription here: one line, one reading (the newer one). The server
    // names it in `same_account`, since only an admin or the operator receives the accounts themselves.
    const email = auth === 'ready' && machineEmail(value.account), match = auth === 'ready' && machine.same_account?.[runtime];
    const same = (email || match) && rows.find(r => !r.own && r.runtime === runtime && (match ? r.profile?.name === match : machineEmail(r.state.account) === email));
    if (same) {
      Object.assign(same, {own: true, auth, value, bots: [...new Set([...same.bots, ...bots])].sort()});
      if (machineNewer(value.weekly, same.state.weekly)) Object.assign(same.state, {weekly: value.weekly, five_hour: value.five_hour});
      continue;
    }
    rows.push({key: `own:${runtime}`, runtime, name: "Computer's login", profile: null, own: true, auth, value, state: reading, bots});
  }
  // Bots on a subscription this computer does not have (or that does not cover their tool).
  for (const b of links) {
    if (!b.profile || rows.some(r => r.profile?.name === b.profile && r.runtime === b.runtime)) continue;
    const key = `missing:${b.profile}:${b.runtime}`, row = rows.find(r => r.key === key);
    if (row) { row.bots.push(b.bot); continue; }
    rows.push({key, runtime: b.runtime, name: b.display_name || b.profile, profile: {name: b.profile, display_name: b.display_name || b.profile},
      own: false, missing: true, state: {signed_in: false}, bots: [b.bot], problem: b.problem || 'Not signed in on this computer'});
  }
  for (const r of rows) {
    // Signed out from Tico is a red dot even with no bots on it yet: nothing signs it back in by itself.
    const cleared = r.state.signed_in === false && machineSignedOut(machine, r.runtime, r.profile?.name ?? '');
    r.attention = !!((r.bots.length && r.state.signed_in === false) || cleared || r.auth === 'rejected' || (r.missing && r.bots.length));
    r.tool = machineToolName(machine, r.runtime);
  }
  return rows.sort((a, b) => a.tool.localeCompare(b.tool) || Number(b.own) - Number(a.own) || a.name.localeCompare(b.name));
}

// What is left of the weekly allowance, as a whole percent; null with no current reading.
function machineLeft(weekly) {
  const reset = Date.parse(weekly?.resets_at);
  if (!Number.isFinite(weekly?.used_percent) || (Number.isFinite(reset) && reset <= Date.now())) return null;
  return Math.round(100 - Math.min(100, Math.max(0, weekly.used_percent)));
}
// "33% left · resets Thu 12:06 AM · as of 2m ago", or what is known when there is no reading.
function machineLeftWords(weekly) {
  const left = machineLeft(weekly), reset = Date.parse(weekly?.resets_at);
  if (left === null) return weekly ? 'Previous week\'s reading; refresh needed' : 'No usage reading yet';
  return [`${left}% left`, Number.isFinite(reset) ? `resets ${machineReset(reset)}` : '',
    weekly.reported_at ? `as of ${ago(weekly.reported_at)}` : ''].filter(Boolean).join(' · ');
}
function machineAttentionWords(conn) {
  if (conn.missing) return conn.problem;
  if (conn.auth === 'rejected') return 'Login expired or rejected';
  if (!conn.attention) return '';
  return conn.bots.length ? 'Signed out; bots use it' : 'Signed out';
}
// One chip per connection: the tool, a thin bar and the % left; a red dot only when it needs attention.
function machineChipHTML(machine, conn) {
  const left = machineLeft(conn.state.weekly), warn = machineAttentionWords(conn);
  const title = [`${conn.tool} · ${conn.name}${conn.own && conn.profile ? " (Computer's login)" : ''}`, warn, machineLeftWords(conn.state.weekly)].filter(Boolean).join(' · ');
  return `<button type="button" class="machine-chip${conn.attention ? ' attention' : ''}" data-computer-open="${esc(machine.id)}" data-chip="${esc(conn.key)}" title="${esc(title)}" aria-label="${esc(title)}">`
    + `${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<span>${esc(conn.tool)}</span>`
    + `${left !== null ? `<meter class="machine-chip-bar" min="0" max="100" low="20" optimum="100" value="${left}" aria-hidden="true"></meter><span class="machine-chip-pct">${left}%</span>` : ''}</button>`;
}
function machineChipsHTML(machine) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  const waiting = machineWaiting(machine) ? `<span class="machine-chip waiting" data-machine-waiting>Waiting for ${esc(machine.label)}</span>` : '';
  if (!machineReported(machine)) return waiting;
  const conns = machineConnections(machine);
  const chips = conns.map(conn => machineChipHTML(machine, conn)).join('')
    || `<button type="button" class="machine-chip attention" data-computer-open="${esc(machine.id)}" data-no-connection><span class="machine-chip-dot" aria-hidden="true"></span><span>No AI connection</span></button>`;
  return `<div class="machine-chips">${chips}${waiting}</div>`;
}

// ---- the pop-up
function machineConnectionHTML(machine, conn, online, manage) {
  const c = {runner_id: machine.id, label: machine.label};
  const kind = conn.profile ? `Subscription · ${conn.name}` : "Computer's login";
  const tags = [conn.own && conn.profile ? '<span class="pill computer-conn-own">Computer\'s login</span>' : ''].join('');
  let signIn;
  if (conn.missing) signIn = `<span class="pill fail">${esc(conn.problem)}</span>`;
  else if (conn.profile) signIn = machineProfileSignIn(conn.state, conn.runtime);
  else signIn = machineSignInHTML(machine, online, conn.runtime, conn.auth, conn.value);
  const offline = online ? '' : ' disabled title="Computer is offline"';
  let action = '';
  if (manage && conn.state.signed_in !== true && !conn.missing) {
    const caps = harnessCaps(conn.runtime), command = !conn.profile && machineLoginCommand(machine, conn.runtime);
    // "Signed out · Sign in": the way back sits next to what is wrong.
    if (caps.browser_sign_in) action = `${conn.state.signed_in === false ? '<span class="computer-conn-sep" aria-hidden="true"> · </span>' : ' '}<button class="ghost machine-signin" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(conn.runtime)}" data-machine="${esc(machine.label)}"${conn.profile ? ` data-profile="${esc(conn.profile.name)}"` : ''}${offline}>Sign in</button>`;
    else if (command) action = ` <span class="machine-login-command" title="Run on the computer${command.includes('<container>') ? '; docker ps shows the container name' : ''}"><code>${esc(command)}</code><button class="ghost machine-signin" type="button" data-machine-copy-login="${esc(command)}">Copy</button></span>`;
    else if (caps.api_keys === 'bot') action = ' <a class="machine-signin-link" href="#/credentials">Add key</a>';
  }
  // Switch account / Sign out (or Use the shared key) per login, from ui/model-login.js; only while online. A line
  // that is both the computer's login and a subscription names which login each pair acts on.
  const login = (profile, signedIn) => manage && !conn.missing ? machineLoginActions(machine, online, conn.runtime, profile, signedIn) : '';
  if (conn.own && conn.profile) {
    const sub = login(conn.profile.name, conn.state.signed_in === true), own = login('', conn.auth === 'ready');
    action += [sub && `<span class="computer-conn-login" data-login="${esc(conn.profile.name)}">Subscription${sub}</span>`,
      own && '<span class="computer-conn-login" data-login="">Computer\'s login' + own + '</span>'].filter(Boolean).join('');
  } else action += login(conn.profile ? conn.profile.name : '', conn.profile ? conn.state.signed_in === true : conn.auth === 'ready');
  const value = conn.value || {};
  const rejected = conn.auth === 'rejected'
    ? `<span class="err machine-rejected" data-rejected="${esc(conn.runtime)}">${value.rejected_at ? esc(ago(value.rejected_at)) + '. ' : ''}${value.rejected_reason ? esc(value.rejected_reason) + '. ' : ''}${hlCredentialFix(value.credential_source)}. It takes no work that needs ${esc(conn.runtime)} until then.</span>` : '';
  const usage = conn.state.signed_in === false ? '' : machineUsageHTML(conn.runtime, conn.state, `${conn.tool} ${conn.name}`, conn.profile ? 'this subscription' : 'this computer');
  const note = conn.profile && !conn.missing ? subsRefreshNote(conn.state.refresh) : '';
  const tools = machineLineActionsHTML(machine, conn, online, manage) + (conn.profile && !conn.missing && manage
    ? subsRefreshHTML(c, conn.profile, conn.runtime, conn.state, online) + subsControlsHTML(c, conn.profile, conn.runtime, conn.state) : '');
  const bots = conn.bots.length ? `Bots: ${conn.bots.map(settingsBotName).join(', ')}` : 'No bots run on it';
  return `<div class="computer-conn${conn.attention ? ' attention' : ''}" data-connection="${esc(conn.key)}" data-machine-tool="${esc(conn.runtime)}">
    <div class="computer-conn-head">${conn.attention ? '<span class="machine-chip-dot" aria-hidden="true"></span>' : ''}<strong>${esc(conn.tool)}</strong><span>${esc(kind)}</span>${tags}</div>
    <div class="computer-conn-signin">${signIn}${action}${rejected}</div>
    ${usage ? `<div class="computer-conn-usage">${usage}</div>` : ''}
    <div class="computer-conn-bots muted">${esc(bots)}</div>
    ${tools || note ? `<div class="computer-conn-tools">${tools}${note ? `<span class="muted" role="status">${esc(note)}</span>` : ''}</div>` : ''}
  </div>`;
}
// Add connection: one button at the end of Connections, opening its own pop-up (computerAddOpen).
function machineAddHTML(machine, online, manage) {
  if (!manage || machine.revoked_at) return '';
  return `<div class="computer-add"><button class="ghost" type="button" data-add-connection="${esc(machine.id)}"${online ? '' : ' disabled title="Computer is offline"'}>Add connection</button></div>`;
}
// Rename and Remove, for a named subscription's line; only while the computer is online, like every action here.
function machineLineActionsHTML(machine, conn, online, manage) {
  if (!manage || conn.own && !conn.profile || conn.missing || !conn.profile) return '';
  const offline = online ? '' : ' disabled title="Computer is offline"';
  const rename = conn.profile.id && subsMaySignIn(machine)
    ? (online ? `<details class="computer-conn-rename" data-profile="rename:${esc(conn.profile.name)}"><summary>Rename</summary>${subsRenameHTML({runner_id: machine.id}, conn.profile)}</details>`
      : `<button class="ghost" type="button"${offline}>Rename</button>`) : '';
  // The computer's own login merged into this line is not removed with it: Sign out is for that one.
  const remove = harnessCaps(conn.runtime).sign_out
    ? `<button class="ghost danger-text" type="button" data-connection-remove="${esc(conn.key)}" data-runner="${esc(machine.id)}"${offline}>Remove</button>` : '';
  return rename + remove;
}
// The Bots page, showing this computer's bots: where a bot is moved to another connection.
function computerBotsPage(id) {
  $('#set-machines dialog.computer-detail')?.close();
  SETTINGS_BOTS_VIEW.computer = id; settingsBotsRemember(); settingsShow('bots'); renderSettingsBots();
}
function computerRemoveOpen(button) {
  const machine = (SETTINGS_DATA?.machines || []).find(m => m.id === button.dataset.runner);
  const conn = machine && machineConnections(machine).find(row => row.key === button.dataset.connectionRemove);
  if (!conn?.profile) return;
  window.TicoModelLogin?.remove({runnerId: machine.id, runtime: conn.runtime, machine: machine.label, profile: conn.profile.name,
    name: conn.name, bots: conn.bots.map(settingsBotName), botsPage: () => computerBotsPage(machine.id)});
}

// ---- the Add connection pop-up: 1 the tool, 2 subscription or API key, 3 a name then the provider's sign-in in this
// same pop-up (ui/model-login.js open `into`), 4 done, and the chip appears once the computer reports it.
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
  if (step.at === 'kind') {
    const subscription = caps.browser_sign_in && caps.subscription_profiles;
    return `${head(`Add ${esc(tool)}`, 'tool')}<p class="model-login-body">How does ${esc(tool)} sign in here?</p><div class="computer-add-choices" data-add-step="kind">
      <button class="ghost computer-add-choice" type="button" data-add-kind="subscription"${subscription ? '' : ' disabled'}><strong>Subscription</strong><span class="muted">${subscription ? 'Sign in with a ChatGPT, Claude or other plan account' : `${esc(tool)} has no subscription sign-in from here`}</span></button>
      <button class="ghost computer-add-choice" type="button" data-add-kind="api_key"><strong>API key</strong><span class="muted">Paste a key from the provider</span></button></div>`;
  }
  if (step.at === 'api_key') {
    // A key stored for this computer only is the next part (it needs the per-computer key store); until then a model
    // key goes in Credentials, as today.
    return `${head(`Add ${esc(tool)} with an API key`, 'kind')}<div data-add-step="api_key"><p class="model-login-body">Adding a key for this computer only comes in the next update.
      For now, add the model key in Credentials; it is used on the computers it is shared with.</p>
      <div class="model-login-actions"><button class="ghost" type="button" data-add-close>Close</button><a class="primary" href="#/credentials" data-add-credentials>Open Credentials</a></div></div>`;
  }
  const own = caps.browser_sign_in && machine.readiness?.runtimes?.[step.runtime]?.authenticated !== 'ready';
  return `${head(`Add a ${esc(tool)} subscription`, 'kind')}<form class="computer-add-name" data-add-step="name" novalidate>
      <label>Name <input name="profile" type="text" autocomplete="off" spellcheck="false" maxlength="80" required placeholder="e.g. Team ChatGPT" value="${esc(step.name || '')}"></label>
      <span class="muted" role="status" data-add-msg></span>
      <p class="muted">Then sign in with the provider here. The chip appears on ${esc(machine.label)} once it reports; no restart needed.</p>
      <div class="model-login-actions">${own ? '<button class="ghost" type="button" data-add-own>Sign in the computer\'s own login instead</button>' : ''}<button class="primary" type="submit">Sign in</button></div></form>`;
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
  const signIn = (profile = '') => {
    signingIn = true;     // the sign-in owns the pop-up from here (its own Close cancels a sign-in still running)
    window.TicoModelLogin?.open({runnerId: id, runtime: step.runtime, machine: machine().label, profile, into: dialog});
  };
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('cancel', event => { if (signingIn) return; event.preventDefault(); dialog.close(); });
  dialog.addEventListener('click', event => {
    if (signingIn) return;
    if (event.target === dialog || event.target.closest('[data-add-close]')) { dialog.close(); return; }
    if (event.target.closest('[data-add-credentials]')) { dialog.close(); $('#set-machines dialog.computer-detail')?.close(); return; }
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
    if (kind && !kind.disabled) { step = {...step, at: kind.dataset.addKind === 'api_key' ? 'api_key' : 'name'}; paint(); return; }
    if (event.target.closest('[data-add-own]')) signIn('');
  });
  dialog.addEventListener('input', event => {
    const field = event.target.closest('[name=profile]'); if (!field || signingIn) return;
    step.name = field.value;
    const slug = subsSlug(field.value);
    dialog.querySelector('[data-add-msg]').textContent = field.value.trim() && slug !== field.value.trim() ? `Saved as ${slug}` : '';
  });
  dialog.addEventListener('submit', event => {
    event.preventDefault();
    if (signingIn) return;
    const form = event.target.closest('[data-add-step="name"]'); if (!form) return;
    const name = subsSlug(form.elements.profile.value), msg = form.querySelector('[data-add-msg]');
    if (!name) { msg.textContent = 'Use letters or numbers'; form.elements.profile.focus(); return; }
    signIn(name);
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
// Each AI tool's version and whether it is enabled here, then the owner's install and update actions.
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
    // Enabled is the owner's choice per computer, always on for what its bots run on; an older runner cannot say.
    const enabled = !tool.id ? '' : botRuntimes.has(name) || (choices[tool.id] ?? (tool.wanted || tool.installed)) ? 'Enabled' : 'Off';
    return `<div class="machine-tool" role="row" data-machine-tool="${esc(name)}">
      <span role="cell" class="machine-tool-name">${esc(tool.name || harnessWords(name))}</span>
      <span role="cell" class="machine-tool-version"${version ? ` title="${esc(version)}"` : ''}>${installed ? esc(version || 'Installed') : '<span class="muted">Not installed</span>'}</span>
      <span role="cell" class="machine-tool-enabled"${botRuntimes.has(name) ? ' title="Its bots use it"' : ''}>${enabled ? esc(enabled) : '<span class="muted">—</span>'}</span></div>`;
  }).join('');
  const table = rows ? `<div class="machine-tools" role="table" aria-label="AI tools on ${esc(machine.label)}"><div class="machine-tool machine-tool-head" role="row">
    <span role="columnheader">Tool</span><span role="columnheader">Installed</span><span role="columnheader">Enabled</span></div>${rows}</div>` : '<p class="muted">No AI tools reported.</p>';
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
      value.pinned ? harnessAction(id, 'unpin', 'Resume updates') : (value.version ? harnessAction(id, 'pin', 'Pin to this version') : '')].join('') : '';
    const title = [value.detail, value.managed ? '' : (value.installed ? 'Installed outside Tico; update it where it was installed' : '')].filter(Boolean).join(' · ');
    const needed = botRuntimes.has(value.runtime);
    const on = needed || (choices[id] ?? (value.wanted || value.installed));
    const toggle = S.me?.role === 'owner' && !machine.revoked_at
      ? ` <label class="machine-harness-toggle"${needed ? ' title="Its bots use it"' : ''}><input type="checkbox" aria-label="Enable ${esc(value.name || id)} on ${esc(machine.label)}" data-harness-choice="${esc(id)}" data-runner="${esc(machine.id)}"${on ? ' checked' : ''}${needed || !online ? ' disabled' : ''}> Enabled</label>` : '';
    return `<span class="machine-harness-item" data-harness-chip="${esc(id)}"><span class="pill ${tone}" title="${esc(title)}">${esc(text)}</span>${toggle}${buttons}</span>`;
  };
  // The owner sees every model CLI, to turn one on; everyone else what is there.
  const harnesses = Object.entries(machine.readiness?.harnesses || {}).filter(([, value]) => S.me?.role === 'owner' || value.installed || value.wanted || value.state !== 'idle').map(harnessChip).join('');
  // Which runner engine it runs (runner/engine.py); hovering says the build, or why it is on Python.
  const engine = machine.readiness?.engine;
  const facts = [`Tico ${machine.version || 'version not reported'}`, machine.platform || '',
    engine?.name ? `<span title="${esc(engine.note || engine.build || '')}">${engine.name === 'rust' ? 'Rust' : 'Python'} runner</span>` : ''].filter(Boolean);
  return `<p class="computer-detail-facts">${facts.map((fact, i) => i === 2 ? fact : esc(fact)).join(' · ')}</p>${window.runnerUpdateHtml?.(machine.update, machine.version) || ''}
    ${table}${harnesses ? `<details data-machine-details="${esc(machine.id)}:tools"><summary>Installation and updates</summary><p class="settings-cell-note">Enabled tools stay installed.</p><div class="machine-harnesses">${harnesses}</div></details>` : ''}`;
}
function machineScreenSectionHTML(machine, online) {
  // Screen (Off / View / Control): offered once the computer itself has a screen turned on (`screen_available`),
  // and kept while a mode is set so it can be turned off again. The operator or a bot admin sets it.
  const screen = machine.screen || 'off';
  const choice = !machine.revoked_at && screenMayOpen(machine) && (machine.screen_available || screen !== 'off')
    ? `<div class="machine-screen-choice" data-computer-screen="${esc(machine.id)}"><span>Screen${betaPill('computer_screen')}</span>${repoSegHTML(`screen-${machine.id}`, 'Screen', [['off', 'Off'], ['view', 'View'], ['control', 'Control']], screen, !online, ' repo-seg-sm')}</div>` : '';
  const link = machineScreenHTML(machine);
  return choice || link ? `${choice}${link}` : `<p class="muted">${machine.screen_available ? 'Screen off.' : 'This computer has not turned its screen on.'}</p>`;
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
  const conns = machineReported(machine) ? machineConnections(machine) : [];
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
// Redraw the open pop-up after Settings loads again; a form being typed in is left alone.
function computerDetailPaint(force = false) {
  const dialog = $('#set-machines dialog.computer-detail');
  if (!dialog?.dataset.computer) return;
  const machine = (SETTINGS_DATA?.machines || []).find(m => m.id === dialog.dataset.computer);
  if (!machine) { dialog.close(); return; }
  if (!force && formBusy(dialog)) return;
  const body = dialog.querySelector('.computer-detail-body'), scroll = body?.scrollTop || 0;
  const open = new Set([...dialog.querySelectorAll('details[open]')].map(node => node.dataset.machineDetails || node.dataset.profile || ''));
  dialog.innerHTML = computerDetailHTML(machine);
  dialog.querySelectorAll('details').forEach(node => { node.open = open.has(node.dataset.machineDetails || node.dataset.profile || ''); });
  if (!force) dialog.querySelector('.computer-detail-body').scrollTop = scroll;
}
