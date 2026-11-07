/* ui/app/segment-pages.ts — Segments in the Strategy section (backend/segments.py, docs/strategy.md#segments): the segment
   list on the Strategy page (ui/strategy-page.js), the segment page at #/strategy/segment/<id> (its strategy field by
   field, its bets, and its market), and the Market tab's compare view and per-segment market (ui/market-page.js). A
   segment's market is the one-page Market's five questions (ui/app/redesign-market.js) scoped to that segment, drawn
   by the same helpers. Every write goes through the API helpers (ui/app/api.js) and shows the server's text on failure.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/segment-pages.js. */
'use strict';

// A segment as the list shows it is SegRow, and a teammate on its faces is SegFace (ui/app/segment-tags.ts, shared).
// One strategy field the segment reads (GET /api/v2/segments/{id} strategy.fields): its text is the segment's own when it
// changed the field, else the company's (from says which).
type SegField = {key: string; title: string; text: string; company: string; changed: boolean; from: string};
type SegStrategy = {draft: boolean; revision: number | null; version: number | null; fields: SegField[]};
type SegBet = {id: string; statement: string; status: string};
// The market's segment page as the API types it (ui/types/api.d.ts, generated from backend/openapi_v2.py). An import()
// type keeps this file a classic script: an import statement would make it a module, and the other scripts could not see it.
type MktPage = import('../types/api').MarketSegmentPage;
// The sizes the page reads (GET /api/v2/market/segments/{id}/page). The generated MarketSegmentSizes types the chosen size as
// accepted and spend only, so the range and growth the page draws are typed here.
type SegSize = {accepted?: boolean; spend_usd?: number | null; low_usd?: number | null; high_usd?: number | null; growth_pct?: number | null};
type SegSizes = {chosen?: SegSize | null; rolled_usd?: {spend_usd?: number | null} | null; estimates?: Array<Record<string, unknown>>};
// GET /api/v2/segments/{id}: the segment, its leads, its strategy and its live bets.
type SegDetail = SegRow & {leads: SegFace[]; strategy: SegStrategy; bets: SegBet[]};
// A segment's row in the compare table (GET /api/v2/market/segments/compare). The rows carry no faces: the compare view
// joins them from the segments list (GET /api/v2/segments), by id.
type SegCompareRow = {id: string; name: string; color: string; money: number | null; yearly_spend: number | null;
  growth: number | null; our_share: number | null; briefing_week: string | null};
type SegCompare = SegCompareRow & {faces: SegFace[]; teammates: number};
// An item of the newest briefings, tagged with its segment ('' is the whole market) and its week.
type SegChanged = {text: string; kind?: string; segment_id: string; week: string};
// GET /api/v2/market/segments/{id}/page: the segment's five answers, briefing, sizes, players and history.
type SegPage = {segment: {id: string; name: string; who: string; color: string; rank: number | null};
  answers: Record<string, string>; briefing: MktPage['briefing']; sizes: SegSizes; players: MktPage['players']; what_changed: MktPage['what_changed'];
  // The company's share of the segment's spend, in percent.
  our_share: number | null};
// What a Market view hands the router back: its shell and the calls the router makes on it.
type SegMount = {shell: HTMLElement; open: (at?: unknown) => void; stop: () => void};

const SEG_ROUTE = '#/strategy/segment/';
const SEG_FACES = 5;
const SEG_STATUS: Record<string, string> = {active: 'Active', proposed: 'Proposed', retired: 'Retired'};
const SEG_ASK = 'Run the "Set up segments" job for this company.';

// The address of a segment page: `#/strategy/segment/<id>`, the id encoded (its slash is part of it).
const isSegmentRoute = (r: string): boolean => r.startsWith(SEG_ROUTE);
const segmentRouteId = (r: string): string => {
  const raw = r.slice(SEG_ROUTE.length).split('?')[0] ?? '';
  try { return decodeURIComponent(raw); } catch { return raw; }
};
const segHref = (id: string): string => SEG_ROUTE + encodeURIComponent(id);
const segErr = (error: unknown, fail: string): string => String((error as {message?: string} | null)?.message || fail);

// The faces: the first few teammates as avatars, then how many more are dedicated.
function segFacesHtml(faces: SegFace[], teammates: number): string {
  if (!faces?.length) return '';
  const shown = faces.slice(0, SEG_FACES), more = (teammates || 0) - shown.length;
  return `<span class="seg-faces" title="${esc(teammates)} teammates">${shown.map(f => `<span class="seg-face">${actorFace(f.ref, 22)}</span>`).join('')}${more > 0 ? `<span class="seg-faces-more tnum">+${more}</span>` : ''}</span>`;
}

// ---------------------------------------------------------------- the list on the Strategy page
// What the list is doing: which row is being edited, whether the add row is open, which ⋯ menu is open. It lives while
// the same box is drawn; a new page draws a new box and starts clean.
let SEG_LIST: {box: HTMLElement | null; list: SegRow[]; owner: boolean; again: () => void; adding: boolean; editing: string} =
  {box: null, list: [], owner: false, again: () => {}, adding: false, editing: ''};

function segRowHtml(seg: SegRow, owner: boolean): string {
  const name = esc(seg.name);
  if (owner && SEG_LIST.editing === seg.id) {
    return `<li class="seg-row seg-editing" data-seg-id="${esc(seg.id)}"><form class="seg-form" data-seg-form novalidate>${segDot(seg)}
        <input type="text" name="name" maxlength="120" value="${esc(seg.name)}" aria-label="Name" required>
        <input type="text" name="who" maxlength="200" value="${esc(seg.who)}" aria-label="Who it is for" placeholder="Who it is for">
        <span class="seg-form-acts"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-seg-cancel>Cancel</button></span></form></li>`;
  }
  const acts = owner ? `<span class="seg-acts">
      <button class="seg-act" type="button" data-seg-edit aria-label="Edit ${name}" title="Edit">${SEG_PENCIL}</button>
      <button class="seg-act" type="button" data-seg-del aria-label="Remove ${name}" title="Remove">${SEG_X}</button>
      <button class="seg-act" type="button" data-seg-menu aria-haspopup="menu" aria-label="More for ${name}" title="More">⋯</button></span>` : '';
  return `<li class="seg-row" data-seg-id="${esc(seg.id)}">${segDot(seg)}
      <a class="seg-main" href="${segHref(seg.id)}"><b>${name}</b>${seg.who ? `<span class="seg-who">${esc(seg.who)}</span>` : ''}</a>
      ${segFacesHtml(seg.faces, seg.teammates)}${acts}</li>`;
}
const SEG_PENCIL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>';
const SEG_X = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

// The list's card: its head (the add button and the suggestion, one style), the add row when open, then the segments. A
// failed read (`failed`, its text) shows where the list would be, and the add button stays.
function segmentListHtml(list: SegRow[], owner: boolean, failed = ''): string {
  const add = owner && !SEG_LIST.adding ? '<button class="strat-add" type="button" data-seg-add>+ Segment</button>' : '';
  const suggest = owner ? '<button class="strat-add" type="button" data-seg-suggest>Ask Assistant to suggest</button>' : '';
  // The two buttons share a group pinned to the right edge (strategy.css gives each .strat-add its own auto margin).
  const acts = add || suggest ? `<span class="seg-head-acts">${add}${suggest}</span>` : '';
  const head = `<div class="strat-card-head"><h2 class="strat-k" id="strat-segs-h">Segments</h2>${acts}</div>`;
  const form = owner && SEG_LIST.adding ? `<form class="seg-form seg-new" data-seg-new novalidate>
      <input type="text" name="name" maxlength="120" autocomplete="off" placeholder="Name" aria-label="Name" required>
      <input type="text" name="who" maxlength="200" autocomplete="off" placeholder="Who it is for" aria-label="Who it is for">
      <span class="seg-form-acts"><button class="primary" type="submit">Add</button><button class="ghost" type="button" data-seg-cancel>Cancel</button></span></form>` : '';
  if (failed) return `${head}${form}<p class="err">${esc(failed)}</p>`;
  const rows = list.map(seg => segRowHtml(seg, owner)).join('');
  if (!rows) return `${head}${form}${form ? '' : '<p class="strat-none-s">None yet.</p>'}`;
  return `${head}${form}<ol class="seg-list" aria-labelledby="strat-segs-h">${rows}</ol>`;
}

// Draws the list into its card, then binds it. `list` undefined is still loading.
function segmentListPaint(box: HTMLElement, list: SegRow[] | null | undefined, owner: boolean, again: () => void, failed = ''): void {
  if (SEG_LIST.box !== box) SEG_LIST = {box, list: [], owner, again, adding: false, editing: ''};
  SEG_LIST.owner = owner;
  SEG_LIST.again = again;
  if (list === undefined) {
    patchHTML(box, '<div class="strat-card-head"><h2 class="strat-k">Segments</h2></div>' + skelRows(3, {dot: false}));
    return;
  }
  // null is a read that failed: the list says why, rather than that there are none.
  SEG_LIST.list = list || [];
  patchHTML(box, segmentListHtml(SEG_LIST.list, owner, list === null ? failed || 'The segments did not load.' : ''));
  segmentListBind(box);
}

// A write in flight (the list's, or the segment page's): no other click is taken until it lands.
let SEG_WRITING = false;
// A write, then the list read again. A refusal (its Problem text) stays on screen as a toast; the form stays open.
async function segWrite(run: () => Promise<unknown>, fail: string): Promise<boolean> {
  if (SEG_WRITING) return false;
  SEG_WRITING = true;
  try { await run(); }
  catch (error) { toast(segErr(error, fail), true); return false; }
  finally { SEG_WRITING = false; }
  SEG_LIST.again();
  return true;
}

// Moves a segment one place in the list: the whole order is sent (POST /api/v2/segments/order).
async function segMove(id: string, by: number): Promise<void> {
  const ids = SEG_LIST.list.map(s => s.id), at = ids.indexOf(id), to = at + by;
  if (at < 0 || to < 0 || to >= ids.length) return;
  ids.splice(at, 1);
  ids.splice(to, 0, id);
  await segWrite(() => post('/v2/segments/order', {ids}), 'Could not move it');
}

function segmentListBind(box: HTMLElement): void {
  const st = SEG_LIST;
  box.onclick = ev => {
    if (SEG_WRITING) return;
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
    const menu = target.closest<HTMLElement>('[data-seg-menu]');
    if (menu) { segRowMenuOpen(menu, id); return; }
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
// The row's ⋯ menu: Move up and Move down, on the shared popover (segPopMount), which a press outside or Escape closes.
function segRowMenuOpen(anchor: HTMLElement, id: string): void {
  const ids = SEG_LIST.list.map(s => s.id), at = ids.indexOf(id);
  const el = document.createElement('div');
  el.className = 'seg-menu';
  el.setAttribute('role', 'menu');
  el.setAttribute('aria-label', 'More');
  el.innerHTML = `<button type="button" role="menuitem" class="seg-mi" data-seg-up${at > 0 ? '' : ' disabled'}><span>Move up</span></button>
    <button type="button" role="menuitem" class="seg-mi" data-seg-down${at < ids.length - 1 ? '' : ' disabled'}><span>Move down</span></button>`;
  el.addEventListener('click', ev => {
    const pick = (ev.target as Element).closest<HTMLButtonElement>('[data-seg-up],[data-seg-down]');
    if (!pick || pick.disabled) return;
    segPopClose();
    void segMove(id, pick.hasAttribute('data-seg-up') ? -1 : 1);
  });
  segPopMount(anchor, el);
  el.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus({preventScroll: true});
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
  // A field left open on the last segment is not carried to this one.
  SEG_PAGE_EDIT = '';
  const main = $('#main');
  main.classList.add('strategy-layout');
  main.innerHTML = strategyHeadHtml('segment') + `<div id="seg-body" class="seg-page">${skelRows(6, {dot: false})}</div>`;
  let seg: SegDetail, fin: {budget: boolean; pnl: boolean};
  try { [seg, fin] = await Promise.all([get('/v2/segments/' + id, {fresh: true}), finSegmentLinks(id)]); }
  catch (error) {
    if (load === SEG_PAGE_LOAD && $('#seg-body')) $('#seg-body').innerHTML = `<p class="err">${esc(segErr(error, 'Could not load the segment'))}</p>`;
    return;
  }
  if (load !== SEG_PAGE_LOAD) return;
  segPageDraw(seg, load, fin);
};

function segPageDraw(seg: SegDetail, load: number, fin: {budget: boolean; pnl: boolean}): void {
  const body = $('#seg-body');
  if (!body) return;
  body.innerHTML = `${segHeadHtml(seg, fin)}<div id="seg-strategy-box">${segStrategyHtml(seg)}</div>${segBetsHtml(seg)}<section class="seg-market mkt-host seg-mkt-host" id="seg-market" aria-label="Market"></section>`;
  segBindStrategy(seg, load);
  void segMarketFill($('#seg-market'), seg.id, () => load === SEG_PAGE_LOAD);
}

// The segment's head: its name, who it is for, its teammates, and the pages that hold its numbers. Budget and P&L show
// only where Finance shows them to this viewer (finSegmentLinks).
function segHeadHtml(seg: SegDetail, fin: {budget: boolean; pnl: boolean}): string {
  const id = encodeURIComponent(seg.id);
  const links: [string, string, string][] = [[RD_GOALS, 'Goals', 'goals'], [GR_ROUTE, 'Growth', 'growth']];
  if (fin.budget) links.push([FIN_ROUTE + '/budgets', 'Budget', 'budget']);
  if (fin.pnl) links.push([FIN_ROUTE + '/pnl', 'P&L', 'pnl']);
  return `<header class="seg-head">
      <div class="seg-title">${segDot(seg)}<h2>${esc(seg.name)}</h2></div>
      ${seg.who ? `<p class="seg-who-l">${esc(seg.who)}</p>` : ''}
      <div class="seg-meta">${segFacesHtml(seg.faces, seg.teammates)}
        <nav class="seg-links" aria-label="Segment's pages">${links.map(([base, label, key]) =>
          `<a href="${esc(base)}?segment=${id}" data-seg-link="${key}">${label}</a>`).join('')}</nav></div></header>`;
}

// The strategy: each field, the company's own in grey until the segment changes it. The owner edits in place.
function segStrategyHtml(seg: SegDetail): string {
  const owner = !!seg.strategy?.draft;
  const rows = (seg.strategy?.fields || []).filter(f => owner || stratText(f.text)).map(f => segFieldHtml(f, owner)).join('');
  return `<section class="card strat-card seg-strat" id="seg-strategy" aria-label="Strategy">
      <div class="strat-card-head"><h2 class="strat-k">Strategy</h2></div>${rows || '<p class="muted strat-empty">None yet</p>'}</section>`;
}
function segFieldHtml(f: SegField, owner: boolean): string {
  const editing = SEG_PAGE_EDIT === f.key;
  const label = f.changed ? 'This segment' : 'From company';
  const acts = !owner || editing ? '' : f.changed
    ? `<button class="linkish" type="button" data-seg-act="edit" data-seg-field-edit="${esc(f.key)}">Edit</button><button class="linkish" type="button" data-seg-act="use" data-seg-field-use="${esc(f.key)}">Use company's</button>`
    : `<button class="linkish" type="button" data-seg-act="change" data-seg-field-edit="${esc(f.key)}">Change for this segment</button>`;
  const body = editing
    ? `<form class="seg-ed-form" data-seg-field-form="${esc(f.key)}" novalidate>
        <textarea name="text" rows="6" aria-label="${esc(f.title)}">${esc(f.text)}</textarea>
        <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-seg-field-cancel>Cancel</button></div></form>`
    : stratText(f.text) ? `<div class="md strat-md${f.changed ? '' : ' seg-company'}">${safeMd(f.text)}</div>`
    : '<p class="muted strat-empty">Not written</p>';
  return `<div class="seg-field" data-seg-field="${esc(f.key)}">
      <div class="seg-field-h"><h3 class="strat-k">${esc(f.title)}</h3><span class="seg-from${f.changed ? ' own' : ''}" data-seg-from="${f.changed ? 'segment' : 'company'}">${label}</span>${acts ? `<span class="seg-field-acts">${acts}</span>` : ''}</div>
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
  if (SEG_WRITING) return;
  SEG_WRITING = true;
  try {
    const out = await put('/v2/segments/strategy', {id: seg.id, revision: seg.strategy?.revision ?? null, sections: {[key]: text}});
    if (load !== SEG_PAGE_LOAD) return;
    SEG_PAGE_EDIT = '';
    const next: SegDetail = {...seg, strategy: out?.strategy || seg.strategy};
    segPageRefresh(next, load);
  } catch (error) {
    if (load !== SEG_PAGE_LOAD) return;
    if ((error as ApiFailure | null)?.status === 409) {
      toast('Changed elsewhere, reloaded');
      SEG_PAGE_EDIT = '';
      segPageReload(seg.id, load);
      return;
    }
    toast(segErr(error, 'Could not save it'), true);
  } finally {
    SEG_WRITING = false;
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
    if (SEG_WRITING) return;
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
    if (SEG_WRITING) return;
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
async function segMarketFill(el: HTMLElement | null, id: string, current: () => boolean): Promise<void> {
  const live = () => current() && !!el?.isConnected;
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
  const sizes: SegSizes = page.sizes || {}, chosen = sizes.chosen || null, estimates = sizes.estimates || [];
  const briefing = page.briefing || null, answers = page.answers || {}, players = page.players || [];
  const spend: number | null = chosen?.spend_usd ?? sizes.rolled_usd?.spend_usd ?? null;
  // The company's share of the segment's spend is the server's (our_share, in percent).
  const share: number | null = typeof page.our_share === 'number' ? page.our_share : null;
  // The players' revenue feeds the players table.
  const revenue = rdqRevenue({segments: [{id: seg.id, parent: null, players}]});
  const owner = mktOwner();
  const tiles: string[] = [];
  // The money is this tile (its range beside it), so the question below carries only the sizes.
  if (spend != null) tiles.push(rdqTile('Yearly spend', `${mktMoney(spend)}${chosen?.low_usd != null ? ` <small>${mktRange(chosen)}</small>` : ''}`,
    Number.isFinite(chosen?.growth_pct) ? `${rdqGrowth(chosen?.growth_pct)} a year` : ''));
  if (spend && share != null) tiles.push(rdqTile(`${esc(companyName())} share`, rdqPct(share), `${mktMoney(spend * share / 100)} of ${mktMoney(spend)}`));
  const ests = estimates.length ? `<ul class="mkt-ests" id="seg-sizes" hidden>${estimates.map(e => mktEstimateHtml(e, {size: chosen})).join('')}</ul>` : '';
  const edit = owner && estimates.length ? '<button class="linkish" type="button" data-seg-sizes aria-expanded="false" aria-controls="seg-sizes">Edit sizes</button>' : '';
  const questions = rdqQuestionsHtml({
    answers, keep: ['money', 'reach', 'saying'],
    changed: briefing ? rdqChangedHtml(briefing) : '',
    changedMore: briefing ? `${esc(rdbDate(briefing))} · <button class="linkish" type="button" data-rdb-read>Read</button>` : '',
    money: ests, moneyMore: edit,
    players: players.length ? rdqPlayersHtml({segments: [{id: seg.id, name: seg.name, players}], total: {spend_usd: spend}}, null, briefing, revenue) : '',
    // A segment has no buyer chain or listening of its own, so its reach and what buyers say show their answers only.
    reach: '', saying: '',
  });
  // The segment page's section names itself Market; the Market tab's own header already does, so only the former has a heading.
  const heading = el.id === 'seg-market' ? '<h2>Market</h2>' : '';
  el.innerHTML = rdqPageHtml({cls: 'seg-mkt', tiles,
    top: `<header class="mkt-top">${heading}
      <span class="spacer"></span><button class="ghost" type="button" data-seg-research>Ask Librarian to research</button></header>`,
    questions});
  rdqPageBind(el, briefing, (ev: Event) => {
    const target = ev.target as Element;
    if (target.closest('[data-seg-research]')) { void segResearch(id); return true; }
    const toggle = target.closest<HTMLElement>('[data-seg-sizes]');
    if (toggle) {
      const list = el.querySelector<HTMLElement>('#seg-sizes');
      if (list) list.hidden = !list.hidden;
      toggle.setAttribute('aria-expanded', String(!!list && !list.hidden));
      return true;
    }
    const accept = target.closest<HTMLElement>('[data-mkt-accept]');
    if (accept) { void segSizeWrite(el, id, post(`/v2/market/sizes/${encodeURIComponent(accept.dataset.mktAccept || '')}/accept`, {}), page); return true; }
    return false;
  });
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
async function segSizeWrite(el: HTMLElement, id: string, run: Promise<unknown>, page: SegPage): Promise<void> {
  try { await run; }
  catch (error) { toast(segErr(error, 'Could not save it'), true); return; }
  const fresh: SegPage = await get('/v2/market/segments/' + id + '/page', {fresh: true}).catch(() => page);
  if (el.isConnected) segMarketDraw(el, id, fresh);
}

// Asks the Librarian for the segment's five answers and sizes; an open task comes back as already asked.
async function segResearch(id: string): Promise<void> {
  try {
    const out = await post('/v2/market/segments/' + id + '/research', {note: ''});
    toast(out?.created === false ? 'Already on it' : 'The Librarian is on it');
  } catch (error) { toast(segErr(error, 'Could not ask the Librarian'), true); }
}

// ---------------------------------------------------------------- the Market tab
// The chips that move between the compare view and one segment's market.
function segChipsHtml(active: string, segs: SegCompareRow[]): string {
  return `<nav class="seg-chips" aria-label="Segments">
    <a class="seg-chip${active === 'all' ? ' cur' : ''}" href="${MARKET_ROUTE}"${active === 'all' ? ' aria-current="page"' : ''}><span class="seg-chip-name">All segments</span></a>
    ${segs.map(s => `<a class="seg-chip${active === s.id ? ' cur' : ''}" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}"${active === s.id ? ' aria-current="page"' : ''}>${segDot(s)}<span class="seg-chip-name">${esc(s.name)}</span></a>`).join('')}
  </nav>`;
}
function segCompareHtml(segs: SegCompare[]): string {
  const rows = segs.map(s => `<tr><td><a class="seg-name" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}">${segDot(s)}${esc(s.name)}</a></td>
      <td class="mkt-num" data-col="yearly">${s.yearly_spend != null ? mktMoney(s.yearly_spend) : ''}</td>
      <td class="mkt-num seg-opt" data-col="growth">${s.growth != null ? rdqGrowth(s.growth) : ''}</td><td class="mkt-num seg-opt" data-col="share">${s.our_share != null ? rdqPct(s.our_share) : ''}</td>
      <td class="seg-opt" data-col="team">${segFacesHtml(s.faces, s.teammates)}</td></tr>`).join('');
  return `<section class="mkt-q seg-cmp-sec" aria-label="Segments">
    <div class="mkt-card mkt-tbl"><table class="mkt-pl seg-cmp"><thead><tr><th data-col="segment">Segment</th><th data-col="yearly">Yearly spend</th><th class="seg-opt" data-col="growth">Growth</th><th class="seg-opt" data-col="share">Our share</th><th class="seg-opt" data-col="team">Team</th></tr></thead>
      <tbody>${rows}</tbody></table></div></section>`;
}
// What changed across all the segments: each item tagged with its segment (All for the whole market).
function segChangedHtml(changed: SegChanged[], segs: SegCompareRow[]): string {
  if (!changed.length) return '';
  const by = new Map(segs.map(s => [s.id, s]));
  const items = changed.map(c => {
    const s = by.get(c.segment_id);
    const tag = s ? `<a class="seg-tag" data-seg-tag="${esc(s.id)}" href="${MARKET_ROUTE}?segment=${encodeURIComponent(s.id)}">${segDot(s)}${esc(s.name)}</a>` : '<span class="seg-tag" data-seg-tag="all">All</span>';
    return `<li>${tag}<span class="mkt-it">${esc(c.text)}</span></li>`;
  }).join('');
  return rdqSection('changed', 'What changed across all', '', '', `<ul class="mkt-card mkt-items seg-changed">${items}</ul>`);
}

// Whether the company has no market yet, by the Market page's own test (no active entity, and no page the seed did not
// write). The compare view and a segment's market then show the setup, as the whole market does. A failed read is not blank.
async function segMarketBlank(): Promise<boolean> {
  const [count, library] = await Promise.all([
    get('/v2/market/entities?status=active&limit=1').catch(() => null),
    get('/company-docs?collection=market').catch(() => null),
  ]);
  if (!count || !library) return false;
  return marketBlank(count.total ?? (count.entities || []).length, library.documents).blank;
}

// The Market tab's compare view: the segments side by side, what changed across them, and the chips to each segment's
// market. With no market yet it shows the setup; with no segments, the whole market.
async function mountMarketCompare(shell: HTMLElement): Promise<SegMount> {
  // A failed compare read is no segments: the whole market shows instead, as with no segments at all.
  const [data, , blank] = await Promise.all([
    get('/v2/market/segments/compare', {fresh: true}).catch(() => null),
    segTagsLoad(),
    segMarketBlank(),
  ]);
  if (blank) return await mountBlank(shell);
  if (!(data?.segments || []).length) return await mountMarketPage(shell);
  // The faces and teammates come from the shared segment list by id; a segment missing there shows no faces.
  const people = new Map<string, SegRow>(segTagRows().map(x => [x.id, x]));
  const segs: SegCompare[] = (data?.segments || []).map((r: SegCompareRow) => ({
    ...r, faces: people.get(r.id)?.faces || [], teammates: people.get(r.id)?.teammates || 0}));
  const changed: SegChanged[] = data?.changed || [];
  shell.innerHTML = `<div class="mkt-pg seg-mkt">${segTopHtml()}${segChipsHtml('all', segs)}${segCompareHtml(segs)}${segChangedHtml(changed, segs)}</div>`;
  return {shell, open: () => { shell.scrollTop = 0; }, stop: () => {}};
}

// One segment's market in the Market tab: the chips, then the segment's five questions.
async function mountSegmentMarketView(shell: HTMLElement, id: string): Promise<SegMount> {
  const [data, blank] = await Promise.all([get('/v2/market/segments/compare', {fresh: true}).catch(() => null), segMarketBlank()]);
  if (blank) return await mountBlank(shell);
  const segs: SegCompareRow[] = data?.segments || [];
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
