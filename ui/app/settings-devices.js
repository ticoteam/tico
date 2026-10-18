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
    <span class="settings-cell-note">${esc(a.last_seen ? `synced ${ago(a.last_seen)}${a.detail ? ` by ${a.detail}` : ''}` : 'not synced yet')}</span>${settingsAppDeliveryNote(e)}</div>`;
  const seen = a.last_seen ? `last seen ${ago(a.last_seen)}` : a.credential ? 'credential issued · no heartbeat yet' : 'no credential yet';
  return `<div class="settings-agent-cell"><strong>${esc(name)}</strong>${a.profile ? ` · <span class="muted">${esc(a.profile)}</span>` : ''}
    <span class="settings-cell-note">${esc(seen)}</span>
    ${manage ? `<span class="settings-agent-actions">${agentPairButton(e)}<button class="ghost" type="button" data-agent-credential="${esc(e.name)}">${a.credential ? 'Rotate credential' : 'Create credential'}</button>${a.credential ? `<button class="ghost" type="button" data-agent-revoke="${esc(e.name)}">Revoke</button>` : ''}</span>` : ''}</div>`;
}
// Where a synced bot's messages go when it is delivered through its desktop app (settings-bot-editor.js).
function settingsAppDeliveryNote(e) {
  const d = e.app_delivery; if (!d?.computer) return '';
  const where = SETTINGS_DATA.machines.find(machine => machine.id === d.computer)?.label || d.computer_label || 'a computer';
  return `<span class="settings-cell-note" data-app-delivery-note>through the ${esc(d.app_label || machineAppName(d.app))} app on ${esc(where)}</span>`;
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
  const login = harnessCaps(runtime).container_login;
  return [
    [local ? 'Set up the runner on this computer (installs Docker if it is missing; keeps itself on this server\'s release)'
           : kind === 'mac' ? 'Set up the runner on that Mac (Docker Desktop must be running; keeps itself on this server\'s release)'
           : 'Set up the runner on the server (installs Docker if it is missing; keeps itself on this server\'s release)',
     `curl -fsSL ${installer} -o install.sh && sh install.sh --runner --name ${name} ${join}${local ? ` --server-network ${network}` : ''} --code '${code}' --label ${quoted}`],
    // Signing in to a model comes only once a provider is chosen; before that there is no model to name, and a CLI
    // that signs in with a key has no login to run. `container_login` is the manifest's login for a terminal with no
    // browser. As `bot`, the user turns run as.
    login ? ['Sign the bots in to a model, once: Sign in on Settings > Computers, or run this (the runner installs the model CLI first; give it a minute)',
     `docker exec -it -u bot ${container} ${login}`] : null,
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
// One row per AI tool on a computer, in aligned parts: the tool, its installed version, whether it is enabled here, its
// sign-in and account, and its usage. From the computer's readiness (`harnesses` per tool, `runtimes` per runtime; an
// older runner sends only `runtimes`) and its named subscriptions (`profiles`, one line each under the tool). Only what
// its bots use (with no bot yet, the team's default), plus anything installed anyway. A CLI whose manifest has
// `browser_sign_in` signs in from here: the computer runs the login as the bot user and relays only the link and
// one-time code (backend/model_login.py, runner-rs/src/login.rs). One with only a `terminal_login` gets that command to
// run there; one whose keys belong to each bot (`api_keys = "bot"`) gets a link to Credentials.
// The account (email, plan) comes only from a provider's own status command (#610) and only to an administrator or the
// computer's operator; anything not reported reads unavailable, never a guessed value.
// The server's rule (model_login._operator): the owner, an admin, or the person whose computer it is.
const machineCanSignIn = machine => settingsIsAdmin() || (!!S.me?.id && machine.operator === S.me.id);
function machineLoginCommand(machine, name) {
  const command = harnessCaps(name).terminal_login;
  return command && machine.update?.kind === 'docker' ? `docker exec -it -u bot <container> ${command}` : command;
}
const machinePlan = plan => plan ? String(plan).replace(/^./, c => c.toUpperCase()) : '';
// A computer reads an account again every 10 minutes (runner-rs/src/accounts.rs READ_EVERY), the answer landing on a
// later heartbeat's report, which is kept a minute; a reading older than this has missed its refresh.
const MACHINE_ACCOUNT_FRESH_MS = 15 * 60000;
// "Signed in as <email> · <plan>" from an account; what the provider did not say is left out, not guessed. A reading
// that has missed its refresh says when it was last checked, since the login may have changed since.
function machineAccountHTML(account, runtime, title = '') {
  const who = account?.kind === 'api_key' ? 'with an API key · no plan usage'
    : [account?.email, machinePlan(account?.plan)].filter(Boolean).join(' · ');
  const rest = who ? (account?.kind === 'api_key' ? who : `as ${who}`)
    : harnessCaps(runtime).account ? '· account not reported yet' : '· account unavailable';
  const checked = Date.parse(account?.checked_at);
  const old = who && Number.isFinite(checked) && Date.now() - checked > MACHINE_ACCOUNT_FRESH_MS
    ? ` <span class="muted" data-account-checked>account last checked ${esc(ago(account.checked_at))}</span>` : '';
  return `<span class="pill ok"${title ? ` title="${esc(title)}"` : ''}>Signed in</span> <span class="machine-account${who ? '' : ' muted'}"${account?.org ? ` title="${esc(account.org)}"` : ''}>${esc(rest)}</span>${old}`;
}
// One usage window ("weekly" or "5-hour window"): what is left and when it resets, with the reading's age. A window with
// no provider percentage says so; it never reads as 0% or a full allowance.
function machineWindowHTML(label, reading, name) {
  if (!reading) return '';
  const percent = Number.isFinite(reading.used_percent) ? Math.min(100, Math.max(0, reading.used_percent)) : null;
  const reported = Date.parse(reading.reported_at), reset = Date.parse(reading.resets_at);
  const expired = Number.isFinite(reset) && reset <= Date.now();
  const stale = Number.isFinite(reported) && Date.now() - reported > 86400000;
  const left = percent === null ? null : Math.round(100 - percent);
  const value = expired ? 'previous window; refresh needed' : left === null ? 'percentage unavailable' : `${left}% left`;
  const notes = [Number.isFinite(reset) && !expired ? `resets ${new Date(reset).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'})}` : '',
    reading.status === 'rejected' ? 'limit reached' : '',
    Number.isFinite(reported) ? `${reading.source === 'manual' ? 'recorded' : 'as of'} ${ago(reading.reported_at)}${stale ? ', may be out of date' : ''}` : ''].filter(Boolean);
  const bar = left !== null && !expired
    ? `<meter class="machine-usage-bar" min="0" max="100" low="20" optimum="100" value="${left}" aria-label="${esc(name)} ${esc(label)}: ${left}% left"></meter>` : '';
  return `<span class="machine-usage-line${stale || expired ? ' stale' : ''}" data-usage-window="${esc(label)}">${bar}<span>${esc(label)} ${esc(value)}</span>${notes.length ? `<span class="muted">${esc(notes.join(' · '))}</span>` : ''}</span>`;
}
// A runtime whose usage comes only from its turns (`weekly_usage = "turns"`, Claude) has nothing to show before one:
// it says where the reading will come from (`where`: this computer, or this subscription).
function machineUsageHTML(runtime, state, name, where = 'this computer') {
  const caps = harnessCaps(runtime);
  if (!caps.weekly_usage) return '<span class="muted" data-usage-window="none">Usage unavailable</span>';
  if (state?.account?.kind === 'api_key') return '<span class="muted" data-usage-window="none">No plan usage (API key)</span>';
  const idle = caps.weekly_usage === 'turns' && !state?.weekly && !state?.five_hour
    ? ` · reported after a ${harnessWords(runtime)} turn on ${where}` : '';
  const weekly = machineWindowHTML('Weekly', state?.weekly, name) || `<span class="muted" data-usage-window="Weekly">Weekly usage unavailable${esc(idle)}</span>`;
  return weekly + machineWindowHTML('5-hour window', state?.five_hour, name);
}
// The sign-in cell: signed in as <account>, signed out, login expired or rejected, checking, or couldn't check (with
// when the computer last reported).
function machineSignInHTML(machine, online, name, auth, value) {
  const checked = machine.last_seen ? ` · last checked ${ago(machine.last_seen)}` : '';
  if (auth !== 'ready' && machineSignedOut(machine, name)) return '<span class="pill fail" title="Signed out from Tico: the company\'s shared key does not sign it back in by itself">Signed out</span>';
  if (auth === 'ready') return `${machineAccountHTML(value.account, name, value.detail)}${online ? '' : ` <span class="muted">${esc(checked.slice(3))}</span>`}`;
  if (auth === 'rejected') return `<span class="pill fail" title="${esc(value.detail || '')}">Login expired or rejected</span>`;
  if (auth === 'missing') return `<span class="pill fail" title="${esc(value.detail || '')}">Signed out</span>`;
  const couldNot = `<span class="pill waiting" title="${esc(value.detail || '')}">Couldn't check</span>${checked ? ` <span class="muted">${esc(checked.slice(3))}</span>` : ''}`;
  if (auth === 'failed') return couldNot;
  return online ? `<span class="pill waiting" title="${esc(value.detail || '')}">Checking…</span>` : couldNot;
}
// A login someone signed out from Tico (backend/model_login.py model_login_cleared); '' is the computer's own login.
const machineSignedOut = (machine, runtime, profile = '') => (machine.signed_out?.[runtime] || []).includes(profile);
// Sign out / Switch account (or Use the shared key) for one login, from the shared sign-in module (ui/model-login.js).
const machineLoginActions = (machine, online, runtime, profile, signedIn) => window.TicoModelLogin?.actionsHTML({
  runnerId: machine.id, runtime, machine: machine.label, profile, signedIn, signedOut: machineSignedOut(machine, runtime, profile), online,
  platform: machine.platform || ''}) || '';
function machineProfileSignIn(state, runtime) {
  if (state?.signed_in === true) return machineAccountHTML(state.account, runtime);
  if (state?.signed_in === false) return '<span class="pill fail">Signed out</span>';
  return '<span class="pill waiting">Couldn\'t check</span>';
}
function machineToolsHtml(machine, online) {
  const needed = Array.isArray(machine.needed_runtimes) ? new Set(machine.needed_runtimes) : null;
  const isNeeded = name => !needed || needed.has(name);
  const runtimes = machine.readiness?.runtimes || {};
  const tools = Object.entries(machine.readiness?.harnesses || {}).map(([id, tool]) => ({...tool, id}));
  const toolFor = name => tools.find(tool => tool.runtime === name && tool.installed) || tools.find(tool => tool.runtime === name) || {};
  const names = [...new Set([...Object.keys(runtimes), ...tools.filter(tool => tool.installed).map(tool => tool.runtime)])]
    .filter(name => isNeeded(name) || runtimes[name]?.installed || toolFor(name).installed);
  const manage = machineCanSignIn(machine) && !machine.revoked_at;
  const choices = machine.harness_choices || {};
  const botRuntimes = new Set(machine.bot_runtimes || ((machine.bots || []).length ? machine.needed_runtimes || [] : []));
  const profiles = Array.isArray(machine.profiles) ? machine.profiles : [];
  const rows = names.map(name => {
    const tool = toolFor(name), value = runtimes[name] || {installed: !!tool.installed, authenticated: tool.authenticated};
    const installed = value.installed || tool.installed, auth = value.authenticated;
    const label = tool.name || harnessWords(name);
    const version = installed ? (tool.version || value.version || '') : '';
    // Enabled is the owner's choice per computer (harness_choices), always on for what its bots run on; an older
    // runner that reports no tools cannot say.
    const enabled = !tool.id ? '' : botRuntimes.has(name) ? 'Enabled' : (choices[tool.id] ?? (tool.wanted || tool.installed)) ? 'Enabled' : 'Off';
    let action = '';
    if (manage && installed && auth !== 'ready') {
      const command = machineLoginCommand(machine, name);
      const caps = harnessCaps(name);
      if (caps.browser_sign_in) action = online
        ? ` <button class="ghost machine-signin" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(name)}" data-machine="${esc(machine.label)}">Sign in</button>` : '';
      else if (command) action = ` <span class="machine-login-command" title="Run on the computer${command.includes('<container>') ? '; docker ps shows the container name' : ''}"><code>${esc(command)}</code><button class="ghost machine-signin" type="button" data-machine-copy-login="${esc(command)}">Copy</button></span>`;
      else if (caps.api_keys === 'bot') action = ' <a class="machine-signin-link" href="#/credentials">Add key</a>';
      if (caps.browser_sign_in) action += machineLoginActions(machine, online, name, '', false);
    } else if (manage && installed) action = machineLoginActions(machine, online, name, '', true);
    const rejected = auth === 'rejected'
      ? `<span class="err machine-rejected" data-rejected="${esc(name)}">${value.rejected_at ? esc(ago(value.rejected_at)) + '. ' : ''}${value.rejected_reason ? esc(value.rejected_reason) + '. ' : ''}${hlCredentialFix(value.credential_source)}. It takes no work that needs ${esc(name)} until then.</span>` : '';
    const signIn = installed ? machineSignInHTML(machine, online, name, auth, value) : '<span class="muted">—</span>';
    // The computer's own login's usage windows (only to whoever may see its account); each named subscription below
    // carries its own.
    const own = installed ? machineUsageHTML(name, {account: value.account, weekly: value.weekly, five_hour: value.five_hour}, label) : '<span class="muted">—</span>';
    const lines = installed ? profiles.filter(p => p.runtimes?.[name]).map(p => {
      const state = p.runtimes[name];
      // A signed-out named subscription signs in again from here, like the computer's own login above.
      const signIn = !manage ? '' : state.signed_in === true ? machineLoginActions(machine, online, name, p.name, true)
        : state.signed_in === false && online && harnessCaps(name).browser_sign_in
          ? ` <button class="ghost machine-signin" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(name)}" data-profile="${esc(p.name)}" data-machine="${esc(machine.label)}" aria-label="Sign in ${esc(p.display_name || p.name)} to ${esc(label)}">Sign in</button>` : '';
      return `<div class="machine-tool machine-tool-profile" role="row" data-machine-profile="${esc(p.name)}" data-machine-tool="${esc(name)}">
        <span role="cell" class="machine-tool-name" title="Named subscription ${esc(p.display_name || p.name)}">${esc(p.display_name || p.name)}</span>
        <span role="cell" class="machine-tool-version"></span><span role="cell" class="machine-tool-enabled"></span>
        <span role="cell" class="machine-tool-signin">${machineProfileSignIn(state, name)}${signIn}</span>
        <span role="cell" class="machine-tool-usage">${state.signed_in === false ? '<span class="muted">—</span>' : machineUsageHTML(name, state, `${label} ${p.display_name || p.name}`, 'this subscription')}</span></div>`;
    }).join('') : '';
    return `<div class="machine-tool" role="row" data-machine-tool="${esc(name)}">
      <span role="cell" class="machine-tool-name">${esc(label)}</span>
      <span role="cell" class="machine-tool-version"${version ? ` title="${esc(version)}"` : ''}>${installed ? esc(version || 'Installed') : '<span class="muted">Not installed</span>'}</span>
      <span role="cell" class="machine-tool-enabled"${botRuntimes.has(name) ? ' title="Its bots use it"' : ''}>${enabled ? esc(enabled) : '<span class="muted">—</span>'}</span>
      <span role="cell" class="machine-tool-signin">${signIn}${action}${rejected}</span>
      <span role="cell" class="machine-tool-usage">${own}</span></div>${lines}`;
  }).join('');
  if (!rows) return '';
  return `<div class="machine-tools" role="table" aria-label="AI tools on ${esc(machine.label)}"><div class="machine-tool machine-tool-head" role="row">
    <span role="columnheader">Tool</span><span role="columnheader">Installed</span><span role="columnheader">Enabled</span><span role="columnheader">Sign-in</span><span role="columnheader">Usage</span></div>${rows}</div>`;
}
// Desktop apps the computer's app driver operates for synced bots (readiness `appdriver`, a Mac's native runner):
// one row per app with its state and the driver's own detail, such as which System Settings entry to turn on. A
// computer without a driver reports no `appdriver`, so it shows nothing here.
const MACHINE_APP_STATES = {no_permission: 'Needs Accessibility permission', not_signed_in: 'Not signed in',
  not_installed: 'Not installed', needs_foreground: 'Can\'t work in the background', ui_changed: 'Layout changed',
  no_driver: 'Driver not installed', error: 'Problem'};
const machineAppName = id => String(id).replace(/^./, c => c.toUpperCase());
// `ready` is what the server checks before it delivers through the app; a state it does not know reads Problem.
const machineAppState = app => app?.ready === true ? 'Ready' : MACHINE_APP_STATES[app?.state] || 'Problem';
function machineAppsHtml(machine) {
  const driver = machine.readiness?.appdriver;
  if (!driver || typeof driver !== 'object') return '';
  const apps = Object.entries(driver.apps || {});
  const rows = apps.map(([id, app]) => `<div class="machine-tool" role="row" data-machine-app="${esc(id)}">
      <span role="cell" class="machine-tool-name">${esc(machineAppName(id))}</span>
      <span role="cell" class="machine-tool-version"${app.version ? ` title="${esc(app.version)}"` : ''}>${app.installed ? esc(app.version || 'Installed') : '<span class="muted">Not installed</span>'}</span>
      <span role="cell" class="machine-tool-signin"><span class="pill ${app.ready === true ? 'ok' : 'fail'}" data-app-state="${esc(app.ready === true ? 'ready' : app.state || 'error')}">${esc(machineAppState(app))}</span>${app.detail ? `<span class="muted machine-app-detail">${esc(app.detail)}</span>` : ''}</span></div>`).join('');
  if (!rows) return '';
  return `<div class="machine-tools machine-apps" role="table" aria-label="Desktop apps on ${esc(machine.label)}"><div class="machine-tool machine-tool-head" role="row">
    <span role="columnheader">Desktop apps</span><span role="columnheader">Installed</span><span role="columnheader">State</span></div>${rows}</div>`;
}
// The row's sign that the screen is on: a link to it for whoever may open it.
function machineScreenHTML(machine) {
  if (!machine.screen || machine.screen === 'off' || machine.revoked_at) return '';
  if (!machine.screen_available) return '<span class="settings-cell-note">Screen not available</span>';
  const label = machine.screen === 'control' ? 'Screen · control' : 'Screen · view';
  return screenMayOpen(machine) ? `<a class="machine-screen" href="${screenHref(machine.id)}"><span class="nav-icon" aria-hidden="true">computer</span>${label}</a>${betaPill('computer_screen')}`
    : `<span class="machine-screen"><span class="nav-icon" aria-hidden="true">computer</span>${label}</span>${betaPill('computer_screen')}`;
}
function renderSettingsMachines() {
  const el = $('#set-machines'); if (!el) return;
  const expanded = new Set([...el.querySelectorAll('details[data-machine-details][open]')].map(node => node.dataset.machineDetails));
  const showRevoked = el.dataset.showRevoked === 'true';
  const revoked = SETTINGS_DATA.machines.filter(machine => machine.revoked_at).length;
  const machines = SETTINGS_DATA.machines.filter(machine => showRevoked || !machine.revoked_at);
  const cards = machines.map(machine => {
    const online = !machine.revoked_at && machine.last_seen && Date.now() - new Date(machine.last_seen) < 60000;
    const tools = machineToolsHtml(machine, online);
    // The model CLIs the computer runs: version, pin, and the owner's Update / Pin actions. The
    // computer applies them between turns (backend/harness_actions.py, runner-rs/src/harness_tools.rs).
    // It installs what its bots run on by itself; the owner turns any other one on or off here.
    const choices = machine.harness_choices || {};
    // What its bots run on, fallbacks included, stays on (the server refuses an off for it); an older server sends
    // no bot_runtimes, only its bots' own runtimes.
    const botRuntimes = new Set(machine.bot_runtimes || ((machine.bots || []).length ? machine.needed_runtimes || [] : []));
    const asked = new Set((machine.harness_actions || []).filter(row => ['requested', 'running'].includes(row.state)).map(row => `${row.harness}:${row.action}`));
    const harnessAction = (id, action, label, value) => {
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
        ? ` <label class="machine-harness-toggle"${needed ? ' title="Its bots use it"' : ''}><input type="checkbox" aria-label="Enable ${esc(value.name || id)} on ${esc(machine.label)}" data-harness-choice="${esc(id)}" data-runner="${esc(machine.id)}"${on ? ' checked' : ''}${needed ? ' disabled' : ''}> Enabled</label>` : '';
      return `<span class="machine-harness-item" data-harness-chip="${esc(id)}"><span class="pill ${tone}" title="${esc(title)}">${esc(text)}</span>${toggle}${buttons}</span>`;
    };
    // The owner sees every model CLI, to turn one on; everyone else what is there.
    const harnesses = Object.entries(machine.readiness?.harnesses || {}).filter(([, value]) => S.me?.role === 'owner' || value.installed || value.wanted || value.state !== 'idle').map(harnessChip).join('');
    const failures = Object.values(machine.readiness?.bots || {}).filter(value => !value.ready).length;
    const bots = machine.bots || [];
    const botNames = bots.map(settingsBotName);
    const status = machine.revoked_at ? ['failed', 'revoked'] : online ? ['ok', 'online'] : ['', 'offline'];
    // Rarely used actions sit in the row's ⋯ menu, so each computer stays one compact row.
    const admin = settingsIsAdmin(), mayRemove = !machine.revoked_at && (admin || machine.operator === S.me?.id);
    // Screen (Off / View / Control): offered once the computer itself has a screen turned on (`screen_available`),
    // and kept while a mode is set so it can be turned off again. The operator or a bot admin sets it.
    const screen = machine.screen || 'off';
    const screenChoice = screenMayOpen(machine) && (machine.screen_available || screen !== 'off')
      ? `<div class="people-menu-check machine-screen-choice" data-computer-screen="${esc(machine.id)}"><span>Screen${betaPill('computer_screen')}</span>${repoSegHTML(`screen-${machine.id}`, 'Screen', [['off', 'Off'], ['view', 'View'], ['control', 'Control']], screen, false, ' repo-seg-sm')}</div>` : '';
    // Performance (everyone reads it); its operator or a bot admin turns the diagnostics off and on.
    const performance = machine.revoked_at ? '' : `<button type="button" role="menuitem" data-computer-performance="${esc(machine.id)}">Performance${betaPill('replay_performance')}</button>`
      + (screenMayOpen(machine) ? `<label class="people-menu-check"><input type="checkbox" data-performance-mode="${esc(machine.id)}" ${machine.performance !== 'off' ? 'checked' : ''}> Performance diagnostics</label>` : '');
    const menu = [bots.length ? `<button type="button" role="menuitem" data-computer-bots="${esc(machine.id)}">Show bots</button>` : '',
      performance, screenChoice,
      !machine.revoked_at && admin ? `<label class="people-menu-check"><input type="checkbox" data-member-bots="${esc(machine.id)}" ${machine.accepts_member_bots ? 'checked' : ''}> Accepts members' bots</label>` : '',
      mayRemove ? `<button type="button" role="menuitem" class="danger-text" data-computer-remove="${esc(machine.id)}">Remove computer</button>` : ''].join('');
    // Which runner engine it runs (runner/engine.py); hovering says the build, or why it is on Python.
    const engine = machine.readiness?.engine;
    const engineLabel = engine?.name ? ` · <span title="${esc(engine.note || engine.build || '')}">${engine.name === 'rust' ? 'Rust' : 'Python'}</span>` : '';
    return `<tr class="machine-card"><td><strong>${esc(machine.label)}</strong><span class="settings-cell-note">${esc(machine.version || 'version not reported')}${machine.platform ? ` · ${esc(machine.platform)}` : ''}${engineLabel}</span>${window.runnerUpdateHtml?.(machine.update, machine.version) || ''}</td>
      <td data-label="Operator">${esc(settingsPersonName(machine.operator))}${!machine.revoked_at && machine.accepts_member_bots ? '<span class="settings-cell-note">Accepts members\' bots</span>' : ''}</td>
      <td data-label="Bots">${bots.length ? `<a href="#/settings" class="machine-bots" data-computer-bots="${esc(machine.id)}" title="${esc(botNames.join(', '))}"><strong>${bots.length} bot${bots.length === 1 ? '' : 's'}</strong><span class="settings-cell-note">${esc(botNames.join(', '))}</span></a>` : '<span class="muted">No bots</span>'}${failures ? `<span class="settings-cell-note err">${failures} not ready</span>` : ''}</td>
      <td data-label="AI tools"><div class="machine-runtime">${tools || '<span class="muted">No AI tools reported</span>'}</div>${machineAppsHtml(machine)}${harnesses ? `<details data-machine-details="${esc(machine.id)}:tools"><summary>Installation and updates</summary><p class="settings-cell-note">Enabled tools stay installed. Sign-in status is shown above.</p><div class="machine-harnesses">${harnesses}</div></details>` : ''}</td>
      <td data-label="Status"><span class="machine-status"><span class="dot ${status[0]}" aria-hidden="true"></span>${status[1]}${machine.last_seen ? `<span class="muted"> · ${esc(ago(machine.last_seen))}</span>` : ''}</span>${machineScreenHTML(machine)}</td>
      <td class="settings-row-actions"><div class="people-cell-more">${menu ? `<button class="people-more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${esc(machine.label)}"><span class="nav-icon" aria-hidden="true">more_horiz</span></button><div class="people-menu" role="menu" hidden>${menu}</div>` : ''}</div></td></tr>`;
  }).join('');
  el.onchange = async event => {
    if (event.target.matches('[data-show-revoked-computers]')) {
      el.dataset.showRevoked = String(event.target.checked);
      renderSettingsMachines();
      el.querySelector('[data-show-revoked-computers]')?.focus();
      return;
    }
    const screenBox = event.target.closest('[data-computer-screen]');
    if (screenBox && event.target.type === 'radio') {
      const id = screenBox.dataset.computerScreen, before = SETTINGS_DATA.machines.find(m => m.id === id)?.screen || 'off';
      screenBox.querySelectorAll('input').forEach(input => { input.disabled = true; });
      try {
        const saved = await post(`/v2/computers/${encodeURIComponent(id)}/screen`, {mode: event.target.value});
        screenSaved(id, {screen: event.target.value, ...saved});
        toast(event.target.value === 'off' ? 'Screen off' : `Screen: ${event.target.value}`);
        await loadSettings();
      } catch (error) {
        toast(error.message, true);
        screenBox.querySelectorAll('input').forEach(input => { input.disabled = false; input.checked = input.value === before; });
      }
      return;
    }
    const choice = event.target.closest('[data-harness-choice]');
    if (choice) {
      choice.disabled = true;
      try {
        await post(`/v2/computers/${encodeURIComponent(choice.dataset.runner)}/harness-choices`, {harness: choice.dataset.harnessChoice, enabled: choice.checked});
        toast(choice.checked ? 'Installing' : 'Turned off');
        await loadSettings();
      } catch (error) { toast(error.message, true); choice.checked = !choice.checked; choice.disabled = false; }
      return;
    }
    const perf = event.target.closest('[data-performance-mode]');
    if (perf) {
      perf.disabled = true;
      try {
        await post(`/v2/computers/${encodeURIComponent(perf.dataset.performanceMode)}/performance`, {mode: perf.checked ? 'on' : 'off'});
        toast(perf.checked ? 'Performance diagnostics on' : 'Performance diagnostics off');
        await loadSettings();
      } catch (error) { toast(error.message, true); perf.checked = !perf.checked; perf.disabled = false; }
      return;
    }
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
    const more = event.target.closest('.people-more');
    if (more) {
      const menu = more.nextElementSibling, open = menu.hidden;
      peopleCloseMenus(menu); menu.hidden = !open; more.setAttribute('aria-expanded', String(open));
      // Fixed, so the table's sideways scroll box does not clip it.
      if (open) { const r = more.getBoundingClientRect(); menu.style.position = 'fixed'; menu.style.top = `${Math.min(r.bottom + 4, innerHeight - menu.offsetHeight - 8)}px`; menu.style.left = `${Math.max(8, r.right - menu.offsetWidth)}px`; menu.style.right = 'auto'; }
      return;
    }
    const perfLink = event.target.closest('[data-computer-performance]');
    if (perfLink) { peopleCloseMenus(); location.hash = performanceHref(perfLink.dataset.computerPerformance); return; }
    const shown = event.target.closest('[data-computer-bots]');
    if (shown) {
      event.preventDefault(); peopleCloseMenus();
      SETTINGS_BOTS_VIEW.computer = shown.dataset.computerBots; settingsBotsRemember(); settingsShow('bots'); renderSettingsBots();
      return;
    }
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
      <td><span class="machine-status"><span class="dot ${agent.revoked_at ? 'failed' : agent.online ? 'ok' : ''}" aria-hidden="true"></span>${agent.revoked_at ? 'revoked' : agent.online ? 'reporting in' : 'not reporting'}${agent.last_seen ? `<span class="muted"> · ${esc(ago(agent.last_seen))}</span>` : ''}</span></td></tr>`).join('');
  const list = `${revoked ? `<label class="settings-cell-note"><input type="checkbox" data-show-revoked-computers${showRevoked ? ' checked' : ''}> Show revoked computers (${revoked})</label>` : ''}
    ${cards ? `<div class="scroll"><table class="settings-table settings-machines"><thead><tr><th>Computer</th><th>Operator</th><th>Bots</th><th>AI tools</th><th>Status</th><th aria-label="Actions"></th></tr></thead><tbody>${cards}</tbody></table></div>` : `<div class="empty">${revoked ? 'No active computers.' : 'No computers yet.'}</div>`}
    ${agents ? `<h3 class="settings-agents-title">External agents${betaPill('external_agents')}</h3><div class="scroll"><table class="settings-table"><thead><tr><th>Agent</th><th>Owner</th><th>Model</th><th>Status</th></tr></thead><tbody>${agents}</tbody></table></div>` : ''}`;
  // Only the list is redrawn; the Add computer form (and the code it shows) keeps what was typed.
  if (!el.querySelector('.machine-enroll')) el.innerHTML = `<div data-machines-list></div>
    <div class="machine-enroll"><select class="settings-inline-select" id="machine-operator" aria-label="Computer owner">
      ${people.map(person => `<option value="${esc(person.id)}" ${person.id === S.me?.id ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}</select>
      <select class="settings-inline-select" id="machine-kind" aria-label="Kind of computer"><option value="mac">Mac</option><option value="linux">Linux or cloud server</option><option value="windows">Windows PC (WSL 2${betaFeature('windows') ? ', Beta' : ''})</option></select>
      <input id="machine-label" type="text" autocomplete="off" aria-label="Computer name" placeholder="Computer name" value="${esc(settingsPersonName(people.find(person => person.id === S.me?.id)?.id || people[0]?.id) + "'s Mac")}">
      <button class="primary" type="button" id="register-machine">Add computer</button>
      <div class="machine-enroll-status" id="machine-enroll-status"></div></div>`;
  el.querySelector('[data-machines-list]').innerHTML = `<p class="settings-cell-note">Computers run your bots. The operator manages the computer; each bot can use its own named subscription.</p>${window.runnerOutdatedBanner?.(machines, S.me?.id, settingsPersonName) || ''}${list}`;
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
      status.innerHTML = `<span>${kind === 'windows' ? `Windows PC through WSL 2${betaPill('windows')}. ` : ''}Code for <strong>${esc(label)}</strong>, good 15 minutes. Then sign it in here.</span>${line(main, 0)}`
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
  return `Set up a ${appName()} local runner for ${settingsPersonName(operator)} using the downloaded ${filename} enrollment file. Work from the Tico checkout on the target Mac. Locate the human's bot repositories, run scripts/setup-runner.sh "$HOME/Downloads/${filename}", then run scripts/tico doctor and resolve every repository, runtime login (Codex or Claude), model, or configuration-readiness problem. Never print or commit the enrollment code or runner token and never open an inbound port. Do not install the private processing or connector workers. Registration must not assign bots; when the computer is ready, tell me to return to ${appName()} Settings and choose it for each intended bot so existing sessions are checkpointed before the move.`;
}
function settingsCopyPrompt(slug) {
  void copyText(settingsSetupPrompt(slug)).then(() => toast(`${slug ? settingsBotName(slug) + ' ' : ''}setup prompt copied`)).catch(() => toast('Could not copy the setup prompt', true));
}
