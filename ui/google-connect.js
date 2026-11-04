/* Settings > Google: two cards, both from GET /v2/google. Whole Workspace (admins) connects the Google Workspace with a
   service-account key (docs/mail.md); Your account connects the person's own mailbox, read only. Google's sign-in
   opens in this tab and comes back to the server; the computer that syncs mail keeps the grant. No token, key or
   client secret stays in the browser once it is sent. */
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
  // The newest setup command this page made. Only the New command button makes one, never a render or a poll.
  let command = null;

  const dateOf = iso => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d : null; };
  const clock = iso => dateOf(iso)?.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) || '';
  const when = iso => dateOf(iso)?.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) || '';
  const live = () => !!command && Date.parse(command.expires_at) > Date.now();
  const domainOf = address => String(address || '').split('@')[1]?.trim().toLowerCase() || '';
  const defaultDomain = g => domainOf(g.me?.mailbox) || domainOf((typeof S === 'object' && S?.me?.email) || '');
  const watchable = host => host.isConnected && !host.closest('[hidden]');
  const unchecked = row => (row ? {...row, checked_at: null} : null);
  // The active row (connected or failing) and the one pending row, which is a replacement while a key is active.
  const rowsOf = g => {
    const ws = g.workspace || null;
    return {active: ws && ws.status !== 'pending' ? ws : null, pend: g.pending || (ws && ws.status === 'pending' ? ws : null)};
  };
  // A replacement ends when the active row changes (a new key was promoted), so it never sticks.
  const replacingNow = (host, active) => {
    const match = !!host._googleReplacing && host._googleReplacing.since === (active?.connected_at ?? null);
    if (!match) host._googleReplacing = null;
    return match;
  };
  const checkLabel = host => (host._googleWatch?.ended ? 'Check again' : 'Check');
  const causesHtml = watching => `<p>Google hasn't accepted the key yet. Check these:</p>
    <ul class="google-causes">${CAUSES.map(cause => `<li>${esc(cause)}</li>`).join('')}</ul>
    <p class="muted" role="status">${watching ? 'Checking again every 20 seconds for 10 minutes.' : 'Still not accepted. Finish the steps above, then check again.'}</p>`;

  function connectedBlock(row) {
    const failing = row.status === 'failing';
    return `<div class="google-facts">
        <div><span class="muted">Domain</span> <strong>${esc(row.domain || '')}</strong></div>
        <div><span class="muted">Client ID</span> <code>${esc(row.client_id || '')}</code></div>
        <div><span class="muted">Connected</span> ${esc(when(row.connected_at))}</div>
      </div>
      ${failing ? `<p role="alert"><strong>Failing.</strong> ${esc(row.last_error || 'Tico could not reach Google with this key.')}</p>` : ''}
      <div class="row"><button type="button" class="ghost" data-ws-replace>Replace key</button>
        <button type="button" class="ghost danger" data-ws-disconnect>Disconnect</button></div>
      <div class="google-confirm" data-ws-disconnect-confirm hidden>
        <p>Disconnect Google for ${esc(row.domain || 'this domain')}? Tico stops reading every covered mailbox.</p>
        <div class="row"><button type="button" class="primary danger" data-ws-disconnect-yes>Disconnect</button>
          <button type="button" class="ghost" data-ws-disconnect-no>Cancel</button></div>
      </div>`;
  }

  function createStep(g, {reachable, cancel, domainValue}) {
    const made = live() ? command : null;
    const tools = reachable
      ? `<div class="row"><button type="button" class="primary" data-ws-command>New command</button>
          <a class="ghost" href="${CLOUD_SHELL}" target="_blank" rel="noopener">Open Cloud Shell</a></div>
          ${made ? `<p class="muted">Paste this in Cloud Shell. It expires at ${esc(clock(made.expires_at))}.</p>
          <div class="machine-command"><code>${esc(made.command)}</code>
          <button type="button" class="ghost" data-copy="${esc(made.command)}">Copy</button></div>` : ''}`
      : '<p class="muted">Cloud Shell can\'t reach this server, so upload the key file instead.</p>';
    return `<div class="google-step" data-ws-step="create"><h4>1 Create the key</h4>
      <label class="google-field"><span>Domain</span>
        <input type="text" data-keep="domain" data-ws-domain value="${esc(domainValue)}" autocomplete="off" spellcheck="false"></label>
      ${tools}
      <label class="google-upload"><span>Or upload the key file</span>
        <input type="file" accept="application/json,.json" data-ws-file></label>
      ${cancel ? '<div class="row"><button type="button" class="ghost" data-ws-replace-cancel>Cancel</button></div>' : ''}
    </div>`;
  }

  function allowStep(row) {
    const scopes = (row.scopes || []).join(',');
    const link = DELEGATION.test(row.delegation_url || '')
      ? `<a class="primary" href="${esc(row.delegation_url)}" target="_blank" rel="noopener">Open Google Admin</a>` : '';
    return `<div class="google-step" data-ws-step="allow"><h4>2 Allow it in Google Admin</h4>
      ${link}
      <div class="machine-command google-copy"><span class="muted">Client ID</span><code>${esc(row.client_id || '')}</code>
        <button type="button" class="ghost" data-copy="${esc(row.client_id || '')}">Copy</button></div>
      <div class="machine-command google-copy"><span class="muted">Scopes</span><code>${esc(scopes)}</code>
        <button type="button" class="ghost" data-copy="${esc(scopes)}">Copy</button></div>
      <p class="muted">Add the client ID and scopes, then click Authorize.</p>
    </div>`;
  }

  function checkStep(g) {
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

  function workspaceCard(g, {active, pend, replacing, flash}) {
    const reachable = g.setup?.reachable !== false;
    const parts = [];
    if (active && !replacing) parts.push(connectedBlock(active));
    if (pend && !replacing) {
      if (!active) parts.push(`<p>Key uploaded for <strong>${esc(pend.domain || '')}</strong>.</p>`);
      parts.push(allowStep(pend));
    }
    if (replacing || (!active && !pend)) {
      parts.push(createStep(g, {reachable, cancel: !!(active || pend), domainValue: active?.domain || pend?.domain || defaultDomain(g)}));
    }
    if (!replacing && (pend || active?.status === 'failing')) parts.push(checkStep(g));
    if (!replacing && !active && pend) parts.push('<div class="row"><button type="button" class="ghost" data-ws-replace>Replace key</button></div>');
    return `<section class="google-card" data-google-card="workspace"><h3>Whole Workspace</h3>${parts.join('')}
      <p class="muted" role="status" data-ws-status>${esc(flash)}</p></section>`;
  }

  function setupPanel(g) {
    const project = (g.workspace || g.pending)?.project_id || '';
    return `<div class="google-step" data-oauth-setup><h4>Set up Connect Google</h4>
      <p>Set it up once for everyone on this server.</p>
      <label class="google-field"><span>Google Cloud project ID</span>
        <input type="text" data-keep="project" data-oauth-project value="${esc(project)}" autocomplete="off" spellcheck="false"></label>
      <ol class="google-steps">
        <li>Open <a class="ghost" data-oauth-link="overview" target="_blank" rel="noopener" aria-disabled="true">Auth overview</a>
          and fill in the app name, support email and audience.</li>
        <li>Open <a class="ghost" data-oauth-link="clients" target="_blank" rel="noopener" aria-disabled="true">Create a client</a>,
          choose Web application, and add this redirect URI:
          <div class="machine-command google-copy"><code>${esc(g.oauth?.redirect_uri || '')}</code>
            <button type="button" class="ghost" data-copy="${esc(g.oauth?.redirect_uri || '')}">Copy</button></div></li>
        <li>Paste its client ID and client secret below.</li>
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
    return `<div class="google-step"><div class="row"><button type="button" class="ghost danger" data-oauth-remove>Remove Google app</button></div>
      <div class="google-confirm" data-oauth-remove-confirm hidden>
        <p>Remove the Google app for this server? People can't connect Google until it is set up again.</p>
        <div class="row"><button type="button" class="primary danger" data-oauth-remove-yes>Remove</button>
          <button type="button" class="ghost" data-oauth-remove-no>Cancel</button></div>
      </div></div>`;
  }

  function personalBody(state) {
    if (!state) return '';
    if (state.error) return `<div class="empty">${esc(state.error)}</div>`;
    const box = `<strong>${esc(state.mailbox)}</strong>`;
    const reads = state.configured ? '<p class="muted">Tico asks Google only to read your mail and calendar. It can\'t send, label, archive or change anything, and sending stays off.</p>' : '';
    const busy = ['ready', 'finishing', 'disconnecting', 'revoking'].includes(state.status);
    let body;
    if (!state.configured) {
      body = '<p>Connect Google isn\'t available on this Tico server yet: whoever runs it has not set up its Google app.</p>';
    } else if (state.status === 'connected') {
      body = `<p>Connected: Tico reads ${box}'s mail and calendar, read only.</p>${state.syncing ? '' : '<p class="muted">No message bot reads this mailbox yet, so nothing is synced until one does.</p>'}
        <div class="row"><button type="button" class="ghost" data-google-connect>Reconnect</button>
        <button type="button" class="ghost" data-google-disconnect>Disconnect</button></div>
        <p class="muted">Disconnect revokes Tico's access at Google. You can also remove it at <a href="https://myaccount.google.com/permissions" target="_blank" rel="noopener">myaccount.google.com/permissions</a>.</p>`;
    } else if (state.status === 'signing_in') {
      body = `<p>Waiting for Google's sign-in for ${box}.</p>
        <p class="muted">If Google said <strong>Access blocked</strong> or <strong>admin_policy_enforced</strong>, your Google Workspace administrator has a policy that blocks Tico. Only they can allow it (Admin console &gt; Security &gt; API controls &gt; App access control); Tico won't ask for more access to get around it.</p>
        <div class="row"><button type="button" class="primary" data-google-connect>Connect Google again</button></div>`;
    } else if (busy) {
      body = `<p>${state.status.startsWith('dis') || state.status === 'revoking' ? 'Disconnecting' : 'Finishing the connection for'} ${box}…</p>
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

  function accountCard(g, personal) {
    const parts = [];
    if (g.me?.covered_by_workspace) parts.push('<p class="muted">Covered by the Workspace connection.</p>');
    if (g.oauth?.configured) {
      parts.push(g.me ? personalBody(personal) : '<p class="muted">Sign in as a person to connect your own mailbox.</p>');
      if (g.can_admin && g.oauth.source === 'settings') parts.push(removeBlock());
      if (g.can_admin && g.oauth.source === 'env') parts.push('<p class="muted">Set on the server.</p>');
    } else if (g.can_admin) {
      parts.push(setupPanel(g));
    } else {
      parts.push('<p>Connect Google isn\'t set up on this server yet. Ask an admin to set up Google.</p>');
    }
    parts.push('<p role="status" data-google-status></p>');
    return `<section class="google-card" data-google-card="account"><h3>Your account</h3>${parts.join('')}</section>`;
  }

  function paintCheck(host) {
    const result = host.querySelector('[data-ws-check-result]');
    if (result) {
      const {pend} = rowsOf(host._googleState.g);
      result.innerHTML = host._googleCheck?.html ?? (pend?.last_error ? `<p class="muted">${esc(pend.last_error)}</p>` : '');
    }
    const button = host.querySelector('[data-ws-check]');
    if (button) button.textContent = checkLabel(host);
  }

  function projectLinks(host) {
    const input = host.querySelector('[data-oauth-project]');
    if (!input) return;
    const project = input.value.trim();
    const valid = PROJECT_ID.test(project);
    const id = encodeURIComponent(project);
    const urls = {
      overview: `https://console.cloud.google.com/auth/overview?project=${id}`,
      clients: `https://console.cloud.google.com/auth/clients/create?project=${id}`,
    };
    host.querySelectorAll('[data-oauth-link]').forEach(link => {
      if (valid) { link.href = urls[link.dataset.oauthLink]; link.removeAttribute('aria-disabled'); }
      else { link.removeAttribute('href'); link.setAttribute('aria-disabled', 'true'); }
    });
  }

  function paint(host) {
    const {g, personal} = host._googleState;
    const {active, pend} = rowsOf(g);
    const replacing = replacingNow(host, active);
    const kept = {};
    host.querySelectorAll('[data-keep]').forEach(el => { kept[el.dataset.keep] = el.value; });
    const flash = host._googleFlash || '';
    host._googleFlash = '';
    host.innerHTML = (g.can_admin ? workspaceCard(g, {active, pend, replacing, flash}) : '') + accountCard(g, personal);
    host.querySelectorAll('[data-keep]').forEach(el => { if (kept[el.dataset.keep] !== undefined) el.value = kept[el.dataset.keep]; });
    paintCheck(host);
    projectLinks(host);
  }

  function setWsStatus(host, message, isErr = false) {
    const el = host.querySelector('[data-ws-status]');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('google-error', isErr);
  }

  const toggle = (host, selector, show) => { const el = host.querySelector(selector); if (el) el.hidden = !show; };

  async function copy(el) {
    try { await copyText(el.dataset.copy); toast('Copied'); }
    catch (error) {
      const code = el.closest('.machine-command')?.querySelector('code');
      if (code) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      toast('Could not copy. The text is selected.', true);
    }
  }

  async function newCommand(host, button) {
    const domain = (host.querySelector('[data-ws-domain]')?.value || '').trim().toLowerCase();
    if (!domain) return setWsStatus(host, 'Enter the domain first.', true);
    button.disabled = true;
    try {
      const made = await post('/v2/google/workspace/setup-code', {domain});
      command = {command: made.command, expires_at: made.expires_at, domain};
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      setWsStatus(host, error.message, true);
    }
  }

  async function uploadKey(host, input) {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > KEY_MAX_BYTES) return setWsStatus(host, 'That file is too large to be a key file.', true);
    let key = null;
    try { key = JSON.parse(await file.text()); } catch (error) { key = null; }
    if (!key || typeof key !== 'object' || Array.isArray(key)) return setWsStatus(host, 'That isn\'t a Google key file.', true);
    const domain = (host.querySelector('[data-ws-domain]')?.value || '').trim().toLowerCase();
    setWsStatus(host, 'Uploading the key…');
    try {
      await post('/v2/google/workspace/key', domain ? {key, domain} : {key});
      host._googleReplacing = null;
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) { setWsStatus(host, error.message, true); }
  }

  // One check, by hand or on the 20 s timer. An unauthorized answer keeps the timer going for 10 minutes.
  async function check(host, auto) {
    let watch = host._googleWatch;
    if (auto) {
      if (!watch || watch.ended) return;
    } else {
      clearTimeout(watch?.timer);
      const mailbox = (host.querySelector('[data-ws-probe]')?.value || '').trim();
      watch = host._googleWatch = {until: Date.now() + CHECK_FOR_MS, mailbox, timer: null, ended: false};
      host._googleCheck = {html: '<p class="muted">Checking…</p>'};
      paintCheck(host);
    }
    let answer;
    try { answer = await post('/v2/google/workspace/check', watch.mailbox ? {mailbox: watch.mailbox} : {}); }
    catch (error) {
      if (host._googleWatch !== watch) return;
      host._googleWatch = null;
      host._googleCheck = {html: `<p role="alert">${esc(error.message)}</p>`};
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
        clearTimeout(watch.timer);
        watch.timer = setTimeout(() => { if (watchable(host) && host._googleWatch === watch) void check(host, true); }, CHECK_EVERY_MS);
      }
      return paintCheck(host);
    }
    host._googleWatch = null;
    host._googleCheck = {html: `<p role="alert">${esc(answer.message || 'Google did not confirm the check.')}</p>`};
    paintCheck(host);
  }

  async function disconnectWorkspace(host, button) {
    button.disabled = true;
    try {
      await writeRequest('DELETE', '/v2/google/workspace');
      clearTimeout(host._googleWatch?.timer);
      host._googleWatch = null;
      host._googleCheck = null;
      host._googleReplacing = null;
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      setWsStatus(host, error.message, true);
    }
  }

  async function saveOAuth(host, form) {
    const status = form.querySelector('[data-oauth-status]');
    const clientId = (form.querySelector('[name=client_id]')?.value || '').trim();
    const clientSecret = (form.querySelector('[name=client_secret]')?.value || '').trim();
    if (!clientId || !clientSecret) { status.textContent = 'Paste the client ID and the client secret.'; return; }
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      await put('/v2/google/oauth-client', {client_id: clientId, client_secret: clientSecret});
      form.reset();
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      status.textContent = error.message;
    }
  }

  async function removeOAuth(host, button) {
    button.disabled = true;
    try {
      await writeRequest('DELETE', '/v2/google/oauth-client');
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      button.disabled = false;
      const status = host.querySelector('[data-google-status]');
      if (status) status.textContent = error.message;
    }
  }

  async function connectPersonal(host, button) {
    const status = host.querySelector('[data-google-status]');
    button.disabled = true;
    try {
      const started = await post('/v2/mail/google/connect', {});
      // Only Google's own sign-in page, never an address from anywhere else.
      if (!/^https:\/\/accounts\.google\.com\//.test(started.auth_url || '')) throw new Error('Unexpected sign-in address');
      location.href = started.auth_url;
    } catch (error) {
      if (status) status.textContent = error.message;
      button.disabled = false;
    }
  }

  async function disconnectPersonal(host, button) {
    const personal = host._googleState.personal;
    if (!confirm(`Disconnect ${personal.mailbox}? Tico's access is revoked at Google, and your message bot stops reading this inbox.`)) return;
    const status = host.querySelector('[data-google-status]');
    button.disabled = true;
    try {
      await post('/v2/mail/google/disconnect', {});
      await window.mountGoogleConnect(host, {force: true});
    } catch (error) {
      if (status) status.textContent = error.message;
      button.disabled = false;
    }
  }

  async function onClick(host, el) {
    if (el.hasAttribute('data-copy')) return copy(el);
    if (el.hasAttribute('data-google-connect')) return connectPersonal(host, el);
    if (el.hasAttribute('data-google-disconnect')) return disconnectPersonal(host, el);
    if (el.hasAttribute('data-ws-command')) return newCommand(host, el);
    if (el.hasAttribute('data-ws-replace')) {
      host._googleReplacing = {since: rowsOf(host._googleState.g).active?.connected_at ?? null};
      return paint(host);
    }
    if (el.hasAttribute('data-ws-replace-cancel')) { host._googleReplacing = null; return paint(host); }
    if (el.hasAttribute('data-ws-disconnect')) return toggle(host, '[data-ws-disconnect-confirm]', true);
    if (el.hasAttribute('data-ws-disconnect-no')) return toggle(host, '[data-ws-disconnect-confirm]', false);
    if (el.hasAttribute('data-ws-disconnect-yes')) return disconnectWorkspace(host, el);
    if (el.hasAttribute('data-ws-check')) return check(host, false);
    if (el.hasAttribute('data-oauth-remove')) return toggle(host, '[data-oauth-remove-confirm]', true);
    if (el.hasAttribute('data-oauth-remove-no')) return toggle(host, '[data-oauth-remove-confirm]', false);
    if (el.hasAttribute('data-oauth-remove-yes')) return removeOAuth(host, el);
  }

  function bind(host) {
    if (host._googleBound) return;
    host._googleBound = true;
    host.addEventListener('click', event => {
      const el = event.target.closest?.('button, a');
      if (el) void onClick(host, el);
    });
    host.addEventListener('submit', event => {
      if (!event.target.matches?.('[data-oauth-form]')) return;
      event.preventDefault();
      void saveOAuth(host, event.target);
    });
    host.addEventListener('change', event => {
      if (event.target.matches?.('[data-ws-file]')) void uploadKey(host, event.target);
    });
    host.addEventListener('input', event => {
      if (event.target.matches?.('[data-oauth-project]')) projectLinks(host);
    });
  }

  // Loads GET /v2/google (and the person's own state when Connect Google is set up), then paints. A render that
  // changes nothing is skipped, so typed values and the open confirm stay put. Settings calls this on every visit to
  // the tab; it never makes a setup code.
  async function mountGoogleConnect(host, {force = false} = {}) {
    if (!host) return;
    const turn = host._googleTurn = (host._googleTurn || 0) + 1;
    clearTimeout(host._googlePoll);
    let g, personal = null;
    try {
      g = await get('/v2/google', {fresh: true});
      if (g.oauth?.configured && g.me) {
        try { personal = await get('/v2/mail/google', {fresh: true}); }
        catch (error) { personal = {error: error.message}; }
      }
    } catch (error) {
      if (turn === host._googleTurn && host.isConnected) host.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
      return;
    }
    if (turn !== host._googleTurn || !host.isConnected) return;
    host._googleState = {g, personal};
    const {active, pend} = rowsOf(g);
    const replacing = replacingNow(host, active);
    const isLive = live();
    const snapshot = JSON.stringify([g.can_admin, unchecked(g.workspace), unchecked(g.pending), g.oauth, g.me,
      g.setup?.reachable, personal, isLive]);
    if (force || host._googleSnap !== snapshot || !host.childElementCount) {
      host._googleSnap = snapshot;
      paint(host);
    }
    bind(host);
    const personalBusy = !!personal && !personal.error && ['ready', 'finishing', 'disconnecting', 'revoking'].includes(personal.status);
    const waitingForKey = !!g.can_admin && isLive && !pend && (!active || replacing);
    if ((personalBusy || waitingForKey) && watchable(host)) {
      host._googlePoll = setTimeout(() => { if (watchable(host)) void mountGoogleConnect(host); }, POLL_MS);
    }
  }

  window.mountGoogleConnect = mountGoogleConnect;
})();
