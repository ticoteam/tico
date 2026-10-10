/* Tools > GitHub: connect the team's GitHub organization through a GitHub App the
   owner creates there (docs/github-app.md). The private key never reaches the browser. */
'use strict';
window.mountGithubConnect = async function (host) {
  if (!host) return;
  const text = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  let state;
  try { state = await get('/v2/github/app'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  if (state.connected) {
    host.innerHTML = `<p>Connected to <strong>${text(state.org)}</strong> through the app <strong>${text(state.slug)}</strong>.
      <span data-gh-install>${state.installed ? 'Installed on the organization.' : 'Not installed yet.'}</span></p>
      ${state.administration ? '<p class="muted">BotOps can create bot repositories.</p>' : `<div data-gh-repo-fix>
        <p><strong>BotOps can't create bot repositories.</strong> The app was set up without Administration, so every new bot waits for a person.</p>
        <p>Fix it once: <a class="primary" role="button" href="${text(state.permissions_url)}" target="_blank" rel="noopener">Let BotOps create repositories</a>
        On that page set <strong>Administration</strong> to <strong>Read and write</strong> and save, accept the new permission for ${text(state.org)} when GitHub asks, then
        <button type="button" class="ghost" data-gh-recheck>Check again</button></p>
        ${(state.waiting || []).length ? `<p class="muted">Waiting for a repository: ${(state.waiting || []).map(w => `${text(w.bot)} (<a href="${text(w.create_url)}" target="_blank" rel="noopener">create ${text(w.repository)} yourself</a>)`).join(', ')}.</p>` : ''}
      </div>`}
      <p class="muted">Connected before release tracking? In <a href="https://github.com/organizations/${encodeURIComponent(state.org || '')}/settings/apps/${encodeURIComponent(state.slug || '')}/permissions" target="_blank" rel="noopener">the app's events on GitHub</a>, tick <strong>Release</strong>. Pushed tags work without it.</p>
      ${state.installed ? '' : `<p class="muted">Choose <strong>All repositories</strong> when GitHub asks. Each bot's token still covers only its own repository.</p>`}
      <div class="row">${state.installed ? '' : `<a class="primary" role="button" href="${text(state.install_url)}" target="_blank" rel="noopener">Install on ${text(state.org)}</a>`}
      <button type="button" class="ghost" data-gh-disconnect>Disconnect</button>
      <a href="${text(state.uninstall_url)}" target="_blank" rel="noopener">Uninstall on GitHub</a></div>
      <p class="muted">Disconnect forgets the app here; delete it on GitHub to revoke access.</p>
      <p role="status" data-gh-status></p>`;
    const recheck = host.querySelector('[data-gh-recheck]');
    if (recheck) recheck.onclick = async () => {
      recheck.disabled = true;
      const status = host.querySelector('[data-gh-status]');
      try {
        const now = await post('/v2/github/app/check-permissions', {});
        if (now.administration) window.mountGithubConnect(host);
        else { status.textContent = 'GitHub still reports no Administration permission. Accept it for the organisation, then check again.'; recheck.disabled = false; }
      } catch (error) { status.textContent = error.message; recheck.disabled = false; }
    };
    host.querySelector('[data-gh-disconnect]').onclick = async event => {
      if (!confirm('Forget the GitHub app?')) return;
      event.target.disabled = true;
      try { await post('/v2/github/app/disconnect', {}); window.mountGithubConnect(host); }
      catch (error) { host.querySelector('[data-gh-status]').textContent = error.message; event.target.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form data-gh-form style="display:grid;gap:10px;max-width:460px">
      <p class="muted">When GitHub asks where to install, choose <strong>All repositories</strong>. Each bot's token still covers only its own repository. For sensitive code, use a separate organization.</p>
      <label>Organization<input name="org" type="text" required maxlength="39" autocomplete="off" spellcheck="false" pattern="[A-Za-z0-9][A-Za-z0-9-]*" placeholder="your-org"></label>
      <label>App name<input name="name" type="text" maxlength="34" autocomplete="off" placeholder="Acme Tico"></label>
      <label><input type="checkbox" name="administration" checked> Let BotOps create bot repositories (recommended; without it a person creates each new bot's repository)</label>
      <button class="primary" type="submit">Connect GitHub</button>
      <p role="status" data-gh-status></p></form>`;
  host.querySelector('[data-gh-form]').onsubmit = async event => {
    event.preventDefault();
    const form = event.target, status = host.querySelector('[data-gh-status]');
    const query = new URLSearchParams({org: form.org.value.trim(), administration: String(form.administration.checked)});
    if (form.name.value.trim()) query.set('name', form.name.value.trim());
    try {
      const setup = await get('/v2/github/app/manifest?' + query);
      const post_form = document.createElement('form');
      post_form.method = 'post'; post_form.action = setup.action;
      const field = document.createElement('input');
      field.type = 'hidden'; field.name = 'manifest'; field.value = JSON.stringify(setup.manifest);
      post_form.append(field); document.body.append(post_form); post_form.submit();
    } catch (error) { status.textContent = error.message; }
  };
};
