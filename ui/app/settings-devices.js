/* ui/app/settings-devices.js — Settings > Computers: computers, enrollment, API tokens, agent credentials, setup prompts
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// An external agent (a Hermes profile) has no computer: the cell holds its credential instead.
// The token is shown once, when it is minted; after that only rotate and revoke remain.
const agentKind = a => `${settingsHarnessName(a.harness) || a.harness} agent`;
// An imported snapshot's freshness says nothing about its external process.
const agentPresenceLabel = (a, online) => a.synced ? (online ? 'History synced recently' : a.last_seen_at ? 'History not synced lately' : 'History not synced yet')
  : !a.credential ? 'No credential yet' : online ? 'Reporting in' : 'Not reporting';
function settingsAgentCell(e) {
  const a = e.agent, name = agentKind(a);
  const manage = settingsCanManageBot(e);
  if (a.synced) return `<div class="settings-agent-cell"><strong>${esc(name)}</strong>${a.profile ? ` · <span class="muted">${esc(a.profile)}</span>` : ''}
    <span class="settings-cell-note">${esc(a.last_seen_at ? `synced ${ago(a.last_seen_at)}${a.detail ? ` by ${a.detail}` : ''}` : 'not synced yet')}</span>${settingsAppDeliveryNote(e)}</div>`;
  const seen = a.last_seen_at ? `last seen ${ago(a.last_seen_at)}` : a.credential ? 'credential issued · no heartbeat yet' : 'no credential yet';
  return `<div class="settings-agent-cell"><strong>${esc(name)}</strong>${a.profile ? ` · <span class="muted">${esc(a.profile)}</span>` : ''}
    <span class="settings-cell-note">${esc(seen)}</span>
    ${manage ? `<span class="settings-agent-actions">${agentPairButton(e)}<button class="ghost" type="button" data-agent-credential="${esc(e.id)}">${a.credential ? 'Rotate credential' : 'Create credential'}</button>${a.credential ? `<button class="ghost" type="button" data-agent-revoke="${esc(e.id)}">Revoke</button>` : ''}</span>` : ''}</div>`;
}
// Where a synced bot's messages go when it is delivered through its desktop app (settings-bot-editor.js).
function settingsAppDeliveryNote(e) {
  const d = e.app_delivery; if (!d?.computer) return '';
  const where = SETTINGS_DATA.machines.find(machine => machine.id === d.computer)?.label || d.computer_label || 'a computer';
  return `<span class="settings-cell-note" data-app-delivery-note>through the ${esc(d.app_label || machineAppName(d.app))} app on ${esc(where)}</span>`;
}
// Pair: the code a Hermes profile printed, typed here; approving hands the profile its credential (backend/agents.py).
const agentPairButton = e => ['hermes', 'openclaw'].includes(e.agent?.harness) ? `<button class="ghost" type="button" data-agent-pair="${esc(e.id)}">Pair</button>` : '';
function settingsAgentPair(slug) {
  const e = S.emps.find(row => row.id === slug); if (!e) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell agent-credential';
  dialog.innerHTML = `<form method="dialog"><header><h2>Pair ${esc(e.display_name)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="tmodal-body agent-credential-body">
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
  const e = S.emps.find(row => row.id === slug); if (!e) return;
  const rotating = !!e.agent?.credential;
  if (rotating && !confirm(`Rotate the agent credential for ${e.display_name}? The current token stops working at once; install the new one on its computer.`)) return;
  let issued;
  try { issued = await post(`/v2/bots/${encodeURIComponent(slug)}/agent-credential`, {}); }
  catch (error) { toast(error.message, true); return; }
  const setup = issued.setup, yaml = agentConfigYaml(setup);
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell agent-credential';
  dialog.innerHTML = `<header><h2>${rotating ? 'New agent credential' : 'Agent credential'} for ${esc(e.display_name)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="tmodal-body agent-credential-body">
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
    `<tr data-token-row="${esc(row.id)}">${everyone ? `<td>${esc(row.name || row.email || row.human)}</td>` : ''}<td><strong>${esc(row.label)}</strong></td><td>${esc(ago(row.created_at))}</td><td>${row.last_used_at ? esc(ago(row.last_used_at)) : '<span class="muted">never</span>'}</td><td>${row.expires_at ? esc(new Date(row.expires_at).toLocaleDateString()) : '<span class="muted">never</span>'}</td><td>${state(row)}</td><td><button class="ghost" type="button" data-token-revoke="${esc(row.id)}" data-token-label="${esc(row.label)}">Revoke</button></td></tr>`).join('')}</tbody></table></div>`
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
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell agent-credential';
  dialog.innerHTML = `<header><h2>Token ${esc(issued.label)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="tmodal-body agent-credential-body">
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
  const e = S.emps.find(row => row.id === slug); if (!e) return;
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
// The computer's AI tools: its readiness (`harnesses` per tool, `runtimes` per runtime; an older runner sends only
// `runtimes`) and its named subscriptions (`profiles`) make its connections (ui/app/computer-detail.js). A CLI whose
// manifest has `browser_sign_in` signs in from the computer's pop-up: the computer runs the login as the bot user and
// relays only the link and one-time code (backend/model_login.py, runner-rs/src/login.rs). One with only a
// `terminal_login` gets that command to run there; one whose keys belong to each bot (`api_keys = "bot"`) gets a link
// to Credentials. The account (email, plan) comes only from a provider's own status command (#610) and only to an
// administrator or the computer's operator; anything not reported reads unavailable, never a guessed value.
// The server's rule (model_login._operator): the owner, an admin, or the person whose computer it is.
const machineCanSignIn = machine => settingsIsAdmin() || (!!S.me?.id && machine.owner_id === S.me.id);
function machineLoginCommand(machine, name) {
  const command = harnessCaps(name).terminal_login;
  return command && machine.update?.kind === 'docker' ? `docker exec -it -u bot <container> ${command}` : command;
}
// A computer reads an account again every 10 minutes (runner-rs/src/accounts.rs READ_EVERY), the answer landing on a
// later heartbeat's report, which is kept a minute; a reading older than this has missed its refresh.
const MACHINE_ACCOUNT_FRESH_MS = 15 * 60000;
const machinePlanName = plan => plan ? String(plan).replace(/^./, c => c.toUpperCase()) : '';
// "Signed in as <email>" from an account (the plan is left out: the usage says what matters); what the provider did not
// say is left out, not guessed. A reading
// that has missed its refresh says when it was last checked, since the login may have changed since.
function machineAccountHTML(account, runtime, title = '') {
  const who = account?.kind === 'api_key' ? 'with an API key · no plan usage'
    : account?.email || '';
  const rest = who ? (account?.kind === 'api_key' ? who : `as ${who}`)
    : harnessCaps(runtime).account ? '· account not reported yet' : '· account unavailable';
  const checked = Date.parse(account?.checked_at);
  const old = who && Number.isFinite(checked) && Date.now() - checked > MACHINE_ACCOUNT_FRESH_MS
    ? ` <span class="muted" data-account-checked>account last checked ${esc(ago(account.checked_at))}</span>` : '';
  const tip = [account?.org, machinePlanName(account?.plan)].filter(Boolean).join(' · ');
  return `<span class="pill ok"${title ? ` title="${esc(title)}"` : ''}>Signed in</span> <span class="machine-account${who ? '' : ' muted'}"${tip ? ` title="${esc(tip)}"` : ''}>${esc(rest)}</span>${old}`;
}
// One usage window ("weekly" or "5-hour window"): what is left and when it resets; its age only once it is a day old. A
// window with no provider percentage says so; it never reads as 0% or a full allowance.
function machineWindowHTML(label, reading, name) {
  if (!reading) return '';
  const percent = Number.isFinite(reading.used_percent) ? Math.min(100, Math.max(0, reading.used_percent)) : null;
  const reported = Date.parse(reading.reported_at), reset = Date.parse(reading.resets_at);
  const expired = Number.isFinite(reset) && reset <= Date.now();
  const stale = Number.isFinite(reported) && Date.now() - reported > 86400000;
  const left = percent === null ? null : Math.round(100 - percent);
  const value = expired ? 'previous window; refresh needed' : left === null ? 'percentage unavailable' : `${left}% left`;
  const notes = [Number.isFinite(reset) && !expired ? `resets ${new Date(reset).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'})}` : '',
    stale ? `from ${ago(reading.reported_at)}` : ''].filter(Boolean);
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
  const idle = caps.weekly_usage === 'turns' && !state?.weekly && !state?.five_hour;
  const weekly = machineWindowHTML('Weekly', state?.weekly, name)
    || `<span class="muted" data-usage-window="Weekly"${idle ? ` title="${esc(`Shows after a ${harnessWords(runtime)} turn on ${where}`)}"` : ''}>${idle ? 'No usage yet' : 'Usage unavailable'}</span>`;
  return weekly + machineWindowHTML('5-hour window', state?.five_hour, name);
}
// The sign-in cell: signed in as <account>, signed out, login expired or rejected, checking, or couldn't check (with
// when the computer last reported).
function machineSignInHTML(machine, online, name, auth, value) {
  const checked = machine.last_seen_at ? ` · last checked ${ago(machine.last_seen_at)}` : '';
  if (auth !== 'ready' && machineKeyCleared(machine, name)) return '<span class="pill fail" title="Its key was cleared from Tico: the company\'s shared key does not sign it back in by itself">No key</span>';
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
// Of those, the computer's own login whose key a person cleared (Clear key, not Sign out): it reads "No key · Add key".
const machineKeyCleared = (machine, runtime) => machineSignedOut(machine, runtime) && (machine.key_cleared || []).includes(runtime);
// Sign out / Switch account (or Use the shared key, or Switch to subscription for the computer's login on an API key)
// for one login, from the shared sign-in module (ui/model-login.js).
const machineLoginActions = (machine, online, runtime, profile, signedIn) => window.TicoModelLogin?.actionsHTML({
  runnerId: machine.id, runtime, machine: machine.label, profile, signedIn, signedOut: machineSignedOut(machine, runtime, profile), online,
  platform: machine.platform || '', onKey: !profile && machine.readiness?.runtimes?.[runtime]?.account?.kind === 'api_key'}) || '';
// The key a login runs on (its readiness `key`: {name, source}), by its stored preview only: this computer's own key or
// the company's shared one (backend/computer_keys.py), or one set on the computer itself.
function machineKeyHTML(machine, runtime, key) {
  if (!key) return '';
  const stored = key.source === 'computer' ? machine.keys?.[runtime] : key.source === 'shared' ? machine.shared_keys?.[runtime] : null;
  const whose = {computer: 'this computer\'s key', shared: 'the company\'s shared key', terminal: 'a key set on the computer'}[key.source] || 'a key';
  return ` <span class="muted machine-key" data-machine-key="${esc(key.source)}">· ${esc(whose)}${stored?.preview ? ` <code>${esc(stored.preview)}</code>` : ''}</span>`;
}
// Replace key / Clear key, or Add key, from the shared sign-in module (ui/model-login.js).
const machineKeyActions = (machine, online, runtime, key, signedIn) => (window.TicoModelLogin?.keyActionsHTML({
  runnerId: machine.id, runtime, machine: machine.label, key, signedIn, online}) || '')
  + (signedIn && key ? window.TicoModelLogin?.toSubscriptionHTML({runnerId: machine.id, runtime, machine: machine.label, online,
    platform: machine.platform || ''}) || '' : '');
// A sign-in several of the computer's bots run on (`shared_logins`, backend/views.py shared_logins), for a harness whose
// login serves one run at a time (`one_turn_per_login`): those bots take turns, so it says so beside that login.
function machineSharedLoginHTML(machine, runtime, profile = '') {
  const row = (machine?.shared_logins || []).find(r => r.runtime === runtime && (r.profile || '') === profile);
  if (!row) return '';
  const names = (row.bots || []).map(settingsBotName), more = row.count - names.length;
  const who = names.length ? ` (${names.join(', ')}${more > 0 ? ` and ${more} more` : ''})` : '';
  return `<span class="muted machine-shared-login" data-shared-login="${esc(runtime)}:${esc(profile)}">${row.count} bots share this sign-in${esc(who)}. They take turns: one ${esc(harnessWords(runtime))} run at a time.</span>`;
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
// "Update" on the row only when one waits; the details are under Versions and updates in the pop-up.
function machineUpdateWords(update) {
  if (!update) return '';
  if (update.state === 'updating') return `<span class="pill waiting" title="${esc(update.label || 'Updating')}">Updating</span>`;
  if (update.state === 'needs_update' || update.state === 'incompatible' || update.outdated)
    return `<span class="pill ${update.state === 'incompatible' ? 'fail' : 'waiting'}" data-runner-outdated title="${esc([update.label, update.target ? `to ${update.target}` : ''].filter(Boolean).join(' '))}">Update</span>`;
  return '';
}
// Turns at once on a computer: a whole number from 1 to 256, or empty for automatic. Anything else is named under the
// row and not sent. The note is set from the answer, since a pop-up whose field is still focused is not redrawn.
async function settingsTurnsAtOnceSave(input) {
  // The field is disabled while its save is in flight, so a change that lands then starts no second save
  // here. The server has not seen that value: it is sent once this save answers, from what the field holds
  // then (below).
  if (input.disabled) return;
  settingsFieldClear(input);
  const text = input.value, n = Number(text);
  const whole = input.checkValidity() && (text === '' || (Number.isInteger(n) && n >= 1 && n <= 256));
  if (!whole) {
    settingsFieldError(input, 'Enter a whole number from 1 to 256, or leave it empty for automatic.');
    return;
  }
  if (text === (input.dataset.current || '')) return;
  const turns_at_once = text === '' ? null : n;
  input.disabled = true;
  let saved = false;
  try {
    const path = `/v2/computers/${encodeURIComponent(input.dataset.turnsAtOnce)}/turns-at-once`;
    const done = await post(path, {turns_at_once});
    saved = true;
    input.dataset.current = text;
    input.defaultValue = text;
    input.disabled = false;
    const machine = (SETTINGS_DATA.machines || []).find(row => row.id === input.dataset.turnsAtOnce);
    const note = input.closest('label')?.querySelector('[data-turns-note]');
    if (note && machine) {
      const turn_limit = done?.turn_limit ?? machine.turn_limit;
      note.textContent = machineTurnsNote({...machine, turns_at_once, turn_limit});
    }
    toast(turns_at_once == null ? 'Turns at once is automatic' : `Turns at once set to ${turns_at_once}`);
  } catch (error) {
    toast(error.message, true);
    // A value typed during the failed save is kept and sent below; only an unchanged field goes back to what is saved.
    if (input.value === text) input.value = input.dataset.current;
    input.disabled = false;
  }
  // The value the field holds now differs from what the server holds: send it, and its own save reloads the page.
  if (input.value !== (input.dataset.current || '')) return settingsTurnsAtOnceSave(input);
  if (saved) await loadSettings();
}
// What a computer is doing now: "1 running · 55 queued", idle, asleep (a cloud computer stopped to save), or offline.
function machineNowWords(machine) {
  if (machine.revoked_at) return 'revoked';
  const counts = machine.turn_counts || {};
  const parts = [['running', 'running'], ['starting', 'starting'], ['queued', 'queued'], ['waiting', 'waiting']]
    .filter(([key]) => Number(counts[key]) > 0).map(([key, word]) => `${counts[key]} ${word}`);
  if (parts.length) return parts.join(' · ');
  if (['stopped', 'stopping'].includes(machine.cloud_machine?.state)) return 'asleep';
  return machineOnline(machine) ? 'idle' : 'offline';
}
// The AI tools that get a column: those a subscription can be given to first (a picker each), then every other tool a
// computer reports (its login's chip, as in the computer's pop-up).
function machineToolColumns(machines) {
  const pickers = subsComputersReady() ? subsTools() : [];
  const seen = new Map();
  for (const machine of machines) for (const conn of machineShown(machine)) if (!pickers.includes(conn.runtime)) seen.set(conn.runtime, conn.tool);
  const others = [...seen].sort((a, b) => a[1].localeCompare(b[1])).map(([runtime]) => runtime);
  return {pickers, others};
}
function machineToolCellHTML(machine, runtime) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  const chips = machineShown(machine).filter(conn => conn.runtime === runtime).map(conn => machineChipHTML(machine, conn)).join('');
  return chips ? `<div class="machine-chips">${chips}</div>` : '<span class="muted">—</span>';
}
// One dense row per computer: status and name, its bots, a column per AI tool, and what it is doing now. A click on
// the row (not on a picker) opens the computer's pop-up.
function renderSettingsMachines() {
  const el = $('#set-machines'); if (!el) return;
  const showRevoked = el.dataset.showRevoked === 'true';
  const revoked = SETTINGS_DATA.machines.filter(machine => machine.revoked_at).length;
  const machines = SETTINGS_DATA.machines.filter(machine => showRevoked || !machine.revoked_at);
  const {pickers, others} = machineToolColumns(machines);
  const cards = machines.map(machine => {
    const online = machineOnline(machine);
    const dot = machine.revoked_at ? 'failed' : online ? 'ok' : '';
    const presence = machinePresenceWords(machine);
    const failures = Object.values(machine.readiness?.bots || {}).filter(value => !value.ready).length;
    const waiting = machineWaiting(machine) ? `<span class="pill waiting" data-machine-waiting title="Waiting for ${esc(machine.label)}">Waiting</span>` : '';
    const now = machineNowWords(machine);
    return `<tr class="machine-card" data-computer-row="${esc(machine.id)}">
      <td class="machine-cell"><span class="machine-head"><span class="machine-status" data-machine-status title="${esc(presence)}"><span class="dot ${dot}" aria-hidden="true"></span><span class="sr-only">${esc(presence)}</span></span><button type="button" class="machine-name" data-computer-open="${esc(machine.id)}" aria-haspopup="dialog">${esc(machine.label)}</button>${machineUpdateWords(machine.update)}${waiting}${failures ? `<span class="pill fail" title="${failures} bot${failures === 1 ? '' : 's'} not ready">${failures} not ready</span>` : ''}</span></td>
      <td class="num tnum machine-bot-count">${(machine.bots || []).length}</td>
      ${pickers.map(runtime => `<td data-label="${esc(subsToolName(runtime))}" data-subs-cell>${subsPickerHTML(machine, runtime)}</td>`).join('')}
      ${others.map(runtime => `<td data-label="${esc(machineToolName(machine, runtime))}">${machineToolCellHTML(machine, runtime)}</td>`).join('')}
      <td class="machine-now${['idle', 'asleep', 'offline', 'revoked'].includes(now) ? ' muted' : ''}" data-machine-now>${esc(now)}${online && machine.restarting ? ' <span class="muted" data-machine-restarting>· restarting</span>' : ''}</td></tr>`;
  }).join('');
  const toolHead = runtime => `<th>${esc(machines.map(m => machineToolName(m, runtime)).find(Boolean) || subsToolName(runtime))}</th>`;
  el.onchange = async event => {
    const pick = event.target.closest('[data-subs-pick]');
    if (pick) { await subsPick(pick); return; }
    if (event.target.matches('[data-show-revoked-computers]')) {
      el.dataset.showRevoked = String(event.target.checked);
      renderSettingsMachines();
      el.querySelector('[data-show-revoked-computers]')?.focus();
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
    const exit = event.target.closest('[data-browser-exit], [data-browser-exit-bots] input, [data-browser-exit-cap]');
    if (exit) { await machineBrowserExitSave(exit); return; }
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
    const inbox = event.target.closest('[data-inbox-only]');
    if (inbox) {
      inbox.disabled = true;
      try {
        await post(`/v2/runners/${encodeURIComponent(inbox.dataset.inboxOnly)}/inbox-only`, {enabled: inbox.checked});
        toast(inbox.checked ? 'Kept for inbox bots' : 'No longer kept for inbox bots');
        await loadSettings();
      } catch (error) { toast(error.message, true); inbox.checked = !inbox.checked; inbox.disabled = false; }
      return;
    }
    const turns = event.target.closest('[data-turns-at-once]');
    if (turns) { await settingsTurnsAtOnceSave(turns); return; }
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
    // The pop-up: a click on a row (its name, a chip, anywhere on it) opens it; ✕ or the backdrop closes it.
    const dialog = el.querySelector('dialog.computer-detail');
    if (dialog && (event.target === dialog || event.target.closest('[data-detail-close]'))) { dialog.close(); return; }
    if (dialog && event.target.closest('dialog.computer-detail a[href]:not([data-computer-bots])')) dialog.close();   // leaving for a link
    // Add, on the row or in the pop-up, opens its own pop-up (before the row's click opens the computer's).
    const add = event.target.closest('[data-add-connection]');
    if (add) { if (!add.disabled) computerAddOpen(add.dataset.addConnection); return; }
    if (event.target.closest('[data-subs-cell] select, [data-subs-reconnect]')) {
      const reconnect = event.target.closest('[data-subs-reconnect]');
      if (reconnect) subsDialog('reconnect', subsById(reconnect.dataset.subsReconnect));
      return;
    }
    if (event.target.closest('#machine-add-dialog')) return;
    const row = event.target.closest('[data-computer-open], [data-computer-row]');
    if (row && !event.target.closest('a')) { computerDetailOpen(row.dataset.computerOpen || row.dataset.computerRow); return; }
    const perfLink = event.target.closest('[data-computer-performance]');
    if (perfLink) { dialog?.close(); location.hash = performanceHref(perfLink.dataset.computerPerformance); return; }
    const shown = event.target.closest('[data-computer-bots]');
    if (shown) {
      event.preventDefault(); dialog?.close();
      settingsShow('bots'); renderSettingsBots();
      return;
    }
    const login = event.target.closest('[data-machine-copy-login]');
    if (login) { void copyText(login.dataset.machineCopyLogin).then(() => toast('Command copied')); return; }
    const remove = event.target.closest('[data-computer-remove]');
    if (remove) {
      const machine = SETTINGS_DATA.machines.find(m => m.id === remove.dataset.computerRemove);
      const bots = (machine?.bots || []).length;
      if (!await settingsConfirm({title: `Revoke ${machine?.label || 'this computer'}?`, text: `It stops taking work at once${bots ? `; its ${bots === 1 ? 'bot waits' : `${bots} bots wait`} until reassigned on the Bots page` : ''}.`, ok: 'Revoke'})) return;
      remove.disabled = true;
      try {
        await post(`/v2/computers/${encodeURIComponent(remove.dataset.computerRemove)}/revoke`, {});
        dialog?.close();
        await loadSettings();
        $('#machine-note').innerHTML = 'Computer revoked. Its bots wait until reassigned in Bots. For Docker, stop its runner from its install directory: <code>docker compose -f runner.compose.yaml down</code>. Include <code>-f runner.override.yaml</code> before <code>down</code> if that file is present. This keeps repositories and sign-in.';
        toast('Computer revoked');
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
  const agents = (SETTINGS_DATA.agents || []).map(agent => `<tr class="machine-card" data-agent-row="${esc(agent.bot)}"><td><strong><a href="#/bot/${esc(encodeURIComponent(agent.bot))}">${esc(agent.display_name || botSlug(agent.bot))}</a></strong><span class="settings-cell-note">${esc(agentKind(agent))}${agent.profile ? ` · profile ${esc(agent.profile)}` : ''}${agent.version ? ` · ${esc(agent.version)}` : ''}${agent.platform ? ` · ${esc(agent.platform)}` : ''}</span></td>
      <td>${esc(settingsPersonName(botRow(agent.bot)?.owner_id))}</td>
      <td>${agent.model ? `${esc(agent.model)}${agent.provider ? `<span class="settings-cell-note">${esc(agent.provider)}</span>` : ''}` : '<span class="muted">—</span>'}</td>
      <td><span class="machine-status"><span class="dot ${agent.revoked_at ? 'failed' : agent.online ? 'ok' : ''}" aria-hidden="true"></span>${agent.revoked_at ? 'revoked' : agent.online ? 'reporting in' : 'not reporting'}${agent.last_seen_at ? `<span class="muted"> · ${esc(ago(agent.last_seen_at))}</span>` : ''}</span></td></tr>`).join('');
  const empty = !SETTINGS_PRESENCE.at && SETTINGS_PRESENCE.failed ? 'Computer presence unavailable · checking…' : revoked ? 'No active computers.' : 'No computers yet.';
  const list = `${cards ? `<div class="scroll"><table class="settings-table settings-machines"><thead><tr><th>Computer</th><th class="num">Bots</th>${pickers.map(toolHead).join('')}${others.map(toolHead).join('')}<th>Now</th></tr></thead><tbody>${cards}</tbody></table></div>` : `<div class="empty">${empty}</div>`}
    ${revoked ? `<label class="settings-cell-note machine-show-revoked"><input type="checkbox" data-show-revoked-computers${showRevoked ? ' checked' : ''}> Show revoked (${revoked})</label>` : ''}
    ${agents ? `<h3 class="settings-agents-title">External agents${betaPill('external_agents')}</h3><div class="scroll"><table class="settings-table"><thead><tr><th>Agent</th><th>Owner</th><th>Model</th><th>Status</th></tr></thead><tbody>${agents}</tbody></table></div>` : ''}`;
  // Only the list is redrawn; the Add computer dialog (and the code it shows) keeps what was typed.
  if (!el.querySelector('#machine-add-dialog')) el.innerHTML = `<div data-machines-list></div><p class="settings-cell-note" id="machine-note" role="status"></p>
    <dialog class="tmodal tmodal-shell machine-add" id="machine-add-dialog" aria-label="Add computer"><header><h2>Add computer</h2><button class="ghost" type="button" data-machine-add-close>Close</button></header>
    <div class="tmodal-body machine-enroll subs-form">
      <label class="subs-field" data-machine-operator-field><span class="k">Owner</span><select class="settings-inline-select" id="machine-operator">
        ${people.map(person => `<option value="${esc(person.id)}" ${person.id === S.me?.id ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}</select></label>
      <label class="subs-field"><span class="k">Kind</span><select class="settings-inline-select" id="machine-kind">${S.config?.hosted ? '<option value="hosted">Hosted computer</option>' : ''}<option value="mac" selected>Mac</option><option value="linux">Linux or cloud server</option><option value="windows">Windows PC (WSL 2${betaFeature('windows') ? ', Beta' : ''})</option></select></label>
      <label class="subs-field" data-machine-label-field><span class="k">Name</span><input id="machine-label" type="text" autocomplete="off" placeholder="Chris's Mac" value="${esc(settingsPersonName(people.find(person => person.id === S.me?.id)?.id || people[0]?.id) + "'s Mac")}"></label>
      <div class="onb-actions"><span class="spacer"></span><button class="primary" type="button" id="register-machine">Get code</button></div>
      <div class="machine-enroll-status" id="machine-enroll-status"></div></div></dialog>`;
  const addDialog = $('#machine-add-dialog');
  addDialog.querySelector('[data-machine-add-close]').onclick = () => addDialog.close();
  const addOpen = $('#machine-add-open');
  if (addOpen) addOpen.onclick = () => { if (!addDialog.open) addDialog.showModal(); $('#machine-kind').focus(); };
  const listRoot = el.querySelector('[data-machines-list]'), restoreFocus = computerFocusRestore(listRoot);
  patchHTML(listRoot, `${window.runnerOutdatedBanner?.(machines, S.me?.id, settingsPersonName, machineOnline) || ''}${list}`);
  restoreFocus();
  computerDetailPaint();
  const machineDefaultLabel = () => $('#machine-kind').value === 'linux' && S.config?.local ? 'This computer'
    : `${settingsPersonName($('#machine-operator').value)}'s ${({linux: 'server', windows: 'PC'})[$('#machine-kind').value] || 'Mac'}`;
  $('#machine-operator').onchange = () => { $('#machine-label').value = machineDefaultLabel(); };
  // A hosted computer is named and sized on app.tico.dev, so the owner and name fields step aside.
  const machineKindPaint = () => { const hosted = $('#machine-kind').value === 'hosted';
    el.querySelector('[data-machine-operator-field]').hidden = hosted; el.querySelector('[data-machine-label-field]').hidden = hosted;
    $('#register-machine').textContent = hosted ? 'Open hosting' : 'Get code'; };
  $('#machine-kind').onchange = () => { $('#machine-label').value = machineDefaultLabel(); machineKindPaint(); };
  machineKindPaint();
  $('#register-machine').onclick = async event => {
    if ($('#machine-kind').value === 'hosted') {
      window.open('https://app.tico.dev/hosting?workspace=' + encodeURIComponent(location.hostname), '_blank', 'noopener');
      return;
    }
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
  const rows = (slug ? S.emps.filter(e => e.id === slug) : S.emps).map(e => {
    const owners = (e.users || []).map(person => person.name || person.id).join(', ') || 'nobody';
    const machine = e.machine?.label || `not assigned (reserved for ${settingsPersonName(e.owner_id)})`;
    return `- ${e.display_name} [${e.slug}]: humans=${owners}; model=${e.model || 'not set'}; effort=${e.reasoning_effort || e.effort || 'not set'}; computer=${machine}`;
  });
  return `Help me update the ${appName()} bot setup.\n\nCurrent cloud state:\n${rows.join('\n')}\n\nAsk me what I want to change, then use the formal owner-admin API. Do not edit the production SQLite database directly. Bot definitions in the ${appName()} backend are authoritative after the one-time bootstrap: create a bot with POST /api/v2/bots and update its name, description, hierarchy, status, repository, or room type with POST /api/v2/bots/{bot}/definition. No ${appName()} application deploy or registry publish is required. Provision the bot's repository on any computer that will run it, and verify computer readiness. Preserve tasks and conversation history. Change a bot's model, reasoning effort, or registered computer through POST /api/v2/bots/{bot}/transitions so ${appName()} checkpoints every current session before applying it. If the transition is blocked because the old computer is unavailable, explain what continuity will be lost and ask me before calling apply-without-checkpoint. Use revision and assignment-generation checks. Humans are still managed separately from bot definitions.`;
}
function settingsEnrollmentPrompt(operator, filename) {
  return `Set up a ${appName()} local runner for ${settingsPersonName(operator)} using the downloaded ${filename} enrollment file. Work from the Tico checkout on the target Mac. Locate the human's bot repositories, run scripts/setup-runner.sh "$HOME/Downloads/${filename}", then run scripts/tico doctor and resolve every repository, runtime login (Codex or Claude), model, or configuration-readiness problem. Never print or commit the enrollment code or runner token and never open an inbound port. Do not install the private processing or connector workers. Registration must not assign bots; when the computer is ready, tell me to return to ${appName()} Settings and choose it for each intended bot so existing sessions are checkpointed before the move.`;
}
function settingsCopyPrompt(slug) {
  void copyText(settingsSetupPrompt(slug)).then(() => toast(`${slug ? settingsBotName(slug) + ' ' : ''}setup prompt copied`)).catch(() => toast('Could not copy the setup prompt', true));
}
