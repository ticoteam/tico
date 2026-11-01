/* ui/app/subscriptions.js — the company's subscriptions (Settings > Models) and which one each computer runs each AI
   tool on (Settings > Computers). A subscription is a saved plan login or API key, sealed on the server; a computer
   holds at most one per AI tool, and every bot on that computer uses it. Bots never pick one.
   Routes: /v2/model-subscriptions and /v2/computer-subscriptions (backend/model_subscriptions.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A computer as the server names it: {runner_id, label}, or a bare runner id.
function subsComputerLabel(c) {
  if (c && typeof c === 'object') return c.label || subsComputerLabel(c.runner_id);
  return (SETTINGS_DATA?.machines || []).find(m => m.id === c)?.label || c || '';
}
// A saved form is no longer a draft: its fields take their saved values as their own, so the pop-up redraws it.
function subsSettle(form) {
  form.querySelectorAll('input, textarea').forEach(field => { field.defaultValue = field.value; });
  if (form.contains(document.activeElement)) document.activeElement.blur();
}

// ---- what the server holds
// `listed`: the server has the routes (an older one answers neither). `capable`: the computers whose Tico can take a
// subscription; null when the server does not say.
const SUBS = {rows: [], kinds: [], connect: null, assignments: [], capable: null, listed: false, error: '', loaded: false, pending: null};
// The kinds, in the server's order, when it sends none.
const SUBS_KINDS = [
  {kind: 'claude_token', label: 'Claude plan', runtime: 'claude', secret_label: 'Token from claude setup-token'},
  {kind: 'codex_login', label: 'ChatGPT plan', runtime: 'codex', secret_label: "Codex's auth.json"},
  {kind: 'anthropic_key', label: 'Anthropic API key', runtime: 'claude', secret_label: 'API key'},
  {kind: 'openai_key', label: 'OpenAI API key', runtime: 'codex', secret_label: 'API key'}];
// Example values for the fields, and the one line of help each kind gets.
const SUBS_EXAMPLE = {
  claude_token: {name: 'Claude Max', secret: 'sk-ant-oat01-…', help: 'Run claude setup-token on any computer signed in to the plan, then paste the token.'},
  codex_login: {name: 'ChatGPT Pro', secret: '{"auth_mode": "chatgpt", "tokens": {…}}', help: 'Paste ~/.codex/auth.json from a computer where codex login is signed in.'},
  anthropic_key: {name: 'Anthropic API', secret: 'sk-ant-api03-…'},
  openai_key: {name: 'OpenAI API', secret: 'sk-proj-…'}};
const subsToolName = runtime => harnessRow(runtime)?.name || harnessWords(runtime);
const subsKind = kind => SUBS.kinds.find(row => row.kind === kind) || SUBS_KINDS.find(row => row.kind === kind) || {kind, label: kind, runtime: ''};
const subsRuntime = sub => sub?.runtime || subsKind(sub?.kind).runtime;
// The AI tools a subscription can be given to, in the kinds' order (Claude Code, then Codex).
const subsTools = () => [...new Set((SUBS.kinds.length ? SUBS.kinds : SUBS_KINDS).map(row => row.runtime).filter(Boolean))];
const subsById = id => SUBS.rows.find(row => row.id === id);
// The subscription a computer runs a tool on, or null.
const subsAssigned = (runner, runtime) => subsById(SUBS.assignments.find(row => row.runner_id === runner && row.runtime === runtime)?.subscription_id);
// Subscriptions can be given to computers: the server says which computers can take one.
const subsComputersReady = () => SUBS.listed && Array.isArray(SUBS.capable);
const subsMayEdit = () => settingsIsAdmin();
// Gateway on: a computer with no subscription for a tool runs it on Tico Gateway (models preset, backend/models_preset.py).
const subsGatewayOn = () => ['subscriptions-gateway', 'gateway'].includes(SETTINGS_PROVIDERS_VIEW?.preset ?? 'subscriptions-gateway');

async function subsLoad() {
  if (SUBS.pending) return SUBS.pending;
  SUBS.pending = Promise.all([
    get('/v2/model-subscriptions', {fresh: true}).catch(error => ({error})),
    get('/v2/computer-subscriptions', {fresh: true}).catch(() => null)
  ]).then(([list, view]) => {
    SUBS.listed = Array.isArray(list?.subscriptions);
    SUBS.error = list?.error instanceof Error ? list.error.message : '';
    SUBS.rows = SUBS.listed ? list.subscriptions : [];
    SUBS.kinds = Array.isArray(list?.kinds) && list.kinds.length ? list.kinds : SUBS_KINDS;
    SUBS.connect = Array.isArray(list?.connect) ? list.connect : null;
    SUBS.assignments = Array.isArray(view?.assignments) ? view.assignments : [];
    SUBS.capable = Array.isArray(view?.capable) ? view.capable : null;
    SUBS.loaded = true;
  }).finally(() => { SUBS.pending = null; });
  return SUBS.pending;
}
// A refusal's code and its extra facts (backend Problem: {error: {code, detail, ...extra}}).
const subsProblem = error => { const body = error?.body?.error; return body && typeof body === 'object' ? body : {}; };
const subsRunning = problem => Number(problem.running ?? problem.detail?.running ?? 0) || 0;
const subsPlural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const subsStopWords = n => `${subsPlural(n, 'running task')} ${n === 1 ? 'stops and restarts' : 'stop and restart'}`;

// ---- Settings > Models > Subscriptions
// Usage: what is left in the plan's window (like the computers' chips), or the month's spend for an API key.
function subsUsageHTML(sub) {
  const usage = sub.usage || {};
  if (typeof usage.used_pct === 'number') {
    const used = Math.max(0, Math.min(100, Math.round(usage.used_pct)));
    const words = `${used}% used${usage.window ? ` in the ${usage.window === 'week' ? 'week' : usage.window + ' window'}` : ''}`;
    const level = used >= 95 ? ' bad' : used >= 80 ? ' warn' : '';
    return `<span class="subs-usage" title="${esc(words)}">${usage.window ? `<span class="subs-usage-w">${esc(usage.window)}</span>` : ''}<span class="subs-bar${level}" aria-hidden="true"><b style="width:${used}%"></b></span><span class="tnum">${used}%</span><span class="sr-only"> used</span></span>`;
  }
  if (typeof usage.cost_usd_month === 'number') {
    const cost = usage.cost_usd_month >= 100 ? Math.round(usage.cost_usd_month).toLocaleString() : usage.cost_usd_month.toFixed(2).replace(/\.00$/, '');
    return `<span class="subs-usage tnum">$${esc(cost)} this month</span>`;
  }
  return '';
}
function subsStatusHTML(sub) {
  if (sub.status !== 'needs_reconnect') return '';
  const title = sub.status_detail ? ` title="${esc(sub.status_detail)}"` : '';
  return subsMayEdit() ? `<button type="button" class="pill waiting subs-reconnect" data-subs-reconnect="${esc(sub.id)}"${title}>Reconnect</button>`
    : `<span class="pill waiting"${title}>Reconnect</span>`;
}
function subsRowHTML(sub) {
  const count = (sub.computers || []).length;
  const names = (sub.computers || []).map(subsComputerLabel).join(', ');
  return `<tr data-subs-row="${esc(sub.id)}">
    <td class="subs-name">${esc(sub.name)}</td>
    <td class="muted subs-kind">${esc(sub.label || subsKind(sub.kind).label)}</td>
    <td class="subs-count" title="${esc(names || 'No computers')}"><span class="nav-icon" aria-hidden="true">computer</span><span class="tnum">${count}</span><span class="sr-only"> ${count === 1 ? 'computer' : 'computers'}</span></td>
    <td class="subs-use">${subsStatusHTML(sub) || subsUsageHTML(sub)}</td>
    <td class="subs-more-cell">${subsMayEdit() ? `<button type="button" class="ghost subs-more" data-subs-more="${esc(sub.id)}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${esc(sub.name)}">⋯</button>` : ''}</td></tr>`;
}
async function renderSettingsSubs() {
  const el = $('#set-subs'); if (!el) return;
  await subsLoad();
  if (!el.isConnected) return;
  const add = $('#subs-add');
  if (add) { add.hidden = !SUBS.listed || !subsMayEdit(); add.onclick = () => subsDialog('add'); }
  let html;
  if (!SUBS.listed) html = `<p class="${SUBS.error ? 'err' : 'muted'}">${esc(SUBS.error || 'This server does not keep subscriptions yet.')}</p>`;
  else if (!SUBS.rows.length) html = `<div class="subs-empty"><span class="muted">No subscriptions yet.</span>${subsMayEdit() ? '<button type="button" class="ghost" data-subs-add>Add subscription</button>' : ''}</div>`;
  else html = `<div class="scroll"><table class="settings-table subs-table"><thead><tr><th>Name</th><th>Kind</th><th><span class="sr-only">Computers</span></th><th>Usage</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${SUBS.rows.map(subsRowHTML).join('')}</tbody></table></div>`;
  const restore = computerFocusRestore(el);
  patchHTML(el, html);
  restore();
  el.onclick = event => {
    if (event.target.closest('[data-subs-add]')) { subsDialog('add'); return; }
    const reconnect = event.target.closest('[data-subs-reconnect]');
    if (reconnect) { subsDialog('reconnect', subsById(reconnect.dataset.subsReconnect)); return; }
    const more = event.target.closest('[data-subs-more]');
    if (more) subsMenu(more, subsById(more.dataset.subsMore));
  };
}
// Rename, Reconnect, Remove: the shared menu pop-over (styles/task-list.css .tl-pop; a sheet on a phone).
function subsMenu(anchor, sub) {
  if (!sub) return;
  let pop = $('#subs-menu');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'subs-menu'; pop.className = 'tl-pop subs-menu'; pop.popover = 'auto';
    pop.addEventListener('toggle', event => {
      if (event.newState !== 'closed') return;
      pop.anchor?.setAttribute('aria-expanded', 'false');
      if (pop.anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) pop.anchor.focus();
    });
    document.body.append(pop);
  }
  pop.anchor = anchor;
  pop.innerHTML = `<div class="tl-menu" role="menu" aria-label="${esc(sub.name)}">
    <button type="button" class="tl-mi" role="menuitem" data-subs-act="rename">Rename</button>
    <button type="button" class="tl-mi" role="menuitem" data-subs-act="reconnect">Reconnect</button>
    <button type="button" class="tl-mi danger" role="menuitem" data-subs-act="remove">Remove</button></div>`;
  pop.onclick = event => {
    const act = event.target.closest('[data-subs-act]')?.dataset.subsAct;
    if (!act) return;
    pop.hidePopover();
    if (act === 'remove') void subsRemove(sub); else subsDialog(act, sub);
  };
  pop.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(pop, anchor);
  pop.querySelector('.tl-mi')?.focus();
}
// The secret field for a kind: Codex's auth.json is a few lines of JSON, everything else one token or key.
// Connect: a computer runs the plan's sign-in and Tico saves the result, so there is nothing to paste
// (backend/model_subscriptions.py `connect`). A plan whose sign-in has no computer to run on says so; a server that
// does not say which can (`SUBS.connect`) has no such route, so no button.
function subsConnectHTML(kind) {
  if (!subsKind(kind).connect || !SUBS.connect) return '';
  const ready = SUBS.connect.includes(kind);
  return `<div class="subs-connect"><button class="primary" type="button" data-subs-connect${ready ? '' : ' disabled'}>Connect ${esc(subsKind(kind).label)}</button>
    ${ready ? '<span class="subs-connect-or">or paste</span>' : '<span class="subs-connect-or">Needs a computer online. Or paste:</span>'}</div>`;
}
function subsSecretHTML(kind) {
  const row = subsKind(kind), example = SUBS_EXAMPLE[kind] || {};
  const label = row.secret_label || SUBS_KINDS.find(k => k.kind === kind)?.secret_label || 'Token or key';
  const help = row.help || example.help || '';
  const field = kind === 'codex_login'
    ? `<textarea name="secret" rows="4" spellcheck="false" autocomplete="off" required placeholder="${esc(example.secret || '')}"></textarea>`
    : `<input name="secret" type="password" spellcheck="false" autocomplete="off" required placeholder="${esc(example.secret || '')}">`;
  return `${subsConnectHTML(kind)}<div class="subs-field" data-subs-secret><span class="k"><label for="subs-secret">${esc(label)}</label>${help ? ` ${helpBubble(help)}` : ''}</span>${field.replace('name="secret"', 'name="secret" id="subs-secret"')}</div>`;
}
// The kind: a search box over the kinds, Claude plan first. Typing filters; arrows move; Enter or a click picks.
function subsKindComboHTML(kind) {
  const rows = SUBS.kinds.length ? SUBS.kinds : SUBS_KINDS;
  return `<div class="subs-combo" data-subs-combo>
    <input type="text" role="combobox" aria-expanded="false" aria-controls="subs-kind-list" aria-autocomplete="list" autocomplete="off" spellcheck="false"
      id="subs-kind" value="${esc(subsKind(kind).label)}" data-kind="${esc(kind)}">
    <ul class="subs-combo-list" id="subs-kind-list" role="listbox" aria-label="Kind" hidden>${rows.map(row =>
      `<li role="option" id="subs-kind-${esc(row.kind)}" data-kind="${esc(row.kind)}" aria-selected="${row.kind === kind}">${esc(row.label)}</li>`).join('')}</ul></div>`;
}
function subsKindComboWire(form, onPick) {
  const box = form.querySelector('[data-subs-combo]'), input = box.querySelector('input'), list = box.querySelector('[role=listbox]');
  const options = () => [...list.querySelectorAll('[role=option]')];
  const visible = () => options().filter(o => !o.hidden);
  let active = -1;
  const mark = i => {
    const shown = visible(); active = shown.length ? (i + shown.length) % shown.length : -1;
    options().forEach(o => o.classList.remove('on'));
    const cur = shown[active];
    if (cur) { cur.classList.add('on'); input.setAttribute('aria-activedescendant', cur.id); cur.scrollIntoView({block: 'nearest'}); }
    else input.removeAttribute('aria-activedescendant');
  };
  const open = (filter = '') => {
    const q = filter.trim().toLowerCase();
    options().forEach(o => { o.hidden = !!q && !o.textContent.toLowerCase().includes(q); });
    list.hidden = false; input.setAttribute('aria-expanded', 'true');
    mark(Math.max(0, visible().findIndex(o => o.dataset.kind === input.dataset.kind)));
  };
  const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
  const pick = option => {
    if (!option) return;
    const changed = option.dataset.kind !== input.dataset.kind;
    input.dataset.kind = option.dataset.kind; input.value = option.textContent;
    options().forEach(o => o.setAttribute('aria-selected', String(o === option)));
    close();
    if (changed) onPick(option.dataset.kind);
  };
  input.onfocus = () => { input.select(); };
  input.onclick = () => { if (list.hidden) open(); };
  input.oninput = () => open(input.value);
  input.onkeydown = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (list.hidden) open(); else mark(active + (event.key === 'ArrowDown' ? 1 : -1));
    } else if (event.key === 'Enter') {
      if (!list.hidden) { event.preventDefault(); pick(visible()[active]); }
    } else if (event.key === 'Escape') {
      // Escape closes the list first; the dialog closes on the next one.
      if (!list.hidden) { event.preventDefault(); event.stopPropagation(); close(); input.value = subsKind(input.dataset.kind).label; }
    }
  };
  input.onblur = () => setTimeout(() => {
    if (box.contains(document.activeElement)) return;
    close(); input.value = subsKind(input.dataset.kind).label;
  }, 120);
  list.onmousedown = event => event.preventDefault();   // keeps focus in the box while a click picks
  list.onclick = event => pick(event.target.closest('[role=option]'));
}
// Add (kind, name, value), Rename (name) or Reconnect (a new value) in one small dialog. Giving a subscription to
// computers is on the Computers tab.
function subsDialog(mode, sub = null) {
  if (mode !== 'add' && !sub) return;
  const kind = sub?.kind || (SUBS.kinds[0] || SUBS_KINDS[0]).kind;
  const title = mode === 'add' ? 'Add subscription' : mode === 'rename' ? `Rename ${sub.name}` : `Reconnect ${sub.name}`;
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal tmodal-shell subs-dialog'; dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<form data-subs-form="${mode}" novalidate><header><h2>${esc(title)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="tmodal-body subs-form">
      ${mode === 'add' ? `<div class="subs-field"><span class="k"><label for="subs-kind">Kind</label></span>${subsKindComboHTML(kind)}</div>` : ''}
      ${mode !== 'reconnect' ? `<label class="subs-field"><span class="k">Name</span><input name="name" type="text" required maxlength="80" autocomplete="off" value="${esc(sub?.name || '')}" placeholder="${esc(SUBS_EXAMPLE[kind]?.name || '')}"></label>` : ''}
      ${mode !== 'rename' ? `<div data-subs-secret-slot>${subsSecretHTML(kind)}</div>` : ''}
    </div>
    <div class="tmodal-foot onb-actions"><button class="ghost" type="button" data-close>Cancel</button><span class="spacer"></span><button class="primary" type="submit">Save</button></div></form>`;
  const form = dialog.querySelector('form');
  dialog.querySelectorAll('[data-close]').forEach(button => { button.onclick = () => dialog.close(); });
  // One primary button: Connect, until there is a value to Save.
  const saveLook = () => {
    const paste = !form.querySelector('[data-subs-connect]:not([disabled])') || !!form.elements.secret?.value.trim();
    form.querySelector('button[type=submit]').className = paste ? 'primary' : 'ghost';
  };
  if (mode === 'add') subsKindComboWire(form, next => {
    form.querySelector('[data-subs-secret-slot]').innerHTML = subsSecretHTML(next);
    form.elements.name.placeholder = SUBS_EXAMPLE[next]?.name || '';
    saveLook();
  });
  // A red line goes once the person types in that field again.
  form.oninput = event => { if (event.target.name) settingsFieldClear(event.target); saveLook(); };
  saveLook();
  form.onclick = async event => {
    const button = event.target.closest('[data-subs-connect]');
    if (!button || button.disabled) return;
    form.querySelectorAll('[data-field-err]').forEach(line => line.remove());
    const name = form.elements.name?.value.trim();
    if (form.elements.name && !name) { settingsFieldError(form.elements.name, 'Enter a name'); form.elements.name.focus(); return; }
    const chosen = mode === 'add' ? form.querySelector('#subs-kind').dataset.kind : sub.kind;
    const start = confirm => post('/v2/model-subscriptions/connect', mode === 'add' ? {kind: chosen, name} : confirm ? {subscription: sub.id, confirm} : {subscription: sub.id});
    button.disabled = true;
    try {
      // A new value restarts the runs on each computer given it: with runs going, the page asks before the sign-in.
      const first = await subsConfirmed(start, running => ({title: `Reconnect ${sub?.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Reconnect', danger: false}));
      if (!first) { button.disabled = false; return; }
      // Closed while that was on its way: nobody is left to finish the sign-in it started.
      if (!dialog.open) { await post(`/v2/computers/${encodeURIComponent(first.runner_id)}/logins/${encodeURIComponent(first.id)}/cancel`, {}).catch(() => {}); return; }
      TicoModelLogin.open({into: dialog, runnerId: first.runner_id, runtime: first.runtime, machine: first.computer || '', connect: {
        title: mode === 'add' ? `Connect ${name}` : `Reconnect ${sub.name}`, first, start: () => start(true),
        done: mode === 'add' ? `${name} added` : `${sub.name} reconnected`}});
    } catch (error) {
      button.disabled = false;
      if (subsProblem(error).code === 'name') { settingsFieldError(form.elements.name, error.message); form.elements.name.focus(); } else toast(error.message, true);
    }
  };
  form.onsubmit = async event => {
    event.preventDefault();
    form.querySelectorAll('[data-field-err]').forEach(line => line.remove());
    const name = form.elements.name?.value.trim(), secret = form.elements.secret?.value.trim();
    if (form.elements.name && !name) { settingsFieldError(form.elements.name, 'Enter a name'); form.elements.name.focus(); return; }
    if (form.elements.secret && !secret) { settingsFieldError(form.elements.secret, 'Paste the token or key'); form.elements.secret.focus(); return; }
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try {
      if (mode === 'add') await post('/v2/model-subscriptions', {kind: form.querySelector('#subs-kind').dataset.kind, name, secret});
      else if (mode === 'rename') await post(`/v2/model-subscriptions/${encodeURIComponent(sub.id)}`, {name});
      else {
        // A new value restarts the runs on each computer given it: with runs going, the page asks first.
        const done = await subsConfirmed(confirm => post(`/v2/model-subscriptions/${encodeURIComponent(sub.id)}`, confirm ? {secret, confirm} : {secret}),
          running => ({title: `Reconnect ${sub.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Reconnect', danger: false}));
        if (!done) { button.disabled = false; return; }
      }
      if (form.elements.secret) form.elements.secret.value = '';   // nothing on the page keeps the value once sent
      toast(mode === 'add' ? `${name} added` : mode === 'rename' ? 'Renamed' : `${sub.name} reconnected`);
      dialog.close();
    } catch (error) {
      const code = subsProblem(error).code;
      const field = code === 'name' ? form.elements.name : code === 'kind' ? form.querySelector('#subs-kind') : code === 'secret' ? form.elements.secret : null;
      if (field) { settingsFieldError(field, error.message); field.focus(); } else toast(error.message, true);
      button.disabled = false;
    }
  };
  dialog.onclose = () => { dialog.remove(); void subsRefresh(); };
  document.body.appendChild(dialog); dialog.showModal();
  (form.elements.name || form.elements.secret)?.focus();
}
// A write that may stop runs: send it; when the server answers 409 confirm_restart, ask in the page with the count
// (`ask(running)` gives the settingsConfirm options) and, on yes, send it again with confirm (a new body, so a fresh
// Idempotency-Key). Resolves to the answer, or null when the person says no.
async function subsConfirmed(write, ask) {
  try { return await write(false); }
  catch (error) {
    const problem = subsProblem(error);
    if (problem.code !== 'confirm_restart') throw error;
    if (!await settingsConfirm(ask(subsRunning(problem)))) return null;
    return write(true);
  }
}
// Remove: asks first; with runs going on its computers, says how many restart and asks once more.
async function subsRemove(sub) {
  const count = (sub.computers || []).length;
  const text = count ? `${subsPlural(count, 'computer')} stop using it.` : 'Tico deletes the saved value.';
  if (!await settingsConfirm({title: `Remove ${sub.name}?`, text, ok: 'Remove'})) return;
  try {
    const done = await subsConfirmed(confirm => writeRequest('DELETE', `/v2/model-subscriptions/${encodeURIComponent(sub.id)}${confirm ? '?confirm=1' : ''}`),
      running => ({title: `Remove ${sub.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Remove'}));
    if (!done) return;
    toast(`${sub.name} removed`);
  } catch (error) { toast(error.message, true); }
  await subsRefresh();
}
// Reload, then redraw whatever shows subscriptions.
async function subsRefresh() {
  await subsLoad();
  if ($('#set-subs')) void renderSettingsSubs();
  if ($('#set-machines') && SETTINGS_DATA.loaded) renderSettingsMachines();
}

// ---- Settings > Computers: one picker per computer and AI tool
const subsSignedInHere = (machine, runtime) => machine.readiness?.runtimes?.[runtime]?.authenticated === 'ready';
// What runs the tool when the computer has no subscription for it.
const subsNoneWords = (machine, runtime) => subsSignedInHere(machine, runtime) ? 'Signed in here' : subsGatewayOn() ? 'Gateway' : 'None';
function subsPickerHTML(machine, runtime) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  // An older runner still runs on its own (or a shared) login: its chip, and Needs update beside it.
  if (!SUBS.capable.includes(machine.id)) {
    const chips = machineShown(machine).filter(conn => conn.runtime === runtime).map(conn => machineChipHTML(machine, conn)).join('');
    return `<span class="subs-cell">${chips ? `<span class="machine-chips">${chips}</span>` : ''}<span class="pill waiting" data-subs-needs-update title="Update Tico on ${esc(machine.label)} to give it a subscription">Needs update</span></span>`;
  }
  const current = subsAssigned(machine.id, runtime);
  const options = SUBS.rows.filter(sub => subsRuntime(sub) === runtime);
  const tool = subsToolName(runtime);
  const select = `<select class="subs-pick" data-subs-pick="${esc(runtime)}" data-runner="${esc(machine.id)}" data-current="${esc(current?.id || '')}" aria-label="${esc(`${tool} on ${machine.label}`)}"${subsMayEdit() ? '' : ' disabled'}>
    <option value="">${esc(subsNoneWords(machine, runtime))}</option>${options.map(sub =>
      `<option value="${esc(sub.id)}"${sub.id === current?.id ? ' selected' : ''}>${esc(sub.name)}</option>`).join('')}</select>`;
  return `<span class="subs-cell">${select}${current?.status === 'needs_reconnect' ? subsStatusHTML(current) : ''}</span>`;
}
// A change: PUT, and when runs would stop, an in-page confirm with the counts, then PUT again with confirm.
async function subsPick(select) {
  const runner = select.dataset.runner, runtime = select.dataset.subsPick, was = select.dataset.current || '';
  const machine = (SETTINGS_DATA.machines || []).find(m => m.id === runner);
  const computer = machine?.label || runner, tool = subsToolName(runtime);
  const id = select.value || null, name = id ? subsById(id)?.name || id : select.options[0]?.textContent || 'None';
  const body = {runner_id: runner, runtime, subscription: id};
  select.disabled = true;
  try {
    const done = await subsConfirmed(confirm => put('/v2/computer-subscriptions', {...body, confirm}),
      running => ({title: `Switch ${computer}'s ${tool} to ${name}?`,
        text: `${subsStopWords(running)}, and every ${tool} session on this computer starts fresh.`, ok: 'Switch', danger: false}));
    if (!done) { select.value = was; return; }
    SUBS.assignments = SUBS.assignments.filter(row => !(row.runner_id === runner && row.runtime === runtime));
    if (id) SUBS.assignments.push({runner_id: runner, runtime, subscription_id: id});
    select.dataset.current = id || '';
    const stopped = Number(done?.stopped) || 0;
    toast(`${computer}'s ${tool}: ${name}${stopped ? ` · ${subsPlural(stopped, 'task')} restarted` : ''}`);
  } catch (error) { toast(error.message, true); select.value = was; }
  finally { select.disabled = !subsMayEdit(); }
  await subsRefresh();
}
