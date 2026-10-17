/* ui/app/subscriptions.js — Subscriptions: the named AI logins on each computer (Settings > AI providers), the one a
   group picks, a bot's own choice, and the line under a bot's model saying which one it will use.
   The server stores stable connection IDs and display names; the logins stay on the computers (runner-rs/src/profiles.rs).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET /v2/subscriptions: {profiles_by_computer: [{runner_id, label, profiles: [{name, runtimes: {codex: {signed_in}}}]}],
// assignments: [{scope, target, profile}]}. An older server answers 404 and every part here stays away.
let SUBS = null;
let SUBS_REFRESH_TIMER = null;
// A subscription signs in from here to a CLI whose manifest has both `browser_sign_in` and `subscription_profiles`;
// weekly usage shows for one with `weekly_usage`, and only `weekly_usage = "refresh"` can be asked to refresh.
const subsRuntimes = () => harnessesWith(c => c.browser_sign_in && c.subscription_profiles).map(row => [row.id, row.name]);
const subsWeeklyName = runtime => (harnessCaps(runtime).weekly_usage && harnessRow(runtime)?.name) || '';
async function subsLoad() {
  try { [SUBS] = await Promise.all([get('/v2/subscriptions').then(subsNormal), loadHarnessRows()]); }
  catch { SUBS = null; }
  return SUBS;
}
function subsNormal(d) {
  const computers = (d?.profiles_by_computer || []).map(c => ({runner_id: c.runner_id, label: c.label || c.runner_id,
    profiles: (c.profiles || []).map(p => {
      if (typeof p === 'string') return {name: p, runtimes: {}};
      let runtimes = p.runtimes || p.runtimes_json || {};
      if (typeof runtimes === 'string') { try { runtimes = JSON.parse(runtimes); } catch { runtimes = {}; } }
      return {name: p.name || p.profile, id: p.id, display_name: p.display_name || p.name || p.profile, runtimes: runtimes || {}};
    }).filter(p => p.name)}));
  return {computers, assignments: d?.assignments || []};
}
const subsNames = () => [...new Set((SUBS?.computers || []).flatMap(c => c.profiles.map(p => p.name)))].sort((a, b) => a.localeCompare(b));
// Labels change independently from immutable local profile keys and existing assignments.
function subsDisplayName(runner, profile) {
  return SUBS?.computers.find(c => c.runner_id === runner)?.profiles.find(p => p.name === profile)?.display_name || profile;
}
function subsRenameHTML(c, p) {
  if (!p.id || !subsMaySignIn(c)) return '';
  return `<form data-subs-rename data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}">
    <label>Name <input name="display_name" required maxlength="80" value="${esc(p.display_name || p.name)}"></label>
    <button class="ghost" type="submit">Save name</button><span class="muted">Login and assignments stay the same.</span><span role="status"></span>
  </form>`;
}
async function subsRename(form) {
  if (!form.reportValidity()) return;
  const button = form.querySelector('button'); button.disabled = true;
  try {
    await put('/v2/subscriptions/name', {runner_id: form.dataset.runner, profile: form.dataset.profile,
      display_name: form.elements.display_name.value});
    BOT_SUB_LINE.clear();
    await renderSettingsSubs(true);
    toast('Subscription renamed');
  } catch (e) { form.querySelector('[role=status]').textContent = e.message; }
  finally { button.disabled = false; }
}
// A computer as the server names it: {runner_id, label}, or a bare runner id.
function subsComputerLabel(c) {
  if (c && typeof c === 'object') return c.label || subsComputerLabel(c.runner_id);
  return SUBS?.computers.find(x => x.runner_id === c)?.label || (SETTINGS_DATA?.machines || []).find(m => m.id === c)?.label || c || '';
}
// Subscription names are lowercase words joined by hyphens (runner-rs/src/profiles.rs): "Acme Research" becomes acme-research.
const subsSlug = name => String(name || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
// Assignments are keyed by group id or bot slug.
const subsAssigned = (scope, target) => (SUBS?.assignments || []).find(x => x.scope === scope && x.target === target)?.profile || '';
const subsGroups = () => S.orgGroups || [];
// The subscription a group gives its bots: its own, else the nearest group above it that has one.
function subsGroupProfile(groupId) {
  const seen = new Set();
  for (let g = subsGroups().find(x => x.id === groupId); g && !seen.has(g.id); g = subsGroups().find(x => x.id === g.parent)) {
    seen.add(g.id);
    const profile = subsAssigned('group', g.id);
    if (profile) return {profile, group: g};
  }
  return {profile: '', group: subsGroups().find(x => x.id === groupId) || null};
}
// True or false as the computer reported it; null when it did not say (an older runner, a computer this person
// cannot see, or a runtime it could not check). Without a runtime: whether any runtime is signed in.
function subsSignedIn(runnerId, profile, runtime = '') {
  const p = SUBS?.computers.find(c => c.runner_id === runnerId)?.profiles.find(x => x.name === profile);
  if (!p) return null;
  if (runtime) { const v = p.runtimes?.[runtime]?.signed_in; return typeof v === 'boolean' ? v : null; }
  const all = Object.values(p.runtimes || {}).map(r => r?.signed_in);
  return all.includes(true) ? true : all.includes(false) ? false : null;
}
// The computers a group's bots sit on where its subscription is known not to be signed in (nested groups count, unless
// they or the bot pick their own). Unknown is not a gap.
function subsGroupGaps(group) {
  const profile = subsAssigned('group', group.id);
  if (!profile) return [];
  const gaps = new Set();
  for (const e of S.emps || []) {
    if (!e.team || subsAssigned('bot', e.name)) continue;
    const from = subsGroupProfile(e.team);
    if (from.group?.id !== group.id || !e.machine?.runner_id) continue;
    if (subsSignedIn(e.machine.runner_id, profile, e.resolved_runtime || '') === false) gaps.add(subsComputerLabel(e.machine.runner_id));
  }
  return [...gaps].sort();
}

// ---- Settings > AI providers > Subscriptions
const subsMaySignIn = c => settingsIsAdmin() || (SETTINGS_DATA?.machines || []).some(m =>
  m.id === (c.runner_id || c.id) && m.operator === S.me?.id);
function subsRuntimeHTML(c, p) {
  const owner = subsMaySignIn(c);
  const reported = Object.keys(p.runtimes || {}).length;
  const shown = reported ? subsRuntimes().filter(([id]) => p.runtimes[id]) : subsRuntimes();
  // Not known for any runtime: no state to show, but an owner can still sign it in.
  if (shown.every(([id]) => typeof p.runtimes?.[id]?.signed_in !== 'boolean') && reported) {
    return owner ? shown.map(([id, label]) => `<button class="ghost subs-signin" type="button" data-model-login data-runner="${esc(c.runner_id)}" data-runtime="${esc(id)}" data-profile="${esc(p.name)}" data-machine="${esc(c.label)}" aria-label="Sign in ${esc(p.name)} to ${esc(label)} on ${esc(c.label)}">Sign in to ${esc(label)}</button>`).join('') : '';
  }
  return shown.map(([id, label]) => {
    const v = reported ? p.runtimes[id]?.signed_in : false;
    if (typeof v !== 'boolean') return '';                 // not known: nothing to say
    return `<span class="subs-rt"><span class="pill ${v ? 'ok' : 'waiting'}">${esc(label)} · ${v ? 'signed in' : 'not signed in'}</span>${!v && owner
      ? `<button class="ghost subs-signin" type="button" data-model-login data-runner="${esc(c.runner_id)}" data-runtime="${esc(id)}" data-profile="${esc(p.name)}" data-machine="${esc(c.label)}" aria-label="Sign in ${esc(p.name)} to ${esc(label)} on ${esc(c.label)}">Sign in</button>` : ''}</span>`;
  }).join('');
}
// Allowance is a provider percentage, not a token estimate. Never roll an old report forward.
const subsWeeklyRuntimes = p => Object.entries(p.runtimes || {}).filter(([runtime, state]) =>
  subsWeeklyName(runtime) && (state.signed_in === true || state.weekly || subsRuntimes().some(([id]) => id === runtime)));
function subsWeeklyHTML(c, p) {
  return subsWeeklyRuntimes(p).map(([runtime, state]) => {
      const weekly = state.weekly, updated = Date.parse(weekly?.reported_at), reset = Date.parse(weekly?.resets_at);
      const expired = Number.isFinite(reset) && reset <= Date.now();
      const stale = Number.isFinite(updated) && Date.now() - updated > 86400000;
      const percent = weekly?.used_percent;
      const value = Number.isFinite(percent) ? `${Math.round(percent)}% used` : 'usage unknown';
      const when = date => new Date(date).toLocaleString();
      const refresh = state.refresh;
      const refreshLabels = {requested: 'Refresh queued', succeeded: 'Last refresh succeeded', failed: 'Refresh failed; last reading kept', unavailable: 'Weekly refresh unavailable; use a provider reading', expired: 'Refresh timed out; check the computer connection', outdated: 'Sign-in changed; refresh weekly usage again'};
      const refreshInfo = refresh ? `${refreshLabels[refresh.state] || 'Refresh unknown'}${refresh.updated_at ? ` · ${when(Date.parse(refresh.updated_at))}` : ''}` : '';
      const authInfo = state.signed_in === true ? 'Signed in' : state.signed_in === false ? 'Sign-in needed' : 'Sign-in unknown';
      const info = weekly ? `${weekly.source === 'manual' ? 'Manually recorded' : 'Provider reported'} ${Number.isFinite(updated) ? when(updated) : 'at an unknown time'}${Number.isFinite(reset) ? ` · resets ${when(reset)}` : ' · reset unknown'}${expired ? ' · previous week; refresh needed' : stale ? ' · may be out of date' : ''}` : 'No weekly report yet';
      return `<div class="subs-weekly"><span>${esc(subsWeeklyName(runtime))} weekly: ${esc(value)}${weekly?.status === 'rejected' ? ' · limit reached' : ''}</span>
        ${Number.isFinite(percent) ? `<progress max="100" value="${percent}" aria-label="${esc(subsWeeklyName(runtime))} last reported weekly usage"></progress>` : ''}
        <span class="muted subs-weekly-note">${esc(authInfo)} · ${esc(info)}</span>
        ${refreshInfo ? `<span class="muted subs-weekly-note" role="status">${esc(refreshInfo)}</span>` : ''}
        ${harnessCaps(runtime).weekly_usage === 'refresh' && subsMaySignIn(c) ? `<button type="button" class="ghost" data-subs-refresh data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}" data-runtime="${esc(runtime)}"${refresh?.state === 'requested' || state.signed_in === false ? ' disabled' : ''}>Refresh weekly usage</button>` : '<span class="muted subs-weekly-note">Updates from supported runs or a manual provider reading; no background model turn.</span>'}
        </div>`;
    }).join('');
}
// Switch account / Sign out for each signed-in runtime of the subscription (ui/model-login.js); the computer must be online.
function subsAccountHTML(c, p) {
  const machine = (SETTINGS_DATA?.machines || []).find(m => m.id === c.runner_id);
  const online = !machine || (!machine.revoked_at && !!machine.last_seen && Date.now() - new Date(machine.last_seen) < 60000);
  return subsRuntimes().filter(([id]) => p.runtimes?.[id]?.signed_in === true).map(([id, label]) => {
    const buttons = window.TicoModelLogin?.actionsHTML({runnerId: c.runner_id, runtime: id, machine: c.label, profile: p.name, signedIn: true, online}) || '';
    return buttons ? `<div class="subs-account" data-subs-account="${esc(id)}"><strong>${esc(label)} account</strong>${buttons}
      <span class="muted">Switching signs in on the side: the account signed in now keeps working until the new one is in.</span></div>` : '';
  }).join('');
}
function subsControlsHTML(c, p) {
  if (!subsMaySignIn(c)) return '';
  const forms = subsRenameHTML(c, p) + subsAccountHTML(c, p) + subsWeeklyRuntimes(p).map(([runtime, state]) => {
    const percent = state.weekly?.used_percent, reset = Date.parse(state.weekly?.resets_at);
    const localReset = Number.isFinite(reset) ? new Date(reset - new Date(reset).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
    return `<form data-subs-weekly data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}" data-runtime="${esc(runtime)}">
      <strong>${esc(subsWeeklyName(runtime))} manual weekly report</strong>
      <label>Used (%) <input type="number" name="percent" min="0" max="100" step="any" value="${Number.isFinite(percent) ? percent : ''}" placeholder="Unknown"></label>
      <label>Weekly reset (local time) <input type="datetime-local" name="reset" value="${localReset}"></label>
      <button class="ghost" type="submit">Save report</button><button class="ghost" type="button" data-subs-weekly-clear>Clear manual report</button>
      <span class="muted">Copy the weekly allowance from your provider. This does not change its limit.</span><span role="status" data-weekly-status></span>
    </form>`;
  }).join('');
  return forms ? `<details class="subs-controls" data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}">
    <summary>Manage subscription</summary>${forms}</details>` : '';
}
function subsScheduleRefresh() {
  clearTimeout(SUBS_REFRESH_TIMER);
  const pending = (SUBS?.computers || []).some(c => c.profiles.some(p => Object.values(p.runtimes || {}).some(state =>
    state.refresh?.state === 'requested' && Date.parse(state.refresh.expires_at) + 30000 > Date.now())));
  if (!pending) return;
  SUBS_REFRESH_TIMER = setTimeout(async () => {
    if (S.route !== SETTINGS || SETTINGS_TAB !== 'providers') return;
    await renderSettingsSubs();
    subsScheduleRefresh();
  }, 5000);
}
async function subsRefreshWeekly(button) {
  button.disabled = true;
  try {
    await post('/v2/subscriptions/refresh', {runner_id: button.dataset.runner, profile: button.dataset.profile, runtime: button.dataset.runtime});
    await renderSettingsSubs(true);
  } catch (e) { toast(e.message, true); }
  finally { button.disabled = false; }
}
const subsControlsKey = el => JSON.stringify([el.dataset.runner, el.dataset.profile]);
async function subsSaveWeekly(form, clear = false) {
  const status = form.querySelector('[data-weekly-status]');
  if (!clear && !form.reportValidity()) return;
  const reset = form.elements.reset.value;
  form.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    await put('/v2/subscriptions/weekly', {runner_id: form.dataset.runner, profile: form.dataset.profile,
      runtime: form.dataset.runtime, used_percent: clear || form.elements.percent.value === '' ? null : Number(form.elements.percent.value),
      resets_at: clear || !reset ? null : new Date(reset).toISOString()});
    toast(clear ? 'Manual report cleared' : 'Weekly report saved');
    await renderSettingsSubs(true);
  } catch (e) { status.textContent = e.message; }
  finally { form.querySelectorAll('button').forEach(b => b.disabled = false); }
}
function subsGroupRowHTML(g, names) {
  const own = subsAssigned('group', g.id), admin = settingsIsAdmin();
  const list = own && !names.includes(own) ? [...names, own] : names;
  // No choice of its own: it uses the nearest group above it that has one.
  const above = g.parent ? subsGroupProfile(g.parent) : {profile: ''};
  const none = above.profile ? `From ${above.group.name} (${above.profile})` : 'None';
  const gaps = subsGroupGaps(g);
  return `<li class="subs-row"><span class="subs-name">${esc(g.name)}</span>
    <select class="settings-inline-select subs-select" data-subs-group="${esc(g.id)}" aria-label="Subscription for ${esc(g.name)}"${admin ? '' : ' disabled'}>
      <option value="">${esc(none)}</option>${list.map(n => `<option value="${esc(n)}"${n === own ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
    ${gaps.map(label => `<span class="pill fail subs-gap">Not signed in on ${esc(label)}</span>`).join('')}</li>`;
}
// A new subscription's name being typed is never wiped by a redraw; `force` redraws anyway (after the person's own
// group change). `loaded`: SUBS was just read, so it is not read again.
const subsTyping = () => formBusy($('#set-subs'));
async function renderSettingsSubs(force = false, loaded = false) {
  const card = $('#settings-subs'), el = $('#set-subs');
  if (!card || !el) return;
  if (!force && subsTyping()) return;
  if (!S.me?.cloud || !(loaded ? SUBS : await subsLoad()) || !$('#set-subs')) { card.hidden = true; return; }
  if (!force && subsTyping()) return;
  const names = subsNames();
  const machines = (SETTINGS_DATA?.machines || []).filter(m => !m.revoked_at && subsMaySignIn(m));
  const owner = machines.length > 0;
  const computers = SUBS.computers.filter(c => c.profiles.length);
  const groups = names.length ? subsGroups() : [];
  const add = owner && machines.length ? `<form class="subs-add" data-subs-add novalidate>
      <input name="profile" type="text" autocomplete="off" spellcheck="false" maxlength="80" required placeholder="New subscription" aria-label="New subscription name" aria-describedby="subs-add-msg">
      <select name="runner" aria-label="Computer">${machines.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('')}</select>
      <select name="runtime" aria-label="Sign in to">${subsRuntimes().map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('')}</select>
      <button class="ghost" type="submit">Sign in</button><span class="muted subs-add-msg" id="subs-add-msg" role="status"></span></form>` : '';
  if (!computers.length && !add) { card.hidden = true; return; }
  const openControls = new Set([...el.querySelectorAll('.subs-controls[open]')].map(subsControlsKey));
  el.innerHTML = `<p class="muted">Named subscriptions stay on their computer. Weekly allowance is approximate and may include use outside Tico. Matching names on different computers are not combined.</p>${computers.map(c => `<div class="subs-pc"><h3 class="subs-h">${esc(c.label)}</h3><ul class="subs-list">${c.profiles.map(p =>
      `<li class="subs-row" data-subs-profile="${esc(p.name)}"><span class="subs-name">${esc(p.display_name || p.name)}</span>${subsRuntimeHTML(c, p)}${subsWeeklyHTML(c, p)}${subsControlsHTML(c, p)}</li>`).join('')}</ul></div>`).join('')}
    ${groups.length ? `<div class="subs-pc subs-groups"><h3 class="subs-h">Groups</h3><ul class="subs-list">${groups.map(g => subsGroupRowHTML(g, names)).join('')}</ul></div>` : ''}
    ${add}`;
  el.querySelectorAll('.subs-controls').forEach(control => { control.open = openControls.has(subsControlsKey(control)); });
  card.hidden = false;
  subsScheduleRefresh();
  el.onchange = async ev => {
    const sel = ev.target.closest('[data-subs-group]'); if (!sel) return;
    sel.disabled = true;
    try {
      await put('/v2/subscriptions', {scope: 'group', target: sel.dataset.subsGroup, profile: sel.value || null});
      toast('Subscription saved');
    } catch (e) { toast(e.message, true); }
    await renderSettingsSubs(true);
  };
  el.oninput = ev => {
    const field = ev.target.closest('[data-subs-add] [name=profile]'); if (!field) return;
    const slug = subsSlug(field.value), msg = $('#subs-add-msg');
    if (msg) msg.textContent = field.value.trim() && slug !== field.value.trim() ? `Saved as ${slug}` : '';
  };
  el.onclick = ev => {
    const refresh = ev.target.closest('[data-subs-refresh]');
    if (refresh) void subsRefreshWeekly(refresh);
    const clear = ev.target.closest('[data-subs-weekly-clear]');
    if (clear) void subsSaveWeekly(clear.closest('form'), true);
  };
  el.onsubmit = ev => {
    const rename = ev.target.closest('[data-subs-rename]');
    if (rename) { ev.preventDefault(); void subsRename(rename); return; }
    const weekly = ev.target.closest('[data-subs-weekly]');
    if (weekly) { ev.preventDefault(); void subsSaveWeekly(weekly); return; }
    const form = ev.target.closest('[data-subs-add]'); if (!form) return;
    ev.preventDefault();
    const name = subsSlug(form.elements.profile.value);
    if (!name) { $('#subs-add-msg').textContent = 'Use letters or numbers'; form.elements.profile.focus(); return; }
    $('#subs-add-msg').textContent = '';
    window.TicoModelLogin?.open({runnerId: form.elements.runner.value, runtime: form.elements.runtime.value, profile: name,
      machine: form.elements.runner.selectedOptions[0]?.textContent || ''});
    form.elements.profile.value = '';                     // handed to the sign-in; the list redraws once it reports
  };
}

// ---- the bot editor: Subscription (from its group, or its own) and the line under Model
const BOT_SUB_LINE = new Map();          // slug -> GET /v2/bots/{bot}/subscription, so a redraw paints at once
function subsSourceWords(source) {
  const s = String(source || '');
  if (s === 'bot') return 'set for this bot';
  if (s.startsWith('group:')) return `from group ${s.slice(6)}`;
  return s === 'computer' ? 'computer default' : '';
}
// {profile, source, computer: {runner_id, label} | null, signed_in: true | false | null, problem}
function subsBotLineHTML(line) {
  if (!line) return '';
  const computer = subsComputerLabel(line.computer);
  const parts = [line.display_name || subsDisplayName(line.computer?.runner_id, line.profile) || 'default', line.profile || line.source !== 'computer' ? subsSourceWords(line.source) : '', computer].filter(Boolean);
  // The server's own words when it has them (a profile this computer lacks, a runner too old to say).
  const warn = line.problem || (line.signed_in === false ? 'Not signed in' : '');
  return `<span class="sb-sub-text">Subscription: ${esc(parts.join(' · '))}</span>${warn ? ` <span class="pill fail sb-sub-warn">${esc(warn)}</span>` : ''}`;
}
// Owners, admins and the person whose computer the bot runs on choose a bot's subscription (backend/subscriptions.py).
const subsMayChooseForBot = e => settingsIsAdmin() || (!!e?.machine?.operator && e.machine.operator === S.me?.id);
function subsBotPaint(rows, slug) {
  const e = (S.emps || []).find(x => x.name === slug);
  const lineEl = rows.querySelector('[data-bot-sub-line]'), row = rows.querySelector('[data-bot-sub-row]');
  const line = BOT_SUB_LINE.get(slug);
  if (lineEl) { lineEl.innerHTML = subsBotLineHTML(line); lineEl.hidden = !line; }
  if (!row) return;
  if (!SUBS || !e) { row.hidden = true; return; }
  const own = subsAssigned('bot', slug), runner = e.machine?.runner_id || '';
  const from = e.team ? subsGroupProfile(e.team) : null;
  const inherit = from?.group ? `From group (${from.group.name})` : 'Computer default';
  // The bot's own computer's subscriptions first; an unavailable assignment waits for sign-in there.
  const here = runner ? (SUBS.computers.find(c => c.runner_id === runner)?.profiles || []).map(p => p.name).sort((a, b) => a.localeCompare(b)) : subsNames();
  const elsewhere = subsNames().filter(n => !here.includes(n));
  if (own && !here.includes(own) && !elsewhere.includes(own)) elsewhere.push(own);
  const opt = n => `<option value="${esc(n)}"${n === own ? ' selected' : ''}>${esc(subsDisplayName(runner, n))}</option>`;
  const sel = row.querySelector('select');
  sel.innerHTML = `<option value="">${esc(inherit)}</option>${runner && elsewhere.length
    ? `${here.length ? `<optgroup label="${esc(subsComputerLabel(runner))}">${here.map(opt).join('')}</optgroup>` : ''}<optgroup label="Other computers">${elsewhere.map(opt).join('')}</optgroup>`
    : here.map(opt).join('')}`;
  sel.disabled = !subsMayChooseForBot(e);
  row.hidden = false;
}
async function subsBotMount(rows, slug) {
  if (!rows || !S.me?.cloud) return;
  if (rows.dataset.subsWired !== slug) {
    rows.dataset.subsWired = slug;
    rows.addEventListener('change', async ev => {
      const sel = ev.target.closest('[data-bot-sub]'); if (!sel) return;
      sel.disabled = true;
      try { await put('/v2/subscriptions', {scope: 'bot', target: slug, profile: sel.value || null}); }
      catch (e) { toast(e.message, true); }
      BOT_SUB_LINE.delete(slug);
      await subsBotMount(rows, slug);
    });
  }
  subsBotPaint(rows, slug);
  const [, line] = await Promise.all([subsLoad(), get(`/v2/bots/${encodeURIComponent(slug)}/subscription`).catch(() => null)]);
  if (line) BOT_SUB_LINE.set(slug, line); else BOT_SUB_LINE.delete(slug);
  if (rows.isConnected) subsBotPaint(rows, slug);
}
