/* ui/app/tools-website.ts — One website tool (#/tools/website/<id>[/activity]): the logins bots and people use on one
   site, through a browser on the company's browser host (or, unless the tool is central only, a caller's own computer).
   Reads GET /api/v2/website-tools/<id> ({tool, status, accounts, host, recent, can_manage, private, can_set_audience,
   audience, seen_by}). Overview: Accounts (one row each: Shared or Own, the username or the holder, a 2FA mark and who
   can use it; its ⋯ holds Show code, Open browser, Replace login, Who can use it, Reveal password · logged, Remove),
   Recent activity, Browser (the browser host, read only; Sites; Central only) and Who can see it (as an API tool's,
   saved through PATCH /api/v2/website-tools/<id> {audience}). Activity: GET …/activity, 50 a page.
   Writes: PATCH/DELETE /api/v2/website-tools/<id>; POST …/accounts; PATCH/DELETE …/accounts/<aid>;
   PUT …/accounts/<aid>/access/<subject> {on} (a person, bot or group; answers the account's access, which the rows
   repaint from); GET /api/v2/website-tools/groups (groups' member counts, for the who picker, ui/app/who-picker.ts); GET …/accounts/<aid>/code (Show code); POST /api/v2/credentials/<aid>/reveal
   (Reveal password, people only, logged). Add tool › Website (toolsWebsiteCreate) is POST /api/v2/website-tools, then
   the account, then its grants. Open browser is the live view (ui/app/browser-view.ts).
   Passwords and 2FA keys are write-only: a form never shows a stored one, and a blank field on Replace login keeps it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Who can use an account: a person, a bot or a group. A group carries its member count; a person or bot granted directly
// who is also in a granted group carries those groups' names (`via`).
interface TwWho {subject: string; kind: string; id: string; name?: string | null; initials?: string; members?: number; via?: string[]}
// A group as GET /api/v2/website-tools/groups lists it: its member count and, when sent, its members as actors.
interface TwGroup {id: string; name?: string; members?: number; actors?: string[]}
interface TwSession {state?: string; where?: string | null; computer?: string | null; by?: TwWho | null}
interface TwAccount {
  id: string; kind: string; username?: string | null; has_password?: boolean; has_totp?: boolean; all_bots?: boolean; holder?: TwWho | null; access?: TwWho[];
  session?: TwSession | null; can_use?: boolean; can_manage?: boolean;
}
interface TwHost {runner_id: string; label: string; online: boolean}
interface TwRow {id?: string; at?: string | null; who?: TwWho | null; verb: string; page?: string | null; account?: string; where?: string; result?: string}
interface TwTool {id?: string; name: string; kind?: string; sites?: string[]; central_only?: boolean}
interface TwData {
  tool: TwTool; status?: {label?: string; tone?: string}; accounts?: TwAccount[]; host?: TwHost | null; recent?: TwRow[];
  can_manage?: boolean; private?: boolean; can_set_audience?: boolean; audience?: ToolApiAudienceIn | null; seen_by?: ToolApiSeen[];
}
interface TwState {run: number; id: string; tab: string; data: TwData | null; act: {rows: TwRow[]; cursor: string}}

let TW: TwState | null = null;
const twPath = (id: string, rest = '') => `/v2/website-tools/${encodeURIComponent(id)}${rest}`;
const twAcctPath = (id: string, aid: string, rest = '') => twPath(id, `/accounts/${encodeURIComponent(aid)}${rest}`);
const twRoot = (): HTMLElement | null => document.getElementById('tool-website');
const twBody = (): HTMLElement | null => twRoot()?.querySelector<HTMLElement>('[data-tw-body]') ?? null;
// The hub's tones as tools.ts's: waiting → wait, neutral → idle.
const TW_TONES: Record<string, string> = {ok: 'ok', waiting: 'wait', wait: 'wait', fail: 'fail', neutral: 'idle', run: 'run'};
const twPill = (s?: {label?: string; tone?: string}) => toolsPill({tone: TW_TONES[s?.tone || ''] || 'idle', text: s?.label || ''});
const TW_VERBS: Record<string, string> = {open: 'opened', fill: 'filled', copy: 'copied', code: 'got code', needs_you: 'asked for you',
  control: 'took control', sign_in: 'signed in', download: 'downloaded', upload: 'uploaded'};
const TW_SESSION: Record<string, string> = {needs_you: 'Needs you', in_use: 'In use'};
const twVerb = (v: string) => TW_VERBS[v] || v;
const twWhere = (r: TwRow) => [r.account === 'own' ? 'own' : r.account ? 'shared' : '', r.where === 'own' ? 'own browser' : r.where ? 'central' : ''].filter(Boolean).join(' · ');
const twFace = (w: TwWho | null | undefined, size = 22) => w ? toolsFace({kind: w.kind === 'bot' || w.kind === 'group' ? w.kind : 'person', id: w.id, name: w.name || w.id}, size) : '';
const twName = (w: TwWho | null | undefined) => w ? w.name || w.id : '';
// An account's line: a shared login's username (the tool's name when it has none); an own one's holder.
const twAcctName = (a: TwAccount) => a.kind === 'own' ? twName(a.holder) || a.username || '' : a.username || TW?.data?.tool.name || '';
const twMine = (a: TwAccount) => a.kind === 'own' && a.holder?.subject === 'human:' + (S.me?.id || '');
// The account a person's Open browser uses: their own, else a shared one they can use.
const twOpenable = (d: TwData | null): TwAccount | undefined => (d?.accounts || []).filter(a => a.can_use).sort((a, b) => Number(twMine(b)) - Number(twMine(a)))[0];

// ----------------------------------------------------------------- the page
async function pageToolWebsite(id: string, tab?: string): Promise<void> {
  const run = (TW?.run || 0) + 1, same = TW?.id === id && twRoot();
  TW = {run, id, tab: tab || 'overview', data: same && TW ? TW.data : null, act: {rows: [], cursor: ''}};
  if (!same) {
    $('#main').innerHTML = `<div class="tt tt-page" id="tool-website" data-tool="${esc(id)}">
      <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-tw-crumb>${esc(id)}</span></nav>
      <div data-tw-head></div><div data-tw-body>${skelRows(4, {card: true})}</div></div>`;
  }
  const root = twRoot();
  if (!root) return;
  root.dataset.tab = TW.tab;
  if (!root.dataset.bound) { root.dataset.bound = '1'; root.addEventListener('click', ev => void twClick(ev)); root.addEventListener('change', ev => void twChange(ev)); }
  try { await twLoad(); }
  catch (e) { const body = twBody(); if (TW?.run === run && body) body.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  if (TW?.run !== run) return;
  twDrawHead();
  if (TW.tab === 'activity') await twActivity(true); else twDrawOverview();
}
async function twLoad(): Promise<void> {
  const state = TW;
  if (!state) return;
  const d: TwData = await get(twPath(state.id), {fresh: true});
  if (TW === state) state.data = d;
}
async function twRedraw(state: TwState): Promise<void> {
  await twLoad();
  if (TW !== state) return;
  twDrawHead();
  if (state.tab !== 'activity') twDrawOverview();
}
function twDrawHead(): void {
  const state = TW, root = twRoot();
  if (!state?.data || !root) return;
  const {tool, status, can_manage: manage} = state.data, base = '#/tools/website/' + encodeURIComponent(state.id);
  const crumb = root.querySelector('[data-tw-crumb]');
  if (crumb) crumb.textContent = tool.name;
  const head = root.querySelector('[data-tw-head]');
  if (!head) return;
  const site = (tool.sites || [])[0] || '';
  head.innerHTML = `<header class="tt-title"><span class="tt-tile lg" aria-hidden="true"><span class="nav-icon">travel_explore</span></span>
      <div class="tt-title-text"><h1>${esc(tool.name)}${state.data.private ? TOOLS_PRIVATE : ''}</h1>${site && site !== tool.name ? `<p class="muted mono">${esc(site)}</p>` : ''}</div>
      <div class="tt-title-act">${twPill(status)}${twOpenable(state.data) ? '<button type="button" class="ghost small" data-tw-open>Open browser</button>' : ''}${
        manage ? `<button type="button" class="ghost small tw-more" data-tw-tool-menu aria-haspopup="menu" aria-expanded="false" aria-label="More" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>` : ''}</div></header>
    <nav class="tt-tabs" aria-label="${esc(tool.name)}">${[['overview', base, 'Overview'], ['activity', base + '/activity', 'Activity']].map(([k, href, label]) =>
      `<a href="${href}" data-tw-tab="${k}"${state.tab === k ? ' class="ph-tab cur" aria-current="page"' : ' class="ph-tab"'}>${label}</a>`).join('')}</nav>`;
}

// ----------------------------------------------------------------- Overview
function twDrawOverview(): void {
  const d = TW?.data, body = twBody();
  if (!d || !body || formBusy(body)) return;
  body.innerHTML = `<div class="tt-cols"><div class="tt-col">${twAccountsCard(d)}${twRecentCard(d)}</div>
    <div class="tt-col">${twBrowserCard(d)}${d.can_set_audience ? twSeeCard(d) : ''}</div></div>`;
}
function twAccountsCard(d: TwData): string {
  const rows = (d.accounts || []).map(a => {
    const st = a.session?.state || '', said = TW_SESSION[st];
    const by = st === 'in_use' && a.session?.by ? ` · ${twName(a.session.by)}` : '';
    const right = a.kind === 'own' ? `<span class="muted tw-acct-user">${esc(a.username || '')}</span>`
      : `${a.all_bots ? '<span class="pill" data-tw-all-bots-label>All bots</span>' : ''}<span class="tt-faces">${(a.access || []).slice(0, 4).map(w => `<span class="tt-face" title="${esc(toolsFaceTitle({kind: w.kind, id: w.id, name: twName(w), members: w.members}))}">${twFace(w)}</span>`).join('')}</span>${
        (a.access || []).length > 4 ? `<span class="muted tt-more">+${(a.access || []).length - 4}</span>` : ''}`;
    return `<div class="tw-acct" data-tw-account="${esc(a.id)}" data-kind="${esc(a.kind)}">
      <span class="pill tw-kind${a.kind === 'own' ? '' : ' in-progress'}">${a.kind === 'own' ? 'Own' : 'Shared'}</span>
      <span class="tw-acct-name">${a.kind === 'own' ? twFace(a.holder) : ''}<span class="tw-acct-text">${esc(twAcctName(a))}</span>${
        a.has_totp ? '<span class="nav-icon tw-2fa" data-tw-2fa role="img" aria-label="2FA" title="2FA">lock</span>' : ''}${
        said ? `<span class="tw-ses" data-state="${esc(st)}" role="img" aria-label="${esc(said + by)}" title="${esc(said + by)}"></span>` : ''}</span>
      <span class="tw-acct-who">${right}</span>
      <button type="button" class="ghost tw-more" data-tw-acct-menu="${esc(a.id)}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${esc(twAcctName(a))}" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button></div>`;
  }).join('');
  return `<section class="card" id="tool-accounts"><header><h2>Accounts</h2><button type="button" class="ghost small settings-head-act" data-tw-acct-add>Add</button></header>
    ${rows ? `<div class="tw-accts">${rows}</div>` : '<p class="muted" data-tw-no-accounts>No accounts yet.</p>'}</section>`;
}
function twRecentCard(d: TwData): string {
  const base = '#/tools/website/' + encodeURIComponent(TW?.id || '');
  const rows = (d.recent || []).map(r => `<li class="tw-rec" data-tw-row="${esc(r.id || '')}"><span class="muted tnum" title="${esc(fmt(r.at))}">${esc(ago(r.at))}</span>${twFace(r.who)}
      <span class="tw-rec-text"><b>${esc(twName(r.who))}</b> ${esc(twVerb(r.verb))}${r.page ? ` <span class="mono">${esc(r.page)}</span>` : ''}${
        twWhere(r) ? ` <span class="muted">· ${esc(twWhere(r))}</span>` : ''}</span></li>`).join('');
  return `<section class="card" id="tool-recent"><header><h2>Recent activity</h2><a class="ghost small settings-head-act" href="${base}/activity">See all</a></header>
    ${rows ? `<ul class="tw-recent">${rows}</ul>` : '<p class="muted">Nothing yet.</p>'}</section>`;
}
function twBrowserCard(d: TwData): string {
  const host = d.host, manage = !!d.can_manage, sites = d.tool.sites || [];
  const hostLine = host ? `<span class="tw-host" data-online="${host.online}"><span class="tw-host-dot" role="img" aria-label="${host.online ? 'Online' : 'Offline'}" title="${host.online ? 'Online' : 'Offline'}"></span>${esc(host.label)}</span>`
    : '<span class="muted" data-tw-no-host>None</span>';
  return `<section class="card" id="tool-browser"><header><h2>Browser</h2></header>
    <dl class="tt-kv"><dt>Runs on</dt><dd>${hostLine}</dd>
      <dt>Sites</dt><dd class="tw-sites">${sites.map(s => `<span class="mono">${esc(s)}</span>`).join('')}${manage ? '<button type="button" class="linkish" data-tw-sites>Edit</button>' : ''}</dd></dl>
    <label class="tw-central"><input type="checkbox" role="switch" class="people-switch" data-tw-central${d.tool.central_only ? ' checked' : ''}${manage ? '' : ' disabled'}> Central only</label></section>`;
}
// Who can see it: as an API tool's (ui/app/tools-api.ts taSeeCard), saved on this tool.
function twSeeCard(d: TwData): string {
  const only = !!d.private, me = 'human:' + (S.me?.id || ''), owner = (d.seen_by || []).some(r => r.why === 'Owner' && r.subject === me);
  const choice = ([['everyone', 'Everyone', !only], ['only', 'Only listed', only]] as const).map(([k, label, on]) =>
    `<button type="button" class="${on ? 'primary' : 'ghost'} small" data-tw-see="${k}" aria-pressed="${on}">${label}</button>`).join('');
  const rows = (d.seen_by || []).map(r => `<tr data-tw-seen="${esc(r.subject)}" data-why="${esc(r.why)}">
      <td class="tt-who-name">${taFace(r)}<span>${esc(r.name || r.id || '')}${r.kind === 'bot' ? ' <span class="muted">· bot</span>' : ''}</span></td>
      <td class="muted">${esc(r.why)}</td>
      <td class="mb-act">${r.why === 'Listed' && TA_LISTS[r.kind] && (owner || r.subject !== me)
        ? `<button type="button" class="ghost tt-x" data-tw-unlist="${esc(r.subject)}" aria-label="Remove ${esc(r.name || r.id || '')}" title="Remove">×</button>` : ''}</td></tr>`).join('');
  return `<section class="card" id="tool-see"><header><h2>Who can see it</h2>${only ? '<button type="button" class="ghost small settings-head-act" data-tw-see-add>Add</button>' : ''}</header>
    <div class="row tt-see-choice" role="group" aria-label="Who can see it">${choice}</div>
    ${only ? `<table class="settings-table tt-who-table tt-see-table"><thead><tr><th>Who</th><th>Why</th><th aria-hidden="true"></th></tr></thead><tbody>${rows}</tbody></table>` : ''}</section>`;
}
const twAudience = (d: TwData | null): ToolApiAudience => {
  const a: ToolApiAudienceIn = d?.audience || {};
  return {everyone: false, people: [...(a.people || [])], teams: [...(a.teams || [])], bots: [...(a.bots || [])]};
};
const twSaveAudience = (id: string, audience: import('../types/api').AccessAudience | null) => patch(twPath(id), {audience: audience && !audience.everyone ? audience : null});
async function twSeeAdd(state: TwState): Promise<void> {
  const level = twAudience(state.data);
  try {
    const groups = await get('/v2/groups');
    await accessEditor({name: state.data?.tool.name, singleAudience: true,
      initialData: {see: level, read: level, write: level, teams: Array.isArray(groups) ? groups : groups.groups || []},
      save: (a: import('../types/api').AccessAudience | null) => twSaveAudience(state.id, a),
      after: () => twRedraw(state)} as Parameters<typeof accessEditor>[0]);
  } catch (e) { toast(errorMessage(e, ''), true); }
}

// ----------------------------------------------------------------- Activity
async function twActivity(fresh: boolean): Promise<void> {
  const state = TW, body = twBody();
  if (!state || !body) return;
  if (fresh) {
    body.innerHTML = `<div data-tw-log>${skelRows(4)}</div><div class="row" data-tw-more hidden><button type="button" class="ghost" data-tw-more-btn>More</button></div>`;
    state.act = {rows: [], cursor: ''};
  }
  const q = new URLSearchParams({limit: '50'});
  if (state.act.cursor) q.set('cursor', state.act.cursor);
  let d: {rows?: TwRow[]; next?: string | null};
  const log = body.querySelector('[data-tw-log]');
  try { d = await get(twPath(state.id, '/activity?' + q), {fresh: true}); }
  catch (e) { if (TW === state && log) log.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  if (TW !== state || !body.isConnected || !log) return;
  state.act.rows = state.act.rows.concat(d.rows || []);
  state.act.cursor = d.next || '';
  const rows = state.act.rows;
  log.innerHTML = rows.length ? `<table class="tt-log tw-log"><thead><tr><th>Time</th><th>Who</th><th>What</th><th>Page</th><th>Where</th></tr></thead>
    <tbody>${rows.map(r => `<tr data-tw-row="${esc(r.id || '')}"><td class="tnum" title="${esc(fmt(r.at))}">${esc(ago(r.at))}</td>
      <td class="tt-who-name">${twFace(r.who)}<span>${esc(twName(r.who))}</span></td>
      <td>${esc(twVerb(r.verb))}${r.result === 'refused' || r.result === 'failed' ? ` <span class="pill fail" data-result="${esc(r.result)}">${r.result === 'refused' ? 'Refused' : 'Failed'}</span>` : ''}</td>
      <td class="mono tt-call">${esc(r.page || '')}</td><td class="muted">${esc(twWhere(r))}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty" data-tw-none>Nothing yet.</div>';
  const more = body.querySelector<HTMLElement>('[data-tw-more]');
  if (more) more.hidden = !state.act.cursor;
}

// ----------------------------------------------------------------- menus
// One ⋯ menu at a time, a popover placed under its button (a bottom sheet on a phone), as Subscriptions' is.
function twMenu(anchor: HTMLElement, label: string, items: [string, string, boolean?][], act: (key: string) => void): void {
  let pop = document.getElementById('tw-menu');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (!pop) {
    const made = document.createElement('div');
    made.id = 'tw-menu'; made.className = 'tl-pop tw-menu'; made.popover = 'auto';
    made.addEventListener('toggle', ev => {
      if ((ev as ToggleEvent).newState !== 'closed') return;
      const from = made.anchor;
      from?.setAttribute('aria-expanded', 'false');
      if (from instanceof HTMLElement && from.isConnected && (made.contains(document.activeElement) || document.activeElement === document.body)) from.focus();
    });
    document.body.append(made);
    pop = made;
  }
  const menu = pop;
  menu.anchor = anchor;
  menu.innerHTML = `<div class="tl-menu" role="menu" aria-label="${esc(label)}">${items.map(([k, text, danger]) =>
    `<button type="button" class="tl-mi${danger ? ' danger' : ''}" role="menuitem" data-tw-act="${esc(k)}">${esc(text)}</button>`).join('')}</div>`;
  menu.onclick = ev => {
    const key = eventElement(ev)?.closest<HTMLElement>('[data-tw-act]')?.dataset.twAct;
    if (!key) return;
    menu.hidePopover();
    act(key);
  };
  menu.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(menu, anchor);
  menu.querySelector<HTMLElement>('.tl-mi')?.focus();
}
function twToolMenu(state: TwState, anchor: HTMLElement): void {
  twMenu(anchor, state.data?.tool.name || 'Website', [['rename', 'Rename'], ['sites', 'Sites'], ['delete', 'Delete', true]], key => {
    if (key === 'rename') twRenameDialog(state);
    else if (key === 'sites') twSitesDialog(state);
    else void twDelete(state);
  });
}
function twAcctMenu(state: TwState, a: TwAccount, anchor: HTMLElement): void {
  const items: [string, string, boolean?][] = [];
  if (a.has_totp && (a.can_use || a.can_manage)) items.push(['code', 'Show code']);
  if (a.can_use) items.push(['open', 'Open browser']);
  if (a.can_manage) {
    items.push(['replace', 'Replace login']);
    if (a.kind === 'shared') items.push(['access', 'Who can use it']);
  }
  // The hub lets whoever uses the account, or manages it, read its password (people only, logged).
  if (a.has_password !== false && (a.can_use || a.can_manage)) items.push(['reveal', 'Reveal password · logged']);
  if (a.can_manage) items.push(['remove', 'Remove', true]);
  if (!items.length) return;
  twMenu(anchor, twAcctName(a), items, key => {
    if (key === 'code') twCodeDialog(state, a);
    else if (key === 'open') twOpenBrowser(state, a);
    else if (key === 'replace') twReplaceDialog(state, a);
    else if (key === 'access') twAccessDialog(state, a);
    else if (key === 'reveal') void twReveal(a);
    else void twRemove(state, a);
  });
}

// ----------------------------------------------------------------- actions
async function twClick(ev: Event): Promise<void> {
  const b = eventElement(ev)?.closest<HTMLButtonElement>('button');
  const state = TW;
  if (!b || !state?.data || b.disabled) return;
  const write = async (run: () => Promise<unknown>) => {
    b.disabled = true;
    try { await run(); await twRedraw(state); }
    catch (e) { toast(errorMessage(e, ''), true); }
    finally { if (b.isConnected) b.disabled = false; }
  };
  if (b.hasAttribute('data-tw-more-btn')) return twActivity(false);
  if (b.hasAttribute('data-tw-tool-menu')) return twToolMenu(state, b);
  if (b.dataset.twAcctMenu) {
    const a = (state.data.accounts || []).find(x => x.id === b.dataset.twAcctMenu);
    if (a) twAcctMenu(state, a, b);
    return;
  }
  if (b.hasAttribute('data-tw-open')) { const a = twOpenable(state.data); if (a) twOpenBrowser(state, a); return; }
  if (b.hasAttribute('data-tw-acct-add')) return twAccountDialog(state);
  if (b.hasAttribute('data-tw-sites')) return twSitesDialog(state);
  if (b.dataset.twSee && b.getAttribute('aria-pressed') !== 'true') {
    if (b.dataset.twSee === 'only') return write(() => twSaveAudience(state.id, twAudience(state.data)));
    if (!await settingsConfirm({title: 'Show to everyone?', text: 'Credential admins can see it and its accounts.', ok: 'Everyone', danger: false})) return;
    return write(() => twSaveAudience(state.id, null));
  }
  if (b.hasAttribute('data-tw-see-add')) return twSeeAdd(state);
  if (b.dataset.twUnlist) {
    const row = (state.data.seen_by || []).find(r => r.subject === b.dataset.twUnlist), audience = twAudience(state.data);
    const list = row ? TA_LISTS[row.kind] : undefined;
    if (!row || !list) return;
    audience[list] = audience[list].filter(x => x !== row.id);
    return write(() => twSaveAudience(state.id, audience));
  }
}
async function twChange(ev: Event): Promise<void> {
  const box = eventElement(ev)?.closest<HTMLInputElement>('[data-tw-central]');
  const state = TW;
  if (!box || !state) return;
  box.disabled = true;
  try { await patch(twPath(state.id), {central_only: box.checked}); await twRedraw(state); }
  catch (e) { box.checked = !box.checked; toast(errorMessage(e, ''), true); }
  finally { if (box.isConnected) box.disabled = false; }
}
function twOpenBrowser(state: TwState, a: TwAccount): void {
  browserView.open({toolId: state.id, accountId: a.id, title: state.data?.tool.name || ''});
}
async function twRemove(state: TwState, a: TwAccount): Promise<void> {
  if (!await settingsConfirm({title: `Remove ${twAcctName(a) || 'this account'}?`, text: 'Bots and people stop signing in with it.', ok: 'Remove'})) return;
  try { await writeRequest('DELETE', twAcctPath(state.id, a.id)); toast('Removed'); await twRedraw(state); }
  catch (e) { toast(errorMessage(e, ''), true); }
}
async function twDelete(state: TwState): Promise<void> {
  const name = state.data?.tool.name || 'this website';
  if (!await settingsConfirm({title: `Delete ${name}?`, text: 'Its accounts and activity go too.', ok: 'Delete'})) return;
  try { await writeRequest('DELETE', twPath(state.id)); toast('Deleted'); location.hash = TOOLS_ROUTE; }
  catch (e) { toast(errorMessage(e, ''), true); }
}
function twRenameDialog(state: TwState): void {
  const dialog = whDialog('Rename', `<label>Name<input name="name" maxlength="80" required autocomplete="off" value="${esc(state.data?.tool.name || '')}"></label>`,
    '<button class="primary" type="submit">Save</button>');
  whSubmit(dialog, async (data: FormData) => {
    await patch(twPath(state.id), {name: String(data.get('name') || '').trim()});
    dialog.close(); toast('Saved');
    await twRedraw(state);
  });
}
const twSiteList = (text: string) => text.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
function twSitesDialog(state: TwState): void {
  const dialog = whDialog('Sites', `<label>Sites<textarea name="sites" rows="4" required spellcheck="false" autocomplete="off" aria-label="Sites">${esc((state.data?.tool.sites || []).join('\n'))}</textarea></label>`,
    '<button class="primary" type="submit">Save</button>');
  whSubmit(dialog, async (data: FormData) => {
    const sites = twSiteList(String(data.get('sites') || ''));
    if (!sites.length) throw new Error('Add a site.');
    await patch(twPath(state.id), {sites});
    dialog.close(); toast('Saved');
    await twRedraw(state);
  });
}

// Show code: the current 2FA code, read again when it runs out. Nothing about it is kept or logged here.
function twCodeDialog(state: TwState, a: TwAccount): void {
  const dialog = whDialog('Code', `<p class="tw-code mono" data-tw-code aria-live="polite">…</p><p class="muted tnum tw-code-left" data-tw-left></p>`,
    '<button class="primary" type="button" data-tw-copy disabled>Copy</button>');
  let code = '', left = 0, timer = 0;
  const shown = dialog.querySelector<HTMLElement>('[data-tw-code]'), until = dialog.querySelector<HTMLElement>('[data-tw-left]');
  const copy = dialog.querySelector<HTMLButtonElement>('[data-tw-copy]');
  const tick = () => {
    if (!dialog.open) return;
    if (left <= 0) { void read(); return; }
    if (until) until.textContent = `${left} s`;
    left -= 1;
    timer = window.setTimeout(tick, 1000);
  };
  const read = async () => {
    try {
      const r: {code?: string; expires_in?: number} = await get(twAcctPath(state.id, a.id, '/code'), {fresh: true});
      if (!dialog.open) return;
      code = String(r.code || '');
      if (shown) shown.textContent = code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
      if (copy) copy.disabled = !code;
      left = Math.max(1, Math.round(Number(r.expires_in) || 30));
      tick();
    } catch (e) {
      if (!dialog.open) return;
      if (shown) shown.textContent = '';
      if (until) until.textContent = errorMessage(e, '');
    }
  };
  if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText(code); toast('Copied'); } catch { /* the code is on screen */ } };
  dialog.addEventListener('close', () => { clearTimeout(timer); code = ''; });
  void read();
}
// Reveal password: the vault's people-only, logged read (as an API key's Reveal), shown for a minute.
async function twReveal(a: TwAccount): Promise<void> {
  const dialog = whDialog('Password', '<p class="muted" data-tw-reveal-wait>Loading…</p>');
  try {
    const r: {value?: string; password?: string} = await post(`/v2/credentials/${encodeURIComponent(a.id)}/reveal`);
    if (!dialog.open) return;
    const value = String(r.value ?? r.password ?? '');
    const wait = dialog.querySelector('[data-tw-reveal-wait]');
    if (wait) wait.outerHTML = `<label>Password<input class="mono" readonly aria-label="Password" data-tw-revealed></label>
      <p class="muted">Logged.</p><button type="button" class="primary" data-tw-copy>Copy</button>`;
    const field = dialog.querySelector<HTMLInputElement>('[data-tw-revealed]');
    if (field) field.value = value;
    const copy = dialog.querySelector<HTMLButtonElement>('[data-tw-copy]');
    if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText(value); toast('Copied'); } catch { field?.select(); } };
    const shut = window.setTimeout(() => { if (dialog.open) dialog.close(); }, 60000);
    dialog.addEventListener('close', () => clearTimeout(shut));
  } catch (e) { const wait = dialog.querySelector('[data-tw-reveal-wait]'); if (dialog.open && wait) wait.textContent = errorMessage(e, ''); }
}

// ----------------------------------------------------------------- forms
// Who may hold an own account: me, and the bots I manage (any bot for an admin).
function twHolders(): [string, string][] {
  const me = S.me, admin = settingsIsAdmin();
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && (admin || (me && (e.owner_id === me.id || e.reports_to === 'human:' + me.id))));
  return [...(me ? [['human:' + me.id, 'Me'] as [string, string]] : []), ...bots.map(e => ['bot:' + e.id, botDisplayName(e.id)] as [string, string])];
}
// ----------------------------------------------------------------- who can use it: rows and the picker
const TW_KIND_RANK: Record<string, number> = {group: 0, person: 1, bot: 2};
const twSortWho = (list: TwWho[]) => [...list].sort((a, b) => (TW_KIND_RANK[a.kind] ?? 3) - (TW_KIND_RANK[b.kind] ?? 3) || twName(a).localeCompare(twName(b)));
// A row's muted text: "Bot", a group's member count, "Also in Design" (or "Also in 2 groups") for a direct grant a granted
// group covers too.
function twWhoSub(w: TwWho): string {
  const via = w.via || [], also = via.length === 1 ? `Also in ${via[0]}` : via.length > 1 ? `Also in ${via.length} groups` : '';
  if (w.kind === 'group') return toolsMembers(w.members);
  return [w.kind === 'bot' ? 'Bot' : '', also].filter(Boolean).join(' · ');
}
// One row: a 24px face, the name, its muted text, and a quiet × that shows on hover or focus (always on a touch screen).
const twWhoRow = (w: TwWho, remove: string) => `<div class="tw-access-row" data-tw-access-row="${esc(w.subject)}">${twFace(w, 24)}<span class="tw-who-n">${esc(twName(w))}</span>
  <span class="tw-who-sub">${esc(twWhoSub(w))}</span><button type="button" class="ghost tw-x" ${remove}="${esc(w.subject)}" aria-label="Remove ${esc(twName(w))}" title="Remove"><span class="nav-icon" aria-hidden="true">close</span></button></div>`;
const twWhoRows = (list: TwWho[], remove: string) => list.length ? twSortWho(list).map(w => twWhoRow(w, remove)).join('') : '<p class="muted tw-nobody">Nobody yet.</p>';
// Groups' member counts (and members, when sent), read once per dialog; until it answers a group shows no count.
async function twGroups(): Promise<Map<string, TwGroup>> {
  try {
    const rows: TwGroup[] = await get('/v2/website-tools/groups', {fresh: true});
    return new Map((Array.isArray(rows) ? rows : []).map(g => [g.id, g]));
  } catch { return new Map(); }
}
const twPersonName = (id: string) => { const p = (S.people || []).find(x => x.id === id); return p?.name || p?.email || id; };
// Who a pick makes: a TwWho for a subject, named from the roster.
function twWhoOf(subject: string, groups: Map<string, TwGroup>): TwWho {
  const [kind = '', id = ''] = subject.split(/:(.*)/s);
  if (kind === 'group') {
    const g = groups.get(id), name = g?.name || (S.orgGroups || []).find(x => x.id === id)?.name || id;
    return {subject, kind: 'group', id, name, ...(g?.members === undefined ? {} : {members: g.members})};
  }
  return {subject, kind: kind === 'bot' ? 'bot' : 'person', id, name: kind === 'bot' ? botDisplayName(id) : twPersonName(id)};
}
// Everyone not yet granted, for the picker: groups (with their member counts), people, bots. One already covered by a
// granted group stays, with "In Design", since a direct grant outlives the group's. With All bots on, no bot is offered.
function twCandidates(granted: TwWho[], groups: Map<string, TwGroup>, allBots = false): WhoCandidate[] {
  const taken = new Set(granted.map(w => w.subject));
  const covering = (subject: string) => granted.filter(w => w.kind === 'group' && (groups.get(w.id)?.actors || []).includes(subject)).map(w => twName(w));
  const inGroups = (subject: string) => { const g = covering(subject); return g.length === 1 ? `In ${g[0]}` : g.length > 1 ? `In ${g.length} groups` : ''; };
  const out: WhoCandidate[] = [];
  for (const g of S.orgGroups || []) {
    const subject = 'group:' + g.id;
    if (taken.has(subject)) continue;
    const w = twWhoOf(subject, groups);
    out.push({subject, kind: 'group', name: twName(w), face: twFace(w, 24), sub: toolsMembers(w.members)});
  }
  for (const p of S.people || []) {
    const subject = 'human:' + p.id;
    if (p.hidden || taken.has(subject)) continue;
    const name = p.name || p.email || p.id;
    out.push({subject, kind: 'person', name, email: p.email || undefined, face: toolsFace({kind: 'person', id: p.id, name}, 24), sub: inGroups(subject)});
  }
  for (const e of allBots ? [] : S.emps || []) {
    const subject = 'bot:' + e.id;
    if (e.status === 'archived' || taken.has(subject)) continue;
    out.push({subject, kind: 'bot', name: botDisplayName(e.id), face: avatar(e.id, 24), sub: ['Bot', inGroups(subject)].filter(Boolean).join(' · ')});
  }
  return out;
}
// The login fields: Shared or Own, whose, username, password, 2FA key and, for shared, who can use it.
function twLoginFields(canShare: boolean): string {
  const holders = twHolders();
  return `${canShare ? `<div class="theme-seg tw-seg" role="radiogroup" aria-label="Account">
      <button type="button" role="radio" aria-checked="true" data-tw-kind="shared">Shared</button><button type="button" role="radio" aria-checked="false" data-tw-kind="own">Own</button></div>` : ''}
    <input type="hidden" name="kind" value="${canShare ? 'shared' : 'own'}">
    <label data-tw-own${canShare ? ' hidden' : ''}>Whose<select name="holder">${holders.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join('')}</select></label>
    <label>Username<input name="username" maxlength="250" autocomplete="off" spellcheck="false" autocapitalize="off"></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" spellcheck="false"></label>
    <label>2FA key (optional)<input name="totp" type="password" autocomplete="off" spellcheck="false" placeholder="Setup key or otpauth:// link"></label>
    ${canShare ? `<div class="tw-who-field" data-tw-shared><span class="tw-who-k">Who can use it</span>
      <label class="tw-all-bots"><input type="checkbox" role="switch" class="people-switch" name="all_bots" data-tw-all-bots checked> All bots</label>
      <p class="muted">Includes bots added later. Turn off to choose individual bots.</p>
      ${whoPickerHTML({label: 'Who can use it'})}
      <div class="tw-access" data-tw-picked></div></div>` : ''}`;
}
// Binds the Shared/Own switch and the who-can-use-it picker; returns the picked subjects.
function twLoginBind(dialog: HTMLDialogElement): Set<string> {
  const picked = new Set<string>();
  const kind = dialog.querySelector<HTMLInputElement>('[name=kind]'), own = dialog.querySelector<HTMLElement>('[data-tw-own]');
  const shared = dialog.querySelector<HTMLElement>('[data-tw-shared]'), rows = dialog.querySelector<HTMLElement>('[data-tw-picked]');
  const allBots = dialog.querySelector<HTMLInputElement>('[data-tw-all-bots]');
  dialog.querySelectorAll<HTMLButtonElement>('[data-tw-kind]').forEach(b => {
    b.onclick = () => {
      const k = b.dataset.twKind || 'shared';
      dialog.querySelectorAll('[data-tw-kind]').forEach(x => { x.setAttribute('aria-checked', String(x === b)); });
      if (kind) kind.value = k;
      if (own) own.hidden = k !== 'own';
      if (shared) shared.hidden = k === 'own';
    };
  });
  if (!shared || !rows) return picked;
  let groups = new Map<string, TwGroup>();
  const chosen = () => [...picked].map(s => twWhoOf(s, groups));
  const paint = () => { rows.innerHTML = picked.size ? twWhoRows(chosen(), 'data-tw-unpick') : ''; };
  const picker = whoPickerWire(shared, {candidates: () => twCandidates(chosen(), groups, !!allBots?.checked), onPick: c => { picked.add(c.subject); paint(); }});
  if (allBots) allBots.onchange = () => picker?.refresh();
  rows.onclick = ev => {
    const s = eventElement(ev)?.closest<HTMLElement>('[data-tw-unpick]')?.dataset.twUnpick;
    if (s) { picked.delete(s); paint(); picker?.input.focus(); }
  };
  void twGroups().then(g => { groups = g; paint(); picker?.refresh(); });
  return picked;
}
// The account's write: the login, then each grant. A grant that fails leaves the account; the toast says so.
async function twAddAccount(toolId: string, data: FormData, picked: Set<string>): Promise<void> {
  const own = data.get('kind') === 'own', totp = String(data.get('totp') || '').trim();
  const username = String(data.get('username') || '').trim(), password = String(data.get('password') || '');
  // No password: an account a person signs in to by hand (Google or SSO, an emailed code, a passkey).
  const made: {id?: string} = await post(twPath(toolId, '/accounts'), {...(username ? {username} : {}), ...(password ? {password} : {}),
    ...(totp ? {totp} : {}), ...(own ? {holder: String(data.get('holder') || '')} : {all_bots: data.get('all_bots') === 'on'})});
  if (own || !made?.id || !picked.size) return;
  const aid = made.id;
  const failed = (await Promise.allSettled([...picked].map(s => put(twAcctPath(toolId, aid, '/access/' + encodeURIComponent(s)), {on: true}))))
    .filter(r => r.status === 'rejected').length;
  if (failed) toast(`${failed} could not be added`, true);
}
const twCanShare = () => !!(S.me?.credential_access || settingsIsAdmin());
function twAccountDialog(state: TwState): void {
  const dialog = whDialog('Add account', twLoginFields(twCanShare()), '<button class="primary" type="submit">Add</button>');
  const picked = twLoginBind(dialog);
  whSubmit(dialog, async (data: FormData) => {
    await twAddAccount(state.id, data, picked);
    dialog.close(); toast('Added');
    await twRedraw(state);
  });
}
// Add tool › Website: the site, its first account and Central only. A refused account keeps the made tool, so Add
// again only adds the account.
function toolsWebsiteCreate(): void {
  const dialog = whDialog('Add website', `<label>Site<input name="site" maxlength="300" required autocomplete="off" spellcheck="false" autocapitalize="off" inputmode="url" placeholder="app.example.com"></label>
    ${twLoginFields(twCanShare())}
    <label class="tw-central"><input type="checkbox" role="switch" class="people-switch" name="central_only"> Central only</label>`,
    '<button class="primary" type="submit">Add</button>');
  dialog.classList.add('tw-add');
  const picked = twLoginBind(dialog);
  let toolId = '';
  whSubmit(dialog, async (data: FormData) => {
    if (!toolId) {
      const site = String(data.get('site') || '').trim();
      const made: {id?: string} = await post('/v2/website-tools', {sites: [site], central_only: data.get('central_only') === 'on'});
      toolId = made?.id || '';
      if (!toolId) throw new Error('Try again');
      dialog.querySelector<HTMLInputElement>('[name=site]')?.setAttribute('readonly', '');
    }
    await twAddAccount(toolId, data, picked);
    dialog.close(); toast('Added');
    location.hash = '#/tools/website/' + encodeURIComponent(toolId);
  });
}
// Replace login: a new username, password or 2FA key; a blank one keeps what is stored, which is never shown.
function twReplaceDialog(state: TwState, a: TwAccount): void {
  const dialog = whDialog('Replace login', `<label>Username<input name="username" maxlength="250" autocomplete="off" spellcheck="false" autocapitalize="off" value="${esc(a.username || '')}"></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" spellcheck="false"${a.has_password === false ? '' : ' placeholder="Unchanged"'}></label>
    <label>2FA key<input name="totp" type="password" autocomplete="off" spellcheck="false" placeholder="${a.has_totp ? 'Unchanged' : 'Setup key or otpauth:// link'}"></label>
    ${a.has_totp ? '<label class="tw-central"><input type="checkbox" name="no_totp"> Remove 2FA</label>' : ''}`,
    '<button class="primary" type="submit">Save</button>');
  whSubmit(dialog, async (data: FormData) => {
    const username = String(data.get('username') || '').trim(), password = String(data.get('password') || ''), totp = String(data.get('totp') || '').trim();
    const body: {username?: string; password?: string; totp?: string | null} = {};
    if (username !== (a.username || '')) body.username = username;
    if (password) body.password = password;
    if (data.get('no_totp') === 'on') body.totp = null; else if (totp) body.totp = totp;
    if (Object.keys(body).length) await patch(twAcctPath(state.id, a.id), body);
    dialog.close(); toast('Saved');
    await twRedraw(state);
  });
}
// Who can use it (a shared account): each change is saved at once, and the rows repaint from the account's access the
// answer carries. The picker at the top; the only button is Done.
function twAccessDialog(state: TwState, a: TwAccount): void {
  const dialog = whDialog('Who can use it', `<label class="tw-all-bots"><input type="checkbox" role="switch" class="people-switch" data-tw-all-bots${a.all_bots ? ' checked' : ''}${state.data?.can_manage ? '' : ' disabled'}> All bots</label>
    <p class="muted">Includes bots added later. Turn off to choose individual bots.</p>
    ${whoPickerHTML({label: 'Add a person, bot or group'})}<div class="tw-access" data-tw-access></div>`, '', {close: 'Done'});
  dialog.classList.add('tw-access-dialog');
  let access = [...(a.access || [])], groups = new Map<string, TwGroup>(), allBots = !!a.all_bots;
  const all = dialog.querySelector<HTMLInputElement>('[data-tw-all-bots]');
  const list = dialog.querySelector<HTMLElement>('[data-tw-access]');
  const err = dialog.querySelector<HTMLElement>('[data-wh-error]');
  // With All bots on, a bot's own grant cannot be taken away (the server says to turn All bots off first).
  const paint = () => {
    if (!list) return;
    list.innerHTML = access.length ? twWhoRows(access, 'data-tw-revoke') : `<p class="muted tw-nobody">${allBots ? 'Nobody else.' : 'Nobody yet.'}</p>`;
    if (allBots) list.querySelectorAll<HTMLButtonElement>('[data-tw-access-row^="bot:"] [data-tw-revoke]').forEach(b => { b.disabled = true; });
  };
  const save = async (subject: string, on: boolean) => {
    if (err) err.textContent = '';
    try {
      const said: {access?: TwWho[]} | null = await put(twAcctPath(state.id, a.id, '/access/' + encodeURIComponent(subject)), {on});
      if (subject === 'bots') allBots = on;
      else access = Array.isArray(said?.access) ? said.access : on ? [...access, twWhoOf(subject, groups)] : access.filter(w => w.subject !== subject);
      paint();
      picker?.refresh();
    } catch (e) { if (all) all.checked = allBots; if (err) err.textContent = errorMessage(e, 'Try again'); }
  };
  const picker = whoPickerWire(dialog, {candidates: () => twCandidates(access, groups, allBots), onPick: c => save(c.subject, true)});
  if (all) all.onchange = async () => { all.disabled = true; await save('bots', all.checked); all.disabled = false; };
  if (list) list.onclick = ev => {
    const s = eventElement(ev)?.closest<HTMLElement>('[data-tw-revoke]')?.dataset.twRevoke;
    if (s) void save(s, false);
  };
  dialog.addEventListener('close', () => void twRedraw(state));
  paint();
  void twGroups().then(g => { groups = g; picker?.refresh(); });
}

function toolWebsiteStop(): void {
  const root = twRoot();
  if (root) browserView.stopIn(root);
  TW = null;
}
