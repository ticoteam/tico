/* Tools > GitHub: connect the team's GitHub account or organization through a GitHub App the
   owner creates there (docs/github-app.md). The private key never reaches the browser. */
'use strict';
const LOST_KEY_CONFIRM = 'The key for the saved GitHub App and directory sync secrets is lost, so they cannot be opened. Replace them? Any saved GitHub App and all directory sync credentials are removed, and you enter them again.';

// The state /v2/github/app answers with, and the setup /v2/github/app/manifest answers with. get() and post() are
// untyped JS, so these shapes are declared here, not checked.
interface GithubWaitingBot { bot?: string; repository?: string; create_url?: string | null; }
interface GithubAppState {
  connected?: boolean; org?: string; slug?: string; installed?: boolean; administration?: boolean; account_type?: string;
  waiting?: GithubWaitingBot[]; permissions_url?: string | null; install_url?: string | null; uninstall_url?: string | null;
  /** Why no check is posted on pull requests (backend/github_app.py CHECKS_WITHHELD), or null when Checks is accepted. */
  checks_withheld?: string | null;
  /** Personal accounts: whether the owner has authorized Tico to create repositories in the account. */
  authorized?: boolean;
  /** The callback URL the App needs as its Callback URL, or '' when its manifest already names it. */
  callback_url?: string;
  /** Bots' own repositories, which Tico makes by itself (backend/bot_repo_maker.py): `problem` is why the rest wait. */
  bot_repositories?: {made: number; taken: number; waiting: number; problem: string};
}
interface GithubRepoRow { full_name: string; enabled?: boolean | number; bot_repo?: boolean | number; archived?: boolean | number | null; reachable?: boolean | number; }
// What GitHub's return says in one sentence (backend/github_app.py `stale_return`); the other returns need no words.
const GITHUB_RETURNS: Record<string, string> = {
  again: 'GitHub is already connected, so nothing changed. If GitHub made a second app just now, delete it there.',
  expired: 'That GitHub link is used or too old. Connect again.',
};
interface GithubManifestSetup { action: string; manifest: unknown; }
interface GithubAuthorizeLink { url?: string; }
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
    const personal = state.account_type === 'User';
    const waiting = (state.waiting || []).length ? `<p class="muted">Waiting for a repository: ${(state.waiting || []).map((w, i) => `${text(w.bot)} (<a data-gh-create="${i}" target="_blank" rel="noopener">create ${text(w.repository)} yourself</a>)`).join(', ')}.</p>` : '';
    // A personal account's empty repositories are made with the owner's authorization (once per account); until it is given,
    // bots wait. Administration is needed by either kind for the repositories Tico makes.
    const authorize = personal && !state.authorized ? `<div data-gh-authorize>
        <p>Let Tico create repositories.</p>
        <p><button type="button" class="primary" data-gh-authorize-link>Authorize</button></p>
        ${state.callback_url ? `<p class="muted">First add <code>${text(state.callback_url)}</code> as the Callback URL of the app on GitHub.</p>` : ''}
      </div>` : '';
    const ready = state.administration && (!personal || state.authorized) ? `<p class="muted">Tico can create bot repositories${personal ? ` on ${text(state.org)}` : ''}.</p>` : '';
    const fix = state.administration ? '' : `<div data-gh-repo-fix>
        <p><strong>Tico can't create bot repositories.</strong> The app was set up without Administration, so every new bot waits for a person.</p>
        <p>Fix it once: <a class="primary" role="button" data-gh-permissions target="_blank" rel="noopener">Let Tico create repositories</a>
        On that page set <strong>Administration</strong> to <strong>Read and write</strong> and save, accept the new permission for ${text(state.org)} when GitHub asks, then
        <button type="button" class="ghost" data-gh-recheck>Check again</button></p>
      </div>`;
    // Tico makes each bot's repository by itself: one line while some are still to come.
    const bots = state.bot_repositories;
    const making = bots && bots.waiting && state.administration && state.installed
      ? `<p class="muted" data-gh-bot-repos>Making bot repositories: ${bots.made} of ${bots.made + bots.waiting}.${bots.problem ? ` ${text(bots.problem)}` : ''}</p>` : '';
    // Installed, and no repository In use yet: the team's repository is chosen here, one tap, so Engineering never
    // waits on it later. Nothing is ticked for the person. A lone product repository comes first.
    let choose = '', shown: GithubRepoRow[] = [];
    if (state.installed) {
      let listed: GithubRepoRow[] = [];
      try { listed = ((await get('/v2/repositories')) as {repositories?: GithubRepoRow[]}).repositories || []; } catch { listed = []; }
      if (!host.isConnected) return;
      const product = listed.filter(r => !r.bot_repo && !r.archived && r.reachable !== false && r.reachable !== 0);
      if (product.length && !product.some(r => r.enabled)) {
        shown = product.slice(0, 8);
        choose = `<div data-gh-choose><p><strong>Which repository does your team work on?</strong></p>
          ${shown.map((r, i) => `<div class="row"><span>${text(r.full_name)}</span> <button type="button" class="${product.length === 1 ? 'primary' : 'ghost'}" data-gh-use="${i}">Use</button></div>`).join('')}
          ${product.length > shown.length ? `<p><a href="#/settings/general">All ${product.length} repositories</a></p>` : ''}</div>`;
      }
    }
    const repos = `${choose}${ready}${making}${authorize}${fix}${waiting}`;
    host.innerHTML = `<p>Connected to <strong>${text(state.org)}</strong> through the app <strong>${text(state.slug)}</strong>.
      <span data-gh-install>${state.installed ? `Installed on the ${personal ? 'account' : 'organization'}.` : 'Not installed yet.'}</span></p>
      ${repos}
      ${state.checks_withheld ? `<p class="muted" data-gh-checks-withheld>${text(state.checks_withheld)}</p>` : ''}
      <p class="muted">Connected before release tracking? In <a data-gh-events target="_blank" rel="noopener">the app's events on GitHub</a>, tick <strong>Release</strong>. Pushed tags work without it.</p>
      ${state.installed ? '' : `<p class="muted">Choose <strong>All repositories</strong> when GitHub asks. Each bot's token still covers only its own repository.</p>`}
      <div class="row">${state.installed ? '' : `<a class="primary" role="button" data-gh-install-link target="_blank" rel="noopener">Install on ${text(state.org)}</a>`}
      <button type="button" class="ghost" data-gh-disconnect>Disconnect</button>
      <a data-gh-uninstall-link target="_blank" rel="noopener">Uninstall on GitHub</a></div>
      <p class="muted">Disconnect forgets the app here; delete it on GitHub to revoke access.</p>
      <p role="status" data-gh-status></p>`;
    // The server's links are set as properties, and only when they point at GitHub over https or at Tico's install route.
    const link = (node: HTMLAnchorElement | null, url: string | null | undefined) => { if (node && url && (url === '/api/v2/github/app/install' || /^https:\/\/github\.com\//.test(url))) node.href = url; };
    ghEl<HTMLElement>(host, '[data-gh-status]').textContent = GITHUB_RETURNS[(/[?&]github=([a-z]+)/.exec(location.hash) || [])[1] || ''] || '';
    host.querySelectorAll<HTMLButtonElement>('[data-gh-use]').forEach(button => {
      button.onclick = async () => {
        button.disabled = true;
        const name = shown[Number(button.dataset.ghUse)]!.full_name;
        try { await put('/v2/repositories/' + name.split('/').map(encodeURIComponent).join('/'), {enabled: true}); window.mountGithubConnect(host); }
        catch (error) { ghEl<HTMLElement>(host, '[data-gh-status]').textContent = errorMessage(error, ''); button.disabled = false; }
      };
    });
    link(host.querySelector<HTMLAnchorElement>('[data-gh-permissions]'), state.permissions_url);
    link(host.querySelector<HTMLAnchorElement>('[data-gh-events]'), state.permissions_url);
    link(host.querySelector<HTMLAnchorElement>('[data-gh-install-link]'), state.install_url);
    link(host.querySelector<HTMLAnchorElement>('[data-gh-uninstall-link]'), state.uninstall_url);
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
    const authorizeLink = host.querySelector<HTMLButtonElement>('[data-gh-authorize-link]');
    if (authorizeLink) authorizeLink.onclick = async () => {
      authorizeLink.disabled = true;
      const status = ghEl<HTMLElement>(host, '[data-gh-status]');
      try {
        // The server names GitHub's authorize page; the owner goes there and GitHub sends them back to Tico.
        const started: GithubAuthorizeLink = await get('/v2/github/app/authorize-link');
        const url = started.url || '';
        if (!/^https:\/\/github\.com\//.test(url)) throw new Error('GitHub did not give an authorize page');
        window.location.assign(url);
      } catch (error) { status.textContent = errorMessage(error, ''); authorizeLink.disabled = false; }
    };
    const disconnect = ghEl<HTMLButtonElement>(host, '[data-gh-disconnect]');
    disconnect.onclick = async () => {
      if (!(await askConfirm('Forget the GitHub app?', disconnect))) return;
      disconnect.disabled = true;
      try { await post('/v2/github/app/disconnect', {}); window.mountGithubConnect(host); }
      catch (error) { ghEl<HTMLElement>(host, '[data-gh-status]').textContent = errorMessage(error, ''); disconnect.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form data-gh-form style="display:grid;gap:10px;max-width:460px">
      <p class="muted">When GitHub asks where to install, choose <strong>All repositories</strong>. Each bot's token still covers only its own repository. For sensitive code, use a separate account or organization.</p>
      <label>GitHub account or organization<input name="org" type="text" required maxlength="39" autocomplete="off" spellcheck="false" pattern="[A-Za-z0-9][A-Za-z0-9-]*" placeholder="your-name or your-org"></label>
      <label>App name<input name="name" type="text" maxlength="34" autocomplete="off" placeholder="Acme Tico"></label>
      <label><input type="checkbox" name="administration" checked> Let Tico create bot repositories (recommended; without it a person creates each new bot's repository)</label>
      <button class="primary" type="submit">Connect GitHub</button>
      <p role="status" data-gh-status></p></form>`;
  const form = ghEl<HTMLFormElement>(host, '[data-gh-form]');
  ghEl<HTMLElement>(host, '[data-gh-status]').textContent = GITHUB_RETURNS[(/[?&]github=([a-z]+)/.exec(location.hash) || [])[1] || ''] || '';
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
        if (errorCode(error) !== 'github_key_missing' || !(await askConfirm(LOST_KEY_CONFIRM))) throw error;
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
