/* Tools > Slack: paste the two tokens of the Slack app created from the manifest
   (docs/slack.md). They are sent once and stored encrypted; the browser is never sent them back. */
'use strict';

// The state /v2/slack/app answers with. get() and post() are untyped JS, so the shape is declared here, not checked.
interface SlackAppState { configured?: boolean; state?: string; message?: string; }
interface Window {
  mountSlackConnect: (host: HTMLElement | null) => Promise<void>;
}

// The `!`: the markup this file just drew always has the element; a missing one throws on first use, as the old lookup did.
function slackEl<T extends Element>(root: ParentNode, selector: string): T { return root.querySelector<T>(selector)!; }

window.mountSlackConnect = async function (host) {
  if (!host) return;
  const text = (value: unknown) => { const s = document.createElement('span'); s.textContent = String(value ?? ''); return s.innerHTML; };
  let state: SlackAppState;
  try { state = await get('/v2/slack/app'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(errorMessage(error, ''))}</div>`; return; }
  if (!host.isConnected) return;
  const labels: Record<string, string> = {connected: 'Connected.', waiting: 'Tokens saved. Waiting for the Slack service (COMPOSE_PROFILES needs <code>slack</code>).',
    disconnected: 'Disconnected'};
  if (state.configured) {
    host.innerHTML = `<p data-slack-state="${text(state.state)}"><strong>${labels[state.state ?? ''] || 'Tokens saved.'}</strong>
      ${state.state === 'disconnected' && state.message ? ' ' + text(state.message) : ''}</p>
      <div class="row"><button type="button" class="ghost" data-slack-disconnect>Disconnect</button></div>
      <p class="muted">Disconnect forgets the tokens here; remove the app on Slack to revoke them.</p>
      <p role="status" data-slack-status></p>`;
    const disconnect = slackEl<HTMLButtonElement>(host, '[data-slack-disconnect]');
    disconnect.onclick = async () => {
      if (!(await askConfirm('Forget the Slack tokens?', disconnect))) return;
      disconnect.disabled = true;
      try { await post('/v2/slack/disconnect', {}); window.mountSlackConnect(host); }
      catch (error) { slackEl<HTMLElement>(host, '[data-slack-status]').textContent = errorMessage(error, ''); disconnect.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form data-slack-form style="display:grid;gap:10px;max-width:460px">
      <p class="muted">Create an app at api.slack.com/apps from <a href="#" data-slack-manifest>this manifest</a>, install it, then paste the bot token (xoxb-) and an app-level token (xapp-, <code>connections:write</code>).</p>
      <label>Bot token<input name="bot_token" type="password" required autocomplete="off" spellcheck="false" placeholder="xoxb-..."></label>
      <label>App-level token<input name="app_token" type="password" required autocomplete="off" spellcheck="false" placeholder="xapp-..."></label>
      <button class="primary" type="submit">Connect Slack</button>
      <p role="status" data-slack-status></p></form>`;
  const status = slackEl<HTMLElement>(host, '[data-slack-status]');
  slackEl<HTMLAnchorElement>(host, '[data-slack-manifest]').onclick = async event => {
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(JSON.stringify(await get('/v2/slack/manifest'), null, 2));
      status.textContent = 'Manifest copied.';
    } catch (error) { status.textContent = errorMessage(error, ''); }
  };
  const form = slackEl<HTMLFormElement>(host, '[data-slack-form]');
  form.onsubmit = async event => {
    event.preventDefault();
    try {
      await put('/v2/slack/tokens', {bot_token: slackEl<HTMLInputElement>(form, '[name=bot_token]').value.trim(), app_token: slackEl<HTMLInputElement>(form, '[name=app_token]').value.trim()});
      window.mountSlackConnect(host);
    } catch (error) { status.textContent = errorMessage(error, ''); }
  };
};
