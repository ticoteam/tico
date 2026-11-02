/* ui/app/growth-buyers.js — Growth › Buyers (#/growth/buyers): who the company sells to, a plain list of names and who
   buys (GET /api/v2/growth/segments; the API calls them segments). A buyer's name is its market-graph segment, so
   Serving and Next are what Strategy's Where we play shows as Us and Next; the status lives only in the buyer's dialog,
   and Dropped ones fold behind a toggle. The owner or an admin adds one (POST, the name picked from the market's
   segments or a new one) and edits one (PATCH /api/v2/growth/segments/{id}). Under the list, the company-wide
   go-to-market (GET /api/v2/gtm: motion, who uses it, sales cycle, objections, playbooks), edited into the draft and
   published with a note (PUT /api/v2/gtm/draft, POST /api/v2/gtm/publish); its versions are on Learning.
   Also the buyer pieces the idea form and its panel share, and what a go-to-market version shows.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GS_STATUS = [['serving', 'Serving'], ['testing', 'Testing'], ['next', 'Next'], ['dropped', 'Dropped']];
// The motions (brand-and-gtm spec §5.2).
const GT_MOTIONS = [['self_serve', 'Self-serve'], ['sales_led', 'Sales-led'], ['partner', 'Partner'], ['marketplace', 'Marketplace'],
  ['local', 'Local services'], ['community', 'Community'], ['ecommerce', 'E-commerce']];
const gtMotion = id => GT_MOTIONS.find(m => m[0] === id)?.[1] || id || '';

// ----------------------------------------------------------------- shared
// The buyers list, or [] when it does not load (an install whose server predates segments).
const gsRead = (fresh = false) => get('/v2/growth/segments', fresh ? {fresh: true} : undefined).then(r => r.segments || []).catch(() => []);
// A buyer by its own id or its market segment's id: records name either.
const gsFind = (segs, id) => (segs || []).find(s => s.id === id || s.segment_id === id);
const gsName = (segs, id) => gsFind(segs, id)?.name || String(id || '').replace(/^segment\//, '');
const gsTags = (segs, ids) => (ids || []).map(id => `<span class="gr-tag gs-tag">${esc(gsName(segs, id))}</span>`).join('');
// What a picker offers: the buyers not dropped, plus any the record already names, so saving never drops one.
function gsPairs(segs, keep = []) {
  const pairs = (segs || []).filter(s => s.status !== 'dropped').map(s => [s.id, s.name]);
  for (const id of keep || []) {
    const s = gsFind(segs, id);
    if (!pairs.some(([x]) => x === id || x === s?.id)) pairs.push([s?.id || id, gsName(segs, id)]);
  }
  return pairs;
}
// The ids a record names, as the picker's values (a market id becomes the growth row's id).
const gsIds = (segs, ids) => (ids || []).map(id => gsFind(segs, id)?.id || id);
// A buyers fieldset for a form, or '' with nothing to pick; `gsPicked` reads it, undefined when absent.
const gsChecks = (segs, on = []) => {
  const pairs = gsPairs(segs, on);
  return pairs.length ? `<fieldset class="gr-fs" data-gs-pick><legend>Buyers</legend>${grChecks('segments', pairs, gsIds(segs, on))}</fieldset>` : '';
};
const gsPicked = form => form.querySelector('[data-gs-pick]') ? grChecked(form, 'segments') : undefined;

// What a go-to-market version shows, read-only.
function gtmBookHtml(b) {
  const m = b.motion, buyer = b.buyer || {};
  return `<dl class="br-dl">${[['Motion', m ? [gtMotion(m.primary), m.secondary ? gtMotion(m.secondary) : ''].filter(Boolean).join(' + ') : ''],
    ['Who buys', buyer.who_buys], ['Who uses it', buyer.who_uses], ['Deal size', buyer.deal_size], ['Sales cycle', buyer.cycle],
    ['Objections', (buyer.objections || []).join(' · ')], ['Playbooks', (b.playbooks || []).join(' · ')]]
    .filter(r => r[1]).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
}
// The go-to-market an editor sees is the draft when there is one; everyone else the published one.
const gtBody = d => (grDocMay(d) ? d?.draft?.body || d?.published?.body : d?.published?.body) || {};

// ----------------------------------------------------------------- the page
function pageGrowthBuyers(state) { void gsLoad(state); }
async function gsLoad(state) {
  let r, gtm;
  try {
    [r, gtm] = await Promise.all([get('/v2/growth/segments', {fresh: true}), get('/v2/gtm', {fresh: true}).catch(() => null)]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = {segs: r.segments || [], gtm, may: r.may_edit ?? grAdmin()};
  gsDraw(state);
}
const GS_RANK = Object.fromEntries(GS_STATUS.map(([s], n) => [s, n]));
function gsRows(list, may) {
  return list.map(s => {
    const inner = `<span class="gb-n">${esc(s.name)}</span>${s.who_buys ? `<span class="muted gb-w">${esc(s.who_buys)}</span>` : ''}`;
    return `<li data-gb-row="${esc(s.id)}">${may ? `<button type="button" class="gb-row" data-gb-edit="${esc(s.id)}">${inner}</button>` : `<div class="gb-row">${inner}</div>`}</li>`;
  }).join('');
}
function gsDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  patchHTML($('#strat-actions'), d.may ? '<button class="primary" type="button" data-gs-new>+ Buyer</button>' : '');
  const sorted = d.segs.slice().sort((a, b) => (GS_RANK[a.status] ?? 9) - (GS_RANK[b.status] ?? 9) || (a.sort ?? 0) - (b.sort ?? 0) || String(a.name).localeCompare(String(b.name)));
  const live = sorted.filter(s => s.status !== 'dropped'), dropped = sorted.filter(s => s.status === 'dropped');
  const list = live.length || dropped.length ? `<section class="card gb-list" aria-label="Buyers"><ul class="gb-ul">${gsRows(live, d.may)}</ul>
    ${dropped.length ? `<button type="button" class="ghost small gb-drop" data-gb-dropped aria-expanded="${!!state.showDropped}">Dropped (${dropped.length})</button>
      <ul class="gb-ul gb-dropped"${state.showDropped ? '' : ' hidden'}>${gsRows(dropped, d.may)}</ul>` : ''}</section>` : '';
  body.innerHTML = list + gtmBlockHtml(d.gtm);
  const add = $('#strat-actions [data-gs-new]');
  if (add) add.onclick = () => void gsNew(state);
  body.querySelectorAll('[data-gb-edit]').forEach(b => { b.onclick = () => gsEdit(state, d.segs.find(s => s.id === b.dataset.gbEdit)); });
  const tog = body.querySelector('[data-gb-dropped]');
  if (tog) tog.onclick = () => { state.showDropped = !state.showDropped; gsDraw(state); };
  const ed = body.querySelector('[data-gtm-edit]');
  if (ed) ed.onclick = () => gtmEdit(state);
}

function gsFields(s, fresh = false) {
  const statuses = GS_STATUS.filter(([v]) => !fresh || v !== 'dropped');
  return `${grField('Who buys', `<input name="who_buys" maxlength="300" value="${esc(s.who_buys || '')}">`)}
    <div class="gr-pair">${grField('Deal size', `<input name="deal_size" maxlength="80" value="${esc(s.deal_size || '')}">`)}
    ${grField('Motion', `<select name="motion">${grOpts(GT_MOTIONS, s.motion, '—')}</select>`)}</div>
    ${grField('Why', `<textarea name="why" rows="2" maxlength="1000">${esc(s.why || '')}</textarea>`)}
    ${grField('Status', `<select name="status">${grOpts(statuses, s.status, null)}</select>`)}`;
}
function gsBody(form, was = null) {
  const v = n => form.elements[n].value.trim();
  const body = {status: v('status'), motion: v('motion') || null, who_buys: v('who_buys') || null, deal_size: v('deal_size') || null, why: v('why')};
  if (!was) return Object.fromEntries(Object.entries(body).filter(([k, x]) => k === 'status' || (x != null && x !== '')));
  return Object.fromEntries(Object.entries(body).filter(([k, x]) => (x ?? '') !== (was[k] ?? '')));
}
async function gsNew(state) {
  const market = await get('/v2/market/spend').then(r => r.segments || []).catch(() => []);
  if (GR !== state) return;
  const taken = new Set(state.data.segs.map(s => s.segment_id)), free = market.filter(m => !taken.has(m.id));
  grDialog('New buyer', `${grField('Name', `<input name="name" maxlength="120" required autocomplete="off" list="gs-market">`)}
    <datalist id="gs-market">${free.map(m => `<option value="${esc(m.name)}"></option>`).join('')}</datalist>
    ${gsFields({status: 'testing'}, true)}`, 'Add', async form => {
    const name = form.elements.name.value.replace(/\s+/g, ' ').trim();
    if (!name) throw new Error('A name');
    const known = free.find(m => String(m.name).toLowerCase() === name.toLowerCase());
    await post('/v2/growth/segments', {...(known ? {segment_id: known.id} : {name}), ...gsBody(form)});
    toast('Buyer added');
    if (GR === state) void gsLoad(state);
  });
}
function gsEdit(state, s) {
  if (!s) return;
  grDialog(s.name, grField('Name', `<input name="name" maxlength="120" required value="${esc(s.name || '')}">`) + gsFields(s), 'Save', async form => {
    const body = gsBody(form, s), name = form.elements.name.value.replace(/\s+/g, ' ').trim();
    if (!name) throw new Error('A name');
    if (name !== s.name) body.name = name;
    if (!Object.keys(body).length) return;
    await patch(`/v2/growth/segments/${encodeURIComponent(s.id)}`, body);
    toast('Saved');
    if (GR === state) void gsLoad(state);
  });
}

// ----------------------------------------------------------------- the company-wide go-to-market
function gtmBlockHtml(d) {
  if (!d) return '';
  const may = grDocMay(d), b = gtBody(d), buyer = b.buyer || {}, m = b.motion;
  const rows = [['Motion', m ? [gtMotion(m.primary), m.secondary ? gtMotion(m.secondary) : ''].filter(Boolean).join(' + ') : ''],
    ['Who uses it', buyer.who_uses], ['Sales cycle', buyer.cycle], ['Objections', (buyer.objections || []).join(' · ')],
    ['Playbooks', (b.playbooks || []).join(' · ')]].filter(r => r[1]);
  if (!rows.length && !may) return '';
  const n = may ? grDocChanged(d) : 0;
  return `<section class="card gb-gtm" aria-label="How we sell">
    ${rows.length ? `<dl class="br-dl">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    ${may ? `<div class="gb-gtm-h">${n ? `<span class="pill waiting">${n === 1 ? '1 change' : `${n} changes`}</span>` : ''}<span class="spacer"></span>
      <button class="ghost small" type="button" data-gtm-edit>Edit</button></div>` : ''}</section>`;
}
// Edit writes the draft with the revision it was read at, then publishes it with the note (only when it differs).
function gtmEdit(state) {
  let d = state.data.gtm;
  const b = gtBody(d), buyer = b.buyer || {}, m = b.motion || {};
  grDialog('How we sell', `<div class="gr-pair">${grField('Motion', `<select name="primary">${grOpts(GT_MOTIONS, m.primary, '—')}</select>`)}
    ${grField('Second motion', `<select name="secondary">${grOpts(GT_MOTIONS, m.secondary, '—')}</select>`)}</div>
    ${grField('Who uses it', `<input name="who_uses" maxlength="300" value="${esc(buyer.who_uses || '')}">`)}
    ${grField('Sales cycle', `<input name="cycle" maxlength="80" value="${esc(buyer.cycle || '')}">`)}
    ${grField('Objections', `<textarea name="objections" rows="3">${esc((buyer.objections || []).join('\n'))}</textarea>`)}
    ${grField('Playbooks', `<input name="playbooks" value="${esc((b.playbooks || []).join(', '))}">`)}
    ${grField('What changed', '<input name="note" maxlength="200" autocomplete="off" required>')}`, 'Publish', async form => {
    const v = n => form.elements[n].value.trim();
    const note = v('note');
    if (!note) throw new Error('What changed');
    const primary = v('primary'), secondary = v('secondary');
    // Fields this form doesn't show (who buys, deal size) come from the latest read, so a retry after a 409 keeps them.
    const next = {...(gtBody(d).buyer || {}), who_uses: v('who_uses'), cycle: v('cycle'), objections: grLines(form.elements.objections.value)};
    const plays = v('playbooks').split(',').map(s => s.trim()).filter(Boolean);
    const sections = {motion: primary ? {primary, secondary: secondary && secondary !== primary ? secondary : null} : null,
      buyer: Object.values(next).some(x => Array.isArray(x) ? x.length : x) ? next : null, playbooks: plays.length ? plays : null};
    let out;
    try { out = await put('/v2/gtm/draft', {sections, revision: d?.draft?.revision ?? null}); }
    catch (e) {
      if (e?.status !== 409) throw e;
      d = await get('/v2/gtm', {fresh: true}).catch(() => d);
      throw new Error('Changed elsewhere. Publish again to replace it.');
    }
    if (out?.draft?.changed?.length) {
      const pub = await post('/v2/gtm/publish', {note});
      toast(`Published v${pub?.published?.n ?? ''}`.trim());
    }
    if (GR === state) void gsLoad(state);
  });
}
