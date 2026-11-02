/* ui/app/finance-admin.js — Finance › Sources and Access (spec §12, §7.5).
   Sources: every source of the books, grouped (banks, cards, payments, payroll, bills and invoices, Tico, accounting,
   manual), collapsed by default; a source opens its account mapping, sync and who sees it, and a finance admin syncs
   it now, maps its accounts to lines and disconnects it. "+ Source" connects Mercury: the token is stored in the
   credential vault and the connection made from it (the token never comes back).
   Access: who sees what in Finance, on the org chart (each teammate's default and grants), private lines, the owner's
   "View as", Edit defaults, and a teammate's "Grant more".
   Reads: GET /api/v2/finance/sources, /finance/access, /finance/balance-sheet (the lines an account maps to). Writes:
   POST /api/v2/credentials, /finance/connections, /finance/books/connect, /finance/books/{id}/disconnect; connection_sync, connection_disconnect, connection_map, view_grant,
   view_revoke and view_defaults_set through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- sources
const FIN_SRC = {open: new Set(), q: '', sel: ''};
function finSourcesPage() {
  const caps = finCaps(), s = FIN_SRC;
  FIN.src = {groups: [], lines: [], load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-fin-q value="${esc(s.q)}" placeholder="Search sources, accounts, cards" aria-label="Search sources"></label>
      <button type="button" class="ghost" data-fin-collapse>Expand all</button><span class="spacer"></span>
      ${finSourceMenuHtml(caps)}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-src">${skelRows(8, {dot: false})}</div>`, '', 'Selected source');
  const body = $('#fin-body');
  body.querySelector('[data-fin-q]').oninput = ev => { s.q = ev.target.value.trim().toLowerCase(); finSrcDraw(); };
  body.onclick = ev => {
    const add = ev.target.closest('[data-fin-connect]');
    if (add) { finPop(add, $('#fin-connect')); return; }
    const pick = ev.target.closest('[data-fin-connect-item]');
    if (pick) { $('#fin-connect').hidden = true; add?.setAttribute('aria-expanded', 'false'); finConnect(pick.dataset.finConnectItem); return; }
    if (ev.target.closest('[data-fin-collapse]')) {
      if (finSrcAllShut()) for (const g of FIN.src.groups) s.open.add(g.group); else s.open.clear();
      finSrcDraw(); return;
    }
    const g = ev.target.closest('[data-fin-group]');
    if (g) { const k = g.dataset.finGroup; if (s.open.has(k)) s.open.delete(k); else s.open.add(k); finSrcDraw(); return; }
    const tr = ev.target.closest('tr[data-fin-src]');
    if (tr) { s.sel = tr.dataset.finSrc; finSrcDraw(); finSrcRail(); }
  };
  void finSrcLoad();
}
async function finSrcLoad() {
  const state = FIN.src, load = ++state.load, admin = finCaps().may('finance.admin');
  const [src, bs] = await Promise.allSettled([get('/v2/finance/sources' + finAs('?'), {fresh: true}),
    admin ? get('/v2/finance/balance-sheet', {fresh: true}) : Promise.resolve(null)]);
  if (FIN?.src !== state || state.load !== load || !$('#fin-src')) return;
  if (src.status === 'rejected') { $('#fin-src').innerHTML = finFail(src.reason); return; }
  state.groups = (src.value.groups || []).filter(g => g.count > 0);
  // The asset and liability lines a money account can map to.
  const sheet = bs.status === 'fulfilled' ? bs.value : null;
  state.lines = sheet ? [...(sheet.assets || []).map(r => ({...r, type: 'asset'})), ...(sheet.liabilities || []).map(r => ({...r, type: 'liability'}))]
    .filter(r => r.account_id).sort((a, b) => String(a.code).localeCompare(String(b.code))) : [];
  finSrcDraw(); finSrcRail();
}
const finSrcKey = r => `${r.kind}:${r.id}`;
// What a source holds, how it is booked, how often it syncs, when it last did, and whether it is well.
function finSrcCols(r) {
  const n = (v, one, many) => `${v.toLocaleString('en-US')} ${v === 1 ? one : many}`;
  const sum = o => Object.values(o || {}).reduce((a, b) => a + (Number(b) || 0), 0);
  const sync = r.sync, err = sync?.last_error;
  let has = '', feeds = '', how = '', last = '', status = ['', ''];
  // backend/finance/tab.py sources: Tico's own rows show whether or not they hold anything yet.
  if (r.kind === 'usage') { has = n(r.entries || 0, 'entry', 'entries'); feeds = 'AI services, by bot'; how = 'Each call'; status = r.connected === false ? ['idle', '—'] : ['ok', 'OK']; }
  else if (r.kind === 'inbox') { has = n(r.bills || 0, 'bill', 'bills'); feeds = 'Payables'; how = 'By email'; status = ['ok', 'OK']; }
  else if (r.kind === 'documents') {
    const bills = 'bills' in r, inv = 'invoices' in r;
    has = [bills ? n(r.bills || 0, 'bill', 'bills') : '', inv ? n(r.invoices || 0, 'invoice', 'invoices') : ''].filter(Boolean).join(' · ');
    feeds = [bills ? 'Payables' : '', inv ? 'Receivables' : ''].filter(Boolean).join(', '); how = 'Built in'; status = ['ok', 'OK'];
  }
  else if (r.kind === 'entries') { has = n((r.manual || 0) + (r.import || 0), 'entry', 'entries'); how = 'By hand'; status = ['idle', '—']; }
  else {
    // A connection has the money accounts it feeds (as its rail lists them), else nothing to count.
    if (r.kind === 'connection') { const k = (FIN.src?.groups || []).flatMap(g => g.rows).filter(x => finSrcMoney(x) && x.sync?.connection_id === r.id).length; has = k ? n(k, 'account', 'accounts') : '—'; }
    else if (r.cards) has = n(sum(r.cards), 'card', 'cards');
    else has = finUsd(r.balance);
    feeds = r.account ? r.account.name : r.kind === 'connection' ? '' : 'Not mapped';
    how = sync ? 'Live' : r.kind === 'connection' ? '' : 'By hand';
    const at = sync?.last_ok_at || sync?.last_sync_at || r.balance_as_of;
    last = at ? ago(at) : '';
    status = sync?.status === 'disconnected' ? ['idle', 'Disconnected'] : sync?.status === 'reconnect' ? ['fail', 'Reconnect']
      : err ? ['fail', 'Error'] : !sync ? ['idle', '—']
      : sync.status === 'new' ? ['waiting', 'Starting'] : ['ok', 'OK'];
  }
  return {has, feeds, how, last, status};
}
// Every group shut (a search opens them all): the button then expands them, else it collapses them.
const finSrcAllShut = () => !FIN_SRC.q && !(FIN.src?.groups || []).some(g => FIN_SRC.open.has(g.group));
function finSrcDraw() {
  const state = FIN.src, s = FIN_SRC, box = $('#fin-src'); if (!box) return;
  // A search shows every match open, so the toggle hides until the search clears.
  const btn = $('#fin-body [data-fin-collapse]'); if (btn) { btn.hidden = !!s.q; btn.textContent = finSrcAllShut() ? 'Expand all' : 'Collapse all'; }
  if (!state.groups.length) { box.innerHTML = finNote('No sources yet.'); return; }
  const match = r => !s.q || [r.name, r.provider, r.last4, r.account?.name].some(v => String(v || '').toLowerCase().includes(s.q));
  const out = [];
  for (const g of state.groups) {
    const rows = g.rows.filter(match);
    if (s.q && !rows.length) continue;
    const open = s.open.has(g.group) || !!s.q;
    out.push(`<tr class="fin-grp"><td colspan="6"><button type="button" class="fin-link" data-fin-group="${esc(g.group)}" aria-expanded="${open}"><span class="fin-caret" aria-hidden="true">${open ? '▾' : '▸'}</span>${esc(g.name)} <span class="muted">· ${g.count}</span></button></td></tr>`);
    if (!open) continue;
    for (const r of rows) {
      const c = finSrcCols(r), k = finSrcKey(r);
      out.push(`<tr data-fin-src="${esc(k)}"${s.sel === k ? ' class="fin-on"' : ''}><td><button type="button" class="fin-link fin-src-name">${esc(r.name || r.provider || '')}${r.last4 ? ` <span class="muted">••${esc(r.last4)}</span>` : ''}</button></td>
        <td>${c.has === '—' || !c.has ? '<span class="muted">—</span>' : esc(c.has)}</td><td>${c.feeds === 'Not mapped' ? '<span class="muted">Not mapped</span>' : esc(c.feeds)}</td><td>${esc(c.how)}</td><td class="fin-nowrap">${esc(c.last)}</td>
        <td>${c.status[1] ? `<span class="pill ${c.status[0]}">${esc(c.status[1])}</span>` : ''}</td></tr>`);
    }
  }
  box.innerHTML = `<table class="fin-t fin-src-t"><thead><tr><th>Source</th><th>Has</th><th>Feeds</th><th>Sync</th><th>Last</th><th>Status</th></tr></thead><tbody>${out.join('')}</tbody></table>`;
}
function finSrcRail() {
  const state = FIN.src, s = FIN_SRC;
  const r = state.groups.flatMap(g => g.rows).find(x => finSrcKey(x) === s.sel);
  if (!r) { finRail(''); return; }
  const c = finSrcCols(r), sync = r.sync, feed = Object.entries(r.feed || {}), cards = Object.entries(r.cards || {});
  const access = r.access || [], admin = finCaps().may('finance.admin');
  const conn = r.kind === 'connection' ? r.id : sync?.connection_id || '', live = !!conn && sync?.status !== 'disconnected';
  const books = r.kind === 'connection' && !!FIN_BOOKS[r.provider];
  const stripe = r.provider === 'stripe' && (r.kind === 'connection' || r.kind === 'stripe');
  const money = finSrcMoney(r), mine = r.kind === 'connection' ? state.groups.flatMap(g => g.rows).filter(x => finSrcMoney(x) && x.sync?.connection_id === r.id) : [];
  finRail(`<div class="fin-rail-title"><h2>${esc(r.name || r.provider || '')}</h2>${c.status[1] ? `<span class="pill ${c.status[0]}">${esc(c.status[1])}</span>` : ''}</div>
    ${r.provider ? finKv('Provider', esc(r.provider)) : ''}${r.address ? finKv('Address', esc(r.address)) : ''}${r.last4 ? finKv('Account', '••' + esc(r.last4)) : ''}
    ${r.kind !== 'connection' && 'balance' in r ? finKv('Balance', finUsd(r.balance) + (r.balance_as_of ? ` <span class="muted">${esc(finDayLabel(r.balance_as_of))}</span>` : '')) : ''}
    ${r.kind !== 'connection' && r.available_balance != null && r.available_balance !== r.balance ? finKv('Available', finUsd(r.available_balance)) : ''}
    ${money ? finKv('Books as', finMapCell(r, admin)) : ''}
    ${mine.length ? `<h3 class="fin-h">Accounts</h3>${mine.map(x => finKv((x.name || '') + (x.last4 ? ' ••' + x.last4 : ''), finMapCell(x, admin))).join('')}` : ''}
    ${sync?.last_ok_at ? finKv('Last sync', esc(ago(sync.last_ok_at))) : ''}${sync?.last_error ? finKv(sync.status === 'reconnect' ? 'Reason' : 'Error', esc(FIN_RECONNECT[sync.last_error] || sync.last_error), 'fin-bad') : ''}
    ${books ? finBooksRail(r) : ''}
    ${feed.length ? `<h3 class="fin-h">Feed</h3>${feed.map(([k, v]) => finKv(k, String(v))).join('')}` : ''}
    ${cards.length ? `<h3 class="fin-h">Cards</h3>${cards.map(([k, v]) => finKv(k, String(v))).join('')}` : ''}
    ${r.payments && r.provider !== 'stripe' ? finMercuryPayments(r.payments) : ''}
    ${stripe && admin ? `<h3 class="fin-h">Webhook</h3><div data-fin-hook>${finKv('Status', '<span class="muted">…</span>')}</div>` : ''}
    ${access.length ? `<h3 class="fin-h">Access</h3>${access.map(a => finKv(finWho(a.who), esc(a.via === 'default' ? 'Default' : a.via || ''))).join('')}` : ''}
    ${admin && (live || books) ? `<div class="fin-foot">${r.kind === 'connection' && live ? '<button type="button" class="ghost" data-fin-disconnect>Disconnect</button>' : ''}${
      books && sync?.status === 'reconnect' ? '<button type="button" class="primary" data-fin-reconnect>Reconnect</button>' : live ? '<button type="button" class="primary" data-fin-sync>Sync now</button>' : ''}</div>` : ''}`);
  const rail = $('#fin-rail'); if (!rail) return;
  rail.querySelectorAll('select[data-fin-map]').forEach(sel => { sel.onchange = () => void finSrcMap(sel); });
  rail.querySelector('[data-fin-reconnect]')?.addEventListener('click', () => finConnect(r.provider));
  rail.querySelector('[data-fin-mirror]')?.addEventListener('click', ev => {
    const on = ev.currentTarget.dataset.finMirror === 'start', name = FIN_BOOKS[r.provider];
    finDialog(on ? `Use ${name} as the books` : `Stop using ${name}`, '', on ? 'Use as books' : 'Stop', async () => {
      const res = await finAct(on ? 'mirror_start' : 'mirror_stop', on ? {connection_id: r.id} : {});
      if (res.status === 'committed') toast(on ? 'Mirroring' : 'Stopped');
      void finSrcLoad();
    });
  });
  if (stripe && admin) void finStripeHook();
  rail.querySelector('[data-fin-sync]')?.addEventListener('click', async ev => {
    const btn = ev.currentTarget; btn.disabled = true;
    try { const res = await finAct('connection_sync', {connection_id: conn}); if (res.status === 'committed') toast('Syncing'); void finSrcLoad(); }
    catch (e) { toast(e.message, true); btn.disabled = false; }
  });
  rail.querySelector('[data-fin-disconnect]')?.addEventListener('click', () => {
    finDialog('Disconnect ' + (r.name || r.provider || ''), '', 'Disconnect', async () => {
      // QuickBooks and Xero go through HQ's broker (it drops its grant first); the rest are a Finance action.
      if (books) { await post(`/v2/finance/books/${encodeURIComponent(conn)}/disconnect`, {}); toast('Disconnected'); }
      else { const res = await finAct('connection_disconnect', {connection_id: conn}); if (res.status === 'committed') toast('Disconnected'); }
      void finSrcLoad();
    });
  });
}
// A money account (a bank, a card program, a payments balance): it books to one line, which a finance admin can change.
const finSrcMoney = r => 'account' in r && !['connection', 'usage', 'documents', 'entries'].includes(r.kind);
// Its line, or a select of the lines it may map to (backend/finance/banking.py connection_map: a card program to a
// liability, anything else to an asset; one money account per line).
function finMapCell(r, admin) {
  const lines = FIN.src.lines;
  if (!admin || !lines.length) return r.account ? esc(r.account.name) : '<span class="muted">Not mapped</span>';
  const want = r.kind === 'card_program' ? 'liability' : 'asset';
  const taken = new Set(FIN.src.groups.flatMap(g => g.rows).filter(x => x.id !== r.id && finSrcMoney(x) && x.account).map(x => x.account.id));
  const opts = lines.filter(l => l.type === want && (!taken.has(l.account_id) || l.account_id === r.account?.id));
  return `<select data-fin-map="${esc(r.id)}" aria-label="${esc(r.name || '')} books as">${r.account ? '' : '<option value="" selected>Not mapped</option>'}${opts.map(l =>
    `<option value="${esc(l.account_id)}"${l.account_id === r.account?.id ? ' selected' : ''}>${esc(l.name)}</option>`).join('')}</select>`;
}
async function finSrcMap(sel) {
  const r = FIN.src.groups.flatMap(g => g.rows).find(x => x.id === sel.dataset.finMap && finSrcMoney(x));
  if (!r || !sel.value || sel.value === r.account?.id) return;
  sel.disabled = true;
  try {
    const res = await finAct('connection_map', {money_account_id: r.id, account_id: sel.value});
    if (res.status === 'committed') toast('Saved');
    void finSrcLoad();
  } catch (e) { toast(e.message, true); sel.disabled = false; sel.value = r.account?.id || ''; }
}
// Connect Mercury: the token goes into the credential vault, then the connection is made from the stored credential
// (POST /api/v2/finance/connections tests it and maps the accounts). The token is never read back. A retry after a
// failed connect reuses the stored credential (updating its value if the token was changed), so one try stores one.
function finConnectMercury() {
  let cred = null, stored = '';
  finDialog('Connect Mercury', `<label>API token<input name="token" type="password" required autocomplete="off" spellcheck="false"></label>
    <label>Approver email<input name="approver" type="email" autocomplete="off" spellcheck="false"></label>`, 'Connect', async form => {
    const token = form.elements.token.value.trim();
    if (!token) throw new Error('Paste the token');
    if (!cred) {
      cred = await post('/v2/credentials', {name: 'Mercury', kind: 'token', secret: token, username: '', env: '', source: 'finance'});
      if (!cred?.id) { cred = null; throw new Error('The token was not stored'); }
      stored = token;
    } else if (token !== stored) {
      cred = await post('/v2/credentials/' + encodeURIComponent(cred.id), {name: 'Mercury', kind: 'token', secret: token, username: '', env: '',
        source: 'finance', ...(cred.revision ? {expected_revision: cred.revision} : {})}) || cred;
      stored = token;
    }
    const body = {credential_id: cred.id, name: 'Mercury'};
    if (form.elements.approver.value.trim()) body.approver_email = form.elements.approver.value.trim();
    const r = await post('/v2/finance/connections', body);
    if (r?.status === 'needs_approval') toast(r.approver ? `Sent to ${finWho(r.approver)} for approval` : 'Sent for approval');
    else if (r?.status !== 'committed') throw new Error(r?.reason || 'Not connected');
    else toast('Mercury connected');
    form.elements.token.value = '';
    if (FIN?.src) void finSrcLoad();
    if (FIN?.bs) void finBsLoad();
  });
}

// "+ Source": a bank or card account (Mercury), Stripe, or the company's books (QuickBooks, Xero). Finance admins only.
function finSourceMenuHtml(caps) {
  if (!caps.may('finance.admin')) return '';
  const items = [['mercury', 'Mercury'], ['stripe', 'Stripe'], ['quickbooks', 'QuickBooks'], ['xero', 'Xero']];
  return `<span class="fin-pop-at"><button type="button" class="primary" data-fin-connect aria-haspopup="menu" aria-expanded="false" aria-controls="fin-connect">+ Source</button>
    <div class="fin-pop fin-menu" id="fin-connect" role="menu" aria-label="Connect" hidden>${items.map(([v, l]) =>
      `<button type="button" role="menuitem" data-fin-connect-item="${v}">${l}</button>`).join('')}</div></span>`;
}
function finConnect(v) {
  if (v === 'mercury') finConnectMercury();
  else if (v === 'stripe') finConnectStripe();
  else if (FIN_BOOKS[v]) finConnectBooks(v);
}
// Connect Stripe (revenue): a restricted key into the credential vault, then the connection from it, as with Mercury.
function finConnectStripe() {
  let cred = null, stored = '';
  finDialog('Connect Stripe', `<label>Restricted key<input name="token" type="password" required autocomplete="off" spellcheck="false" placeholder="rk_live_…"></label>`, 'Connect', async form => {
    const token = form.elements.token.value.trim();
    if (!token) throw new Error('Paste the key');
    if (!cred) {
      cred = await post('/v2/credentials', {name: 'Stripe', kind: 'token', secret: token, username: '', env: '', source: 'finance'});
      if (!cred?.id) { cred = null; throw new Error('The key was not stored'); }
      stored = token;
    } else if (token !== stored) {
      cred = await post('/v2/credentials/' + encodeURIComponent(cred.id), {name: 'Stripe', kind: 'token', secret: token, username: '', env: '',
        source: 'finance', ...(cred.revision ? {expected_revision: cred.revision} : {})}) || cred;
      stored = token;
    }
    const r = await post('/v2/finance/connections', {provider: 'stripe', credential_id: cred.id, name: 'Stripe'});
    if (r?.status === 'needs_approval') toast(r.approver ? `Sent to ${finWho(r.approver)} for approval` : 'Sent for approval');
    else if (r?.status !== 'committed') throw new Error(r?.reason || 'Not connected');
    else toast('Stripe connected');
    form.elements.token.value = '';
    if (FIN?.src) void finSrcLoad();
    if (FIN?.bs) void finBsLoad();
  });
}
// QuickBooks or Xero: POST /api/v2/finance/books/connect {provider} -> {url, expires_in}, HQ's consent page. The server
// takes the browser's return (GET /api/v2/finance/books/return/{flow}) and sends it to #/finance?books=connected or
// ?books=error&reason=<code> (finBooksReturn in finance.js).
function finConnectBooks(provider) {
  const name = FIN_BOOKS[provider];
  finDialog('Connect ' + name, '', 'Continue', async () => {
    const r = await post('/v2/finance/books/connect', {provider});
    if (!String(r?.url || '').startsWith('https://')) throw new Error(name + ' did not answer');
    location.assign(r.url);
    return false;
  });
}
// Why a books connection needs a person (backend/finance/books_link.py): its codes, in words.
const FIN_RECONNECT = {reconnect_required: 'Sign-in expired', vault_pending: 'Saving sign-in', vault_write_failed: 'Sign-in not saved'};
const FIN_MIRROR = {on: 'On', backfill: 'Backfilling', backfilling: 'Backfilling', off: 'Off', stopped: 'Off'};
const FIN_OUTBOX = [['pending', 'Waiting'], ['sent', 'Sent'], ['failed', 'Failed'], ['withheld', 'Withheld']];
// A books connection: whether it is the books (mirror), what waits to be written to it, and the owner's switch.
function finBooksRail(r) {
  const state = typeof r.mirror === 'string' ? r.mirror : r.mirror?.state || '';
  const out = r.outbox || {}, rows = FIN_OUTBOX.filter(([k]) => out[k]);
  const live = r.sync?.status === 'active' || r.sync?.status === 'new';
  const btn = finCaps().may('finance.book') && live
    ? `<div class="fin-foot"><button type="button" class="ghost" data-fin-mirror="${state === 'on' || state.startsWith('backfill') ? 'stop">Stop mirror' : 'start">Use as books'}</button></div>` : '';
  return (state ? finKv('Mirror', esc(FIN_MIRROR[state] || state)) : '') + (r.mirror?.last_at ? finKv('Last change', esc(ago(r.mirror.last_at))) : '')
    + (rows.length ? `<h3 class="fin-h">Outbox</h3>${rows.map(([k, l]) => finKv(l, Number(out[k]).toLocaleString('en-US'), k === 'failed' ? 'fin-bad' : '')).join('')}` : '') + btn;
}
// Mercury's payments: money out waits in Mercury's own approval queue (spec §9), unless the owner turned on direct send.
function finMercuryPayments(p) {
  return `<h3 class="fin-h">Payments</h3>${finKv('Payments', p.enabled ? 'On' : 'Off')}${finKv('Approve in', p.eligible ? 'Mercury' : '<span class="fin-bad">No approver</span>')}${
    finKv('Direct send', p.direct_send ? 'On' : 'Off')}${p.why_not ? finKv('Note', esc(p.why_not)) : ''}`;
}
// Stripe's webhook is a Finance trigger (preset stripe_finance, backend/trigger_presets.py): Settings hides it, so it is
// made here. The address and the signing secret show once.
const finIsFinTrigger = t => t.consumer === 'finance' || ['mercury', 'stripe_finance', 'intuit', 'xero'].includes(t.preset);
async function finStripeHook() {
  const box = $('#fin-rail [data-fin-hook]'); if (!box) return;
  let list = [];
  try { list = (await get('/v2/triggers', {fresh: true})).triggers || []; } catch { box.innerHTML = ''; return; }
  if (!box.isConnected) return;
  const t = list.find(x => x.preset === 'stripe_finance');
  box.innerHTML = t ? finKv('Last call', t.last_received ? esc(ago(t.last_received)) : '<span class="muted">Never</span>')
      + (t.refused_24h ? finKv('Refused', String(t.refused_24h), 'fin-bad') : '')
    : '<div class="fin-foot"><button type="button" class="ghost" data-fin-hook-add>Add webhook</button></div>';
  box.querySelector('[data-fin-hook-add]')?.addEventListener('click', finStripeHookAdd);
}
function finStripeHookAdd() {
  let made = null;
  finDialog('Stripe webhook', `<label>Signing secret<input name="secret" maxlength="300" autocomplete="off" spellcheck="false" placeholder="Leave empty to make one"></label>`, 'Add', async form => {
    if (!made) made = (await post('/v2/triggers', {name: 'Stripe (Finance)'})).trigger;
    const secret = form.elements.secret.value.trim();
    const signed = (await post(`/v2/triggers/${encodeURIComponent(made.id)}/signing`, {preset: 'stripe_finance', ...(secret ? {secret} : {})})).trigger;
    const shown = [made.replayed ? null : ['Address', made.url], secret || signed.replayed ? null : ['Signing secret', signed.signing_secret]].filter(x => x?.[1]);
    form.elements.secret.value = '';
    setTimeout(() => finShowOnce('Stripe webhook', shown), 0);
    if (FIN?.src) void finStripeHook();
  });
}
// Values a person copies now and never sees again (an address with its secret, a signing secret).
function finShowOnce(title, pairs) {
  if (!pairs.length) return;
  const dlg = finDialog(title, pairs.map(([k, v], i) => `<div class="trigger-secret" role="status"><p><strong>${esc(k)}</strong> <span class="muted">Shown once</span></p>
    <div class="row"><code class="trigger-url">${esc(v)}</code><button class="ghost" type="button" data-fin-copy="${i}">Copy</button></div></div>`).join(''), 'Done', () => {});
  dlg.querySelector('.fin-foot [data-close]')?.remove();
  dlg.querySelectorAll('[data-fin-copy]').forEach(b => { b.onclick = async () => { await copyText(pairs[Number(b.dataset.finCopy)][1]); toast('Copied'); }; });
}

// ----------------------------------------------------------------- access
const FIN_WHAT = {everything: 'Everything', pnl: 'P&L', expenses: 'Expenses', pnl_lines: 'P&L line', balance_sheet: 'Balance sheet',
  bs_lines: 'Balance sheet line', payables: 'Payables', receivables: 'Receivables', sources: 'Sources'};
const FIN_ACC = {sel: ''};
// What a default reads as for this teammate. A manager's covers the teams they lead and everyone who reports to them;
// the Assistant acts as whoever asks it, so it sees what they see.
const finAsker = row => row.teammate === 'bot:' + assistantBot();
function finDefaultLabel(row) {
  if (finAsker(row)) return 'Same as who asks';
  // backend/finance/tab.py default_team: the one top team a manager's default covers, else none (several, or reports only).
  if (row.default === 'team_expenses' && row.default_team) return `${finTeamName(row.default_team)} expenses`;
  return {everything: 'Everything', none: 'Nothing', own_expenses: 'Own expenses', team_expenses: 'Team expenses'}[row.default] || 'Own expenses';
}
const finDefaultCls = row => finAsker(row) ? 'own' : {everything: 'all', team_expenses: 'mgr', own_expenses: 'own', none: 'none'}[row.default] || 'own';
function finLineNames(ids) {
  const rows = [...(FIN_PNL.last?.rows || []), ...(FIN.accLines?.pnl || []), ...(FIN.accLines?.bs || [])];
  return (ids || []).map(id => rows.find(r => r.account_id === id)?.name || 'A line').join(', ');
}
function finGrantLabel(g) {
  const whose = g.whose === 'own' ? 'own' : g.whose === 'team' ? finTeamName(g.whose_team) : 'company';
  if (g.what === 'expenses') return g.whose === 'own' ? 'Own expenses' : g.whose === 'team' ? `${finTeamName(g.whose_team)} expenses` : 'Company expenses';
  if (g.what === 'pnl_lines' || g.what === 'bs_lines') return `${finLineNames(g.account_ids)} · ${whose}`;
  if (g.what === 'everything' || g.whose === 'company' || g.what.startsWith('bs') || g.what === 'balance_sheet') return FIN_WHAT[g.what] || g.what;
  return `${FIN_WHAT[g.what] || g.what} · ${whose}`;
}
// The rows of the Access table in order: the owner, then each team (org chart order) with its members and the teams
// under it, then teammates on no team, then the helpers. {group, d} heads a group; {t, d} is a teammate.
function finAccRows(acc) {
  const teams = acc.teams || [], all = acc.teammates || [];
  const owner = all.filter(t => t.default === 'everything');
  const helpers = all.filter(t => !owner.includes(t) && finHelper(t.teammate));
  const mates = all.filter(t => !owner.includes(t) && !helpers.includes(t));
  const out = owner.map(t => ({t, d: 0}));
  const walk = (team, d) => {
    out.push({group: team.name, d});
    for (const m of mates.filter(m => m.team === team.id).sort((a, b) => (b.teammate === team.lead) - (a.teammate === team.lead))) out.push({t: m, d});
    for (const k of finTeamKids(teams, team.id)) if (d < 12) walk(k, d + 1);
  };
  for (const team of finTeamKids(teams, '')) walk(team, 0);
  const loose = mates.filter(m => !m.team || !teams.some(x => x.id === m.team));
  if (loose.length) out.push({group: 'No team', d: 0}, ...loose.map(t => ({t, d: 0})));
  if (helpers.length) out.push({group: 'Helpers', d: 0}, ...helpers.map(t => ({t, d: 0})));
  return out;
}
function finAccessPage() {
  const acc = FIN.acc;
  $('#fin-body').innerHTML = `<div class="fin-bar">${finApproverHtml(acc)}<span class="spacer"></span>${acc.can_edit_defaults ? '<button type="button" class="ghost" data-fin-defaults>Edit defaults</button>' : ''}
      ${acc.can_view_as ? '<button type="button" class="ghost" data-fin-viewas>View as…</button>' : ''}</div>`
    + finSplit(`<h2 class="fin-h2">Org chart</h2><div class="fin-scroll" id="fin-acc"></div><div id="fin-private"></div>`, '', 'Selected teammate');
  if (!acc.teammates.some(t => t.teammate === FIN_ACC.sel)) FIN_ACC.sel = finAccRows(acc).find(r => r.t)?.t.teammate || '';
  $('#fin-body').onclick = ev => {
    if (ev.target.closest('[data-fin-defaults]')) { finDefaultsEdit(); return; }
    if (ev.target.closest('[data-fin-viewas]')) { finViewAsPick(); return; }
    const revoke = ev.target.closest('[data-fin-revoke]');
    if (revoke) { void finRevoke(revoke); return; }
    const tr = ev.target.closest('tr[data-fin-mate]');
    if (tr) { FIN_ACC.sel = tr.dataset.finMate; finAccDraw(); finAccRail(); }
  };
  const pick = $('#fin-body [data-fin-approver]');
  if (pick) pick.onchange = () => void finApproverSet(pick);
  finAccDraw(); finAccRail();
}
// The one person finance-admin approvals go to (§16): the owner's to name, the owner by default.
function finApproverHtml(acc) {
  if (!acc.can_edit_defaults) return '';
  const people = (acc.teammates || []).filter(t => t.kind === 'person'), owner = people.find(t => t.default === 'everything');
  const opt = (v, l) => `<option value="${esc(v)}"${v === (acc.finance_approver || '') ? ' selected' : ''}>${esc(l)}</option>`;
  return `<label class="fin-inline">Finance approver<select data-fin-approver>${opt('', owner ? finWho(owner.teammate) : 'Owner')}${people.filter(t => t !== owner).map(t => opt(t.teammate, finWho(t.teammate))).join('')}</select></label>`;
}
async function finApproverSet(pick) {
  pick.disabled = true;
  try {
    const r = await finAct('finance_approver_set', {approver: pick.value});
    if (r.status === 'committed') toast('Saved');
  } catch (e) { toast(e.message, true); }
  await finAccReload();
  pick.value = FIN.acc.finance_approver || '';
  pick.disabled = false;
}
function finAccDraw() {
  const acc = FIN.acc, box = $('#fin-acc'); if (!box) return;
  const role = t => t.default === 'everything' ? 'Owner' : t.default === 'team_expenses' && !finAsker(t) ? 'Manager' : '';
  const grants = t => (t.grants || []).map(g => `<span class="fin-chip fin-grant${g.status !== 'active' ? ' muted' : ''}">${esc(finGrantLabel(g))}${g.status !== 'active' ? ' · suspended' : ''}</span>${g.granted_by
    ? `<span class="muted fin-small">by ${esc(finWho(g.granted_by))}</span>` : ''}`).join(' ');
  const row = (t, d) => `<tr data-fin-mate="${esc(t.teammate)}"${FIN_ACC.sel === t.teammate ? ' class="fin-on"' : ''} style="--d:${d}"><td><span class="fin-who fin-ind">${finAvatar(t.teammate, 20)}<button type="button" class="fin-link">${esc(finWho(t.teammate))}</button>${role(t) ? `<span class="muted fin-small">${role(t)}</span>` : ''}</span></td>
    <td><span class="fin-s ${finDefaultCls(t)}">${esc(finDefaultLabel(t))}</span></td><td>${grants(t) || '<span class="muted">—</span>'}</td></tr>`;
  const out = finAccRows(acc).map(r => r.t ? row(r.t, r.d) : `<tr class="fin-grp" style="--d:${r.d}"><td colspan="3"><span class="fin-ind">${esc(r.group)}</span></td></tr>`);
  box.innerHTML = `<table class="fin-t fin-acc-t"><thead><tr><th>Teammate</th><th>By default</th><th>Granted</th></tr></thead><tbody>${out.join('')}</tbody></table>`;
  // backend/finance/tab.py access: others_see totals|hidden; full_detail the teammates who see it all (owners only).
  // Payroll by person is private by default (a payroll line is made private when it comes in), so that default shows
  // even before there is one: its totals follow the managers' payroll default.
  const priv = acc.private_lines || [], d = acc.defaults || {};
  const chip = (name, hidden, full) => `<span class="fin-chip">${esc(name)} <span class="muted">· ${hidden ? 'Hidden' : 'Totals only'}${full.length ? ' · Full detail: ' + esc(full.map(finWho).join(', ')) : ''}</span></span>`;
  // Only whoever sees everything reads them (backend/finance/tab.py access); finance.admin adds no view (§7.5).
  $('#fin-private').innerHTML = finCaps().all ? `<h2 class="fin-h2">Private lines</h2><div class="fin-private">${chip('Payroll by person', d.manager_payroll === 'hidden', [])}${priv.map(p =>
    chip(p.name, p.others_see === 'hidden', Array.isArray(p.full_detail) ? p.full_detail : [])).join('')}</div>` : '';
}
function finAccRail() {
  const acc = FIN.acc, me = finMe(), t = (acc.teammates || []).find(x => x.teammate === FIN_ACC.sel);
  if (!t) { finRail(''); return; }
  const teams = acc.teams || [];
  const path = []; for (let id = t.team, n = 0; id && n < 12; n++) { const team = teams.find(x => x.id === id); if (!team) break; path.unshift(team.name); id = team.parent; }
  const owner = !!acc.can_view_as;
  const grants = (t.grants || []).map((g, i) => {
    const mine = owner || g.granted_by === me;
    return `<div class="fin-kv"><span data-fin-glabel="${i}">${esc(finGrantLabel(g))}</span><span class="muted fin-small">${g.granted_by ? esc(finWho(g.granted_by)) : ''}${g.expires_at ? ' · until ' + esc(finDayLabel(g.expires_at)) : ' · no end'}${mine
      ? ` <button type="button" class="ghost fin-x" data-fin-revoke="${esc(g.id)}" aria-label="Revoke ${esc(finGrantLabel(g))}">✕</button>` : ''}</span></div>`;
  }).join('');
  // The server says where a grant would go through (may_grant, from check_grant); the Whose list offers the teams
  // this viewer leads.
  const leads = new Set(teams.filter(x => x.lead === me).flatMap(x => [...finTeamsBelow(x.id)]));
  const may = !!t.may_grant;
  const named = (t.grants || []).some(g => (g.account_ids || []).length);
  const L = may || named ? finAccLines() : null;
  // Line names come with the lines read; relabel the grants then (the form keeps what was typed).
  if (L && !L.ready) L.wait.then(() => {
    if (FIN_ACC.sel !== t.teammate) return;
    (t.grants || []).forEach((g, i) => { const el = $(`#fin-rail [data-fin-glabel="${i}"]`); if (el) el.textContent = finGrantLabel(g); });
  });
  const whats = owner ? Object.entries(FIN_WHAT) : [['expenses', FIN_WHAT.expenses], ['pnl_lines', FIN_WHAT.pnl_lines]];
  finRail(`<div class="fin-rail-title">${finAvatar(t.teammate, 28)}<h2>${esc(finWho(t.teammate))}</h2></div>
    ${path.length ? finKv('Team', esc(path.join(' › '))) : ''}${finKv('Kind', t.kind === 'bot' ? 'Bot' : 'Person')}${t.reports_to ? finKv('Reports to', esc(finWho(t.reports_to))) : ''}
    <h3 class="fin-h">Sees</h3>${finKv(finDefaultLabel(t), '<span class="muted fin-small">Default</span>')}${grants}
    ${may ? `<h3 class="fin-h">Grant more</h3><form class="fin-form fin-rail-form" data-fin-grant>
      <label>What<select name="what">${whats.map(([v, l]) => `<option value="${v}"${v === 'expenses' ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
      <label data-fin-line hidden>Line<select name="line"></select></label>
      <label data-fin-whose>Whose<select name="whose"><option value="own">Their own</option>${teams.filter(x => owner || leads.has(x.id)).map(x => `<option value="team:${esc(x.id)}"${x.id === t.team ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}${owner ? '<option value="company">Company</option>' : ''}</select></label>
      <label>Until<input type="date" name="until" min="${finToday()}"></label>
      <div class="fin-foot"><button type="submit" class="primary">Grant</button></div></form>` : ''}`);
  const form = $('#fin-rail [data-fin-grant]'); if (!form) return;
  const sync = () => {
    const what = form.elements.what.value, line = form.querySelector('[data-fin-line]');
    // A manager names expense lines only (core/view.py: their pnl_lines grants are expense lines).
    const pnl = (L.pnl || []).filter(r => owner || r.type === 'expense');
    const opts = what === 'pnl_lines' ? pnl : what === 'bs_lines' ? L.bs || [] : [];
    const keep = form.elements.line.value;
    line.hidden = !['pnl_lines', 'bs_lines'].includes(what);
    form.elements.line.innerHTML = opts.map(r => `<option value="${esc(r.account_id)}"${r.account_id === keep ? ' selected' : ''}>${esc(r.name)}</option>`).join('');
    // Everything, the balance sheet and its lines, payables, receivables and sources are company-wide.
    form.querySelector('[data-fin-whose]').hidden = !['expenses', 'pnl', 'pnl_lines'].includes(what);
  };
  form.elements.what.onchange = sync; sync();
  if (!L.ready) L.wait.then(() => { if (form.isConnected) sync(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const what = form.elements.what.value, whose = form.elements.whose.value, scoped = !form.querySelector('[data-fin-whose]').hidden;
    const payload = {subject_kind: 'teammate', subject: t.teammate, what};
    if (scoped) { payload.whose = whose.startsWith('team:') ? 'team' : whose; if (whose.startsWith('team:')) payload.whose_team = whose.slice(5); }
    if (what === 'pnl_lines' || what === 'bs_lines') {
      if (!form.elements.line.value) { toast('Pick a line', true); return; }
      payload.account_ids = [form.elements.line.value];
    }
    if (form.elements.until.value) payload.expires_at = form.elements.until.value + 'T23:59:59Z';
    const btn = form.querySelector('[type=submit]'); btn.disabled = true;
    try {
      const r = await finAct('view_grant', payload);
      if (r.status === 'committed') toast('Granted');
      await finAccReload();
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  };
}
// Read once per visit to Access: the P&L lines (company view for whoever sees it) and the balance sheet's lines.
function finAccLines() {
  if (FIN.accLines) return FIN.accLines;
  const caps = finCaps(), [a, z] = finPnlRange('month');
  const L = FIN.accLines = {pnl: null, bs: null, ready: false, wait: null};
  L.wait = Promise.allSettled([get(`/v2/finance/pnl?start=${a}&end=${z}&period=month&basis=cash&view=${caps.company ? 'company' : 'expenses'}`, {fresh: true}),
    caps.balance ? get('/v2/finance/balance-sheet', {fresh: true}) : Promise.resolve(null)]).then(([p, b]) => {
    L.pnl = p.status === 'fulfilled' ? (p.value?.rows || []).filter(r => !r.private).sort((x, y) => String(x.code).localeCompare(String(y.code))) : [];
    const sheet = b.status === 'fulfilled' ? b.value : null;
    L.bs = sheet ? ['assets', 'liabilities', 'equity'].flatMap(k => sheet[k] || []).filter(r => r.account_id) : [];
    L.ready = true;
  });
  return L;
}
async function finAccReload() {
  try { FIN.acc = FIN_ACCESS = await get('/v2/finance/access', {fresh: true}); } catch { /* keep what is drawn */ }
  if ($('#fin-acc')) { finAccDraw(); finAccRail(); }
}
async function finRevoke(btn) {
  btn.disabled = true;
  try {
    const r = await finAct('view_revoke', {grant_id: btn.dataset.finRevoke});
    if (r.status === 'committed') toast('Revoked');
    await finAccReload();
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}
function finViewAsPick() {
  const acc = FIN.acc, me = finMe();
  finDialog('View as', `<label>Teammate<select name="who">${acc.teammates.filter(t => t.teammate !== me).map(t =>
    `<option value="${esc(t.teammate)}">${esc(finWho(t.teammate))}</option>`).join('')}</select></label>`, 'View', async form => {
    finViewAs(form.elements.who.value);
  });
}
// The company's defaults (§7.5): owners see everything; managers and everyone else as set here; revenue, the
// balance sheet, payables and receivables stay the owners' unless granted.
function finDefaultsEdit() {
  const d = FIN.acc.defaults || {}, opt = (v, l, cur) => `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`;
  finDialog('Edit defaults', `<label>Owners<select disabled><option>Everything</option></select></label>
    <label>Managers<select name="manager">${opt('team_expenses', 'Expenses of what they manage', d.manager)}${opt('own_expenses', 'Own expenses', d.manager)}</select></label>
    <label>Managers' payroll<select name="manager_payroll">${opt('totals', 'Totals', d.manager_payroll)}${opt('hidden', 'Hidden', d.manager_payroll)}</select></label>
    <label>Everyone else<select name="member">${opt('own_expenses', 'Own expenses', d.member)}${opt('none', 'Nothing', d.member)}</select></label>
    <label>Revenue, balance sheet, payables, receivables<select disabled><option>Owners only</option></select></label>`, 'Save', async form => {
    const pick = Object.fromEntries(['manager', 'member', 'manager_payroll'].map(k => [k, form.elements[k].value]).filter(([k, v]) => v && v !== d[k]));
    if (!Object.keys(pick).length) return;
    const r = await finAct('view_defaults_set', pick);
    if (r.status === 'committed') toast('Saved');
    await finAccReload();
  });
}
