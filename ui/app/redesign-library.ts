/* ui/app/redesign-library.ts — A tool's own page under Tools, for a tool a bot declares and for a key no tool holds.
   ui/app/tools.ts lists them and the shell's toolsRoute routes them:
   - #/tools/<service>: one place for everything about the tool: its issues, who has it (each with what they may change
     there), its keys (set or not, when a bot last used one, Rotate / Access / Reveal, Add for a variable the vault does
     not hold), then what its page says, Slack's and GitHub's connect cards and, on Slack's, the threads linked to Slack
     channels.
   - #/tools/key/<id>: one stored key no tool names (a model key, a service with no page yet): its facts, who it is
     granted to, and Rotate / Access / Reveal / Delete.
   Keys was a page of its own (#/tools/keys, a list beside the open tool); a tool's keys are inside the tool now, the
   list is Tools' (lcModel gives it the declared tools and loose keys), and the old addresses redirect.
   The tools are GET /api/v2/tools (each with the bots whose entries declare it, their write level and any problem a
   computer reported); the credential vault (GET /api/v2/credentials, for whoever may open it) adds each credential, who
   it is granted to and when a bot last used it; the groups (GET /api/v2/groups) put each teammate under their team.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- this page's types (the server's shapes, as the page reads them)
type LcKind = 'api' | 'sql' | 'browser' | 'mail' | 'cli' | 'mcp';
type LcLevel = 'never' | 'approval' | 'allowed';
type LcGroup = import('../types/api').Group;
/** One bot's connection to a tool (backend/integrations.py `connections`). */
interface LcConnection { bot: string; status?: string; write?: LcLevel; problem?: string; env?: string; }
/** One tool as GET /api/v2/tools lists it (`integrations`): the fields this page reads, and the bots that declare it. */
interface LcTool {
  service: string; title: string; kind: LcKind; summary: string; credentials: string[]; aliases?: string[];
  writes: LcLevel; status?: string; bots?: string[]; connections?: LcConnection[];
}
interface LcToolsResponse { integrations?: LcTool[]; }
/** A credential as the vault lists it, with the fields this page reads beyond VaultCredential (vault.ts). `tool_id`:
    the API tool that holds it as a key (its page shows it), absent from a hub that does not say. */
interface LcCredential extends VaultCredential {
  last_used_at?: string | null; last_used_by?: string; can_reveal?: boolean; preview?: string; updated_at?: string; tool_id?: string | null;
}
/** A bot's access to one tool or credential: its write level (blank when only a credential grants it) and its problem. */
interface LcBotAccess { level: LcLevel | ''; problem: string; via?: 'credential'; }
interface LcPersonAccess { level: LcLevel | ''; }
interface LcIssue { bot?: string; text: string; }
/** One row of the list: a declared tool, or a credential no tool names. */
interface LcItem {
  key: string; row?: LcTool; cred?: LcCredential; title: string; kind: string; icon: string; group: string;
  bots: Map<string, LcBotAccess>; people: Map<string, LcPersonAccess>; issues: LcIssue[]; creds: LcCredential[]; text: string;
}
/** The open page's state (LC): what it read, the item it shows (a service, or credentials/<id>), and the teammate or
    team the Tools list was narrowed to, whose team opens first under Access. */
interface LcState { rows: LcTool[] | null; groups: LcGroup[]; items: LcItem[]; key: string; root: HTMLElement; who: string; }
type LcSlackLink = import('../types/api').SlackLink;
interface LcSlackPage { links?: LcSlackLink[]; next_cursor?: string | null; available?: boolean; }
type LcThread = ThreadRow;
interface LcThreadsPage { attention?: LcThread[]; threads?: LcThread[]; next_cursor?: string | null; }
type LcLine = {team: LcGroup | undefined; sort: string; html: string};

const LC_KIND_ICONS: Record<string, string> = {api: 'api', sql: 'inventory_2', browser: 'travel_explore', mail: 'mail', cli: 'code', mcp: 'link', credential: 'key_vertical'};
const LC_CRED_KIND: Record<string, string> = {api_key: 'API key', password: 'Password', token: 'Token', file: 'File', connection: 'Connection'};
const LC_LEVEL_RANK: Record<string, number> = {never: 0, approval: 1, allowed: 2};
let LC: LcState | null = null;              // {rows, groups, items, key, root, who}
// The slots the page's facts and detail are drawn into: `$` finds them by id, and the element type is named here once.
const lcFacts = (): HTMLElement | null => $('#tool-facts') as HTMLElement | null;
const lcDetail = (): HTMLElement => $('#int-detail') as HTMLElement;
// The tool list a page has loaded: a null list would have thrown inside lcModel, so the same failure is named here.
function lcRows(state: LcState): LcTool[] { if (!state.rows) throw new Error('Tool page: no tool list loaded'); return state.rows; }

// ----------------------------------------------------------------- the model: one item per tool or loose credential
const lcBotName = (slug: string): string => botDisplayName(slug);
// The tools list names a bot by its id; one named by slug (a server from before ids, or an old link) is read as its id.
const lcBotId = (ref: string): string => botByRef(ref)?.id || ref;
const lcPerson = (id: string): {id: string; name?: string | null} => (S.people || []).find(p => p.id === id) || (VAULT?.people || []).find(p => p.id === id) || {id, name: id};
// Variables a tool names (CLOSE_API_KEY), from its page and from the entries bots declare; words like API are not one.
const lcEnvs = (row: LcTool): (string | undefined)[] => [...new Set([...credEnvs(row.credentials), ...(row.connections || []).map(c => c.env)].filter(e => e && e.includes('_') && !e.includes('<')))];

function lcModel(rows: LcTool[]): LcItem[] {
  const creds: LcCredential[] = VAULT?.credentials || [], used = new Set<string>();
  const items: LcItem[] = rows.map((row): LcItem => {
    const bots = new Map<string, LcBotAccess>(), people = new Map<string, LcPersonAccess>(), issues: LcIssue[] = [];
    for (const conn of row.connections || []) {
      const c = {...conn, bot: lcBotId(conn.bot)}, was = bots.get(c.bot);
      const level = c.write || row.writes;
      if (!was || Number(LC_LEVEL_RANK[level]) > Number(LC_LEVEL_RANK[was.level])) bots.set(c.bot, {level, problem: c.problem || was?.problem || ''});
      else if (c.problem && !was.problem) was.problem = c.problem;
      if (c.problem) issues.push({bot: c.bot, text: c.problem});
    }
    for (const bot of (row.bots || []).map(lcBotId)) if (!bots.has(bot)) bots.set(bot, {level: row.writes, problem: ''});
    const mine: LcCredential[] = row.status === 'removed' ? [] : vaultForIntegration(row);
    for (const cred of mine) {
      used.add(cred.id);
      if (!cred.stored) issues.push({text: `${cred.name}: no value stored`});
      for (const g of cred.grants || []) {
        const [kind, id = ''] = g.subject.split(':');
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
    const bots = new Map<string, LcBotAccess>(), people = new Map<string, LcPersonAccess>();
    for (const g of cred.grants || []) {
      const [kind, id = ''] = g.subject.split(':');
      if (kind === 'bot') bots.set(id, {level: '', problem: ''});
      if (kind === 'human') people.set(id, {level: ''});
    }
    items.push({key: 'credentials/' + cred.id, cred, title: cred.name, kind: LC_CRED_KIND[cred.kind] || cred.kind, icon: 'key_vertical',
                group: 'Credentials', bots, people, creds: [cred], issues: cred.stored ? [] : [{text: 'No value stored'}],
                text: [cred.name, cred.env, cred.username, cred.source, 'credential key'].join(' ').toLowerCase()});
  }
  return items;
}

// Everyone in a group and the groups under it: {bots, people}. Tools' Who has access narrows by a team this way.
function lcGroupMembers(groups: LcGroup[], gid: string): {bots: Set<string>; people: Set<string>} {
  const bots = new Set<string>(), people = new Set<string>();
  const walk = (id: string): void => {
    const g = groups.find(x => x.id === id);
    if (!g) return;
    (g.bots || []).forEach(b => bots.add(b)); (g.people || []).forEach(p => people.add(p));
    groups.filter(x => x.parent === id).forEach(x => walk(x.id));
  };
  walk(gid);
  return {bots, people};
}

// ----------------------------------------------------------------- one tool, or one credential
// Who has it: one line a teammate, under the team they are in (folded when there are many), each with what they may
// change there and a red dot when their entry has a problem.
function lcAccessHTML(item: LcItem): string {
  if (!LC) throw new Error('Tool page: no page is open');
  const groups = LC.groups || [], teamOf = new Map<string, LcGroup>(), who = LC.who;
  const path = (g: LcGroup): string => {
    const names: string[] = []; let x: LcGroup | undefined = g, i = 0;
    while (x && i++ < 8) { names.unshift(x.name); x = groups.find(y => y.id === x!.parent); }
    return names.join(' › ');
  };
  for (const g of groups) { (g.bots || []).forEach(b => teamOf.set('b:' + b, g)); (g.people || []).forEach(p => teamOf.set('p:' + p, g)); }
  const lines: LcLine[] = [
    ...[...item.people].map(([id, a]) => ({team: teamOf.get('p:' + id), sort: lcPerson(id).name || id, html: `<a class="lc-who-row" href="#/person/${encodeURIComponent(id)}">${personAvatar(lcPerson(id), 20)}<span class="lc-wn">${esc(lcPerson(id).name || id)}</span>${a.level ? toolWritePill(a.level) : ''}</a>`})),
    ...[...item.bots].map(([slug, a]) => ({team: teamOf.get('b:' + slug), sort: lcBotName(slug), html: `<a class="lc-who-row" href="#/bot/${encodeURIComponent(slug)}"${a.problem ? ` title="${esc(a.problem)}"` : ''}>${avatar(slug, 20)}<span class="lc-wn">${esc(lcBotName(slug))}</span>${a.problem ? `<span class="lc-dot" role="img" aria-label="${esc(a.problem)}"></span>` : ''}${a.via === 'credential' ? '<span class="muted lc-via">key</span>' : ''}${a.level ? toolWritePill(a.level) : ''}</a>`})),
  ];
  if (!lines.length) return '<p class="muted lc-none">Nobody has it.</p>';
  const byTeam = new Map<string, LcLine[]>();
  for (const line of lines) {
    const label = line.team ? path(line.team) : 'No team';
    if (!byTeam.has(label)) byTeam.set(label, []);
    byTeam.get(label)!.push(line);
  }
  const teams = [...byTeam].sort(([a], [b]) => Number(a === 'No team') - Number(b === 'No team') || a.localeCompare(b, undefined, {numeric: true}));
  // Many teammates: each team folds; the team Tools was narrowed to (and the teams under it) open, else the first.
  const many = lines.length > 24, picked = who.startsWith('group:') ? groups.find(g => g.id === who.slice(6)) : null;
  const opened = picked ? new Set([...byTeam.keys()].filter(label => label === path(picked) || label.startsWith(path(picked) + ' › '))) : null;
  const sorted = (xs: LcLine[]): string => xs.sort((a, b) => a.sort.localeCompare(b.sort, undefined, {numeric: true})).map(x => x.html).join('');
  const [only] = teams;
  if (teams.length === 1 && only && only[0] === 'No team') return sorted(only[1]);   // no teams to tell apart
  return teams.map(([label, xs], i) => `<details class="lc-team"${!many || (opened?.size ? opened.has(label) : i === 0) ? ' open' : ''}><summary><span>${esc(label)}</span><span class="muted">${xs.length}</span></summary>
    ${sorted(xs)}</details>`).join('');
}
// One stored credential: name and variable, whether a value is stored, when a bot last used it, and what may be done.
function lcCredHTML(cred: LcCredential): string {
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
function lcEnvHTML(env: string | undefined, row: LcTool): string {
  const conns = (row.connections || []).filter(c => c.env === env);
  const [word, tone] = conns.some(c => c.problem) ? ['Missing', 'fail'] : conns.some(c => c.status === 'ready') ? ['On computer', 'ok'] : ['Not set', ''];
  return `<div class="lc-cred"><span class="nav-icon" aria-hidden="true">key_vertical</span>
    <span class="lc-cn"><b class="mono">${esc(env)}</b></span>
    <span class="pill ${tone}">${esc(word)}</span><span class="lc-used"></span>
    <span class="lc-acts">${VAULT?.can_manage ? `<button class="linkish" type="button" data-lc-add-env="${esc(env)}">Add</button>` : ''}</span></div>`;
}
function lcIssuesHTML(item: LcItem): string {
  if (!item.issues.length) return '';
  // The same problem on many bots is one line, with who has it.
  const byText = new Map<string, string[]>();
  for (const issue of item.issues) {
    if (!byText.has(issue.text)) byText.set(issue.text, []);
    if (issue.bot) byText.get(issue.text)!.push(issue.bot);
  }
  return `<section class="card lc-issues-card"><header><h2>Issues <span class="muted">${item.issues.length}</span></h2></header>${[...byText].map(([text, bots]) =>
    `<div class="lc-issue"><span class="lc-dot" aria-hidden="true"></span><span>${esc(text)}${bots.length ? ` <span class="muted">· ${esc(bots.slice(0, 3).map(lcBotName).join(', '))}${bots.length > 3 ? ` +${bots.length - 3}` : ''}</span>` : ''}</span></div>`).join('')}</section>`;
}
function lcFactsHTML(item: LcItem | undefined): string {
  if (!item) return '';
  const row = item.row;
  if (!row) throw new Error('Tool page: this item has no tool');
  const n = item.bots.size + item.people.size;
  const held = new Set(item.creds.map(c => c.env).filter(Boolean));
  const missing = row.status === 'removed' ? [] : lcEnvs(row).filter(env => !held.has(env));
  const creds = item.creds.map(lcCredHTML).join('') + missing.map(env => lcEnvHTML(env, row)).join('');
  return lcIssuesHTML(item)
    + `<section class="card lc-access"><header><h2>Access <span class="muted">${n}</span></h2></header>${lcAccessHTML(item)}</section>`
    + (creds ? `<section class="card lc-creds"><header><h2>Keys</h2></header>${creds}</section>` : '');
}
// The facts' buttons open the vault's own dialogs (ui/app/vault.ts); a saved change redraws them (lcVaultChanged).
function lcWireFacts(): void {
  const host = lcFacts();
  if (!host) return;
  host.onclick = async ev => {
    const b = eventElement(ev)?.closest('button');
    if (!b) return;
    const cred = (id: string): VaultCredential | undefined => VAULT?.credentials.find(c => c.id === id);
    if (b.dataset.vaultEdit) vaultEdit(cred(b.dataset.vaultEdit));
    else if (b.dataset.vaultShare) vaultShare(b.dataset.vaultShare);
    else if (b.dataset.vaultReveal) vaultReveal(b.dataset.vaultReveal);
    else if (b.dataset.vaultDelete) { if (await askConfirm(`Delete ${cred(b.dataset.vaultDelete)?.name || 'this credential'} and every grant of it?`, b)) void vaultDelete(b.dataset.vaultDelete); }
    else if (b.dataset.lcAddEnv) {
      const cur = LC;
      if (!cur) throw new Error('Tool page: no page is open');
      const row = cur.items.find(i => i.key === cur.key)?.row;
      vaultEdit({name: row?.title || '', env: b.dataset.lcAddEnv, source: row?.service || '', kind: /TOKEN/.test(b.dataset.lcAddEnv) ? 'token' : 'api_key'});
    }
  };
}
// The connections an owner makes here: the GitHub App on GitHub's page, the Slack workspace and its channels on Slack's.
type LcCard = [string, string, ((host: HTMLElement | null) => Promise<void>)?];
function lcMountConnect(service: string): void {
  const cards: LcCard[] = {
    github: S.me?.role === 'owner' ? [['GitHub App', 'set-github', window.mountGithubConnect] satisfies LcCard] : [],
    slack: [...(S.me?.role === 'owner' ? [['Slack workspace', 'set-slack', window.mountSlackConnect] satisfies LcCard] : []),
            ...(S.me?.bot_admin ? [['Slack channels', 'set-slack-channels', window.mountSlackChannels] satisfies LcCard] : [])],
  }[service] || [];
  const facts = lcFacts();
  if (!facts || !cards.length) return;
  facts.insertAdjacentHTML('afterend', cards.map(([title, id]) => `<section class="card lc-connect"><header><h2>${esc(title)}</h2></header><div id="${id}">${skelRows(1, {card: true})}</div></section>`).join(''));
  for (const [, id, mount] of cards) mount?.($('#' + id));
}
// A key no tool names (or any key, on its own page): its facts, who has it, and what may be done.
function lcDrawCredential(id: string, back: string): void {
  if (!LC) throw new Error('Tool page: no page is open');
  const item = LC.items.find(i => i.key === 'credentials/' + id), cred = item?.cred;
  const detail = lcDetail();
  if (!item || !cred) { detail.innerHTML = `<div class="meeting-head">${back}<div><h1>Key</h1></div></div><div class="empty" data-lc-gone>No key here, or you cannot open it.</div>`; return; }
  const row = (dt: string, dd: string | undefined): string => dd ? `<dt>${dt}</dt><dd>${dd}</dd>` : '';
  const grants = (cred.grants || []).map(g => {
    const [kind, gid = ''] = g.subject.split(':');
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

// A vault dialog saved a change (ui/app/vault.ts vaultLoad): the Tools list's keys and the open page follow.
function lcVaultChanged(): void {
  toolsVaultChanged();
  if (!LC?.rows || !document.body.contains(LC.root)) return;
  LC.items = lcModel(LC.rows);
  const key = LC.key;
  if (key.startsWith('credentials/')) {
    // A deleted key leaves nothing to show: back to the keys on Tools.
    if (!LC.items.some(i => i.key === key)) { location.replace(TOOLS_ROUTE + '?show=keys'); return; }
    lcDrawCredential(key.slice(12), '');
    lcKeyCrumb(LC);
    return;
  }
  const facts = lcFacts(), item = LC.items.find(i => i.key === key);
  if (facts && item) { facts.innerHTML = lcFactsHTML(item); }
}
// What every page here reads: the tools bots declare, the vault (for whoever may open it) and the teams.
async function lcRead(state: LcState, tools = true): Promise<void> {
  const [rows, , groups] = await Promise.all([
    tools ? get<LcToolsResponse>('/v2/tools').then(r => r.integrations || []).catch(() => []) : Promise.resolve([]),
    vaultFetch().catch(() => null),
    get<LcGroup[] | {groups?: LcGroup[]}>('/v2/groups').catch(() => []),
  ]);
  state.rows = rows; state.groups = Array.isArray(groups) ? groups : groups?.groups || [];
}

// ----------------------------------------------------------------- a declared tool's own page
// The tool's page full width under Tools (#/tools/<service>): its issues, who has it and its keys, what its page says,
// and Slack's and GitHub's connect cards. A vault dialog's change redraws its facts (lcVaultChanged).
async function pageToolService(service: string): Promise<void> {
  // A redraw of the same page (route() again, a poll) keeps it, and what is typed in its forms.
  if (LC?.key === service && document.body.contains(LC.root)) return;
  $('#main').innerHTML =`<div class="tt tt-page" id="tool-service">
    <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-tl-crumb>${esc(service)}</span></nav>
    <div id="int-detail">${skelRows(4, {card: true})}</div>
    <dialog class="bot-editor" id="credential-dialog" aria-label="Credential"></dialog></div>`;
  // Opened from Tools narrowed to a team: that team opens first under Access.
  LC = {rows: null, groups: [], items: [], key: service, root: $('#tool-service') as HTMLElement, who: toolsWhoPicked()};
  const state = LC;
  await lcRead(state);
  if (LC !== state) return;
  state.items = lcModel(lcRows(state));
  await integrationDetail(service, ++INT_LOAD, {base: TOOLS_ROUTE,
    facts: () => lcFactsHTML(state.items.find(i => i.key === service)),
    after: (page: {service: string; title: string}) => {
      const crumb = state.root.querySelector<HTMLElement>('[data-tl-crumb]');
      if (crumb) crumb.textContent = page.title;
      lcWireFacts(); lcMountConnect(page.service);
      if (page.service === 'slack') void lcSlackLinks(state);
    }});
}

// ----------------------------------------------------------------- a key no tool holds
// #/tools/key/<id>: one stored key as its own page (what the Keys page drew beside its list). Its name, type, value
// preview and source; who it is granted to; Rotate, Access, Reveal and Delete through the vault's dialogs.
async function pageToolKey(id: string): Promise<void> {
  const key = 'credentials/' + id;
  if (LC?.key === key && document.body.contains(LC.root)) return;
  $('#main').innerHTML = `<div class="tt tt-page" id="tool-key">
    <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-tl-crumb>Key</span></nav>
    <div id="int-detail">${skelRows(3, {card: true})}</div>
    <dialog class="bot-editor" id="credential-dialog" aria-label="Credential"></dialog></div>`;
  LC = {rows: null, groups: [], items: [], key, root: $('#tool-key') as HTMLElement, who: ''};
  const state = LC;
  await lcRead(state, false);
  if (LC !== state) return;
  state.items = lcModel([]);   // every credential as its own item, whichever tool it belongs to
  lcDrawCredential(id, '');
  lcKeyCrumb(state);
}
const lcKeyCrumb = (state: LcState): void => {
  const crumb = state.root.querySelector<HTMLElement>('[data-tl-crumb]'), cred = state.items.find(i => i.key === state.key)?.cred;
  if (crumb && cred) crumb.textContent = cred.name;
};

// Slack: threads linked to Slack channels (admins only; the rows are ui/app/thread-connect.ts's).
async function lcSlackLinks(state: LcState): Promise<void> {
  const facts = lcFacts();
  if (!facts || !tcnAdmin()) return;
  const live = (): boolean => LC === state && sec.isConnected;
  const sec = document.createElement('section');
  sec.className = 'card cn-sec'; sec.id = 'tool-slack-links';
  sec.innerHTML = `<header><h2>Linked threads</h2>
      <button type="button" class="ghost small settings-head-act" data-cn-open aria-expanded="false">Link a thread</button></header>
    <div class="cn-form" hidden><select name="thread" aria-label="Thread"></select>
      <button type="button" class="ghost small" data-cn-threads hidden>More threads</button>
      <input name="channel" placeholder="#channel" aria-label="Slack channel" maxlength="200" autocomplete="off">
      <label class="tcn-check"><input type="checkbox" name="history">Copy history</label>
      <button type="button" class="primary small" data-cn-link>Link</button></div>
    <span class="err cn-err" role="status"></span>
    <ul class="cn-list" aria-label="Slack links"><li>${skelRows(2)}</li></ul>
    <div class="row cn-xmore" hidden><button class="ghost" type="button">More</button></div>`;
  (state.root.querySelector('.lc-connect:last-of-type') || facts).after(sec);
  const list = sec.querySelector<HTMLElement>('.cn-list')!, more = sec.querySelector<HTMLElement>('.cn-xmore')!, err = sec.querySelector<HTMLElement>('.cn-err')!;
  const form = sec.querySelector<HTMLElement>('.cn-form')!, pick = form.querySelector<HTMLSelectElement>('[name=thread]')!;
  const openBtn = sec.querySelector<HTMLButtonElement>('[data-cn-open]')!, threadsBtn = form.querySelector<HTMLButtonElement>('[data-cn-threads]')!;
  const chanInput = form.querySelector<HTMLInputElement>('[name=channel]')!, histBox = form.querySelector<HTMLInputElement>('[name=history]')!;
  const linkBtn = form.querySelector<HTMLButtonElement>('[data-cn-link]')!;
  const failed = (e: unknown): void => { err.textContent = errorMessage(e, ''); };
  let rows: LcSlackLink[] = [], cursor = '';
  const draw = async (): Promise<void> => {
    const titles = await Promise.all(rows.map(l => tcnThreadTitle(l.thread_id)));
    if (!live()) return;
    list.innerHTML = rows.map((l, i) => tcnSlackRowHTML(l, titles[i])).join('') || '<li class="muted cn-none">No linked threads</li>';
  };
  const load = async (fresh = false): Promise<void> => {
    const data = await get<LcSlackPage>('/v2/slack/links?limit=50' + (!fresh && cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
    if (!live()) return;
    rows = (fresh ? [] : rows).concat(data.links || []);
    cursor = data.next_cursor || '';
    openBtn.hidden = data.available === false;
    more.hidden = !cursor;
    await draw();
  };
  more.querySelector<HTMLButtonElement>('button')!.onclick = () => void load().catch(failed);
  // The threads a channel can be linked to: roots open to the company or to their members, a page at a time.
  let threadCursor = '', threadsRead = false;
  const offered = new Set<string>();
  const loadThreads = async (): Promise<void> => {
    const r = await get<LcThreadsPage>('/v2/threads?limit=50' + (threadCursor ? '&cursor=' + encodeURIComponent(threadCursor) : ''), {fresh: true});
    threadsRead = true;
    threadCursor = r.next_cursor || '';
    const linked = new Set(rows.map(l => l.thread_id));
    const add = [...(r.attention || []), ...(r.threads || [])]
      .filter(t => tcnSlackLinkable(t) && !t.archived_at && !offered.has(t.id) && !linked.has(t.id));
    add.forEach(t => offered.add(t.id));
    pick.insertAdjacentHTML('beforeend', add.map(t => `<option value="${esc(t.id)}">${esc(threadTitle(t))}</option>`).join(''));
    threadsBtn.hidden = !threadCursor;
    if (!offered.size && !threadCursor) pick.innerHTML = '<option value="" disabled selected>No threads to link</option>';
  };
  const toggle = (open: boolean): void => { form.hidden = !open; openBtn.setAttribute('aria-expanded', String(open)); };
  openBtn.onclick = () => {
    toggle(form.hidden);
    if (form.hidden) return;
    if (!threadsRead) void loadThreads().catch(failed);
    chanInput.focus();
  };
  threadsBtn.onclick = () => void loadThreads().catch(failed);
  chanInput.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); linkBtn.click(); } };
  linkBtn.onclick = async () => {
    const b = linkBtn, channel = chanInput.value.trim();
    if (!pick.value) { err.textContent = 'Choose a thread'; return; }
    if (!channel) { err.textContent = 'Name a channel'; return; }
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/slack/links', {thread_id: pick.value, channel, history: histBox.checked});
      pick.selectedOptions[0]?.remove();
      chanInput.value = '';
      toggle(false);
      await load(true);
    } catch (e) { failed(e); }
    finally { b.disabled = false; }
  };
  list.onclick = async ev => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('button'); if (!b) return;
    const link = rows.find(l => l.id === b.closest<HTMLElement>('[data-link]')?.dataset.link); if (!link) return;
    const drop = b.matches('[data-slack-unlink]');
    if (drop && !(await askConfirm(`Unlink ${tcnChannel(link)}?`, b))) return;
    b.disabled = true; err.textContent = '';
    try { await tcnSlackAct(link, drop ? 'unlink' : 'resume'); await load(true); }
    catch (e) { failed(e); b.disabled = false; }
  };
  try { await load(); }
  catch (e) { if (live()) { list.innerHTML = ''; failed(e); } }
}

// Leaving Tools drops the list, so coming back reads the hub again.
function rdToolsStop(): void { LC = null; }
