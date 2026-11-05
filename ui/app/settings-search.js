/* ui/app/settings-search.js — Settings search reads the same catalogue as BotOps. */
'use strict';

let SETTINGS_SEARCH_CATALOG = null;
let SETTINGS_SEARCH_REPOSITORIES = null;
let SETTINGS_SEARCH_BOT_REPOS = new Map();
let SETTINGS_SEARCH_BOUND = false;
let SETTINGS_SEARCH_REQUEST = 0;

function settingsSearchMount() {
  const root = $('#settings-search');
  if (!root) return;
  root.innerHTML = `<button class="settings-search-open" type="button" data-settings-search-open aria-label="Search settings" title="Search settings">
      <span class="nav-icon" aria-hidden="true">search</span><span>Search settings</span></button>
    <div class="settings-search-panel" data-settings-search-panel hidden>
      <label class="sr-only" for="settings-search-input">Search settings</label>
      <div class="settings-search-row"><input id="settings-search-input" type="search" autocomplete="off" spellcheck="false" placeholder="Search settings" aria-controls="settings-search-results">
        <button class="ghost" type="button" data-settings-search-close>Clear</button></div>
      <div class="settings-search-results" id="settings-search-results" role="listbox" aria-label="Settings search results" aria-live="polite"></div>
    </div>`;
  root.onclick = event => {
    if (event.target.closest('[data-settings-search-open]')) { settingsSearchOpen(); return; }
    if (event.target.closest('[data-settings-search-close]')) { settingsSearchClose(); return; }
    const result = event.target.closest('[data-settings-search-result]');
    if (result) {
      const go = event.target.closest('[data-settings-search-go]');
      if (result.dataset.disabled === 'true' && !go) { event.preventDefault(); return; }
      if (result.dataset.repo) {
        try { sessionStorage.setItem('tico.settings.search.repo-target', JSON.stringify({repo: result.dataset.repo, target: result.dataset.target || ''})); }
        catch { /* the destination still opens if storage is unavailable */ }
      } else if (result.dataset.bot) {
        try { sessionStorage.setItem('tico.settings.search.bot-target', JSON.stringify({bot: result.dataset.bot, target: result.dataset.target || ''})); }
        catch { /* the destination still opens if storage is unavailable */ }
      } else SETTINGS_SEARCH_HIGHLIGHT = true;
      settingsSearchClose({focus: false});
      return;
    }
    const ask = event.target.closest('[data-settings-search-ask]');
    if (ask) {
      const query = root.querySelector('#settings-search-input')?.value.trim();
      askDraft(`Ask BotOps to help me find “${query || 'a setting'}” in Settings: `);
    }
  };
  const input = root.querySelector('#settings-search-input');
  input.oninput = () => settingsSearchUpdate(input.value);
  input.onkeydown = event => { if (event.key === 'Escape') { event.preventDefault(); settingsSearchClose(); } };
  if (!SETTINGS_SEARCH_BOUND) {
    SETTINGS_SEARCH_BOUND = true;
    document.addEventListener('keydown', event => {
      if (event.key !== '/' || event.altKey || event.ctrlKey || event.metaKey) return;
      const active = document.activeElement;
      if (active?.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (!$('#settings-search')) return;
      event.preventDefault(); settingsSearchOpen();
    });
  }
}

function settingsSearchOpen() {
  const nav = $('#settings-tabs'), panel = $('[data-settings-search-panel]');
  if (!nav || !panel) return;
  nav.classList.add('settings-searching');
  $('.settings-layout')?.classList.add('settings-searching');
  panel.hidden = false;
  const input = $('#settings-search-input');
  input.focus();
  if (input.value) settingsSearchUpdate(input.value);
  else $('#settings-search-results').innerHTML = '<p class="muted">Type at least 2 characters.</p>';
}

function settingsSearchClose({focus = true} = {}) {
  const nav = $('#settings-tabs'), panel = $('[data-settings-search-panel]');
  if (!nav || !panel) return;
  ++SETTINGS_SEARCH_REQUEST;
  nav.classList.remove('settings-searching');
  $('.settings-layout')?.classList.remove('settings-searching');
  panel.hidden = true;
  const input = $('#settings-search-input');
  if (input) input.value = '';
  $('#settings-search-results').replaceChildren();
  if (focus) $('[data-settings-search-open]')?.focus();
}

async function settingsSearchCatalog() {
  if (SETTINGS_SEARCH_CATALOG) return SETTINGS_SEARCH_CATALOG;
  const response = await get('/v2/settings/catalog?limit=200');
  SETTINGS_SEARCH_CATALOG = Array.isArray(response?.settings) ? response.settings : [];
  return SETTINGS_SEARCH_CATALOG;
}

function settingsSearchFields(setting) {
  return [setting.name, setting.where, setting.key?.replace(/[._]/g, ' '), setting.words, setting.why,
    ...(setting.parts || []).flatMap(part => [part.name, part.help])].filter(Boolean).join(' ').toLowerCase();
}

function settingsSearchMatches(query, fields) {
  return query.split(/\s+/).filter(Boolean).every(word => fields.toLowerCase().includes(word));
}

function settingsSearchHref(setting) {
  const card = String(setting.card || '');
  const hash = card.indexOf('#/');
  if (hash >= 0) return card.slice(hash);
  if (card.startsWith('/settings')) return '#' + card;
  return '#/settings';
}

function settingsSearchGroup(where = '') {
  if (/Settings > People(?: >|$)/.test(where)) return 'people';
  if (/Settings > Bots(?: >|$)/.test(where)) return 'bots';
  if (/Settings > AI models(?: >|$)/.test(where)) return 'models';
  if (/Settings > Computers(?: >|$)/.test(where)) return 'computers';
  if (/Settings > Limits & billing(?: >|$)/.test(where)) return 'limits';
  if (/Settings > Automation(?: >|$)/.test(where)) return 'automation';
  if (/Settings > Tasks(?: >|$)/.test(where)) return 'tasks';
  if (/Settings > Connections(?: >|$)/.test(where)) return 'connections';
  if (/Settings > Company & policies(?: >|$)/.test(where)) return 'policies';
  if (/Settings > Health & history(?: >|$)/.test(where)) return 'health';
  if (where.startsWith('Settings >')) return 'you';
  return '';
}

function settingsSearchAllowed(setting) {
  const group = settingsSearchGroup(setting.where);
  if (group && !settingsGroupShown(group)) return false;
  if (setting.who === 'owner' && S.me?.role !== 'owner') return false;
  if (setting.who === 'admin' && !settingsIsAdmin()) return false;
  return true;
}

function settingsSearchVisibleBots() {
  return (S.emps || []).filter(bot => bot.id && bot.status !== 'archived' &&
    !isHiddenBot(bot.id) && (!botTabs || botTabs(bot.id).includes('settings')));
}

function settingsSearchBotName(bot) {
  return botDisplayName(bot.id) || bot.display_name || bot.slug || bot.id;
}

async function settingsSearchBotRepo(bot) {
  const cached = SETTINGS_SEARCH_BOT_REPOS.get(bot.id);
  if (cached) return cached;
  const pending = Promise.all([
    get(`/v2/bots/${encodeURIComponent(bot.id)}/repositories`),
    get('/v2/repositories')
  ]).then(([view, team]) => ({view, team})).catch(error => ({error}));
  SETTINGS_SEARCH_BOT_REPOS.set(bot.id, pending);
  const result = await pending;
  SETTINGS_SEARCH_BOT_REPOS.set(bot.id, result);
  return result;
}

async function settingsSearchRepositories() {
  if (SETTINGS_SEARCH_REPOSITORIES) return SETTINGS_SEARCH_REPOSITORIES;
  SETTINGS_SEARCH_REPOSITORIES = await get('/v2/repositories');
  return SETTINGS_SEARCH_REPOSITORIES;
}

function settingsSearchRow({name, where, help = '', href, bot = '', target = '', repo = '', disabled = false, reason = '', goHref = ''}) {
  return {name, where, help, href, bot, target, repo, disabled, reason, goHref};
}

function settingsSearchRowHTML(row, index) {
  const details = `<strong>${esc(row.name)}</strong><span>${esc(row.where)}</span>${row.help ? `<small>${esc(row.help)}</small>` : ''}`;
  const data = `${row.bot ? `data-bot="${esc(row.bot)}"` : ''}${row.repo ? ` data-repo="${esc(row.repo)}"` : ''}${row.target ? ` data-target="${esc(row.target)}"` : ''}`;
  if (row.disabled) return `<div class="settings-search-result is-disabled" role="option" data-settings-search-result data-disabled="true" data-index="${index}" ${data}>
      ${details}<small>${esc(row.reason)}</small><a class="settings-search-go" data-settings-search-go href="${esc(row.goHref || row.href)}">Go to Repositories</a></div>`;
  return `<a class="settings-search-result" role="option" data-settings-search-result data-index="${index}" ${data} href="${esc(row.href)}">${details}</a>`;
}

async function settingsSearchRows(catalog, query, request) {
  const rows = [], pending = [], errors = [];
  const bots = settingsSearchVisibleBots();
  for (const setting of catalog) {
    if (!settingsSearchAllowed(setting)) continue;
    if (setting.target === 'bot') {
      for (const bot of bots) {
        const botName = settingsSearchBotName(bot);
        const botEditor = setting.key === 'bot.repositories' || setting.where.includes('Bot editor');
        const href = botEditor ? `#/bot/${encodeURIComponent(bot.id)}/settings` : settingsSearchHref(setting);
        const parts = setting.parts?.length ? setting.parts : [{name: setting.name, help: setting.why || ''}];
        for (const part of parts) {
          const name = part.name || setting.name;
          const where = setting.key === 'bot.repositories' ? `Settings > Bots > ${botName} > Repositories > ${name}`
            : botEditor ? setting.where.replace('Bot editor', botName)
            : `${setting.where} > ${botName}`;
          if (!settingsSearchMatches(query, `${name} ${part.help || ''} ${setting.where} ${setting.words || ''} ${botName}`)) continue;
          const target = setting.key === 'bot.repositories' ? name : botEditor ? `field:${setting.key.slice(4).replace(/\./g, '_')}` : '';
          const row = settingsSearchRow({name, where, help: part.help, href,
            bot: botEditor ? bot.id : '', target});
          if (setting.key === 'bot.repositories' && ['Run workflows', 'Edit workflow files'].includes(name)) {
            const state = await settingsSearchBotRepo(bot);
            if (request !== SETTINGS_SEARCH_REQUEST) return {rows: [], pending, errors};
            if (state.error) errors.push(`Could not load ${botName}'s repository settings.`);
            else if (!state.team.github_connected) {
              row.disabled = true;
              row.reason = 'The GitHub App is not connected.';
              row.goHref = settingsSearchHref(setting);
              row.bot = '';
              row.target = '';
            } else if (state.view.mode === 'own') {
              row.disabled = true;
              row.reason = `Not shown for ${botName} because Repositories is set to Own repo only. Switch to Chosen repos to set it per repository.`;
              row.target = 'repositories';
            } else if (state.view.mode === 'all' && state.view.all_access !== 'write') {
              row.disabled = true;
              row.reason = `Not available for ${botName} because All ticked repos needs Write access. Choose Write to set it.`;
              row.target = 'repositories';
            }
          }
          rows.push(row);
        }
      }
      continue;
    }
    if (setting.key === 'repo.enabled' || setting.key === 'repo.setup_command') {
      if (!settingsSearchMatches(query, settingsSearchFields(setting))) continue;
      let repoData;
      try { repoData = await settingsSearchRepositories(); }
      catch (error) { errors.push(`Could not load repositories: ${error.message || 'try again'}.`); continue; }
      if (request !== SETTINGS_SEARCH_REQUEST) return {rows: [], pending, errors};
      const repos = (repoData.repositories || []).filter(repo => !repo.archived && !repo.bot_repo);
      for (const repo of repos) {
        if (!settingsSearchMatches(query, `${settingsSearchFields(setting)} ${repo.full_name}`)) continue;
        rows.push(settingsSearchRow({name: setting.name, where: `${setting.where} > ${repo.full_name}`,
          href: settingsSearchHref(setting), repo: repo.full_name, target: setting.key}));
      }
      continue;
    }
    if (settingsSearchMatches(query, settingsSearchFields(setting))) {
      rows.push(settingsSearchRow({name: setting.name, where: setting.where, help: setting.why || '', href: settingsSearchHref(setting)}));
    }
  }
  return {rows, pending, errors};
}

function settingsSearchRender(rows, query, errors = []) {
  const results = $('#settings-search-results');
  if (!results) return;
  const notices = errors.map(error => `<p class="muted">${esc(error)}</p>`).join('');
  if (!rows.length) {
    results.innerHTML = `${notices}<p class="muted">No setting matches “${esc(query)}”. Try fewer words, or the name of the bot or repository.</p>
      <button class="linkish settings-search-ask" type="button" data-settings-search-ask>Ask BotOps</button>`;
    return;
  }
  results.innerHTML = `${notices}${rows.map(settingsSearchRowHTML).join('')}`;
}

async function settingsSearchUpdate(value) {
  const results = $('#settings-search-results');
  if (!results) return;
  const query = String(value || '').trim().toLowerCase();
  const request = ++SETTINGS_SEARCH_REQUEST;
  if (query.length < 2) { results.innerHTML = '<p class="muted">Type at least 2 characters.</p>'; return; }
  results.innerHTML = '<p class="muted">Searching…</p>';
  try {
    const catalog = await settingsSearchCatalog();
    if (request !== SETTINGS_SEARCH_REQUEST || !document.querySelector('[data-settings-search-panel]')?.isConnected) return;
    const answer = await settingsSearchRows(catalog, query, request);
    if (request !== SETTINGS_SEARCH_REQUEST) return;
    settingsSearchRender(answer.rows, query, answer.errors);
  } catch (error) {
    if (request !== SETTINGS_SEARCH_REQUEST) return;
    results.innerHTML = `<p class="err">Couldn't search settings: ${esc(error.message || 'try again')}</p>`;
  }
}

async function settingsSearchRevealBot(slug) {
  let target;
  try { target = JSON.parse(sessionStorage.getItem('tico.settings.search.bot-target') || 'null'); } catch {}
  if (!target || target.bot !== slug) return;
  sessionStorage.removeItem('tico.settings.search.bot-target');
  const host = $('#bot-editor');
  if (!host) return;
  const start = Date.now();
  const reveal = () => {
    if (!host.isConnected || Date.now() - start > 5000) return;
    const field = target.target.startsWith('field:') ? target.target.slice(6) : '';
    const selector = target.target === 'Run workflows' ? '[data-brepo-run]'
      : target.target === 'Edit workflow files' ? '[data-brepo-edit]'
      : target.target === 'repositories' ? '[data-bot-repos]'
      : field ? `[name="${CSS.escape(field)}"]` : '[data-bot-repos]';
    const control = host.querySelector(selector);
    if (!control || control.hidden) { setTimeout(reveal, 150); return; }
    control.scrollIntoView({block: 'center'});
    control.classList.add('settings-search-highlight');
    setTimeout(() => control.classList.remove('settings-search-highlight'), 1800);
    if (control.matches('input,select,button')) control.focus({preventScroll: true});
  };
  reveal();
}

function settingsSearchRevealRepo() {
  let target;
  try { target = JSON.parse(sessionStorage.getItem('tico.settings.search.repo-target') || 'null'); } catch {}
  if (!target?.repo) return;
  const row = [...document.querySelectorAll('#set-repos [data-repo]')].find(item => item.dataset.repo === target.repo);
  if (!row) return;
  sessionStorage.removeItem('tico.settings.search.repo-target');
  const control = row.querySelector(target.target === 'repo.setup_command' ? '[data-repo-setup]' : '[data-repo-tick]');
  row.scrollIntoView({block: 'center'});
  row.classList.add('settings-search-highlight');
  setTimeout(() => row.classList.remove('settings-search-highlight'), 1800);
  if (control) control.focus({preventScroll: true});
}
