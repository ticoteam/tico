/* ui/app/subscriptions.js — Subscriptions: the named AI logins on each computer, the controls each one keeps in the
   computer's pop-up on Settings > Computers (rename, refresh and record weekly usage, add one), a bot's own choice, and
   the line under a bot's model saying which connection it runs on.
   The server stores stable connection IDs and display names; the logins stay on the computers (runner-rs/src/profiles.rs).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET /v2/subscriptions: {profiles_by_computer: [{runner_id, label, profiles: [{name, runtimes: {codex: {signed_in}}}]}],
// assignments: [{scope: 'bot', target, profile}]}. An older server answers 404 and every part here stays away.
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
  return {computers, assignments: (d?.assignments || []).filter(row => (row.scope || 'bot') === 'bot')};
}
const subsNames = () => [...new Set((SUBS?.computers || []).flatMap(c => c.profiles.map(p => p.name)))].sort((a, b) => a.localeCompare(b));
// Labels change independently from immutable local profile keys and existing assignments.
function subsDisplayName(runner, profile) {
  return SUBS?.computers.find(c => c.runner_id === runner)?.profiles.find(p => p.name === profile)?.display_name || profile;
}
// A computer as the server names it: {runner_id, label}, or a bare runner id.
function subsComputerLabel(c) {
  if (c && typeof c === 'object') return c.label || subsComputerLabel(c.runner_id);
  return SUBS?.computers.find(x => x.runner_id === c)?.label || (SETTINGS_DATA?.machines || []).find(m => m.id === c)?.label || c || '';
}
// Subscription names are lowercase words joined by hyphens (runner-rs/src/profiles.rs): "Acme Research" becomes acme-research.
const subsSlug = name => String(name || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
// A bot's own choice; with none it runs on its computer's own login.
const subsAssigned = bot => (SUBS?.assignments || []).find(x => x.target === bot)?.profile || '';

// ---- a subscription's controls, in the computer's pop-up (ui/app/computer-detail.js)
// The server's rule (backend/subscriptions.py, model_login): an administrator or the computer's operator.
const subsMaySignIn = c => settingsIsAdmin() || (SETTINGS_DATA?.machines || []).some(m =>
  m.id === (c.runner_id || c.id) && m.operator === S.me?.id);
function subsRenameHTML(c, p) {
  if (!p.id || !subsMaySignIn(c)) return '';
  return `<form data-subs-rename data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}">
    <label>Name <input name="display_name" required maxlength="80" value="${esc(p.display_name || p.name)}"></label>
    <button class="ghost" type="submit">Save name</button><span class="muted">Login and bots stay the same.</span><span role="status"></span>
  </form>`;
}
// A saved form is no longer a draft: its fields take their saved values as their own, so the pop-up redraws it.
function subsSettle(form) {
  form.querySelectorAll('input, textarea').forEach(field => { field.defaultValue = field.value; });
  if (form.contains(document.activeElement)) document.activeElement.blur();
}
async function subsRename(form) {
  if (!form.reportValidity()) return;
  const button = form.querySelector('button'); button.disabled = true;
  try {
    await put('/v2/subscriptions/name', {runner_id: form.dataset.runner, profile: form.dataset.profile,
      display_name: form.elements.display_name.value});
    BOT_SUB_LINE.clear();
    subsSettle(form);
    await loadSettings();
    toast('Subscription renamed');
  } catch (e) { form.querySelector('[role=status]').textContent = e.message; }
  finally { button.disabled = false; }
}
// Refresh weekly usage, for a CLI whose manifest can be asked (`weekly_usage = "refresh"`); the computer must be online.
function subsRefreshHTML(c, p, runtime, state, online) {
  if (harnessCaps(runtime).weekly_usage !== 'refresh' || !subsMaySignIn(c)) return '';
  const busy = state.refresh?.state === 'requested';
  return `<button type="button" class="ghost" data-subs-refresh data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}" data-runtime="${esc(runtime)}"${busy || !online || state.signed_in === false ? ' disabled' : ''}>${busy ? 'Refresh queued' : 'Refresh usage'}</button>`;
}
const SUBS_REFRESH_WORDS = {succeeded: 'Last refresh succeeded', failed: 'Refresh failed; last reading kept',
  unavailable: 'Weekly refresh unavailable; use a provider reading', expired: 'Refresh timed out; check the computer connection',
  outdated: 'Sign-in changed; refresh usage again'};
const subsRefreshNote = refresh => refresh && SUBS_REFRESH_WORDS[refresh.state]
  ? `${SUBS_REFRESH_WORDS[refresh.state]}${refresh.updated_at ? ` · ${ago(refresh.updated_at)}` : ''}` : '';
// The manual weekly reading, as % left: the provider's own page says what is left, and so does every chip here.
function subsWeeklyFormHTML(c, p, runtime, state) {
  if (!subsMaySignIn(c) || !subsWeeklyName(runtime)) return '';
  const used = state.weekly?.used_percent, reset = Date.parse(state.weekly?.resets_at);
  const left = Number.isFinite(used) ? Math.round((100 - used) * 100) / 100 : '';
  const localReset = Number.isFinite(reset) ? new Date(reset - new Date(reset).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
  return `<form data-subs-weekly data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}" data-runtime="${esc(runtime)}">
    <strong>Manual weekly reading</strong>
    <label>Left (%) <input type="number" name="percent" min="0" max="100" step="any" value="${left}" placeholder="Unknown"></label>
    <label>Weekly reset (local time) <input type="datetime-local" name="reset" value="${localReset}"></label>
    <button class="ghost" type="submit">Save reading</button><button class="ghost" type="button" data-subs-weekly-clear>Clear reading</button>
    <span class="muted">Copy what the provider shows. This does not change its limit.</span><span role="status" data-weekly-status></span>
  </form>`;
}
function subsControlsHTML(c, p, runtime, state) {
  const forms = subsRenameHTML(c, p) + subsWeeklyFormHTML(c, p, runtime, state);
  return forms ? `<details class="subs-controls" data-runner="${esc(c.runner_id)}" data-profile="${esc(p.name)}" data-runtime="${esc(runtime)}">
    <summary>Manage</summary>${forms}</details>` : '';
}
// While a refresh is queued, read Settings again until the computer answers (or the request expires).
function subsScheduleRefresh() {
  clearTimeout(SUBS_REFRESH_TIMER);
  const pending = (SETTINGS_DATA?.machines || []).some(m => (m.profiles || []).some(p => Object.values(p.runtimes || {}).some(state =>
    state.refresh?.state === 'requested' && Date.parse(state.refresh.expires_at) + 30000 > Date.now())));
  if (!pending) return;
  SUBS_REFRESH_TIMER = setTimeout(async () => {
    if (S.route !== SETTINGS || SETTINGS_TAB !== 'devices') return;
    await loadSettings();
  }, 5000);
}
async function subsRefreshWeekly(button) {
  button.disabled = true;
  try {
    await post('/v2/subscriptions/refresh', {runner_id: button.dataset.runner, profile: button.dataset.profile, runtime: button.dataset.runtime});
    await loadSettings();
  } catch (e) { toast(e.message, true); button.disabled = false; }
}
async function subsSaveWeekly(form, clear = false) {
  const status = form.querySelector('[data-weekly-status]');
  if (!clear && !form.reportValidity()) return;
  const reset = form.elements.reset.value, left = form.elements.percent.value;
  form.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    await put('/v2/subscriptions/weekly', {runner_id: form.dataset.runner, profile: form.dataset.profile,
      runtime: form.dataset.runtime, used_percent: clear || left === '' ? null : 100 - Number(left),
      resets_at: clear || !reset ? null : new Date(reset).toISOString()});
    toast(clear ? 'Reading cleared' : 'Reading saved');
    if (clear) form.reset();
    subsSettle(form);
    await loadSettings();
  } catch (e) { status.textContent = e.message; }
  finally { form.querySelectorAll('button').forEach(b => b.disabled = false); }
}
// Add a subscription on this computer: a name, the tool, then the provider's sign-in in its own pop-up.
function subsAddHTML(machine, online) {
  if (!subsMaySignIn(machine) || machine.revoked_at || !subsRuntimes().length) return '';
  return `<form class="subs-add" data-subs-add data-runner="${esc(machine.id)}" data-machine="${esc(machine.label)}" novalidate>
    <input name="profile" type="text" autocomplete="off" spellcheck="false" maxlength="80" required placeholder="New subscription" aria-label="New subscription name"${online ? '' : ' disabled'}>
    <select name="runtime" aria-label="Sign in to"${online ? '' : ' disabled'}>${subsRuntimes().map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('')}</select>
    <button class="ghost" type="submit"${online ? '' : ' disabled'}>Sign in</button><span class="muted subs-add-msg" role="status" data-subs-add-msg></span></form>`;
}
// The pop-up's events for these controls; true when one was handled.
function subsHandleClick(ev) {
  const refresh = ev.target.closest('[data-subs-refresh]');
  if (refresh) { void subsRefreshWeekly(refresh); return true; }
  const clear = ev.target.closest('[data-subs-weekly-clear]');
  if (clear) { void subsSaveWeekly(clear.closest('form'), true); return true; }
  return false;
}
function subsHandleInput(ev) {
  const field = ev.target.closest('[data-subs-add] [name=profile]'); if (!field) return;
  const slug = subsSlug(field.value), msg = field.form.querySelector('[data-subs-add-msg]');
  msg.textContent = field.value.trim() && slug !== field.value.trim() ? `Saved as ${slug}` : '';
}
function subsHandleSubmit(ev) {
  const rename = ev.target.closest('[data-subs-rename]');
  if (rename) { ev.preventDefault(); void subsRename(rename); return true; }
  const weekly = ev.target.closest('[data-subs-weekly]');
  if (weekly) { ev.preventDefault(); void subsSaveWeekly(weekly); return true; }
  const form = ev.target.closest('[data-subs-add]'); if (!form) return false;
  ev.preventDefault();
  const name = subsSlug(form.elements.profile.value), msg = form.querySelector('[data-subs-add-msg]');
  if (!name) { msg.textContent = 'Use letters or numbers'; form.elements.profile.focus(); return true; }
  msg.textContent = '';
  window.TicoModelLogin?.open({runnerId: form.dataset.runner, runtime: form.elements.runtime.value, profile: name, machine: form.dataset.machine});
  form.elements.profile.value = '';                     // handed to the sign-in; the pop-up redraws once it reports
  return true;
}

// ---- the bot editor: its connection (the computer's login, or a subscription of its own) and the line under Model
const BOT_SUB_LINE = new Map();          // slug -> GET /v2/bots/{bot}/subscription, so a redraw paints at once
// A connection by the name the Computers page gives it.
const subsConnectionName = line => line?.profile ? (line.display_name || subsDisplayName(line.computer?.runner_id, line.profile) || line.profile) : "Computer's login";
const subsToolName = line => harnessRow(line?.harness)?.name || harnessRow(line?.runtime)?.name || harnessWords(line?.runtime) || 'its model';
// {profile, display_name, source, computer: {runner_id, label} | null, runtime, harness, signed_in, problem}; `here`
// leaves the computer out, for the computer's own pop-up.
function subsBotLineHTML(line, here = false) {
  if (!line) return '';
  const where = here ? '' : line.computer ? ` on ${subsComputerLabel(line.computer)}` : ' · no computer yet';
  // The server's own words when it has them (a profile this computer lacks, a runner too old to say).
  const warn = line.problem || (line.signed_in === false ? 'Signed out' : '');
  return `<span class="sb-sub-text">Runs on ${esc(subsToolName(line))} · ${esc(subsConnectionName(line))}${esc(where)}</span>${warn ? ` <span class="pill fail sb-sub-warn">${esc(warn)}</span>` : ''}`;
}
// Owners, admins and the person whose computer the bot runs on choose a bot's connection (backend/subscriptions.py).
const subsMayChooseForBot = e => settingsIsAdmin() || (!!e?.machine?.operator && e.machine.operator === S.me?.id);
function subsBotPaint(rows, slug) {
  const e = (S.emps || []).find(x => x.name === slug);
  const lineEl = rows.querySelector('[data-bot-sub-line]'), row = rows.querySelector('[data-bot-sub-row]');
  const line = BOT_SUB_LINE.get(slug);
  if (lineEl) { lineEl.innerHTML = subsBotLineHTML(line); lineEl.hidden = !line; }
  if (!row) return;
  if (!SUBS || !e) { row.hidden = true; return; }
  const own = subsAssigned(slug), runner = e.machine?.runner_id || '';
  // The bot's own computer's subscriptions first; an unavailable choice waits for sign-in there.
  const here = runner ? (SUBS.computers.find(c => c.runner_id === runner)?.profiles || []).map(p => p.name).sort((a, b) => a.localeCompare(b)) : subsNames();
  const elsewhere = subsNames().filter(n => !here.includes(n));
  if (own && !here.includes(own) && !elsewhere.includes(own)) elsewhere.push(own);
  const opt = n => `<option value="${esc(n)}"${n === own ? ' selected' : ''}>${esc(subsDisplayName(runner, n))}</option>`;
  const sel = row.querySelector('select');
  sel.innerHTML = `<option value="">Computer's login</option>${runner && elsewhere.length
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
