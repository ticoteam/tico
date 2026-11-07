/* ui/app/segment-pages.ts — Segments in the Strategy section (backend/segments.py, docs/strategy.md#segments): the segment
   list on the Strategy page (ui/strategy-page.js), the segment page at #/strategy/segment/<id> (its strategy field by
   field, its bets, and its market), and the Market tab's compare view and per-segment market (ui/market-page.js). A
   segment's market is the one-page Market's five questions (ui/app/redesign-market.js) scoped to that segment, drawn
   by the same helpers. Every write goes through the API helpers (ui/app/api.js) and shows the server's text on failure.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/segment-pages.js. */
'use strict';

// One teammate on a segment's faces (backend/segments.py _face): a ref (`person:<id>` or `bot:<id>`), a name and a kind.
type SegFace = {ref: string; name: string; kind: string};
// A segment as the list shows it (GET /api/v2/segments): its id, name, who, color, rank, faces (at most five kept) and
// how many teammates are dedicated to it.
type SegItem = {id: string; name: string; who: string; color: string; rank: number; faces: SegFace[]; teammates: number};
// One strategy field the segment reads (GET /api/v2/segments/{id} strategy.fields): its text is the segment's own when it
// changed the field, else the company's (from says which).
type SegField = {key: string; title: string; text: string; company: string; changed: boolean; from: string};
type SegStrategy = {draft: boolean; revision: number | null; version: number | null; fields: SegField[]};
type SegBet = {id: string; statement: string; status: string};
// GET /api/v2/segments/{id}: the segment, its leads, its strategy and its live bets.
type SegDetail = SegItem & {leads: SegFace[]; strategy: SegStrategy; bets: SegBet[]};
// A segment's row in the compare table (GET /api/v2/market/segments/compare).
type SegCompare = SegItem & {money: number | null; yearly_spend: number | null; growth: number | null;
  our_share: number | null; briefing_week: string | null};
// An item of the newest briefings, tagged with its segment ('' is the whole market) and its week.
type SegChanged = {text: string; kind?: string; segment_id: string; week: string};
// GET /api/v2/market/segments/{id}/page: the segment's five answers, briefing, sizes, players and history.
type SegPage = {segment: {id: string; name: string; who: string; color: string; rank: number | null};
  answers: Record<string, string>; briefing: any; sizes: any; players: any[]; what_changed: any[]};
// What a Market view hands the router back: its shell and the calls the router makes on it.
type SegMount = {shell: HTMLElement; open: (at?: any) => void; stop: () => void};

const SEG_ROUTE = '#/strategy/segment/';
// The eight colours a segment can have (backend/models.py SegmentCreate). Each maps to an existing token in the stylesheet.
const SEG_COLOURS = ['blue', 'green', 'amber', 'red', 'purple', 'teal', 'pink', 'gray'];
const SEG_FACES = 5;
const SEG_STATUS: Record<string, string> = {active: 'Active', proposed: 'Proposed', retired: 'Retired'};
const SEG_ASK = 'Run the "Set up segments" job for this company.';

// The address of a segment page: `#/strategy/segment/<id>`, the id encoded (its slash is part of it).
const isSegmentRoute = (r: string): boolean => r.startsWith(SEG_ROUTE);
const segmentRouteId = (r: string): string => {
  const raw = r.slice(SEG_ROUTE.length).split('?')[0];
  try { return decodeURIComponent(raw); } catch { return raw; }
};
const segHref = (id: string): string => SEG_ROUTE + encodeURIComponent(id);
const segColour = (c: string): string => (SEG_COLOURS.includes(c) ? c : 'gray');
const segDot = (color: string): string => `<span class="seg-dot" data-seg-color="${esc(segColour(color))}" aria-hidden="true"></span>`;
const segErr = (error: any, fail: string): string => String(error?.message || fail);

// The faces: the first few teammates as avatars, then how many more are dedicated.
function segFacesHtml(faces: SegFace[], teammates: number): string {
  if (!faces?.length) return '';
  const shown = faces.slice(0, SEG_FACES), more = (teammates || 0) - shown.length;
  return `<span class="seg-faces" title="${esc(teammates)} teammates">${shown.map(f => `<span class="seg-face">${actorFace(f.ref, 22)}</span>`).join('')}${more > 0 ? `<span class="seg-more tnum">+${more}</span>` : ''}</span>`;
}

// ---------------------------------------------------------------- the list on the Strategy page
// What the list is doing: which row is being edited, whether the add row is open, which ⋯ menu is open. It lives while
// the same box is drawn; a new page draws a new box and starts clean.
let SEG_LIST: {box: HTMLElement | null; list: SegItem[]; owner: boolean; again: () => void; adding: boolean; editing: string} =
  {box: null, list: [], owner: false, again: () => {}, adding: false, editing: ''};

function segRowHtml(seg: SegItem, i: number, n: number, owner: boolean): string {
  const name = esc(seg.name);
  if (owner && SEG_LIST.editing === seg.id) {
    return `<li class="seg-row seg-editing" data-seg-id="${esc(seg.id)}"><form class="seg-form" data-seg-form novalidate>${segDot(seg.color)}
        <input type="text" name="name" maxlength="120" value="${esc(seg.name)}" aria-label="Name" required>
        <input type="text" name="who" maxlength="200" value="${esc(seg.who)}" aria-label="Who it is for" placeholder="Who it is for">
        <span class="seg-form-acts"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-seg-cancel>Cancel</button></span></form></li>`;
  }
  const acts = owner ? `<span class="seg-acts">
      <button class="seg-act" type="button" data-seg-edit aria-label="Edit ${name}" title="Edit">${SEG_PENCIL}</button>
      <button class="seg-act" type="button" data-seg-del aria-label="Remove ${name}" title="Remove">${SEG_X}</button>
      <span class="seg-more-wrap"><button class="seg-act" type="button" data-seg-menu aria-haspopup="menu" aria-expanded="false" aria-label="More for ${name}" title="More">⋯</button>
        <span class="seg-menu" role="menu" hidden>
          <button type="button" role="menuitem" data-seg-up${i ? '' : ' disabled'}>Move up</button>
          <button type="button" role="menuitem" data-seg-down${i < n - 1 ? '' : ' disabled'}>Move down</button>
        </span></span></span>` : '';
  return `<li class="seg-row" data-seg-id="${esc(seg.id)}">${segDot(seg.color)}
      <a class="seg-main" href="${segHref(seg.id)}"><b>${name}</b>${seg.who ? `<span class="seg-who">${esc(seg.who)}</span>` : ''}</a>
      ${segFacesHtml(seg.faces, seg.teammates)}${acts}</li>`;
}
const SEG_PENCIL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>';
const SEG_X = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function segmentListHtml(list: SegItem[], owner: boolean): string {
  const add = owner && !SEG_LIST.adding ? '<button class="strat-add" type="button" data-seg-add>+ Segment</button>' : '';
  const suggest = owner ? '<button class="ghost strat-mini" type="button" data-seg-suggest>Ask Assistant to suggest</button>' : '';
  const head = `<div class="strat-card-head"><h2 class="strat-k" id="strat-segs-h">Segments</h2>${add}${suggest}</div>`;
  const form = owner && SEG_LIST.adding ? `<form class="seg-form seg-new" data-seg-new novalidate>
      <input type="text" name="name" maxlength="120" autocomplete="off" placeholder="Name" aria-label="Name" required>
      <input type="text" name="who" maxlength="200" autocomplete="off" placeholder="Who it is for (optional)" aria-label="Who it is for">
      <span class="seg-form-acts"><button class="primary" type="submit">Add</button><button class="ghost" type="button" data-seg-cancel>Cancel</button></span></form>` : '';
  const rows = list.map((seg, i) => segRowHtml(seg, i, list.length, owner)).join('');
  if (!rows) return `${head}${form}${form ? '' : '<p class="strat-none-s">None yet.</p>'}`;
  return `${head}${form}<ol class="seg-list" aria-labelledby="strat-segs-h">${rows}</ol>`;
}

// Draws the list into its card, then binds it. `list` undefined is still loading.
function segmentListPaint(box: HTMLElement, list: SegItem[] | null | undefined, owner: boolean, again: () => void): void {
  if (SEG_LIST.box !== box) SEG_LIST = {box, list: [], owner, again, adding: false, editing: ''};
  SEG_LIST.owner = owner;
  SEG_LIST.again = again;
  if (list === undefined) {
    patchHTML(box, '<div class="strat-card-head"><h2 class="strat-k">Segments</h2></div>' + skelRows(3, {dot: false}));
    return;
  }
  SEG_LIST.list = list || [];
  patchHTML(box, segmentListHtml(SEG_LIST.list, owner));
  segmentListBind(box);
}

// A write, then the list read again. A refusal (its Problem text) stays on screen as a toast; the form stays open.
async function segWrite(run: () => Promise<any>, fail: string): Promise<boolean> {
  try { await run(); }
  catch (error) { toast(segErr(error, fail), true); return false; }
  SEG_LIST.again();
  return true;
}

// Moves a segment one place in the list: the whole order is sent (POST /api/v2/segments/order).
async function segMove(id: string, by: number): Promise<void> {
  const ids = SEG_LIST.list.map(s => s.id), at = ids.indexOf(id), to = at + by;
  if (at < 0 || to < 0 || to >= ids.length) return;
  [ids[at], ids[to]] = [ids[to], ids[at]];
  await segWrite(() => post('/v2/segments/order', {ids}), 'Could not move it');
}

function segmentListBind(box: HTMLElement): void {
  const st = SEG_LIST;
  listenSegMenus();
  box.onclick = ev => {
    const target = ev.target as Element;
    const row = target.closest('[data-seg-id]') as HTMLElement | null;
    const id = row?.dataset.segId || '';
    const seg = st.list.find(s => s.id === id);
    if (target.closest('[data-seg-add]')) { st.adding = true; st.editing = ''; segmentListPaint(box, st.list, st.owner, st.again); box.querySelector<HTMLInputElement>('[data-seg-new] input')?.focus(); return; }
    if (target.closest('[data-seg-suggest]')) { askDraft(SEG_ASK); return; }
    if (target.closest('[data-seg-cancel]')) { st.adding = false; st.editing = ''; segmentListPaint(box, st.list, st.owner, st.again); return; }
    if (!seg) return;
    if (target.closest('[data-seg-edit]')) { st.editing = id; st.adding = false; segmentListPaint(box, st.list, st.owner, st.again); box.querySelector<HTMLInputElement>('[data-seg-form] input')?.focus(); return; }
    if (target.closest('[data-seg-del]')) {
      if (confirm(`Remove ${seg.name}? Its teammate tags go.`)) void segWrite(() => post('/v2/segments/remove', {id}), 'Could not remove it');
      return;
    }
    const menu = target.closest('[data-seg-menu]');
    if (menu) {
      const pop = menu.parentElement?.querySelector<HTMLElement>('.seg-menu');
      const open = !!pop?.hidden;
      document.querySelectorAll<HTMLElement>('.seg-menu').forEach(m => { m.hidden = true; });
      if (pop) pop.hidden = !open;
      menu.setAttribute('aria-expanded', String(open));
      return;
    }
    if (target.closest('[data-seg-up]')) { void segMove(id, -1); return; }
    if (target.closest('[data-seg-down]')) { void segMove(id, 1); }
  };
  box.onsubmit = ev => {
    ev.preventDefault();
    const form = ev.target as HTMLFormElement;
    const field = (n: string): HTMLInputElement | null => form.querySelector<HTMLInputElement>(`input[name="${n}"]`);
    const name = (field('name')?.value || '').replace(/\s+/g, ' ').trim(), who = (field('who')?.value || '').replace(/\s+/g, ' ').trim();
    if (!name) { field('name')?.focus(); return; }
    if (form.hasAttribute('data-seg-new')) {
      // The new one is added and the form closes; a refusal keeps it open with the text in it.
      void segWrite(async () => { await post('/v2/segments', {name, who}); st.adding = false; }, 'Could not add it');
      return;
    }
    // The edit is saved and the row closes; the id is the row's, and a slash in it is part of the path.
    const id = form.closest<HTMLElement>('[data-seg-id]')?.dataset.segId || '';
    void segWrite(async () => { await post('/v2/segments/' + id, {name, who}); st.editing = ''; }, 'Could not save it');
  };
  box.onkeydown = ev => {
    if (ev.key !== 'Escape' || (!st.adding && !st.editing)) return;
    st.adding = false; st.editing = '';
    segmentListPaint(box, st.list, st.owner, st.again);
  };
}
// A ⋯ menu closes on a click anywhere else (one listener for the page, not one per draw).
let SEG_MENU_LISTENING = false;
function listenSegMenus(): void {
  if (SEG_MENU_LISTENING) return;
  SEG_MENU_LISTENING = true;
  document.addEventListener('click', closeSegMenus, {capture: true});
}
function closeSegMenus(ev: Event): void {
  const target = ev.target as Element;
  if (target.closest?.('.seg-more-wrap')) return;
  document.querySelectorAll<HTMLElement>('.seg-menu').forEach(m => { if (!m.hidden) { m.hidden = true; m.parentElement?.querySelector('[data-seg-menu]')?.setAttribute('aria-expanded', 'false'); } });
}

// ---------------------------------------------------------------- the segment page
let SEG_PAGE_LOAD = 0;
// The open segment page: its strategy field being edited (a key), so a redraw keeps it.
let SEG_PAGE_EDIT = '';

// Leaving the segment page: a read that lands after is dropped.
window.segmentPagesStop = function segmentPagesStop(): void {
  SEG_PAGE_LOAD++;
  SEG_PAGE_EDIT = '';
};

window.pageSegment = async function pageSegment(): Promise<void> {
  const load = ++SEG_PAGE_LOAD, id = segmentRouteId(S.route);
  const main = $('#main');
  main.classList.add('strategy-layout');
  main.innerHTML = strategyHeadHtml('strategy') + `<div id="seg-body" class="seg-page">${skelRows(6, {dot: false})}</div>`;
  let seg: SegDetail;
  try { seg = await get('/v2/segments/' + id, {fresh: true}); }
  catch (error) {
    if (load === SEG_PAGE_LOAD && $('#seg-body')) $('#seg-body').innerHTML = `<p class="err">${esc(segErr(error, 'Could not load the segment'))}</p>`;
    return;
  }
  if (load !== SEG_PAGE_LOAD) return;
  segPageDraw(seg, load);
};

function segPageDraw(seg: SegDetail, load: number): void {
  const body = $('#seg-body');
  if (!body) return;
  body.innerHTML = `${segHeadHtml(seg)}<div id="seg-strategy-box">${segStrategyHtml(seg)}</div>${segBetsHtml(seg)}<section class="seg-market mkt-host seg-mkt-host" id="seg-market" aria-label="Market"></section>`;
  segBindStrategy(seg, load);
  void segMarketFill($('#seg-market'), seg.id, load);
}

function segHeadHtml(seg: SegDetail): string {
  const id = encodeURIComponent(seg.id);
  const links: [string, string][] = [[RD_GOALS, 'Goals'], [GR_ROUTE, 'Growth'], [RD_BUDGET, 'Budget'], [FIN_ROUTE + '/pnl', 'P&L']];
  return `<header class="seg-head">
      <div class="seg-title">${segDot(seg.color)}<h2>${esc(seg.name)}</h2></div>
      ${seg.who ? `<p class="seg-who-l">${esc(seg.who)}</p>` : ''}
      <div class="seg-meta">${segFacesHtml(seg.faces, seg.teammates)}
        <nav class="seg-links" aria-label="Segment's pages">${links.map(([base, label]) =>
          `<a href="${esc(base)}?segment=${id}">${label}</a>`).join('')}</nav></div></header>`;
}

// The strategy: each field, the company's own in grey until the segment changes it. The owner edits in place.
function segStrategyHtml(seg: SegDetail): string {
  const owner = !!seg.strategy?.draft;
  const rows = (seg.strategy?.fields || []).filter(f => owner || stratText(f.text)).map(f => segFieldHtml(f, owner)).join('');
  return `<section class="card strat-card seg-strat" id="seg-strategy" aria-label="Strategy">
      <div class="strat-card-head"><h2 class="strat-k">Strategy</h2></div>${rows || '<p class="muted strat-empty">Nothing written for this segment yet.</p>'}</section>`;
}
function segFieldHtml(f: SegField, owner: boolean): string {
  const editing = SEG_PAGE_EDIT === f.key;
  const label = f.changed ? 'This segment' : 'From company';
  const acts = !owner || editing ? '' : f.changed
    ? `<button class="ghost strat-mini" type="button" data-seg-field-edit="${esc(f.key)}">Edit</button><button class="ghost strat-mini" type="button" data-seg-field-use="${esc(f.key)}">Use company's</button>`
    : `<button class="ghost strat-mini" type="button" data-seg-field-edit="${esc(f.key)}">Change for this segment</button>`;
  const body = editing
    ? `<form class="seg-ed-form" data-seg-field-form="${esc(f.key)}" novalidate>
        <textarea name="text" rows="6" aria-label="${esc(f.title)}">${esc(f.text)}</textarea>
        <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-seg-field-cancel>Cancel</button></div></form>`
    : stratText(f.text) ? `<div class="md strat-md${f.changed ? '' : ' seg-company'}">${safeMd(f.text)}</div>`
    : '<p class="muted strat-empty">Not written</p>';
  return `<div class="seg-field" data-seg-field="${esc(f.key)}">
      <div class="seg-field-h"><h3 class="strat-k">${esc(f.title)}</h3><span class="seg-from${f.changed ? ' own' : ''}">${label}</span><span class="spacer"></span>${acts}</div>
      ${body}</div>`;
}

// The segment's live bets, read only (the bets are edited on the Strategy page).
function segBetsHtml(seg: SegDetail): string {
  const bets = seg.bets || [];
  if (!bets.length) return '';
  return `<section class="card strat-card seg-bets" aria-label="Bets"><div class="strat-card-head"><h2 class="strat-k">Bets</h2></div>
      <ol class="seg-bet-list">${bets.map(b => `<li><a href="#/strategy?bet=${encodeURIComponent(b.id)}">${esc(b.statement)}</a>
        <span class="pill strat-pill ${esc(b.status)}">${esc(SEG_STATUS[b.status] || b.status)}</span></li>`).join('')}</ol></section>`;
}

// Writes one field of the segment's strategy (PUT /api/v2/segments/strategy, with the draft revision it was read at).
// "" gives the company's text back. A changed draft (409) is read again whole.
async function segWriteField(seg: SegDetail, load: number, key: string, text: string): Promise<void> {
  try {
    const out = await put('/v2/segments/strategy', {id: seg.id, revision: seg.strategy?.revision ?? null, sections: {[key]: text}});
    if (load !== SEG_PAGE_LOAD) return;
    SEG_PAGE_EDIT = '';
    const next: SegDetail = {...seg, strategy: out?.strategy || seg.strategy};
    segPageRefresh(next, load);
  } catch (error: any) {
    if (load !== SEG_PAGE_LOAD) return;
    if (error?.status === 409) {
      toast('Changed elsewhere, reloaded');
      SEG_PAGE_EDIT = '';
      segPageReload(seg.id, load);
      return;
    }
    toast(segErr(error, 'Could not save it'), true);
  }
}
// Draws the strategy card again, keeping the rest of the page.
function segPageRefresh(seg: SegDetail, load: number): void {
  if (load !== SEG_PAGE_LOAD) return;
  const box = $('#seg-strategy-box');
  if (!box) return;
  box.innerHTML = segStrategyHtml(seg);
  segBindStrategy(seg, load);
}
async function segPageReload(id: string, load: number): Promise<void> {
  try {
    const seg = await get('/v2/segments/' + id, {fresh: true});
    if (load === SEG_PAGE_LOAD) segPageRefresh(seg, load);
  } catch (error) { toast(segErr(error, 'Could not load the segment'), true); }
}

function segBindStrategy(seg: SegDetail, load: number): void {
  const box: HTMLElement | null = $('#seg-strategy-box');
  if (!box) return;
  box.onclick = ev => {
    const target = ev.target as Element;
    const edit = target.closest<HTMLElement>('[data-seg-field-edit]');
    if (edit) { SEG_PAGE_EDIT = edit.dataset.segFieldEdit || ''; segPageRefresh(seg, load); box.querySelector<HTMLTextAreaElement>('textarea')?.focus(); return; }
    if (target.closest('[data-seg-field-cancel]')) { SEG_PAGE_EDIT = ''; segPageRefresh(seg, load); return; }
    const use = target.closest<HTMLElement>('[data-seg-field-use]');
    if (use) { void segWriteField(seg, load, use.dataset.segFieldUse || '', ''); }
  };
  const form = box.querySelector<HTMLFormElement>('[data-seg-field-form]');
  if (!form) return;
  const key = form.dataset.segFieldForm || '';
  const area = form.elements.namedItem('text') as HTMLTextAreaElement;
  const field = seg.strategy?.fields.find(f => f.key === key);
  form.onkeydown = ev => { if (ev.key === 'Escape') { SEG_PAGE_EDIT = ''; segPageRefresh(seg, load); } };
  form.onsubmit = ev => {
    ev.preventDefault();
    const text = area.value.trim();
    // Words the company already has are not a change for this segment; leaving them is the same as Cancel.
    if (text === stratText(field?.text) && field?.changed) { SEG_PAGE_EDIT = ''; segPageRefresh(seg, load); return; }
    if (text === stratText(field?.company) && !field?.changed) { SEG_PAGE_EDIT = ''; segPageRefresh(seg, load); return; }
    void segWriteField(seg, load, key, text);
  };
}

// ---------------------------------------------------------------- a segment's market
// The segment's market: the five questions of the Market page, scoped to the segment. Shared by the segment page and the
// Market tab's segment view. The owner sizes it: Edit sizes shows the estimates, and each can be accepted or its
// parameters changed (the same routes the map's size panel uses).
async function segMarketFill(el: HTMLElement | null, id: string, load: number | (() => boolean)): Promise<void> {
  const live = () => (typeof load === 'function' ? load() : true) && !!el?.isConnected;
  if (!el) return;
  el.innerHTML = `<div class="mkt-pg seg-mkt">${skelRows(3, {card: true, dot: false})}</div>`;
  let page: SegPage;
  try { page = await get('/v2/market/segments/' + id + '/page', {fresh: true}); }
  catch (error) { if (live()) el.innerHTML = `<p class="market-none">${esc(segErr(error, 'The market did not load.'))}</p>`; return; }
  if (!live()) return;
  segMarketDraw(el, id, page);
}

function segMarketDraw(el: HTMLElement, id: string, page: SegPage): void {
  const seg = page.segment || {id, name: id, who: '', color: 'gray', rank: null};
  const sizes = page.sizes || {}, chosen = sizes.chosen || null, estimates: any[] = sizes.estimates || [];
  const briefing = page.briefing || null, answers = page.answers || {}, players = page.players || [];
  const spend: number | null = chosen?.spend_usd ?? sizes.rolled_usd?.spend_usd ?? null;
  const revenue = rdqRevenue({segments: [{id: seg.id, parent: null, players}]});
  const ours = revenue.get(marketSelfId()) || 0;
  const owner = mktOwner();
  const tiles: string[] = [];
  if (spend != null) tiles.push(rdqTile('Yearly spend', mktMoney(spend), `${Number.isFinite(chosen?.growth_pct) ? rdqGrowth(chosen.growth_pct) + ' a year ' : ''}${chosen ? '' : 'Rolled up from its parts'}`));
  if (spend && ours) tiles.push(rdqTile(`${esc(companyName())} share`, rdqPct(100 * ours / spend), `${mktMoney(ours)} of ${mktMoney(spend)}`));
  const sections: string[] = [];
  if (briefing) sections.push(rdqSection('changed', 'What changed?', answers.changed || '',
    `${esc(rdbDate(briefing))} · <button class="linkish" type="button" data-rdb-read>Read</button>`, rdqChangedHtml(briefing)));
  if (spend != null || estimates.length) {
    const money = chosen ? `<div class="mkt-card seg-money"><b>${mktMoney(spend)}</b>${chosen.low_usd != null ? ` <small>${mktRange(chosen)}</small>` : ''}</div>` : '';
    const edit = owner && estimates.length ? '<button class="linkish" type="button" data-seg-sizes aria-expanded="false" aria-controls="seg-sizes">Edit sizes</button>' : '';
    const ests = estimates.length ? `<ul class="mkt-ests" id="seg-sizes" hidden>${estimates.map(e => mktEstimateHtml(e, {size: chosen})).join('')}</ul>` : '';
    sections.push(rdqSection('money', 'Where is the money going?', answers.money || '', edit, `${money}${ests}`));
  }
  if (players.length) sections.push(rdqSection('players', 'Who are we up against?', answers.players || '', '',
    rdqPlayersHtml({segments: [{id: seg.id, name: seg.name, players}], total: {spend_usd: spend}}, null, briefing, revenue)));
  const research = '<button class="ghost" type="button" data-seg-research>Ask Librarian to research</button>';
  el.innerHTML = `<div class="mkt-pg seg-mkt"><header class="mkt-top"><h2>Market</h2><span class="mkt-name">${esc(seg.name)}</span>
      <span class="spacer"></span>${research}</header>
    ${tiles.length ? `<div class="mkt-tiles">${tiles.join('')}</div>` : ''}${sections.join('') || '<p class="market-none">Nothing here yet.</p>'}</div>`;
  el.onclick = ev => {
    const target = ev.target as Element;
    if (target.closest('[data-rdb-read]')) { briefingOpen(briefing); return; }
    if (target.closest('[data-seg-research]')) { void segResearch(el, id); return; }
    if (target.closest('[data-seg-sizes]')) {
      const list = el.querySelector<HTMLElement>('#seg-sizes'), btn = target.closest('[data-seg-sizes]') as HTMLElement;
      if (list) list.hidden = !list.hidden;
      btn.setAttribute('aria-expanded', String(!!list && !list.hidden));
      return;
    }
    const accept = target.closest<HTMLElement>('[data-mkt-accept]');
    if (accept) { void segSizeWrite(el, id, post(`/v2/market/sizes/${encodeURIComponent(accept.dataset.mktAccept || '')}/accept`, {}), page); return; }
    const player = target.closest<HTMLElement>('[data-player]');
    if (player && !target.closest('a')) void rdqPlayerOpen(player.dataset.player || '');
  };
  el.onsubmit = ev => {
    const param = (ev.target as Element).closest<HTMLFormElement>('[data-mkt-param]');
    if (!param) return;
    ev.preventDefault();
    const input = param.elements.namedItem('value') as HTMLInputElement;
    const value = Number(input.value);
    if (input.value.trim() === '' || !Number.isFinite(value)) { toast('Enter a number', true); return; }
    void segSizeWrite(el, id, post(`/v2/market/sizes/${encodeURIComponent(param.dataset.size || '')}/params`, {params: {[param.dataset.name || '']: value}}), page);
  };
}

// A size write, then the segment's market read again (the page redraws with what the server now says).
async function segSizeWrite(el: HTMLElement, id: string, run: Promise<any>, page: SegPage): Promise<void> {
  try { await run; }
  catch (error) { toast(segErr(error, 'Could not save it'), true); return; }
  const fresh: SegPage = await get('/v2/market/segments/' + id + '/page', {fresh: true}).catch(() => page);
  if (el.isConnected) segMarketDraw(el, id, fresh);
}

// Asks the Librarian for the segment's five answers and sizes; an open task comes back as already asked.
async function segResearch(el: HTMLElement, id: string): Promise<void> {
  try {
    const out = await post('/v2/market/segments/' + id + '/research', {note: ''});
    toast(out?.created === false ? 'Already on it' : 'The Librarian is on it');
  } catch (error) { toast(segErr(error, 'Could not ask the Librarian'), true); }
}

// ---------------------------------------------------------------- the Market tab
// The chips that move between the compare view and one segment's market.
function segChipsHtml(active: string, segs: SegCompare[]): string {
  return `<nav class="seg-chips" aria-label="Segments">
    <a class="seg-chip${active === 'all' ? ' cur' : ''}" href="${MARKET_ROUTE}"${active === 'all' ? ' aria-current="page"' : ''}>All segments</a>
    ${segs.map(s => `<a class="seg-chip${active === s.id ? ' cur' : ''}" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}"${active === s.id ? ' aria-current="page"' : ''}>${segDot(s.color)}${esc(s.name)}</a>`).join('')}
  </nav>`;
}
function segCompareHtml(segs: SegCompare[]): string {
  if (!segs.length) return '<p class="market-none">No segments yet.</p>';
  const rows = segs.map(s => `<tr><td><a class="seg-name" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}">${segDot(s.color)}${esc(s.name)}</a></td>
      <td class="mkt-num">${s.money != null ? mktMoney(s.money) : ''}</td><td class="mkt-num">${s.yearly_spend != null ? mktMoney(s.yearly_spend) : ''}</td>
      <td class="mkt-num seg-opt">${s.growth != null ? rdqGrowth(s.growth) : ''}</td><td class="mkt-num seg-opt">${s.our_share != null ? rdqPct(s.our_share) : ''}</td>
      <td class="seg-opt">${segFacesHtml(s.faces, s.teammates)}</td></tr>`).join('');
  return `<section class="mkt-q" aria-labelledby="seg-cmp-h"><div class="mkt-qh"><h3 id="seg-cmp-h">Segments</h3></div>
    <div class="mkt-card mkt-tbl"><table class="mkt-pl seg-cmp"><thead><tr><th>Segment</th><th>Money</th><th>Yearly spend</th><th class="seg-opt">Growth</th><th class="seg-opt">Our share</th><th class="seg-opt">Faces</th></tr></thead>
      <tbody>${rows}</tbody></table></div></section>`;
}
// What changed across all the segments: each item tagged with its segment (All for the whole market).
function segChangedHtml(changed: SegChanged[], segs: SegCompare[]): string {
  if (!changed.length) return '';
  const by = new Map(segs.map(s => [s.id, s]));
  const items = changed.map(c => {
    const s = by.get(c.segment_id);
    const tag = s ? `<a class="seg-tag" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}">${segDot(s.color)}${esc(s.name)}</a>` : '<span class="seg-tag">All</span>';
    return `<li>${tag}<span class="mkt-it">${esc(c.text)}</span></li>`;
  }).join('');
  return rdqSection('changed', 'What changed across all', '', '', `<ul class="mkt-card mkt-items">${items}</ul>`);
}

// The Market tab's compare view: the segments side by side, what changed across them, and the chips to each segment's market.
async function mountMarketCompare(shell: HTMLElement): Promise<SegMount> {
  const data = await get('/v2/market/segments/compare', {fresh: true});
  const segs: SegCompare[] = data?.segments || [], changed: SegChanged[] = data?.changed || [];
  shell.innerHTML = `<div class="mkt-pg seg-mkt">${segTopHtml()}${segChipsHtml('all', segs)}${segCompareHtml(segs)}${segChangedHtml(changed, segs)}</div>`;
  return {shell, open: () => { shell.scrollTop = 0; }, stop: () => {}};
}

// One segment's market in the Market tab: the chips, then the segment's five questions.
async function mountSegmentMarketView(shell: HTMLElement, id: string): Promise<SegMount> {
  const data = await get('/v2/market/segments/compare', {fresh: true}).catch(() => null);
  const segs: SegCompare[] = data?.segments || [];
  shell.innerHTML = `<div class="mkt-pg seg-mkt">${segTopHtml()}${segChipsHtml(id, segs)}<div id="seg-mkt-body"></div></div>`;
  const body = shell.querySelector<HTMLElement>('#seg-mkt-body');
  const load = ++MARKET_SEG_LOAD;
  if (body) await segMarketFill(body, id, () => shell.isConnected && MARKET_SEG_LOAD === load);
  return {shell, open: () => { shell.scrollTop = 0; }, stop: () => { MARKET_SEG_LOAD++; }};
}
let MARKET_SEG_LOAD = 0;

// The Market tab's links: the whole market's page and its map (the graph), and the Segments page's address.
function segTopHtml(): string {
  return `<header class="mkt-top"><h2>Market</h2><span class="spacer"></span><a class="ghost" href="${MARKET_ROUTE}?view=whole">Whole market</a><a class="ghost" href="${MARKET_MAP}">Open map</a></header>`;
}
