/* ui/app/growth-segments.js — Growth › Segments (#/growth/segments): the customer segments the company serves, tests
   and plans next (GET /api/v2/growth/segments), grouped by status with Dropped folded. A segment's name is its
   market-graph segment, so Serving and Next are what Strategy's Where we play shows as Us and Next. The owner or an
   admin adds one (POST, the name picked from the market's segments or a new one), moves it between statuses and edits
   its motion, buyer and deal size in place, the rest in its dialog (PATCH /api/v2/growth/segments/{id}); the server
   decides who may.
   Also the segment pieces the Channels tab, the idea form and Go-to-market's channels share.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GS_STATUS = [['serving', 'Serving', 'ok'], ['testing', 'Testing', 'in-progress'], ['next', 'Next', 'waiting'], ['dropped', 'Dropped', '']];

// ----------------------------------------------------------------- shared
// The segments list, or [] when it does not load (an install whose server predates segments).
const gsRead = (fresh = false) => get('/v2/growth/segments', fresh ? {fresh: true} : undefined).then(r => r.segments || []).catch(() => []);
// A segment by its own id or its market segment's id: records name either.
const gsFind = (segs, id) => (segs || []).find(s => s.id === id || s.segment_id === id);
const gsName = (segs, id) => gsFind(segs, id)?.name || String(id || '').replace(/^segment\//, '');
const gsTags = (segs, ids) => (ids || []).map(id => `<span class="gr-tag gs-tag">${esc(gsName(segs, id))}</span>`).join('');
// What a picker offers: the segments not dropped, plus any the record already names, so saving never drops one.
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
// A segments fieldset for a form, or '' with nothing to pick; `gsPicked` reads it, undefined when absent.
const gsChecks = (segs, on = []) => {
  const pairs = gsPairs(segs, on);
  return pairs.length ? `<fieldset class="gr-fs" data-gs-pick><legend>Segments</legend>${grChecks('segments', pairs, gsIds(segs, on))}</fieldset>` : '';
};
const gsPicked = form => form.querySelector('[data-gs-pick]') ? grChecked(form, 'segments') : undefined;
const gsMotion = m => (typeof GT_MOTIONS !== 'undefined' && GT_MOTIONS.find(x => x[0] === m)?.[1]) || m || '';
const gsMotionOpts = v => grOpts((typeof GT_MOTIONS !== 'undefined' ? GT_MOTIONS : []).map(([id, label]) => [id, label]), v, '—');

// ----------------------------------------------------------------- the page
function pageGrowthSegments(state) { void gsLoad(state); }
async function gsLoad(state) {
  let r, chans;
  try {
    [r, chans] = await Promise.all([get('/v2/growth/segments', {fresh: true}), get('/v2/growth/channels').then(x => x.channels || []).catch(() => [])]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = {segs: r.segments || [], chans, may: r.may_edit ?? grAdmin()};
  gsDraw(state);
}
function gsRow(s, d) {
  const may = d.may, chans = (s.channels || []).map(slug => `<span class="gr-tag">${esc(grChanName(d.chans, slug))}</span>`).join('');
  const name = `<th scope="row"><span class="gs-n">${esc(s.name)}</span>${s.why ? `<span class="muted gr-sub">${esc(s.why)}</span>` : ''}</th>`;
  const text = (f, label, max) => may ? `<input class="gs-in" data-gs-f="${f}" aria-label="${esc(`${label}, ${s.name}`)}" maxlength="${max}" value="${esc(s[f] || '')}">` : esc(s[f] || '—');
  return `<tr data-gs-row="${esc(s.id)}">${name}
    ${may ? `<td><select class="gs-in" data-gs-f="status" aria-label="${esc(`Status, ${s.name}`)}">${grOpts(GS_STATUS.map(([v, l]) => [v, l]), s.status, null)}</select></td>` : ''}
    <td>${may ? `<select class="gs-in" data-gs-f="motion" aria-label="${esc(`Motion, ${s.name}`)}">${gsMotionOpts(s.motion)}</select>` : esc(gsMotion(s.motion) || '—')}</td>
    <td class="gs-buyer">${text('who_buys', 'Buyer', 300)}</td><td class="gs-deal">${text('deal_size', 'Deal size', 80)}</td>
    <td><span class="gs-chips">${chans}</span></td><td class="num">${esc(String(s.ideas ?? 0))}</td>
    ${may ? `<td><button class="ghost small" type="button" data-gs-edit="${esc(s.id)}">Edit</button></td>` : ''}</tr>`;
}
function gsTable(list, d) {
  // Fixed column widths, so the status groups' tables line up.
  const cols = [['seg', 1], ['status', d.may], ['motion', 1], ['buyer', 1], ['deal', 1], ['chans', 1], ['ideas', 1], ['edit', d.may]].filter(c => c[1]);
  return `<div class="gr-scroll"><table class="gr-t gs-t${d.may ? ' gs-may' : ''}"><colgroup>${cols.map(([c]) => `<col class="gs-c-${c}">`).join('')}</colgroup><thead><tr><th scope="col">Segment</th>${d.may ? '<th scope="col">Status</th>' : ''}
    <th scope="col">Motion</th><th scope="col">Buyer</th><th scope="col">Deal size</th><th scope="col">Channels</th><th scope="col" class="num">Ideas</th>
    ${d.may ? '<th scope="col"><span class="sr-only">Edit</span></th>' : ''}</tr></thead><tbody>${list.map(s => gsRow(s, d)).join('')}</tbody></table></div>`;
}
function gsDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  patchHTML($('#strat-actions'), d.may ? '<button class="primary" type="button" data-gs-new>+ Segment</button>' : '');
  const open = body.querySelector('details.gs-dropped')?.open;
  body.innerHTML = GS_STATUS.map(([st, label]) => {
    const list = d.segs.filter(s => s.status === st).sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0) || String(a.name).localeCompare(String(b.name)));
    if (!list.length) return '';
    const h = `<span class="gr-k">${esc(label)}</span><span class="muted gs-cnt">${list.length}</span>`;
    return st === 'dropped' ? `<details class="card gs-group gs-dropped" data-gs-group="dropped"${open ? ' open' : ''}><summary class="gs-h">${h}</summary>${gsTable(list, d)}</details>`
      : `<section class="card gs-group" data-gs-group="${st}" aria-label="${esc(label)}"><h3 class="gs-h">${h}</h3>${gsTable(list, d)}</section>`;
  }).join('');
  const add = $('#strat-actions [data-gs-new]');
  if (add) add.onclick = () => void gsNew(state);
  body.querySelectorAll('[data-gs-f]').forEach(el => { el.onchange = () => void gsSave(state, el); });
  body.querySelectorAll('[data-gs-edit]').forEach(b => { b.onclick = () => gsEdit(state, d.segs.find(s => s.id === b.dataset.gsEdit)); });
}
// One field saved in place; a status change moves the row to its group.
async function gsSave(state, el) {
  const id = el.closest('[data-gs-row]')?.dataset.gsRow, f = el.dataset.gsF, s = state.data?.segs.find(x => x.id === id);
  if (!s) return;
  const v = el.value.trim();
  if ((s[f] || '') === v) return;
  el.disabled = true;
  try {
    const out = await patch(`/v2/growth/segments/${encodeURIComponent(id)}`, {[f]: f === 'status' ? v : v || null});
    if (GR !== state) return;
    Object.assign(s, out?.segment || {[f]: v || null});
    toast('Saved');
    if (f === 'status') gsDraw(state); else el.disabled = false;
  } catch (e) {
    toast(e.message || 'Not saved', true);
    if (GR === state) void gsLoad(state);
  }
}
async function gsNew(state) {
  const market = await get('/v2/market/spend').then(r => r.segments || []).catch(() => []);
  if (GR !== state) return;
  const taken = new Set(state.data.segs.map(s => s.segment_id)), free = market.filter(m => !taken.has(m.id));
  grDialog('New segment', `${grField('Name', `<input name="name" maxlength="120" required autocomplete="off" list="gs-market">`)}
    <datalist id="gs-market">${free.map(m => `<option value="${esc(m.name)}"></option>`).join('')}</datalist>
    ${gsFields({status: 'testing'}, true)}`, 'Add', async form => {
    const name = form.elements.name.value.replace(/\s+/g, ' ').trim();
    if (!name) throw new Error('A name');
    const known = free.find(m => String(m.name).toLowerCase() === name.toLowerCase());
    const out = await post('/v2/growth/segments', {...(known ? {segment_id: known.id} : {name}), ...gsBody(form)});
    toast('Segment added');
    if (GR === state) void gsLoad(state);
    return out;
  });
}
function gsFields(s, fresh = false) {
  const statuses = GS_STATUS.filter(([v]) => !fresh || v !== 'dropped').map(([v, l]) => [v, l]);
  return `<div class="gr-pair">${grField('Status', `<select name="status">${grOpts(statuses, s.status, null)}</select>`)}
    ${grField('Motion', `<select name="motion">${gsMotionOpts(s.motion)}</select>`)}</div>
    <div class="gr-pair">${grField('Buyer', `<input name="who_buys" maxlength="300" value="${esc(s.who_buys || '')}">`)}
    ${grField('Deal size', `<input name="deal_size" maxlength="80" value="${esc(s.deal_size || '')}">`)}</div>
    ${grField('Owner', `<select name="owner_ref">${grOwnerOpts(s.owner_ref)}</select>`)}
    ${grField('Why', `<textarea name="why" rows="2" maxlength="1000">${esc(s.why || '')}</textarea>`)}`;
}
function gsBody(form, was = null) {
  const v = n => form.elements[n].value.trim();
  const body = {status: v('status'), motion: v('motion') || null, who_buys: v('who_buys') || null, deal_size: v('deal_size') || null,
    owner_ref: v('owner_ref') || null, why: v('why')};
  if (!was) return Object.fromEntries(Object.entries(body).filter(([k, x]) => k === 'status' || (x != null && x !== '')));
  return Object.fromEntries(Object.entries(body).filter(([k, x]) => (x ?? '') !== (was[k] ?? '')));
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
