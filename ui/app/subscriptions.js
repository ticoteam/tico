/* ui/app/subscriptions.js — what a bot's turns sign in with. Each computer has one login per AI tool, used by all its
   bots (backend/subscriptions.py); a person's own saved subscription, picked for a task, a bot or a team, takes its
   place wherever the bot runs (backend/model_subscriptions.py). Here: the line under a bot's model, the Subscriptions
   card in Settings > Computers, and the pickers on the bot editor, the task window and a team's page.
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

// ---- the bot editor: the line under Model, and the Subscription row under it
const BOT_SUB_LINE = new Map();          // slug -> GET /v2/bots/{bot}/subscription, so a redraw paints at once
const BOT_SUB_PICKS = new Map();         // slug -> GET /v2/subscription-picks for the bot
const subsToolName = line => harnessRow(line?.harness)?.name || harnessRow(line?.runtime)?.name || harnessWords(line?.runtime) || 'its model';
// {source: 'computer', computer: {runner_id, label} | null, runtime, harness, signed_in, problem}; `here` leaves the
// computer out, for the computer's own pop-up. `effect` is the bot's picker row for that runtime: a person's
// subscription in effect replaces the computer's login, wherever the bot runs.
function subsBotLineHTML(line, here = false, effect = null) {
  if (!line) return '';
  const where = here ? '' : line.computer ? ` on ${subsComputerLabel(line.computer)}` : ' · no computer yet';
  if (effect?.subscription) {
    const owner = firstName(effect.subscription.owner_name);
    return `<span class="sb-sub-text">Runs on ${esc(subsToolName(line))} · ${esc(effect.subscription.name)}${owner ? ` (${esc(owner)})` : ''}${esc(where)}</span>${effect.note ? ` <span class="pill fail sb-sub-warn">${esc(effect.note)}</span>` : ''}`;
  }
  // The server's own words when it has them (a runner too old to say).
  const warn = line.problem || (line.signed_in === false ? 'Signed out' : '');
  return `<span class="sb-sub-text">Runs on ${esc(subsToolName(line))} · Computer's login${esc(where)}</span>${warn ? ` <span class="pill fail sb-sub-warn">${esc(warn)}</span>` : ''}`;
}
function subsBotPaint(rows, slug) {
  const lineEl = rows.querySelector('[data-bot-sub-line]'), view = BOT_SUB_PICKS.get(slug);
  const picks = view?.runtimes || [], first = picks[0];
  // Self-hosted, the line shows only while a subscription is in effect (BOT_SUB_LINE is read on the cloud only).
  const line = BOT_SUB_LINE.get(slug) || (first?.effect?.subscription ? {runtime: first.runtime} : null);
  const effect = line && (picks.find(r => r.runtime === line.runtime) || first)?.effect;
  if (lineEl) { lineEl.innerHTML = subsBotLineHTML(line, false, effect); lineEl.hidden = !line; }
  const row = rows.querySelector('[data-bot-subs-row]'), box = row?.querySelector('[data-bot-subs]');
  if (!box) return;
  row.hidden = !view?.can_pick;
  if (!view?.can_pick) return;
  const one = picks.length === 1;
  box.innerHTML = picks.length && picks.some(r => r.options.length || r.pick)
    ? picks.map(r => `${one ? '' : `<span class="subs-tool">${esc(r.label)}</span>`}${subsSelectHTML(view, r, `${r.label} subscription`)}`).join('')
    : '<a class="muted" href="#/settings/subscriptions">Add one in Settings</a>';
}
// `stage` holds a change until Save bot (ui/app/settings-bot-editor.js); a pick has no bot revision of its own.
async function subsBotMount(rows, slug, stage) {
  if (!rows) return;
  subsBotPaint(rows, slug);
  const [line, view] = await Promise.all([
    S.me?.cloud ? get(`/v2/bots/${encodeURIComponent(slug)}/subscription`).catch(() => null) : null,
    subsPicksGet('bot', slug).catch(() => null)]);
  if (line) BOT_SUB_LINE.set(slug, line); else BOT_SUB_LINE.delete(slug);
  if (view) BOT_SUB_PICKS.set(slug, view); else BOT_SUB_PICKS.delete(slug);
  if (!rows.isConnected) return;
  subsBotPaint(rows, slug);
  const box = rows.querySelector('[data-bot-subs]');
  if (box) box.onchange = ev => {
    const select = ev.target.closest('[data-subs-select]'); if (!select) return;
    const runtime = select.dataset.subsSelect, value = select.value;
    const run = value === select.dataset.current ? null : async rev => { await subsPickPut('bot', slug, runtime, value); return rev; };
    if (stage) { stage(`subscription:${runtime}`, 'subscription', run); return; }
    if (run) void run(0).then(() => subsBotMount(rows, slug), error => { toast(error.message, true); select.value = select.dataset.current; });
  };
}
// ---- Settings > Computers > Subscriptions: a person's own model logins and keys (backend/model_subscriptions.py).
// A value is pasted once and never shown again; Reconnect pastes a new one. The list says where each is picked.
const SUBS_KINDS = [['claude_token', 'Claude'], ['codex_login', 'ChatGPT'], ['anthropic_key', 'Anthropic API key'], ['openai_key', 'OpenAI API key']];
// What each kind asks for: the output of `claude setup-token`, the auth.json `codex login` writes, or a key.
const SUBS_ASK = {claude_token: 'claude setup-token output', codex_login: 'Contents of ~/.codex/auth.json',
  anthropic_key: 'sk-ant-api…', openai_key: 'sk-…'};
const SUBS_STATUS = {working: '<span class="pill ok">working</span>', needs_reconnect: '<span class="pill fail">needs reconnect</span>',
  new: '<span class="muted">not used yet</span>'};
function subsSecretHTML(kind) {
  return kind === 'codex_login'
    ? `<textarea name="secret" rows="3" required spellcheck="false" autocomplete="off" placeholder="${esc(SUBS_ASK[kind])}" aria-label="auth.json"></textarea>`
    : `<input name="secret" type="password" required autocomplete="off" spellcheck="false" placeholder="${esc(SUBS_ASK[kind] || '')}" aria-label="Token or key">`;
}
const subsUsedWord = u => u.scope === 'bot' ? u.name : u.scope === 'team' ? `${u.name} team` : `Task: ${u.name}`;
async function renderSettingsSubscriptions() {
  const el = $('#set-subscriptions'); if (!el) return;
  let rows;
  try { rows = (await get('/v2/me/subscriptions', {fresh: true})).subscriptions || []; }
  catch (error) { el.innerHTML = `<div class="err">${esc(error.message || 'Subscriptions could not be loaded')}</div>`; return; }
  const table = rows.length ? `<div class="scroll"><table class="settings-table"><thead><tr><th>Name</th><th>Kind</th><th>Status</th><th>Used by</th><th></th></tr></thead><tbody>${rows.map(s =>
    `<tr data-sub-row="${esc(s.id)}"><td><strong>${esc(s.name)}</strong>${s.preview ? ` <span class="muted">${esc(s.preview)}</span>` : ''}</td><td>${esc(s.label)}</td>
      <td>${SUBS_STATUS[s.status] || esc(s.status)}</td>
      <td>${s.used_by.length ? esc(s.used_by.map(subsUsedWord).join(', ')) : '<span class="muted">Nothing</span>'}</td>
      <td class="row"><button class="ghost" type="button" data-sub-reconnect="${esc(s.id)}">Reconnect</button><button class="ghost" type="button" data-sub-remove="${esc(s.id)}">Remove</button></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty">No subscriptions yet.</div>';
  // Only the list is redrawn; the form under it keeps what is being typed.
  if (!el.querySelector('[data-subs-list]')) el.innerHTML = `<div data-subs-list></div>
    <form class="row settings-token-form settings-subs-form" id="settings-subs-form">
      <label>Kind <select name="kind" aria-label="Kind">${SUBS_KINDS.map(([k, label]) => `<option value="${k}">${esc(label)}</option>`).join('')}</select></label>
      <label>Name <input name="name" type="text" maxlength="80" autocomplete="off" placeholder="Claude Max" aria-label="Name"></label>
      <span data-subs-secret>${subsSecretHTML(SUBS_KINDS[0][0])}</span>
      <button class="primary" type="submit">Add</button>
    </form>`;
  el.querySelector('[data-subs-list]').innerHTML = table;
  el.querySelectorAll('[data-sub-reconnect]').forEach(button => { button.onclick = () => subsReconnect(rows.find(s => s.id === button.dataset.subReconnect)); });
  el.querySelectorAll('[data-sub-remove]').forEach(button => { button.onclick = () => void subsRemove(rows.find(s => s.id === button.dataset.subRemove)); });
  const form = el.querySelector('#settings-subs-form');
  form.kind.onchange = () => { form.querySelector('[data-subs-secret]').innerHTML = subsSecretHTML(form.kind.value); };
  form.onsubmit = async event => {
    event.preventDefault();
    const secret = form.secret.value.trim();
    if (!secret) { form.secret.focus(); return; }
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try {
      const saved = await post('/v2/me/subscriptions', {kind: form.kind.value, name: form.name.value.trim() || null, secret});
      form.reset(); form.kind.onchange();
      toast(`${saved.name} saved`);
    } catch (error) { settingsFieldError(form, error.message); }
    button.disabled = false;
    await renderSettingsSubscriptions();
  };
}
// Reconnect: a new value for a saved subscription, in a dialog of its own; its picks apply again once saved.
function subsReconnect(sub) {
  if (!sub) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell'; dialog.setAttribute('aria-label', `Reconnect ${sub.name}`);
  dialog.innerHTML = `<header><h2>Reconnect ${esc(sub.name)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <form class="tmodal-body settings-subs-reconnect" data-subs-reconnect>${subsSecretHTML(sub.kind)}
      <div class="onb-actions"><span class="spacer"></span><button class="primary" type="submit">Save</button></div></form>`;
  const form = dialog.querySelector('form');
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  form.onsubmit = async event => {
    event.preventDefault();
    const secret = form.secret.value.trim(); if (!secret) { form.secret.focus(); return; }
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try { await post(`/v2/me/subscriptions/${encodeURIComponent(sub.id)}`, {secret}); toast(`${sub.name} reconnected`); dialog.close(); }
    catch (error) { settingsFieldError(form.secret, error.message); button.disabled = false; }
  };
  dialog.onclose = () => { dialog.remove(); void renderSettingsSubscriptions(); };
  document.body.appendChild(dialog); dialog.showModal();
  form.secret.focus();
}
async function subsRemove(sub) {
  if (!sub) return;
  const used = sub.used_by.length ? ` ${sub.used_by.length === 1 ? 'Its one pick falls' : `Its ${sub.used_by.length} picks fall`} back at once.` : '';
  if (!await settingsConfirm({title: `Remove ${sub.name}?`, text: `Tico deletes it.${used}`, ok: 'Remove'})) return;
  try { await writeRequest('DELETE', `/v2/me/subscriptions/${encodeURIComponent(sub.id)}`); toast(`${sub.name} removed`); }
  catch (error) { toast(error.message, true); }
  await renderSettingsSubscriptions();
}

// ---- pickers: which of the caller's subscriptions a task, bot or team runs on, per AI tool, and what is in effect
// (GET/PUT /v2/subscription-picks). Resolved task -> bot -> team (up the nested teams) -> the computer's login or the
// Gateway. A pick someone else set shows as theirs; only your own subscriptions are offered.
function subsEffectHTML(row, scope) {
  const effect = row.effect || {};
  let words;
  if (effect.subscription) {
    const s = effect.subscription, owner = firstName(s.owner_name);
    const from = effect.source === scope ? '' : effect.source === 'team' ? ` · ${effect.name} team` : effect.source === 'bot' ? " · the bot's" : '';
    words = `${s.name}${owner ? ` (${owner})` : ''}${from}`;
  } else words = effect.source === 'computer' ? `Computer's login · ${effect.computer}` : effect.source === 'gateway' ? 'Tico Gateway' : scope === 'team' ? "Each bot's own" : 'Nothing yet';
  const passed = [...new Map((effect.passed || []).map(p => [p.subscription.id, p])).values()].map(p => `<span class="pill fail">${esc(p.subscription.name)} ${p.why === 'needs_reconnect' ? 'needs reconnect' : 'is off'}</span>`).join(' ');
  return `<span class="muted">Runs on</span> <span class="subs-effect" data-subs-effect="${esc(row.runtime)}">${esc(words)}</span>${passed ? ` ${passed}` : ''}${effect.note ? ` <span class="muted">${esc(effect.note)}</span>` : ''}`;
}
function subsSelectHTML(view, row, label) {
  const pick = row.pick, theirs = pick && !pick.mine && !row.options.some(o => o.id === pick.id);
  return `<select class="settings-inline-select" data-subs-select="${esc(row.runtime)}" data-current="${esc(pick?.id || '')}" aria-label="${esc(label)}">
    <option value="">Not set</option>${theirs ? `<option value="${esc(pick.id)}" selected>${esc(pick.name)} · not yours</option>` : ''}
    ${row.options.map(o => `<option value="${esc(o.id)}"${o.id === pick?.id ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select>`;
}
// Whether a level has anything to show: a pick, something in effect from a subscription, or something to pick.
const subsWorthShowing = view => (view?.runtimes || []).some(r => r.pick || r.effect?.subscription || (view.can_pick && r.options.length));
// One line per AI tool: its picker (for someone who may pick) and what its turns run on now.
function subsPicksHTML(view) {
  return (view.runtimes || []).map(row => `<div class="subs-pick" data-subs-runtime="${esc(row.runtime)}"><span class="subs-tool">${esc(row.label)}</span>
    ${view.can_pick ? subsSelectHTML(view, row, `${row.label} subscription`) : ''}<span class="subs-now">${subsEffectHTML(row, view.scope)}</span></div>`).join('');
}
const subsPicksGet = (scope, id) => get(`/v2/subscription-picks?scope=${encodeURIComponent(scope)}&id=${encodeURIComponent(id)}`, {fresh: true});
const subsPickPut = (scope, id, runtime, subscription) => put('/v2/subscription-picks', {scope, id, runtime, subscription: subscription || null});
// A task's or a team's pickers save when picked; `box` is redrawn from the answer.
async function subsPicksMount(box, scope, id, view = null, always = false) {
  if (!box) return null;
  try { view ||= await subsPicksGet(scope, id); } catch { box.hidden = !always; return null; }
  if (!box.isConnected) return view;
  box.hidden = !always && !subsWorthShowing(view);
  box.querySelector('[data-subs-body]').innerHTML = subsPicksHTML(view);
  box.onchange = async ev => {
    const select = ev.target.closest('[data-subs-select]'); if (!select) return;
    select.disabled = true;
    try { await subsPicksMount(box, scope, id, await subsPickPut(scope, id, select.dataset.subsSelect, select.value), always); }
    catch (error) { toast(error.message, true); select.disabled = false; select.value = select.dataset.current; }
  };
  return view;
}
// The task window's Subscription section (ui/app/task-modal.js), for a bot's task.
const subsTaskHTML = t => String(t.owner || '').startsWith('bot:') && t.run_model !== undefined
  ? '<section class="tsec task-subs" data-task-subs aria-label="Subscription" hidden><h3 class="rail-h">Subscription</h3><div data-subs-body></div></section>' : '';
const subsTaskBind = (d, task) => void subsPicksMount($('[data-task-subs]', d), 'task', String(task.id));
// A team's page (ui/app/dashboard.js): a header button opening its pickers, for whoever may pick or when one is in effect.
async function subsTeamMount(root, gid) {
  const button = root?.querySelector('[data-subs-team]'); if (!button) return;
  const view = await subsPicksGet('team', gid).catch(() => null);
  if (!button.isConnected || !view || !(view.can_pick || subsWorthShowing(view))) return;
  const on = view.runtimes.some(r => r.pick);
  button.hidden = false; button.classList.toggle('on', on);
  button.onclick = () => {
    const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell'; dialog.setAttribute('aria-label', 'Subscription');
    dialog.innerHTML = `<header><h2>Subscription</h2><button class="ghost" type="button" data-close>Close</button></header>
      <div class="tmodal-body subs-team" data-subs-box><div data-subs-body></div>${view.can_pick && !view.runtimes.some(r => r.options.length) ? '<p class="muted">Add one in <a href="#/settings/subscriptions">Settings &gt; Computers</a>.</p>' : ''}</div>`;
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.onclose = () => { dialog.remove(); void subsTeamMount(root, gid); };
    document.body.appendChild(dialog); dialog.showModal();
    void subsPicksMount(dialog.querySelector('[data-subs-box]'), 'team', gid, view, true);
  };
}
