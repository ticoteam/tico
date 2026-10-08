/* ui/app/settings-devices.js — Settings > Computers: computers, enrollment, API tokens, agent credentials, setup prompts
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// An external agent (a Hermes profile) has no computer: the cell holds its credential instead.
// The token is shown once, when it is minted; after that only rotate and revoke remain.
const agentKind = a => `${settingsHarnessName(a.harness) || a.harness} agent`;
// An imported snapshot's freshness says nothing about its external process.
const agentPresenceLabel = (a, online) => a.synced ? (online ? 'History synced recently' : a.last_seen ? 'History not synced lately' : 'History not synced yet')
  : !a.credential ? 'No credential yet' : online ? 'Reporting in' : 'Not reporting';
function settingsAgentCell(e) {
  const a = e.agent, name = agentKind(a);
  const manage = settingsCanManageBot(e);
  if (a.synced) return `<div class="settings-agent-cell"><strong>${esc(name)}</strong>${a.profile ? ` · <span class="muted">${esc(a.profile)}</span>` : ''}
    <span class="settings-cell-note">${esc(a.last_seen ? `synced ${ago(a.last_seen)}${a.detail ? ` by ${a.detail}` : ''}` : 'not synced yet')}</span></div>`;
  const seen = a.last_seen ? `last seen ${ago(a.last_seen)}` : a.credential ? 'credential issued · no heartbeat yet' : 'no credential yet';
  return `<div class="settings-agent-cell"><strong>${esc(name)}</strong>${a.profile ? ` · <span class="muted">${esc(a.profile)}</span>` : ''}
    <span class="settings-cell-note">${esc(seen)}</span>
    ${manage ? `<span class="settings-agent-actions">${agentPairButton(e)}<button class="ghost" type="button" data-agent-credential="${esc(e.name)}">${a.credential ? 'Rotate credential' : 'Create credential'}</button>${a.credential ? `<button class="ghost" type="button" data-agent-revoke="${esc(e.name)}">Revoke</button>` : ''}</span>` : ''}</div>`;
}
// Pair: the code a Hermes profile printed, typed here; approving hands the profile its credential (backend/agents.py).
const agentPairButton = e => ['hermes', 'openclaw'].includes(e.agent?.harness) ? `<button class="ghost" type="button" data-agent-pair="${esc(e.name)}">Pair</button>` : '';
function settingsAgentPair(slug) {
  const e = S.emps.find(row => row.name === slug); if (!e) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal agent-credential';
  dialog.innerHTML = `<form method="dialog"><header><h2>Pair ${esc(e.display_name)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="agent-credential-body">
      <label>Code<input name="code" type="text" required autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="12" placeholder="K7QM-4F2P"></label>
      <p class="muted">Printed by <code>hermes_agent.py pair</code> on the profile's computer (Hermes or OpenClaw).</p>
      <p class="muted" data-pair-preview></p>
      <div class="row"><button class="primary" type="submit">Pair</button><span class="muted" data-pair-status></span></div>
    </div></form>`;
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-pair-status]');
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  const preview = dialog.querySelector('[data-pair-preview]');
  let previewCode = '', pendingPreview;
  const loadPreview = async () => {
    const code = form.code.value.trim();
    if (code.replace(/[^a-z0-9]/gi, '').length !== 8) { preview.textContent = ''; previewCode = ''; return; }
    const shown = await get(`/v2/agents/pairing-preview?code=${encodeURIComponent(code)}`);
    if (form.code.value.trim() !== code) return;
    previewCode = code;
    preview.textContent = `${shown.profile || 'Profile'} · ${shown.host || 'Computer'} · ${settingsHarnessName(shown.harness) || shown.harness}${e.agent?.credential ? ' · replaces current credential' : ''}`;
  };
  form.code.oninput = () => {
    clearTimeout(pendingPreview); previewCode = '';
    pendingPreview = setTimeout(() => void loadPreview().catch(error => { preview.textContent = error.message; }), 250);
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('button[type=submit]'); button.disabled = true; status.textContent = 'Pairing…';
    try {
      if (previewCode !== form.code.value.trim()) await loadPreview();
      const done = await post('/v2/agents/pairings/approve', {code: form.code.value.trim(), bot: slug});
      toast(`Paired ${done.profile || 'the profile'}${done.host ? ` on ${done.host}` : ''}`);
      dialog.close();
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; button.disabled = false; }
  };
  dialog.onclose = () => { clearTimeout(pendingPreview); dialog.remove(); void loadSettings(); };
  document.body.appendChild(dialog); dialog.showModal(); form.code.focus();
}
async function settingsAgentCredential(slug) {
  const e = S.emps.find(row => row.name === slug); if (!e) return;
  const rotating = !!e.agent?.credential;
  if (rotating && !confirm(`Rotate the agent credential for ${e.display_name}? The current token stops working at once; install the new one on its computer.`)) return;
  let issued;
  try { issued = await post(`/v2/bots/${encodeURIComponent(slug)}/agent-credential`, {}); }
  catch (error) { toast(error.message, true); return; }
  const setup = issued.setup, yaml = agentConfigYaml(setup);
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal agent-credential';
  dialog.innerHTML = `<header><h2>${rotating ? 'New agent credential' : 'Agent credential'} for ${esc(e.display_name)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="agent-credential-body">
      <p>Shown once. It is this bot's identity; revoking it stops the bot at once.</p>
      <label>Token<div class="row"><code class="agent-token" data-token>${esc(issued.token)}</code><button class="ghost" type="button" data-copy-token>Copy</button></div></label>
      <label>On the computer that runs the ${e.agent?.harness === 'openclaw' ? 'OpenClaw' : 'Hermes'} profile, one command installs Tico as an MCP server in the profile and a heartbeat timer:
        <div class="row"><code data-command>curl -fsSL -H "Authorization: Bearer ${esc(issued.token)}" ${esc(setup.url)}/api/v2/agents/setup-script -o hermes_agent.py &amp;&amp; python3 hermes_agent.py install${e.agent?.harness === 'openclaw' ? ' --harness openclaw' : ''} --profile ${esc(e.agent?.profile || slug)} --url ${esc(setup.url)} --bot ${esc(slug)} --token ${esc(issued.token)}</code><button class="ghost" type="button" data-copy-command>Copy</button></div></label>
      <details><summary>Or by hand: the profile's config.yaml and the heartbeat</summary>
        <pre data-yaml>${esc(yaml)}</pre>
        <p class="muted">Heartbeat: <code>POST ${esc(setup.url)}${esc(setup.heartbeat.path)}</code> with <code>Authorization: Bearer &lt;token&gt;</code> every ${setup.heartbeat.every_seconds} s; the bot shows offline after ${setup.heartbeat.offline_after_seconds} s without one.</p>
      </details>
    </div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.querySelector('[data-copy-token]').onclick = () => void copyText(issued.token).then(() => toast('Token copied'));
  dialog.querySelector('[data-copy-command]').onclick = () => void copyText(dialog.querySelector('[data-command]').textContent).then(() => toast('Command copied'));
  dialog.onclose = () => { dialog.remove(); void loadSettings(); };
  document.body.appendChild(dialog); dialog.showModal();
}
// Personal API tokens (backend/personal_tokens.py): listed, made and revoked only from a signed-in
// browser; the secret is shown once, in the same dialog the agent credential uses. The owner and the
// admins see everyone's tokens, with whose each is, and may revoke any of them.
async function renderSettingsTokens() {
  const el = $('#set-tokens'); if (!el) return;
  const everyone = settingsIsAdmin();
  let rows;
  try { rows = (await get(everyone ? '/v2/access/tokens' : '/v2/me/tokens')).tokens || []; }
  catch (error) { el.innerHTML = `<div class="err">${esc(error.message || 'Tokens could not be loaded')}</div>`; return; }
  const now = Date.now();
  const state = row => row.revoked_at ? '<span class="pill fail">revoked</span>'
    : row.expires_at && new Date(row.expires_at) < now ? '<span class="pill fail">expired</span>' : '<span class="pill ok">active</span>';
  const live = rows.filter(row => !row.revoked_at);
  const table = live.length ? `<div class="scroll"><table class="settings-table"><thead><tr>${everyone ? '<th>Person</th>' : ''}<th>Label</th><th>Created</th><th>Last used</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>${live.map(row =>
    `<tr data-token-row="${esc(row.id)}">${everyone ? `<td>${esc(row.name || row.email || row.human)}</td>` : ''}<td><strong>${esc(row.label)}</strong></td><td>${esc(ago(row.created))}</td><td>${row.last_used ? esc(ago(row.last_used)) : '<span class="muted">never</span>'}</td><td>${row.expires_at ? esc(new Date(row.expires_at).toLocaleDateString()) : '<span class="muted">never</span>'}</td><td>${state(row)}</td><td><button class="ghost" type="button" data-token-revoke="${esc(row.id)}" data-token-label="${esc(row.label)}">Revoke</button></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty">No tokens yet.</div>';
  // Only the list is redrawn; the form under it keeps what is being typed.
  if (!el.querySelector('[data-tokens-list]')) el.innerHTML = `<div data-tokens-list></div>${settingsCanMakeTokens() ? `
    <form class="row settings-token-form" id="settings-token-form">
      <label>Label <input name="label" type="text" required maxlength="80" autocomplete="off" placeholder="CI script" aria-label="Token label"></label>
      <label>Expires in <input name="days" type="number" inputmode="numeric" min="1" max="365" value="90" required aria-label="Days until the token expires"> days</label>
      <button class="primary" type="submit">New token</button>
    </form>` : ''}`;
  el.querySelector('[data-tokens-list]').innerHTML = table;
  el.querySelectorAll('[data-token-revoke]').forEach(button => { button.onclick = () => void settingsTokenRevoke(button.dataset.tokenRevoke, button.dataset.tokenLabel); });
  const form = el.querySelector('#settings-token-form');
  if (form) form.onsubmit = async event => {
    event.preventDefault();
    const label = form.label.value.trim(), days = Number(form.days.value);
    if (!label) { form.label.focus(); return; }
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try {
      const issued = await post('/v2/me/tokens', {label, expires_in_days: days});
      form.reset();
      settingsTokenShow(issued);
    } catch (error) { toast(error.message, true); button.disabled = false; }
  };
}
function settingsTokenShow(issued) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal agent-credential';
  dialog.innerHTML = `<header><h2>Token ${esc(issued.label)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="agent-credential-body">
      <p>Shown once. It acts as you, and expires ${esc(new Date(issued.expires_at).toLocaleDateString())}.</p>
      <label>Token<div class="row"><code class="agent-token" data-token>${esc(issued.token)}</code><button class="ghost" type="button" data-copy-token>Copy</button></div></label>
      <label>In a shell, for the <code>hub</code> command line and scripts:<div class="row"><code data-command>export HUB_API_URL=${esc(location.origin)} HUB_TOKEN=${esc(issued.token)}</code><button class="ghost" type="button" data-copy-command>Copy</button></div></label>
    </div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.querySelector('[data-copy-token]').onclick = () => void copyText(issued.token).then(() => toast('Token copied'));
  dialog.querySelector('[data-copy-command]').onclick = () => void copyText(dialog.querySelector('[data-command]').textContent).then(() => toast('Command copied'));
  dialog.onclose = () => { dialog.remove(); void renderSettingsTokens(); };
  document.body.appendChild(dialog); dialog.showModal();
}
// Connect an agent: ui/connect-agent.js.
$('#connect-agent').onclick = () => window.connectAgent?.();
async function settingsTokenRevoke(id, label) {
  if (!confirm(`Revoke the token ${label}? Anything using it stops at once.`)) return;
  try { await post(`/v2/me/tokens/${encodeURIComponent(id)}/revoke`, {}); toast(`Token ${label} revoked`); }
  catch (error) { toast(error.message, true); }
  await renderSettingsTokens();
}
function agentConfigYaml(setup) {
  return `mcp_servers:\n  tico:\n    url: "${setup.mcp_servers.tico.url}"\n    headers:\n      Authorization: "Bearer \${TICO_AGENT_TOKEN}"\n# .env in the profile directory:\nTICO_AGENT_TOKEN=${setup.token}`;
}
async function settingsAgentRevoke(slug) {
  const e = S.emps.find(row => row.name === slug); if (!e) return;
  if (!confirm(`Revoke the agent credential for ${e.display_name}? Its computer loses access at once; the bot record stays.`)) return;
  try { await post(`/v2/bots/${encodeURIComponent(slug)}/agent-credential/revoke`, {}); toast(`${e.display_name} credential revoked`); await loadSettings(); }
  catch (error) { toast(error.message, true); }
}
// The Add computer flow, shared by Settings and the first-run wizard: one private short-lived
// setup file, downloaded by the browser. The commands that consume it are shown by the caller.
// A Linux server, a Mac with Docker Desktop and a Windows PC (WSL 2) need no file: the one-time code goes on the line.
async function enrollmentCode(operator) { return post('/v2/enrollments', {operator}); }
const shellSafe = value => String(value).replace(/["$`\\\n]/g, '');
// The release the server runs, as the tag its installer and images carry; '' for a build with none.
const serverReleaseTag = () => {
  const version = String(S.config?.runner_compat?.version || '').replace(/^v/, '');
  return /^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$/.test(version) ? 'v' + version : '';
};
// The installer sets the runner up with its updater sidecar, so it follows the server's releases;
// a bare `docker run` has no sidecar and stays where it is.
// The container name follows from the code, so the commands of one Add computer agree with each other.
// It starts with a letter or digit: install.sh and install-wsl.ps1 accept no other first character for --name.
const runnerName = code => `${String(S.config?.compose_project || 'tico').toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+/, '').slice(0, 20) || 'tico'}-${String(code).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8) || 'computer'}`;
function dockerRunnerCommands(code, label, runtime, kind = 'linux') {
  const tag = serverReleaseTag(), quoted = `"${shellSafe(label)}"`;
  const installer = tag ? `https://github.com/ticoteam/tico/releases/download/${tag}/install.sh`
                        : 'https://github.com/ticoteam/tico/releases/latest/download/install.sh';
  // A server that answers on this computer only (the quick start) is not reachable at 127.0.0.1 from inside the
  // runner's container, which has a loopback of its own. The runner joins the server's Docker network instead.
  const local = !!S.config?.local;
  const join = local ? '--url http://server:8765' : `--url ${runnerUrl()}`;
  const network = String(S.config?.server_network || 'tico_default').replace(/[^a-zA-Z0-9_.-]/g, '');
  const name = runnerName(code);
  const container = `tico-runner-${name}`;
  return [
    [local ? 'Set up the runner on this computer (installs Docker if it is missing; keeps itself on this server\'s release)'
           : kind === 'mac' ? 'Set up the runner on that Mac (Docker Desktop must be running; keeps itself on this server\'s release)'
           : 'Set up the runner on the server (installs Docker if it is missing; keeps itself on this server\'s release)',
     `curl -fsSL ${installer} | sh -s -- --runner --name ${name} ${join}${local ? ` --server-network ${network}` : ''} --code '${code}' --label ${quoted}`],
    // Signing in to a model comes only once a provider is chosen; before that there is no model to name.
    // `claude auth login` signs the CLI in; `claude setup-token` would only print a token. As `bot`, the user turns run as.
    runtime ? ['Sign the bots in to a model, once: Sign in on Settings > Computers, or run this (the runner installs the model CLI first; give it a minute)',
     `docker exec -it -u bot ${container} ${runtime === 'claude' ? 'claude auth login' : 'codex login --device-auth'}`] : null,
    ['Or with plain Docker instead of the line above. It has no updater, so it will not follow the server\'s releases',
     `docker run -d --name ${container} --restart unless-stopped${local ? ` --network ${network}` : ''} -v ${container}_runner-home:/home/runner ghcr.io/ticoteam/tico-runner:${tag || 'latest'} join ${join} --code '${code}' --label ${quoted}`],
  ];
}
// A Windows PC runs the Linux runner in WSL 2 (infra/windows/install-wsl.ps1, published with each release).
// The code is quoted: a code may start with '-', which PowerShell would read as a parameter name.
function windowsRunnerCommands(code, label) {
  const tag = serverReleaseTag();
  const script = tag ? `https://github.com/ticoteam/tico/releases/download/${tag}/install-wsl.ps1`
                     : 'https://github.com/ticoteam/tico/releases/latest/download/install-wsl.ps1';
  const quoted = `'${shellSafe(label).replace(/'/g, "''")}'`;
  return [['In PowerShell as administrator on that PC. Sets up WSL 2 and Ubuntu if needed (may ask for a restart), keeps Ubuntu running while you are signed in, and turns off sleep on mains power (add -AllowSleep to skip)',
    `& ([scriptblock]::Create((irm ${script}))) -Url ${runnerUrl()} -Code '${code}' -Label ${quoted} -Name ${runnerName(code)}`]];
}
async function enrollmentDownload(operator, label) {
  const filename = `tico-enrollment-${operator}-${Date.now().toString(36)}.json`;
  const enrollment = await enrollmentCode(operator);
  const setup = {...enrollment, operator, label, url: runnerUrl()};
  const url = URL.createObjectURL(new Blob([JSON.stringify(setup)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return filename;
}
// One chip per AI tool on a computer: its version and whether it is signed in, from the computer's readiness
// (`harnesses` per tool, `runtimes` per runtime; an older runner sends only `runtimes`). Only what the team's providers
// or an assigned bot use, plus anything installed anyway. Codex and Claude Code sign in from here: the computer runs
// the login as the bot user and relays only the link and one-time code (backend/model_login.py, runner/login.py).
// The others have no login the computer can relay, so the chip says what to run there.
const MACHINE_SIGNIN = ['codex', 'claude'];
const MACHINE_LOGIN_COMMANDS = {grok: 'grok login', cursor: 'cursor-agent login'};   // backend/providers.py `login`
const MACHINE_KEY_ONLY = ['gemini', 'pi'];
// The server's rule (model_login._operator): the owner, an admin, or the person whose computer it is.
const machineCanSignIn = machine => settingsIsAdmin() || (!!S.me?.id && machine.operator === S.me.id);
function machineLoginCommand(machine, name) {
  const command = MACHINE_LOGIN_COMMANDS[name];
  return command && machine.update?.kind === 'docker' ? `docker exec -it -u bot <container> ${command}` : command;
}
function machineToolsHtml(machine, online) {
  const needed = Array.isArray(machine.needed_runtimes) ? new Set(machine.needed_runtimes) : null;
  const isNeeded = name => !needed || needed.has(name);
  const runtimes = machine.readiness?.runtimes || {};
  const tools = Object.values(machine.readiness?.harnesses || {});
  const toolFor = name => tools.find(tool => tool.runtime === name && tool.installed) || tools.find(tool => tool.runtime === name) || {};
  const names = [...new Set([...Object.keys(runtimes), ...tools.filter(tool => tool.installed).map(tool => tool.runtime)])]
    .filter(name => isNeeded(name) || runtimes[name]?.installed || toolFor(name).installed);
  const manage = machineCanSignIn(machine) && !machine.revoked_at;
  return names.map(name => {
    const tool = toolFor(name), value = runtimes[name] || {installed: !!tool.installed, authenticated: tool.authenticated};
    const installed = value.installed || tool.installed, auth = value.authenticated;
    const state = !installed ? 'not installed' : auth === 'ready' ? 'signed in' : auth === 'rejected' ? 'sign-in rejected'
      : auth === 'unknown' ? 'checking' : 'not signed in';
    const tone = auth === 'ready' ? 'ok' : auth === 'rejected' ? 'fail' : auth === 'unknown' || !isNeeded(name) ? 'waiting' : 'fail';
    const label = `${tool.name || harnessWords(name)}${installed && tool.version ? ' ' + tool.version : ''} · ${state}`;
    let action = '';
    if (manage && installed && auth !== 'ready') {
      const command = machineLoginCommand(machine, name);
      if (MACHINE_SIGNIN.includes(name)) action = online
        ? ` <button class="ghost machine-signin" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(name)}" data-machine="${esc(machine.label)}">Sign in</button>` : '';
      else if (command) action = ` <span class="machine-login-command" title="Run on the computer${command.includes('<container>') ? '; docker ps shows the container name' : ''}"><code>${esc(command)}</code><button class="ghost machine-signin" type="button" data-machine-copy-login="${esc(command)}">Copy</button></span>`;
      else if (MACHINE_KEY_ONLY.includes(name)) action = ' <a class="machine-signin-link" href="#/credentials">Add key</a>';
    }
    const rejected = auth === 'rejected'
      ? `<span class="err machine-rejected" data-rejected="${esc(name)}">${value.rejected_at ? esc(ago(value.rejected_at)) + '. ' : ''}${value.rejected_reason ? esc(value.rejected_reason) + '. ' : ''}${hlCredentialFix(value.credential_source)}. It takes no work that needs ${esc(name)} until then.</span>` : '';
    return `<span class="machine-runtime-item" data-machine-tool="${esc(name)}"><span class="pill ${tone}" title="${esc(value.detail || '')}">${esc(label)}</span>${action}${rejected}</span>`;
  }).join('');
}
function renderSettingsMachines() {
  const el = $('#set-machines'); if (!el) return;
  const expanded = new Set([...el.querySelectorAll('details[data-machine-details][open]')].map(node => node.dataset.machineDetails));
  const cards = SETTINGS_DATA.machines.map(machine => {
    const online = !machine.revoked_at && machine.last_seen && Date.now() - new Date(machine.last_seen) < 60000;
    const tools = machineToolsHtml(machine, online);
    // The model CLIs the computer runs: version, pin, and the owner's Update / Pin actions. The
    // computer applies them between turns (backend/harness_actions.py, runner/harness_tools.py).
    const asked = new Set((machine.harness_actions || []).filter(row => ['requested', 'running'].includes(row.state)).map(row => `${row.harness}:${row.action}`));
    const harnessAction = (id, action, label, value) => {
      const waiting = asked.has(`${id}:${action}`);
      return ` <button class="ghost" type="button" data-harness-action="${esc(action)}" data-runner="${esc(machine.id)}" data-harness="${esc(id)}"${waiting ? ' disabled' : ''}>${waiting ? 'Requested' : esc(label)}</button>`;
    };
    const harnessChip = ([id, value]) => {
      const owner = S.me?.role === 'owner' && online && value.managed;
      const busy = value.state !== 'idle';
      const text = `${value.name || id} ${value.installed ? (value.version || 'installed') : 'not installed'}${value.pinned ? ' · pinned' : ''}${value.update_available ? ` · ${value.latest || 'update'} available` : ''}${busy ? ` · ${value.state}` : ''}`;
      const tone = value.state === 'failed' ? 'fail' : !value.installed || busy ? 'waiting' : value.update_available ? 'waiting' : 'ok';
      const buttons = owner && value.installed && !busy ? [
        value.update_available ? harnessAction(id, 'update', 'Update') : '',
        value.pinned ? harnessAction(id, 'unpin', 'Resume updates') : (value.version ? harnessAction(id, 'pin', 'Pin to this version') : '')].join('') : '';
      const title = [value.detail, value.managed ? '' : (value.installed ? 'Installed outside Tico; update it where it was installed' : '')].filter(Boolean).join(' · ');
      return `<span class="machine-harness-item" data-harness-chip="${esc(id)}"><span class="pill ${tone}" title="${esc(title)}">${esc(text)}</span>${buttons}</span>`;
    };
    const harnesses = Object.entries(machine.readiness?.harnesses || {}).filter(([, value]) => value.installed || value.wanted || value.state !== 'idle').map(harnessChip).join('');
    const failures = Object.values(machine.readiness?.bots || {}).filter(value => !value.ready).length;
    const botLink = slug => `<a href="#/bot/${encodeURIComponent(slug)}/more">${esc(settingsBotName(slug))}</a>`;
    const bots = machine.bots || [];
    const botList = bots.slice(0, 3).map(botLink).join(', ');
    const moreBots = bots.length > 3 ? `<details data-machine-details="${esc(machine.id)}:bots"><summary>+${bots.length - 3} more bots</summary>${bots.slice(3).map(botLink).join(', ')}</details>` : '';

    return `<tr class="machine-card"><td><strong>${esc(machine.label)}</strong><span class="settings-cell-note">${esc(machine.version || 'version not reported')}${machine.platform ? ` · ${esc(machine.platform)}` : ''}</span>${window.runnerUpdateHtml?.(machine.update, machine.version) || ''}</td>
      <td data-label="Operator">${esc(settingsPersonName(machine.operator))}${machine.revoked_at ? '' : settingsIsAdmin()
        ? `<label class="settings-cell-note machine-members"><input type="checkbox" data-member-bots="${esc(machine.id)}" ${machine.accepts_member_bots ? 'checked' : ''}> Accepts members' bots</label>`
        : machine.accepts_member_bots ? '<span class="settings-cell-note">Accepts members\' bots</span>' : ''}</td>
      <td data-label="Bots"><strong>${bots.length} bot${bots.length === 1 ? '' : 's'}</strong>${bots.length ? `<div class="settings-cell-note">${botList}${moreBots}<div><a href="#/settings" data-computer-reassign>Manage bot assignments</a></div></div>` : ''}${failures ? ` <span class="err">${failures} not ready</span>` : ''}</td>
      <td data-label="AI tools"><div class="machine-runtime" aria-label="AI tools on ${esc(machine.label)}">${tools || '<span class="muted">No AI tools reported</span>'}</div>${harnesses ? `<details data-machine-details="${esc(machine.id)}:tools"><summary>Updates</summary><div class="machine-harnesses">${harnesses}</div></details>` : ''}</td>
      <td data-label="Status">${machine.revoked_at ? '<span class="pill fail">revoked</span>' : online ? '<span class="pill ok">online</span>' : '<span class="pill">offline</span>'}${machine.last_seen ? `<span class="settings-cell-note">${esc(ago(machine.last_seen))}</span>` : ''}${!machine.revoked_at && (settingsIsAdmin() || machine.operator === S.me?.id) ? `<button class="ghost" type="button" data-computer-remove="${esc(machine.id)}">Remove computer</button>` : ''}</td></tr>`;
  }).join('');
  el.onchange = async event => {
    const box = event.target.closest('[data-member-bots]');
    if (!box) return;
    box.disabled = true;
    try {
      await post(`/v2/computers/${encodeURIComponent(box.dataset.memberBots)}/member-bots`, {accepts: box.checked});
      toast(box.checked ? 'Members\' bots may now go on this computer' : 'Members\' bots no longer go on this computer');
      await loadSettings();
    } catch (error) { toast(error.message, true); box.checked = !box.checked; box.disabled = false; }
  };
  el.onclick = async event => {
    if (event.target.closest('[data-computer-reassign]')) { settingsShow('bots'); return; }
    const login = event.target.closest('[data-machine-copy-login]');
    if (login) { void copyText(login.dataset.machineCopyLogin).then(() => toast('Command copied')); return; }
    const remove = event.target.closest('[data-computer-remove]');
    if (remove) {
      remove.disabled = true;
      try {
        await post(`/v2/computers/${encodeURIComponent(remove.dataset.computerRemove)}/revoke`, {});
        await loadSettings();
        $('#machine-enroll-status').innerHTML = 'Computer removed. Its bots wait until reassigned in Bots. For Docker, stop its runner from its install directory: <code>docker compose -f runner.compose.yaml down</code>. Include <code>-f runner.override.yaml</code> before <code>down</code> if that file is present. This keeps repositories and sign-in.';
        toast('Computer removed');
      } catch (error) { toast(error.message, true); remove.disabled = false; }
      return;
    }
    const button = event.target.closest('[data-harness-action]');
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
      await post(`/v2/computers/${encodeURIComponent(button.dataset.runner)}/harness-actions`, {harness: button.dataset.harness, action: button.dataset.harnessAction});
      toast(button.dataset.harnessAction === 'update' ? 'Update requested; the computer applies it when no run is using it' : 'Saved');
      await loadSettings();
    } catch (error) { toast(error.message, true); button.disabled = false; }
  };
  const people = S.me?.role === 'owner' ? SETTINGS_DATA.people : SETTINGS_DATA.people.filter(person => person.id === S.me?.id);
  const agents = (SETTINGS_DATA.agents || []).map(agent => `<tr class="machine-card" data-agent-row="${esc(agent.bot)}"><td><strong><a href="#/bot/${esc(agent.bot)}">${esc(agent.display_name || agent.bot)}</a></strong><span class="settings-cell-note">${esc(agentKind(agent))}${agent.profile ? ` · profile ${esc(agent.profile)}` : ''}${agent.version ? ` · ${esc(agent.version)}` : ''}${agent.platform ? ` · ${esc(agent.platform)}` : ''}</span></td>
      <td>${esc(settingsPersonName(S.emps.find(row => row.name === agent.bot)?.operator))}</td>
      <td>${agent.model ? `${esc(agent.model)}${agent.provider ? `<span class="settings-cell-note">${esc(agent.provider)}</span>` : ''}` : '<span class="muted">—</span>'}</td>
      <td>${agent.revoked_at ? '<span class="pill fail">revoked</span>' : agent.online ? '<span class="pill ok">reporting in</span>' : '<span class="pill">not reporting</span>'}${agent.last_seen ? `<span class="settings-cell-note">${esc(ago(agent.last_seen))}</span>` : ''}</td></tr>`).join('');
  const list = `${cards ? `<div class="scroll"><table class="settings-table settings-machines"><thead><tr><th>Computer</th><th>Operator</th><th>Bots</th><th>AI tools</th><th>Status</th></tr></thead><tbody>${cards}</tbody></table></div>` : '<div class="empty">No computers yet.</div>'}
    ${agents ? `<h3 class="settings-agents-title">External agents</h3><div class="scroll"><table class="settings-table"><thead><tr><th>Agent</th><th>Owner</th><th>Model</th><th>Status</th></tr></thead><tbody>${agents}</tbody></table></div>` : ''}`;
  // Only the list is redrawn; the Add computer form (and the code it shows) keeps what was typed.
  if (!el.querySelector('.machine-enroll')) el.innerHTML = `<div data-machines-list></div>
    <div class="machine-enroll"><select class="settings-inline-select" id="machine-operator" aria-label="Computer owner">
      ${people.map(person => `<option value="${esc(person.id)}" ${person.id === S.me?.id ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}</select>
      <select class="settings-inline-select" id="machine-kind" aria-label="Kind of computer"><option value="mac">Mac</option><option value="linux">Linux or cloud server</option><option value="windows">Windows PC (WSL 2, beta)</option></select>
      <input id="machine-label" type="text" autocomplete="off" aria-label="Computer name" placeholder="Computer name" value="${esc(settingsPersonName(people.find(person => person.id === S.me?.id)?.id || people[0]?.id) + "'s Mac")}">
      <button class="primary" type="button" id="register-machine">Add computer</button>
      <div class="machine-enroll-status" id="machine-enroll-status"></div></div>`;
  el.querySelector('[data-machines-list]').innerHTML = `<p class="settings-cell-note">Computers run your bots. The operator manages the computer; each bot can use its own named subscription.</p>${list}`;
  el.querySelectorAll('details[data-machine-details]').forEach(node => { node.open = expanded.has(node.dataset.machineDetails); });
  const machineDefaultLabel = () => $('#machine-kind').value === 'linux' && S.config?.local ? 'This computer'
    : `${settingsPersonName($('#machine-operator').value)}'s ${({linux: 'server', windows: 'PC'})[$('#machine-kind').value] || 'Mac'}`;
  $('#machine-operator').onchange = () => { $('#machine-label').value = machineDefaultLabel(); };
  $('#machine-kind').onchange = () => { $('#machine-label').value = machineDefaultLabel(); };
  $('#register-machine').onclick = async event => {
    const operator = $('#machine-operator').value, label = $('#machine-label').value.trim();
    if (!label) { toast('Give this computer a recognizable name', true); return; }
    event.target.disabled = true;
    try {
      const status = $('#machine-enroll-status'), kind = $('#machine-kind').value;
      const {code} = await enrollmentCode(operator);
      // Settings signs the computer in from its row, so the Docker sign-in line is left to the setup wizard.
      const [main, ...more] = kind === 'windows' ? windowsRunnerCommands(code, label)
        : dockerRunnerCommands(code, label, '', kind).filter(Boolean);
      const line = ([note, command], i) => `<span class="muted">${esc(note)}</span><span class="machine-command"><code>${esc(command)}</code><button class="ghost" type="button" data-machine-copy="${i}">Copy</button></span>`;
      status.innerHTML = `<span>Code for <strong>${esc(label)}</strong>, good 15 minutes. Then sign it in here.</span>${line(main, 0)}`
        + (more.length ? `<details><summary>Other ways</summary>${more.map((row, i) => line(row, i + 1)).join('')}</details>` : '')
        + (kind === 'mac' ? '<button class="ghost" type="button" id="machine-checkout">Use a Tico checkout instead</button>' : '');
      const commands = [main, ...more];
      status.querySelectorAll('[data-machine-copy]').forEach(button => button.onclick = () =>
        void copyText(commands[Number(button.dataset.machineCopy)][1]).then(() => toast('Command copied')));
      const checkout = $('#machine-checkout');
      if (checkout) checkout.onclick = async () => {
        checkout.disabled = true;
        try {
          const filename = await enrollmentDownload(operator, label);
          status.innerHTML = `Setup file downloaded. Run in the Tico checkout on that Mac:<span class="machine-command"><code>scripts/setup-runner.sh "$HOME/Downloads/${esc(filename)}"</code><button class="ghost" type="button" id="copy-enrollment-command">Copy</button></span><button class="ghost" type="button" id="copy-enrollment-prompt">Copy AI setup prompt</button>`;
          $('#copy-enrollment-command').onclick = () => void copyText(`scripts/setup-runner.sh "$HOME/Downloads/${filename}"`).then(() => toast('Setup command copied'));
          $('#copy-enrollment-prompt').onclick = () => void copyText(settingsEnrollmentPrompt(operator, filename)).then(() => toast('Computer setup prompt copied'));
        } catch (error) { toast(error.message, true); checkout.disabled = false; }
      };
    } catch (error) { toast(error.message, true); }
    finally { event.target.disabled = false; }
  };
}
function settingsSetupPrompt(slug) {
  const rows = (slug ? S.emps.filter(e => e.name === slug) : S.emps).map(e => {
    const owners = (e.users || []).map(person => person.name || person.id).join(', ') || 'nobody';
    const machine = e.machine?.label || `not assigned (reserved for ${settingsPersonName(e.operator)})`;
    return `- ${e.display_name} [${e.name}]: humans=${owners}; model=${e.model || 'not set'}; effort=${e.reasoning_effort || e.effort || 'not set'}; computer=${machine}`;
  });
  return `Help me update the ${appName()} bot setup.\n\nCurrent cloud state:\n${rows.join('\n')}\n\nAsk me what I want to change, then use the formal owner-admin API. Do not edit the production SQLite database directly. Bot definitions in the ${appName()} backend are authoritative after the one-time bootstrap: create a bot with POST /api/v2/bots and update its name, description, hierarchy, status, repository, or room type with POST /api/v2/bots/{bot}/definition. No ${appName()} application deploy or registry publish is required. Provision the bot's repository on any computer that will run it, and verify computer readiness. Preserve tasks and conversation history. Change a bot's model, reasoning effort, or registered computer through POST /api/v2/bots/{bot}/transitions so ${appName()} checkpoints every current session before applying it. If the transition is blocked because the old computer is unavailable, explain what continuity will be lost and ask me before calling apply-without-checkpoint. Use revision and assignment-generation checks. Humans are still managed separately from bot definitions.`;
}
function settingsEnrollmentPrompt(operator, filename) {
  return `Set up a ${appName()} local runner for ${settingsPersonName(operator)} using the downloaded ${filename} enrollment file. Work from the Tico checkout on the target Mac. Locate the human's bot repositories, run scripts/setup-runner.sh "$HOME/Downloads/${filename}", then run runtime/runner-venv/bin/python -m runner doctor and resolve every repository, runtime login (Codex or Claude), model, or configuration-readiness problem. Never print or commit the enrollment code or runner token and never open an inbound port. Do not install the private processing or connector workers. Registration must not assign bots; when the computer is ready, tell me to return to ${appName()} Settings and choose it for each intended bot so existing sessions are checkpointed before the move.`;
}
function settingsCopyPrompt(slug) {
  void copyText(settingsSetupPrompt(slug)).then(() => toast(`${slug ? settingsBotName(slug) + ' ' : ''}setup prompt copied`)).catch(() => toast('Could not copy the setup prompt', true));
}
