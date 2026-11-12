/* The Connection card of Tools › Settings › Email (ui/app/inboxes-email.ts), from GET /v2/google. An admin picks one of two
   ways: Whole company (a service-account key connects every mailbox of the domain, docs/mail.md) or Each person signs
   in (Google sign-in for the server once, then each person connects their own mailbox). The way already set up is
   picked; with nothing set up, neither is. Each way's setup is a prompt for the person's own coding agent by default,
   or the steps by hand. Everyone else sees only Connect Google for their own mailbox (read only with Tico's shared app;
   the company's own app can also label, archive and draft), and nothing when the company's key covers it. Who can use
   each mailbox and What Tico may do are Tools'. Google's sign-in opens in this tab and comes back to the
   server; the computer that syncs mail keeps the grant. No token, key or client secret stays in the browser once it
   is sent, and nothing here messages anyone. */
'use strict';

// A key row of GET /v2/google (workspace or pending), as backend/google_workspace.py's _view sends it.
interface GoogleKeyRow {
  status: string;
  domain?: string | null;
  project_id?: string | null;
  client_id?: string | null;
  scopes?: string[];
  delegation_url?: string | null;
  connected_at?: string | null;
  checked_at?: string | null;
  last_error?: string | null;
  generation?: number;
  narrowable?: boolean;
}
interface GoogleConnectOAuth { configured?: boolean; source?: string | null; redirect_uri?: string; client_id?: string | null }
// GET /v2/google. Every field is optional, so a caller's `{}` stands for a view not read yet.
interface GoogleConnectView {
  can_admin?: boolean;
  workspace?: GoogleKeyRow | null;
  pending?: GoogleKeyRow | null;
  oauth?: GoogleConnectOAuth;
  me?: {mailbox?: string; covered_by_workspace?: boolean} | null;
  permissions?: Record<string, boolean>;
  setup?: {base?: string; reachable?: boolean; newest?: number};
}
// The setup command this page made: POST /v2/google/workspace/setup-code plus the domain it names.
interface GoogleConnectCommand { command: string; expires_at: string; domain: string; generation: number }
interface GoogleConnectActs { mail?: boolean; calendar?: boolean }
// GET /v2/mail/google (backend/google_connect.py's view), or the error its read failed with.
interface GoogleConnectPersonal {
  error?: string;
  configured?: boolean;
  app?: string;
  mailbox?: string;
  syncing?: boolean;
  status?: string;
  acts?: GoogleConnectActs;
  message?: string | null;
  hint?: string | null;
  stalled?: boolean;
}
interface GoogleConnectState { g: GoogleConnectView; personal: GoogleConnectPersonal | null }
interface GoogleConnectRows { active: GoogleKeyRow | null; pend: GoogleKeyRow | null; replacing: boolean; flash: string }
interface GoogleConnectWatch { until: number; mailbox: string; timer: number | null; ended: boolean; generation: number; paused?: boolean }
interface GoogleConnectCheck { ok?: boolean; mailbox?: string | null; error_kind?: string | null; message?: string | null }
// The element the card paints into, with what it keeps between paints.
interface GoogleConnectHost extends Element {
  _googleReplacing?: {since: number} | null;
  _googleWatch?: GoogleConnectWatch | null;
  _googleHow?: Record<string, string | undefined>;
  _googleState?: GoogleConnectState;
  _googleCheck?: {html: string} | null;
  _googleKeep?: Record<string, string>;
  _googleFlash?: string;
  _googlePick?: string | undefined;
  _googleBound?: boolean;
  _googleTurn?: number;
  _googlePoll?: number;
  _googleSnap?: string;
}
// A clicked button or link; `disabled` only does anything on a button.
interface GoogleConnectControl extends HTMLElement { disabled?: boolean }
interface Window { mountGoogleConnect: (host: GoogleConnectHost | null, options?: {force?: boolean}) => Promise<void> }

(function () {
  'use strict';
  const POLL_MS = 5000, CHECK_EVERY_MS = 20000, CHECK_FOR_MS = 600000, KEY_MAX_BYTES = 65536;
  const PROJECT_ID = /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/;
  const CLOUD_SHELL = 'https://shell.cloud.google.com/?show=terminal';
  const DELEGATION = /^https:\/\/admin\.google\.com\//;
  const CAUSES = [
    'Authorize has not been clicked yet.',
    'The client ID or the scopes differ from the ones shown.',
    'Google has not applied it yet. This usually takes minutes, up to 24 hours.',
    'The organization needs a second super admin to approve it.',
  ];
  // The newest setup command this page made. Only New command and Make prompt make one, never a render or a poll.
  let command: GoogleConnectCommand | null = null;

  const dateOf = (iso: string | null | undefined) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d : null; };
  const clock = (iso: string) => dateOf(iso)?.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) || '';
  const live = () => !!command && Date.parse(command.expires_at) > Date.now();
  const domainOf = (address: unknown) => String(address || '').split('@')[1]?.trim().toLowerCase() || '';
  const defaultDomain = (g: GoogleConnectView) => domainOf(g.me?.mailbox) || domainOf((typeof S === 'object' && S?.me?.email) || '');
  const watchable = (host: GoogleConnectHost) => host.isConnected && !host.closest('[hidden]');
  // This tab's address on the server's public URL, for a teammate or an agent to open.
  const googleLink = () => `${String((typeof publicUrl === 'function' && publicUrl()) || location.origin).replace(/\/+$/, '')}/#/tools/settings/email`;
  const unchecked = (row: GoogleKeyRow | null | undefined) => (row ? {...row, checked_at: null} : null);
  // The active row (connected or failing) and the one pending row, which is a replacement while a key is active.
  const rowsOf = (g: GoogleConnectView) => {
    const ws = g.workspace || null;
    return {active: ws && ws.status !== 'pending' ? ws : null, pend: g.pending || (ws && ws.status === 'pending' ? ws : null)};
  };
  // Every key view carries the server's generation, which counts up with each key. A view without one counts as -1.
  const generationOf = (row: GoogleKeyRow | null | undefined) => (typeof row?.generation === 'number' && Number.isInteger(row.generation) ? row.generation : -1);
  const newestGeneration = (g: GoogleConnectView) => Math.max(generationOf(g.workspace), generationOf(g.pending));
  // A replacement ends when a key (pending or active) arrives with a generation newer than the newest one seen at the
  // Replace click. Until then it keeps polling. It never sticks.
  const replacingNow = (host: GoogleConnectHost, g: GoogleConnectView) => {
    const r = host._googleReplacing;
    const match = !!r && newestGeneration(g) <= r.since;
    if (!match) host._googleReplacing = null;
    return match;
  };
  const checkLabel = (host: GoogleConnectHost) => (host._googleWatch?.ended ? 'Check again' : 'Check');
  const causesHtml = (watching: boolean) => `<p>Google hasn't accepted the key yet. Check these:</p>
    <ul class="google-causes">${CAUSES.map(cause => `<li>${esc(cause)}</li>`).join('')}</ul>
    <p class="muted" role="status">${watching ? 'Checking again every 20 seconds for 10 minutes.' : 'Still not accepted. Finish the steps above, then check again.'}</p>`;

  // The in-page confirm for Disconnect. An uploaded key that never connected is removed, and nothing is read yet.
  const disconnectConfirm = (domain: string | null | undefined, reading: boolean) => `<div class="google-confirm" data-ws-disconnect-confirm hidden>
        <p>${reading ? `Disconnect Google for ${esc(domain || 'this domain')}? Tico stops reading every covered mailbox.`
          : `Remove the uploaded key for ${esc(domain || 'this domain')}? Nothing is read until Google is connected.`}</p>
        <div class="row"><button type="button" class="primary danger" data-ws-disconnect-yes>Disconnect</button>
          <button type="button" class="ghost" data-ws-disconnect-no>Cancel</button></div>
      </div>`;

  // A failing key, a pending replacement or a key still to narrow shows step 3 (Check) under this, so the line's own
  // Check connection (and its result) is only for a working key with nothing else to check.
  function connectedBlock(row: GoogleKeyRow, stepped: boolean) {
    const failing = row.status === 'failing';
    const own = !failing && !stepped;
    const checked = row.checked_at || row.connected_at;
    return `<p class="google-connected" data-ws-connected>${failing ? '<strong>Failing</strong>' : '<span class="pill ok">Connected</span>'}${
        checked ? ` <span class="muted">· checked ${esc(ago(checked))}</span>` : ''}</p>
      ${failing ? `<p role="alert">${esc(row.last_error || 'Tico could not reach Google with this key.')}</p>` : ''}
      <div class="row">${own ? '<button type="button" class="ghost" data-ws-recheck>Check connection</button>' : ''}
        <button type="button" class="ghost" data-ws-replace>Replace key</button>
        <button type="button" class="ghost danger" data-ws-disconnect>Disconnect</button></div>
      ${own ? '<div data-ws-check-result role="status"></div>' : ''}
      ${disconnectConfirm(row.domain, true)}`;
  }

  // The two ways to do a path's setup: a prompt for the person's own coding agent (the default), or the steps by hand.
  const howOf = (host: GoogleConnectHost, path: string) => host._googleHow?.[path] || 'agent';
  const howToggle = (path: string, how: string) => `<div class="google-how" role="group" aria-label="How to set it up">
      <button type="button" data-google-how="agent" data-google-path="${path}" aria-pressed="${how === 'agent'}">Have my agent do it</button>
      <button type="button" data-google-how="self" data-google-path="${path}" aria-pressed="${how === 'self'}">Set it up myself</button></div>`;
  const promptBlock = (name: string, text: string, more = '') => `<div class="google-prompt" data-google-prompt="${name}">
      <textarea readonly rows="9" spellcheck="false" aria-label="Prompt for your agent">${esc(text)}</textarea>
      <div class="row"><button type="button" class="ghost" data-copy-prompt>Copy</button>${more}</div></div>`;

  // The Workspace prompt holds the setup command, so it lives exactly as long as the command does (only in this page).
  // What Tico asks Google for follows What Tico may do (its card in Tools › Settings › Email, the company's one setting).
  const MAY: [string, string][] = [['organize_mail', 'Organize mail'], ['send_mail', 'Send mail'], ['change_calendar', 'Create and change calendar events']];
  const allows = (g: GoogleConnectView | undefined, name: string) => g?.permissions?.[name] !== false;
  const asksFor = (g: GoogleConnectView | undefined) => ['read mail and calendar', ...MAY.filter(([name]) => allows(g, name)).map(([, label]) => label.toLowerCase())]
    .join(', ');

  function workspacePrompt(made: GoogleConnectCommand, g: GoogleConnectView | undefined) {
    const domain = made.domain || 'your domain';
    const minutes = Math.max(1, Math.round((Date.parse(made.expires_at) - Date.now()) / 60000));
    return [
      `Connect Tico to the Google Workspace for ${domain}.`,
      `Tico will ask Google for: ${asksFor(g)}.`,
      '',
      `1. Use gcloud signed in as a Google Workspace super admin of ${domain}: in Cloud Shell (${CLOUD_SHELL}), or locally after gcloud auth login.`,
      `2. Run this command. It is valid ${minutes} minutes, one use. Don't save it anywhere:`,
      `   ${made.command}`,
      `3. When it prints "Done", tell me to open ${googleLink()}, click Open Google Admin, then click Authorize. I must click Authorize myself.`,
      '4. Then tell me to click Check in Tico.',
    ].join('\n');
  }

  function accountsPrompt(g: GoogleConnectView) {
    const domain = defaultDomain(g) || 'our Google Workspace';
    return [
      `Set up Google sign-in for Tico. Use a browser signed in to Google Cloud as an admin of ${domain}.`,
      `Tico will ask Google for: ${asksFor(g)}.`,
      '',
      `1. Create or pick a Google Cloud project under ${domain}'s Google organization (the Internal audience needs one).`,
      '2. Turn on the Gmail API (gmail.googleapis.com) and the Google Calendar API (calendar-json.googleapis.com) in it: APIs & Services > Library, or gcloud services enable gmail.googleapis.com calendar-json.googleapis.com --project <project>.',
      '3. Google Auth Platform > Branding (or Overview > Get started): app name "Tico", a support email, audience Internal. The last step asks to agree to Google\'s API Services User Data Policy: ask me before you agree.',
      '4. Clients > Create client > Web application. Add this authorized redirect URI, exactly:',
      `   ${g.oauth?.redirect_uri || ''}`,
      '   Google shows the client secret once, in the dialog after Create: copy it then.',
      `5. Put the client ID and client secret into Tico at ${googleLink()} (Each person signs in > Set it up myself > Client ID, Client secret > Save), in my signed-in browser. If you can't use my browser, print them once for me to paste. Never write the secret to a file or anywhere else.`,
      '',
      'Google has no gcloud command for these OAuth web clients, so steps 3 and 4 need the browser.',
      'Then tell me to click Connect Google.',
    ].join('\n');
  }

  // The part of step 1 that shows the setup command (inside the prompt, or bare), in its own box so a Domain edit can
  // drop it in place without a repaint taking the cursor out of the field.
  function madePart(how: string, made: GoogleConnectCommand | null, g?: GoogleConnectView) {
    const inner = how === 'agent'
      ? (made ? promptBlock('workspace', workspacePrompt(made, g), '<button type="button" class="ghost" data-ws-prompt>New prompt</button>')
        : '<div class="row"><button type="button" class="primary" data-ws-prompt>Make prompt</button></div>')
      : (made ? `<p class="muted">Paste this in Cloud Shell. It expires at ${esc(clock(made.expires_at))}.</p>
          <div class="machine-command"><code>${esc(made.command)}</code>
          <button type="button" class="ghost" data-copy="${esc(made.command)}">Copy</button></div>` : '');
    return `<div data-ws-made>${inner}</div>`;
  }

  // A setup command names its domain, so a different Domain makes it wrong: drop it, and the next click makes one for
  // the new domain.
  function domainEdited(host: GoogleConnectHost, input: HTMLInputElement) {
    if (!command || input.value.trim().toLowerCase() === command.domain) return;
    command = null;
    const box = input.closest('[data-ws-step="create"]')?.querySelector('[data-ws-made]');
    if (box) box.outerHTML = madePart(howOf(host, 'workspace'), null);
  }

  function createStep(host: GoogleConnectHost, g: GoogleConnectView, {reachable, cancel, domainValue}: {reachable: boolean; cancel: boolean; domainValue: string}) {
    const made = live() ? command : null;
    const domain = `<label class="google-field"><span>Domain</span>
        <input type="text" data-keep="domain" data-ws-domain value="${esc(domainValue)}" autocomplete="off" spellcheck="false"></label>`;
    const upload = (label: string) => `<label class="google-upload"><span>${label}</span>
        <input type="file" accept="application/json,.json" data-ws-file></label>`;
    const back = cancel ? '<div class="row"><button type="button" class="ghost" data-ws-replace-cancel>Cancel</button></div>' : '';
    if (!reachable) {
      return `<div class="google-step" data-ws-step="create"><h4>1 Upload a key file</h4>${domain}
        <p class="muted">Cloud Shell can't reach this server, so upload the key file instead.</p>${upload('Key file')}${back}</div>`;
    }
    const how = howOf(host, 'workspace');
    const body = how === 'agent'
      ? madePart(how, made, g)
      : `<div class="row"><button type="button" class="primary" data-ws-command>New command</button>
          <a class="ghost" href="${CLOUD_SHELL}" target="_blank" rel="noopener">Open Cloud Shell</a></div>
          ${madePart(how, made, g)}
          <details class="google-more"><summary>Or upload a key file</summary>${upload('Key file')}</details>`;
    return `<div class="google-step" data-ws-step="create"><h4>1 Run one command in Cloud Shell</h4>${domain}
      ${howToggle('workspace', how)}${body}${back}</div>`;
  }

  function allowStep(row: GoogleKeyRow) {
    const scopes = (row.scopes || []).join(',');
    const link = DELEGATION.test(row.delegation_url || '')
      ? `<a class="primary" href="${esc(row.delegation_url)}" target="_blank" rel="noopener">Open Google Admin</a>` : '';
    return `<div class="google-step" data-ws-step="allow"><h4>2 Authorize in Google Admin</h4>
      ${link}
      <div class="machine-command google-copy"><span class="muted">Client ID</span><code>${esc(row.client_id || '')}</code>
        <button type="button" class="ghost" data-copy="${esc(row.client_id || '')}">Copy</button></div>
      <div class="machine-command google-copy"><span class="muted">Scopes</span><code>${esc(scopes)}</code>
        <button type="button" class="ghost" data-copy="${esc(scopes)}">Copy</button></div>
      <p class="muted">Add the client ID and scopes, then click Authorize.</p>
    </div>`;
  }

  function checkStep(g: GoogleConnectView) {
    const probe = g.me?.mailbox || '';
    return `<div class="google-step" data-ws-step="check"><h4>3 Check</h4>
      <label class="google-field"><span>Mailbox to check</span>
        <input type="email" data-keep="probe" data-ws-probe list="google-probe" value="${esc(probe)}"
          placeholder="Any covered mailbox" autocomplete="off" spellcheck="false"></label>
      <datalist id="google-probe">${probe ? `<option value="${esc(probe)}"></option>` : ''}</datalist>
      <div class="row"><button type="button" class="primary" data-ws-check>Check</button></div>
      <div data-ws-check-result role="status"></div>
    </div>`;
  }

  function workspaceCard(host: GoogleConnectHost, g: GoogleConnectView, {active, pend, replacing, flash}: GoogleConnectRows) {
    const reachable = g.setup?.reachable !== false;
    const parts: string[] = [];
    // A key authorized before the read-only scopes: step 2 again with every scope, then Check, keeping the key.
    const legacy = !!active && !pend && !replacing && active.narrowable === false;
    if (active && !replacing) parts.push(connectedBlock(active, !!pend || legacy));
    if (legacy) parts.push(allowStep(active));
    if (pend && !replacing) {
      if (!active) parts.push(`<p>Key uploaded for <strong>${esc(pend.domain || '')}</strong>.</p>`);
      parts.push(allowStep(pend));
    }
    if (replacing || (!active && !pend)) {
      parts.push(createStep(host, g, {reachable, cancel: !!(active || pend), domainValue: active?.domain || pend?.domain || defaultDomain(g)}));
    }
    if (!replacing && (pend || legacy || active?.status === 'failing')) parts.push(checkStep(g));
    if (!replacing && !active && pend) {
      parts.push(`<div class="row"><button type="button" class="ghost" data-ws-replace>Replace key</button>
        <button type="button" class="ghost danger" data-ws-disconnect>Disconnect</button></div>${disconnectConfirm(pend.domain, false)}`);
    }
    // A key authorized before the read-only scopes keeps its write scopes at Google; Tico holds What Tico may do.
    if (legacy) parts.push('<p class="muted" data-google-may-narrow>Redo step 2 to narrow Google access.</p>');
    return `<section class="google-card" data-google-card="workspace">${parts.join('')}
      <p class="muted" role="status" data-ws-status>${esc(flash)}</p></section>`;
  }

  function setupPanel(g: GoogleConnectView) {
    const project = (g.workspace || g.pending)?.project_id || '';
    return `<div class="google-step" data-oauth-setup>
      <label class="google-field"><span>Google Cloud project ID</span>
        <input type="text" data-keep="project" data-oauth-project value="${esc(project)}" autocomplete="off" spellcheck="false"></label>
      <ol class="google-steps">
        <li>Turn on the <a class="ghost" data-oauth-link="gmail" target="_blank" rel="noopener" aria-disabled="true">Gmail API</a>
          and the <a class="ghost" data-oauth-link="calendar" target="_blank" rel="noopener" aria-disabled="true">Google Calendar API</a>.</li>
        <li>Open <a class="ghost" data-oauth-link="overview" target="_blank" rel="noopener" aria-disabled="true">Auth overview</a>
          and fill in the app name, support email and audience.</li>
        <li>Open <a class="ghost" data-oauth-link="clients" target="_blank" rel="noopener" aria-disabled="true">Create a client</a>,
          choose Web application, and add this redirect URI:
          <div class="machine-command google-copy"><code>${esc(g.oauth?.redirect_uri || '')}</code>
            <button type="button" class="ghost" data-copy="${esc(g.oauth?.redirect_uri || '')}">Copy</button></div></li>
        <li>Copy the client ID and client secret Google shows after Create (it shows the secret once) and paste them below.</li>
      </ol>
      <p class="muted"><strong>Internal</strong>: only accounts in your domain. Gmail needs no Google verification. It needs a project in your organization.</p>
      <p class="muted"><strong>External</strong>: any Google account, but Gmail is a restricted scope, so Google's verification applies.</p>
      <form class="google-form" data-oauth-form>
        <label class="google-field"><span>Client ID</span>
          <input type="text" name="client_id" data-keep="client-id" autocomplete="off" spellcheck="false"></label>
        <label class="google-field"><span>Client secret</span>
          <input type="password" name="client_secret" data-keep="client-secret" autocomplete="off"></label>
        <div class="row"><button type="submit" class="primary">Save</button></div>
        <p class="muted" role="status" data-oauth-status></p>
      </form>
    </div>`;
  }

  function removeBlock() {
    return `<div class="row"><button type="button" class="ghost danger" data-oauth-remove>Remove Google app</button></div>
      <div class="google-confirm" data-oauth-remove-confirm hidden>
        <p>Remove the Google app for this server? People can't connect Google until it is set up again.</p>
        <div class="row"><button type="button" class="primary danger" data-oauth-remove-yes>Remove</button>
          <button type="button" class="ghost" data-oauth-remove-no>Cancel</button></div>
      </div>`;
  }

  function personalBody(state: GoogleConnectPersonal | null) {
    if (!state) return '';
    if (state.error) return `<div class="empty">${esc(state.error)}</div>`;
    const box = `<strong>${esc(state.mailbox)}</strong>`;
    const reads = !state.configured ? '' : state.app === 'own'
      ? '<p class="muted">With your company\'s Google app, Connect Google can also label, archive and draft mail, and add calendar events. Tico\'s shared app only reads your mail and calendar. Sending stays off.</p>'
      : '<p class="muted">Tico asks Google only to read your mail and calendar. It can\'t send, label, archive or change anything, and sending stays off.</p>';
    const busy = ['ready', 'finishing', 'disconnecting', 'revoking'].includes(state.status ?? '');
    // What the grant can write beyond reading: per service (state.acts = {mail, calendar}), named in the connected line.
    const acts: GoogleConnectActs = state.acts || {};
    const writes = [acts.mail && 'label, archive and draft mail', acts.calendar && 'add calendar events'].filter(Boolean);
    const extra = writes.length === 2 ? `${writes[0]}, and ${writes[1]}` : writes[0];
    let body: string;
    if (!state.configured) {
      body = '<p>Connect Google isn\'t available on this Tico server yet: whoever runs it has not set up its Google app.</p>';
    } else if (state.status === 'connected') {
      body = `<p>Connected: Tico reads ${box}'s mail and calendar${extra ? `, and can also ${extra}` : ', read only'}.</p>${state.message ? `<p class="muted">${esc(state.hint)}</p>` : ''}${state.syncing ? '' : '<p class="muted">No message bot reads this mailbox yet, so nothing is synced until one does.</p>'}
        <div class="row"><button type="button" class="ghost" data-google-connect>Reconnect</button>
        <button type="button" class="ghost" data-google-disconnect>Disconnect</button></div>
        <p class="muted">Disconnect revokes Tico's access at Google. You can also remove it at <a href="https://myaccount.google.com/permissions" target="_blank" rel="noopener">myaccount.google.com/permissions</a>.</p>`;
    } else if (state.status === 'signing_in') {
      body = `<p>Waiting for Google's sign-in for ${box}.</p>
        <p class="muted">If Google said <strong>Access blocked</strong> or <strong>admin_policy_enforced</strong>, your Google Workspace administrator has a policy that blocks Tico. Only they can allow it (Admin console &gt; Security &gt; API controls &gt; App access control); Tico won't ask for more access to get around it.</p>
        <div class="row"><button type="button" class="primary" data-google-connect>Connect Google again</button></div>`;
    } else if (busy) {
      body = `<p>${state.status?.startsWith('dis') || state.status === 'revoking' ? 'Disconnecting' : 'Finishing the connection for'} ${box}…</p>
        ${state.stalled ? `<p role="alert"><strong>${esc(state.message)}</strong></p>
        <p class="muted">Remove Tico now at <a href="https://myaccount.google.com/permissions" target="_blank" rel="noopener">myaccount.google.com/permissions</a>. The computer finishes the disconnect when it is back online.</p>`
          : '<p class="muted">The computer that syncs your mail finishes this, usually within two minutes.</p>'}`;
    } else {
      const failed = state.status === 'failed' ? `<p role="alert"><strong>${esc(state.message)}</strong></p><p class="muted">${esc(state.hint)}</p>` : '';
      body = `${failed}<p>Connect ${box} so your message bot can read your inbox and calendar. You sign in to Google and approve; no administrator or setup is needed.</p>
        <p class="muted">If Google says it hasn't verified this app, this Tico server uses its own private Google app. Continue with Advanced only if you trust whoever runs this server.</p>
        <div class="row"><button type="button" class="primary" data-google-connect>Connect Google</button></div>`;
    }
    return body + reads;
  }

  // Your own mailbox: the Connect Google card, or why there is none.
  function yourAccount(g: GoogleConnectView, personal: GoogleConnectPersonal | null) {
    const covered = g.me?.covered_by_workspace ? '<p class="muted">Covered by the Workspace connection.</p>' : '';
    if (!g.me) return '<p class="muted">Sign in as a person to connect your own mailbox.</p>';
    return covered + personalBody(personal);
  }

  // Each person signs in, for an admin: Google sign-in for the server once, then your own mailbox. Everyone else's is
  // in the Inboxes list.
  function accountsCard(host: GoogleConnectHost, g: GoogleConnectView, personal: GoogleConnectPersonal | null) {
    const oauth: GoogleConnectOAuth = g.oauth || {};
    let first: string;
    if (oauth.configured) {
      first = oauth.source === 'env' ? '<p class="muted">Done. Set on the server.</p>' : `<p class="muted">Done.</p>${removeBlock()}`;
    } else {
      const how = howOf(host, 'accounts');
      first = howToggle('accounts', how) + (how === 'agent' ? promptBlock('accounts', accountsPrompt(g)) : setupPanel(g));
    }
    const later = oauth.configured ? '' : ' google-later';
    return `<section class="google-card" data-google-card="account">
      <div class="google-step" data-acct-step="signin"><h4>1 Set up Google sign-in (once)</h4>${first}</div>
      <div class="google-step${later}" data-acct-step="you"><h4>2 Connect your account</h4>${oauth.configured ? yourAccount(g, personal) : ''}</div>
      <p role="status" data-google-status></p></section>`;
  }

  // Everyone who isn't an admin: Connect Google for their own mailbox, never the setup; nothing when the company's
  // key covers it (Tools › Settings › Email says so).
  function memberCard(g: GoogleConnectView, personal: GoogleConnectPersonal | null) {
    if (g.me?.covered_by_workspace) return '';
    const body = g.oauth?.configured ? yourAccount(g, personal) : '<p>Ask an admin to connect Google in Tools › Settings.</p>';
    return `<section class="google-card" data-google-card="account">${body}
      <p role="status" data-google-status></p></section>`;
  }

  // The way an admin sees: the one picked, else the one set up (the Workspace when both are, since it covers the
  // mailboxes), else neither until one is picked. A way once shown stays shown when it is taken down again.
  function modeOf(host: GoogleConnectHost, g: GoogleConnectView) {
    const {active, pend} = rowsOf(g);
    const set = active || pend ? 'workspace' : g.oauth?.configured ? 'accounts' : '';
    if (!host._googlePick && set) host._googlePick = set;
    return host._googlePick || '';
  }

  const MODES = [['workspace', 'Whole company'], ['accounts', 'Each person signs in']];
  const modeToggle = (mode: string) => `<div class="theme-seg google-mode" role="radiogroup" aria-label="How Tico connects" data-google-mode>${
    MODES.map(([key, label]) => `<button type="button" role="radio" data-google-pick="${key}" aria-checked="${mode === key}">${label}</button>`).join('')}</div>`;

  function adminView(host: GoogleConnectHost, g: GoogleConnectView, personal: GoogleConnectPersonal | null, rows: GoogleConnectRows) {
    const mode = modeOf(host, g);
    const body = mode === 'workspace' ? workspaceCard(host, g, rows) : mode === 'accounts' ? accountsCard(host, g, personal) : '';
    return modeToggle(mode) + body;
  }

  function paintCheck(host: GoogleConnectHost) {
    const result = host.querySelector('[data-ws-check-result]');
    if (result) {
      const {pend} = rowsOf(host._googleState?.g || {});
      result.innerHTML = host._googleCheck?.html ?? (pend?.last_error ? `<p class="muted">${esc(pend.last_error)}</p>` : '');
    }
    const button = host.querySelector('[data-ws-check]');
    if (button) button.textContent = checkLabel(host);
  }

  function projectLinks(host: GoogleConnectHost) {
    const input = host.querySelector<HTMLInputElement>('[data-oauth-project]');
    if (!input) return;
    const project = input.value.trim();
    const valid = PROJECT_ID.test(project);
    const id = encodeURIComponent(project);
    const urls: Record<string, string> = {
      overview: `https://console.cloud.google.com/auth/overview?project=${id}`,
      clients: `https://console.cloud.google.com/auth/clients/create?project=${id}`,
      gmail: `https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=${id}`,
      calendar: `https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=${id}`,
    };
    host.querySelectorAll<HTMLAnchorElement>('[data-oauth-link]').forEach(link => {
      if (valid) { link.href = urls[link.dataset.oauthLink ?? ''] ?? ''; link.removeAttribute('aria-disabled'); }
      else { link.removeAttribute('href'); link.setAttribute('aria-disabled', 'true'); }
    });
  }

  function paint(host: GoogleConnectHost) {
    if (!host._googleState) return;
    const {g, personal} = host._googleState;
    const {active, pend} = rowsOf(g);
    const replacing = replacingNow(host, g);
    // Typed values live on the host, in memory only, so a field that leaves the page (Have my agent do it) comes back
    // as it was. saveOAuth drops the client secret once Google sign-in is saved.
    host._googleKeep = host._googleKeep || {};
    const kept = host._googleKeep;
    host.querySelectorAll<HTMLInputElement>('[data-keep]').forEach(el => { kept[el.dataset.keep ?? ''] = el.value; });
    const flash = host._googleFlash || '';
    host._googleFlash = '';
    host.innerHTML = g.can_admin ? adminView(host, g, personal, {active, pend, replacing, flash}) : memberCard(g, personal);
    host.querySelectorAll<HTMLInputElement>('[data-keep]').forEach(el => { const value = kept[el.dataset.keep ?? '']; if (value !== undefined) el.value = value; });
    paintCheck(host);
    projectLinks(host);
  }

  function setWsStatus(host: GoogleConnectHost, message: string, isErr = false) {
    const el = host.querySelector('[data-ws-status]');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('google-error', isErr);
  }

  const toggle = (host: GoogleConnectHost, selector: string, show: boolean) => { const el = host.querySelector<HTMLElement>(selector); if (el) el.hidden = !show; };

  async function copy(el: HTMLElement) {
    const area = el.hasAttribute('data-copy-prompt') ? el.closest('.google-prompt')?.querySelector('textarea') : null;
    try { await copyText(area ? area.value : el.dataset.copy ?? ''); toast(el.hasAttribute('data-copy-link') ? 'Link copied' : 'Copied'); }
    catch (error) {
      const code = el.closest('.machine-command')?.querySelector('code');
      if (area) area.select();
      else if (code) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
      toast('Could not copy. The text is selected.', true);
    }
  }

  // New command and Make prompt: each click makes a fresh setup code, the newest generation, so any older one is superseded.
  async function newCommand(host: GoogleConnectHost, button: GoogleConnectControl) {
    const domain = (host.querySelector<HTMLInputElement>('[data-ws-domain]')?.value || '').trim().toLowerCase();
    if (!domain) return setWsStatus(host, 'Enter the domain first.', true);
    button.disabled = true;
    try {
      const made: {command: string; expires_at: string; generation: number} = await post('/v2/google/workspace/setup-code', {domain});
      // The key this command brings in carries this generation, so any key at or past it uses the command up.
      command = {command: made.command, expires_at: made.expires_at, domain, generation: made.generation};
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      setWsStatus(host, errorMessage(error, ''), true);
    }
  }

  async function uploadKey(host: GoogleConnectHost, input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > KEY_MAX_BYTES) return setWsStatus(host, 'That file is too large to be a key file.', true);
    let key = null;
    try { key = JSON.parse(await file.text()); } catch (error) { key = null; }
    if (!key || typeof key !== 'object' || Array.isArray(key)) return setWsStatus(host, 'That isn\'t a Google key file.', true);
    const domain = (host.querySelector<HTMLInputElement>('[data-ws-domain]')?.value || '').trim().toLowerCase();
    setWsStatus(host, 'Uploading the key…');
    try {
      await post('/v2/google/workspace/key', domain ? {key, domain} : {key});
      host._googleReplacing = null;
      command = null; // the uploaded key supersedes the setup command
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) { setWsStatus(host, errorMessage(error, ''), true); }
  }

  // One check, by hand or on the 20 s timer. An unauthorized answer keeps the timer going for 10 minutes.
  async function check(host: GoogleConnectHost, auto: boolean) {
    let watch = host._googleWatch;
    if (auto) {
      if (!watch || watch.ended) return;
    } else {
      clearTimeout(watch?.timer ?? undefined);
      const mailbox = (host.querySelector<HTMLInputElement>('[data-ws-probe]')?.value || '').trim();
      watch = host._googleWatch = {until: Date.now() + CHECK_FOR_MS, mailbox, timer: null, ended: false,
        generation: newestGeneration(host._googleState?.g || {})};
      host._googleCheck = {html: '<p class="muted">Checking…</p>'};
      paintCheck(host);
    }
    let answer: GoogleConnectCheck;
    try { answer = await post('/v2/google/workspace/check', watch.mailbox ? {mailbox: watch.mailbox} : {}); }
    catch (error) {
      if (host._googleWatch !== watch) return;
      host._googleWatch = null;
      host._googleCheck = {html: `<p role="alert">${esc(errorMessage(error, ''))}</p>`};
      return paintCheck(host);
    }
    if (host._googleWatch !== watch) return;
    if (answer.ok) {
      host._googleWatch = null;
      host._googleCheck = null;
      host._googleFlash = `Connected · checked ${answer.mailbox || watch.mailbox || 'a covered mailbox'}`;
      return window.mountGoogleConnect(host, {force: true});
    }
    if (answer.error_kind === 'unauthorized_client') {
      const going = watch.until > Date.now();
      watch.ended = !going;
      host._googleCheck = {html: causesHtml(going)};
      if (going) {
        clearTimeout(watch.timer ?? undefined);
        watch.timer = setTimeout(() => {
          watch.timer = null;
          if (host._googleWatch !== watch) return;
          if (watchable(host)) void check(host, true);
          else {
            watch.paused = true;
            host._googleCheck = {html: causesHtml(false)};
            paintCheck(host);
          }
        }, CHECK_EVERY_MS);
      }
      return paintCheck(host);
    }
    host._googleWatch = null;
    host._googleCheck = {html: `<p role="alert">${esc(answer.message || 'Google did not confirm the check.')}</p>`};
    paintCheck(host);
  }

  async function disconnectWorkspace(host: GoogleConnectHost, button: GoogleConnectControl) {
    button.disabled = true;
    try {
      await writeRequest('DELETE', '/v2/google/workspace', undefined);
      clearTimeout(host._googleWatch?.timer ?? undefined);
      host._googleWatch = null;
      host._googleCheck = null;
      host._googleReplacing = null;
      command = null; // a setup command made before the disconnect must not show again
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      setWsStatus(host, errorMessage(error, ''), true);
    }
  }

  async function saveOAuth(host: GoogleConnectHost, form: HTMLFormElement) {
    const status = form.querySelector('[data-oauth-status]');
    if (!status) return;
    const clientId = (form.querySelector<HTMLInputElement>('[name=client_id]')?.value || '').trim();
    const clientSecret = (form.querySelector<HTMLInputElement>('[name=client_secret]')?.value || '').trim();
    if (!clientId || !clientSecret) { status.textContent = 'Paste the client ID and the client secret.'; return; }
    const button = form.querySelector<HTMLButtonElement>('button[type=submit]');
    if (!button) return;
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      await put('/v2/google/oauth-client', {client_id: clientId, client_secret: clientSecret});
      form.reset();
      if (host._googleKeep) { delete host._googleKeep['client-id']; delete host._googleKeep['client-secret']; }
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      status.textContent = errorMessage(error, '');
    }
  }

  async function removeOAuth(host: GoogleConnectHost, button: GoogleConnectControl) {
    button.disabled = true;
    try {
      await writeRequest('DELETE', '/v2/google/oauth-client', undefined);
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      const status = host.querySelector('[data-google-status]');
      if (status) status.textContent = errorMessage(error, '');
    }
  }

  async function connectPersonal(host: GoogleConnectHost, button: GoogleConnectControl) {
    const status = host.querySelector('[data-google-status]');
    button.disabled = true;
    try {
      const started: {auth_url: string; mailbox: string} = await post('/v2/mail/google/connect', {});
      // Only Google's own sign-in page, never an address from anywhere else.
      if (!/^https:\/\/accounts\.google\.com\//.test(started.auth_url || '')) throw new Error('Unexpected sign-in address');
      location.href = started.auth_url;
    } catch (error) {
      if (status) status.textContent = errorMessage(error, '');
      button.disabled = false;
    }
  }

  async function disconnectPersonal(host: GoogleConnectHost, button: GoogleConnectControl) {
    const personal = host._googleState?.personal;
    if (!personal) return;
    if (!(await askConfirm(`Disconnect ${personal.mailbox}? Tico's access is revoked at Google, and your message bot stops reading this inbox.`))) return;
    const status = host.querySelector('[data-google-status]');
    button.disabled = true;
    try {
      await post('/v2/mail/google/disconnect', {});
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      if (status) status.textContent = errorMessage(error, '');
      button.disabled = false;
    }
  }

  async function onClick(host: GoogleConnectHost, el: GoogleConnectControl) {
    if (el.hasAttribute('data-copy') || el.hasAttribute('data-copy-prompt')) return copy(el);
    if (el.hasAttribute('data-google-pick')) { host._googlePick = el.dataset.googlePick; return paint(host); }
    if (el.hasAttribute('data-google-how')) {
      host._googleHow = {...host._googleHow, [String(el.dataset.googlePath)]: el.dataset.googleHow};
      return paint(host);
    }
    if (el.hasAttribute('data-ws-prompt')) return newCommand(host, el);
    if (el.hasAttribute('data-google-connect')) return connectPersonal(host, el);
    if (el.hasAttribute('data-google-disconnect')) return disconnectPersonal(host, el);
    if (el.hasAttribute('data-ws-command')) return newCommand(host, el);
    if (el.hasAttribute('data-ws-replace')) {
      host._googleReplacing = {since: newestGeneration(host._googleState?.g || {})};
      // A mount, not a paint: it re-reads the key state and starts the poll that waits for the new key.
      return window.mountGoogleConnect(host, {force: true});
    }
    if (el.hasAttribute('data-ws-replace-cancel')) { host._googleReplacing = null; return paint(host); }
    if (el.hasAttribute('data-ws-disconnect')) return toggle(host, '[data-ws-disconnect-confirm]', true);
    if (el.hasAttribute('data-ws-disconnect-no')) return toggle(host, '[data-ws-disconnect-confirm]', false);
    if (el.hasAttribute('data-ws-disconnect-yes')) return disconnectWorkspace(host, el);
    if (el.hasAttribute('data-ws-check') || el.hasAttribute('data-ws-recheck')) return check(host, false);
    if (el.hasAttribute('data-oauth-remove')) return toggle(host, '[data-oauth-remove-confirm]', true);
    if (el.hasAttribute('data-oauth-remove-no')) return toggle(host, '[data-oauth-remove-confirm]', false);
    if (el.hasAttribute('data-oauth-remove-yes')) return removeOAuth(host, el);
  }

  function bind(host: GoogleConnectHost) {
    if (host._googleBound) return;
    host._googleBound = true;
    host.addEventListener('click', event => {
      const el = eventElement(event)?.closest<GoogleConnectControl>('button, a');
      if (el) void onClick(host, el);
    });
    host.addEventListener('submit', event => {
      if (!(event.target instanceof HTMLFormElement) || !event.target.matches('[data-oauth-form]')) return;
      event.preventDefault();
      void saveOAuth(host, event.target);
    });
    host.addEventListener('change', event => {
      if (event.target instanceof HTMLInputElement && event.target.matches('[data-ws-file]')) void uploadKey(host, event.target);
    });
    host.addEventListener('input', event => {
      if (eventElement(event)?.matches('[data-oauth-project]')) projectLinks(host);
      if (event.target instanceof HTMLInputElement && event.target.matches('[data-ws-domain]')) domainEdited(host, event.target);
    });
  }

  // Loads GET /v2/google (and the person's own state when Connect Google is set up), then paints. A render that
  // changes nothing is skipped, so typed values and the open confirm stay put. Inboxes calls this on every
  // visit to the tab; it never makes a setup code.
  async function mountGoogleConnect(host: GoogleConnectHost | null, {force = false}: {force?: boolean} = {}) {
    if (!host) return;
    host._googleTurn = (host._googleTurn || 0) + 1;
    const turn = host._googleTurn;
    clearTimeout(host._googlePoll);
    let g: GoogleConnectView, personal: GoogleConnectPersonal | null = null;
    try {
      g = await get('/v2/google', {fresh: true});
      if (g.oauth?.configured && g.me) {
        try { personal = await get('/v2/mail/google', {fresh: true}); }
        catch (error) { personal = {error: errorMessage(error, '')}; }
      }
    } catch (error) {
      if (turn === host._googleTurn && host.isConnected) host.innerHTML = `<div class="empty">${esc(errorMessage(error, ''))}</div>`;
      return;
    }
    if (turn !== host._googleTurn || !host.isConnected) return;
    host._googleState = {g, personal};
    const {active} = rowsOf(g);
    // A setup command is used up by the first read that shows a key, pending or active, at its generation or newer.
    // Every read checks this, so a key that arrived while the tab was hidden still counts on the next visit.
    // A setup command another admin made since supersedes this one too (the server takes only the newest).
    if (command && (newestGeneration(g) >= command.generation || (g.setup?.newest ?? 0) > command.generation)) command = null;
    const replacing = replacingNow(host, g);
    const isLive = live();
    const snapshot = JSON.stringify([g.can_admin, unchecked(g.workspace), unchecked(g.pending), g.oauth, g.me,
      g.setup?.reachable, g.permissions, personal, isLive]);
    if (force || host._googleSnap !== snapshot || !host.childElementCount) {
      host._googleSnap = snapshot;
      paint(host);
    }
    bind(host);
    // Returning to a paused check keeps its original deadline, including the time spent away. A key replaced or
    // disconnected meanwhile (another tab or admin) ends it instead: its mailbox and deadline were for the old key.
    const watch = host._googleWatch;
    if (watch && !watch.ended && watchable(host) && (watch.paused || watch.until <= Date.now())
        && (replacing || newestGeneration(g) !== watch.generation)) {
      clearTimeout(watch.timer ?? undefined);
      host._googleWatch = null;
      host._googleCheck = null;
      paintCheck(host);
    } else if (watch && !watch.ended && watchable(host) && (watch.paused || watch.until <= Date.now())) {
      clearTimeout(watch.timer ?? undefined);
      watch.timer = null;
      watch.paused = false;
      watch.ended = watch.until <= Date.now();
      if (watch.ended) {
        host._googleCheck = {html: causesHtml(false)};
        paintCheck(host);
      } else {
        host._googleCheck = {html: '<p class="muted">Checking…</p>'};
        paintCheck(host);
        void check(host, true);
      }
    }
    const personalBusy = !!personal && !personal.error && ['ready', 'finishing', 'disconnecting', 'revoking'].includes(personal.status ?? '');
    // Polls for the whole replacement, and while a live command waits for a first key.
    const waitingForKey = !!g.can_admin && (replacing || (isLive && !active));
    if ((personalBusy || waitingForKey) && watchable(host)) {
      host._googlePoll = setTimeout(() => { if (watchable(host)) void mountGoogleConnect(host); }, POLL_MS);
    }
  }

  window.mountGoogleConnect = mountGoogleConnect;
})();
