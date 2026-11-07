/* Tools > Slack: paste the two tokens of the Slack app created from the manifest
   (docs/slack.md). They are sent once and stored encrypted; the browser is never sent them back. */
'use strict';
window.mountSlackConnect = async function (host) {
  if (!host) return;
  const text = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  let state;
  try { state = await get('/v2/slack/app'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  const labels = {connected: 'Connected.', waiting: 'Tokens saved. Waiting for the Slack service (COMPOSE_PROFILES needs <code>slack</code>).',
    disconnected: 'Disconnected'};
  if (state.configured) {
    host.innerHTML = `<p data-slack-state="${text(state.state)}"><strong>${labels[state.state] || 'Tokens saved.'}</strong>
      ${state.state === 'disconnected' && state.message ? ' ' + text(state.message) : ''}</p>
      <div class="row"><button type="button" class="ghost" data-slack-disconnect>Disconnect</button></div>
      <p class="muted">Disconnect forgets the tokens here; remove the app on Slack to revoke them.</p>
      <p role="status" data-slack-status></p>`;
    host.querySelector('[data-slack-disconnect]').onclick = async event => {
      if (!confirm('Forget the Slack tokens?')) return;
      event.target.disabled = true;
      try { await post('/v2/slack/disconnect', {}); window.mountSlackConnect(host); }
      catch (error) { host.querySelector('[data-slack-status]').textContent = error.message; event.target.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form data-slack-form style="display:grid;gap:10px;max-width:460px">
      <p class="muted">Create an app at api.slack.com/apps from <a href="#" data-slack-manifest>this manifest</a>, install it, then paste the bot token (xoxb-) and an app-level token (xapp-, <code>connections:write</code>).</p>
      <label>Bot token<input name="bot_token" type="password" required autocomplete="off" spellcheck="false" placeholder="xoxb-..."></label>
      <label>App-level token<input name="app_token" type="password" required autocomplete="off" spellcheck="false" placeholder="xapp-..."></label>
      <button class="primary" type="submit">Connect Slack</button>
      <p role="status" data-slack-status></p></form>`;
  const status = host.querySelector('[data-slack-status]');
  host.querySelector('[data-slack-manifest]').onclick = async event => {
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(JSON.stringify(await get('/v2/slack/manifest'), null, 2));
      status.textContent = 'Manifest copied.';
    } catch (error) { status.textContent = error.message; }
  };
  host.querySelector('[data-slack-form]').onsubmit = async event => {
    event.preventDefault();
    const form = event.target;
    try {
      await put('/v2/slack/tokens', {bot_token: form.bot_token.value.trim(), app_token: form.app_token.value.trim()});
      window.mountSlackConnect(host);
    } catch (error) { status.textContent = error.message; }
  };
};
