/* ui/app/settings-repositories.js — Settings > Repositories, and a bot's Repositories in its settings
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Repositories
// The team's repositories from its GitHub App (backend: /api/v2/repositories). The owner or an admin adds the ones the
// team works on to In use (the stored `enabled` tick); bots reach them through their own access below. Everyone else
// sees the same lists, read only. An organization of hundreds stays short: an In use row opens in place for its setup
// command, and Not in use is one line until it is searched or opened.
let REPOS = null;                 // the last GET: {repositories, new_bot_default, github_connected}
let REPOS_SHOW_BOTS = false;      // bot repositories stay out of Not in use until asked for
let REPOS_SHOW_ARCHIVED = false;  // archived ones too
let REPOS_OFF_OPEN = false;       // Not in use opened without a search
const REPOS_OPEN = new Set();     // In use rows opened to their setup command
const REPOS_OFF_MAX = 100;        // Not in use draws this many rows at most; a search narrows the rest
const REPO_SOURCES = ['tico.json', 'conductor.json'];
const REPO_NEW_BOT = [['own', 'Their own repo'], ['all', 'All repos in use']];
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

// The bots whose own repository this is, from the roster already loaded; '' when none is known.
function repoUsedBy(r) {
  if (!r.bot_repo) return '';
  const short = String(r.full_name).split('/').pop().toLowerCase();
  return (S.emps || []).filter(e => String(e.repo || (e.slug ? `bot-${e.slug}` : '')).replace(/\.git$/i, '').split('/').pop().toLowerCase() === short)
    .map(e => e.display_name || e.slug || e.id).join(', ');
}
const repoChipsHTML = r => `${r.bot_repo ? '<span class="repo-tag">bot</span>' : ''}${r.archived ? '<span class="repo-tag">archived</span>' : ''}${
  r.reachable === false ? '<span class="repo-tag repo-warn" title="GitHub no longer lists it">Not reachable</span>' : ''}`;
const repoSrcHTML = r => REPO_SOURCES.includes(r.setup_source) ? `<small class="repo-src" title="Read from ${esc(r.setup_source)}">${esc(r.setup_source)}</small>` : '';
const repoLineHTML = r => `<span class="repo-nm">${repoNameHTML(r.full_name)}</span>${repoChipsHTML(r)}<span class="repo-branch">${esc(r.default_branch || '')}</span>`;

// In use: one line; a tap opens the setup command, who uses it, and Remove. Only open rows draw an input.
function reposOnRowHTML(r, admin, i) {
  const open = REPOS_OPEN.has(r.full_name), id = `repo-more-${i}`, used = repoUsedBy(r);
  return `<li class="repo-row on${open ? ' open' : ''}" data-repo="${esc(r.full_name)}">
    <button type="button" class="repo-line" data-repo-open aria-expanded="${open}" aria-controls="${id}" title="${esc(r.full_name)}">${repoLineHTML(r)}<span class="nav-icon repo-chev" aria-hidden="true">chevron_right</span></button>
    ${open ? `<div class="repo-more" id="${id}">
      <span class="repo-setup"><input type="text" data-repo-setup value="${esc(r.setup_command || '')}" placeholder="${admin ? 'Setup command' : 'No setup command'}" aria-label="Setup command for ${esc(r.full_name)}" spellcheck="false" autocomplete="off" maxlength="2000"${admin ? '' : ` readonly title="${REPO_LOCKED}"`}>${repoSrcHTML(r)}</span>
      ${used ? `<span class="repo-used">Used by ${esc(used)}</span>` : ''}
      ${admin ? '<button type="button" class="ghost repos-small" data-repo-remove>Remove</button>' : ''}</div>` : ''}</li>`;
}
// Not in use: one line and Add.
const reposOffRowHTML = (r, admin) => `<li class="repo-row" data-repo="${esc(r.full_name)}">
    <span class="repo-line" title="${esc(r.full_name)}">${repoLineHTML(r)}</span>${admin ? '<button type="button" class="ghost repos-small" data-repo-add>Add</button>' : ''}</li>`;

function reposDrawList() {
  const host = $('#set-repos'), onList = host?.querySelector('[data-repos-on]');
  if (!onList || !REPOS) return;
  const admin = settingsIsAdmin(), byName = (a, b) => a.full_name.localeCompare(b.full_name);
  const all = REPOS.repositories || [];
  const on = all.filter(r => r.enabled).sort(byName);
  const off = all.filter(r => !r.enabled && (REPOS_SHOW_ARCHIVED || !r.archived) && (REPOS_SHOW_BOTS || !r.bot_repo)).sort(byName);
  for (const name of [...REPOS_OPEN]) if (!on.some(r => r.full_name === name)) REPOS_OPEN.delete(name);
  host.querySelector('[data-repos-on-n]').textContent = on.length;
  onList.innerHTML = on.length ? on.map((r, i) => reposOnRowHTML(r, admin, i)).join('') : '<li class="empty">None.</li>';
  const q = (host.querySelector('[data-repos-filter]')?.value || '').trim().toLowerCase(), show = REPOS_OFF_OPEN || !!q;
  host.querySelector('[data-repos-off-n]').textContent = off.length;
  host.querySelector('[data-repos-off-toggle]').setAttribute('aria-expanded', String(show));
  host.querySelector('[data-repos-off-body]').hidden = !show;
  const offList = host.querySelector('[data-repos-off]');
  if (!show) { offList.innerHTML = ''; return; }
  const rows = off.filter(r => !q || r.full_name.toLowerCase().includes(q)), more = rows.length - REPOS_OFF_MAX;
  offList.innerHTML = rows.length ? rows.slice(0, REPOS_OFF_MAX).map(r => reposOffRowHTML(r, admin)).join('')
    + (more > 0 ? `<li class="empty">${more} more · search to narrow</li>` : '')
    : `<li class="empty">${q ? 'No match.' : 'None.'}</li>`;
}
async function renderSettingsRepos(fresh) {
  const host = $('#set-repos');
  if (!host) return;
  try { REPOS = fresh || await get('/v2/repositories'); }
  catch (error) { host.innerHTML = `<div class="err">${esc(error.message)}</div>`; return; }
  if (!$('#set-repos')) return;
  const admin = settingsIsAdmin(), dis = admin ? '' : ` disabled title="${REPO_LOCKED}"`, owner = S.me?.role === 'owner';
  if (!REPOS.github_connected) {
    host.innerHTML = `<header><h2>Repositories</h2></header><p class="repos-line">The GitHub App is not connected; bots use their computers' own GitHub sign-in.${owner ? ` <a href="#/tools/github" data-repos-connect>Connect GitHub</a>`
      : ` <span data-repos-ask>Ask ${esc(settingsOwnerName() || 'the owner')} to connect GitHub.</span>`}</p>`;
    return;
  }
  const bots = (REPOS.repositories || []).some(r => r.bot_repo && !r.enabled);
  const archived = (REPOS.repositories || []).some(r => r.archived && !r.enabled);
  const formOpen = !!host.querySelector('[data-product-repo]:not([hidden])');
  PRODUCT_REPO_PREVIEW = null;
  host.innerHTML = `<header><h2>Repositories</h2>${helpBubble('Read from GitHub daily and on Refresh. A setup command saved here wins over tico.json and conductor.json.')}<span class="spacer"></span>
      <button class="ghost repos-small" type="button" data-repos-refresh${dis}>Refresh</button></header>
    ${admin ? '' : settingsWhoCanHTML('which repositories bots use', {admins: true})}
    <div class="repos-bar"><label class="repos-newbot"><span class="repos-k">New bots get</span>
      <select name="repos_new_bot" data-repos-newbot${dis}>${REPO_NEW_BOT.map(([v, text]) =>
        `<option value="${v}"${v === (REPOS.new_bot_default || 'own') ? ' selected' : ''}>${esc(text)}</option>`).join('')}</select></label>
      ${owner ? `<span class="spacer"></span><button class="ghost repos-small" type="button" data-product-repo-open aria-expanded="${formOpen}" aria-controls="product-repo-form">New repository</button>` : ''}</div>
    ${owner ? `<section class="repos-new" id="product-repo-form" data-product-repo${formOpen ? '' : ' hidden'}>
      <div class="repos-new-row"><input id="product-repo-name" data-product-repo-name maxlength="100" autocomplete="off" spellcheck="false" placeholder="Repository name" aria-label="Repository name">
      <button class="ghost repos-small" type="button" data-product-repo-preview>Preview</button></div>
      <div class="repos-line" data-product-repo-result role="status" aria-live="polite" hidden></div>
      <button type="button" class="repos-small" data-product-repo-create hidden disabled>Create repository</button>
    </section>` : ''}
    <h3 class="repos-h">In use <span class="repos-n" data-repos-on-n></span></h3>
    <ul class="repos-list" data-repos-on aria-label="In use"></ul>
    <div class="repos-off-head"><button type="button" class="repos-h repos-toggle" data-repos-off-toggle aria-expanded="false" aria-controls="repos-off-body"><span class="nav-icon repo-chev" aria-hidden="true">chevron_right</span>Not in use <span class="repos-n" data-repos-off-n></span></button>
      <input type="search" class="repos-filter" data-repos-filter placeholder="Search" aria-label="Search repositories not in use" autocomplete="off"></div>
    <div id="repos-off-body" data-repos-off-body hidden>
      ${bots || archived ? `<div class="repos-off-opts">${bots ? `<label class="repos-bots"><input type="checkbox" data-repos-bots${REPOS_SHOW_BOTS ? ' checked' : ''}>Bot repos</label>` : ''}
        ${archived ? `<label class="repos-bots"><input type="checkbox" data-repos-archived${REPOS_SHOW_ARCHIVED ? ' checked' : ''}>Archived</label>` : ''}</div>` : ''}
      <ul class="repos-list" data-repos-off aria-label="Not in use"></ul></div>`;
  reposDrawList();
}
// A setup command saved: patched in place from what the server saved, so the cursor stays wherever the person moved it.
function reposPatchRow(r) {
  const input = $(`#set-repos [data-repo="${CSS.escape(r.full_name)}"] [data-repo-setup]`);
  if (!input) return;
  input.defaultValue = r.setup_command || '';
  if (input !== document.activeElement) input.value = r.setup_command || '';
  input.parentElement.querySelector('.repo-src')?.remove();
  input.insertAdjacentHTML('afterend', repoSrcHTML(r));
}
// Add or Remove moves the row to the other list; the keyboard goes to the row that took its place.
function reposMoved(list, index) {
  const host = $('#set-repos');
  if (!host || document.activeElement && document.activeElement !== document.body) return;   // the person has moved on
  const rows = host.querySelectorAll(`[${list}] > .repo-row`);
  const row = rows[Math.min(index, rows.length - 1)];
  (row?.querySelector(list === 'data-repos-on' ? '[data-repo-open]' : '[data-repo-add]') || host.querySelector('[data-repos-filter]'))?.focus();
}
async function reposSave(name, body, control) {
  const row = REPOS?.repositories?.find(r => r.full_name === name);
  if (!row) return;
  const tick = body.enabled !== undefined;
  const list = control.closest('[data-repos-on]') ? 'data-repos-on' : 'data-repos-off';
  const index = [...(control.closest(`[${list}]`)?.children || [])].indexOf(control.closest('li'));
  if (tick) control.disabled = true;
  settingsFieldClear(control);
  try {
    const saved = await put(`/v2/repositories/${repoPath(name)}`, body);
    Object.assign(row, saved && saved.full_name ? saved : body,
      body.setup_command !== undefined && !(saved && saved.full_name) ? {setup_source: body.setup_command ? 'settings' : null} : {});
    toast('Saved');
  } catch (error) {
    if (tick) control.disabled = false;
    settingsFieldError(control, error.message);
    if (!tick) reposPatchRow(row);
    return;
  }
  if (!tick) { reposPatchRow(row); return; }
  if (!row.enabled) REPOS_OPEN.delete(name);
  reposDrawList();
  reposMoved(list, index);
}
document.addEventListener('change', async event => {
  const host = event.target.closest('#set-repos');
  if (!host) return;
  const t = event.target, name = t.closest('[data-repo]')?.dataset.repo;
  if (t.matches('[data-repos-archived]')) { REPOS_SHOW_ARCHIVED = t.checked; reposDrawList(); return; }
  if (t.matches('[data-repos-bots]')) { REPOS_SHOW_BOTS = t.checked; reposDrawList(); return; }
  if (!settingsIsAdmin()) return;
  if (t.matches('[data-repo-setup]') && name) {
    const row = REPOS.repositories.find(r => r.full_name === name);
    if ((row?.setup_command || '') !== t.value.trim()) return reposSave(name, {setup_command: t.value.trim()}, t);
  }
  if (t.name === 'repos_new_bot') {
    const before = REPOS.new_bot_default || 'own';
    settingsFieldClear(t);
    try { await put('/v2/repositories/settings', {new_bot_default: t.value}); REPOS.new_bot_default = t.value; toast('Saved'); }
    catch (error) { settingsFieldError(t, error.message); t.value = before; }
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
  const filter = event.target.closest?.('#set-repos [data-repos-filter]');
  if (filter && event.key === 'Escape' && filter.value) { event.preventDefault(); filter.value = ''; reposDrawList(); return; }
  const input = event.target.closest?.('#set-repos [data-repo-setup]');
  if (!input) return;
  if (event.key === 'Enter') { event.preventDefault(); input.blur(); }
  if (event.key === 'Escape') {
    const row = REPOS?.repositories?.find(r => r.full_name === input.closest('[data-repo]').dataset.repo);
    input.value = row?.setup_command || ''; input.blur();
  }
});
document.addEventListener('click', async event => {
  const host = event.target.closest('#set-repos');
  if (!host) return;
  const rowButton = event.target.closest('[data-repo-open]');
  if (rowButton) {
    const name = rowButton.closest('[data-repo]').dataset.repo;
    if (REPOS_OPEN.has(name)) REPOS_OPEN.delete(name); else REPOS_OPEN.add(name);
    reposDrawList();
    host.querySelector(`[data-repo="${CSS.escape(name)}"] ${REPOS_OPEN.has(name) && settingsIsAdmin() ? '[data-repo-setup]' : '[data-repo-open]'}`)?.focus();
    return;
  }
  const add = event.target.closest('[data-repo-add], [data-repo-remove]');
  if (add) {
    if (!settingsIsAdmin()) return;
    return reposSave(add.closest('[data-repo]').dataset.repo, {enabled: add.matches('[data-repo-add]')}, add);
  }
  if (event.target.closest('[data-repos-off-toggle]')) {
    const filter = host.querySelector('[data-repos-filter]');
    const shown = REPOS_OFF_OPEN || !!filter?.value.trim();
    REPOS_OFF_OPEN = !shown;
    if (shown && filter) filter.value = '';
    reposDrawList();
    return;
  }
  const openForm = event.target.closest('[data-product-repo-open]');
  if (openForm) {
    const section = host.querySelector('[data-product-repo]');
    if (!section) return;
    section.hidden = !section.hidden;
    openForm.setAttribute('aria-expanded', String(!section.hidden));
    if (!section.hidden) section.querySelector('[data-product-repo-name]').focus();
    return;
  }
  const previewButton = event.target.closest('[data-product-repo-preview]');
  if (previewButton) {
    if (S.me?.role !== 'owner') return;
    const section = previewButton.closest('[data-product-repo]');
    const result = section.querySelector('[data-product-repo-result]');
    const create = section.querySelector('[data-product-repo-create]');
    const name = section.querySelector('[data-product-repo-name]').value.trim();
    previewButton.disabled = true;
    result.hidden = false; result.textContent = 'Checking…';
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
  const createButton = event.target.closest('[data-product-repo-create]');
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
      toast(`Created ${created.repository}`);
    } catch (error) {
      resultHost.hidden = false; resultHost.textContent = error.message;
      createButton.disabled = false; createButton.textContent = 'Create repository';
    }
    return;
  }
  const button = event.target.closest('[data-repos-refresh]');
  if (!button) return;
  button.disabled = true; button.textContent = 'Refreshing…';
  try { await renderSettingsRepos(await post('/v2/repositories/refresh', {})); toast('Repositories refreshed'); }
  catch (error) { toast(error.message, true); button.disabled = false; button.textContent = 'Refresh'; }
});

// ----------------------------------------------------------------- a bot's Repositories
// In the bot editor: Own repo only, All ticked repos (read or write), or Chosen repos with read or write each; a
// chosen write repo may also let the bot run workflows there (GitHub Actions: write) and edit workflow files
// (GitHub Workflows: write). All ticked repos lists its repositories with the same two options each, kept apart from
// the chosen repos' own; with Read they show greyed with the reason, never hidden.
// Each change saves on its own, like the other rows there. Owners and admins change it; the bot's other
// managers see it read only. Create product repositories is on for every bot until the Owner or an admin turns it
// off; only the Owner turns it back on.
// Hidden while GitHub is not connected.
const BOT_REPO_MODES = [['own', 'Own repo only'], ['all', 'All ticked repos'], ['chosen', 'Chosen repos']];
const BOT_REPO_ACCESS = [['read', 'Read'], ['write', 'Write']];
// A chosen write repo's options: [grant flag, data attribute, label, tooltip, what Effective adds].
const BOT_REPO_FLAGS = [
  ['run_workflows', 'data-brepo-run', 'Run workflows', 'Start, rerun and cancel workflow runs (GitHub Actions: write)', ' + workflows'],
  ['edit_workflows', 'data-brepo-edit', 'Edit workflow files', 'Create and change files in .github/workflows (GitHub Workflows: write). A workflow pushed on a branch runs with the repository\'s secrets before review', ' + workflow edits']];
const brepoFlagSets = chosen => Object.fromEntries(BOT_REPO_FLAGS.map(([flag]) => [flag, new Set((chosen || []).filter(c => c[flag]).map(c => c.full_name))]));
// The selector of a row's checkbox, to put the keyboard back on it after a redraw.
const brepoControl = el => `[${BOT_REPO_FLAGS.map(([, attr]) => attr).find(attr => el.matches(`[${attr}]`)) || 'data-brepo-pick'}]`;
// A row's two options: checked when on, greyed with `why` (said in the tooltip) when they cannot change.
const brepoFlagsHTML = (name, flags, on, why) => BOT_REPO_FLAGS.map(([flag, attr, label, title]) =>
  `<label class="brepo-run" title="${esc(why ? `${title}. ${why}` : title)}"><input type="checkbox" ${attr} aria-label="${esc(label)} in ${esc(name)}"${on && flags[flag].has(name) ? ' checked' : ''}${why ? ' disabled' : ''}>${esc(label)}</label>`).join('');
async function botReposMount(host, slug) {
  if (!host) return;
  let view, team;
  try {
    [view, team] = await Promise.all([get(`/v2/bots/${encodeURIComponent(slug)}/repositories`), get('/v2/repositories')]);
  } catch { return; }      // an older server, or no rights to read it: the section just stays away
  if (!host.isConnected || !team.github_connected) return;
  const admin = settingsIsAdmin(), owner = S.me?.role === 'owner';
  const state = {mode: view.mode || 'own', all_access: view.all_access || 'write', create_repositories: !!view.create_repositories,
    create_product_repositories: !!view.create_product_repositories,
    chosen: new Map((view.chosen || []).map(c => [c.full_name, c.access === 'read' ? 'read' : 'write'])),
    flags: brepoFlagSets(view.chosen), served: brepoFlagSets(view.chosen),
    all_flags: brepoFlagSets(view.all_repos), all_served: brepoFlagSets(view.all_repos),
    effective: view.effective || []};
  // The ticked repositories, less bot repositories, plus anything already chosen that has since left that list.
  const pickable = () => {
    const names = (team.repositories || []).filter(r => r.enabled && !r.bot_repo).map(r => r.full_name);
    for (const name of state.chosen.keys()) if (!names.some(n => n.toLowerCase() === name.toLowerCase())) names.push(name);
    return names.sort((a, b) => a.localeCompare(b));
  };
  // What All ticked repos reaches: the ticked repositories, less bot repositories, a bot- name counting as one, as the
  // server reads it (backend all_ticked).
  const ticked = () => (team.repositories || []).filter(r => r.enabled && !r.bot_repo && !/^bot-/i.test(r.full_name.split('/')[1] || ''))
    .map(r => r.full_name).sort((a, b) => a.localeCompare(b));
  // Why All ticked repos' options cannot change, said beside them; '' when they can.
  const allWhy = () => !admin ? 'Only an Owner or admin changes this' : state.all_access !== 'write' ? 'Needs Write for all ticked repos' : '';
  const id = `brepo-${slug}`;
  const paint = () => {
    const pick = pickable();
    host.innerHTML = `<legend class="brepo-h">Repositories</legend><span class="muted brepo-status" data-brepo-status role="status"></span>
      <label class="repo-name"><input type="checkbox" data-brepo-create${state.create_repositories ? ' checked' : ''}${!admin || slug === 'botops' ? ' disabled' : ''}>Create bot repositories</label>
      <p class="muted">Private bot repositories in the connected organization. GitHub App permission is also required. Otherwise, ask your Assistant.</p>
      <label class="repo-name"><input type="checkbox" data-brepo-product${state.create_product_repositories ? ' checked' : ''}${owner || admin && state.create_product_repositories ? '' : ' disabled'}>Create product repositories</label>
      <p class="muted">Private repositories with any other name in the connected organization; the bot gets write to each one it makes and you are told. On for every bot unless switched off; only the Owner switches it back on.</p>
      ${repoSegHTML(`${id}-mode`, 'Repository access', BOT_REPO_MODES, state.mode, !admin, ' brepo-mode')}
      ${state.mode === 'all' ? `<div class="brepo-all">${repoSegHTML(`${id}-all`, 'Access to all ticked repos', BOT_REPO_ACCESS, state.all_access, !admin, ' repo-seg-sm')}</div>
      ${allWhy() && admin ? `<p class="muted brepo-why" data-brepo-why>Run workflows and Edit workflow files need Write. Choose Write to tick them for a repository.</p>` : ''}
      <ul class="brepo-list" aria-label="All ticked repos">${ticked().length ? ticked().map(name =>
        `<li class="brepo-row" data-brepo-all="${esc(name)}"><span class="repo-name" title="${esc(name)}"><span>${repoNameHTML(name)}</span></span>
          ${brepoFlagsHTML(name, state.all_flags, state.all_access === 'write', allWhy())}</li>`).join('') : '<li class="empty">No ticked repos.</li>'}</ul>` : ''}
      ${state.mode === 'chosen' ? `<ul class="brepo-list" aria-label="Chosen repos">${pick.length ? pick.map(name => {
        const on = state.chosen.has(name);
        return `<li class="brepo-row" data-brepo="${esc(name)}"><label class="repo-name" title="${esc(name)}"><input type="checkbox" data-brepo-pick${on ? ' checked' : ''}${admin ? '' : ' disabled'}><span>${repoNameHTML(name)}</span></label>
          ${repoSegHTML(`${id}-r-${name}`, `Access to ${name}`, BOT_REPO_ACCESS, state.chosen.get(name) || 'write', !admin || !on, ' repo-seg-sm')}
          ${brepoFlagsHTML(name, state.flags, on && state.chosen.get(name) === 'write', !admin ? 'Only an Owner or admin changes this' : !on ? 'Tick the repo first' : state.chosen.get(name) !== 'write' ? 'Needs Write' : '')}</li>`;
      }).join('') : '<li class="empty">No ticked repos.</li>'}</ul>` : ''}
      ${state.effective.length ? `<div class="brepo-eff"><span class="brepo-k">Effective</span><span>${state.effective.map(r =>
        `<span class="brepo-eff-item">${esc(r.full_name)} <span class="brepo-acc">${esc(r.access || 'write')}${BOT_REPO_FLAGS.map(([flag, , , , more]) => r[flag] ? more : '').join('')}</span></span>`).join('')}</span></div>` : ''}`;
    host.hidden = false;
  };
  // What the server holds, drawn as it is: after the latest save, and after a refused one.
  const adopt = view => {
    state.mode = view.mode || 'own'; state.all_access = view.all_access || 'write'; state.create_repositories = !!view.create_repositories;
    state.create_product_repositories = !!view.create_product_repositories;
    state.chosen = new Map((view.chosen || []).map(c => [c.full_name, c.access === 'read' ? 'read' : 'write']));
    state.flags = brepoFlagSets(view.chosen);
    state.served = brepoFlagSets(view.chosen);
    state.all_flags = brepoFlagSets(view.all_repos);
    state.all_served = brepoFlagSets(view.all_repos);
    state.effective = view.effective || [];
  };
  // All ticked repos' own ticks for a save, on the same terms as a chosen repo's: only those on or being turned off.
  // One since unticked in Settings is left as the server holds it.
  const allTicks = () => {
    const names = [...new Set(BOT_REPO_FLAGS.flatMap(([flag]) => [...state.all_flags[flag], ...state.all_served[flag]]))]
      .filter(name => ticked().includes(name)).sort();
    return names.length ? {all_repos: names.map(full_name => ({full_name, ...Object.fromEntries(BOT_REPO_FLAGS
      .filter(([flag]) => state.all_flags[flag].has(full_name) || state.all_served[flag].has(full_name))
      .map(([flag]) => [flag, state.all_flags[flag].has(full_name)]))}))} : {};
  };
  const say = html => { const el = host.querySelector('[data-brepo-status]'); if (el) el.innerHTML = html; };
  // Redraw, keeping the keyboard on the control it was on.
  const repaint = () => {
    const at = document.activeElement, keep = host.contains(at) ? (at.type === 'radio'
      ? `input[name="${CSS.escape(at.name)}"][value="${CSS.escape(at.value)}"]`
      : at.matches('[data-brepo-create]') ? '[data-brepo-create]' : at.matches('[data-brepo-product]') ? '[data-brepo-product]' : at.closest('[data-brepo]') ? `[data-brepo="${CSS.escape(at.closest('[data-brepo]').dataset.brepo)}"] ${brepoControl(at)}`
      : at.closest('[data-brepo-all]') ? `[data-brepo-all="${CSS.escape(at.closest('[data-brepo-all]').dataset.brepoAll)}"] ${brepoControl(at)}` : '') : '';
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
          create_product_repositories: state.create_product_repositories,
          // Run workflows and Edit workflow files go only when on or being turned off; left out, the server keeps
          // what it holds.
          chosen: [...state.chosen].map(([full_name, access]) => ({full_name, access, ...(access !== 'write' ? {} : Object.fromEntries(
            BOT_REPO_FLAGS.filter(([flag]) => state.flags[flag].has(full_name) || state.served[flag].has(full_name))
              .map(([flag]) => [flag, state.flags[flag].has(full_name)])))})),
          ...allTicks()};
        // An option this save turns on may be on once it lands, so an untick made meanwhile goes as an explicit off:
        // a later save that left it out would keep it.
        for (const [flag] of BOT_REPO_FLAGS) {
          for (const c of body.chosen) if (c[flag]) state.served[flag].add(c.full_name);
          for (const r of body.all_repos || []) if (r[flag]) state.all_served[flag].add(r.full_name);
        }
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
    const t = event.target, row = t.closest('[data-brepo]')?.dataset.brepo, allRow = t.closest('[data-brepo-all]')?.dataset.brepoAll;
    const flagOf = () => BOT_REPO_FLAGS.find(([, attr]) => t.matches(`[${attr}]`))?.[0];
    if (t.matches('[data-brepo-create]')) state.create_repositories = t.checked;
    else if (t.matches('[data-brepo-product]')) state.create_product_repositories = t.checked;
    else if (t.name === `${id}-mode`) state.mode = t.value;
    else if (t.name === `${id}-all`) { state.all_access = t.value; if (t.value === 'read') for (const set of Object.values(state.all_flags)) set.clear(); }
    else if (allRow && flagOf()) { const set = state.all_flags[flagOf()]; if (t.checked) set.add(allRow); else set.delete(allRow); }
    else if (t.matches('[data-brepo-pick]') && row) { if (t.checked) state.chosen.set(row, 'write'); else { state.chosen.delete(row); for (const set of Object.values(state.flags)) set.delete(row); } }
    else if (BOT_REPO_FLAGS.some(([, attr]) => t.matches(`[${attr}]`)) && row) {
      const set = state.flags[BOT_REPO_FLAGS.find(([, attr]) => t.matches(`[${attr}]`))[0]];
      if (t.checked) set.add(row); else set.delete(row);
    }
    else if (row && t.type === 'radio') { state.chosen.set(row, t.value); if (t.value === 'read') for (const set of Object.values(state.flags)) set.delete(row); }
    else return;
    // Redraw, then put the keyboard back on the control that changed.
    const back = t.type === 'radio' ? `input[name="${CSS.escape(t.name)}"][value="${CSS.escape(t.value)}"]`
      : t.matches('[data-brepo-create]') ? '[data-brepo-create]' : t.matches('[data-brepo-product]') ? '[data-brepo-product]'
      : allRow ? `[data-brepo-all="${CSS.escape(allRow)}"] ${brepoControl(t)}` : `[data-brepo="${CSS.escape(row)}"] ${brepoControl(t)}`;
    paint(); host.querySelector(back)?.focus();
    void save();
  };
  paint();
}
