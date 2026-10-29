/* ui/app/redesign-org.js — Strategy › Org (redesign PLAN §6 wave 6): the org chart, top down, the same chart on a phone
   (pinch and pan) and a desktop. The owner on top; under them the teammates outside groups and every top-level group,
   a group a dashed outline with its name on a tab that folds it. Inside a group the lead comes first and teammates hang
   under whoever they report to. Helpers (BotOps, the Librarian, Assistants, message bots) are off
   the chart. Tapping a teammate opens a sheet: their home group, and Move to… for the owner (any group) and a lead (the
   groups it leads), which moves them at once (PATCH /api/v2/groups/{id}). Anyone else sees the group only; org changes
   beyond that are the Strategy Bot's to suggest in words and to make when the person asks it (redesign CONTRACT §3).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RDO_FOLDED_KEY = 'tico.org.chart.folded';
let RDO = null;             // {groups: [GET /v2/groups rows], zoom, folded: Set of group ids}
let RDO_SEQ = 0;
const rdoFoldedLoad = () => { try { return new Set(JSON.parse(localStorage.getItem(RDO_FOLDED_KEY) || '[]')); } catch { return new Set(); } };
const rdoFoldedSave = () => { try { localStorage.setItem(RDO_FOLDED_KEY, JSON.stringify([...RDO.folded])); } catch {} };

// The groups with their leads: GET /v2/groups when it has answered (a list, or {groups} from an older hub), else the
// roster's copy, which carries no leads.
const rdoGroups = () => RDO?.groups || S.orgGroups || [];
const rdoGroup = id => rdoGroups().find(g => g.id === id) || (S.orgGroups || []).find(g => g.id === id) || null;
const rdoGroupName = id => id ? rdoGroup(id)?.name || id : 'No group';
const rdoRef = n => n.kind === 'person' ? 'person:' + n.id : 'bot:' + n.name;
const rdoKey = n => n.kind === 'group' ? 'g:' + n.id : n.kind === 'person' ? 'p:' + n.id : 'b:' + n.name;
const rdoName = n => n.kind === 'group' ? n.name : n.kind === 'person' ? (n.person?.name || n.id) : botDisplayName(n.name);
function rdoRefName(ref) {
  const [kind, id] = [String(ref).split(':')[0], String(ref).slice(String(ref).indexOf(':') + 1)];
  if (kind === 'person' || kind === 'human') return (S.people || []).find(p => p.id === id)?.name || id;
  return botDisplayName(id);
}
// A teammate's home group: the `team` its roster row carries (backend/groups.py).
function rdoHome(ref) {
  const id = ref.slice(ref.indexOf(':') + 1);
  return (ref.startsWith('person:') ? (S.people || []).find(p => p.id === id)?.team : (S.emps || []).find(e => e.name === id)?.team) || '';
}
const rdoLeadOf = ref => rdoGroups().filter(g => g.lead === ref).map(g => g.id);
// The leads of a group and of every group above it.
function rdoLeadsAbove(gid) {
  const out = [];
  for (let g = rdoGroup(gid), hops = 0; g && hops < 20; g = g.parent ? rdoGroup(g.parent) : null, hops++) if (g.lead) out.push(g.lead);
  return out;
}
// The groups this viewer may move a teammate of `from` into: every one (and No group) for the owner and bot admins,
// who edit groups in the sidebar; for a lead over `from`, the groups it leads and those below (backend/groups.py
// `_lead_move`); else none. The hub has the last word.
function rdoMoveTargets(from) {
  if (orgCanGroups()) return null;
  const me = 'person:' + (S.me?.id || ''), mine = g => rdoLeadsAbove(g).includes(me);
  return from && mine(from) ? rdoGroups().filter(g => mine(g.id)).map(g => g.id) : [];
}

// ----------------------------------------------------------------- the tree
// The sidebar's chart (orgTreeByParent: groups, reports_to) without helpers (the Strategy Bot too) and recruiting seats.
function rdoTree() {
  const by = orgTreeWithHelpers(orgTreeByParent()), out = {};
  for (const [k, kids] of Object.entries(by)) out[k] = kids.filter(n => n.kind !== 'seat' && !(n.kind === 'group' && n.helpers));
  return out;
}
function rdoTop(by) {
  const roots = (by[''] || []).filter(n => n.kind === 'person');
  return roots.find(n => n.id === S.me?.owner_id) || roots.find(n => n.person?.role === 'owner') || roots[0] || null;
}
// Teammates first (in the chart's order), then groups by their order; in a group its lead first.
function rdoSorted(kids, gid) {
  const lead = gid ? rdoGroup(gid)?.lead : '';
  const rank = n => n.kind === 'group' ? 2 : lead && rdoRef(n) === lead ? 0 : 1;
  return kids.map((n, i) => [n, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || (a[0].kind === 'group' && b[0].kind === 'group' ? (a[0].order || 0) - (b[0].order || 0) : 0) || a[1] - b[1]).map(x => x[0]);
}
function rdoCount(by, key, seen = new Set()) {
  if (seen.has(key) || seen.size > 2000) return 0;
  seen.add(key);
  return (by[key] || []).reduce((t, n) => t + (n.kind === 'group' ? 0 : 1) + rdoCount(by, rdoKey(n), seen), 0);
}

// ----------------------------------------------------------------- drawing
function rdoCard(n, {top = false} = {}) {
  const ref = rdoRef(n), name = rdoName(n);
  const lead = rdoLeadOf(ref).length > 0;
  const bot = n.kind === 'bot' ? (S.emps || []).find(e => e.name === n.name) : null;
  const role = top ? 'Owner' : lead ? 'Lead' : String(bot?.role || '').trim().length <= 24 ? String(bot?.role || '').trim() : '';
  const av = n.kind === 'person' ? personAvatar(n.person || {name}, 16) : avatar(n.name, 16);
  const cls = ['rdo-card', top && 'top', lead && 'lead'].filter(Boolean).join(' ');
  return `<button type="button" class="${cls}" data-rdo-ref="${esc(ref)}" aria-label="${esc(name)}" title="${esc(name)}"><b>${esc(name)}</b>
    <span class="rdo-r">${av}${role ? `<span class="rdo-rl">${esc(role)}</span>` : ''}</span></button>`;
}
// A teammate and, under it on a rail, whoever reports to it inside the same group.
function rdoNode(by, n, ctx, depth) {
  if (depth > 14 || ctx.seen.has(rdoKey(n))) return '';
  ctx.seen.add(rdoKey(n));
  if (n.kind === 'group') return rdoGroupBlock(by, n, ctx, depth);
  const kids = rdoSorted(by[rdoKey(n)] || [], '');
  const stack = kids.length ? `<ul class="rdo-stack">${kids.map(k => `<li class="${k.kind === 'group' ? 'is-g' : ''}">${rdoNode(by, k, ctx, depth + 1)}</li>`).join('')}</ul>` : '';
  return rdoCard(n, ctx) + stack;
}
function rdoGroupBlock(by, g, ctx, depth) {
  const folded = RDO.folded.has(g.id), kids = rdoSorted(by['g:' + g.id] || [], g.id);
  const count = rdoCount(by, 'g:' + g.id);
  const body = kids.map(k => `<div class="rdo-gi${k.kind === 'group' ? ' is-g' : ''}">${rdoNode(by, k, ctx, depth + 1)}</div>`).join('');
  return `<section class="rdo-g${folded ? ' folded' : ''}" data-rdo-group="${esc(g.id)}">
    <button type="button" class="rdo-tab" data-rdo-fold="${esc(g.id)}" aria-expanded="${folded ? 'false' : 'true'}" title="${esc(g.name)}"><span class="rdo-tn">${esc(g.name)}</span>${folded ? `<span class="rdo-n">${count}</span>` : ''}</button>
    <div class="rdo-gb">${body}</div></section>`;
}
function rdoChartHtml() {
  const by = rdoTree(), top = rdoTop(by);
  const ctx = {seen: new Set()};
  const row = top ? [...(by[rdoKey(top)] || []), ...(by[''] || []).filter(n => n !== top)] : (by[''] || []);
  if (top) ctx.seen.add(rdoKey(top));
  const cols = rdoSorted(row, '').map(n => rdoNode(by, n, ctx, 0)).filter(Boolean);
  if (!top && !cols.length) return '<p class="muted rd-empty">No one on the chart yet.</p>';
  return (top ? `<div class="rdo-top${cols.length ? ' has-row' : ''}">${rdoCard(top, {top: true})}</div>` : '')
    + (cols.length ? `<ul class="rdo-row">${cols.map(c => `<li>${c}</li>`).join('')}</ul>` : '');
}
function rdoDraw() {
  const cv = $('#rdo-cv'), pane = $('#rdo-pane');
  if (!cv || !pane) return;
  cv.innerHTML = rdoChartHtml();
  rdoFitHeight();
  if (RDO.zoom == null) {
    // First look: the whole width when it fits with names still readable (12px or more), else readable, centred on
    // the owner, and panned by dragging.
    cv.style.zoom = 1;
    const fit = (pane.clientWidth - 2) / Math.max(1, cv.scrollWidth);
    const name = parseFloat(getComputedStyle(cv.querySelector('.rdo-card b') || cv).fontSize) || 13;
    RDO.zoom = Math.max(0.85, 12 / name, Math.min(1, fit));
    rdoZoom(RDO.zoom);
    rdoCentre();
    return;
  }
  rdoZoom(RDO.zoom);
}
// On a phone the chart's frame runs down to the tab bar, so the page itself does not scroll.
function rdoFitHeight() {
  const pane = $('#rdo-pane');
  if (!pane) return;
  if (!matchMedia('(max-width:760px)').matches) { pane.style.height = ''; return; }
  const bar = $('#mobile-nav'), bottom = bar && bar.getClientRects().length ? bar.getBoundingClientRect().top : innerHeight;
  pane.style.height = Math.max(240, Math.floor(bottom - pane.getBoundingClientRect().top - 12)) + 'px';
}
// The owner's card in the middle of the frame, across.
function rdoCentre() {
  const pane = $('#rdo-pane'), top = $('#rdo-cv .rdo-card.top');
  if (!pane || !top) return;
  const a = top.getBoundingClientRect(), b = pane.getBoundingClientRect();
  pane.scrollLeft += (a.left + a.width / 2) - (b.left + b.width / 2);
}
function rdoZoom(z, at) {
  const cv = $('#rdo-cv'), pane = $('#rdo-pane');
  if (!cv || !pane) return;
  const old = Number(cv.style.zoom) || 1;
  z = Math.max(0.35, Math.min(1.6, z));
  // Keep the point under the fingers (or the pane's middle) where it was.
  const mx = at ? at.x : pane.clientWidth / 2, my = at ? at.y : pane.clientHeight / 2;
  const cx = (pane.scrollLeft + mx) / old, cy = (pane.scrollTop + my) / old;
  RDO.zoom = z;
  cv.style.zoom = z;
  pane.scrollLeft = cx * z - mx; pane.scrollTop = cy * z - my;
}

// ----------------------------------------------------------------- the page
async function pageRdOrg() {
  const main = $('#main'), seq = ++RDO_SEQ;
  main.classList.add('strategy-layout', 'rd-org-layout');
  RDO ||= {groups: null, zoom: null, folded: rdoFoldedLoad()};
  RDO.zoom = null;
  main.innerHTML = strategyHeadHtml('org') + `<div class="rdo-wrap"><div class="rdo-pane" id="rdo-pane"><div class="rdo-cv rd-org" id="rdo-cv"></div></div>
      <div class="rdo-zoom" role="group" aria-label="Zoom"><button type="button" class="ghost" data-rdo-zoom="out" aria-label="Zoom out">−</button>
        <button type="button" class="ghost" data-rdo-zoom="in" aria-label="Zoom in">+</button></div></div>`;
  rdoWire($('#rdo-pane'));
  window.removeEventListener('resize', rdoFitHeight); window.addEventListener('resize', rdoFitHeight);
  rdoDraw();
  // The groups with their leads.
  const groups = await get('/v2/groups').catch(() => null);
  if (seq !== RDO_SEQ || !$('#rdo-cv')) return;
  if (groups) RDO.groups = Array.isArray(groups) ? groups : groups.groups || [];
  rdoDraw();
}
// After a move: the roster (homes) and the groups (leads) again.
async function rdoReload() {
  await refresh(true);
  const groups = await get('/v2/groups', {fresh: true}).catch(() => null);
  if (groups) RDO.groups = Array.isArray(groups) ? groups : groups.groups || [];
  if ($('#rdo-cv')) rdoDraw();
}

function rdoWire(pane) {
  // Pinch to zoom; one finger pans the pane natively (touch-action: pan-x pan-y).
  let pinch = null;
  const mid = (t, box) => ({x: (t[0].clientX + t[1].clientX) / 2 - box.left, y: (t[0].clientY + t[1].clientY) / 2 - box.top});
  const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  pane.addEventListener('touchstart', ev => { if (ev.touches.length === 2) pinch = {d: dist(ev.touches), z: RDO.zoom}; }, {passive: true});
  pane.addEventListener('touchmove', ev => {
    if (!pinch || ev.touches.length !== 2) return;
    ev.preventDefault();
    rdoZoom(pinch.z * dist(ev.touches) / pinch.d, mid(ev.touches, pane.getBoundingClientRect()));
  }, {passive: false});
  pane.addEventListener('touchend', ev => { if (ev.touches.length < 2) pinch = null; });
  // A mouse drags the chart about (a finger pans natively); a drag is not a tap on the card it started on.
  let drag = null;
  pane.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'mouse' || ev.button !== 0) return;
    drag = {x: ev.clientX, y: ev.clientY, left: pane.scrollLeft, top: pane.scrollTop, moved: false};
  });
  pane.addEventListener('pointermove', ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true; pane.classList.add('dragging');
    pane.scrollLeft = drag.left - dx; pane.scrollTop = drag.top - dy;
  });
  const end = () => { if (drag?.moved) { pane.classList.remove('dragging'); pane.dataset.dragged = '1'; setTimeout(() => delete pane.dataset.dragged, 0); } drag = null; };
  pane.addEventListener('pointerup', end);
  pane.addEventListener('pointerleave', end);
  pane.addEventListener('click', ev => { if (pane.dataset.dragged) { ev.stopPropagation(); ev.preventDefault(); } }, true);
  // A trackpad's pinch arrives as a wheel with ctrlKey.
  pane.addEventListener('wheel', ev => {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    const box = pane.getBoundingClientRect();
    rdoZoom(RDO.zoom * Math.exp(-ev.deltaY / 200), {x: ev.clientX - box.left, y: ev.clientY - box.top});
  }, {passive: false});
}

document.addEventListener('click', ev => {
  if (!ev.target.closest?.('.rd-org-layout')) return;
  const fold = ev.target.closest('[data-rdo-fold]');
  if (fold) {
    const id = fold.dataset.rdoFold;
    RDO.folded.has(id) ? RDO.folded.delete(id) : RDO.folded.add(id);
    rdoFoldedSave(); rdoDraw();
    $(`[data-rdo-fold="${CSS.escape(id)}"]`)?.focus();
    return;
  }
  const zoom = ev.target.closest('[data-rdo-zoom]');
  if (zoom) { rdoZoom(RDO.zoom * (zoom.dataset.rdoZoom === 'in' ? 1.25 : 0.8)); return; }
  const card = ev.target.closest('[data-rdo-ref]');
  if (card) rdoSheet(card.dataset.rdoRef);
});

// Move to…: the groups `allowed` names (all of them when null) in chart order, nested ones indented; No group for those
// who may move anyone.
function rdoGroupOptions(current, allowed) {
  const groups = S.orgGroups || [], out = [];
  const walk = (parent, depth) => groups.filter(g => (g.parent || '') === parent).sort((a, b) => (a.order || 0) - (b.order || 0)).forEach(g => {
    if (depth > 10 || out.some(([id]) => id === g.id)) return;
    out.push([g.id, '\u00a0'.repeat(depth * 2) + g.name]); walk(g.id, depth + 1);
  });
  walk('', 0);
  for (const g of groups) if (!out.some(([id]) => id === g.id)) out.push([g.id, g.name]);
  const shown = allowed ? out.filter(([id]) => allowed.includes(id)) : [['', 'No group'], ...out];
  return shown.map(([id, label]) => `<option value="${esc(id)}"${id === current ? ' selected' : ''}>${esc(label)}</option>`).join('');
}
function rdoSheet(ref) {
  const name = rdoRefName(ref), from = rdoHome(ref), led = rdoLeadOf(ref), targets = rdoMoveTargets(from);
  const may = !targets || targets.length > 0;
  const id = ref.slice(ref.indexOf(':') + 1), href = ref.startsWith('person:') ? `#/person/${encodeURIComponent(id)}` : `#/bot/${encodeURIComponent(id)}`;
  const body = `<div class="bd-fields rdo-sheet">
      <div class="bd-field"><span>Group</span><span data-rdo-home>${esc(rdoGroupName(from))}${led.length ? ` · lead of ${esc(led.map(rdoGroupName).join(', '))}` : ''}</span></div>
      ${may ? `<label class="bd-field"><span>Move to…</span><select name="to">${rdoGroupOptions(from, targets)}</select></label>` : ''}
      <a class="rdo-open" href="${href}">Open ${esc(name)}</a></div>`;
  const dlg = useDialog(name, body, async form => {
    const to = form.to.value;
    if (to === from) throw new Error('Already in ' + rdoGroupName(to) + '.');
    await rdoMove(ref, from, to);
    toast(`${name} moved to ${rdoGroupName(to)}`);
  }, () => void rdoReload());
  dlg.classList.add('rd-sheet', 'rdo-dialog');
  const submit = dlg.querySelector('[type=submit]');
  if (may) submit.textContent = 'Move';
  else dlg.querySelector('.use-dialog-row').remove();
  dlg.querySelector('.rdo-open').onclick = () => dlg.close();
}
// The move itself: into a group (it leaves the one it was in), or out of its group.
async function rdoMove(ref, from, to) {
  const kind = ref.startsWith('person:') ? 'people' : 'bots', id = ref.slice(ref.indexOf(':') + 1);
  if (to) await patch(`/v2/groups/${encodeURIComponent(to)}`, {add: {[kind]: [id]}});
  else await patch(`/v2/groups/${encodeURIComponent(from)}`, {remove: {[kind]: [id]}});
}
