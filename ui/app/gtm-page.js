/* ui/app/gtm-page.js — The Go-to-market page (#/growth/gtm[/tab], brand-and-gtm spec §5) and its card on the Strategy
   page. Tabs: Motion, Buyer and Wishes edit the GTM draft (published with a note, like Brand; the draft pieces are in
   brand-page.js); Channels and Sources are rows that change without a publish (/api/v2/growth/channels, /sources, one
   source per kind of truth, two flagged); Model is the KPI tree from /api/v2/growth/kpis, whose place in the tree the
   owner sets through the KPI update route (POST /api/v2/kpis/{id}).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let GT = null;            // the open Go-to-market page: {load, tab, data, props, publishing, ch, src, conflicts, kpis}
const GT_TABS = [['motion', 'Motion'], ['buyer', 'Buyer'], ['channels', 'Channels'], ['sources', 'Sources'], ['model', 'Model'], ['wishes', 'Wishes']];
// The motions and what each sets by default (spec §5.2).
const GT_MOTIONS = [
  ['self_serve', 'Self-serve', 'Content/SEO, Lifecycle, Growth Engineer', 'Visitors → signups → paid → churn', 'Stripe, PostHog or GA4, Search Console'],
  ['sales_led', 'Sales-led', 'Head of Sales, SDR, AE, RevOps', 'Pipeline → win rate → deal size → cycle', 'CRM, attribution partner'],
  ['partner', 'Partner', 'Partnerships, Partner Marketing', 'Partners → active partners → deals per partner', 'CRM, partner portal'],
  ['marketplace', 'Marketplace', 'Supply lead, Demand lead', 'Supply × demand × match rate × take rate', 'Own database, Stripe'],
  ['local', 'Local services', 'Local SEO, Reviews, Paid Ads', 'Leads → bookings → repeat rate', 'Ads accounts, Google Business Profile, booking system'],
  ['community', 'Community', 'Community, Social, Partnerships', 'Reach → members → actives → paid', 'Platform admin, social accounts'],
  ['ecommerce', 'E-commerce', 'Paid Social, Lifecycle, Merchandising', 'Sessions → conversion → order value → repeat', 'Shopify, Triple Whale or Northbeam'],
];
const gtMotion = id => GT_MOTIONS.find(m => m[0] === id);
const GT_REACH = [['mcp', 'MCP'], ['credential', 'Credential'], ['url', 'URL'], ['person', 'Person']];
const GT_MAY = [['read', 'Read'], ['write', 'Write notes'], ['act', 'Act within rules']];
const GT_ROLES = [['north_star', 'North Star'], ['input', 'Input'], ['guardrail', 'Guardrail']];
const gtMayList = s => String(s || '').split(',').map(x => x.trim()).filter(Boolean);
const gtMayText = s => gtMayList(s).map(x => GT_MAY.find(m => m[0] === x)?.[1] || x).join(' · ');
// The people and bots a row can belong to, as human:<id> and bot:<slug>.
function grOwnerOpts(selected) {
  const sel = String(selected || ''), bare = sel.replace(/^bot:/, '');
  const people = (S.people || []).filter(p => !p.hidden).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived').slice().sort((a, b) => String(a.display_name || a.name).localeCompare(String(b.display_name || b.name)));
  return `<option value="">—</option><optgroup label="People">${people.map(p => `<option value="human:${esc(p.id)}"${sel === 'human:' + p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e => `<option value="bot:${esc(e.name)}"${bare === e.name ? ' selected' : ''}>${esc(e.display_name || e.name)}</option>`).join('')}</optgroup>`;
}
const gtBar = (now, target) => {
  if (now == null || target == null || !Number(target)) return '';
  const p = Math.max(0, Math.min(100, Number(now) / Number(target) * 100));
  return `<span class="gr-bar-t" role="img" aria-label="${Math.round(p)}% of target"><span style="width:${p.toFixed(1)}%"></span></span>`;
};

// ----------------------------------------------------------------- the Strategy page's card
// Whether there is a go-to-market to show: a motion or buyer, or a live channel or source.
function gtmCardEmpty(d, x = {}) {
  const body = d?.published?.body || (grDocMay(d) ? d?.draft?.body : null);
  return !body?.motion && !body?.buyer && !(x.channels || []).some(c => !c.retired_at) && !(x.sources || []).some(s => !s.retired_at);
}
// With none yet, the owner's way in (the Strategy page's setup row); others see nothing.
const gtmSetupHtml = (d, x = {}) => d && grDocMay(d) && gtmCardEmpty(d, x) ? `<a class="ghost gr-btn" href="${GTM_ROUTE}">Set up go-to-market</a>` : '';
function gtmCardHtml(d, x = {}) {
  if (!d || gtmCardEmpty(d, x)) return '';
  const may = grDocMay(d), body = d.published?.body || (may ? d.draft?.body : null);
  const chans = (x.channels || []).filter(c => !c.retired_at), srcs = (x.sources || []).filter(s => !s.retired_at);
  const m = body?.motion, ns = (x.kpis || []).find(k => k.growth_role === 'north_star'), n = may ? grDocChanged(d) : 0;
  const scaling = chans.filter(c => c.stage === 'scaling');
  const row = (k, v) => v ? `<div class="gt-cr"><span class="gr-k">${esc(k)}</span><span>${v}</span></div>` : '';
  return `<a class="card gt-card" href="${GTM_ROUTE}"><span class="gt-card-h"><span class="strat-k">Go-to-market</span><span class="spacer"></span>${n ? `<span class="pill waiting">${n === 1 ? '1 change' : `${n} changes`}</span>` : ''}</span>
    ${row('Motion', m ? esc([gtMotion(m.primary)?.[1], gtMotion(m.secondary)?.[1]].filter(Boolean).join(' + ')) : '')}
    ${row('Deal', esc([body?.buyer?.deal_size, body?.buyer?.cycle].filter(Boolean).join(' · ')))}
    ${row('Scaling', esc(scaling.map(c => c.name).join(', ')))}
    ${row('Sources', srcs.length ? esc(String(srcs.length)) : '')}
    ${ns ? `<div class="gt-cr gt-ns"><span class="gr-k">North Star</span><span>${esc(ns.name)} <b>${esc(grKpiVal(ns))}</b>${ns.target != null ? `<span class="muted"> / ${esc(kpiNum(ns.target, ns.unit))}</span>` : ''}</span>${gtBar(ns.latest, ns.target)}</div>` : ''}</a>`;
}

// ----------------------------------------------------------------- the page
function pageGtm() {
  const path = (S.route || '').split('?')[0];
  const tab = path.slice(GTM_ROUTE.length + 1).split('/')[0] || 'motion';
  if (!GT_TABS.some(t => t[0] === tab)) { location.replace(GTM_ROUTE); return; }
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const keep = GT || {};
  GT = {load: ++GR_LOAD, tab, data: keep.data || null, publishing: false, ch: keep.ch || null, src: keep.src || null, conflicts: keep.conflicts || [], kpis: keep.kpis || null,
    chMay: keep.chMay, srcMay: keep.srcMay};
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = growthHead('gtm') + `<div id="gr-body" class="gr-body">${GT.data ? '' : skelRows(6, {dot: false})}</div>`;
  if (GT.data) { gtmDraw(); GT.drawn = true; }
  void gtmRead(GT);
}
async function gtmRead(state) {
  try {
    const [data, ch, src, k] = await Promise.all([get('/v2/gtm', {fresh: true}), get('/v2/growth/channels', {fresh: true}),
      get('/v2/growth/sources', {fresh: true}), get('/v2/growth/kpis', {fresh: true}).catch(() => ({kpis: []}))]);
    if (GT !== state) return;
    const next = {data, ch: ch.channels || [], src: src.sources || [], conflicts: src.conflicts || [], kpis: k.kpis || [], chMay: ch.may_edit, srcMay: src.may_edit};
    // A page already drawn from the last read keeps what is being typed when nothing changed.
    const same = state.drawn && JSON.stringify(next) === JSON.stringify({data: state.data, ch: state.ch, src: state.src, conflicts: state.conflicts, kpis: state.kpis, chMay: state.chMay, srcMay: state.srcMay});
    Object.assign(state, next);
    if (same) return;
  } catch (e) { if (GT === state) $('#gr-body').innerHTML = grFail(e); return; }
  gtmDraw();
}
function gtmDraw() {
  const state = GT, body = $('#gr-body');
  if (!state || !body || !state.data) return;
  const d = state.data, doc = ['motion', 'buyer', 'wishes'].includes(state.tab);
  const tabs = `<nav class="gr-tabs gt-tabs" aria-label="Go-to-market">${GT_TABS.map(([k, label]) =>
    `<a href="${GTM_ROUTE}${k === 'motion' ? '' : '/' + k}"${state.tab === k ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('')}</nav>`;
  // The draft's tools sit in the section head, beside the Growth tabs.
  patchHTML($('#strat-actions'), grDocToolsHtml(d, state));
  body.innerHTML = (state.publishing ? grDocPublishHtml() : '') + tabs
    + `<div class="gt-pane">${({motion: gtMotionHtml, buyer: gtBuyerHtml, wishes: gtWishesHtml, channels: gtChannelsHtml, sources: gtSourcesHtml, model: gtModelHtml})[state.tab](state)}</div>`;
  grDocBind($('#main'), '/v2/gtm', state, gtmDraw, gtmBookHtml);
  if (doc) gtDocBind(state); else if (state.tab === 'channels') gtChannelsBind(state); else if (state.tab === 'sources') gtSourcesBind(state); else gtModelBind(state);
}
const gtBody = state => (grDocMay(state.data) ? state.data.draft?.body || state.data.published?.body : state.data.published?.body) || {};

// What a version shows: the versioned sections, read-only.
function gtmBookHtml(b) {
  const m = b.motion, buyer = b.buyer || {};
  const dl = rows => rows.filter(r => r[1]).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  return `<dl class="br-dl">${dl([['Motion', m ? [gtMotion(m.primary)?.[1], gtMotion(m.secondary)?.[1]].filter(Boolean).join(' + ') : ''],
    ['Who buys', buyer.who_buys], ['Who uses it', buyer.who_uses], ['Deal size', buyer.deal_size], ['Sales cycle', buyer.cycle],
    ['Objections', (buyer.objections || []).join(' · ')], ['Playbooks', (b.playbooks || []).join(' · ')]])}</dl>
    ${(b.wishes || []).length ? `<ul class="gt-wishes">${b.wishes.map(w => `<li><span>${esc(w.text)}</span>${w.scope ? `<span class="gr-tag">${esc(w.scope)}</span>` : ''}<span class="pill ${BR_LEVEL[w.level] || ''}">${esc(w.level || '')}</span></li>`).join('')}</ul>` : ''}`;
}

// ----------------------------------------------------------------- Motion, Buyer, Wishes (the draft)
function gtMotionHtml(state) {
  const b = gtBody(state), m = b.motion || {}, may = grDocMay(state.data), set = gtMotion(m.primary);
  const tiles = GT_MOTIONS.filter(x => may || x[0] === m.primary || x[0] === m.secondary).map(([id, name, , model]) => {
    const tag = id === m.primary ? 'Primary' : id === m.secondary ? 'Secondary' : '';
    const inner = `<span class="gt-mo-n">${esc(name)}</span>${tag ? `<span class="pill ${id === m.primary ? 'ok' : ''}">${tag}</span>` : ''}<span class="muted gt-mo-l">${esc(model)}</span>`;
    return may ? `<button type="button" class="gt-mo${tag ? ' gt-on' : ''}" data-motion="${id}" aria-pressed="${!!tag}">${inner}</button>` : `<div class="gt-mo gt-on">${inner}</div>`;
  }).join('');
  const sets = set ? `<section class="card gr-sec gt-sets"><h3 class="gr-k">This sets</h3><dl class="br-dl"><dt>Growth team</dt><dd>${esc(set[2])}</dd><dt>Model</dt><dd>${esc(set[3])}</dd>
      <dt>Suggested sources</dt><dd>${esc(set[4])}</dd></dl></section>` : '';
  const pb = b.playbooks || [];
  const plays = may ? `<form class="card gr-sec gr-form" data-gt-form="playbooks" novalidate>${grField('Playbooks', `<input name="playbooks" value="${esc(pb.join(', '))}">`)}
      <div class="gr-foot"><button class="ghost small" type="submit">Save</button></div></form>`
    : pb.length ? `<section class="card gr-sec"><h3 class="gr-k">Playbooks</h3><div class="gr-flow">${pb.map(p => `<span class="gr-tag">${esc(p)}</span>`).join('')}</div></section>` : '';
  return `<div class="gt-motion"><div class="gt-mos">${tiles}</div><div class="gt-side">${sets}${plays}</div></div>`;
}
function gtBuyerHtml(state) {
  const b = gtBody(state).buyer || {};
  if (!grDocMay(state.data)) return gtmBookHtml({buyer: b});
  return `<form class="card gr-form gt-buyer" data-gt-form="buyer" novalidate>
    ${grField('Who buys', `<input name="who_buys" maxlength="300" value="${esc(b.who_buys || '')}">`)}
    ${grField('Who uses it', `<input name="who_uses" maxlength="300" value="${esc(b.who_uses || '')}">`)}
    <div class="gr-pair">${grField('Deal size', `<input name="deal_size" maxlength="80" value="${esc(b.deal_size || '')}">`)}
    ${grField('Sales cycle', `<input name="cycle" maxlength="80" value="${esc(b.cycle || '')}">`)}</div>
    ${grField('Top objections', `<textarea name="objections" rows="3">${esc((b.objections || []).join('\n'))}</textarea>`)}
    <div class="gr-foot"><button class="primary" type="submit">Save</button></div></form>`;
}
function gtWishesHtml(state) {
  const w = gtBody(state).wishes || [];
  if (!grDocMay(state.data)) return gtmBookHtml({wishes: w});
  return `<form class="card gr-form" data-gt-form="wishes" novalidate>${grRowsHtml('wishes', 'Wishes', w)}
    <div class="gr-foot"><button class="primary" type="submit">Save</button></div></form>`;
}
function gtDocBind(state) {
  const body = $('#gr-body'), save = (sections, btn) => {
    if (btn) btn.disabled = true;
    return grDocSave('/v2/gtm', state, sections, gtmDraw).then(ok => {
      if (ok) { toast('Saved'); return; }
      if (btn?.isConnected) btn.disabled = false;
      body.querySelectorAll('[data-motion]').forEach(x => { x.disabled = false; });
    });
  };
  body.querySelectorAll('[data-motion]').forEach(b => {
    b.onclick = () => {
      const m = {...(gtBody(state).motion || {})}, id = b.dataset.motion;
      let next;
      if (id === m.primary) next = m.secondary ? {primary: m.secondary, secondary: null} : null;
      else if (id === m.secondary) next = {primary: id, secondary: m.primary};
      else next = m.primary ? {primary: m.primary, secondary: id} : {primary: id, secondary: null};
      body.querySelectorAll('[data-motion]').forEach(x => { x.disabled = true; });
      void save({motion: next});
    };
  });
  const form = body.querySelector('[data-gt-form]');
  if (!form) return;
  if (form.dataset.gtForm === 'wishes') grRowsBind(form);
  form.onsubmit = ev => {
    ev.preventDefault();
    const kind = form.dataset.gtForm, el = form.elements, btn = form.querySelector('[type=submit]');
    if (kind === 'playbooks') { const p = el.playbooks.value.split(',').map(s => s.trim()).filter(Boolean); return void save({playbooks: p.length ? p : null}, btn); }
    if (kind === 'wishes') { const w = grRowsRead(form, 'wishes'); return void save({wishes: w.length ? w : null}, btn); }
    const v = {who_buys: el.who_buys.value.trim(), who_uses: el.who_uses.value.trim(), deal_size: el.deal_size.value.trim(), cycle: el.cycle.value.trim(), objections: grLines(el.objections.value)};
    void save({buyer: Object.values(v).some(x => Array.isArray(x) ? x.length : x) ? v : null}, btn);
  };
}

// ----------------------------------------------------------------- Channels
function gtChannelsHtml(state) {
  const may = state.chMay ?? grAdmin(), list = (state.ch || []).filter(c => !c.retired_at).sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0) || String(a.name).localeCompare(String(b.name)));
  const src = id => (state.src || []).find(s => s.id === id), kpi = id => (state.kpis || []).find(k => k.id === id);
  const add = may ? `<div class="gr-bar"><span class="spacer"></span><button class="primary" type="button" data-ch-add>+ Channel</button></div>` : '';
  if (!list.length) return add;
  return add + `<div class="card gr-scroll"><table class="gr-t"><thead><tr><th scope="col">Channel</th><th scope="col">Stage</th><th scope="col">Owner</th>
    <th scope="col" class="num">Budget / mo</th><th scope="col">Numbers from</th><th scope="col">Guardrail</th>${may ? '<th scope="col"><span class="sr-only">Edit</span></th>' : ''}</tr></thead>
    <tbody>${list.map(c => { const s = src(c.source_id), g = kpi(c.guardrail_kpi_id); return `<tr>
      <th scope="row"><span class="gr-cn"><span class="gr-track gr-track-${esc(c.track)}">${c.track === 'brand' ? 'Brand' : 'Performance'}</span>${esc(c.name)}</span>${c.why ? `<span class="muted gr-sub">${esc(c.why)}</span>` : ''}</th>
      <td>${grStage(c.stage)}</td><td>${esc(grWho(c.owner_ref) || '—')}</td><td class="num">${esc(grUsd(c.budget_month))}</td>
      <td>${s ? `<a href="${GTM_ROUTE}/sources">${esc(s.system)}</a>` : '—'}</td>
      <td>${g ? `${esc(g.name)}${c.guardrail_max != null ? ` ≤ ${esc(kpiNum(c.guardrail_max, g.unit))}` : ''}` : '—'}</td>
      ${may ? `<td><button class="ghost small" type="button" data-ch-edit="${esc(c.slug)}">Edit</button></td>` : ''}</tr>`; }).join('')}</tbody></table></div>`;
}
function gtChannelDialog(state, c = null) {
  const kp = state.kpis || [], live = (state.src || []).filter(s => !s.retired_at);
  const body = `${grField('Name', `<input name="name" maxlength="80" required value="${esc(c?.name || '')}">`)}
    ${c ? '' : grField('Slug', `<input name="slug" maxlength="40" pattern="[a-z0-9][a-z0-9-]*" required autocapitalize="off">`)}
    <div class="gr-pair">${grField('Track', `<select name="track">${grOpts([['performance', 'Performance'], ['brand', 'Brand']], c?.track || 'performance', null)}</select>`)}
    ${grField('Stage', `<select name="stage">${grOpts(Object.entries(GR_CH_STAGE).map(([k, v]) => [k, v[0]]), c?.stage || 'idea', null)}</select>`)}</div>
    <div class="gr-pair">${grField('Owner', `<select name="owner_ref">${grOwnerOpts(c?.owner_ref)}</select>`)}
    ${grField('Budget / mo', `<input name="budget_month" inputmode="decimal" value="${esc(c?.budget_month ?? '')}">`)}</div>
    ${grField('Numbers from', `<select name="source_id">${grOpts(live.map(s => [s.id, `${s.system} · ${s.truth_for}`]), c?.source_id, '—')}</select>`)}
    <div class="gr-pair">${grField('Guardrail', `<select name="guardrail_kpi_id">${grOpts(kp.map(k => [k.id, k.name]), c?.guardrail_kpi_id, '—')}</select>`)}
    ${grField('Max', `<input name="guardrail_max" inputmode="decimal" value="${esc(c?.guardrail_max ?? '')}">`)}</div>
    ${grField('Why', `<textarea name="why" rows="2" maxlength="1000">${esc(c?.why || '')}</textarea>`)}
    ${c ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const dlg = grDialog(c ? c.name : 'New channel', body, c ? 'Save' : 'Add', async form => {
    const el = form.elements, v = n => el[n].value.trim();
    const out = {name: v('name'), track: v('track'), stage: v('stage'), owner_ref: v('owner_ref') || null, budget_month: grNum(v('budget_month')),
      source_id: v('source_id') || null, guardrail_kpi_id: v('guardrail_kpi_id') || null, guardrail_max: grNum(v('guardrail_max')), why: v('why')};
    if (!out.name) throw new Error('A name');
    if (c) await patch(`/v2/growth/channels/${encodeURIComponent(c.slug)}`, out);
    else {
      out.slug = v('slug').toLowerCase();
      if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(out.slug)) throw new Error('Slug: lowercase letters, digits and -');
      await post('/v2/growth/channels', out);
    }
    toast('Saved'); void gtmRead(state);
  });
  if (!c) {
    const name = dlg.querySelector('[name=name]'), slug = dlg.querySelector('[name=slug]');
    name.oninput = () => { if (!slug.dataset.touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); };
    slug.oninput = () => { slug.dataset.touched = '1'; };
  }
  const retire = dlg.querySelector('[data-retire]');
  if (retire) gtRetireButton(retire, dlg, `/v2/growth/channels/${encodeURIComponent(c.slug)}/retire`, state);
}
// Retire asks once more on the same button (no window.confirm).
function gtRetireButton(btn, dlg, path, state) {
  btn.onclick = async () => {
    if (!btn.dataset.armed) { btn.dataset.armed = '1'; btn.textContent = 'Retire for good'; return; }
    btn.disabled = true;
    try { await post(path, {}); toast('Retired'); dlg.close(); void gtmRead(state); }
    catch (e) { toast(e.message || 'Not retired', true); btn.disabled = false; }
  };
}
function gtChannelsBind(state) {
  const body = $('#gr-body');
  const add = body.querySelector('[data-ch-add]');
  if (add) add.onclick = () => gtChannelDialog(state);
  body.querySelectorAll('[data-ch-edit]').forEach(b => { b.onclick = () => gtChannelDialog(state, state.ch.find(c => c.slug === b.dataset.chEdit)); });
}

// ----------------------------------------------------------------- Sources
function gtSourcesHtml(state) {
  const may = state.srcMay ?? grAdmin(), list = (state.src || []).filter(s => !s.retired_at).sort((a, b) => String(a.truth_for).localeCompare(String(b.truth_for)));
  const clash = new Set((state.conflicts || []).map(t => String(t).toLowerCase()));
  const banners = (state.conflicts || []).map(t => {
    const rows = list.filter(s => String(s.truth_for).toLowerCase() === String(t).toLowerCase());
    return `<div class="gr-banner gr-amber" data-clash="${esc(t)}"><span><b>${esc(t)}</b> · ${esc(rows.map(s => s.system).join(', '))}</span><span class="spacer"></span>
      ${may ? rows.map(s => `<button class="ghost small" type="button" data-use="${esc(s.id)}">Use ${esc(s.system)}</button>`).join('') : ''}</div>`;
  }).join('');
  const add = may ? `<div class="gr-bar"><span class="spacer"></span><button class="primary" type="button" data-src-add>+ Source</button></div>` : '';
  if (!list.length) return add;
  const reach = s => `${GT_REACH.find(r => r[0] === s.reach_kind)?.[1] || esc(s.reach_kind)}${s.reach_ref ? ` <code>${esc(s.reach_ref)}</code>` : ''}`;
  return banners + add + `<div class="card gr-scroll"><table class="gr-t"><thead><tr><th scope="col">Truth for</th><th scope="col">Source</th><th scope="col">Reached by</th>
    <th scope="col">Bots may</th><th scope="col">Owner</th>${may ? '<th scope="col"><span class="sr-only">Edit</span></th>' : ''}</tr></thead>
    <tbody>${list.map(s => `<tr${clash.has(String(s.truth_for).toLowerCase()) ? ' class="gr-clash"' : ''}><th scope="row">${esc(s.truth_for)}</th><td>${esc(s.system)}</td><td>${reach(s)}</td>
      <td>${esc(gtMayText(s.may) || '—')}</td><td>${esc(grWho(s.owner_ref) || '—')}</td>
      ${may ? `<td><button class="ghost small" type="button" data-src-edit="${esc(s.id)}">Edit</button></td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}
async function gtSourceDialog(state, s = null) {
  // What reach_ref can name: connected MCP tools and stored credentials, else free text.
  const [tools, creds] = await Promise.all([get('/v2/tools').then(r => r.integrations || []).catch(() => []),
    S.me?.credential_access ? get('/v2/credentials').then(r => r.credentials || []).catch(() => []) : []]);
  const opts = [...tools.map(t => t.service || t.title).filter(Boolean), ...creds.map(c => c.name).filter(Boolean)];
  const truths = [...new Set((state.src || []).map(x => x.truth_for))];
  const may = gtMayList(s?.may || 'read');
  const body = `${grField('Truth for', `<input name="truth_for" maxlength="80" required list="gt-truths" value="${esc(s?.truth_for || '')}"><datalist id="gt-truths">${truths.map(t => `<option value="${esc(t)}">`).join('')}</datalist>`)}
    ${grField('Source', `<input name="system" maxlength="80" required value="${esc(s?.system || '')}">`)}
    <div class="gr-pair">${grField('Reached by', `<select name="reach_kind">${grOpts(GT_REACH, s?.reach_kind || 'mcp', null)}</select>`)}
    ${grField('Which', `<input name="reach_ref" maxlength="300" list="gt-reach" value="${esc(s?.reach_ref || '')}"><datalist id="gt-reach">${opts.map(o => `<option value="${esc(o)}">`).join('')}</datalist>`)}</div>
    <fieldset class="gr-fs"><legend>Bots may</legend>${grChecks('may', GT_MAY, may)}</fieldset>
    ${grField('Owner', `<select name="owner_ref">${grOwnerOpts(s?.owner_ref)}</select>`)}
    ${s ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const dlg = grDialog(s ? `${s.truth_for} · ${s.system}` : 'New source', body, s ? 'Save' : 'Add', async form => {
    const v = n => form.elements[n].value.trim();
    const out = {truth_for: v('truth_for'), system: v('system'), reach_kind: v('reach_kind'), reach_ref: v('reach_ref'), may: grChecked(form, 'may').join(','), owner_ref: v('owner_ref') || null};
    if (!out.truth_for || !out.system) throw new Error('Truth for and source');
    if (s) await patch(`/v2/growth/sources/${encodeURIComponent(s.id)}`, out); else await post('/v2/growth/sources', out);
    toast('Saved'); void gtmRead(state);
  });
  const retire = dlg.querySelector('[data-retire]');
  if (retire) gtRetireButton(retire, dlg, `/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, state);
}
function gtSourcesBind(state) {
  const body = $('#gr-body');
  const add = body.querySelector('[data-src-add]');
  if (add) add.onclick = () => void gtSourceDialog(state);
  body.querySelectorAll('[data-src-edit]').forEach(b => { b.onclick = () => void gtSourceDialog(state, state.src.find(s => s.id === b.dataset.srcEdit)); });
  // "Use X" keeps X and retires the other sources for that truth.
  body.querySelectorAll('[data-use]').forEach(b => {
    b.onclick = async () => {
      const t = b.closest('[data-clash]').dataset.clash.toLowerCase();
      const others = state.src.filter(s => !s.retired_at && s.id !== b.dataset.use && String(s.truth_for).toLowerCase() === t);
      b.closest('[data-clash]').querySelectorAll('button').forEach(x => { x.disabled = true; });
      try { for (const s of others) await post(`/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, {}); toast('Fixed'); }
      catch (e) { toast(e.message || 'Not fixed', true); }
      void gtmRead(state);
    };
  });
}

// ----------------------------------------------------------------- Model: the KPI tree
function gtModelHtml(state) {
  // The growth KPI read says per KPI whether the caller may edit it (the KPI update route's check).
  const kpis = state.kpis || [], mayKpi = k => k.may_edit === true, may = kpis.some(mayKpi);
  if (!kpis.length) return '';
  const ns = kpis.find(k => k.growth_role === 'north_star');
  const tree = ns ? grTree(kpis, ns.id) : [];
  const inTree = new Set([ns?.id, ...tree.map(k => k.id)]);
  const guards = kpis.filter(k => k.growth_role === 'guardrail' && !inTree.has(k.id));
  const rest = kpis.filter(k => !inTree.has(k.id) && k.growth_role !== 'guardrail');
  const stage = id => GR_STAGES.find(s => s[0] === id)?.[1] || '';
  const row = (k, depth = 0, cls = '') => `<tr class="${cls}"><th scope="row"><span class="gt-kn" style="--d:${depth}">${esc(k.name)}</span></th>
      <td class="num">${esc(grKpiVal(k))}</td><td class="num">${k.target != null ? esc(kpiNum(k.target, k.unit)) : '—'}</td><td class="gt-barc">${gtBar(k.latest, k.target)}</td>
      <td>${esc(GT_ROLES.find(r => r[0] === k.growth_role)?.[1] || '')}</td><td>${esc(stage(k.stage))}</td><td>${esc(k.loop_name ? `${k.loop_name}${k.loop_step != null ? ' · ' + k.loop_step : ''}` : '')}</td>
      ${may ? `<td>${mayKpi(k) ? `<button class="ghost small" type="button" data-kpi-edit="${esc(k.id)}">Edit</button>` : ''}</td>` : ''}</tr>`;
  const group = (label, rows) => rows ? `<tbody><tr class="gt-grp"><th scope="rowgroup" colspan="${may ? 8 : 7}">${esc(label)}</th></tr>${rows}</tbody>` : '';
  return `<div class="card gr-scroll"><table class="gr-t gt-model"><thead><tr><th scope="col">KPI</th><th scope="col" class="num">Now</th><th scope="col" class="num">Target</th>
    <th scope="col"><span class="sr-only">Progress</span></th><th scope="col">Role</th><th scope="col">Stage</th><th scope="col">Loop</th>${may ? '<th scope="col"><span class="sr-only">Edit</span></th>' : ''}</tr></thead>
    ${ns ? group('North Star', row(ns, 0, 'gt-nsr') + tree.map(k => row(k, k.depth + 1, k.growth_role === 'guardrail' ? 'gr-guard' : '')).join('')) : ''}
    ${group('Guardrails', guards.map(k => row(k, 0, 'gr-guard')).join(''))}${group('Other', rest.map(k => row(k)).join(''))}</table></div>`;
}
function gtModelBind(state) {
  $('#gr-body').querySelectorAll('[data-kpi-edit]').forEach(b => {
    b.onclick = () => {
      const k = state.kpis.find(x => x.id === b.dataset.kpiEdit);
      const below = new Set([k.id, ...grTree(state.kpis, k.id).map(x => x.id)]);
      grDialog(k.name, `${grField('Feeds', `<select name="parent_kpi_id">${grOpts(state.kpis.filter(x => !below.has(x.id)).map(x => [x.id, x.name]), k.parent_kpi_id, '—')}</select>`)}
        <div class="gr-pair">${grField('Role', `<select name="growth_role">${grOpts(GT_ROLES, k.growth_role, '—')}</select>`)}
        ${grField('Stage', `<select name="stage">${grOpts(GR_STAGES, k.stage, '—')}</select>`)}</div>
        <div class="gr-pair">${grField('Loop', `<input name="loop_name" maxlength="80" value="${esc(k.loop_name || '')}">`)}
        ${grField('Step', `<input name="loop_step" type="number" min="0" step="1" value="${esc(k.loop_step ?? '')}">`)}</div>`, 'Save', async form => {
        const v = n => form.elements[n].value.trim();
        await post(`/v2/kpis/${encodeURIComponent(k.id)}`, {parent_kpi_id: v('parent_kpi_id') || null, growth_role: v('growth_role') || null,
          stage: v('stage') || null, loop_name: v('loop_name') || null, loop_step: v('loop_step') === '' ? null : Number(v('loop_step'))});
        toast('Saved'); void gtmRead(state);
      });
    };
  });
}
