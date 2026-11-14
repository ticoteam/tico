/* ui/app/tools-api.ts — One API tool's page under Tools (ui/app/tools.ts routes it): #/tools/api/<id> and its Activity
   tab, #/tools/api/<id>/activity. An API tool holds one key a level (Read, Read and write); who may use it is a level a
   person or bot, which the hub turns into grants on the right key (PUT /api/v2/api-tools/<id>/access/<subject>). Its
   calls go through Tico's proxy and are logged (delivery 'proxy'), or the key sits on the bots' computers ('computer',
   what every tool migrated from before was) until someone moves it to the proxy. A check is an authenticated GET to
   its check path every 30 minutes; the page shows the last 24. Limits per action are not built yet: the card says so.
   Reads GET /api/v2/api-tools/<id> ({tool, keys, access, checks, recent, status, can_manage}) and
   GET …/activity?result=&cursor= ({rows, next}: newest first, a page at a time). A row is a call through the proxy
   (kind 'call': method, path with query keys only, result sent | refused | failed) or, for a key on computers, the
   newest time a run took it (kind 'use'); each has `at`. Revealing a key is the vault's own audited
   request (POST /api/v2/credentials/<id>/reveal). Who can see it: `private` (Only listed) puts a Private pill after the
   name; whoever may change it (`can_set_audience`) gets the card with `audience` and `seen_by` ([{subject, kind, id,
   name, why}]: Owner, Listed, Can use it) and saves through PATCH /api/v2/api-tools/<id> {audience: null | audience},
   picking with the page audience's editor (accessEditor, ui/app/settings-access.ts). An access row of kind 'finance'
   is Finance reading the key: shown, never changed here. A Google Ads or Meta Ads tool also shows a Finance card to
   finance admins (GET /api/v2/finance/ad-accounts?tool_id=; anyone else is refused and sees no card): the ad spend
   import's settings and each ad account's bot (POST /api/v2/finance/ad-imports, …/ad-imports/run, PATCH
   …/ad-accounts/<id>). Styles: ui/styles/tools.css.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TA_LEVELS: [string, string][] = [['read', 'Read'], ['read_write', 'Read and write']];
const TA_RESULTS: Record<string, [string, string]> = {sent: ['Sent', 'ok'], refused: ['Refused', 'fail'], failed: ['Failed', 'fail'], use: ['Used', '']};
const TA_FILTERS: [string, string][] = [['', 'All'], ['sent', 'Sent'], ['refused', 'Refused'], ['failed', 'Failed']];
const TA_AUTH: [string, string][] = [['bearer', 'Bearer token'], ['basic_user', 'Basic, key as user'], ['basic_password', 'Basic, key as password'],
  ['header', 'Header'], ['query', 'Query parameter']];
const TA_CHECKS = 24;

interface ToolApiAccess {subject: string; kind: string; name?: string | null; level?: string; fixed?: boolean; last_used_at?: string | null}
interface ToolApiSeen {subject: string; kind: string; id?: string; name?: string | null; why: string}
interface ToolApiCall {
  id?: string; kind?: string; at?: string | null; method?: string; path?: string; bot?: string | null; actor?: string;
  result: string; status_code?: number | string | null; detail?: string | null; access_level?: string;
}
interface ToolApiPage {rows?: ToolApiCall[]; next?: string | null}
interface ToolApiKey {id: string; name: string; preview?: string | null; access_level: string; expires_on?: string | null;
  last_used_at?: string | null; created_at?: string | null}
interface ToolApiCheck {ok: boolean; checked_at?: string | null; status_code?: number | string | null; detail?: string | null}
interface ToolApiTool {
  id?: string; name: string; base_url?: string | null; check_path?: string | null; auth_scheme?: string | null;
  delivery?: string | null; service?: string | null;
}
interface ToolApiAudienceIn {people?: string[]; teams?: string[]; bots?: string[]}
interface ToolApiAudience {everyone: boolean; people: string[]; teams: string[]; bots: string[]}
interface ToolApiData {
  tool: ToolApiTool; status?: ToolsStatus; private?: boolean; can_manage?: boolean; can_set_audience?: boolean;
  access?: ToolApiAccess[]; seen_by?: ToolApiSeen[]; audience?: ToolApiAudienceIn | null; keys?: ToolApiKey[];
  recent?: ToolApiCall[]; checks?: ToolApiCheck[];
}
interface ToolApiPerson {id: string; name?: string; email?: string | null; hidden?: boolean; archived?: boolean}
type ToolApiFinance = import('../types/api').FinanceAdAccounts & {campaigns?: FcCampaign[]};
interface ToolApiState {
  run: number; id: string; tab: string | undefined; data: ToolApiData | null; fin: ToolApiFinance | null;
  act: {rows: ToolApiCall[]; cursor: string; result: string};
}
interface ToolApiFormFields {name: string; base_url: string; auth_scheme: string; check_path: string}

let TA: ToolApiState | null = null;   // {run, id, tab, data, act: {rows, cursor, result}}
const taPeople = (): ToolApiPerson[] => S.people || [];
const taLevelName = (level?: string | null) => (TA_LEVELS.find(([k]) => k === level) || [level, level || ''])[1];
const taPath = (id: string, rest = '') => `/v2/api-tools/${encodeURIComponent(id)}${rest}`;
const taHost = (url?: string | null): string => { try { return new URL(url || '').host; } catch { return url || ''; } };
const taResult = (r: string): string => { const [word, tone] = TA_RESULTS[r] || [r || '', '']; return `<span class="pill${tone ? ' ' + tone : ''}" data-result="${esc(r || '')}">${esc(word)}</span>`; };
// A row's caller: its bot, else the person its subject names (human:<id>).
const taWho = (row: ToolApiCall): string => row.bot ? botDisplayName(row.bot) : personDisplay(String(row.actor || '').replace(/^human:/, ''));
const taCall = (c: ToolApiCall): string => c.kind === 'use' ? 'Key taken by a run' : `${c.method || ''} ${c.path || ''}`.trim();
const taFace = (a: {kind: string; subject: string; name?: string | null}): string => a.kind === 'bot' ? avatar(a.subject.slice(4), 22)
  : a.kind === 'finance' || a.kind === 'group' ? `<span class="tt-tile sm" aria-hidden="true"><span class="nav-icon">${a.kind === 'finance' ? 'account_balance' : 'group'}</span></span>`
  : personAvatar((S.people || []).find(p => p.id === a.subject.split(':')[1]) || {name: a.name}, 22);

// ----------------------------------------------------------------- the page
async function pageToolApi(id: string, tab?: string): Promise<void> {
  const run = (TA?.run || 0) + 1, same = TA?.id === id && $('#tool-api');
  TA = {run, id, tab, data: same && TA ? TA.data : null, fin: same && TA ? TA.fin : null,
    act: {rows: [], cursor: '', result: same && TA ? TA.act.result : ''}};
  if (!same) $('#main').innerHTML = `<div class="tt tt-page" id="tool-api" data-tool="${esc(id)}">
    <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-ta-crumb>${esc(id)}</span></nav>
    <div data-ta-head></div><div data-ta-body>${skelRows(4, {card: true})}</div></div>`;
  const root = $('#tool-api');
  root.dataset.tab = tab || 'overview';
  if (!root.dataset.bound) { root.dataset.bound = '1'; root.addEventListener('click', (ev: Event) => void taClick(ev)); }
  try { await taLoad(); }
  catch (e) { if (TA?.run === run) root.querySelector('[data-ta-body]').innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  if (TA?.run !== run) return;
  taDrawHead();
  if (tab === 'activity') await taActivity(true); else { taDrawOverview(); void taFinanceLoad(); }
}
async function taLoad(): Promise<void> {
  const state = TA;
  if (!state) return;
  const d: ToolApiData = await get(taPath(state.id), {fresh: true});
  if (TA === state) state.data = d;
}
function taDrawHead(): void {
  const state = TA;
  if (!state?.data) return;
  const root = $('#tool-api'), {tool, status, can_manage: manage} = state.data, base = '#/tools/api/' + encodeURIComponent(state.id);
  root.querySelector('[data-ta-crumb]').textContent = tool.name;
  const sub = ['API key', taHost(tool.base_url)].filter(Boolean).join(' · ');
  root.querySelector('[data-ta-head]').innerHTML = `<header class="tt-title"><span class="tt-tile lg" aria-hidden="true"><span class="nav-icon">api</span></span>
      <div class="tt-title-text"><h1>${esc(tool.name)}${state.data.private ? TOOLS_PRIVATE : ''}</h1><p class="muted">${esc(sub)}</p></div>
      <div class="tt-title-act">${toolsPill(status)}${tool.base_url && tool.check_path ? '<button type="button" class="ghost small" data-ta-check>Check now</button>' : ''}${
        manage ? '<button type="button" class="ghost small" data-ta-settings>Settings</button>' : ''}</div></header>
    <nav class="tt-tabs" aria-label="${esc(tool.name)}">${[['overview', base, 'Overview'], ['activity', base + '/activity', 'Activity']].map(([k, href, label]) =>
      `<a href="${href}" data-ta-tab="${k}"${(state.tab || 'overview') === k ? ' class="ph-tab cur" aria-current="page"' : ' class="ph-tab"'}>${label}</a>`).join('')}</nav>`;
}

// ----------------------------------------------------------------- Overview
function taDrawOverview(): void {
  const d = TA?.data, body = $('#tool-api [data-ta-body]');
  if (!d) return;
  if (formBusy(body)) return;   // a half-typed Add stays put
  body.innerHTML = `<div class="tt-cols"><div class="tt-col">${taWhoCard(d)}${taRecentCard(d)}</div>
    <div class="tt-col">${d.can_set_audience ? taSeeCard(d) : ''}${taFinanceSlot(d)}${taKeysCard(d)}${taCheckCard(d)}${taLimitsCard()}</div></div>`;
  const finSlot: HTMLElement | null = body.querySelector('[data-ta-finance]');
  taFinanceBind(finSlot);
}
function taWhoCard(d: ToolApiData): string {
  const manage = d.can_manage;
  const rows = (d.access || []).map(a => `<tr data-ta-subject-row="${esc(a.subject)}"${a.fixed ? ' data-ta-fixed' : ''}>
      <td class="tt-who-name">${taFace(a)}<span>${esc(a.name || a.subject)}${a.kind === 'bot' ? ' <span class="muted">· bot</span>' : ''}</span></td>
      <td>${manage && !a.fixed ? `<div class="theme-seg" role="radiogroup" aria-label="Level for ${esc(a.name)}">${TA_LEVELS.map(([k, label]) =>
        `<button type="button" role="radio" aria-checked="${a.level === k}" data-ta-level="${k}" data-ta-subject="${esc(a.subject)}">${label}</button>`).join('')}</div>` : esc(taLevelName(a.level))}</td>
      <td class="muted tt-used">${a.last_used_at ? esc(ago(a.last_used_at)) : ''}</td>
      <td class="mb-act">${manage && !a.fixed ? `<button type="button" class="ghost tt-x" data-ta-remove="${esc(a.subject)}" aria-label="Remove ${esc(a.name)}" title="Remove">×</button>` : ''}</td></tr>`).join('');
  const taken = new Set((d.access || []).map(a => a.subject));
  const people = taPeople().filter(p => !p.hidden && !p.archived && !taken.has('human:' + p.id));
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && !taken.has('bot:' + e.id));
  const add = manage && (people.length || bots.length) ? `<form class="tt-add-form" data-ta-add-form hidden>
      <select name="subject" aria-label="Who">${people.length ? `<optgroup label="People">${people.map(p => `<option value="human:${esc(p.id)}">${esc(p.name || p.email || p.id)}</option>`).join('')}</optgroup>` : ''}${
        bots.length ? `<optgroup label="Bots">${bots.map(b => `<option value="bot:${esc(b.id)}">${esc(botDisplayName(b.id))}</option>`).join('')}</optgroup>` : ''}</select>
      <select name="level" aria-label="Level">${TA_LEVELS.map(([k, label]) => `<option value="${k}">${label}</option>`).join('')}</select>
      <button type="submit" class="primary small">Add</button><button type="button" class="ghost small" data-ta-add-cancel>Cancel</button></form>` : '';
  return `<section class="card" id="tool-access"><header><h2>Who can use it</h2>${add ? '<button type="button" class="ghost small settings-head-act" data-ta-add-open>Add</button>' : ''}</header>
    ${add}${rows ? `<table class="settings-table tt-who-table"><tbody>${rows}</tbody></table>` : '<p class="muted" data-ta-nobody>Nobody yet.</p>'}
    <p class="muted tt-foot">Assistants act as their person.</p></section>`;
}
// Who can see it: Everyone (today's rules) or Only listed; the owner, the list and whoever can use it see it.
const TA_LISTS: Record<string, 'people' | 'teams' | 'bots'> = {person: 'people', group: 'teams', bot: 'bots'};
function taSeeCard(d: ToolApiData): string {
  const only = !!d.private, me = 'human:' + (S.me?.id || ''), owner = (d.seen_by || []).some(r => r.why === 'Owner' && r.subject === me);
  const choice = [['everyone', 'Everyone', !only], ['only', 'Only listed', only]].map(([k, label, on]) =>
    `<button type="button" class="${on ? 'primary' : 'ghost'} small" data-ta-see="${k}" aria-pressed="${on}">${label}</button>`).join('');
  const rows = (d.seen_by || []).map(r => `<tr data-ta-seen="${esc(r.subject)}" data-why="${esc(r.why)}">
      <td class="tt-who-name">${taFace(r)}<span>${esc(r.name || r.id)}${r.kind === 'bot' ? ' <span class="muted">· bot</span>' : ''}</span></td>
      <td class="muted">${esc(r.why)}</td>
      <td class="mb-act">${r.why === 'Listed' && TA_LISTS[r.kind] && (owner || r.subject !== me)
        ? `<button type="button" class="ghost tt-x" data-ta-unlist="${esc(r.subject)}" aria-label="Remove ${esc(r.name || r.id)}" title="Remove">×</button>` : ''}</td></tr>`).join('');
  return `<section class="card" id="tool-see"><header><h2>Who can see it</h2>${only ? '<button type="button" class="ghost small settings-head-act" data-ta-see-add>Add</button>' : ''}</header>
    <div class="row tt-see-choice" role="group" aria-label="Who can see it">${choice}</div>
    ${only ? `<table class="settings-table tt-who-table tt-see-table"><thead><tr><th>Who</th><th>Why</th><th aria-hidden="true"></th></tr></thead><tbody>${rows}</tbody></table>` : ''}</section>`;
}
const taAudience = (d: ToolApiData | null): ToolApiAudience => {
  const a: ToolApiAudienceIn = d?.audience || {};
  return {everyone: false, people: [...(a.people || [])], teams: [...(a.teams || [])], bots: [...(a.bots || [])]};
};
const taSaveAudience = (audience: import('../types/api').AccessAudience | null) => patch(taPath(TA?.id || ''), {audience: audience && !audience.everyone ? audience : null});
async function taSeeAdd(): Promise<void> {
  const state = TA;
  if (!state) return;
  const level = taAudience(state.data);
  try {
    const groups = await get('/v2/groups');
    await accessEditor({name: state.data?.tool.name, singleAudience: true,
      initialData: {see: level, read: level, write: level, teams: Array.isArray(groups) ? groups : groups.groups || []},
      save: taSaveAudience,
      after: async () => { await taLoad(); if (TA === state) { taDrawHead(); taDrawOverview(); } }} as Parameters<typeof accessEditor>[0]);
  } catch (e) { toast(errorMessage(e, ''), true); }
}

function taRecentCard(d: ToolApiData): string {
  const base = '#/tools/api/' + encodeURIComponent(TA?.id || '');
  const rows = (d.recent || []).map(c => `<tr><td class="muted tnum" title="${esc(fmt(c.at))}">${esc(ago(c.at))}</td><td>${esc(taWho(c))}</td>
      <td class="mono tt-call">${esc(taCall(c))}</td><td>${taResult(c.result)}</td></tr>`).join('');
  return `<section class="card" id="tool-recent"><header><h2>Recent activity</h2><a class="ghost small settings-head-act" href="${base}/activity">See all</a></header>
    ${rows ? `<table class="tt-log tt-log-short"><tbody>${rows}</tbody></table>` : '<p class="muted">No calls yet.</p>'}</section>`;
}
function taKeysCard(d: ToolApiData): string {
  const manage = d.can_manage, keys = d.keys || [], tool = d.tool;
  const rows = keys.map(k => `<div class="tt-key" data-ta-key="${esc(k.id)}"><span class="nav-icon" aria-hidden="true">key_vertical</span>
      <span class="tt-key-name"><b>${esc(k.name)}</b>${k.preview ? ` <span class="mono muted">${esc(k.preview)}</span>` : ''}</span>
      <span class="pill${k.access_level === 'read' ? '' : ' in-progress'}" data-key-level="${esc(k.access_level)}">${esc(taLevelName(k.access_level))}</span>
      <span class="tt-key-acts">${manage ? `<button type="button" class="linkish" data-ta-rotate="${esc(k.id)}">Rotate</button>` : ''}<button type="button" class="linkish" data-ta-reveal="${esc(k.id)}">Reveal · logged</button>${
        manage ? `<button type="button" class="linkish danger" data-ta-key-delete="${esc(k.id)}">Remove</button>` : ''}</span></div>`).join('');
  const soonest = keys.map(k => k.expires_on).filter(Boolean).sort()[0];
  const used = keys.map(k => k.last_used_at).filter(Boolean).sort().pop();
  const added = keys.map(k => k.created_at).filter(Boolean).sort()[0];
  const row = (dt: string, dd: string | null | undefined) => dd ? `<dt>${esc(dt)}</dt><dd>${dd}</dd>` : '';
  const calls = tool.delivery === 'computer'
    ? `<span data-ta-delivery="computer">Key on computers</span>${manage ? ' · <button type="button" class="linkish" data-ta-proxy>Move to proxy</button>' : ''}`
    : '<span data-ta-delivery="proxy">Through Tico · logged</span>';
  const expires = `${soonest ? esc(soonest) : 'No date'}${manage && keys.length ? ' · <button type="button" class="linkish" data-ta-expires>Set</button>' : ''}`;
  return `<section class="card" id="tool-keys"><header><h2>Keys</h2>${manage ? '<button type="button" class="ghost small settings-head-act" data-ta-key-add>Add key</button>' : ''}</header>
    ${rows || '<p class="muted" data-ta-no-keys>No keys yet.</p>'}
    <dl class="tt-kv">${row('Calls', calls)}${keys.length ? row('Expires', expires) : ''}${row('Added', added && `<span title="${esc(fmt(added))}">${esc(ago(added))}</span>`)}${
      row('Last used', used && `<span title="${esc(fmt(used))}">${esc(ago(used))}</span>`)}</dl></section>`;
}
// The last 24 checks, oldest first: a square each, green when it worked.
function taCheckCard(d: ToolApiData): string {
  const checks = (d.checks || []).slice(0, TA_CHECKS), last = checks[0], tool = d.tool;
  const squares = [...Array<ToolApiCheck | null>(TA_CHECKS - checks.length).fill(null), ...checks.slice().reverse()].map(c => c
    ? `<span class="tt-sq ${c.ok ? 'ok' : 'fail'}" title="${esc(`${fmt(c.checked_at)} · ${c.status_code || ''} ${c.detail || ''}`.trim())}"></span>`
    : '<span class="tt-sq"></span>').join('');
  const line = tool.check_path ? `GET ${tool.check_path}${last ? ` → ${last.status_code || last.detail || ''}` : ''}` : 'No check path';
  return `<section class="card" id="tool-check"><header><h2>Check</h2></header>
    <p class="muted">${tool.delivery === 'proxy' ? 'Every 30 min' : 'On Check now'}</p><p class="mono tt-check-line">${esc(line)}</p>
    <div class="tt-squares" role="img" aria-label="${checks.filter(c => c.ok).length} of ${checks.length} checks worked">${squares}</div>
    <p class="muted tt-foot">Last 12 hours</p></section>`;
}
// Limits per action come later (the spec's next step); the card shows where they will be.
const taLimitsCard = (): string => `<section class="card tt-soon" id="tool-limits"><header><h2>Limits</h2><span class="pill" data-ta-soon>Not available yet</span></header>
  <label class="tt-off"><input type="checkbox" role="switch" class="people-switch" disabled> Check calls against limits</label></section>`;

// ----------------------------------------------------------------- Activity
async function taActivity(fresh: boolean): Promise<void> {
  const state = TA;
  if (!state) return;
  const body = $('#tool-api [data-ta-body]');
  if (fresh) {
    body.innerHTML = `<div class="tt-bar"><div class="theme-seg" role="radiogroup" aria-label="Show">${TA_FILTERS.map(([k, label]) =>
      `<button type="button" role="radio" data-ta-show="${k}" aria-checked="${state.act.result === k}">${label}</button>`).join('')}</div></div>
      <div data-ta-log>${skelRows(4)}</div><div class="row" data-ta-more hidden><button type="button" class="ghost" data-ta-more-btn>More</button></div>`;
    state.act.rows = []; state.act.cursor = '';
  }
  const q = new URLSearchParams({limit: '50'});
  if (state.act.result) q.set('result', state.act.result);
  if (state.act.cursor) q.set('cursor', state.act.cursor);
  let d: ToolApiPage;
  try { d = await get(taPath(state.id, '/activity?' + q), {fresh: true}); }
  catch (e) { if (TA === state) body.querySelector('[data-ta-log]').innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  if (TA !== state || !body.isConnected) return;
  state.act.rows = state.act.rows.concat(d.rows || []);
  state.act.cursor = d.next || '';
  const rows = state.act.rows;
  body.querySelector('[data-ta-log]').innerHTML = rows.length ? `<table class="tt-log"><thead><tr><th>Time</th><th>Who</th><th>Call</th><th>Level</th><th>Result</th></tr></thead>
    <tbody>${rows.map(c => `<tr data-ta-call="${esc(c.id || '')}"><td class="tnum" title="${esc(fmt(c.at))}">${esc(ago(c.at))}</td><td>${esc(taWho(c))}</td>
      <td class="mono tt-call">${esc(taCall(c))}${c.status_code ? ` <span class="muted">→ ${esc(c.status_code)}</span>` : ''}</td>
      <td>${esc(taLevelName(c.access_level))}</td><td>${taResult(c.result)}${c.detail && c.result !== 'sent' ? `<span class="muted tt-log-why">${esc(c.detail)}</span>` : ''}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty" data-ta-none>Nothing yet.</div>';
  const more: HTMLElement | null = body.querySelector('[data-ta-more]');
  if (more) more.hidden = !state.act.cursor;
}

// ----------------------------------------------------------------- actions
async function taClick(ev: Event): Promise<void> {
  const b = eventElement(ev)?.closest('button');
  const state = TA;
  if (!b || !state?.data) return;
  const id = state.id;
  // A write, then the page read again and drawn; a refusal is a toast and leaves the page as it was.
  const write = async (run: () => Promise<unknown>) => {
    b.disabled = true;
    try { await run(); await taLoad(); if (TA !== state) return; taDrawHead(); if (state.tab !== 'activity') taDrawOverview(); }
    catch (e) { toast(errorMessage(e, ''), true); }
    finally { if (b.isConnected) b.disabled = false; }
  };
  if (b.dataset.taShow !== undefined) {
    state.act.result = b.dataset.taShow;
    b.parentElement?.querySelectorAll('[data-ta-show]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    state.act.rows = []; state.act.cursor = '';
    return taActivity(false);
  }
  if (b.hasAttribute('data-ta-more-btn')) return taActivity(false);
  if (b.hasAttribute('data-ta-check')) {
    return write(async () => {
      const c = await post(taPath(id, '/check'), {});
      toast(c?.ok ? 'Working' : c?.detail || 'Check failed', !c?.ok);
    });
  }
  if (b.dataset.taLevel && b.getAttribute('aria-checked') !== 'true') {
    return write(() => put(taPath(id, '/access/' + encodeURIComponent(String(b.dataset.taSubject))), {level: b.dataset.taLevel}));
  }
  if (b.dataset.taRemove) return write(() => put(taPath(id, '/access/' + encodeURIComponent(String(b.dataset.taRemove))), {level: 'none'}));
  if (b.hasAttribute('data-ta-add-open')) { const f = $('#tool-api [data-ta-add-form]'); f.hidden = false; f.querySelector('select').focus(); return; }
  if (b.hasAttribute('data-ta-add-cancel')) { const form = b.closest('form'); if (form) form.hidden = true; return; }
  if (b.hasAttribute('data-ta-proxy')) {
    if (!await settingsConfirm({title: 'Move to proxy?', text: 'Calls go through Tico and are logged. The key leaves the computers on their next run.', ok: 'Move to proxy', danger: false})) return;
    return write(() => patch(taPath(id), {delivery: 'proxy'}));
  }
  if (b.dataset.taSee && b.getAttribute('aria-pressed') !== 'true') {
    if (b.dataset.taSee === 'only') return write(() => taSaveAudience(taAudience(state.data)));
    if (!await settingsConfirm({title: 'Show to everyone?', text: 'Credential admins can see it and its keys.', ok: 'Everyone', danger: false})) return;
    return write(() => taSaveAudience(null));
  }
  if (b.hasAttribute('data-ta-see-add')) return taSeeAdd();
  if (b.dataset.taUnlist) {
    const row = (state.data.seen_by || []).find(r => r.subject === b.dataset.taUnlist), audience = taAudience(state.data);
    if (!row) return;
    const list = TA_LISTS[row.kind];
    if (!list) return;
    audience[list] = audience[list].filter(x => x !== row.id);
    return write(() => taSaveAudience(audience));
  }
  if (b.hasAttribute('data-ta-settings')) return taSettingsDialog();
  if (b.dataset.taReveal) return taReveal(b.dataset.taReveal);
  if (b.dataset.taRotate) return taKeyDialog((state.data.keys || []).find(k => k.id === b.dataset.taRotate));
  if (b.hasAttribute('data-ta-key-add')) return taKeyDialog(null);
  if (b.hasAttribute('data-ta-expires')) return taExpiresDialog();
  if (b.dataset.taKeyDelete) {
    const key = (state.data.keys || []).find(k => k.id === b.dataset.taKeyDelete);
    if (!await settingsConfirm({title: `Remove ${key?.name || 'this key'}?`, text: 'Calls that used it stop working.', ok: 'Remove'})) return;
    return write(() => writeRequest('DELETE', taPath(id, '/keys/' + encodeURIComponent(String(b.dataset.taKeyDelete)))));
  }
}
// ----------------------------------------------------------------- Finance: ad spend (finance admins only)
const TAF_SERVICES: (string | null | undefined)[] = ['google-ads', 'meta-ads'];
const tafOptions = (rows: string[][], picked: string | null | undefined, empty: string): string => `<option value="">${esc(empty)}</option>${rows.map(([v, label]) =>
  `<option value="${esc(v)}"${v === picked ? ' selected' : ''}>${esc(label)}</option>`).join('')}`;
function taFinanceSlot(d: ToolApiData): string {
  const fin = TA?.fin;
  return TAF_SERVICES.includes(d.tool?.service) ? `<div data-ta-finance>${fin ? taFinanceCard(fin) : ''}</div>` : '';
}
async function taFinanceLoad(): Promise<void> {
  const state = TA;
  if (!state || !TAF_SERVICES.includes(state.data?.tool?.service)) return;
  let found: ToolApiFinance | null = null;
  try { found = await get('/v2/finance/ad-accounts?tool_id=' + encodeURIComponent(state.id), {fresh: true}); } catch { found = null; }
  if (found) found.campaigns = await taCampaignList();
  if (TA !== state) return;
  state.fin = found;
  const slot = $('#tool-api [data-ta-finance]');
  if (slot && !formBusy(slot)) { slot.innerHTML = found ? taFinanceCard(found) : ''; taFinanceBind(slot); }
}
// This month's campaigns the viewer sees (finance-campaigns.ts draws them under their ad accounts). The owner picker
// needs the teammates and teams list, which the finance page loads: fetched here when it is not in yet.
const taMonthKey = (): string => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
async function taCampaignList(): Promise<FcCampaign[]> {
  try { if (!FIN_ACCESS) await finProbe(); } catch { /* the picker shows the current owner only */ }
  try { return (await get('/v2/finance/campaigns?month=' + taMonthKey(), {fresh: true})).campaigns || []; } catch { return []; }
}
// The ad accounts read the Finance card last drew, with its campaigns.
let taFinDrawn: ToolApiFinance | null = null;
// After an owner change: the campaigns read again and the card is drawn in place.
async function taCampaignsReload(): Promise<void> {
  const fin = taFinDrawn, slot = $('#tool-api [data-ta-finance]');
  if (!fin || !slot) return;
  fin.campaigns = await taCampaignList();
  if (slot.isConnected && taFinDrawn === fin) slot.innerHTML = taFinanceCard(fin);
}
function taFinanceCard(f: ToolApiFinance, campaigns: FcCampaign[] = f.campaigns || []): string {
  taFinDrawn = f;
  const imp = f.import, on = imp.enabled, other = f.elsewhere;
  const seg = `<div class="theme-seg" role="radiogroup" aria-label="Ad spend">${[[true, 'On'], [false, 'Off']].map(([v, label]) =>
    `<button type="button" role="radio" aria-checked="${on === v}" data-taf-on="${v}"${other && v && !on ? ' disabled' : ''}>${label}</button>`).join('')}</div>`;
  const statusPill: Record<string, string> = {reconnect: '<span class="pill needs" data-taf-status>Reconnect</span>', failed: '<span class="pill fail" data-taf-status>Failed</span>'};
  const status = statusPill[imp.status || ''] || '';
  const bots = (S.emps || []).filter(e => e.status !== 'archived').map(e => [e.id, botDisplayName(e.id)]);
  const keys = f.slots.map(k => `<label class="taf-key"><span>${esc(k.label)}</span><select data-taf-slot="${esc(k.slot)}">${
    tafOptions(k.options.map(o => [o.id, o.name]), k.credential_id, k.required ? 'Choose a key' : 'None')}</select></label>`).join('');
  const rows = f.accounts.filter(a => a.active || a.month_to_date).map(a => `<tr data-taf-account="${esc(a.id)}">
      <td>${esc(a.name)} <span class="muted mono">${esc(a.external_id)}</span></td>
      <td>${a.bot_slug ? '' : '<span class="pill needs">Not assigned</span> '}<select data-taf-bot="${esc(a.id)}" aria-label="Spend goes to">${tafOptions(bots, a.bot_slug || '', 'Not assigned')}</select></td>
      <td class="tnum">${esc(tafMoney(a.month_to_date, a.currency))}</td></tr>${finCampaignRows(a.id, campaigns, a.currency)}`).join('');
  return `<section class="card" id="tool-finance"><header><h2>Finance</h2>${status}</header>
    <div class="taf-row"><span class="taf-k">Ad spend</span>${seg}<span class="muted">every ${imp.every_hours} h · from</span><input type="date" data-taf-from value="${esc(imp.history_from)}" aria-label="From"></div>
    ${other ? `<p class="muted taf-note">${other.account ? `Account ${esc(other.account)} imports` : 'Imports'} from ${esc(other.tool_name)}</p>` : ''}
    <div class="taf-row"><span class="taf-k">Books as</span><select data-taf-line aria-label="Books line">${tafOptions(f.books_lines.map(l => [l.id, l.name]), imp.account_id, 'Choose a line')}</select><span class="muted">· ${esc(f.platform_label)}</span></div>
    <div class="taf-row"><span class="taf-k">Last import</span><span class="muted" data-taf-last>${esc(imp.last_import_at ? ago(imp.last_import_at) : 'Never')}</span>${on ? '<button type="button" class="ghost small" data-taf-run>Import now</button>' : ''}</div>
    ${imp.last_error ? `<p class="muted taf-note" data-taf-error>${esc(imp.last_error)}</p>` : ''}
    <details class="taf-keys"><summary>Keys</summary>${keys}</details>
    ${rows ? `<div class="scroll"><table class="taf-table"><thead><tr><th>Ad account</th><th>Spend goes to</th><th>${esc(f.month_label)} to date</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<p class="muted taf-note">No ad accounts yet</p>'}</section>`;
}
// The card's writes: each answers with the card's new state, drawn in place; a refusal is a toast.
function taFinanceBind(slot: HTMLElement | null): void {
  if (!slot || slot.dataset.bound) return;
  slot.dataset.bound = '1';
  const state = TA;
  if (!state) return;
  const save = async (run: () => Promise<ToolApiFinance | null | undefined>, el: {disabled: boolean} | null) => {
    if (el) el.disabled = true;
    try { const f = await run(); if (TA !== state) return; if (f?.import) state.fin = f; else await taFinanceLoad(); }
    catch (e) { toast(errorMessage(e, ''), true); }
    if (TA === state && slot.isConnected) { slot.innerHTML = state.fin ? taFinanceCard(state.fin) : ''; }
  };
  const settings = (extra: Record<string, unknown>) => post('/v2/finance/ad-imports', {tool_id: state.id, enabled: state.fin?.import.enabled, ...extra});
  slot.addEventListener('click', ev => {
    const b = eventElement(ev)?.closest('button');
    const fin = state.fin;
    if (!b || !fin) return;
    if (b.dataset.tafOn !== undefined && b.getAttribute('aria-checked') !== 'true') {
      const credentials: Record<string, string | null | undefined> = Object.fromEntries(fin.slots.filter(k => k.credential_id).map(k => [k.slot, k.credential_id]));
      void save(() => settings({enabled: b.dataset.tafOn === 'true', history_from: fin.import.history_from,
        account_id: fin.import.account_id, credentials}), b);
    }
    if (b.hasAttribute('data-taf-run')) void save(() => post('/v2/finance/ad-imports/run', {tool_id: state.id}).then(f => { toast('Importing'); return f; }), b);
  });
  slot.addEventListener('change', ev => {
    const el = eventElement(ev);
    if (!state.fin || !(el instanceof HTMLInputElement || el instanceof HTMLSelectElement)) return;
    if (el.matches('[data-taf-from]') && el.value) void save(() => settings({history_from: el.value}), el);
    else if (el.matches('[data-taf-line]')) void save(() => settings({account_id: el.value}), el);
    else if (el.matches('[data-taf-slot]')) void save(() => settings({credentials: {[String(el.dataset.tafSlot)]: el.value || null}}), el);
    else if (el.matches('[data-fc-owner]') && el instanceof HTMLSelectElement) void finCampaignOwnerChange(el);
    else if (el.matches('[data-taf-bot]')) {
      void save(async () => {
        const row = await patch('/v2/finance/ad-accounts/' + encodeURIComponent(String(el.dataset.tafBot)), {bot_slug: el.value || null});
        const a = state.fin?.accounts.find(x => x.id === row.id); if (a) a.bot_slug = row.bot_slug;
        return state.fin;
      }, el);
    }
  });
}
document.addEventListener('submit', ev => {
  const form = eventElement(ev)?.closest<HTMLFormElement>('#tool-api [data-ta-add-form]'); if (!form || !TA) return;
  ev.preventDefault();
  const state = TA, button = form.querySelector<HTMLButtonElement>('[type=submit]');
  const subject = form.querySelector<HTMLSelectElement>('[name=subject]'), level = form.querySelector<HTMLSelectElement>('[name=level]');
  if (!button || !subject || !level) return;
  button.disabled = true;
  put(taPath(state.id, '/access/' + encodeURIComponent(subject.value)), {level: level.value})
    .then(taLoad).then(() => { if (TA === state) { form.reset(); form.hidden = true; taDrawOverview(); } })
    .catch(e => { toast(errorMessage(e, ''), true); button.disabled = false; });
});

// Add key, or Rotate one: its name, value and level (a new value replaces the stored one; it is never shown back).
function taKeyDialog(key: ToolApiKey | null | undefined): void {
  const state = TA;
  if (!state) return;
  const rotating = !!key;
  const dialog = whDialog(key ? `Rotate ${key.name}` : 'Add key', `${rotating ? '' : '<label>Name<input name="name" maxlength="150" required autocomplete="off" placeholder="Read key"></label>'}
    <label>Value<input name="value" type="password" required autocomplete="off" spellcheck="false"></label>
    ${rotating ? '' : `<label>Username (optional)<input name="username" maxlength="250" autocomplete="off" spellcheck="false"></label>
    <label>Level<select name="access_level">${TA_LEVELS.map(([k, label]) => `<option value="${k}"${k === 'read_write' ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
    <label>Expires (optional)<input name="expires_on" type="date"></label>`}`,
    `<button class="primary" type="submit">${rotating ? 'Rotate' : 'Add key'}</button>`);
  whSubmit(dialog, async (data: FormData) => {
    if (key) await patch(taPath(state.id, '/keys/' + encodeURIComponent(key.id)), {value: data.get('value')});
    else await post(taPath(state.id, '/keys'), {name: data.get('name'), value: data.get('value'), username: data.get('username') || undefined,
      access_level: data.get('access_level'), expires_on: data.get('expires_on') || undefined});
    dialog.close(); toast(rotating ? 'Rotated' : 'Key added');
    await taLoad(); if (TA === state) { taDrawHead(); taDrawOverview(); }
  });
}
function taExpiresDialog(): void {
  const state = TA;
  if (!state) return;
  const keys = state.data?.keys || [];
  const dialog = whDialog('Expires', `${keys.length > 1 ? `<label>Key<select name="key">${keys.map(k => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('')}</select></label>` : ''}
    <label>Date<input name="expires_on" type="date" value="${esc(keys[0]?.expires_on || '')}"></label>`, '<button class="primary" type="submit">Save</button>');
  whSubmit(dialog, async (data: FormData) => {
    await patch(taPath(state.id, '/keys/' + encodeURIComponent(String(data.get('key') || keys[0]!.id))), {expires_on: data.get('expires_on') || null});
    dialog.close(); toast('Saved');
    await taLoad(); if (TA === state) taDrawOverview();
  });
}
// Reveal: the vault's audited read of the value, shown for a minute.
async function taReveal(cid: string): Promise<void> {
  const dialog = whDialog('Key', '<p class="muted" data-ta-reveal-wait>Loading…</p>');
  try {
    const r = await post(`/v2/credentials/${encodeURIComponent(cid)}/reveal`);
    if (!dialog.open) return;
    const wait = dialog.querySelector('[data-ta-reveal-wait]');
    if (wait) wait.outerHTML = `<label>Value<textarea readonly aria-label="Value">${esc(r.value)}</textarea></label>
      <p class="muted">Logged.</p><button type="button" class="primary" data-ta-copy>Copy</button>`;
    const copy = dialog.querySelector<HTMLButtonElement>('[data-ta-copy]');
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(r.value); toast('Copied'); } catch { dialog.querySelector<HTMLTextAreaElement>('textarea')?.select(); }
    };
    setTimeout(() => { if (dialog.open) dialog.close(); }, 60000);
  } catch (e) { const wait = dialog.querySelector('[data-ta-reveal-wait]'); if (dialog.open && wait) wait.textContent = errorMessage(e, ''); }
}

// ----------------------------------------------------------------- Add tool › API, and a tool's Settings
// One form for both: name, address, where the key goes and the check path. Settings sends only what changed.
function taToolForm(tool?: ToolApiTool | null): string {
  const t: Partial<ToolApiTool> = tool || {}, scheme = String(t.auth_scheme || 'bearer'), [kind = '', named = ''] = scheme.split(/:(.*)/s);
  const auth = TA_AUTH.some(([k]) => k === kind) ? kind : 'bearer';
  return `<label>Name<input name="name" maxlength="80" required autocomplete="off" placeholder="Acme CRM" value="${esc(t.name || '')}"></label>
    <label>Base URL<input name="base_url" type="url" maxlength="300" autocomplete="off" spellcheck="false" placeholder="https://api.acme.example" value="${esc(t.base_url || '')}"></label>
    <label>Key goes in<select name="auth_scheme">${TA_AUTH.map(([k, label]) => `<option value="${k}"${k === auth ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
    <label data-ta-auth-name${['header', 'query'].includes(auth) ? '' : ' hidden'}>Header or parameter name<input name="auth_name" maxlength="100" autocomplete="off" spellcheck="false" placeholder="X-Api-Key" value="${esc(named)}"></label>
    <label>Check path<input name="check_path" maxlength="300" autocomplete="off" spellcheck="false" placeholder="/v1/me" value="${esc(t.check_path || '')}"></label>`;
}
function taToolFields(data: FormData): ToolApiFormFields {
  let auth = String(data.get('auth_scheme') ?? '');
  if (['header', 'query'].includes(auth)) {
    const name = String(data.get('auth_name') || '').trim();
    if (!name) throw new Error('Name the header or parameter.');
    auth += ':' + name;
  }
  return {name: String(data.get('name') || '').trim(), base_url: String(data.get('base_url') || '').trim(), auth_scheme: auth,
    check_path: String(data.get('check_path') || '').trim()};
}
function taToolDialog(tool?: ToolApiTool | null) {
  const dialog = whDialog(tool ? 'Settings' : 'Add API', taToolForm(tool), `<button class="primary" type="submit">${tool ? 'Save' : 'Add'}</button>`);
  const scheme = dialog.querySelector<HTMLSelectElement>('[name=auth_scheme]'), named = dialog.querySelector<HTMLElement>('[data-ta-auth-name]');
  if (!scheme || !named) return dialog;
  scheme.onchange = () => { named.hidden = !['header', 'query'].includes(scheme.value); };
  return dialog;
}
function toolsApiCreate(): void {
  const dialog = taToolDialog(null);
  whSubmit(dialog, async (data: FormData) => {
    const f = taToolFields(data);
    const made = await post('/v2/api-tools', {name: f.name, base_url: f.base_url || undefined, auth_scheme: f.auth_scheme,
      check_path: f.check_path || undefined});
    dialog.close(); toast('Added');
    location.hash = made?.id ? '#/tools/api/' + encodeURIComponent(made.id) : TOOLS_ROUTE;   // the tool, as POST /api-tools answers it
  });
}
function taSettingsDialog(): void {
  const state = TA;
  if (!state?.data) return;
  const tool = state.data.tool, dialog = taToolDialog(tool);
  whSubmit(dialog, async (data: FormData) => {
    const f = taToolFields(data), changed = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== (tool[k as keyof ToolApiTool] || '')));
    if (Object.keys(changed).length) await patch(taPath(state.id), changed);
    dialog.close(); toast('Saved');
    await taLoad(); if (TA === state) { taDrawHead(); if (state.tab !== 'activity') taDrawOverview(); }
  });
}

function toolApiStop() { TA = null; }
