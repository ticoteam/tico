/* ui/app/redesign-library.js — Library › Tools: every tool the team has, who may use it and which ones have issues, in one
   place. The tools are GET /api/v2/tools (each with the bots whose entries declare it, their write level and any
   problem a computer reported); the credential vault (GET /api/v2/credentials, for whoever may open it) adds each
   credential, who it is granted to and when a bot last used it; the groups (GET /api/v2/groups) let the list be
   narrowed to a team, nested teams included. A row opens the tool's page (integrationDetail, ui/app/integrations.js)
   beside the list on a computer, as its own screen on a phone; a credential no tool names is its own row.
   Addresses: #/library/tools[/<service> | /credentials/<id>][?who=bot:<slug>|human:<id>|group:<id>&issues=1&q=…]. The old
   ones (#/library/connections, #/integrations, #/credentials, Settings › Credentials) lead here.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RD_TOOLS = '#/library/tools';
const RD_CONNECTIONS = '#/library/connections';   // the page's first name
const LC_KIND_ICONS = {api: 'api', sql: 'inventory_2', browser: 'travel_explore', mail: 'mail', cli: 'code', mcp: 'link', credential: 'key_vertical'};
const LC_CRED_KIND = {api_key: 'API key', password: 'Password', token: 'Token', file: 'File', connection: 'Connection'};
const LC_LEVEL_RANK = {never: 0, approval: 1, allowed: 2};
let LC = null;              // {rows, groups, items, error, root, loading, key, filter: {who, issues, q}, show}
const lcPhone = () => matchMedia('(max-width:760px)').matches;
// The filters ride in the address, so a reload, a shared link or a bot page's link opens the same list.
function lcQuery(filter = LC?.filter || {}) {
  const q = new URLSearchParams();
  if (filter.who) q.set('who', filter.who);
  if (filter.issues) q.set('issues', '1');
  if (filter.q) q.set('q', filter.q);
  const text = q.toString();
  return text ? '?' + text : '';
}
const lcHref = key => RD_TOOLS + (key ? '/' + key.split('/').map(encodeURIComponent).join('/') : '') + lcQuery();

// ----------------------------------------------------------------- the model: one item per tool or loose credential
const lcBotName = slug => botDisplayName(slug);
const lcPerson = id => (S.people || []).find(p => p.id === id) || (VAULT?.people || []).find(p => p.id === id) || {id, name: id};
// Variables a tool names (CLOSE_API_KEY), from its page and from the entries bots declare; words like API are not one.
const lcEnvs = row => [...new Set([...credEnvs(row.credentials), ...(row.connections || []).map(c => c.env)].filter(e => e && e.includes('_') && !e.includes('<')))];

function lcModel(rows) {
  const creds = VAULT?.credentials || [], used = new Set();
  const items = rows.map(row => {
    const bots = new Map(), people = new Map(), issues = [];
    for (const c of row.connections || []) {
      const was = bots.get(c.bot);
      const level = c.write || row.writes;
      if (!was || LC_LEVEL_RANK[level] > LC_LEVEL_RANK[was.level]) bots.set(c.bot, {level, problem: c.problem || was?.problem || ''});
      else if (c.problem && !was.problem) was.problem = c.problem;
      if (c.problem) issues.push({bot: c.bot, text: c.problem});
    }
    for (const bot of row.bots || []) if (!bots.has(bot)) bots.set(bot, {level: row.writes, problem: ''});
    const mine = row.status === 'removed' ? [] : vaultForIntegration(row);
    for (const cred of mine) {
      used.add(cred.id);
      if (!cred.stored) issues.push({text: `${cred.name}: no value stored`});
      for (const g of cred.grants || []) {
        const [kind, id] = g.subject.split(':');
        if (kind === 'bot' && !bots.has(id)) bots.set(id, {level: row.writes, problem: '', via: 'credential'});
        if (kind === 'human' && !people.has(id)) people.set(id, {level: row.writes});
      }
    }
    const group = row.status === 'removed' ? 'Removed' : bots.size || people.size ? 'In use' : 'Available';
    const kind = INTEGRATION_KIND[row.kind] || row.kind || '';
    return {key: row.service, row, title: row.title || row.service, kind, icon: LC_KIND_ICONS[row.kind] || 'api', group,
            bots, people, issues, creds: mine,
            text: [row.service, row.title, kind, row.summary, ...(row.aliases || []), ...lcEnvs(row), ...mine.map(c => c.name)].join(' ').toLowerCase()};
  });
  // A credential no tool names (a model key, a service with no page yet) is a row of its own.
  for (const cred of creds) {
    if (used.has(cred.id)) continue;
    const bots = new Map(), people = new Map();
    for (const g of cred.grants || []) {
      const [kind, id] = g.subject.split(':');
      if (kind === 'bot') bots.set(id, {level: '', problem: ''});
      if (kind === 'human') people.set(id, {level: ''});
    }
    items.push({key: 'credentials/' + cred.id, cred, title: cred.name, kind: LC_CRED_KIND[cred.kind] || cred.kind, icon: 'key_vertical',
                group: 'Credentials', bots, people, creds: [cred], issues: cred.stored ? [] : [{text: 'No value stored'}],
                text: [cred.name, cred.env, cred.username, cred.source, 'credential key'].join(' ').toLowerCase()});
  }
  return items;
}

// Everyone in a group and the groups under it: {bots, people}.
function lcGroupMembers(gid) {
  const groups = LC.groups || [], bots = new Set(), people = new Set();
  const walk = id => {
    const g = groups.find(x => x.id === id);
    if (!g) return;
    g.bots.forEach(b => bots.add(b)); g.people.forEach(p => people.add(p));
    groups.filter(x => x.parent === id).forEach(x => walk(x.id));
  };
  walk(gid);
  return {bots, people};
}
function lcShown() {
  const {who, issues, q} = LC.filter;
  const words = (q || '').toLowerCase().split(/\s+/).filter(Boolean);
  const [kind, id] = who ? [who.slice(0, who.indexOf(':')), who.slice(who.indexOf(':') + 1)] : [];
  const team = kind === 'group' ? lcGroupMembers(id) : null;
  return LC.items.filter(item => (!issues || item.issues.length) && words.every(w => item.text.includes(w))
    && (!kind || (kind === 'bot' ? item.bots.has(id) : kind === 'human' ? item.people.has(id)
      : [...item.bots.keys()].some(b => team.bots.has(b)) || [...item.people.keys()].some(p => team.people.has(p)))));
}
function lcGrouped(items) {
  return ['In use', 'Available', 'Credentials', 'Removed'].map(label => [label, items.filter(i => i.group === label)]).filter(([, xs]) => xs.length);
}

// ----------------------------------------------------------------- the page
async function pageRdTools() {
  const [path, query = ''] = S.route.split('?');
  const key = path.slice(RD_TOOLS.length + 1).split('/').map(decodeURIComponent).join('/');
  const params = new URLSearchParams(query);
  $('#main').classList.add('lc-layout');
  if (LC?.loading && document.body.contains(LC.root)) {
    // The list is still loading for an earlier visit: wait for it, then draw only if this is still the route.
    const state = LC;
    await state.loading;
    if (LC !== state || location.hash.split('?')[0] !== path) return;
  } else if (!LC || !document.body.contains(LC.root)) {
    $('#main').innerHTML = `<div class="lc" id="lc-root">
      <header class="lc-ph"><h1>Tools</h1>${helpBubble('Who may use each tool, and what they may change there: read only, with approval, or freely.')}
        <span class="spacer"></span><button class="docs-ask" id="lc-ask" type="button" data-ask-open aria-label="Ask" title="Ask">${ldotHTML({state: 'still', size: 16})}<span class="docs-ask-word">Ask</span></button><span class="lc-add"></span></header>
      <div class="lc-body"><aside class="lc-list" aria-label="Tools">
          <div class="lc-filters"><input class="lc-q" type="search" placeholder="Search" aria-label="Search tools" autocomplete="off">
            <div class="lc-frow"><select class="lc-who" aria-label="Who has access"><option value="">Everyone</option></select>
              <button class="lc-issues" type="button" aria-pressed="false"><span class="lc-dot" aria-hidden="true"></span>Issues <span class="lc-in"></span></button></div></div>
          <div class="lc-scroll">${skelRows(6)}</div></aside>
        <section class="lc-pane" aria-live="polite"><div id="int-detail"></div></section></div>
      <dialog class="bot-editor" id="credential-dialog" aria-label="Credential"></dialog></div>`;
    LC = {rows: null, groups: [], items: [], error: '', key: null, root: $('#lc-root'), loading: null,
          filter: {who: params.get('who') || '', issues: params.get('issues') === '1', q: params.get('q') || ''}};
    const state = LC;
    lcWire();
    state.loading = Promise.all([
      get('/v2/tools').then(r => r.integrations || []),
      vaultFetch().catch(() => null),
      get('/v2/groups').catch(() => []),
    ]).then(([rows, , groups]) => { state.rows = rows; state.groups = Array.isArray(groups) ? groups : groups?.groups || []; },
            e => { state.rows = []; state.error = e.message; })
      .then(() => { state.loading = null; if (LC !== state) return; state.items = lcModel(state.rows); lcDrawControls(); });
    await state.loading;
    // Left, or moved to another tool meanwhile (a filter typed while it loads changes only the query). The address, not
    // S.route: a click away that the router has not handled yet must not be sent back to the first tool below.
    if (LC !== state || location.hash.split('?')[0] !== path) return;
  }
  // `?show=credentials` (the old Credentials page's links) opens on the credentials.
  const show = params.get('show');
  const shown = lcShown();
  // A computer opens on the first row; a phone opens on the list.
  if (!key && !lcPhone() && shown.length) {
    const first = (show === 'credentials' && shown.find(i => i.group === 'Credentials')) || lcGrouped(shown)[0][1][0];
    location.replace(lcHref(first.key)); return;
  }
  LC.root.dataset.screen = key ? 'detail' : 'list';
  lcDrawList(key);
  if (!key && show === 'credentials') LC.root.querySelector('.lc-gh[data-group="Credentials"]')?.scrollIntoView({block: 'start'});
  if (key === LC.key) return;
  LC.key = key;
  await lcDrawDetail();
}

// The search box, the who-has-access picker and Issues: each narrows the list and is kept in the address.
function lcWire() {
  const root = LC.root, q = root.querySelector('.lc-q'), who = root.querySelector('.lc-who'), issues = root.querySelector('.lc-issues');
  q.value = LC.filter.q;
  const changed = (picked = false) => {
    history.replaceState(null, '', location.hash.split('?')[0] + lcQuery());
    S.route = LAST_ROUTE = location.hash;
    lcDrawList(LC.key);
    // The open tool's access opens on the team picked; its back link keeps the filters.
    const facts = $('#tool-facts'), item = LC.items.find(i => i.key === LC.key);
    if (picked && facts && item?.row) facts.innerHTML = lcFactsHTML(item);
    $('.lc-back')?.setAttribute('href', RD_TOOLS + lcQuery());
  };
  q.oninput = () => { LC.filter.q = q.value.trim(); changed(); };
  who.onchange = () => { LC.filter.who = who.value; changed(true); };
  issues.onclick = () => { LC.filter.issues = !LC.filter.issues; changed(); };
  root.querySelector('.lc-add').onclick = ev => { if (ev.target.closest('[data-lc-add]')) vaultEdit(); };
}
// The picker: teams (nested ones indented under theirs), people, bots. Built once the groups have arrived.
function lcDrawControls() {
  const root = LC.root, who = root.querySelector('.lc-who');
  const groups = LC.groups || [], depth = g => { let d = 0, p = g.parent; while (p && d < 8) { d++; p = groups.find(x => x.id === p)?.parent; } return d; };
  const ordered = [], visit = parent => groups.filter(g => (g.parent || '') === (parent || '')).forEach(g => { ordered.push(g); visit(g.id); });
  visit('');
  groups.filter(g => !ordered.includes(g)).forEach(g => ordered.push(g));   // a group whose parent is hidden
  const opt = (value, label) => `<option value="${esc(value)}">${esc(label)}</option>`;
  const people = (S.people || []).filter(p => !p.hidden && !p.archived);
  const bots = (S.emps || []).filter(e => e.status !== 'archived').map(e => [e.name, lcBotName(e.name)]).sort((a, b) => a[1].localeCompare(b[1], undefined, {numeric: true}));
  who.innerHTML = opt('', 'Everyone')
    + (ordered.length ? `<optgroup label="Teams">${ordered.map(g => opt('group:' + g.id, ' '.repeat(depth(g) * 2) + g.name)).join('')}</optgroup>` : '')
    + (people.length ? `<optgroup label="People">${people.map(p => opt('human:' + p.id, p.name || p.email || p.id)).join('')}</optgroup>` : '')
    + (bots.length ? `<optgroup label="Bots">${bots.map(([slug, name]) => opt('bot:' + slug, name)).join('')}</optgroup>` : '');
  // A filter for someone the list does not know (a link to a bot this person cannot see) still shows as chosen.
  if (LC.filter.who && !who.querySelector(`option[value="${CSS.escape(LC.filter.who)}"]`)) who.insertAdjacentHTML('beforeend', opt(LC.filter.who, LC.filter.who.split(':')[1]));
  who.value = LC.filter.who;
  root.querySelector('.lc-add').innerHTML = VAULT?.can_manage ? '<button class="ghost small" type="button" data-lc-add>Add credential</button>' : '';
}

function lcDrawList(key) {
  const box = LC.root.querySelector('.lc-scroll');
  const issues = LC.root.querySelector('.lc-issues'), count = LC.items.filter(i => i.issues.length).length;
  issues.setAttribute('aria-pressed', String(!!LC.filter.issues));
  issues.hidden = !count && !LC.filter.issues;
  LC.root.querySelector('.lc-in').textContent = count || '';
  if (LC.error) { box.innerHTML = `<p class="err lc-pad">${esc(LC.error)}</p>`; return; }
  const shown = lcShown();
  const html = lcGrouped(shown).map(([label, xs]) => `<div class="lc-gh" data-group="${esc(label)}"><span>${esc(label)}</span><span>${xs.length}</span></div>` + xs.map(item => {
    const n = item.bots.size + item.people.size, sel = item.key === key;
    const who = n ? `${n} with access` : 'Nobody has it';
    const trouble = item.issues.length ? `${item.issues.length} issue${item.issues.length === 1 ? '' : 's'}: ${item.issues[0].text}` : '';
    return `<a class="lc-row${sel ? ' sel' : ''}" href="${lcHref(item.key)}" data-key="${esc(item.key)}"${sel ? ' aria-current="page"' : ''} title="${esc([item.row?.summary, trouble].filter(Boolean).join(' · '))}">
      <span class="nav-icon" aria-hidden="true">${item.icon}</span>
      <span class="lc-nm">${esc(item.title)} <span class="muted">· ${esc(item.kind)}</span></span>
      ${item.issues.length ? `<span class="lc-dot" role="img" aria-label="${esc(trouble)}"></span>` : ''}
      <span class="lc-n${n ? '' : ' none'}" title="${esc(who)}" aria-label="${esc(who)}">${n}</span></a>`;
  }).join('')).join('');
  box.innerHTML = html || `<p class="muted lc-pad">${LC.items.length ? 'Nothing matches.' : 'No tools yet.'}</p>`;
}

// ----------------------------------------------------------------- one tool, or one credential
async function lcDrawDetail() {
  const key = LC.key, detail = $('#int-detail');
  if (!key) { detail.innerHTML = ''; return; }
  $('.lc-pane').scrollTop = 0;
  const back = `<a class="lc-back" href="${RD_TOOLS + lcQuery()}" aria-label="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></a>`;
  if (key.startsWith('credentials/')) { lcDrawCredential(key.slice(12), back); return; }
  detail.innerHTML = skelRows(4, {card: true});
  const service = key;
  await integrationDetail(service, ++INT_LOAD, {base: RD_TOOLS, head: back,
    facts: () => lcFactsHTML(LC.items.find(i => i.key === service)),
    after: page => { lcWireFacts(); lcMountConnect(page.service); }});
}

// Who has it: one line a teammate, under the team they are in (folded when there are many), each with what they may
// change there and a red dot when their entry has a problem.
function lcAccessHTML(item) {
  const groups = LC.groups || [], teamOf = new Map(), path = g => {
    const names = []; let x = g, i = 0;
    while (x && i++ < 8) { names.unshift(x.name); x = groups.find(y => y.id === x.parent); }
    return names.join(' › ');
  };
  for (const g of groups) { g.bots.forEach(b => teamOf.set('b:' + b, g)); g.people.forEach(p => teamOf.set('p:' + p, g)); }
  const lines = [
    ...[...item.people].map(([id, a]) => ({team: teamOf.get('p:' + id), sort: lcPerson(id).name || id, html: `<a class="lc-who-row" href="#/person/${encodeURIComponent(id)}">${personAvatar(lcPerson(id), 20)}<span class="lc-wn">${esc(lcPerson(id).name || id)}</span>${a.level ? toolWritePill(a.level) : ''}</a>`})),
    ...[...item.bots].map(([slug, a]) => ({team: teamOf.get('b:' + slug), sort: lcBotName(slug), html: `<a class="lc-who-row" href="#/bot/${encodeURIComponent(slug)}"${a.problem ? ` title="${esc(a.problem)}"` : ''}>${avatar(slug, 20)}<span class="lc-wn">${esc(lcBotName(slug))}</span>${a.problem ? `<span class="lc-dot" role="img" aria-label="${esc(a.problem)}"></span>` : ''}${a.via === 'credential' ? '<span class="muted lc-via">key</span>' : ''}${a.level ? toolWritePill(a.level) : ''}</a>`})),
  ];
  if (!lines.length) return '<p class="muted lc-none">Nobody has it.</p>';
  const byTeam = new Map();
  for (const line of lines) {
    const label = line.team ? path(line.team) : 'No team';
    if (!byTeam.has(label)) byTeam.set(label, []);
    byTeam.get(label).push(line);
  }
  const teams = [...byTeam].sort(([a], [b]) => (a === 'No team') - (b === 'No team') || a.localeCompare(b, undefined, {numeric: true}));
  // Many teammates: each team folds; the team the list is narrowed to (and the teams under it) open, else the first.
  const many = lines.length > 24, picked = LC.filter.who.startsWith('group:') ? groups.find(g => g.id === LC.filter.who.slice(6)) : null;
  const opened = picked ? new Set([...byTeam.keys()].filter(label => label === path(picked) || label.startsWith(path(picked) + ' › '))) : null;
  const sorted = xs => xs.sort((a, b) => a.sort.localeCompare(b.sort, undefined, {numeric: true})).map(x => x.html).join('');
  if (teams.length === 1 && teams[0][0] === 'No team') return sorted(teams[0][1]);   // no teams to tell apart
  return teams.map(([label, xs], i) => `<details class="lc-team"${!many || (opened?.size ? opened.has(label) : i === 0) ? ' open' : ''}><summary><span>${esc(label)}</span><span class="muted">${xs.length}</span></summary>
    ${sorted(xs)}</details>`).join('');
}
// One stored credential: name and variable, whether a value is stored, when a bot last used it, and what may be done.
function lcCredHTML(cred) {
  const used = cred.last_used_at ? `used ${ago(cred.last_used_at)}${cred.last_used_by ? ' by ' + lcBotName(cred.last_used_by) : ''}` : 'never used';
  const acts = [
    VAULT?.can_manage ? `<button class="linkish" type="button" data-vault-edit="${esc(cred.id)}">${cred.stored ? 'Rotate' : 'Set'}</button>` : '',
    `<button class="linkish" type="button" data-vault-share="${esc(cred.id)}">${VAULT?.can_manage ? 'Access' : 'Use with my bots'}</button>`,
    cred.stored && cred.can_reveal ? `<button class="linkish" type="button" data-vault-reveal="${esc(cred.id)}">Reveal</button>` : '',
  ].filter(Boolean).join('');
  return `<div class="lc-cred" data-cred="${esc(cred.id)}"><span class="nav-icon" aria-hidden="true">key_vertical</span>
    <span class="lc-cn"><b>${esc(cred.name)}</b>${cred.env ? ` <span class="mono muted">${esc(cred.env)}</span>` : ''}</span>
    <span class="pill ${cred.stored ? 'ok' : 'fail'}">${cred.stored ? 'Set' : 'No value'}</span>
    <span class="muted lc-used" title="${esc(cred.last_used_at ? fmt(cred.last_used_at) : '')}">${esc(used)}</span>
    <span class="lc-acts">${acts}</span></div>`;
}
// A variable the tool names that the vault does not hold: what the bots' computers say about it, and Add.
function lcEnvHTML(env, row) {
  const conns = (row.connections || []).filter(c => c.env === env);
  const [word, tone] = conns.some(c => c.problem) ? ['Missing', 'fail'] : conns.some(c => c.status === 'ready') ? ['On computer', 'ok'] : ['Not set', ''];
  return `<div class="lc-cred"><span class="nav-icon" aria-hidden="true">key_vertical</span>
    <span class="lc-cn"><b class="mono">${esc(env)}</b></span>
    <span class="pill ${tone}">${esc(word)}</span><span class="lc-used"></span>
    <span class="lc-acts">${VAULT?.can_manage ? `<button class="linkish" type="button" data-lc-add-env="${esc(env)}">Add</button>` : ''}</span></div>`;
}
function lcIssuesHTML(item) {
  if (!item.issues.length) return '';
  // The same problem on many bots is one line, with who has it.
  const byText = new Map();
  for (const issue of item.issues) {
    if (!byText.has(issue.text)) byText.set(issue.text, []);
    if (issue.bot) byText.get(issue.text).push(issue.bot);
  }
  return `<section class="card lc-issues-card"><header><h2>Issues <span class="muted">${item.issues.length}</span></h2></header>${[...byText].map(([text, bots]) =>
    `<div class="lc-issue"><span class="lc-dot" aria-hidden="true"></span><span>${esc(text)}${bots.length ? ` <span class="muted">· ${esc(bots.slice(0, 3).map(lcBotName).join(', '))}${bots.length > 3 ? ` +${bots.length - 3}` : ''}</span>` : ''}</span></div>`).join('')}</section>`;
}
function lcFactsHTML(item) {
  if (!item) return '';
  const row = item.row, n = item.bots.size + item.people.size;
  const held = new Set(item.creds.map(c => c.env).filter(Boolean));
  const missing = row.status === 'removed' ? [] : lcEnvs(row).filter(env => !held.has(env));
  const creds = item.creds.map(lcCredHTML).join('') + missing.map(env => lcEnvHTML(env, row)).join('');
  return lcIssuesHTML(item)
    + `<section class="card lc-access"><header><h2>Access <span class="muted">${n}</span></h2></header>${lcAccessHTML(item)}</section>`
    + (creds ? `<section class="card lc-creds"><header><h2>Credentials</h2></header>${creds}</section>` : '');
}
// The facts' buttons open the vault's own dialogs (ui/app/vault.js); a saved change redraws them (lcVaultChanged).
function lcWireFacts() {
  const host = $('#tool-facts');
  if (!host) return;
  host.onclick = ev => {
    const b = ev.target.closest('button');
    if (!b) return;
    const cred = id => VAULT?.credentials.find(c => c.id === id);
    if (b.dataset.vaultEdit) vaultEdit(cred(b.dataset.vaultEdit));
    else if (b.dataset.vaultShare) vaultShare(b.dataset.vaultShare);
    else if (b.dataset.vaultReveal) vaultReveal(b.dataset.vaultReveal);
    else if (b.dataset.vaultDelete) { if (confirm(`Delete ${cred(b.dataset.vaultDelete)?.name || 'this credential'} and every grant of it?`)) void vaultDelete(b.dataset.vaultDelete); }
    else if (b.dataset.lcAddEnv) {
      const row = LC.items.find(i => i.key === LC.key)?.row;
      vaultEdit({name: row?.title || '', env: b.dataset.lcAddEnv, source: row?.service || '', kind: /TOKEN/.test(b.dataset.lcAddEnv) ? 'token' : 'api_key'});
    }
  };
}
// The connections an owner makes here: the GitHub App on GitHub's page, the Slack workspace and its channels on Slack's.
function lcMountConnect(service) {
  const cards = {
    github: S.me?.role === 'owner' ? [['GitHub App', 'set-github', window.mountGithubConnect]] : [],
    slack: [...(S.me?.role === 'owner' ? [['Slack workspace', 'set-slack', window.mountSlackConnect]] : []),
            ...(S.me?.bot_admin ? [['Slack channels', 'set-slack-channels', window.mountSlackChannels]] : [])],
  }[service] || [];
  const facts = $('#tool-facts');
  if (!facts || !cards.length) return;
  facts.insertAdjacentHTML('afterend', cards.map(([title, id]) => `<section class="card lc-connect"><header><h2>${esc(title)}</h2></header><div id="${id}">${skelRows(1, {card: true})}</div></section>`).join(''));
  for (const [, id, mount] of cards) mount?.($('#' + id));
}
// A credential no tool names: its facts, who has it, and what may be done.
function lcDrawCredential(id, back) {
  const item = LC.items.find(i => i.key === 'credentials/' + id), cred = item?.cred;
  const detail = $('#int-detail');
  if (!cred) { detail.innerHTML = `<div class="meeting-head">${back}<div><h1>Credential</h1></div></div><div class="empty">No credential here, or you cannot open it.</div>`; return; }
  const row = (dt, dd) => dd ? `<dt>${dt}</dt><dd>${dd}</dd>` : '';
  const grants = (cred.grants || []).map(g => {
    const [kind, gid] = g.subject.split(':');
    const label = g.subject === 'computers' ? 'Every computer' : kind === 'computer' ? vaultSubject(g.subject) : null;
    if (label) return `<div class="lc-who-row"><span class="nav-icon" aria-hidden="true">computer</span><span class="lc-wn">${esc(label)}</span></div>`;
    const who = kind === 'human' ? `${personAvatar(lcPerson(gid), 20)}<span class="lc-wn">${esc(lcPerson(gid).name || gid)}</span>` : `${avatar(gid, 20)}<span class="lc-wn">${esc(lcBotName(gid))}</span>`;
    return `<a class="lc-who-row" href="${kind === 'human' ? '#/person/' : '#/bot/'}${encodeURIComponent(gid)}">${who}${g.parent_id ? '<span class="muted lc-via">delegated</span>' : ''}</a>`;
  }).join('') || '<p class="muted lc-none">Owners only.</p>';
  detail.innerHTML = `<div class="meeting-head">${back}<div><h1>${esc(cred.name)}</h1></div></div>
    <div id="tool-facts">${lcIssuesHTML(item)}
    <section class="card lc-creds">${lcCredHTML(cred)}<dl class="int-meta">${row('Type', esc(LC_CRED_KIND[cred.kind] || cred.kind))}${row('Username', esc(cred.username || ''))}${row('Value', cred.preview && `<span class="mono">${esc(cred.preview)}</span>`)}${row('From', esc(cred.source || ''))}
      ${row('Changed', cred.updated_at && `<span title="${esc(fmt(cred.updated_at))}">${esc(ago(cred.updated_at))}</span>`)}</dl>
      ${VAULT?.can_manage ? `<div class="row lc-del"><button class="ghost danger small" type="button" data-vault-delete="${esc(cred.id)}">Delete</button></div>` : ''}</section>
    <section class="card lc-access"><header><h2>Access <span class="muted">${(cred.grants || []).length}</span></h2></header>${grants}</section></div>`;
  lcWireFacts();
}

// A vault dialog saved a change (ui/app/vault.js vaultLoad): the list and what the open row shows follow.
function lcVaultChanged() {
  if (!LC?.rows || !document.body.contains(LC.root)) return;
  LC.items = lcModel(LC.rows);
  lcDrawControls();
  lcDrawList(LC.key);
  const key = LC.key || '';
  if (key.startsWith('credentials/')) {
    // A deleted credential leaves nothing to show: back to the list.
    if (!LC.items.some(i => i.key === key)) { location.replace(RD_TOOLS + lcQuery()); return; }
    lcDrawCredential(key.slice(12), `<a class="lc-back" href="${RD_TOOLS + lcQuery()}" aria-label="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></a>`);
    return;
  }
  const facts = $('#tool-facts'), item = LC.items.find(i => i.key === key);
  if (facts && item) { facts.innerHTML = lcFactsHTML(item); }
}

// The redesign's router asks first (ui/app/redesign.js): Library › Tools, and every old address of what it holds.
function rdToolsRoute(r) {
  for (const old of [RD_CONNECTIONS, INTEGRATIONS]) if (rdUnder(r, old)) { rdToolsOpen(RD_TOOLS + r.slice(old.length)); return true; }
  if (rdUnder(r, CREDENTIALS)) { rdToolsOpen(RD_TOOLS + '?show=credentials'); return true; }
  if (rdUnder(r, RD_TOOLS)) { pageRdTools(); return true; }
  return false;
}
// An old address the redesign's router did not take (ui/app/router.js): Library › Tools at its new one, in place. The
// router marked the rail for the old address, and the rail may already hold Tools (redesignNav ran first), so it is
// marked again for the new one.
function rdToolsOpen(hash) {
  history.replaceState(null, '', hash);
  S.route = LAST_ROUTE = location.hash;
  renderTree();
  pageRdTools();
}
// Leaving the page drops its list, so coming back reads the hub again.
function rdToolsStop() { LC = null; }
