/* ui/app/subscriptions.ts — the company's subscriptions (Settings > Models), grouped by owner, and which one each
   computer runs each AI tool on (Settings > Computers). A subscription is a saved plan login or API key, sealed on the
   server; a computer holds one per AI tool, or one person's plans for it (used in turn: the soonest weekly reset with
   usage left, the next when it is spent), and every bot on that computer uses it. Bots never pick one.
   Routes: /v2/model-subscriptions and /v2/computer-subscriptions (backend/model_subscriptions.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET /v2/model-subscriptions: an older server answers without the lists (`listed` false), and a failed read is {error}.
type SubsListAnswer = Partial<import('../types/api').ModelSubscriptionList> & {connect?: string[]; error?: unknown};
// A kind: the server's rows, or SUBS_KINDS when it sends none.
interface SubsKindRow {kind: string; label: string; runtime: string; secret_label?: string; owned?: boolean; connect?: boolean; help?: string}
interface SubsUsage {used_pct?: number | null; resets_at?: string | null; window?: string | null; reason?: string | null; cost_usd_month?: number | null; weekly_resets_at?: string | null}
// A computer as the server names it: {runner_id, label}, or a bare runner id.
type SubsComputer = string | {runner_id: string; label?: string};
// `order`: where a plan comes in its owner's plans for its tool (the plan in use first, then the pick order; null when
// held or waiting to be reconnected). `in_use_on`: the computers whose turns sign in with it now.
interface SubsRow {id: string; name: string; kind: string; label?: string; runtime?: string; owner_id?: string; owner_name?: string; computers?: SubsComputer[]; status?: string; status_detail?: string; usage?: SubsUsage | null; order?: number | null; in_use_on?: string[]}
// A computer given a person's plans: the plan in use (`on`) and why (backend model_subscriptions.set_state).
interface SubsSetWhy {kind: 'soonest' | 'held' | 'reconnect' | 'all_held' | 'none' | string; plan?: string | null; until?: string | null}
interface SubsSet {person: string; person_name: string; plans: number; on: string | null; on_name: string | null; why: SubsSetWhy}
interface SubsAssignment {runner_id: string; runtime: string; subscription_id: string; person?: string; set?: SubsSet | null}
// A person who owns plans: how many per AI tool.
interface SubsOwner {id: string; name: string; [runtime: string]: string | number | undefined}
interface SubsState {rows: SubsRow[]; kinds: SubsKindRow[]; connect: string[] | null; assignments: SubsAssignment[]; capable: string[] | null; people: SubsOwner[]; listed: boolean; error: string; loaded: boolean; pending: Promise<void> | null}
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
const SUBS: SubsState = {rows: [], kinds: [], connect: null, assignments: [], capable: null, people: [], listed: false, error: '', loaded: false, pending: null};
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
// What a computer is given for a tool: its row (one subscription, or a person's plans), or undefined.
const subsAssignment = (runner: string, runtime: string) => SUBS.assignments.find(row => row.runner_id === runner && row.runtime === runtime);
// The subscription a computer runs a tool on now, or null (for a person's plans, the one in use).
const subsAssigned = (runner: string, runtime: string) => subsById(subsAssignment(runner, runtime)?.subscription_id);
// Subscriptions can be given to computers: the server says which computers can take one.
const subsComputersReady = () => SUBS.listed && Array.isArray(SUBS.capable);
const subsMayEdit = () => settingsIsAdmin();
// Gateway on: a computer with no subscription for a tool runs it on Tico Gateway (models preset, backend/models_preset.py).
const subsGatewayOn = () => ['subscriptions-gateway', 'gateway'].includes(SETTINGS_PROVIDERS_VIEW?.preset ?? 'subscriptions-gateway');

async function subsLoad() {
  if (SUBS.pending) return SUBS.pending;
  SUBS.pending = Promise.all([
    get<SubsListAnswer>('/v2/model-subscriptions', {fresh: true}).catch((error: unknown): SubsListAnswer => ({error})),
    get<import('../types/api').ComputerSubscriptionList & {people?: SubsOwner[]}>('/v2/computer-subscriptions', {fresh: true}).catch(() => null)
  ]).then(([list, view]) => {
    SUBS.listed = Array.isArray(list?.subscriptions);
    SUBS.error = list?.error instanceof Error ? list.error.message : '';
    SUBS.rows = SUBS.listed ? list.subscriptions! : [];
    SUBS.kinds = Array.isArray(list?.kinds) && list.kinds.length ? list.kinds : SUBS_KINDS;
    SUBS.connect = Array.isArray(list?.connect) ? list.connect : null;
    SUBS.assignments = Array.isArray(view?.assignments) ? view.assignments as SubsAssignment[] : [];
    SUBS.people = Array.isArray(view?.people) ? view.people : [];
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
// Usage: what is left of the plan's tighter window (the server's reading is its most-used one, 5-hour or week), a bar
// of that share, and the hours until it resets ("62% left · resets in 98h"); the window and the reset time in the
// tooltip. A plan with no reading says so, with the server's reason in the tooltip. An API key shows the month's spend.
// Computers never show here: only on the count (subsCountHTML).
function subsHoursUntil(iso: string | null | undefined) {
  const ms = Date.parse(iso || '') - Date.now();
  if (!(ms > 0)) return '';
  return ms >= 3600e3 ? `${Math.round(ms / 3600e3)}h` : `${Math.max(1, Math.round(ms / 60000))}m`;
}
function subsUsageHTML(sub: SubsRow) {
  const usage: SubsUsage = sub.usage || {};
  if (typeof usage.used_pct === 'number') {
    const left = 100 - Math.max(0, Math.min(100, Math.round(usage.used_pct)));
    const resetIn = subsHoursUntil(usage.resets_at);
    const at = resetIn ? new Date(usage.resets_at!).toLocaleString(undefined, {weekday: 'short', hour: 'numeric', minute: '2-digit'}) : '';
    const window = usage.window === 'week' ? 'Week' : usage.window === '5h' ? '5-hour window' : '';
    const words = [`${window ? `${window}: ` : ''}${left}% left`, at ? `resets ${at} (in ${resetIn})` : ''].filter(Boolean).join(' · ');
    const level = left <= 5 ? ' bad' : left <= 20 ? ' warn' : '';
    return `<span class="subs-usage" title="${esc(words)}">${usage.window ? `<span class="subs-usage-w">${esc(usage.window)}</span>` : ''}<span class="subs-bar${level}" aria-hidden="true"><b style="width:${left}%"></b></span><span class="subs-left tnum">${left}% left</span>${resetIn ? `<span class="subs-reset tnum">resets in ${esc(resetIn)}</span>` : ''}</span>`;
  }
  if (typeof usage.cost_usd_month === 'number') {
    const cost = usage.cost_usd_month >= 100 ? Math.round(usage.cost_usd_month).toLocaleString() : usage.cost_usd_month.toFixed(2).replace(/\.00$/, '');
    return `<span class="subs-usage tnum">$${esc(cost)} this month</span>`;
  }
  if (usage.reason || subsOwned(sub.kind)) return `<span class="subs-usage muted subs-none"${usage.reason ? ` title="${esc(usage.reason)}"` : ''}>No reading yet</span>`;
  return '';
}
// The plan's own state, which stops any reading: its sign-in expired.
function subsStatusHTML(sub: SubsRow) {
  if (sub.status !== 'needs_reconnect') return '';
  const title = sub.status_detail ? ` title="${esc(sub.status_detail)}"` : '';
  return subsMayEdit() ? `<span class="subs-diagnosis">Sign-in expired</span> <button type="button" class="pill waiting subs-reconnect" data-subs-reconnect="${esc(sub.id)}"${title}>Sign in again</button>`
    : `<span class="pill waiting"${title}>Sign-in expired. Ask an admin to sign in again.</span>`;
}
// A short day and time ("Fri 09:00") for a hold's end.
const subsWhen = (iso: string | null | undefined) => {
  const at = Date.parse(iso || '');
  return Number.isFinite(at) ? new Date(at).toLocaleString(undefined, {weekday: 'short', hour: '2-digit', minute: '2-digit'}) : '';
};
// The computers a subscription is given to: directly, or (a plan) through its owner's plans for its tool, so a person's
// next or held plan counts the computers its set is on.
function subsComputers(sub: SubsRow): SubsComputer[] {
  const found = new Map<string, SubsComputer>();
  for (const c of sub.computers || []) found.set(typeof c === 'string' ? c : c.runner_id, c);
  if (subsOwned(sub.kind) && sub.owner_id) {
    for (const a of SUBS.assignments) if (a.person === sub.owner_id && a.runtime === subsRuntime(sub) && !found.has(a.runner_id)) found.set(a.runner_id, a.runner_id);
  }
  return [...found.values()];
}
const subsOffline = (c: SubsComputer) => {
  const machine = (SETTINGS_DATA?.machines || []).find(row => row.id === (typeof c === 'string' ? c : c.runner_id));
  return !!machine && (machine.online === false || ['stopped', 'stopping'].includes(String(machine.cloud_machine?.state)));
};
// The count of computers, their names in its tooltip; an amber dot when one is offline, which opens Settings > Computers.
function subsCountHTML(sub: SubsRow) {
  const computers = subsComputers(sub);
  const count = computers.length;
  const title = count ? computers.map(subsComputerLabel).join(', ') : 'Not on a computer. Give it to one in Settings › Computers.';
  const offline = computers.filter(subsOffline).map(subsComputerLabel);
  const words = offline.length ? `${offline.join(', ')} ${offline.length === 1 ? 'is' : 'are'} offline` : '';
  return `<td class="subs-count" title="${esc(title)}"><span class="nav-icon" aria-hidden="true">computer</span><span class="tnum">${count}</span><span class="sr-only"> ${count === 1 ? 'computer' : 'computers'}</span>${words ? `<button type="button" class="subs-offline" data-subs-offline title="${esc(words)}" aria-label="${esc(words)}. Open Settings › Computers"></button>` : ''}</td>`;
}
// In a person's group: the plan in use and the next one get a short mark after the name; the in-use one names its
// computers in the tooltip.
function subsPickHTML(sub: SubsRow, next: boolean) {
  const using = sub.in_use_on || [];
  if (using.length) return `<span class="pill subs-mark in-use" data-subs-mark="in-use" title="${esc(`In use on ${using.map(subsComputerLabel).join(', ')}`)}">In use</span>`;
  if (next) return '<span class="pill subs-mark" data-subs-mark="next" title="Next when the plan in use runs out">Next</span>';
  return '';
}
// Kind: the tool for a plan ("Claude", "ChatGPT"), the key's kind for an API key.
function subsKindLabel(sub: SubsRow) {
  const label = sub.label || subsKind(sub.kind).label;
  return subsOwned(sub.kind) ? label.replace(/ plan$/i, '') : label;
}
function subsRowHTML(sub: SubsRow, next = false) {
  const pick = subsPickHTML(sub, next);
  const order = typeof sub.order === 'number' ? String(sub.order) : subsOwned(sub.kind) ? '—' : '·';
  return `<tr data-subs-row="${esc(sub.id)}"${sub.in_use_on?.length ? ' class="subs-in-use"' : ''}>
    <td class="subs-order tnum muted" title="${typeof sub.order === 'number' ? `${esc(subsToolName(subsRuntime(sub)))} pick order` : ''}">${order}</td>
    <td class="subs-name">${esc(sub.name)}${pick ? ` ${pick}` : ''}</td>
    <td class="muted subs-kind">${esc(subsKindLabel(sub))}</td>
    ${subsCountHTML(sub)}
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
  else html = `<div class="scroll"><table class="settings-table subs-table"><thead><tr><th><span class="sr-only">Order</span></th><th>Name</th><th>Kind</th><th><span class="sr-only">Computers</span></th><th>Usage</th><th><span class="sr-only">Actions</span></th></tr></thead>
    ${subsGroups().map(subsGroupHTML).join('')}</table></div>`;
  const restore = computerFocusRestore(el);
  patchHTML(el, html);
  restore();
  el.onclick = (event: MouseEvent) => {
    if (eventElement(event)?.closest('[data-subs-add]')) { planSignIn(); return; }
    if (eventElement(event)?.closest('[data-subs-offline]')) { settingsShow('computers'); return; }
    const reconnect = eventElement(event)?.closest<HTMLElement>('[data-subs-reconnect]');
    if (reconnect) { const sub = subsById(reconnect.dataset.subsReconnect); if (sub) planSignIn({sub}); return; }
    const more = eventElement(event)?.closest<HTMLElement>('[data-subs-more]');
    if (more) subsMenu(more, subsById(more.dataset.subsMore));
  };
}
// The list grouped by owner: the viewer's own group first, then the others by name, then "No owner" (API keys, and any
// plan without one). Inside a group, each tool's plans in pick order (`order`), then the held ones and keys by name.
interface SubsGroup {owner: string; name: string; rows: SubsRow[]}
function subsGroups(): SubsGroup[] {
  const me = S.me?.id ? 'human:' + S.me.id : '';
  const groups = new Map<string, SubsGroup>();
  for (const sub of SUBS.rows) {
    const owner = subsOwned(sub.kind) && sub.owner_id ? sub.owner_id : '';
    if (!groups.has(owner)) groups.set(owner, {owner, name: owner ? sub.owner_name || subsPersonId(owner) : 'No owner', rows: []});
    groups.get(owner)!.rows.push(sub);
  }
  const tools = subsTools();
  for (const group of groups.values()) group.rows.sort((a, b) =>
    Number(typeof a.order !== 'number') - Number(typeof b.order !== 'number') || tools.indexOf(subsRuntime(a)) - tools.indexOf(subsRuntime(b))
    || (a.order ?? 0) - (b.order ?? 0) || a.name.localeCompare(b.name));
  return [...groups.values()].sort((a, b) => Number(!a.owner) - Number(!b.owner) || Number(b.owner === me) - Number(a.owner === me) || a.name.localeCompare(b.name));
}
function subsGroupHTML(group: SubsGroup) {
  const me = S.me?.id && group.owner === 'human:' + S.me.id;
  const keys = group.rows.every(sub => !subsOwned(sub.kind));
  const count = subsPlural(group.rows.length, keys ? 'API key' : group.owner ? 'plan' : 'subscription');
  const person = (SETTINGS_DATA.people || []).find(p => p.id === subsPersonId(group.owner)) || {id: subsPersonId(group.owner), name: group.name};
  const avatar = group.owner ? personAvatar(person, 22) : '<span class="av subs-noone" aria-hidden="true">—</span>';
  // The next plan of each tool: the first in pick order after the one in use.
  const next = new Set<string>();
  for (const tool of subsTools()) {
    const plans = group.rows.filter(sub => subsRuntime(sub) === tool && typeof sub.order === 'number');
    if (plans.some(sub => sub.in_use_on?.length)) { const after = plans.find(sub => !sub.in_use_on?.length); if (after) next.add(after.id); }
  }
  return `<tbody class="subs-group" data-subs-group="${esc(group.owner || 'none')}"><tr class="subs-group-head"><th colspan="6" scope="rowgroup"><span class="subs-group-title">${avatar}<span class="subs-group-name">${esc(group.name)}</span><span class="muted">· ${esc(count)}</span>${me ? '<span class="pill subs-you">You</span>' : ''}</span></th></tr>
    ${group.rows.map(sub => subsRowHTML(sub, next.has(sub.id))).join('')}</tbody>`;
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
// A person's plans for a tool, as the picker offers them: "person:<actor>" in the select's value.
const SUBS_PERSON = 'person:';
const subsPersonPlans = (name: string, count: number) => `${name}'s plans · ${count}`;
// The line under a computer's select when it is given a person's plans: which plan is in use, and why.
function subsSetLine(machine: SubsMachine, runtime: string, set: SubsSet) {
  const why = set.why || {kind: 'soonest'};
  const until = subsWhen(why.until);
  if (why.kind === 'none') return `None of ${set.person_name}'s plans is for ${subsToolName(runtime)}`;
  if (why.kind === 'all_held') {
    // Past its own login, the computer's own Fall back to Tico Gateway decides (computer-detail.ts), not the company's switch.
    const runs = subsSignedInHere(machine, runtime) ? "using this computer's own login" : machine.gateway_fallback ? 'on the Gateway' : 'waiting';
    return ['All at their limit', until ? `next reset ${until}` : '', runs].filter(Boolean).join(' · ');
  }
  const on = `On ${set.on_name || 'a plan'}`;
  if (why.kind === 'held') return `${on} · ${why.plan} at its limit${until ? ` until ${until}` : ''}`;
  if (why.kind === 'reconnect') return `${on} · ${why.plan} needs reconnecting`;
  return `${on} · soonest reset with usage left`;
}
function subsPickerHTML(machine: SubsMachine, runtime: string) {
  if (machine.revoked_at) return '<span class="muted">—</span>';
  // An older runner still runs on its own (or a shared) login: its chip, and Needs update beside it.
  if (!SUBS.capable!.includes(machine.id)) {
    const chips = machineShown(machine).filter(conn => conn.runtime === runtime).map(conn => machineChipHTML(machine, conn)).join('');
    return `<span class="subs-cell">${chips ? `<span class="machine-chips">${chips}</span>` : ''}<span class="pill waiting" data-subs-needs-update title="Update Tico on ${esc(machine.label)} to give it a subscription">Needs update</span></span>`;
  }
  const given = subsAssignment(machine.id, runtime);
  const set = given?.person ? given.set : null;
  const current = set ? null : subsAssigned(machine.id, runtime);
  const value = given?.person ? SUBS_PERSON + given.person : current?.id || '';
  // People first (each one who owns a plan for this tool, with their count), single plans next with their owner, this
  // computer's own login (or what runs it without one) last.
  const people = SUBS.people.filter(person => Number(person[runtime]) > 0);
  const options = SUBS.rows.filter(sub => subsRuntime(sub) === runtime);
  const tool = subsToolName(runtime);
  const option = (v: string, text: string) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(text)}</option>`;
  const select = `<select class="subs-pick" data-subs-pick="${esc(runtime)}" data-runner="${esc(machine.id)}" data-current="${esc(value)}" aria-label="${esc(`${tool} on ${machine.label}`)}"${subsMayEdit() ? '' : ' disabled'}>
    ${people.length ? `<optgroup label="People">${people.map(person => option(SUBS_PERSON + person.id, subsPersonPlans(person.name, Number(person[runtime])))).join('')}</optgroup>` : ''}
    ${options.length ? `<optgroup label="Single plans">${options.map(sub => option(sub.id, sub.owner_name && subsOwned(sub.kind) ? `${sub.name} · ${sub.owner_name}` : sub.name)).join('')}</optgroup>` : ''}
    ${option('', subsNoneWords(machine, runtime) === 'Signed in here' ? "This computer's own login" : subsNoneWords(machine, runtime))}</select>`;
  if (set) return `<span class="subs-cell subs-cell-set">${select}<span class="subs-set-line muted" data-subs-set-line>${esc(subsSetLine(machine, runtime, set))}</span></span>`;
  return `<span class="subs-cell">${select}${current?.status === 'needs_reconnect' ? subsStatusHTML(current) : ''}</span>`;
}
// A change: PUT, and when runs would stop, an in-page confirm with the counts, then PUT again with confirm.
async function subsPick(select: HTMLSelectElement) {
  const runner = select.dataset.runner!, runtime = select.dataset.subsPick!, was = select.dataset.current || '';
  const machine = (SETTINGS_DATA.machines || []).find(m => m.id === runner);
  const computer = machine?.label || runner, tool = subsToolName(runtime);
  const person = select.value.startsWith(SUBS_PERSON) ? select.value.slice(SUBS_PERSON.length) : null;
  const id = person ? null : select.value || null;
  const owner = person ? SUBS.people.find(p => p.id === person) : null;
  const name = person ? `${owner?.name || subsPersonId(person)}'s plans` : id ? subsById(id)?.name || id : select.selectedOptions[0]?.textContent || 'None';
  const body = {runner_id: runner, runtime, subscription: id, ...(person ? {person} : {})};
  select.disabled = true;
  try {
    const done = await subsConfirmed((confirm: boolean): Promise<import('../types/api').ComputerSubscriptionResult> => put('/v2/computer-subscriptions', {...body, confirm}),
      running => ({title: `Switch ${computer}'s ${tool} to ${name}?`,
        text: `${subsStopWords(running)}, and every ${tool} session on this computer starts fresh.`, ok: 'Switch', danger: false}));
    if (!done) { select.value = was; return; }
    SUBS.assignments = SUBS.assignments.filter(row => !(row.runner_id === runner && row.runtime === runtime));
    if (id) SUBS.assignments.push({runner_id: runner, runtime, subscription_id: id});
    select.dataset.current = select.value;
    const stopped = Number(done?.stopped) || 0;
    toast(`${computer}'s ${tool}: ${name}${stopped ? ` · ${subsPlural(stopped, 'task')} restarted` : ''}`);
  } catch (error) { toast(errorMessage(error, ''), true); select.value = was; }
  finally { select.disabled = !subsMayEdit(); }
  await subsRefresh();
}
