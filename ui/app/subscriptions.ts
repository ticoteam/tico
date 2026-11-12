/* ui/app/subscriptions.ts — the company's subscriptions (Settings > Models) and which one each computer runs each AI
   tool on (Settings > Computers). A subscription is a saved plan login or API key, sealed on the server; a computer
   holds at most one per AI tool, and every bot on that computer uses it. Bots never pick one.
   Routes: /v2/model-subscriptions and /v2/computer-subscriptions (backend/model_subscriptions.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET /v2/model-subscriptions: an older server answers without the lists (`listed` false), and a failed read is {error}.
type SubsListAnswer = Partial<import('../types/api').ModelSubscriptionList> & {connect?: string[]; error?: unknown};
// A kind: the server's rows, or SUBS_KINDS when it sends none.
interface SubsKindRow {kind: string; label: string; runtime: string; secret_label?: string; owned?: boolean; connect?: boolean; help?: string}
interface SubsUsage {used_pct?: number | null; resets_at?: string | null; window?: string | null; reason?: string | null; cost_usd_month?: number | null}
// A computer as the server names it: {runner_id, label}, or a bare runner id.
type SubsComputer = string | {runner_id: string; label?: string};
interface SubsRow {id: string; name: string; kind: string; label?: string; runtime?: string; owner_id?: string; owner_name?: string; computers?: SubsComputer[]; status?: string; status_detail?: string; usage?: SubsUsage | null}
interface SubsAssignment {runner_id: string; runtime: string; subscription_id: string}
interface SubsState {rows: SubsRow[]; kinds: SubsKindRow[]; connect: string[] | null; assignments: SubsAssignment[]; capable: string[] | null; listed: boolean; error: string; loaded: boolean; pending: Promise<void> | null}
// A refusal's extra facts; `detail` is the message (an object with the count on an older server).
interface SubsProblem {code?: unknown; running?: unknown; detail?: unknown}
// A computer on Settings > Computers, with what its Tico says about each AI tool's sign-in.
type SubsMachine = SettingsMachine & {readiness?: {runtimes?: Record<string, {authenticated?: unknown} | undefined> | null} | null};
// A person who may own a plan.
interface SubsPerson {id: string; name?: string; email?: string; hidden?: boolean; sign_in?: unknown}
// The Edit dialog's form: Name and, for a plan, Owner.
interface SubsFormFields extends HTMLFormControlsCollection {name?: HTMLInputElement; owner?: HTMLSelectElement}
interface SubsForm extends HTMLFormElement {readonly elements: SubsFormFields}
type SubsAsk = (running: number) => {title: string; text: string; ok?: string; danger?: boolean};

// A computer as the server names it: {runner_id, label}, or a bare runner id.
function subsComputerLabel(c: SubsComputer | null | undefined): string {
  if (c && typeof c === 'object') return c.label || subsComputerLabel(c.runner_id);
  return (SETTINGS_DATA?.machines || []).find(m => m.id === c)?.label || c || '';
}
// A saved form is no longer a draft: its fields take their saved values as their own, so the pop-up redraws it.
function subsSettle(form: Element) {
  form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea').forEach(field => { field.defaultValue = field.value; });
  if (form.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
}

// ---- what the server holds
// `listed`: the server has the routes (an older one answers neither). `capable`: the computers whose Tico can take a
// subscription; null when the server does not say.
const SUBS: SubsState = {rows: [], kinds: [], connect: null, assignments: [], capable: null, listed: false, error: '', loaded: false, pending: null};
// The kinds, in the server's order, when it sends none.
const SUBS_KINDS: [SubsKindRow, ...SubsKindRow[]] = [
  {kind: 'claude_token', label: 'Claude plan', runtime: 'claude', secret_label: 'Token from claude setup-token', owned: true},
  {kind: 'codex_login', label: 'ChatGPT plan', runtime: 'codex', secret_label: "Codex's auth.json", owned: true},
  {kind: 'anthropic_key', label: 'Anthropic API key', runtime: 'claude', secret_label: 'API key'},
  {kind: 'openai_key', label: 'OpenAI API key', runtime: 'codex', secret_label: 'API key'}];
const subsToolName = (runtime: string | undefined) => harnessRow(runtime)?.name || harnessWords(runtime);
const subsKind = (kind: string): SubsKindRow => SUBS.kinds.find(row => row.kind === kind) || SUBS_KINDS.find(row => row.kind === kind) || {kind, label: kind, runtime: ''};
// A plan has an owner; an API key has none.
const subsOwned = (kind: string) => { const row = subsKind(kind); return 'owned' in row ? !!row.owned : !!SUBS_KINDS.find(k => k.kind === kind)?.owned; };
const subsRuntime = (sub: SubsRow) => sub?.runtime || subsKind(sub?.kind).runtime;
// The AI tools a subscription can be given to, in the kinds' order (Claude Code, then Codex).
const subsTools = () => [...new Set((SUBS.kinds.length ? SUBS.kinds : SUBS_KINDS).map(row => row.runtime).filter(Boolean))];
const subsById = (id: string | null | undefined) => SUBS.rows.find(row => row.id === id);
// The subscription a computer runs a tool on, or null.
const subsAssigned = (runner: string, runtime: string) => subsById(SUBS.assignments.find(row => row.runner_id === runner && row.runtime === runtime)?.subscription_id);
// Subscriptions can be given to computers: the server says which computers can take one.
const subsComputersReady = () => SUBS.listed && Array.isArray(SUBS.capable);
const subsMayEdit = () => settingsIsAdmin();
// Gateway on: a computer with no subscription for a tool runs it on Tico Gateway (models preset, backend/models_preset.py).
const subsGatewayOn = () => ['subscriptions-gateway', 'gateway'].includes(SETTINGS_PROVIDERS_VIEW?.preset ?? 'subscriptions-gateway');

async function subsLoad() {
  if (SUBS.pending) return SUBS.pending;
  SUBS.pending = Promise.all([
    get<SubsListAnswer>('/v2/model-subscriptions', {fresh: true}).catch((error: unknown): SubsListAnswer => ({error})),
    get<import('../types/api').ComputerSubscriptionList>('/v2/computer-subscriptions', {fresh: true}).catch(() => null)
  ]).then(([list, view]) => {
    SUBS.listed = Array.isArray(list?.subscriptions);
    SUBS.error = list?.error instanceof Error ? list.error.message : '';
    SUBS.rows = SUBS.listed ? list.subscriptions! : [];
    SUBS.kinds = Array.isArray(list?.kinds) && list.kinds.length ? list.kinds : SUBS_KINDS;
    SUBS.connect = Array.isArray(list?.connect) ? list.connect : null;
    SUBS.assignments = Array.isArray(view?.assignments) ? view.assignments : [];
    SUBS.capable = Array.isArray(view?.capable) ? view.capable : null;
    SUBS.loaded = true;
  }).finally(() => { SUBS.pending = null; });
  return SUBS.pending;
}
// A refusal's code and its extra facts (backend Problem: {error: {code, detail, ...extra}}).
const subsProblem = (error: unknown): SubsProblem => errorBody(error);
const subsRunning = (problem: SubsProblem) => Number(problem.running ?? (problem.detail as {running?: unknown} | undefined)?.running ?? 0) || 0;
const subsPlural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const subsStopWords = (n: number) => `${subsPlural(n, 'running task')} ${n === 1 ? 'stops and restarts' : 'stop and restart'}`;

// ---- Settings > Models > Subscriptions
// Usage: the share used in the plan's window, a small bar, and the time until it resets ("4d 15h"); the share left and
// the reset time in the tooltip. An API key shows the month's spend.
function subsResetIn(iso: string | null | undefined) {
  const ms = Date.parse(iso || '') - Date.now();
  if (!(ms > 0)) return '';
  const m = Math.max(1, Math.round(ms / 60000)), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
  return d ? `${d}d${h ? ` ${h}h` : ''}` : h ? `${h}h${m % 60 ? ` ${m % 60}m` : ''}` : `${m}m`;
}
function subsUsageHTML(sub: SubsRow) {
  const usage: SubsUsage = sub.usage || {};
  if (typeof usage.used_pct === 'number') {
    const used = Math.max(0, Math.min(100, Math.round(usage.used_pct)));
    const resetIn = subsResetIn(usage.resets_at);
    const at = resetIn ? new Date(usage.resets_at!).toLocaleString(undefined, {weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';
    const words = [`${used}% used${usage.window ? ` in the ${usage.window === 'week' ? 'week' : usage.window + ' window'}` : ''}`,
      `${100 - used}% left`, at ? `resets ${at}` : ''].filter(Boolean).join(' · ');
    const level = used >= 95 ? ' bad' : used >= 80 ? ' warn' : '';
    return `<span class="subs-usage" title="${esc(words)}">${usage.window ? `<span class="subs-usage-w">${esc(usage.window)}</span>` : ''}<span class="subs-bar${level}" aria-hidden="true"><b style="width:${used}%"></b></span><span class="tnum">${used}%</span><span class="sr-only"> used</span>${resetIn ? `<span class="subs-reset tnum"><span class="sr-only">resets in </span>${esc(resetIn)}</span>` : ''}</span>`;
  }
  if (usage.reason) return `<span class="subs-usage muted">${esc(usage.reason)}</span>`;
  if (typeof usage.cost_usd_month === 'number') {
    const cost = usage.cost_usd_month >= 100 ? Math.round(usage.cost_usd_month).toLocaleString() : usage.cost_usd_month.toFixed(2).replace(/\.00$/, '');
    return `<span class="subs-usage tnum">$${esc(cost)} this month</span>`;
  }
  return '';
}
function subsStatusHTML(sub: SubsRow) {
  if (sub.status === 'needs_reconnect') {
    const title = sub.status_detail ? ` title="${esc(sub.status_detail)}"` : '';
    return subsMayEdit() ? `<span class="subs-diagnosis">Sign-in expired</span> <button type="button" class="pill waiting subs-reconnect" data-subs-reconnect="${esc(sub.id)}"${title}>Sign in again</button>`
      : `<span class="pill waiting"${title}>Sign-in expired. Ask an admin to sign in again.</span>`;
  }
  const computers = sub.computers || [];
  if (!computers.length && subsOwned(sub.kind)) return `<span class="subs-diagnosis">No computer assigned</span>${subsMayEdit() ? ' <span class="muted">Assign this plan to a computer in Settings → Computers.</span>' : ''}`;
  if (sub.status && sub.status !== 'working') {
    const names = computers.map(subsComputerLabel).filter(Boolean).join(', ');
    return `<span class="subs-diagnosis">Connection unavailable${names ? ` on ${esc(names)}` : ''}</span>`;
  }
  return '';
}
function subsRowHTML(sub: SubsRow) {
  const count = (sub.computers || []).length;
  const names = (sub.computers || []).map(subsComputerLabel).join(', ');
  return `<tr data-subs-row="${esc(sub.id)}">
    <td class="subs-name">${esc(sub.name)}</td>
    <td class="muted subs-kind">${esc(sub.label || subsKind(sub.kind).label)}</td>
    <td class="subs-owner">${esc(sub.owner_name || '')}</td>
    <td class="subs-count" title="${esc(names || 'No computers')}"><span class="nav-icon" aria-hidden="true">computer</span><span class="tnum">${count}</span><span class="sr-only"> ${count === 1 ? 'computer' : 'computers'}</span></td>
    <td class="subs-use">${subsStatusHTML(sub) || subsUsageHTML(sub)}</td>
    <td class="subs-more-cell">${subsMayEdit() ? `<button type="button" class="ghost subs-more" data-subs-more="${esc(sub.id)}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${esc(sub.name)}">⋯</button>` : ''}</td></tr>`;
}
async function renderSettingsSubs() {
  const el = $('#set-subs'); if (!el) return;
  await subsLoad();
  if (!el.isConnected) return;
  const add = $('#subs-add');
  if (add) { add.hidden = !SUBS.listed || !subsMayEdit(); add.onclick = () => { planSignIn(); }; }
  let html;
  if (!SUBS.listed) html = `<p class="${SUBS.error ? 'err' : 'muted'}">${esc(SUBS.error || 'This server does not keep subscriptions yet.')}</p>`;
  else if (!SUBS.rows.length) html = `<div class="subs-empty"><span class="muted">No subscriptions yet.</span>${subsMayEdit() ? '<button type="button" class="ghost" data-subs-add>Add subscription</button>' : ''}</div>`;
  else html = `<div class="scroll"><table class="settings-table subs-table"><thead><tr><th>Name</th><th>Kind</th><th>Owner</th><th><span class="sr-only">Computers</span></th><th>Usage</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${SUBS.rows.map(subsRowHTML).join('')}</tbody></table></div>`;
  const restore = computerFocusRestore(el);
  patchHTML(el, html);
  restore();
  el.onclick = (event: MouseEvent) => {
    if (eventElement(event)?.closest('[data-subs-add]')) { planSignIn(); return; }
    const reconnect = eventElement(event)?.closest<HTMLElement>('[data-subs-reconnect]');
    if (reconnect) { const sub = subsById(reconnect.dataset.subsReconnect); if (sub) planSignIn({sub}); return; }
    const more = eventElement(event)?.closest<HTMLElement>('[data-subs-more]');
    if (more) subsMenu(more, subsById(more.dataset.subsMore));
  };
}
// Edit, Reconnect, Remove: the shared menu pop-over (styles/task-list.css .tl-pop; a sheet on a phone).
function subsMenu(anchor: HTMLElement, sub: SubsRow | undefined) {
  if (!sub) return;
  let pop = $('#subs-menu');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'subs-menu'; pop.className = 'tl-pop subs-menu'; pop.popover = 'auto';
    pop.addEventListener('toggle', (event: ToggleEvent) => {
      if (event.newState !== 'closed') return;
      pop.anchor?.setAttribute('aria-expanded', 'false');
      if (pop.anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) pop.anchor.focus();
    });
    document.body.append(pop);
  }
  pop.anchor = anchor;
  pop.innerHTML = `<div class="tl-menu" role="menu" aria-label="${esc(sub.name)}">
    <button type="button" class="tl-mi" role="menuitem" data-subs-act="edit">Edit</button>
    <button type="button" class="tl-mi" role="menuitem" data-subs-act="reconnect">Reconnect</button>
    <button type="button" class="tl-mi danger" role="menuitem" data-subs-act="remove">Remove</button></div>`;
  pop.onclick = (event: MouseEvent) => {
    const act = eventElement(event)?.closest<HTMLElement>('[data-subs-act]')?.dataset.subsAct;
    if (!act) return;
    pop.hidePopover();
    if (act === 'remove') void subsRemove(sub); else if (act === 'reconnect') planSignIn({sub}); else subsDialog(sub);
  };
  pop.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(pop, anchor);
  pop.querySelector('.tl-mi')?.focus();
}
// A plan's owner: a person of the company whose sign-in is on (the server checks). Ids here are bare; the server's are
// `human:<id>`. A saved one's owner who has since left stays listed so the field shows who it is.
const subsPersonId = (actor: unknown) => String(actor || '').replace(/^human:/, '');
function subsOwnerHTML(current: string, shown: boolean) {
  const people = (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.people || [])).filter((p: SubsPerson) => !p.hidden && p.sign_in !== false);
  if (!people.length) return '';
  const rows = current && !people.some(p => p.id === current) ? [{id: current, name: current}, ...people] : people;
  return `<label class="subs-field" data-subs-owned${shown ? '' : ' hidden'}><span class="k">Owner</span><select name="owner" data-current="${esc(current)}">${current ? '' : '<option value="" selected></option>'}${rows.map((p: SubsPerson) =>
    `<option value="${esc(p.id)}"${p.id === current ? ' selected' : ''}>${esc(p.name || p.email || p.id)}</option>`).join('')}</select></label>`;
}
// The owner to send: for a plan, only a change from the default (the person adding it, or the saved owner).
const subsOwnerPicked = (form: SubsForm) => {
  const o = form.elements.owner;
  return o && !o.closest<HTMLElement>('[data-subs-owned]')!.hidden && o.value && o.value !== o.dataset.current ? {owner_id: 'human:' + o.value} : {};
};
// Edit: the name and, for a plan, its owner. Adding one and reconnecting it are the sign-in sheet (app/plan-signin.ts);
// giving a subscription to computers is on the Computers tab.
function subsDialog(sub: SubsRow | null | undefined) {
  if (!sub) return;
  const title = `Edit ${sub.name}`;
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal tmodal-shell subs-dialog'; dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<form data-subs-form="edit" novalidate><header><h2>${esc(title)}</h2><button class="ghost" type="button" data-close>Close</button></header>
    <div class="tmodal-body subs-form">
      <label class="subs-field"><span class="k">Name</span><input name="name" type="text" required maxlength="80" autocomplete="off" value="${esc(sub.name || '')}"></label>
      ${subsOwned(sub.kind) ? subsOwnerHTML(subsPersonId(sub.owner_id), true) : ''}
    </div>
    <div class="tmodal-foot onb-actions"><button class="ghost" type="button" data-close>Cancel</button><span class="spacer"></span><button class="primary" type="submit">Save</button></div></form>`;
  const form = dialog.querySelector<SubsForm>('form');
  if (!form) return;
  dialog.querySelectorAll<HTMLElement>('[data-close]').forEach(button => { button.onclick = () => dialog.close(); });
  // A red line goes once the person types in that field again.
  form.oninput = event => { const field = event.target as HTMLInputElement | HTMLSelectElement; if (field.name) settingsFieldClear(field); };
  form.onsubmit = async event => {
    event.preventDefault();
    form.querySelectorAll('[data-field-err]').forEach(line => line.remove());
    const name = form.elements.name?.value.trim();
    if (form.elements.name && !name) { settingsFieldError(form.elements.name, 'Enter a name'); form.elements.name.focus(); return; }
    const button = form.querySelector<HTMLButtonElement>('button[type=submit]'); if (!button) return; button.disabled = true;
    try {
      await post(`/v2/model-subscriptions/${encodeURIComponent(sub.id)}`, {name, ...subsOwnerPicked(form)});
      toast('Saved');
      dialog.close();
    } catch (error) {
      const code = subsProblem(error).code;
      const field = code === 'name' ? form.elements.name : code === 'owner' ? form.elements.owner : null;
      if (field) { settingsFieldError(field, errorMessage(error, '')); field.focus(); } else toast(errorMessage(error, ''), true);
      button.disabled = false;
    }
  };
  dialog.onclose = () => { dialog.remove(); void subsRefresh(); };
  document.body.appendChild(dialog); dialog.showModal();
  form.elements.name?.focus();
}
// A write that may stop runs: send it; when the server answers 409 confirm_restart, ask in the page with the count
// (`ask(running)` gives the settingsConfirm options) and, on yes, send it again with confirm (a new body, so a fresh
// Idempotency-Key). Resolves to the answer, or null when the person says no.
async function subsConfirmed<T>(write: (confirm: boolean) => Promise<T>, ask: SubsAsk): Promise<T | null> {
  try { return await write(false); }
  catch (error) {
    const problem = subsProblem(error);
    if (problem.code !== 'confirm_restart') throw error;
    if (!await settingsConfirm(ask(subsRunning(problem)))) return null;
    return write(true);
  }
}
// Remove: asks first; with runs going on its computers, says how many restart and asks once more.
async function subsRemove(sub: SubsRow) {
  const count = (sub.computers || []).length;
  const text = count ? `${subsPlural(count, 'computer')} stop using it.` : 'Tico deletes the saved value.';
  if (!await settingsConfirm({title: `Remove ${sub.name}?`, text, ok: 'Remove'})) return;
  try {
    const done = await subsConfirmed((confirm: boolean): Promise<import('../types/api').ModelSubscriptionDeleted> => writeRequest('DELETE', `/v2/model-subscriptions/${encodeURIComponent(sub.id)}${confirm ? '?confirm=1' : ''}`),
      running => ({title: `Remove ${sub.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Remove'}));
    if (!done) return;
    toast(`${sub.name} removed`);
  } catch (error) { toast(errorMessage(error, ''), true); }
  await subsRefresh();
}
// Reload, then redraw whatever shows subscriptions.
async function subsRefresh() {
  await subsLoad();
  if ($('#set-subs')) void renderSettingsSubs();
  if ($('#set-machines') && SETTINGS_DATA.loaded) renderSettingsMachines();
}

// ---- Settings > Computers: one picker per computer and AI tool
const subsSignedInHere = (machine: SubsMachine, runtime: string) => machine.readiness?.runtimes?.[runtime]?.authenticated === 'ready';
// What runs the tool when the computer has no subscription for it.
const subsNoneWords = (machine: SubsMachine, runtime: string) => subsSignedInHere(machine, runtime) ? 'Signed in here' : subsGatewayOn() ? 'Gateway' : 'None';
function subsPickerHTML(machine: SubsMachine, runtime: string) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  // An older runner still runs on its own (or a shared) login: its chip, and Needs update beside it.
  if (!SUBS.capable!.includes(machine.id)) {
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
async function subsPick(select: HTMLSelectElement) {
  const runner = select.dataset.runner!, runtime = select.dataset.subsPick!, was = select.dataset.current || '';
  const machine = (SETTINGS_DATA.machines || []).find(m => m.id === runner);
  const computer = machine?.label || runner, tool = subsToolName(runtime);
  const id = select.value || null, name = id ? subsById(id)?.name || id : select.options[0]?.textContent || 'None';
  const body = {runner_id: runner, runtime, subscription: id};
  select.disabled = true;
  try {
    const done = await subsConfirmed((confirm: boolean): Promise<import('../types/api').ComputerSubscriptionResult> => put('/v2/computer-subscriptions', {...body, confirm}),
      running => ({title: `Switch ${computer}'s ${tool} to ${name}?`,
        text: `${subsStopWords(running)}, and every ${tool} session on this computer starts fresh.`, ok: 'Switch', danger: false}));
    if (!done) { select.value = was; return; }
    SUBS.assignments = SUBS.assignments.filter(row => !(row.runner_id === runner && row.runtime === runtime));
    if (id) SUBS.assignments.push({runner_id: runner, runtime, subscription_id: id});
    select.dataset.current = id || '';
    const stopped = Number(done?.stopped) || 0;
    toast(`${computer}'s ${tool}: ${name}${stopped ? ` · ${subsPlural(stopped, 'task')} restarted` : ''}`);
  } catch (error) { toast(errorMessage(error, ''), true); select.value = was; }
  finally { select.disabled = !subsMayEdit(); }
  await subsRefresh();
}
