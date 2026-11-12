/* ui/app/inboxes-outbox.ts — Letters out through Tico's Gateway: its page (#/tools/address/outbox) and its card on
   Tools › Settings › Letters out. Registry: ui/app/inboxes.ts. */
'use strict';

// The fields of the letters routes' answers that this page reads (api.d.ts has no type for them).
interface LettersOutAddress {
  [field: string]: string | null | undefined;
}
interface LettersOutConfig {
  id?: string;
  availability?: string;
  mode?: string;
  return_address?: LettersOutAddress | null;
  monthly_cap_micros?: number | null;
}
interface LettersOutGrant {
  grantee_type: string;
  grantee_id: string;
  mode?: string | null;
  monthly_cap_micros?: number | null;
}
interface LettersOutGrants {
  grants?: LettersOutGrant[];
}
interface LettersOutRow {
  id: string;
  acting_bot_id?: string | null;
  automation_id?: string | null;
  billing?: string | null;
  can_cancel?: boolean | null;
  cancel_possible?: boolean;
  color?: boolean;
  created_at: string;
  duplex?: boolean;
  error?: string | null;
  mail_class: string;
  page_count?: number | null;
  proof?: string | null;
  recipient?: LettersOutAddress | null;
  redacted?: boolean;
  requested_by?: string | null;
  return_address?: LettersOutAddress | null;
  status: string;
  total_micros?: number | null;
  tracking?: { barcode: string; status?: string | null }[] | null;
}
interface LettersOutPage {
  items?: LettersOutRow[];
  next_before?: string | null;
}
// One change to the letters list, pushed live: the letter it sets, or the id of one that is gone.
interface LettersOutEvent {
  id: string;
  gone?: boolean;
  invalidate?: boolean;
  letter?: LettersOutRow;
}
interface LettersOutAccess {
  send?: boolean;
}
interface LettersOutBot {
  id: string;
  display_name?: string | null;
  name?: string | null;
  slug?: string | null;
}
interface LettersOutAutomation {
  id: string;
  name?: string | null;
  enabled?: boolean;
  flow?: unknown;
}
type LettersOutAutomations = LettersOutAutomation[] | { automations?: LettersOutAutomation[] };

// The named controls of this card's forms. form.elements has no typed names, so each use casts it to this (erased).
interface LettersOutControls {
  principal: HTMLSelectElement;
  mode: HTMLSelectElement;
  cap: HTMLInputElement;
  [name: string]: HTMLInputElement | HTMLSelectElement;
}

// A page element the drawn markup must hold: a missing one throws a plain Error that names it.
function outboxNeed<T extends Element = HTMLElement>(selector: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`${selector} is not on the page`);
  return el;
}
// A letters form's named controls: the one cast for them, so every form reads its fields the same way.
function lettersOutControls(form: HTMLFormElement): LettersOutControls {
  return form.elements as unknown as LettersOutControls;
}
const LETTERS_OUT_MODES: [string, string][] = [['ask_first', 'Ask first'], ['on_their_own', 'On their own'], ['never', 'Never']];
let LETTERS_OUT_LIVE: (() => void) | null = null;
const lettersOutMoney = (micros: number | null | undefined) => '$' + (Number(micros || 0) / 1000000).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 6});
const lettersOutAddress = (address: LettersOutAddress | null | undefined) => address ? [address.name, address.line1, address.line2,
  [address.city, address.state, address.postal_code].filter(Boolean).join(' ')].filter(Boolean).join(', ') : '';
const lettersOutSettings = (ctx: InboxCtx): Promise<LettersOutConfig> => ctx.once('letters-out-settings', async (): Promise<LettersOutConfig> => {
  try { return await get('/v2/letters/settings', {fresh: true}); }
  catch (e) {
    // The code of a thrown value: api.js's ApiFailure carries no top-level code, so this reads the property only when it is there.
    const code = typeof e === 'object' && e !== null && 'code' in e ? e.code : undefined;
    if (errorStatus(e) === 404 || errorStatus(e) === 503 || code === 'not_found' || code === 'unconfigured') return {availability: 'unconfigured'};
    throw e;
  }
});
const lettersOutGrants = (ctx: InboxCtx): Promise<LettersOutGrants> => ctx.admin ? ctx.once('letters-out-grants', () => get('/v2/letters/outboxes/company/grants', {fresh: true})) : Promise.resolve({grants: []});
const lettersOutName = (grant: LettersOutGrant) => grant.grantee_type === 'bot' ? botDisplayName(grant.grantee_id) : personDisplay(grant.grantee_id);
const lettersOutStatus = (settings: LettersOutConfig): [string, string] => settings.mode === 'never' ? ['off', 'Never']
  : settings.availability === 'unconfigured' ? ['off', 'Coming soon']
  : settings.availability !== 'ready' ? ['off', 'Not set up']
  : !settings.return_address ? ['attention', 'Return address needed'] : ['ok', 'Ready'];
function lettersOutOptions(value: string | null | undefined, company?: string | null, inherit = false) {
  if ((company === 'never' && value !== 'never') || (company === 'ask_first' && value === 'on_their_own')) value = null;
  return (inherit ? `<option value=""${value == null ? ' selected' : ''}>Company default</option>` : '') +
    LETTERS_OUT_MODES.map(([key, label]) => `<option value="${key}"${key === value ? ' selected' : ''}${
      (company === 'never' && key !== 'never') || (company === 'ask_first' && key === 'on_their_own') ? ' disabled' : ''}>${label}</option>`).join('');
}
// A grant's principal as 'type:id': its option's value, or a grant row's data-lo-grant.
function lettersGrantParts(value: string): [string, string] {
  const [type = '', id = ''] = value.split(':');
  return [type, id];
}
function lettersOutCap(value: string) {
  if (String(value).trim() === '') return null;
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0 || amount > 1000000) throw new Error('Enter a monthly cap from $0 to $1,000,000.');
  return Math.round(amount * 1000000);
}
function lettersOutProof(row: LettersOutRow) {
  return /^\/api\/v2\/files\/[A-Za-z0-9_-]+$/.test(String(row.proof || ''))
    ? `<a href="${esc(row.proof)}" target="_blank" rel="noopener">Proof PDF</a>` : '';
}
// row.billing may be null (never billed): includes() is then false, as before the ! typed it.
function lettersOutActivity(row: LettersOutRow) {
  const labels: Record<string, string> = {quoted: 'Quoted', waiting_for_ok: 'Waiting for OK', ready: 'Queued', submitting: 'Checking submission',
    needs_review: 'Needs review',
    submitted: 'Submitted', printing: 'Printing', mailed: 'Mailed', delivered: 'Delivered', returned: 'Returned',
    failed: 'Failed', cancelled: 'Cancelled', declined: 'Declined'};
  const tone = row.error || row.status === 'needs_review' ? 'attention' : ['waiting_for_ok', 'submitting', 'ready'].includes(row.status) ? 'pending'
    : ['failed', 'returned'].includes(row.status) ? 'attention' : ['cancelled', 'declined', 'quoted'].includes(row.status) ? 'off' : 'ok';
  const canCancel = row.can_cancel ?? (row.cancel_possible || ['quoted', 'waiting_for_ok', 'ready'].includes(row.status));
  const sender = row.requested_by || (row.acting_bot_id ? 'bot:' + row.acting_bot_id : '');
  return `<li class="lo-letter" data-lo-letter="${esc(row.id)}"><div class="lo-letter-top"><div>
    <strong>${row.redacted ? 'Private letter' : esc(row.recipient?.name || 'Recipient')}</strong>${row.redacted ? '' : `<span class="muted">${esc([row.recipient?.city, row.recipient?.state].filter(Boolean).join(', '))}</span>`}
    </div>${inboxesPill(tone, row.error === 'unconfigured' ? 'Coming soon' : labels[row.status] || row.status)}</div>
    <div class="lo-letter-meta"><time datetime="${esc(row.created_at)}">${esc(new Date(row.created_at).toLocaleDateString())}</time>
    <span>${esc(row.page_count ?? '—')} pages</span><span>${esc(String(row.mail_class).replaceAll('_', ' '))}</span>
    <span>${sender ? esc(actorLabel(sender)) : 'Person'}</span><strong>${lettersOutMoney(row.total_micros)}</strong>
    ${['none', 'void', 'reversed'].includes(row.billing!) ? '<span>Not charged</span>' : ''}</div>
    ${row.status === 'needs_review' ? '<p class="muted">Submission needs an admin’s review. The total stays reserved until the outcome is confirmed.</p>' : ''}
    <div class="lo-letter-actions">${row.redacted ? '' : lettersOutProof(row)}${!row.redacted && row.status === 'waiting_for_ok' ? `<a href="${NEEDS_YOU}">Review</a>` : ''}
      ${canCancel ? `<button class="ghost small" type="button" data-lo-cancel="${esc(row.id)}">Cancel</button>` : ''}
      ${row.redacted ? '<p class="muted">Recipient and proof are private.</p>' : `<details><summary>Details</summary><dl class="lo-facts"><dt>Recipient</dt><dd>${esc(lettersOutAddress(row.recipient))}</dd>
      <dt>Return address</dt><dd>${esc(lettersOutAddress(row.return_address))}</dd><dt>Print</dt><dd>${row.color ? 'Color' : 'Black and white'} · ${row.duplex ? 'Duplex' : 'Single sided'}</dd>
      ${row.tracking?.length ? `<dt>Tracking</dt><dd>${row.tracking.map(t => `${esc(t.barcode)}${t.status ? ' · ' + esc(t.status) : ''}`).join('<br>')}</dd>` : ''}
      ${row.error ? `<dt>Status</dt><dd>${esc(row.error.replaceAll('_', ' '))}</dd>` : ''}</dl></details>`}</div></li>`;
}

async function lettersOutAccess(host: Element, ctx: InboxCtx, config: LettersOutConfig, grants: LettersOutGrant[]): Promise<void> {
  if (!ctx.admin) {
    const access: LettersOutAccess = await get('/v2/letters/access', {fresh: true});
    if (ctx.live()) host.innerHTML = inboxesCard('lo-access', 'Who can use it', `<p>${access.send ? 'Send access' : 'No send access'}</p>`);
    return;
  }
  const bots: LettersOutBot[] = await ctx.once('letters-out-bots', () => get('/v2/bots', {fresh: true}));
  if (!ctx.live()) return;
  const candidates = [...(Array.isArray(bots) ? bots : []).map(b => ({type: 'bot', id: b.id, name: b.display_name || b.name || b.slug || b.id})),
    ...(S.people || []).filter(p => !p.hidden).map(p => ({type: 'person', id: p.id, name: p.name || p.id}))]
    .filter(p => !grants.some(g => g.grantee_type === p.type && g.grantee_id === p.id));
  host.innerHTML = inboxesCard('lo-access', 'Who can use it', `<p class="muted">Owners and admins</p><ul class="lo-grants">${grants.map(g =>
    `<li data-lo-grant="${esc(g.grantee_type + ':' + g.grantee_id)}"><div class="lo-grant-head"><strong>${esc(lettersOutName(g))}</strong>
      <button type="button" class="ghost small" data-lo-revoke>Remove</button></div>${g.grantee_type === 'bot' ?
      `<form class="lo-bot-policy"><label>Sending<select name="mode" aria-label="${esc(lettersOutName(g))} sends letters">${lettersOutOptions(g.mode, config.mode, true)}</select></label>
      <label>Monthly cap ($)<input name="cap" type="number" min="0" max="1000000" step="0.01" placeholder="No cap" value="${g.monthly_cap_micros == null ? '' : Number(g.monthly_cap_micros) / 1000000}"></label>
      <button type="submit" class="ghost small">Save</button></form>` : '<span class="muted">Send</span>'}</li>`).join('')}</ul>
    ${candidates.length ? `<form data-lo-add class="lo-grant-add"><label>Teammate<select name="principal">${candidates.map(p =>
      `<option value="${esc(p.type + ':' + p.id)}">${esc(p.name)}${p.type === 'bot' ? ' · Bot' : ''}</option>`).join('')}</select></label>
      <button type="submit" class="ghost">Give send access</button></form>` : ''}`);
  const addForm = host.querySelector<HTMLFormElement>('[data-lo-add]');
  addForm?.addEventListener('submit', async event => {
    event.preventDefault(); const form = addForm!, button = outboxNeed<HTMLButtonElement>('button', form), controls = lettersOutControls(form); button.disabled = true;
    const [type, id] = lettersGrantParts(controls.principal.value);
    try { await put(`/v2/letters/outboxes/company/grants/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, {level: 'send'}); if (ctx.live()) ctx.refresh(); }
    catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
  });
  for (const item of host.querySelectorAll<HTMLElement>('[data-lo-grant]')) {
    const [type, id] = lettersGrantParts(item.dataset.loGrant!), path = `/v2/letters/outboxes/company/grants/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
    const revoke = item.querySelector<HTMLButtonElement>('[data-lo-revoke]')!;
    revoke.onclick = async () => {
      const button = revoke; button.disabled = true;
      try { await writeRequest('DELETE', path, {}); if (ctx.live()) ctx.refresh(); }
      catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
    };
    const policy = item.querySelector<HTMLFormElement>('form');
    policy?.addEventListener('submit', async event => {
      event.preventDefault(); const form = policy!, button = outboxNeed<HTMLButtonElement>('button', form), controls = lettersOutControls(form); button.disabled = true;
      try { await put(`/v2/letters/bots/${encodeURIComponent(id)}/settings`, {mode: controls.mode.value || null, monthly_cap_micros: lettersOutCap(controls.cap.value)});
        if (ctx.live()) { toast('Saved'); button.disabled = false; } }
      catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
    });
  }
}

async function lettersOutDetail(host: Element, _id: string, ctx: InboxCtx): Promise<{kind: string; id: string; name: string; address: string} | undefined> {
  LETTERS_OUT_LIVE?.(); LETTERS_OUT_LIVE = null;
  const config = await lettersOutSettings(ctx); if (!ctx.live()) return;
  if (config.id !== 'company') { host.innerHTML = inboxesSoon('lo-outbox', 'Letters out'); return {kind: 'outbox', id: 'company', name: 'Letters out', address: ''}; }
  const [grantData, activity] = await Promise.all([lettersOutGrants(ctx), get('/v2/letters?limit=25', {fresh: true})]);
  if (!ctx.live()) return;
  const [status, statusText] = lettersOutStatus(config), grants = grantData.grants || [];
  const rows = new Map<string, LettersOutRow>((activity.items || []).map((row: LettersOutRow): [string, LettersOutRow] => [row.id, row])); let next: string | null | undefined = activity.next_before;
  let refreshing: Map<string, LettersOutEvent> | null = null, refreshDirty = false, epoch = 0, revision = 0;
  const rowRevisions = new Map<string, number>();
  host.innerHTML = inboxesHead(host, {name: 'Letters out', status, statusText, sub: 'Tico Gateway · At cost + 25%',
    actions: '<a class="ghost" href="#/tools/settings/letters-out">Settings</a>'}) +
    '<div class="lo-layout"><div><div data-lo-access></div>' +
    inboxesCard('lo-automations', 'Automations', '<div data-lo-automations>Loading…</div>', inboxesAutomationsLink('letters')) +
    inboxesCard('lo-activity', 'Activity', '<ul class="lo-activity" data-lo-activity></ul><button type="button" class="ghost" data-lo-more hidden>Load more</button>') +
    '</div><aside>' + inboxesCard('lo-outbox', 'This outbox', `<dl class="lo-facts"><dt>Return address</dt><dd>${esc(lettersOutAddress(config.return_address) || 'Not set up')}</dd>
      <dt>Sending</dt><dd>${esc(LETTERS_OUT_MODES.find(([key]) => key === config.mode)?.[1] || config.mode)}</dd>
      <dt>Monthly cap</dt><dd>${config.monthly_cap_micros == null ? 'No cap' : lettersOutMoney(config.monthly_cap_micros)}</dd><dt>Billed by</dt><dd>Tico Gateway</dd></dl>`) + '</aside></div>';
  const paint = () => {
    if (!ctx.live()) return;
    outboxNeed<HTMLElement>('[data-lo-activity]', host).innerHTML = rows.size ? [...rows.values()].map(lettersOutActivity).join('') : '<li class="muted">No letters yet</li>';
    host.querySelector<HTMLElement>('[data-lo-more]')!.hidden = !next;
  };
  paint();
  const apply = (event: LettersOutEvent) => {
    if (event.gone) rows.delete(event.id);
    else if (event.letter) {
      if (!rows.has(event.id)) { const old = [...rows]; rows.clear(); rows.set(event.id, event.letter); for (const [key, value] of old) rows.set(key, value); }
      else rows.set(event.id, event.letter);
    }
  };
  const invalidate = () => {
    epoch++;
    // Every invalidation hides revoked content, including while an older read is in flight.
    rows.clear(); next = null; paint();
    if (refreshing) { refreshDirty = true; return; }
    const pending = new Map<string, LettersOutEvent>(); refreshing = pending; refreshDirty = false;
    get('/v2/letters?limit=25', {fresh: true}).then(page => {
      if (!ctx.live() || refreshDirty) return;
      rows.clear(); for (const row of page.items || []) rows.set(row.id, row);
      for (const event of pending.values()) apply(event);
      next = page.next_before; paint();
    }).catch(e => { if (ctx.live() && !refreshDirty) toast(errorMessage(e, ''), true); }).finally(() => {
      const again = refreshDirty; refreshing = null; refreshDirty = false;
      if (again && ctx.live()) invalidate();
    });
  };
  outboxNeed<HTMLElement>('[data-lo-activity]', host).onclick = async event => {
    const button = eventElement(event)!.closest<HTMLButtonElement>('[data-lo-cancel]'); if (!button) return; button.disabled = true;
    const id2 = button.dataset.loCancel!, started = epoch, seen = rowRevisions.get(id2);
    try { const row: LettersOutRow = await post(`/v2/letters/${encodeURIComponent(button.dataset.loCancel!)}/cancel`, {});
      if (ctx.live() && started === epoch && seen === rowRevisions.get(id2)) {
        rows.set(row.id, row); paint(); toast(row.status === 'cancelled' ? 'Cancelled' : row.status); } }
    catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
  };
  const loadMore = host.querySelector<HTMLButtonElement>('[data-lo-more]')!;
  loadMore.onclick = async () => {
    const button = loadMore; button.disabled = true;
    const started = epoch, seen = revision;
    try { const more = await get(`/v2/letters?limit=25&before=${encodeURIComponent(next!) /* the more button is hidden without a cursor */}`, {fresh: true}); if (!ctx.live() || started !== epoch) return;
      for (const row of more.items || []) if ((rowRevisions.get(row.id) || 0) <= seen) rows.set(row.id, row);
      next = more.next_before; paint(); }
    catch (e) { if (ctx.live()) toast(errorMessage(e, ''), true); }
    finally { if (ctx.live()) button.disabled = false; }
  };
  LETTERS_OUT_LIVE = liveOn('letters', (event: LettersOutEvent) => {
    if (!ctx.live()) { LETTERS_OUT_LIVE?.(); LETTERS_OUT_LIVE = null; return; }
    if (event.invalidate) { invalidate(); return; }
    if (event.gone || event.letter) {
      rowRevisions.set(event.id, ++revision);
      refreshing?.set(event.id, event); apply(event); paint();
    }
  });
  await lettersOutAccess(host.querySelector('[data-lo-access]')!, ctx, config, grants);
  if (!ctx.live()) return;
  const automations: LettersOutAutomations = await ctx.once('automations', () => get('/v2/automations?limit=100', {fresh: true}));
  if (!ctx.live()) return;
  const used = new Set([...rows.values()].map(row => row.automation_id).filter(Boolean));
  const related = (Array.isArray(automations) ? automations : automations.automations || []).filter(a => used.has(a.id) || /tico_letter_(quote|send)/.test(JSON.stringify(a.flow || {})));
  host.querySelector<HTMLElement>('[data-lo-automations]')!.innerHTML = related.length ? `<ul class="ib-autos">${related.map(a =>
    `<li><a href="#/automations/${encodeURIComponent(a.id)}">${esc(a.name || a.id)}</a>${a.enabled === false ? ' <span class="muted">· off</span>' : ''}</li>`).join('')}</ul>` : '<p class="muted">None</p>';
  return {kind: 'outbox', id: 'company', name: 'Letters out', address: ''};
}

async function lettersOutSettingsRender(host: Element, ctx: InboxCtx): Promise<void> {
  const config = await lettersOutSettings(ctx); if (!ctx.live()) return;
  if (config.id !== 'company') { host.innerHTML = inboxesSoon('inboxes-letters-out', 'Letters out'); return; }
  const [status, statusText] = lettersOutStatus(config), address: LettersOutAddress = config.return_address || {};
  const fields: [string, string][] = [['name', 'Name'], ['line1', 'Street'], ['line2', 'Suite (optional)'], ['city', 'City'], ['state', 'State'], ['postal_code', 'ZIP']];
  host.innerHTML = inboxesCard('inboxes-letters-out', 'Letters out', `<p>${inboxesPill(status, statusText)} <span class="muted">Tico Gateway · At cost + 25%</span></p>
    <form class="lo-settings"><fieldset${ctx.admin ? '' : ' disabled'}><legend>Return address</legend><div class="lo-address">${fields.map(([name, label]) =>
      `<label>${label}<input name="${name}" value="${esc(address[name] || '')}" maxlength="${name === 'state' ? 2 : name === 'postal_code' ? 10 : 100}"${name === 'state' ? ' pattern="[A-Z]{2}"' : name === 'postal_code' ? ' pattern="[0-9]{5}(-[0-9]{4})?"' : ''}></label>`).join('')}</div>
    <label class="lo-setting-row"><span>Sending</span><select name="mode">${lettersOutOptions(config.mode)}</select></label>
    <label class="lo-setting-row"><span>Monthly cap ($)</span><input name="cap" type="number" min="0" max="1000000" step="0.01" placeholder="No cap" value="${config.monthly_cap_micros == null ? '' : Number(config.monthly_cap_micros) / 1000000}"></label>
    <div class="lo-setting-row"><span>Always ask</span><span>Certified mail<br>Courts and agencies<br>Letters we couldn’t fully read<br>First letter to an address</span></div>
    ${ctx.admin ? '<div class="lo-controls"><button type="submit" class="primary">Save</button><button type="button" class="ghost" data-lo-check>Check connection</button><a href="#/tools/address/outbox">Who can use it</a></div>' : ''}</fieldset></form>`);
  if (!ctx.admin) return;
  const form = host.querySelector<HTMLFormElement>('form')!, controls = lettersOutControls(form);
  form.onsubmit = async event => {
    event.preventDefault(); const button = form.querySelector<HTMLButtonElement>('[type=submit]')!; button.disabled = true;
    try {
      const returnAddress: Record<string, string> = Object.fromEntries(fields.map(([key]): [string, string] => [key, controls[key]!.value.trim()]));
      const hasAddress = Object.values(returnAddress).some(Boolean);
      if (hasAddress && ['name', 'line1', 'city', 'state', 'postal_code'].some(key => !returnAddress[key])) throw new Error('Complete the return address.');
      await put('/v2/letters/settings', {return_address: hasAddress ? returnAddress : null, mode: controls.mode.value,
        monthly_cap_micros: lettersOutCap(controls.cap.value)});
      if (ctx.live()) { toast('Saved'); ctx.refresh(); }
    } catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
  };
  const check = host.querySelector<HTMLButtonElement>('[data-lo-check]')!;
  check.onclick = async () => {
    const button = check; button.disabled = true;
    try { const result = await get('/v2/letters/settings?check=true', {fresh: true}); if (ctx.live()) {
      const [state, label] = lettersOutStatus(result), pill = host.querySelector('[data-ib-status]');
      if (pill) pill.outerHTML = inboxesPill(state, label);
      toast(label); button.disabled = false;
    } }
    catch (e) { if (ctx.live()) { toast(errorMessage(e, ''), true); button.disabled = false; } }
  };
}

inboxesKind('outbox', {label: 'Letters out', order: 50,
  detail: lettersOutDetail});
inboxesSetting('letters-out', {section: 'letters-out', order: 10, render: lettersOutSettingsRender});
