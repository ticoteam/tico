/* ui/app/inboxes-outbox.js — Letters out through Tico's Gateway. Registry: ui/app/inboxes.js. */
'use strict';

const LETTERS_OUT_MODES = [['ask_first', 'Ask first'], ['on_their_own', 'On their own'], ['never', 'Never']];
let LETTERS_OUT_LIVE = null;
const lettersOutMoney = micros => '$' + (Number(micros || 0) / 1000000).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 6});
const lettersOutAddress = address => address ? [address.name, address.line1, address.line2,
  [address.city, address.state, address.postal_code].filter(Boolean).join(' ')].filter(Boolean).join(', ') : '';
const lettersOutSettings = ctx => ctx.once('letters-out-settings', async () => {
  try { return await get('/v2/letters/settings', {fresh: true}); }
  catch (e) { if (e.status === 404 || e.status === 503 || e.code === 'not_found' || e.code === 'unconfigured') return {availability: 'unconfigured'}; throw e; }
});
const lettersOutGrants = ctx => ctx.admin ? ctx.once('letters-out-grants', () => get('/v2/letters/outboxes/company/grants', {fresh: true})) : Promise.resolve({grants: []});
const lettersOutName = grant => grant.grantee_type === 'bot' ? botDisplayName(grant.grantee_id) : personDisplay(grant.grantee_id);
const lettersOutStatus = settings => settings.mode === 'never' ? ['off', 'Never']
  : settings.availability === 'unconfigured' ? ['off', 'Coming soon']
  : settings.availability !== 'ready' ? ['off', 'Not set up']
  : !settings.return_address ? ['attention', 'Return address needed'] : ['ok', 'Ready'];
function lettersOutOptions(value, company, inherit = false) {
  if ((company === 'never' && value !== 'never') || (company === 'ask_first' && value === 'on_their_own')) value = null;
  return (inherit ? `<option value=""${value == null ? ' selected' : ''}>Company default</option>` : '') +
    LETTERS_OUT_MODES.map(([key, label]) => `<option value="${key}"${key === value ? ' selected' : ''}${
      (company === 'never' && key !== 'never') || (company === 'ask_first' && key === 'on_their_own') ? ' disabled' : ''}>${label}</option>`).join('');
}
function lettersOutCap(value) {
  if (String(value).trim() === '') return null;
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0 || amount > 1000000) throw new Error('Enter a monthly cap from $0 to $1,000,000.');
  return Math.round(amount * 1000000);
}
function lettersOutProof(row) {
  return /^\/api\/v2\/files\/[A-Za-z0-9_-]+$/.test(String(row.proof || ''))
    ? `<a href="${esc(row.proof)}" target="_blank" rel="noopener">Proof PDF</a>` : '';
}
function lettersOutActivity(row) {
  const labels = {quoted: 'Quoted', waiting_for_ok: 'Waiting for OK', ready: 'Queued', submitting: 'Checking submission',
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
    ${['none', 'void', 'reversed'].includes(row.billing) ? '<span>Not charged</span>' : ''}</div>
    ${row.status === 'needs_review' ? '<p class="muted">Submission needs an admin’s review. The total stays reserved until the outcome is confirmed.</p>' : ''}
    <div class="lo-letter-actions">${row.redacted ? '' : lettersOutProof(row)}${!row.redacted && row.status === 'waiting_for_ok' ? '<a href="#/foryou">Review</a>' : ''}
      ${canCancel ? `<button class="ghost small" type="button" data-lo-cancel="${esc(row.id)}">Cancel</button>` : ''}
      ${row.redacted ? '<p class="muted">Recipient and proof are private.</p>' : `<details><summary>Details</summary><dl class="lo-facts"><dt>Recipient</dt><dd>${esc(lettersOutAddress(row.recipient))}</dd>
      <dt>Return address</dt><dd>${esc(lettersOutAddress(row.return_address))}</dd><dt>Print</dt><dd>${row.color ? 'Color' : 'Black and white'} · ${row.duplex ? 'Duplex' : 'Single sided'}</dd>
      ${row.tracking?.length ? `<dt>Tracking</dt><dd>${row.tracking.map(t => `${esc(t.barcode)}${t.status ? ' · ' + esc(t.status) : ''}`).join('<br>')}</dd>` : ''}
      ${row.error ? `<dt>Status</dt><dd>${esc(row.error.replaceAll('_', ' '))}</dd>` : ''}</dl></details>`}</div></li>`;
}

async function lettersOutAccess(host, ctx, config, grants) {
  if (!ctx.admin) {
    const access = await get('/v2/letters/access', {fresh: true});
    if (ctx.live()) host.innerHTML = inboxesCard('lo-access', 'Who can use it', `<p>${access.send ? 'Send access' : 'No send access'}</p>`);
    return;
  }
  const bots = await ctx.once('letters-out-bots', () => get('/v2/bots', {fresh: true}));
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
  host.querySelector('[data-lo-add]')?.addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
    const [type, id] = form.elements.principal.value.split(':');
    try { await put(`/v2/letters/outboxes/company/grants/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, {level: 'send'}); if (ctx.live()) ctx.refresh(); }
    catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
  });
  for (const item of host.querySelectorAll('[data-lo-grant]')) {
    const [type, id] = item.dataset.loGrant.split(':'), path = `/v2/letters/outboxes/company/grants/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
    item.querySelector('[data-lo-revoke]').onclick = async event => {
      const button = event.currentTarget; button.disabled = true;
      try { await writeRequest('DELETE', path, {}); if (ctx.live()) ctx.refresh(); }
      catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
    };
    item.querySelector('form')?.addEventListener('submit', async event => {
      event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
      try { await put(`/v2/letters/bots/${encodeURIComponent(id)}/settings`, {mode: form.elements.mode.value || null, monthly_cap_micros: lettersOutCap(form.elements.cap.value)});
        if (ctx.live()) { toast('Saved'); button.disabled = false; } }
      catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
    });
  }
}

async function lettersOutDetail(host, id, ctx) {
  LETTERS_OUT_LIVE?.(); LETTERS_OUT_LIVE = null;
  const config = await lettersOutSettings(ctx); if (!ctx.live()) return;
  if (config.id !== 'company') { host.innerHTML = inboxesSoon('lo-outbox', 'Letters out'); return {kind: 'outbox', id: 'company', name: 'Letters out', address: ''}; }
  const [grantData, activity] = await Promise.all([lettersOutGrants(ctx), get('/v2/letters?limit=25', {fresh: true})]);
  if (!ctx.live()) return;
  const [status, statusText] = lettersOutStatus(config), grants = grantData.grants || [];
  const rows = new Map((activity.items || []).map(row => [row.id, row])); let next = activity.next_before;
  let refreshing = null, refreshDirty = false, epoch = 0, revision = 0;
  const rowRevisions = new Map();
  host.innerHTML = inboxesHead(host, {name: 'Letters out', status, statusText, sub: 'Tico Gateway · At cost + 25%',
    actions: '<a class="ghost" href="#/settings/inboxes/settings/letters-out">Settings</a>'}) +
    '<div class="lo-layout"><div><div data-lo-access></div>' +
    inboxesCard('lo-automations', 'Automations', '<div data-lo-automations>Loading…</div>', inboxesAutomationsLink('letters')) +
    inboxesCard('lo-activity', 'Activity', '<ul class="lo-activity" data-lo-activity></ul><button type="button" class="ghost" data-lo-more hidden>Load more</button>') +
    '</div><aside>' + inboxesCard('lo-outbox', 'This outbox', `<dl class="lo-facts"><dt>Return address</dt><dd>${esc(lettersOutAddress(config.return_address) || 'Not set up')}</dd>
      <dt>Sending</dt><dd>${esc(LETTERS_OUT_MODES.find(([key]) => key === config.mode)?.[1] || config.mode)}</dd>
      <dt>Monthly cap</dt><dd>${config.monthly_cap_micros == null ? 'No cap' : lettersOutMoney(config.monthly_cap_micros)}</dd><dt>Billed by</dt><dd>Tico Gateway</dd></dl>`) + '</aside></div>';
  const paint = () => {
    if (!ctx.live()) return;
    host.querySelector('[data-lo-activity]').innerHTML = rows.size ? [...rows.values()].map(lettersOutActivity).join('') : '<li class="muted">No letters yet</li>';
    host.querySelector('[data-lo-more]').hidden = !next;
  };
  paint();
  const apply = event => {
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
    const pending = new Map(); refreshing = pending; refreshDirty = false;
    get('/v2/letters?limit=25', {fresh: true}).then(page => {
      if (!ctx.live() || refreshDirty) return;
      rows.clear(); for (const row of page.items || []) rows.set(row.id, row);
      for (const event of pending.values()) apply(event);
      next = page.next_before; paint();
    }).catch(e => { if (ctx.live() && !refreshDirty) toast(e.message, true); }).finally(() => {
      const again = refreshDirty; refreshing = null; refreshDirty = false;
      if (again && ctx.live()) invalidate();
    });
  };
  host.querySelector('[data-lo-activity]').onclick = async event => {
    const button = event.target.closest('[data-lo-cancel]'); if (!button) return; button.disabled = true;
    const id = button.dataset.loCancel, started = epoch, seen = rowRevisions.get(id);
    try { const row = await post(`/v2/letters/${encodeURIComponent(button.dataset.loCancel)}/cancel`, {});
      if (ctx.live() && started === epoch && seen === rowRevisions.get(id)) {
        rows.set(row.id, row); paint(); toast(row.status === 'cancelled' ? 'Cancelled' : row.status); } }
    catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
  };
  host.querySelector('[data-lo-more]').onclick = async event => {
    const button = event.currentTarget; button.disabled = true;
    const started = epoch, seen = revision;
    try { const more = await get(`/v2/letters?limit=25&before=${encodeURIComponent(next)}`, {fresh: true}); if (!ctx.live() || started !== epoch) return;
      for (const row of more.items || []) if ((rowRevisions.get(row.id) || 0) <= seen) rows.set(row.id, row);
      next = more.next_before; paint(); }
    catch (e) { if (ctx.live()) toast(e.message, true); }
    finally { if (ctx.live()) button.disabled = false; }
  };
  LETTERS_OUT_LIVE = liveOn('letters', event => {
    if (!ctx.live()) { LETTERS_OUT_LIVE?.(); LETTERS_OUT_LIVE = null; return; }
    if (event.invalidate) { invalidate(); return; }
    if (event.gone || event.letter) {
      rowRevisions.set(event.id, ++revision);
      refreshing?.set(event.id, event); apply(event); paint();
    }
  });
  await lettersOutAccess(host.querySelector('[data-lo-access]'), ctx, config, grants);
  if (!ctx.live()) return;
  const automations = await ctx.once('automations', () => get('/v2/automations?limit=100', {fresh: true}));
  if (!ctx.live()) return;
  const used = new Set([...rows.values()].map(row => row.automation_id).filter(Boolean));
  const related = (Array.isArray(automations) ? automations : automations.automations || []).filter(a => used.has(a.id) || /hub_letter_(quote|send)/.test(JSON.stringify(a.flow || {})));
  host.querySelector('[data-lo-automations]').innerHTML = related.length ? `<ul class="ib-autos">${related.map(a =>
    `<li><a href="#/automations/${encodeURIComponent(a.id)}">${esc(a.name || a.id)}</a>${a.enabled === false ? ' <span class="muted">· off</span>' : ''}</li>`).join('')}</ul>` : '<p class="muted">None</p>';
  return {kind: 'outbox', id: 'company', name: 'Letters out', address: ''};
}

async function lettersOutSettingsRender(host, ctx) {
  const config = await lettersOutSettings(ctx); if (!ctx.live()) return;
  if (config.id !== 'company') { host.innerHTML = inboxesSoon('inboxes-letters-out', 'Letters out'); return; }
  const [status, statusText] = lettersOutStatus(config), address = config.return_address || {};
  const fields = [['name', 'Name'], ['line1', 'Street'], ['line2', 'Suite (optional)'], ['city', 'City'], ['state', 'State'], ['postal_code', 'ZIP']];
  host.innerHTML = inboxesCard('inboxes-letters-out', 'Letters out', `<p>${inboxesPill(status, statusText)} <span class="muted">Tico Gateway · At cost + 25%</span></p>
    <form class="lo-settings"><fieldset${ctx.admin ? '' : ' disabled'}><legend>Return address</legend><div class="lo-address">${fields.map(([name, label]) =>
      `<label>${label}<input name="${name}" value="${esc(address[name] || '')}" maxlength="${name === 'state' ? 2 : name === 'postal_code' ? 10 : 100}"${name === 'state' ? ' pattern="[A-Z]{2}"' : name === 'postal_code' ? ' pattern="[0-9]{5}(-[0-9]{4})?"' : ''}></label>`).join('')}</div>
    <label class="lo-setting-row"><span>Sending</span><select name="mode">${lettersOutOptions(config.mode)}</select></label>
    <label class="lo-setting-row"><span>Monthly cap ($)</span><input name="cap" type="number" min="0" max="1000000" step="0.01" placeholder="No cap" value="${config.monthly_cap_micros == null ? '' : Number(config.monthly_cap_micros) / 1000000}"></label>
    <div class="lo-setting-row"><span>Always ask</span><span>Certified mail<br>Courts and agencies<br>Letters we couldn’t fully read<br>First letter to an address</span></div>
    ${ctx.admin ? '<div class="lo-controls"><button type="submit" class="primary">Save</button><button type="button" class="ghost" data-lo-check>Check connection</button><a href="#/settings/inboxes/outbox/company">Who can use it</a></div>' : ''}</fieldset></form>`);
  if (!ctx.admin) return;
  const form = host.querySelector('form');
  form.onsubmit = async event => {
    event.preventDefault(); const button = form.querySelector('[type=submit]'); button.disabled = true;
    try {
      const returnAddress = Object.fromEntries(fields.map(([key]) => [key, form.elements[key].value.trim()]));
      const any = Object.values(returnAddress).some(Boolean);
      if (any && ['name', 'line1', 'city', 'state', 'postal_code'].some(key => !returnAddress[key])) throw new Error('Complete the return address.');
      await put('/v2/letters/settings', {return_address: any ? returnAddress : null, mode: form.elements.mode.value,
        monthly_cap_micros: lettersOutCap(form.elements.cap.value)});
      if (ctx.live()) { toast('Saved'); ctx.refresh(); }
    } catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
  };
  host.querySelector('[data-lo-check]').onclick = async event => {
    const button = event.currentTarget; button.disabled = true;
    try { const result = await get('/v2/letters/settings?check=true', {fresh: true}); if (ctx.live()) {
      const [state, label] = lettersOutStatus(result), pill = host.querySelector('[data-ib-status]');
      if (pill) pill.outerHTML = inboxesPill(state, label);
      toast(label); button.disabled = false;
    } }
    catch (e) { if (ctx.live()) { toast(e.message, true); button.disabled = false; } }
  };
}

inboxesKind('outbox', {group: 'letters-out', label: 'Letters out', order: 50,
  rows: async ctx => { const config = await lettersOutSettings(ctx); if (!ctx.live() || config.id !== 'company') return [];
    const [data, grants] = await Promise.all([ctx.once('letters-out-latest', () => get('/v2/letters?limit=1', {fresh: true})), lettersOutGrants(ctx)]); if (!ctx.live()) return [];
    const [status, statusText] = lettersOutStatus(config), all = grants.grants || [];
    return [{id: 'company', name: 'Letters out', sub: 'Tico Gateway', status, statusText, latest: data.items?.[0]?.created_at ? Date.parse(data.items[0].created_at) : null,
      usedBy: {bots: all.filter(g => g.grantee_type === 'bot').length, people: all.filter(g => g.grantee_type === 'person').length,
        names: all.slice(0, 3).map(lettersOutName)}, search: 'letters print post postage ' + lettersOutAddress(config.return_address)}]; },
  detail: lettersOutDetail});
inboxesSetting('letters-out', {section: 'letters-out', order: 10, render: lettersOutSettingsRender});
inboxesStep('letters-out', {order: 40, title: 'Send letters', optional: true,
  done: async ctx => !!(await lettersOutSettings(ctx)).return_address,
  render: async (host, ctx) => {
    const config = await lettersOutSettings(ctx); if (!ctx.live()) return;
    // An older server keeps the shell's disabled setup step until its Letters API is available.
    if (config.id !== 'company') { host.innerHTML = '<p><button type="button" class="ghost" disabled>Connect Click2Mail</button> <span class="pill">Coming soon</span></p>'; return; }
    await lettersOutSettingsRender(host, ctx);
  }});
