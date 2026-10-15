/* Settings > Inbox: connect your own Google account, read only (docs/mail.md, Connect your Google account).
   Google's sign-in opens in this tab and comes back to the server; the computer that syncs mail keeps the grant.
   No token ever reaches the browser. */
'use strict';

window.mountGoogleConnect = async function (host) {
  if (!host) return;
  const text = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  clearTimeout(host._googlePoll);
  let state;
  try { state = await get('/v2/mail/google'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  const box = `<strong>${text(state.mailbox)}</strong>`;
  const reads = '<p class="muted">Tico asks Google only to read your mail and calendar. It can\'t send, label, archive or change anything, and sending stays off.</p>';
  const busy = ['signing_in', 'ready', 'finishing', 'disconnecting', 'revoking'].includes(state.status);
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
      <p class="muted">The computer that syncs your mail finishes this, usually within two minutes.</p>`;
  } else {
    const failed = state.status === 'failed' ? `<p role="alert"><strong>${text(state.message)}</strong></p><p class="muted">${text(state.hint)}</p>` : '';
    body = `${failed}<p>Connect ${box} so your message bot can read your inbox and calendar. You sign in to Google and approve; no administrator or setup is needed.</p>
      <p class="muted">If Google says it hasn't verified this app, this Tico server uses its own private Google app. Continue with Advanced only if you trust whoever runs this server.</p>
      <div class="row"><button type="button" class="primary" data-google-connect>Connect Google</button></div>`;
  }
  host.innerHTML = body + (state.configured ? reads : '') + '<p role="status" data-google-status></p>';
  const status = host.querySelector('[data-google-status]');
  const connect = host.querySelector('[data-google-connect]');
  if (connect) connect.onclick = async () => {
    connect.disabled = true;
    try {
      const started = await post('/v2/mail/google/connect', {});
      // Only Google's own sign-in page, never an address from anywhere else.
      if (!/^https:\/\/accounts\.google\.com\//.test(started.auth_url || '')) throw new Error('Unexpected sign-in address');
      location.href = started.auth_url;
    } catch (error) { status.textContent = error.message; connect.disabled = false; }
  };
  const disconnect = host.querySelector('[data-google-disconnect]');
  if (disconnect) disconnect.onclick = async () => {
    if (!confirm(`Disconnect ${state.mailbox}? Tico's access is revoked at Google, and your message bot stops reading this inbox.`)) return;
    disconnect.disabled = true;
    try { await post('/v2/mail/google/disconnect', {}); window.mountGoogleConnect(host); }
    catch (error) { status.textContent = error.message; disconnect.disabled = false; }
  };
  if (busy && state.status !== 'signing_in') host._googlePoll = setTimeout(() => { if (host.isConnected) window.mountGoogleConnect(host); }, 5000);
};
