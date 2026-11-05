/* ui/app/tools-api.js — One API tool's page under Tools (ui/app/tools.js routes it): #/tools/api/<id> and its Activity
   tab, #/tools/api/<id>/activity. An API tool holds one key a level (Read, Read and write); who may use it is a level a
   person or bot, which the hub turns into grants on the right key (PUT /api/v2/api-tools/<id>/access/<subject>). Its
   calls go through Tico's proxy and are logged (delivery 'proxy'), or the key sits on the bots' computers ('computer',
   what every tool migrated from before was) until someone moves it to the proxy. A check is an authenticated GET to
   its check path every 30 minutes; the page shows the last 24. Limits per action are not built yet: the card says so.
   Reads GET /api/v2/api-tools/<id> ({tool, keys, access, checks, recent, status, can_manage}) and
   GET …/activity?result=&cursor= (newest first, a page at a time). Revealing a key is the vault's own audited
   request (POST /api/v2/credentials/<id>/reveal). Styles: ui/styles/tools.css.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TA_LEVELS = [['read', 'Read'], ['read_write', 'Read and write']];
const TA_RESULTS = {sent: ['Sent', 'ok'], refused: ['Refused', 'fail'], failed: ['Failed', 'fail']};
const TA_FILTERS = [['', 'All'], ['sent', 'Sent'], ['refused', 'Refused'], ['failed', 'Failed']];
const TA_AUTH = [['bearer', 'Bearer token'], ['basic_user', 'Basic, key as user'], ['basic_password', 'Basic, key as password'],
  ['header', 'Header'], ['query', 'Query parameter']];
const TA_CHECKS = 24;
let TA = null;   // {run, id, tab, data, act: {rows, cursor, result}}
const taLevelName = level => (TA_LEVELS.find(([k]) => k === level) || [level, level || ''])[1];
const taPath = (id, rest = '') => `/v2/api-tools/${encodeURIComponent(id)}${rest}`;
const taHost = url => { try { return new URL(url).host; } catch { return url || ''; } };
const taResult = r => { const [word, tone] = TA_RESULTS[r] || [r || '', '']; return `<span class="pill${tone ? ' ' + tone : ''}" data-result="${esc(r || '')}">${esc(word)}</span>`; };
const taWho = row => row.bot ? botDisplayName(row.bot) : row.actor_name || personDisplay(row.actor || '') || row.actor || '';
const taFace = a => a.kind === 'bot' ? avatar(a.subject.slice(4), 22)
  : personAvatar((S.people || []).find(p => p.id === a.subject.split(':')[1]) || {name: a.name}, 22);

// ----------------------------------------------------------------- the page
async function pageToolApi(id, tab) {
  const run = (TA?.run || 0) + 1, same = TA?.id === id && $('#tool-api');
  TA = {run, id, tab, data: same ? TA.data : null, act: {rows: [], cursor: '', result: same ? TA.act.result : ''}};
  if (!same) $('#main').innerHTML = `<div class="tt tt-page" id="tool-api" data-tool="${esc(id)}">
    <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-ta-crumb>${esc(id)}</span></nav>
    <div data-ta-head></div><div data-ta-body>${skelRows(4, {card: true})}</div></div>`;
  const root = $('#tool-api');
  root.dataset.tab = tab || 'overview';
  if (!root.dataset.bound) { root.dataset.bound = '1'; root.addEventListener('click', ev => void taClick(ev)); }
  try { await taLoad(); }
  catch (e) { if (TA?.run === run) root.querySelector('[data-ta-body]').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (TA?.run !== run) return;
  taDrawHead();
  if (tab === 'activity') await taActivity(true); else taDrawOverview();
}
async function taLoad() {
  const state = TA, d = await get(taPath(state.id), {fresh: true});
  if (TA === state) state.data = d;
}
function taDrawHead() {
  const root = $('#tool-api'), {tool, status, can_manage: manage} = TA.data, base = '#/tools/api/' + encodeURIComponent(TA.id);
  root.querySelector('[data-ta-crumb]').textContent = tool.name;
  const sub = ['API key', taHost(tool.base_url)].filter(Boolean).join(' · ');
  root.querySelector('[data-ta-head]').innerHTML = `<header class="tt-title"><span class="tt-tile lg" aria-hidden="true"><span class="nav-icon">api</span></span>
      <div class="tt-title-text"><h1>${esc(tool.name)}</h1><p class="muted">${esc(sub)}</p></div>
      <div class="tt-title-act">${toolsPill(status)}${manage ? '<button type="button" class="ghost small" data-ta-check>Check now</button>' : ''}</div></header>
    <nav class="tt-tabs" aria-label="${esc(tool.name)}">${[['overview', base, 'Overview'], ['activity', base + '/activity', 'Activity']].map(([k, href, label]) =>
      `<a href="${href}" data-ta-tab="${k}"${(TA.tab || 'overview') === k ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
}

// ----------------------------------------------------------------- Overview
function taDrawOverview() {
  const d = TA.data, body = $('#tool-api [data-ta-body]');
  if (formBusy(body)) return;   // a half-typed Add stays put
  body.innerHTML = `<div class="tt-cols"><div class="tt-col">${taWhoCard(d)}${taRecentCard(d)}</div>
    <div class="tt-col">${taKeysCard(d)}${taCheckCard(d)}${taLimitsCard()}</div></div>`;
}
function taWhoCard(d) {
  const manage = d.can_manage;
  const rows = (d.access || []).map(a => `<tr data-ta-subject-row="${esc(a.subject)}">
      <td class="tt-who-name">${taFace(a)}<span>${esc(a.name || a.subject)}${a.kind === 'bot' ? ' <span class="muted">· bot</span>' : ''}</span></td>
      <td>${manage ? `<div class="theme-seg" role="radiogroup" aria-label="Level for ${esc(a.name)}">${TA_LEVELS.map(([k, label]) =>
        `<button type="button" role="radio" aria-checked="${a.level === k}" data-ta-level="${k}" data-ta-subject="${esc(a.subject)}">${label}</button>`).join('')}</div>` : esc(taLevelName(a.level))}</td>
      <td class="muted tt-used">${a.last_used_at ? esc(ago(a.last_used_at)) : ''}</td>
      <td class="mb-act">${manage ? `<button type="button" class="ghost tt-x" data-ta-remove="${esc(a.subject)}" aria-label="Remove ${esc(a.name)}" title="Remove">×</button>` : ''}</td></tr>`).join('');
  const taken = new Set((d.access || []).map(a => a.subject));
  const people = (S.people || []).filter(p => !p.hidden && !p.archived && !taken.has('human:' + p.id));
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
function taRecentCard(d) {
  const base = '#/tools/api/' + encodeURIComponent(TA.id);
  const rows = (d.recent || []).map(c => `<tr><td class="muted tnum" title="${esc(fmt(c.created_at))}">${esc(ago(c.created_at))}</td><td>${esc(taWho(c))}</td>
      <td class="mono tt-call">${esc(c.method ? `${c.method} ${c.path || ''}` : 'Key read')}</td><td>${taResult(c.result)}</td></tr>`).join('');
  return `<section class="card" id="tool-recent"><header><h2>Recent activity</h2><a class="ghost small settings-head-act" href="${base}/activity">See all</a></header>
    ${rows ? `<table class="tt-log tt-log-short"><tbody>${rows}</tbody></table>` : '<p class="muted">No calls yet.</p>'}</section>`;
}
function taKeysCard(d) {
  const manage = d.can_manage, keys = d.keys || [], tool = d.tool;
  const rows = keys.map(k => `<div class="tt-key" data-ta-key="${esc(k.id)}"><span class="nav-icon" aria-hidden="true">key_vertical</span>
      <span class="tt-key-name"><b>${esc(k.name)}</b>${k.preview ? ` <span class="mono muted">${esc(k.preview)}</span>` : ''}</span>
      <span class="pill${k.access_level === 'read' ? '' : ' in-progress'}" data-key-level="${esc(k.access_level)}">${esc(taLevelName(k.access_level))}</span>
      <span class="tt-key-acts">${manage ? `<button type="button" class="linkish" data-ta-rotate="${esc(k.id)}">Rotate</button>` : ''}<button type="button" class="linkish" data-ta-reveal="${esc(k.id)}">Reveal · logged</button>${
        manage ? `<button type="button" class="linkish danger" data-ta-key-delete="${esc(k.id)}">Remove</button>` : ''}</span></div>`).join('');
  const soonest = keys.map(k => k.expires_on).filter(Boolean).sort()[0];
  const used = keys.map(k => k.last_used_at).filter(Boolean).sort().pop();
  const added = keys.map(k => k.created_at).filter(Boolean).sort()[0];
  const row = (dt, dd) => dd ? `<dt>${esc(dt)}</dt><dd>${dd}</dd>` : '';
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
function taCheckCard(d) {
  const checks = (d.checks || []).slice(0, TA_CHECKS), last = checks[0], tool = d.tool;
  const squares = [...Array(TA_CHECKS - checks.length).fill(null), ...checks.slice().reverse()].map(c => c
    ? `<span class="tt-sq ${c.ok ? 'ok' : 'fail'}" title="${esc(`${fmt(c.checked_at)} · ${c.status_code || ''} ${c.detail || ''}`.trim())}"></span>`
    : '<span class="tt-sq"></span>').join('');
  const line = tool.check_path ? `GET ${tool.check_path}${last ? ` → ${last.status_code || last.detail || ''}` : ''}` : 'No check path';
  return `<section class="card" id="tool-check"><header><h2>Check</h2></header>
    <p class="muted">Every 30 min</p><p class="mono tt-check-line">${esc(line)}</p>
    <div class="tt-squares" role="img" aria-label="${checks.filter(c => c.ok).length} of ${checks.length} checks worked">${squares}</div>
    <p class="muted tt-foot">Last 12 hours</p></section>`;
}
// Limits per action come later (the spec's next step); the card shows where they will be.
const taLimitsCard = () => `<section class="card tt-soon" id="tool-limits"><header><h2>Limits</h2><span class="pill" data-ta-soon>Not available yet</span></header>
  <label class="tt-off"><input type="checkbox" role="switch" class="people-switch" disabled> Check calls against limits</label></section>`;

// ----------------------------------------------------------------- Activity
async function taActivity(fresh) {
  const state = TA, body = $('#tool-api [data-ta-body]');
  if (fresh) {
    body.innerHTML = `<div class="tt-bar"><div class="theme-seg" role="radiogroup" aria-label="Show">${TA_FILTERS.map(([k, label]) =>
      `<button type="button" role="radio" data-ta-show="${k}" aria-checked="${state.act.result === k}">${label}</button>`).join('')}</div></div>
      <div data-ta-log>${skelRows(4)}</div><div class="row" data-ta-more hidden><button type="button" class="ghost" data-ta-more-btn>More</button></div>`;
    state.act.rows = []; state.act.cursor = '';
  }
  const q = new URLSearchParams({limit: '50'});
  if (state.act.result) q.set('result', state.act.result);
  if (state.act.cursor) q.set('cursor', state.act.cursor);
  let d;
  try { d = await get(taPath(state.id, '/activity?' + q), {fresh: true}); }
  catch (e) { if (TA === state) body.querySelector('[data-ta-log]').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (TA !== state || !body.isConnected) return;
  state.act.rows = state.act.rows.concat(d.calls || d.activity || []);
  state.act.cursor = d.next_cursor || '';
  const rows = state.act.rows;
  body.querySelector('[data-ta-log]').innerHTML = rows.length ? `<table class="tt-log"><thead><tr><th>Time</th><th>Who</th><th>Call</th><th>Level</th><th>Result</th></tr></thead>
    <tbody>${rows.map(c => `<tr data-ta-call="${esc(c.id || '')}"><td class="tnum" title="${esc(fmt(c.created_at))}">${esc(ago(c.created_at))}</td><td>${esc(taWho(c))}</td>
      <td class="mono tt-call">${esc(c.method ? `${c.method} ${c.path || ''}` : 'Key read')}${c.status_code ? ` <span class="muted">→ ${esc(c.status_code)}</span>` : ''}</td>
      <td>${esc(taLevelName(c.access_level))}</td><td>${taResult(c.result)}${c.detail && c.result !== 'sent' ? `<span class="muted tt-log-why">${esc(c.detail)}</span>` : ''}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty" data-ta-none>Nothing yet.</div>';
  body.querySelector('[data-ta-more]').hidden = !state.act.cursor;
}

// ----------------------------------------------------------------- actions
async function taClick(ev) {
  const b = ev.target.closest('button'); if (!b || !TA?.data) return;
  const id = TA.id, state = TA;
  // A write, then the page read again and drawn; a refusal is a toast and leaves the page as it was.
  const write = async run => {
    b.disabled = true;
    try { await run(); await taLoad(); if (TA !== state) return; taDrawHead(); if (state.tab !== 'activity') taDrawOverview(); }
    catch (e) { toast(e.message, true); }
    finally { if (b.isConnected) b.disabled = false; }
  };
  if (b.dataset.taShow !== undefined) {
    state.act.result = b.dataset.taShow;
    b.parentElement.querySelectorAll('[data-ta-show]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
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
    return write(() => put(taPath(id, '/access/' + encodeURIComponent(b.dataset.taSubject)), {level: b.dataset.taLevel}));
  }
  if (b.dataset.taRemove) return write(() => put(taPath(id, '/access/' + encodeURIComponent(b.dataset.taRemove)), {level: 'none'}));
  if (b.hasAttribute('data-ta-add-open')) { const f = $('#tool-api [data-ta-add-form]'); f.hidden = false; f.querySelector('select').focus(); return; }
  if (b.hasAttribute('data-ta-add-cancel')) { b.closest('form').hidden = true; return; }
  if (b.hasAttribute('data-ta-proxy')) {
    if (!await settingsConfirm({title: 'Move to proxy?', text: 'Calls go through Tico and are logged. The key leaves the computers on their next run.', ok: 'Move to proxy', danger: false})) return;
    return write(() => patch(taPath(id), {delivery: 'proxy'}));
  }
  if (b.dataset.taReveal) return taReveal(b.dataset.taReveal);
  if (b.dataset.taRotate) return taKeyDialog(TA.data.keys.find(k => k.id === b.dataset.taRotate));
  if (b.hasAttribute('data-ta-key-add')) return taKeyDialog(null);
  if (b.hasAttribute('data-ta-expires')) return taExpiresDialog();
  if (b.dataset.taKeyDelete) {
    const key = TA.data.keys.find(k => k.id === b.dataset.taKeyDelete);
    if (!await settingsConfirm({title: `Remove ${key?.name || 'this key'}?`, text: 'Calls that used it stop working.', ok: 'Remove'})) return;
    return write(() => writeRequest('DELETE', taPath(id, '/keys/' + encodeURIComponent(b.dataset.taKeyDelete))));
  }
}
document.addEventListener('submit', ev => {
  const form = ev.target.closest?.('#tool-api [data-ta-add-form]'); if (!form || !TA) return;
  ev.preventDefault();
  const state = TA, button = form.querySelector('[type=submit]');
  button.disabled = true;
  put(taPath(state.id, '/access/' + encodeURIComponent(form.elements.subject.value)), {level: form.elements.level.value})
    .then(taLoad).then(() => { if (TA === state) { form.reset(); form.hidden = true; taDrawOverview(); } })
    .catch(e => { toast(e.message, true); button.disabled = false; });
});

// Add key, or Rotate one: its name, value and level (a new value replaces the stored one; it is never shown back).
function taKeyDialog(key) {
  const state = TA, rotating = !!key;
  const dialog = whDialog(rotating ? `Rotate ${key.name}` : 'Add key', `${rotating ? '' : '<label>Name<input name="name" maxlength="150" required autocomplete="off" placeholder="Read key"></label>'}
    <label>Value<input name="value" type="password" required autocomplete="off" spellcheck="false"></label>
    ${rotating ? '' : `<label>Username (optional)<input name="username" maxlength="250" autocomplete="off" spellcheck="false"></label>
    <label>Level<select name="access_level">${TA_LEVELS.map(([k, label]) => `<option value="${k}"${k === 'read_write' ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
    <label>Expires (optional)<input name="expires_on" type="date"></label>`}`,
    `<button class="primary" type="submit">${rotating ? 'Rotate' : 'Add key'}</button>`);
  whSubmit(dialog, async data => {
    if (rotating) await patch(taPath(state.id, '/keys/' + encodeURIComponent(key.id)), {value: data.get('value')});
    else await post(taPath(state.id, '/keys'), {name: data.get('name'), value: data.get('value'), username: data.get('username') || undefined,
      access_level: data.get('access_level'), expires_on: data.get('expires_on') || undefined});
    dialog.close(); toast(rotating ? 'Rotated' : 'Key added');
    await taLoad(); if (TA === state) { taDrawHead(); taDrawOverview(); }
  });
}
function taExpiresDialog() {
  const state = TA, keys = state.data.keys || [];
  const dialog = whDialog('Expires', `${keys.length > 1 ? `<label>Key<select name="key">${keys.map(k => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('')}</select></label>` : ''}
    <label>Date<input name="expires_on" type="date" value="${esc(keys[0]?.expires_on || '')}"></label>`, '<button class="primary" type="submit">Save</button>');
  whSubmit(dialog, async data => {
    await patch(taPath(state.id, '/keys/' + encodeURIComponent(data.get('key') || keys[0].id)), {expires_on: data.get('expires_on') || null});
    dialog.close(); toast('Saved');
    await taLoad(); if (TA === state) taDrawOverview();
  });
}
// Reveal: the vault's audited read of the value, shown for a minute.
async function taReveal(cid) {
  const dialog = whDialog('Key', '<p class="muted" data-ta-reveal-wait>Loading…</p>');
  try {
    const r = await post(`/v2/credentials/${encodeURIComponent(cid)}/reveal`);
    if (!dialog.open) return;
    dialog.querySelector('[data-ta-reveal-wait]').outerHTML = `<label>Value<textarea readonly aria-label="Value">${esc(r.value)}</textarea></label>
      <p class="muted">Logged.</p><button type="button" class="primary" data-ta-copy>Copy</button>`;
    dialog.querySelector('[data-ta-copy]').onclick = async () => {
      try { await navigator.clipboard.writeText(r.value); toast('Copied'); } catch { dialog.querySelector('textarea').select(); }
    };
    setTimeout(() => { if (dialog.open) dialog.close(); }, 60000);
  } catch (e) { if (dialog.open) dialog.querySelector('[data-ta-reveal-wait]').textContent = e.message; }
}

// ----------------------------------------------------------------- Add tool › API
function toolsApiCreate() {
  const dialog = whDialog('Add API', `<label>Name<input name="name" maxlength="120" required autocomplete="off" placeholder="Acme CRM"></label>
    <label>Base URL<input name="base_url" type="url" maxlength="500" autocomplete="off" spellcheck="false" placeholder="https://api.acme.example"></label>
    <label>Key goes in<select name="auth_scheme">${TA_AUTH.map(([k, label]) => `<option value="${k}">${label}</option>`).join('')}</select></label>
    <label data-ta-auth-name hidden>Header or parameter name<input name="auth_name" maxlength="100" autocomplete="off" spellcheck="false" placeholder="X-Api-Key"></label>
    <label>Check path<input name="check_path" maxlength="300" autocomplete="off" spellcheck="false" placeholder="/v1/me"></label>`,
    '<button class="primary" type="submit">Add</button>');
  const scheme = dialog.querySelector('[name=auth_scheme]'), named = dialog.querySelector('[data-ta-auth-name]');
  scheme.onchange = () => { named.hidden = !['header', 'query'].includes(scheme.value); };
  whSubmit(dialog, async data => {
    let auth = data.get('auth_scheme');
    if (['header', 'query'].includes(auth)) {
      const name = String(data.get('auth_name') || '').trim();
      if (!name) throw new Error('Name the header or parameter.');
      auth += ':' + name;
    }
    const made = await post('/v2/api-tools', {name: data.get('name'), base_url: data.get('base_url') || undefined, auth_scheme: auth,
      check_path: data.get('check_path') || undefined});
    dialog.close(); toast('Added');
    const id = made?.tool?.id || made?.id;
    location.hash = id ? '#/tools/api/' + encodeURIComponent(id) : TOOLS_ROUTE;
  });
}

function toolApiStop() { TA = null; }
