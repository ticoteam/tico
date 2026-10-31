/* ui/app/finance-admin.js — Finance › Sources and Access (spec §12, §7.5).
   Sources: every source of the books, grouped (banks, cards, payments, payroll, bills and invoices, Tico, accounting,
   manual), collapsed by default; a source opens its account mapping, sync and who sees it. "+ Source" connects
   Mercury: the token is stored in the credential vault and the connection made from it (the token never comes back).
   Access: who sees what in Finance, on the org chart (each teammate's default and grants), private lines, the owner's
   "View as", Edit defaults, and a teammate's "Grant more".
   Reads: GET /api/v2/finance/sources, /finance/access. Writes: POST /api/v2/credentials, /finance/connections;
   view_grant, view_revoke (and the defaults) through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- sources
const FIN_SRC = {open: new Set(), q: '', sel: ''};
function finSourcesPage() {
  const caps = finCaps(), s = FIN_SRC;
  FIN.src = {groups: [], load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-fin-q value="${esc(s.q)}" placeholder="Search sources, accounts, cards" aria-label="Search sources"></label>
      <button type="button" class="ghost" data-fin-collapse>Collapse all</button><span class="spacer"></span>
      ${caps.may('finance.admin') ? '<button type="button" class="primary" data-fin-connect>+ Source</button>' : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-src">${skelRows(8, {dot: false})}</div>`, '', 'Selected source');
  const body = $('#fin-body');
  body.querySelector('[data-fin-q]').oninput = ev => { s.q = ev.target.value.trim().toLowerCase(); finSrcDraw(); };
  body.onclick = ev => {
    if (ev.target.closest('[data-fin-connect]')) { finConnectMercury(); return; }
    if (ev.target.closest('[data-fin-collapse]')) { s.open.clear(); finSrcDraw(); return; }
    const g = ev.target.closest('[data-fin-group]');
    if (g) { const k = g.dataset.finGroup; if (s.open.has(k)) s.open.delete(k); else s.open.add(k); finSrcDraw(); return; }
    const tr = ev.target.closest('tr[data-fin-src]');
    if (tr) { s.sel = tr.dataset.finSrc; finSrcDraw(); finSrcRail(); }
  };
  void finSrcLoad();
}
async function finSrcLoad() {
  const state = FIN.src, load = ++state.load;
  let d;
  try { d = await get('/v2/finance/sources' + finAs('?'), {fresh: true}); }
  catch (e) { if (FIN?.src === state && $('#fin-src')) $('#fin-src').innerHTML = finFail(e); return; }
  if (FIN?.src !== state || state.load !== load || !$('#fin-src')) return;
  state.groups = (d.groups || []).filter(g => g.count > 0);
  finSrcDraw(); finSrcRail();
}
const finSrcKey = r => `${r.kind}:${r.id}`;
// What a source holds, how it is booked, how often it syncs, when it last did, and whether it is well.
function finSrcCols(r) {
  const n = (v, one, many) => `${v.toLocaleString('en-US')} ${v === 1 ? one : many}`;
  const sum = o => Object.values(o || {}).reduce((a, b) => a + (Number(b) || 0), 0);
  const sync = r.sync, err = sync?.last_error;
  let has = '', feeds = '', how = '', last = '', status = ['', ''];
  if (r.kind === 'usage') { has = n(r.entries || 0, 'entry', 'entries'); feeds = 'AI services, by bot'; how = 'Each call'; status = ['ok', 'OK']; }
  else if (r.kind === 'documents') { has = [r.bills ? n(r.bills, 'bill', 'bills') : '', r.invoices ? n(r.invoices, 'invoice', 'invoices') : ''].filter(Boolean).join(' · '); feeds = 'Payables, Receivables'; how = 'Built in'; status = ['ok', 'OK']; }
  else if (r.kind === 'entries') { has = n((r.manual || 0) + (r.import || 0), 'entry', 'entries'); how = 'By hand'; status = ['idle', '—']; }
  else {
    if (r.kind === 'connection') has = r.provider || '';
    else if (r.cards) has = n(sum(r.cards), 'card', 'cards');
    else has = finUsd(r.balance);
    feeds = r.account ? r.account.name : r.kind === 'connection' ? '' : 'Not mapped';
    how = sync ? 'Live' : r.kind === 'connection' ? '' : 'By hand';
    const at = sync?.last_ok_at || sync?.last_sync_at || r.balance_as_of;
    last = at ? ago(at) : '';
    status = err ? ['fail', 'Error'] : !sync ? ['idle', '—'] : sync.status === 'new' ? ['waiting', 'Starting'] : ['ok', 'OK'];
  }
  return {has, feeds, how, last, status};
}
function finSrcDraw() {
  const state = FIN.src, s = FIN_SRC, box = $('#fin-src'); if (!box) return;
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
        <td>${esc(c.has)}</td><td>${c.feeds === 'Not mapped' ? '<span class="muted">Not mapped</span>' : esc(c.feeds)}</td><td>${esc(c.how)}</td><td class="fin-nowrap">${esc(c.last)}</td>
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
  const access = r.access || [];
  finRail(`<div class="fin-rail-title"><h2>${esc(r.name || r.provider || '')}</h2>${c.status[1] ? `<span class="pill ${c.status[0]}">${esc(c.status[1])}</span>` : ''}</div>
    ${r.provider ? finKv('Provider', esc(r.provider)) : ''}${r.last4 ? finKv('Account', '••' + esc(r.last4)) : ''}
    ${r.kind !== 'connection' && 'balance' in r ? finKv('Balance', finUsd(r.balance) + (r.balance_as_of ? ` <span class="muted">${esc(finDayLabel(r.balance_as_of))}</span>` : '')) : ''}
    ${'account' in r ? finKv('Books as', r.account ? esc(r.account.name) : '<span class="muted">Not mapped</span>') : ''}
    ${sync?.last_ok_at ? finKv('Last sync', esc(ago(sync.last_ok_at))) : ''}${sync?.last_error ? finKv('Error', esc(sync.last_error), 'fin-bad') : ''}
    ${feed.length ? `<h3 class="fin-h">Feed</h3>${feed.map(([k, v]) => finKv(k, String(v))).join('')}` : ''}
    ${cards.length ? `<h3 class="fin-h">Cards</h3>${cards.map(([k, v]) => finKv(k, String(v))).join('')}` : ''}
    ${r.payments ? `<h3 class="fin-h">Payments</h3>${finKv('Approval queue', r.payments.eligible ? 'Eligible' : 'Not eligible')}${finKv('Payments', r.payments.enabled ? 'On' : 'Off')}${r.payments.why_not ? finKv('Note', esc(r.payments.why_not)) : ''}` : ''}
    ${access.length ? `<h3 class="fin-h">Access</h3>${access.map(a => finKv(finWho(a.who), esc(a.via === 'default' ? 'Default' : a.via || ''))).join('')}` : ''}`);
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

// ----------------------------------------------------------------- access
const FIN_WHAT = {everything: 'Everything', pnl: 'P&L', expenses: 'Expenses', pnl_lines: 'P&L line', balance_sheet: 'Balance sheet',
  bs_lines: 'Balance sheet line', payables: 'Payables', receivables: 'Receivables', sources: 'Sources'};
const FIN_ACC = {sel: ''};
// What a default reads as for this teammate: the teams they manage, their own, or nothing.
function finDefaultLabel(row, acc) {
  if (row.default === 'everything') return 'Everything';
  if (row.default === 'none') return 'Nothing';
  if (row.default === 'own_expenses') return 'Own expenses';
  const mine = (acc.teams || []).filter(t => t.lead === row.teammate), ids = new Set(mine.map(t => t.id));
  const led = mine.filter(t => !ids.has(t.parent)).map(t => t.name);   // a team under one they lead is in it already
  return (led.length ? led.join(', ') : 'Team') + ' expenses';
}
const finDefaultCls = d => ({everything: 'all', team_expenses: 'mgr', own_expenses: 'own', none: 'none'}[d] || 'own');
function finLineNames(ids) {
  const rows = [...(FIN_PNL.last?.rows || []), ...['assets', 'liabilities', 'equity'].flatMap(k => FIN.bs?.data?.[k] || [])];
  return (ids || []).map(id => rows.find(r => r.account_id === id)?.name || 'A line').join(', ');
}
function finGrantLabel(g) {
  const whose = g.whose === 'own' ? 'own' : g.whose === 'team' ? finTeamName(g.whose_team) : 'company';
  if (g.what === 'expenses') return g.whose === 'own' ? 'Own expenses' : g.whose === 'team' ? `${finTeamName(g.whose_team)} expenses` : 'Company expenses';
  if (g.what === 'pnl_lines' || g.what === 'bs_lines') return `${finLineNames(g.account_ids)} · ${whose}`;
  if (g.what === 'everything' || g.whose === 'company' || g.what.startsWith('bs') || g.what === 'balance_sheet') return FIN_WHAT[g.what] || g.what;
  return `${FIN_WHAT[g.what] || g.what} · ${whose}`;
}
const finLeads = (acc, who) => (acc.teams || []).some(t => t.lead === who);
function finAccessPage() {
  const acc = FIN.acc, caps = finCaps();
  $('#fin-body').innerHTML = `<div class="fin-bar"><span class="spacer"></span>${acc.can_edit_defaults ? '<button type="button" class="ghost" data-fin-defaults>Edit defaults</button>' : ''}
      ${acc.can_view_as ? '<button type="button" class="ghost" data-fin-viewas>View as…</button>' : ''}</div>`
    + finSplit(`<h2 class="fin-h2">Org chart</h2><div class="fin-scroll" id="fin-acc"></div><div id="fin-private"></div>`, '', 'Selected teammate');
  if (!acc.teammates.some(t => t.teammate === FIN_ACC.sel)) FIN_ACC.sel = (acc.teammates.find(t => t.teammate !== caps.who) || acc.teammates[0])?.teammate || '';
  $('#fin-body').onclick = ev => {
    if (ev.target.closest('[data-fin-defaults]')) { finDefaultsEdit(); return; }
    if (ev.target.closest('[data-fin-viewas]')) { finViewAsPick(); return; }
    const revoke = ev.target.closest('[data-fin-revoke]');
    if (revoke) { void finRevoke(revoke); return; }
    const tr = ev.target.closest('tr[data-fin-mate]');
    if (tr) { FIN_ACC.sel = tr.dataset.finMate; finAccDraw(); finAccRail(); }
  };
  finAccDraw(); finAccRail();
}
function finAccDraw() {
  const acc = FIN.acc, box = $('#fin-acc'); if (!box) return;
  const teams = acc.teams || [], mates = acc.teammates || [];
  const role = t => t.default === 'everything' ? 'Owner' : finLeads(acc, t.teammate) ? 'Manager' : '';
  const grants = t => (t.grants || []).map(g => `<span class="fin-chip fin-grant${g.status !== 'active' ? ' muted' : ''}">${esc(finGrantLabel(g))}${g.status !== 'active' ? ' · suspended' : ''}</span>${g.granted_by
    ? `<span class="muted fin-small">by ${esc(finWho(g.granted_by))}</span>` : ''}`).join(' ');
  const row = (t, d) => `<tr data-fin-mate="${esc(t.teammate)}"${FIN_ACC.sel === t.teammate ? ' class="fin-on"' : ''} style="--d:${d}"><td><span class="fin-who fin-ind">${finAvatar(t.teammate, 20)}<button type="button" class="fin-link">${esc(finWho(t.teammate))}</button>${role(t) ? `<span class="muted fin-small">${role(t)}</span>` : ''}</span></td>
    <td><span class="fin-s ${finDefaultCls(t.default)}">${esc(finDefaultLabel(t, acc))}</span></td><td>${grants(t) || '<span class="muted">—</span>'}</td></tr>`;
  const out = [];
  // Those on no team shown (the owner, usually) first; then each team, its members and the teams under it.
  for (const t of mates.filter(m => !m.team || !teams.some(x => x.id === m.team))) out.push(row(t, 0));
  const kids = id => teams.filter(t => (t.parent || '') === id || (!id && t.parent && !teams.some(x => x.id === t.parent)));
  const walk = (team, d) => {
    const members = mates.filter(m => m.team === team.id).sort((a, b) => (b.teammate === team.lead) - (a.teammate === team.lead));
    out.push(`<tr class="fin-grp" style="--d:${d}"><td colspan="3"><span class="fin-ind">${esc(team.name)}</span></td></tr>`);
    for (const m of members) out.push(row(m, d));
    for (const k of kids(team.id)) if (d < 12) walk(k, d + 1);
  };
  for (const t of kids('')) walk(t, 0);
  box.innerHTML = `<table class="fin-t fin-acc-t"><thead><tr><th>Teammate</th><th>By default</th><th>Granted</th></tr></thead><tbody>${out.join('')}</tbody></table>`;
  const priv = acc.private_lines || [];
  $('#fin-private').innerHTML = priv.length ? `<h2 class="fin-h2">Private lines</h2><div class="fin-chips">${priv.map(p =>
    `<span class="fin-chip">${esc(p.name)} <span class="muted">· ${p.full_detail ? 'full detail' : 'totals only'}</span></span>`).join('')}</div>` : '';
}
function finAccRail() {
  const acc = FIN.acc, me = finMe(), t = (acc.teammates || []).find(x => x.teammate === FIN_ACC.sel);
  if (!t) { finRail(''); return; }
  const teams = acc.teams || [];
  const path = []; for (let id = t.team, n = 0; id && n < 12; n++) { const team = teams.find(x => x.id === id); if (!team) break; path.unshift(team.name); id = team.parent; }
  const owner = !!acc.can_view_as;
  const grants = (t.grants || []).map(g => {
    const mine = owner || g.granted_by === me;
    return `<div class="fin-kv"><span>${esc(finGrantLabel(g))}</span><span class="muted fin-small">${g.granted_by ? esc(finWho(g.granted_by)) : ''}${g.expires_at ? ' · until ' + esc(finDayLabel(g.expires_at)) : ' · no end'}${mine
      ? ` <button type="button" class="ghost fin-x" data-fin-revoke="${esc(g.id)}" aria-label="Revoke ${esc(finGrantLabel(g))}">✕</button>` : ''}</span></div>`;
  }).join('');
  // Owners grant anything; a manager grants to their teams' members (the route checks either way). No self-grants.
  const leads = new Set(teams.filter(x => x.lead === me).map(x => x.id));
  const may = t.teammate !== me && (owner || (t.team && leads.has(t.team)));
  const lines = (FIN_PNL.last?.rows || []).filter(r => !r.private);
  const bsLines = ['assets', 'liabilities', 'equity'].flatMap(k => FIN.bs?.data?.[k] || []).filter(r => r.account_id);
  const whats = owner ? Object.entries(FIN_WHAT) : [['expenses', FIN_WHAT.expenses], ['pnl_lines', FIN_WHAT.pnl_lines]];
  finRail(`<div class="fin-rail-title">${finAvatar(t.teammate, 28)}<div><h2>${esc(finWho(t.teammate))}</h2><div class="muted fin-small">${esc([path.join(' › '), t.kind === 'bot' ? 'bot' : 'person', t.reports_to ? 'reports to ' + finWho(t.reports_to) : ''].filter(Boolean).join(' · '))}</div></div></div>
    <h3 class="fin-h">Sees</h3>${finKv(finDefaultLabel(t, acc), '<span class="muted fin-small">Default</span>')}${grants}
    ${may ? `<h3 class="fin-h">Grant more</h3><form class="fin-form fin-rail-form" data-fin-grant>
      <label>What<select name="what">${whats.map(([v, l]) => `<option value="${v}"${v === 'expenses' ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
      <label data-fin-line hidden>Line<select name="line"></select></label>
      <label data-fin-whose>Whose<select name="whose"><option value="own">Their own</option>${teams.filter(x => owner || leads.has(x.id)).map(x => `<option value="team:${esc(x.id)}"${x.id === t.team ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}${owner ? '<option value="company">Company</option>' : ''}</select></label>
      <label>Until<input type="date" name="until" min="${finToday()}"></label>
      <div class="fin-foot"><button type="submit" class="primary">Grant</button></div></form>` : ''}`);
  const form = $('#fin-rail [data-fin-grant]'); if (!form) return;
  const sync = () => {
    const what = form.elements.what.value, line = form.querySelector('[data-fin-line]');
    const opts = what === 'pnl_lines' ? lines : what === 'bs_lines' ? bsLines : [];
    line.hidden = !opts.length;
    form.elements.line.innerHTML = opts.map(r => `<option value="${esc(r.account_id)}">${esc(r.name)}</option>`).join('');
    // Everything, the balance sheet and its lines, payables, receivables and sources are company-wide.
    form.querySelector('[data-fin-whose]').hidden = !['expenses', 'pnl', 'pnl_lines'].includes(what);
  };
  form.elements.what.onchange = sync; sync();
  form.onsubmit = async ev => {
    ev.preventDefault();
    const what = form.elements.what.value, whose = form.elements.whose.value, scoped = !form.querySelector('[data-fin-whose]').hidden;
    const payload = {subject_kind: 'teammate', subject: t.teammate, what};
    if (scoped) { payload.whose = whose.startsWith('team:') ? 'team' : whose; if (whose.startsWith('team:')) payload.whose_team = whose.slice(5); }
    if (what === 'pnl_lines' || what === 'bs_lines') {
      if (!form.elements.line.value) { toast('Open the P&L or balance sheet first to pick a line', true); return; }
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
    <label>Managers<select name="manager">${opt('team_expenses', 'Expenses of what they manage', d.manager)}${opt('own_expenses', 'Own expenses', d.manager)}${opt('none', 'Nothing', d.manager)}</select></label>
    <label>Everyone else<select name="member">${opt('own_expenses', 'Own expenses', d.member)}${opt('none', 'Nothing', d.member)}</select></label>
    <label>Revenue, balance sheet, payables, receivables<select disabled><option>Owners only</option></select></label>`, 'Save', async form => {
    const r = await finAct('view_defaults_set', {manager: form.elements.manager.value, member: form.elements.member.value});
    if (r.status === 'committed') toast('Saved');
    await finAccReload();
  });
}
