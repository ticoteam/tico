/* ui/app/settings-repositories.js — Settings > Repositories, and a bot's Repositories in its settings
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Repositories
// The team's repositories from its GitHub App (backend: /api/v2/repositories). The owner or an admin ticks the ones
// the team works on; bots reach them through their own access below. Everyone else sees the same list, read only.
let REPOS = null;                 // the last GET: {repositories, new_bot_default, github_connected}
let REPOS_SHOW_BOTS = false;      // bot repositories stay out of the list until asked for
let REPOS_SHOW_ARCHIVED = false; // archived repositories are available on request
const REPOS_FILTER_AT = 10;       // a short list reads at a glance; the filter box earns its place past this
const REPO_SOURCES = ['tico.json', 'conductor.json'];
let PRODUCT_REPO_PREVIEW = null;
const repoPath = name => String(name).split('/').map(encodeURIComponent).join('/');
const repoNameHTML = name => {
  const cut = String(name).indexOf('/');
  return cut < 0 ? esc(name) : `<span class="repo-org">${esc(name.slice(0, cut + 1))}</span>${esc(name.slice(cut + 1))}`;
};
// Two to three choices as one row of real radios: arrow keys move between them, as everywhere else.
function repoSegHTML(name, label, options, value, disabled, extra = '') {
  return `<div class="repo-seg${extra}" role="radiogroup" aria-label="${esc(label)}">${options.map(([v, text]) =>
    `<label><input type="radio" name="${esc(name)}" value="${esc(v)}"${v === value ? ' checked' : ''}${disabled ? ' disabled' : ''}><span>${esc(text)}</span></label>`).join('')}</div>`;
}
const REPO_LOCKED = 'Owners and admins change this';

function reposRowHTML(r, admin) {
  const dis = admin ? '' : ` disabled title="${REPO_LOCKED}"`;
  const src = REPO_SOURCES.includes(r.setup_source) ? `<small class="repo-src" title="Read from ${esc(r.setup_source)}">${esc(r.setup_source)}</small>` : '';
  return `<li class="repo-row${r.enabled ? ' on' : ''}" data-repo="${esc(r.full_name)}">
    <label class="repo-name" title="${esc(r.full_name)}"><input type="checkbox" data-repo-tick${r.enabled ? ' checked' : ''}${dis}><span>${repoNameHTML(r.full_name)}</span>${r.bot_repo ? '<span class="repo-tag">bot</span>' : ''}${r.archived ? '<span class="repo-tag">archived</span>' : ''}</label>
    <span class="repo-branch">${esc(r.default_branch || '')}</span>
    <span class="repo-setup"><input type="text" data-repo-setup value="${esc(r.setup_command || '')}" placeholder="${admin ? 'Setup command' : ''}" aria-label="Setup command for ${esc(r.full_name)}" spellcheck="false" autocomplete="off" maxlength="2000"${admin ? '' : ` readonly title="${REPO_LOCKED}"`}>${src}</span>
    <span class="repo-state">${r.reachable === false ? '<span class="repo-warn" title="GitHub no longer lists it">Not reachable</span>' : ''}</span></li>`;
}
function reposDrawList() {
  const host = $('#set-repos'), list = host?.querySelector('[data-repos-list]');
  if (!list || !REPOS) return;
  const admin = settingsIsAdmin();
  const q = (host.querySelector('[data-repos-filter]')?.value || '').trim().toLowerCase();
  const all = (REPOS.repositories || []).filter(r => REPOS_SHOW_ARCHIVED || !r.archived).filter(r => REPOS_SHOW_BOTS || !r.bot_repo || r.enabled)
    .sort((a, b) => a.full_name.localeCompare(b.full_name));
  const rows = all.filter(r => !q || r.full_name.toLowerCase().includes(q));
  const filter = host.querySelector('[data-repos-filter]');
  if (filter) filter.hidden = all.length <= REPOS_FILTER_AT && !q;
  list.innerHTML = rows.length ? rows.map(r => reposRowHTML(r, admin)).join('')
    : `<li class="empty">${all.length ? 'No match.' : 'No repositories.'}</li>`;
}
async function renderSettingsRepos(fresh) {
  const host = $('#set-repos');
  if (!host) return;
  try { REPOS = fresh || await get('/v2/repositories'); }
  catch (error) { host.innerHTML = `<div class="err">${esc(error.message)}</div>`; return; }
  if (!$('#set-repos')) return;
  const admin = settingsIsAdmin(), dis = admin ? '' : ` disabled title="${REPO_LOCKED}"`;
  if (!REPOS.github_connected) {
    host.innerHTML = `<header><h2>Repositories</h2></header><p class="repos-line">The GitHub App is not connected; bots use their computers' own GitHub sign-in.${S.me?.role === 'owner' ? ` <a href="${INTEGRATIONS}" data-repos-connect>Connect GitHub</a>` : ''}</p>`;
    return;
  }
  const bots = (REPOS.repositories || []).filter(r => r.bot_repo).length;
  PRODUCT_REPO_PREVIEW = null;
  host.innerHTML = `<header><h2>Repositories</h2><span class="spacer"></span>
      ${bots ? `<label class="repos-bots"><input type="checkbox" data-repos-bots${REPOS_SHOW_BOTS ? ' checked' : ''}>Show bot repos</label>` : ''}
      <label class="repos-bots"><input type="checkbox" data-repos-archived${REPOS_SHOW_ARCHIVED ? ' checked' : ''}>Show archived</label>
      <button class="ghost repos-small" type="button" data-repos-refresh${dis}>Refresh</button></header>
    <p class="repos-line">Refreshes daily and on Refresh. Archived repositories are hidden unless Show archived is selected.</p>
    <div class="repos-bar"><span class="repos-k" id="repos-newbot">New bots get</span>
      ${repoSegHTML('repos_new_bot', 'New bots get', [['own', 'Own repo only'], ['all', 'All ticked repos']], REPOS.new_bot_default || 'own', !admin)}
      <input type="search" class="repos-filter" data-repos-filter placeholder="Filter" aria-label="Filter repositories" autocomplete="off" hidden></div>
    ${S.me?.role === 'owner' ? `<section class="card" data-product-repo>
      <h3>Product repository</h3><p class="repos-line">Create an exact empty private repository in ${esc(REPOS.github_org || 'the connected organization')}. This does not grant it to any bot.</p>
      <label for="product-repo-name">Repository name</label><div class="repos-bar"><input id="product-repo-name" data-product-repo-name maxlength="100" autocomplete="off" placeholder="tico-recorder">
      <button class="ghost" type="button" data-product-repo-preview>Preview</button></div>
      <div data-product-repo-result role="status" aria-live="polite" hidden></div>
      <button type="button" data-product-repo-create hidden disabled>Create private empty repository</button>
    </section>` : ''}
    <ul class="repos-list" data-repos-list aria-label="Repositories"></ul>`;
  reposDrawList();
}
// One change at a time per row. The row is patched in place from what the server saved (or put back on a
// refusal), so the cursor stays wherever the person moved it meanwhile.
function reposPatchRow(r) {
  const li = $(`#set-repos [data-repo="${CSS.escape(r.full_name)}"]`);
  if (!li) return;
  li.classList.toggle('on', !!r.enabled);
  li.querySelector('[data-repo-tick]').checked = !!r.enabled;
  const input = li.querySelector('[data-repo-setup]');
  input.defaultValue = r.setup_command || '';
  if (input !== document.activeElement) input.value = r.setup_command || '';
  li.querySelector('.repo-src')?.remove();
  if (REPO_SOURCES.includes(r.setup_source))
    input.insertAdjacentHTML('afterend', `<small class="repo-src" title="Read from ${esc(r.setup_source)}">${esc(r.setup_source)}</small>`);
}
async function reposSave(name, body, control) {
  const row = REPOS?.repositories?.find(r => r.full_name === name);
  if (!row) return;
  const tick = control.matches('[data-repo-tick]');
  if (tick) control.disabled = true;
  try {
    const saved = await put(`/v2/repositories/${repoPath(name)}`, body);
    Object.assign(row, saved && saved.full_name ? saved : body,
      body.setup_command !== undefined && !(saved && saved.full_name) ? {setup_source: body.setup_command ? 'settings' : null} : {});
  } catch (error) { toast(error.message, true); }
  if (tick) { control.disabled = false; if (document.activeElement === document.body) control.focus(); }
  reposPatchRow(row);
}
document.addEventListener('change', async event => {
  const host = event.target.closest('#set-repos');
  if (!host) return;
  const t = event.target, name = t.closest('[data-repo]')?.dataset.repo;
  if (t.matches('[data-repos-archived]')) { REPOS_SHOW_ARCHIVED = t.checked; reposDrawList(); return; }
  if (t.matches('[data-repos-bots]')) { REPOS_SHOW_BOTS = t.checked; reposDrawList(); return; }
  if (!settingsIsAdmin()) return;
  if (t.matches('[data-repo-tick]') && name) return reposSave(name, {enabled: t.checked}, t);
  if (t.matches('[data-repo-setup]') && name) {
    const row = REPOS.repositories.find(r => r.full_name === name);
    if ((row?.setup_command || '') !== t.value.trim()) return reposSave(name, {setup_command: t.value.trim()}, t);
  }
  if (t.name === 'repos_new_bot') {
    const before = REPOS.new_bot_default || 'own';
    try { await put('/v2/repositories/settings', {new_bot_default: t.value}); REPOS.new_bot_default = t.value; }
    catch (error) {
      toast(error.message, true);
      const back = host.querySelector(`input[name=repos_new_bot][value="${before}"]`); if (back) back.checked = true;
    }
  }
});
document.addEventListener('input', event => {
  if (event.target.matches('#set-repos [data-repos-filter]')) reposDrawList();
  if (event.target.matches('#set-repos [data-product-repo-name]')) {
    PRODUCT_REPO_PREVIEW = null;
    const result = event.target.closest('[data-product-repo]')?.querySelector('[data-product-repo-result]');
    const create = event.target.closest('[data-product-repo]')?.querySelector('[data-product-repo-create]');
    if (result) { result.hidden = true; result.textContent = ''; }
    if (create) { create.hidden = true; create.disabled = true; }
  }
});
document.addEventListener('keydown', event => {
  const input = event.target.closest?.('#set-repos [data-repo-setup]');
  if (!input) return;
  if (event.key === 'Enter') { event.preventDefault(); input.blur(); }
  if (event.key === 'Escape') {
    const row = REPOS?.repositories?.find(r => r.full_name === input.closest('[data-repo]').dataset.repo);
    input.value = row?.setup_command || ''; input.blur();
  }
});
document.addEventListener('click', async event => {
  const previewButton = event.target.closest('#set-repos [data-product-repo-preview]');
  if (previewButton) {
    if (S.me?.role !== 'owner') return;
    const section = previewButton.closest('[data-product-repo]');
    const result = section.querySelector('[data-product-repo-result]');
    const create = section.querySelector('[data-product-repo-create]');
    const name = section.querySelector('[data-product-repo-name]').value.trim();
    previewButton.disabled = true;
    result.hidden = false; result.textContent = 'Checking the connected organization and current GitHub App permission…';
    try {
      const query = new URLSearchParams({name});
      PRODUCT_REPO_PREVIEW = await get(`/v2/github/product-repos/preview?${query}`);
      const p = PRODUCT_REPO_PREVIEW;
      result.textContent = `${p.repository} · private · empty (no initial commit). ${p.capability_detail}`;
      create.hidden = p.capability !== 'available';
      create.disabled = p.capability !== 'available';
    } catch (error) {
      PRODUCT_REPO_PREVIEW = null;
      result.textContent = error.message;
      create.hidden = true; create.disabled = true;
    } finally { previewButton.disabled = false; }
    return;
  }
  const createButton = event.target.closest('#set-repos [data-product-repo-create]');
  if (createButton) {
    if (S.me?.role !== 'owner') return;
    const section = createButton.closest('[data-product-repo]');
    const resultHost = section.querySelector('[data-product-repo-result]');
    const currentName = section.querySelector('[data-product-repo-name]').value.trim();
    if (!PRODUCT_REPO_PREVIEW || currentName !== PRODUCT_REPO_PREVIEW.name || PRODUCT_REPO_PREVIEW.capability !== 'available') {
      PRODUCT_REPO_PREVIEW = null;
      resultHost.hidden = false; resultHost.textContent = 'Preview this name again before creating it.';
      createButton.hidden = true; createButton.disabled = true;
      return;
    }
    createButton.disabled = true; createButton.textContent = 'Creating…';
    try {
      const p = PRODUCT_REPO_PREVIEW;
      const created = await post('/v2/github/product-repos', {org: p.org, name: p.name, visibility: p.visibility, auto_init: p.auto_init, confirmed: true});
      PRODUCT_REPO_PREVIEW = null;
      resultHost.hidden = false;
      resultHost.innerHTML = `Created <a href="${esc(created.html_url)}" target="_blank" rel="noopener noreferrer">${esc(created.repository)}</a>. ${esc(created.note)}`;
      createButton.hidden = true;
      toast(`Created ${created.repository}. ${created.note}`);
    } catch (error) {
      resultHost.hidden = false; resultHost.textContent = error.message;
      createButton.disabled = false; createButton.textContent = 'Create private empty repository';
    }
    return;
  }
  const button = event.target.closest('#set-repos [data-repos-refresh]');
  if (!button) return;
  button.disabled = true; button.textContent = 'Refreshing…';
  try { await renderSettingsRepos(await post('/v2/repositories/refresh', {})); toast('Repositories refreshed'); }
  catch (error) { toast(error.message, true); button.disabled = false; button.textContent = 'Refresh'; }
});

// ----------------------------------------------------------------- a bot's Repositories
// In the bot editor: Own repo only, All ticked repos (read or write), or Chosen repos with read or write each.
// Each change saves on its own, like the other rows there. Owners and admins change it; the bot's other
// managers see it read only. Hidden while GitHub is not connected.
const BOT_REPO_MODES = [['own', 'Own repo only'], ['all', 'All ticked repos'], ['chosen', 'Chosen repos']];
const BOT_REPO_ACCESS = [['read', 'Read'], ['write', 'Write']];
async function botReposMount(host, slug) {
  if (!host) return;
  let view, team;
  try {
    [view, team] = await Promise.all([get(`/v2/bots/${encodeURIComponent(slug)}/repositories`), get('/v2/repositories')]);
  } catch { return; }      // an older server, or no rights to read it: the section just stays away
  if (!host.isConnected || !team.github_connected) return;
  const admin = settingsIsAdmin();
  const state = {mode: view.mode || 'own', all_access: view.all_access || 'write', create_repositories: !!view.create_repositories,
    chosen: new Map((view.chosen || []).map(c => [c.full_name, c.access === 'read' ? 'read' : 'write'])),
    effective: view.effective || []};
  // The ticked repositories, less bot repositories, plus anything already chosen that has since left that list.
  const pickable = () => {
    const names = (team.repositories || []).filter(r => r.enabled && !r.bot_repo).map(r => r.full_name);
    for (const name of state.chosen.keys()) if (!names.some(n => n.toLowerCase() === name.toLowerCase())) names.push(name);
    return names.sort((a, b) => a.localeCompare(b));
  };
  const id = `brepo-${slug}`;
  const paint = () => {
    const pick = pickable();
    host.innerHTML = `<legend class="brepo-h">Repositories</legend><span class="muted brepo-status" data-brepo-status role="status"></span>
      <label class="repo-name"><input type="checkbox" data-brepo-create${state.create_repositories ? ' checked' : ''}${!admin || slug === 'botops' ? ' disabled' : ''}>Create bot repositories</label>
      <p class="muted">Private bot repositories in the connected organization. GitHub App permission is also required. Otherwise, ask BotOps.</p>
      ${repoSegHTML(`${id}-mode`, 'Repository access', BOT_REPO_MODES, state.mode, !admin, ' brepo-mode')}
      ${state.mode === 'all' ? `<div class="brepo-all">${repoSegHTML(`${id}-all`, 'Access to all ticked repos', BOT_REPO_ACCESS, state.all_access, !admin, ' repo-seg-sm')}</div>` : ''}
      ${state.mode === 'chosen' ? `<ul class="brepo-list" aria-label="Chosen repos">${pick.length ? pick.map(name => {
        const on = state.chosen.has(name);
        return `<li class="brepo-row" data-brepo="${esc(name)}"><label class="repo-name" title="${esc(name)}"><input type="checkbox" data-brepo-pick${on ? ' checked' : ''}${admin ? '' : ' disabled'}><span>${repoNameHTML(name)}</span></label>
          ${repoSegHTML(`${id}-r-${name}`, `Access to ${name}`, BOT_REPO_ACCESS, state.chosen.get(name) || 'write', !admin || !on, ' repo-seg-sm')}</li>`;
      }).join('') : '<li class="empty">No ticked repos.</li>'}</ul>` : ''}
      ${state.effective.length ? `<div class="brepo-eff"><span class="brepo-k">Effective</span><span>${state.effective.map(r =>
        `<span class="brepo-eff-item">${esc(r.full_name)} <span class="brepo-acc">${esc(r.access || 'write')}</span></span>`).join('')}</span></div>` : ''}`;
    host.hidden = false;
  };
  // What the server holds, drawn as it is: after the latest save, and after a refused one.
  const adopt = view => {
    state.mode = view.mode || 'own'; state.all_access = view.all_access || 'write'; state.create_repositories = !!view.create_repositories;
    state.chosen = new Map((view.chosen || []).map(c => [c.full_name, c.access === 'read' ? 'read' : 'write']));
    state.effective = view.effective || [];
  };
  const say = html => { const el = host.querySelector('[data-brepo-status]'); if (el) el.innerHTML = html; };
  // Redraw, keeping the keyboard on the control it was on.
  const repaint = () => {
    const at = document.activeElement, keep = host.contains(at) ? (at.type === 'radio'
      ? `input[name="${CSS.escape(at.name)}"][value="${CSS.escape(at.value)}"]`
      : at.matches('[data-brepo-create]') ? '[data-brepo-create]' : at.closest('[data-brepo]') ? `[data-brepo="${CSS.escape(at.closest('[data-brepo]').dataset.brepo)}"] [data-brepo-pick]` : '') : '';
    paint();
    if (keep) host.querySelector(keep)?.focus();
  };
  // One save at a time per bot: a change made while one is on its way waits, and only the latest choice is sent
  // after it, so an earlier save can never land last and undo it.
  let saving = false, again = false;
  const read = () => get(`/v2/bots/${encodeURIComponent(slug)}/repositories`);
  const save = async () => {
    if (saving) { again = true; say('Saving…'); return; }
    saving = true;
    try {
      let failure = null, view = null;
      for (;;) {
        again = false; failure = null; view = null;
        say('Saving…');
        const body = {mode: state.mode, all_access: state.all_access, create_repositories: state.create_repositories,
          chosen: [...state.chosen].map(([full_name, access]) => ({full_name, access}))};
        try {
          const saved = await put(`/v2/bots/${encodeURIComponent(slug)}/repositories`, body);
          view = saved?.mode ? saved : await read();
        } catch (error) {
          failure = error;
          try { view = await read(); } catch { view = null; }   // refused: back to what the server holds
        }
        // A choice made while that was on its way (or while the server's copy was read back) goes next; the copy
        // in hand is already out of date.
        if (!again) break;
      }
      if (view) adopt(view);
      repaint();
      if (!failure) say('Saved');
      else if (view) say(`<span class="err">${esc(failure.message)}</span>`);
      else say(`<span class="err">Not saved: ${esc(failure.message)}</span>`);   // the choice on screen is not what is saved
    } finally { saving = false; }
  };
  host.onchange = event => {
    if (!admin) return;
    const t = event.target, row = t.closest('[data-brepo]')?.dataset.brepo;
    if (t.matches('[data-brepo-create]')) state.create_repositories = t.checked;
    else if (t.name === `${id}-mode`) state.mode = t.value;
    else if (t.name === `${id}-all`) state.all_access = t.value;
    else if (t.matches('[data-brepo-pick]') && row) { if (t.checked) state.chosen.set(row, 'write'); else state.chosen.delete(row); }
    else if (row && t.type === 'radio') state.chosen.set(row, t.value);
    else return;
    // Redraw, then put the keyboard back on the control that changed.
    const back = t.type === 'radio' ? `input[name="${CSS.escape(t.name)}"][value="${CSS.escape(t.value)}"]`
      : t.matches('[data-brepo-create]') ? '[data-brepo-create]' : `[data-brepo="${CSS.escape(row)}"] [data-brepo-pick]`;
    paint(); host.querySelector(back)?.focus();
    void save();
  };
  paint();
}
