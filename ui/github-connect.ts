/* Tools > GitHub: connect the team's GitHub organization through a GitHub App the
   owner creates there (docs/github-app.md). The private key never reaches the browser. */
'use strict';
const LOST_KEY_CONFIRM = 'The key for the saved GitHub App and directory sync secrets is lost, so they cannot be opened. Replace them? Any saved GitHub App and all directory sync credentials are removed, and you enter them again.';

// The state /v2/github/app answers with, and the setup /v2/github/app/manifest answers with. get() and post() are
// untyped JS, so these shapes are declared here, not checked.
interface GithubWaitingBot { bot?: string; repository?: string; create_url?: string | null; }
interface GithubAppState {
  connected?: boolean; org?: string; slug?: string; installed?: boolean; administration?: boolean;
  waiting?: GithubWaitingBot[]; permissions_url?: string | null; install_url?: string | null; uninstall_url?: string | null;
}
interface GithubManifestSetup { action: string; manifest: unknown; }
interface Window {
  mountGithubConnect: (host: HTMLElement | null) => Promise<void>;
}

// The `!`: the markup this file just drew always has the element; a missing one throws on first use, as the old lookup did.
function ghEl<T extends Element>(root: ParentNode, selector: string): T { return root.querySelector<T>(selector)!; }

window.mountGithubConnect = async function (host) {
  if (!host) return;
  const text = (value: unknown) => { const s = document.createElement('span'); s.textContent = String(value ?? ''); return s.innerHTML; };
  let state: GithubAppState;
  try { state = await get('/v2/github/app'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(errorMessage(error, ''))}</div>`; return; }
  if (!host.isConnected) return;
  if (state.connected) {
    host.innerHTML = `<p>Connected to <strong>${text(state.org)}</strong> through the app <strong>${text(state.slug)}</strong>.
      <span data-gh-install>${state.installed ? 'Installed on the organization.' : 'Not installed yet.'}</span></p>
      ${state.administration ? '<p class="muted">Tico can create bot repositories.</p>' : `<div data-gh-repo-fix>
        <p><strong>Tico can't create bot repositories.</strong> The app was set up without Administration, so every new bot waits for a person.</p>
        <p>Fix it once: <a class="primary" role="button" data-gh-permissions target="_blank" rel="noopener">Let Tico create repositories</a>
        On that page set <strong>Administration</strong> to <strong>Read and write</strong> and save, accept the new permission for ${text(state.org)} when GitHub asks, then
        <button type="button" class="ghost" data-gh-recheck>Check again</button></p>
        ${(state.waiting || []).length ? `<p class="muted">Waiting for a repository: ${(state.waiting || []).map((w, i) => `${text(w.bot)} (<a data-gh-create="${i}" target="_blank" rel="noopener">create ${text(w.repository)} yourself</a>)`).join(', ')}.</p>` : ''}
      </div>`}
      ${state.checks_withheld ? `<p class="muted" data-gh-checks-withheld>${text(state.checks_withheld)}</p>` : ''}
      <p class="muted">Connected before release tracking? In <a href="https://github.com/organizations/${encodeURIComponent(state.org || '')}/settings/apps/${encodeURIComponent(state.slug || '')}/permissions" target="_blank" rel="noopener">the app's events on GitHub</a>, tick <strong>Release</strong>. Pushed tags work without it.</p>
      ${state.installed ? '' : `<p class="muted">Choose <strong>All repositories</strong> when GitHub asks. Each bot's token still covers only its own repository.</p>`}
      <div class="row">${state.installed ? '' : `<a class="primary" role="button" href="${text(state.install_url)}" target="_blank" rel="noopener">Install on ${text(state.org)}</a>`}
      <button type="button" class="ghost" data-gh-disconnect>Disconnect</button>
      <a href="${text(state.uninstall_url)}" target="_blank" rel="noopener">Uninstall on GitHub</a></div>
      <p class="muted">Disconnect forgets the app here; delete it on GitHub to revoke access.</p>
      <p role="status" data-gh-status></p>`;
    // The server's GitHub links are set as properties, and only when they point at GitHub over https.
    const link = (node: HTMLAnchorElement | null, url: string | null | undefined) => { if (node && url && /^https:\/\/github\.com\//.test(url)) node.href = url; };
    link(host.querySelector<HTMLAnchorElement>('[data-gh-permissions]'), state.permissions_url);
    host.querySelectorAll<HTMLAnchorElement>('[data-gh-create]').forEach(node => { link(node, (state.waiting || [])[Number(node.dataset.ghCreate)]?.create_url); });
    const recheck = host.querySelector<HTMLButtonElement>('[data-gh-recheck]');
    if (recheck) recheck.onclick = async () => {
      recheck.disabled = true;
      const status = ghEl<HTMLElement>(host, '[data-gh-status]');
      try {
        const now: GithubAppState = await post('/v2/github/app/check-permissions', {});
        if (now.administration) window.mountGithubConnect(host);
        else { status.textContent = 'GitHub still reports no Administration permission. Accept it for the organisation, then check again.'; recheck.disabled = false; }
      } catch (error) { status.textContent = errorMessage(error, ''); recheck.disabled = false; }
    };
    const disconnect = ghEl<HTMLButtonElement>(host, '[data-gh-disconnect]');
    disconnect.onclick = async () => {
      if (!confirm('Forget the GitHub app?')) return;
      disconnect.disabled = true;
      try { await post('/v2/github/app/disconnect', {}); window.mountGithubConnect(host); }
      catch (error) { ghEl<HTMLElement>(host, '[data-gh-status]').textContent = errorMessage(error, ''); disconnect.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form data-gh-form style="display:grid;gap:10px;max-width:460px">
      <p class="muted">When GitHub asks where to install, choose <strong>All repositories</strong>. Each bot's token still covers only its own repository. For sensitive code, use a separate organization.</p>
      <label>Organization<input name="org" type="text" required maxlength="39" autocomplete="off" spellcheck="false" pattern="[A-Za-z0-9][A-Za-z0-9-]*" placeholder="your-org"></label>
      <label>App name<input name="name" type="text" maxlength="34" autocomplete="off" placeholder="Acme Tico"></label>
      <label><input type="checkbox" name="administration" checked> Let Tico create bot repositories (recommended; without it a person creates each new bot's repository)</label>
      <button class="primary" type="submit">Connect GitHub</button>
      <p role="status" data-gh-status></p></form>`;
  const form = ghEl<HTMLFormElement>(host, '[data-gh-form]');
  form.onsubmit = async event => {
    event.preventDefault();
    const status = ghEl<HTMLElement>(host, '[data-gh-status]');
    const org = ghEl<HTMLInputElement>(form, '[name=org]'), name = ghEl<HTMLInputElement>(form, '[name=name]');
    const query = new URLSearchParams({org: org.value.trim(), administration: String(ghEl<HTMLInputElement>(form, '[name=administration]').checked)});
    if (name.value.trim()) query.set('name', name.value.trim());
    try {
      let setup: GithubManifestSetup;
      try { setup = await get('/v2/github/app/manifest?' + query); }
      catch (error) {
        // github-app.key is lost with no backup copy: only an explicit confirm drops what it sealed (docs/install.md).
        if (errorCode(error) !== 'github_key_missing' || !confirm(LOST_KEY_CONFIRM)) throw error;
        await post('/v2/github/app/replace-lost-key', {});
        setup = await get('/v2/github/app/manifest?' + query);
      }
      const post_form = document.createElement('form');
      post_form.method = 'post'; post_form.action = setup.action;
      const field = document.createElement('input');
      field.type = 'hidden'; field.name = 'manifest'; field.value = JSON.stringify(setup.manifest);
      post_form.append(field); document.body.append(post_form); post_form.submit();
    } catch (error) { status.textContent = errorMessage(error, ''); }
  };
};
