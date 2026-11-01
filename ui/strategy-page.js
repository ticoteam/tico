/* Strategy: how the team means to win (backend/strategy.py, docs/strategy.md#the-page). One line on top (the summary),
   then Where we play beside the bets, the competitors beside related news, then four fixed sections and any the owner
   adds. The owner edits the draft in place and publishes numbered versions with a note; everyone else reads the latest
   published version. Where we play and Competitors are the market graph's own rows (backend/strategy_market.py), and
   Related news is what Listening found. Lenses (ui/lenses-page.js) and Market (ui/market-page.js) are the section's
   other tabs. */
'use strict';
let STRAT = null;          // the open page: {data, kpis, load, adding, editSection, addSection, publishing}
let STRAT_LOAD = 0;
// The market cards' reads, each undefined while it loads and null when it could not be read: {spend, rivals, news}.
let STRAT_MKT = {};
const isStrategyRoute = r => r === '#/strategy' || r.startsWith('#/strategy?');
const STRAT_STATUS = {proposed: 'Proposed', active: 'Active', retired: 'Retired'};
const STRAT_MAX_ACTIVE = 7;
// The four sections every strategy has, in this order. The placeholder is the question; nothing else is shown.
const STRAT_SECTIONS = [
  {key: 'serve', title: 'Who we serve', prompt: 'The customer, what they use today, and the one you turned down'},
  {key: 'sell', title: 'What we sell, and for how much', prompt: 'The product, the price, and the price that would break if doubled or halved'},
  {key: 'know', title: "What we know that others don't", prompt: 'What the team understands that the obvious competitors miss'},
  {key: 'not', title: "What we won't do", prompt: 'The trade-offs: what the team refuses'},
];
const STRAT_WRONG_KINDS = {kpi: 'KPI', thesis: 'Market', text: 'Sentence'};
// A sentence wrong-if needs a day to look again (strategy._wrong_if); left empty, it is 90 days out.
const STRAT_CHECK_DAYS = 90;
const stratCheckBy = () => new Date(Date.now() + STRAT_CHECK_DAYS * 864e5).toISOString().slice(0, 10);
const STRAT_X = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const STRAT_NEWS_MAX = 8;
const STRAT_TAGS = {hot: 'Hot', lead: 'Lead', rival: 'Rival', market: 'Market'};

// The heading and the tabs, the same on each, so switching tabs never moves them (ui/app/redesign.js).
window.strategyHeadHtml = tab => redesignStrategyHead(tab);

window.strategyStop = function strategyStop() {
  STRAT_LOAD++;
  if (STRAT) S.lastRead.strategy = {data: STRAT.data, market: STRAT_MKT};   // the next visit paints it at once
  STRAT = null;
  const dlg = document.getElementById('strat-versions');
  if (dlg?.open) dlg.close();
};

// A return visit paints the strategy as last read, then reads it again and patches what changed; only the first visit
// waits behind a skeleton. The market cards load beside it and never hold the page up.
window.pageStrategy = async function pageStrategy() {
  const load = ++STRAT_LOAD;
  const main = $('#main');
  main.classList.add('strategy-layout');
  const kept = S.lastRead.strategy;
  STRAT_MKT = {...(kept?.market || {})};
  main.innerHTML = strategyHeadHtml('strategy') + `<div id="strat-body" class="strat-body">${kept ? '' : skelRows(6, {dot: false})}</div>`;
  const open = data => {
    STRAT = {data, kpis: [], load, adding: false, editSection: '', addSection: false, publishing: false};
    strategyRender();
    // #/strategy?bet=<id> (a goal's bet chip) scrolls to that bet and flashes it.
    const bet = new URLSearchParams(S.route.split('?')[1] || '').get('bet');
    const row = bet && document.querySelector(`[data-bet="${CSS.escape(bet)}"]`);
    if (row) { row.scrollIntoView({block: 'center'}); row.classList.add('strat-flash'); }
  };
  if (kept) open(kept.data);
  for (const key of ['spend', 'rivals', 'news', 'growth']) void strategyMarketRead(key, load);
  let data;
  try { data = await get('/v2/strategy'); }
  catch (error) {
    if (load === STRAT_LOAD && !kept && $('#strat-body')) $('#strat-body').innerHTML = `<p class="err">${esc(error.message || 'Could not load the strategy')}</p>`;
    return;
  }
  // A read that lands after the page was left is kept for the next visit when that visit has nothing yet.
  if (load !== STRAT_LOAD) { S.lastRead.strategy ||= {data}; return; }
  S.lastRead.strategy = {data, market: STRAT_MKT};
  if (!STRAT) { open(data); return; }
  // Something opened for editing meanwhile keeps the page as drawn; the save reads the strategy again.
  const state = STRAT;
  if (state.adding || state.editSection || state.addSection || state.publishing) return;
  const same = sameData(data, state.data);
  state.data = data;
  if (!same) strategyRender();
};

// Only the owner writes; the server says so (may_edit) and is the one that enforces it.
const strategyMayEdit = data => data?.may_edit ?? S.me?.role === 'owner';
const stratText = s => String(s ?? '').trim();
const stratRank = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9);
const stratExtra = version => version?.sections?.extra || {};
// Blank: nothing written at all. The vision counts, though the page no longer shows it (the Goals page reads it).
function strategyBlank(version, bets = []) {
  if (!version) return true;
  const s = version.sections || {};
  return !stratText(version.summary) && !stratText(version.vision) && !STRAT_SECTIONS.some(sec => stratText(s[sec.key]))
    && !Object.values(stratExtra(version)).some(x => stratText(x?.body) || stratText(x?.title)) && !bets.length;
}
// What the reader sees: the owner the draft and the live bets; everyone else the latest published version and its
// snapshot of the bets.
function strategyView(data) {
  const owner = strategyMayEdit(data) && !!data.draft;
  const version = owner ? data.draft : data.current;
  const bets = (owner ? data.bets : version?.bets) || [];
  const label = data.label || 'Bets';
  return {owner, version, bets: bets.slice().sort(stratRank), label, one: data.label_one || label};
}

// A Wrong if is set when it names what it watches: a KPI and a threshold, a market id, or a sentence.
function stratWrongSet(w) {
  if (!w?.kind) return false;
  if (w.kind === 'kpi') return !!w.kpi_id && w.threshold !== null && w.threshold !== undefined && w.threshold !== '';
  if (w.kind === 'thesis') return !!stratText(w.market_id);
  return !!stratText(w.text);
}
// Why a bet cannot be active, or '' when it can. Rules out and Wrong if are optional (backend/strategy.py _may_be).
function stratActiveRefusal(bet, bets) {
  const others = (bets || []).filter(b => b.status === 'active' && b.id !== bet.id).length;
  if (others >= STRAT_MAX_ACTIVE) return `At most ${STRAT_MAX_ACTIVE} can be active`;
  return '';
}
// The server's rule for publishing, said before the click: one active bet, and What we won't do written.
function strategyPublishRefusal(data, view) {
  if (!(data.bets || []).some(b => b.status === 'active')) return `Needs an active ${view.one.toLowerCase()}`;
  if (!stratText(data.draft?.sections?.not)) return "Needs What we won't do";
  return '';
}

// Whether the draft says anything the latest published version does not: its words, its sections, or its bets.
function strategyDiffers(data) {
  const a = data.draft, b = data.current;
  if (!b) return !strategyBlank(a, data.bets || []);
  if (!a) return false;
  if (stratText(a.summary) !== stratText(b.summary) || stratText(a.vision) !== stratText(b.vision)) return true;
  if ((a.guide || '') !== (b.guide || '')) return true;
  for (const sec of STRAT_SECTIONS) if (stratText(a.sections?.[sec.key]) !== stratText(b.sections?.[sec.key])) return true;
  const extra = v => JSON.stringify(Object.entries(stratExtra(v)).map(([k, x]) => [k, stratText(x?.title), stratText(x?.body)]).sort());
  if (extra(a) !== extra(b)) return true;
  // A version keeps every bet not retired; one retired since is missing from the live side, so it still differs.
  const sig = list => (list || []).filter(c => c.status !== 'retired').sort(stratRank)
    .map(c => [c.id, stratText(c.statement), stratText(c.why), stratText(c.rules_out), JSON.stringify(c.wrong_if || null), JSON.stringify(c.cites || []), c.status].join('\u0001')).join('\u0002');
  return sig(data.bets) !== sig(b.bets);
}

const stratDate = d => {
  if (!d) return '';
  const at = new Date(/^\d{4}-\d{2}-\d{2}$/.test(d) ? d + 'T00:00' : d);
  if (isNaN(at)) return String(d);
  return at.toLocaleDateString(undefined, {day: 'numeric', month: 'short', ...(at.getFullYear() !== new Date().getFullYear() ? {year: 'numeric'} : {})});
};
// The name of a KPI: the KPI list, or the KPIs the bet's goals carry, or else its id.
function stratKpi(id) {
  const all = STRAT?.kpis || [];
  const k = all.find(x => x.id === id);
  if (k) return k;
  for (const goals of Object.values(STRAT?.data?.goals_by_bet || {}))
    for (const g of goals || []) { const hit = (g.kpis || []).find(x => x.id === id); if (hit) return hit; }
  return {id, name: id};
}
// A Wrong if in plain words. A sentence with no date to check it by is "Not yet measurable".
function strategyWrongIfHtml(w) {
  const by = w?.check_by ? ` <span class="muted">· check by ${esc(stratDate(w.check_by))}</span>` : '';
  if (!stratWrongSet(w)) return '<span class="muted">Not yet measurable</span>';
  if (w.kind === 'kpi') {
    const k = stratKpi(w.kpi_id), unit = k.unit === '%' ? '%' : k.unit ? ' ' + k.unit : '';
    return `${esc(k.name || k.id)} ${w.direction === 'above' ? 'above' : 'below'} ${esc(String(w.threshold))}${esc(unit)}${by}`;
  }
  if (w.kind === 'thesis') return `${esc(w.market_id)} changes${by}`;
  return `${esc(w.text)}${by || ' <span class="muted">· Not yet measurable</span>'}`;
}

function strategyStatusText(data, view) {
  if (!view.owner_id) return '';
  if (data.current && strategyDiffers(data)) return `Draft differs from v${data.current.n}`;
  if (!data.current && !strategyBlank(data.draft, data.bets || [])) return 'Not published';
  return '';
}
// The line above the page: the published version and who published it, then (for the owner) where the draft stands.
function strategyMetaHtml(data, view) {
  const v = data.current, status = strategyStatusText(data, view);
  const head = v ? `v${esc(v.n)}${v.published ? ` · published <time datetime="${esc(v.published)}" title="${esc(fmt(v.published))}">${esc(stratDate(v.published))}</time>` : ''}${v.published_by ? ` by ${esc(firstName(goalActorName(v.published_by)) || goalActorName(v.published_by))}` : ''}` : '';
  if (!head && !status) return '';
  return `<p class="strat-meta">${head}${head && status ? ' · ' : ''}${status ? `<span class="strat-status" data-strat-status>${esc(status)}</span>` : ''}</p>`;
}

function strategyActionsHtml(data, view) {
  const versions = data.current ? '<button class="ghost" type="button" data-strat-versions>Versions</button>' : '';
  if (!view.owner_id) return versions;
  const refusal = strategyPublishRefusal(data, view), fresh = strategyDiffers(data);
  const why = refusal || (fresh ? '' : 'Nothing new to publish');
  // Why Publish is off sits in a (?) beside it, not a line of its own.
  return `${versions}<button class="primary" type="button" data-strat-publish${why ? ` disabled title="${esc(why)}"` : ''}>Publish</button>${refusal ? helpBubble(refusal, 'data-strat-why-not') : ''}`;
}

function strategyPublishHtml() {
  return `<form class="strat-publish" data-strat-publish-form novalidate>
      <input type="text" name="note" maxlength="200" autocomplete="off" placeholder="What changed" aria-label="What changed" required>
      <button class="primary" type="submit">Publish</button><button class="ghost" type="button" data-strat-publish-cancel>Cancel</button></form>`;
}

// A goal's colour is its status (red, yellow, green); none yet is gray.
function strategyGoalsHtml(goals) {
  if (!goals?.length) return '';
  return `<ul class="strat-goals" aria-label="Goals">${goals.map(g =>
    `<li><a href="${GOALS}/${encodeURIComponent(g.id)}">${gdot(g.colour ?? g.status)}<span>${esc(g.title)}</span></a></li>`).join('')}</ul>`;
}

// ---------------------------------------------------------------- a bet's form
// The whole bet (Why, Rules out, Wrong if, status), for Setup's Your bets step (ui/setup-strategy.js); this page edits
// only the statement.
function strategyBetFormHtml(c = {}) {
  const status = c.status || 'proposed', w = c.wrong_if || {}, kind = STRAT_WRONG_KINDS[w.kind] ? w.kind : 'text';
  const kpis = STRAT?.kpis || [];
  const kpiOptions = [...kpis, ...(w.kpi_id && !kpis.some(k => k.id === w.kpi_id) ? [stratKpi(w.kpi_id)] : [])];
  const field = (name, inner, cls = '') => `<label class="goal-f${cls}"><span>${name}</span>${inner}</label>`;
  return `<form class="strat-bet-form" data-bet-form="${esc(c.id || 'new')}" novalidate>
      ${field('Statement', `<input type="text" name="statement" maxlength="300" autocomplete="off" value="${esc(c.statement || '')}" required>`)}
      ${field('Why', `<textarea name="why" rows="2" maxlength="1000">${esc(c.why || '')}</textarea>`)}
      ${field('Rules out', `<textarea name="rules_out" rows="2" maxlength="1000">${esc(c.rules_out || '')}</textarea>`)}
      <fieldset class="strat-wrong" data-wrong>
        <legend>Wrong if</legend>
        <div class="strat-wrong-row">
          <label class="goal-f strat-wrong-kind"><span class="sr-only">Kind</span><select name="wrong_kind" aria-label="Wrong if kind">${Object.entries(STRAT_WRONG_KINDS).map(([v, label]) =>
            `<option value="${v}"${v === kind ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
          <span class="strat-wrong-fields" data-wrong-kind="kpi"${kind === 'kpi' ? '' : ' hidden'}>
            <select name="kpi_id" aria-label="KPI"><option value="">KPI…</option>${kpiOptions.map(k =>
              `<option value="${esc(k.id)}"${k.id === w.kpi_id ? ' selected' : ''}>${esc(k.name || k.id)}</option>`).join('')}</select>
            <select name="direction" aria-label="Direction"><option value="below"${w.direction === 'above' ? '' : ' selected'}>below</option><option value="above"${w.direction === 'above' ? ' selected' : ''}>above</option></select>
            <input type="number" name="threshold" step="any" inputmode="decimal" aria-label="Threshold" placeholder="Threshold" value="${esc(w.threshold ?? '')}"></span>
          <span class="strat-wrong-fields" data-wrong-kind="thesis"${kind === 'thesis' ? '' : ' hidden'}>
            <input type="text" name="market_id" maxlength="200" autocomplete="off" aria-label="Market id" placeholder="Market id" value="${esc(w.market_id || '')}"></span>
          <span class="strat-wrong-fields" data-wrong-kind="text"${kind === 'text' ? '' : ' hidden'}>
            <input type="text" name="wrong_text" maxlength="400" autocomplete="off" aria-label="Wrong if" placeholder="What would prove it wrong" value="${esc(w.text || '')}"></span>
        </div>
        <label class="goal-f strat-wrong-by"><span>Check by</span><input type="date" name="check_by" value="${esc(w.check_by || '')}"></label>
      </fieldset>
      ${field('Status', `<select name="status">${Object.entries(STRAT_STATUS).map(([v, label]) =>
        `<option value="${v}"${v === status ? ' selected' : ''}>${label}</option>`).join('')}</select>`, ' strat-bet-status')}
      <p class="strat-form-why" data-bet-why-not hidden></p>
      <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-bet-cancel>Cancel</button></div></form>`;
}
// The form's Wrong if, as the API takes it; null when nothing is filled in.
function strategyWrongFromForm(el) {
  const kind = el.wrong_kind.value, check_by = el.check_by.value || null;
  let w;
  if (kind === 'kpi') {
    const t = el.threshold.value.trim();
    w = {kind, kpi_id: el.kpi_id.value || null, direction: el.direction.value, threshold: t === '' ? null : Number(t)};
    if (!w.kpi_id && w.threshold === null && !check_by) return null;
  } else if (kind === 'thesis') {
    w = {kind, market_id: el.market_id.value.trim()};
    if (!w.market_id && !check_by) return null;
  } else {
    w = {kind: 'text', text: el.wrong_text.value.trim()};
    if (!w.text && !check_by) return null;
    if (w.text && !check_by) return {...w, check_by: stratCheckBy()};
  }
  return {...w, check_by};
}

// ---------------------------------------------------------------- the bets
// A bet is its statement, edited in place. The number is the handle that drags it (or arrow keys); × retires it, and
// Retired below brings it back. A new one starts active while there is room.
const stratLive = data => (data.bets || []).filter(c => c.status !== 'retired').sort(stratRank);
const stratRoom = data => (data.bets || []).filter(c => c.status === 'active').length < STRAT_MAX_ACTIVE;

function strategyBetHtml(c, n, view, data) {
  const status = c.status || 'proposed', health = (data.health_by_bet || {})[c.id], goals = (data.goals_by_bet || {})[c.id];
  const one = esc(view.one);
  const statement = view.owner_id
    ? `<textarea class="strat-ed strat-bet-st" data-bet-st rows="1" maxlength="300" placeholder="A ${one.toLowerCase()}" aria-label="${one} ${n + 1}">${esc(c.statement)}</textarea>`
    : `<span class="strat-bet-st">${esc(c.statement)}</span>`;
  const num = view.owner_id
    ? `<button type="button" class="strat-bet-n tnum" data-bet-drag aria-label="Move ${one.toLowerCase()} ${n + 1}" title="Drag to move">${n + 1}</button>`
    : `<span class="strat-bet-n tnum" aria-hidden="true">${n + 1}</span>`;
  const pill = status === 'active' ? '' : view.owner_id
    ? `<button type="button" class="pill strat-pill ${esc(status)}" data-bet-activate title="Make it active">${STRAT_STATUS[status] || status}</button>`
    : `<span class="pill strat-pill ${esc(status)}">${STRAT_STATUS[status] || status}</span>`;
  return `<li class="strat-bet strat-${esc(status)}" data-bet="${esc(c.id)}">${num}
      <div class="strat-bet-main">${statement}${strategyGoalsHtml(goals)}</div>
      <span class="strat-bet-side">${health ? `<span class="strat-health" data-bet-health="${esc(health)}">${gdot(health, 'Health, from its KPI goals')}</span>` : ''}${pill}
        ${view.owner_id ? `<button type="button" class="strat-x" data-bet-x aria-label="Remove ${one.toLowerCase()} ${n + 1}">${STRAT_X}</button>` : ''}</span></li>`;
}
function strategyNewBetHtml(view, n) {
  return `<li class="strat-bet strat-bet-new" data-bet-new><span class="strat-bet-n tnum" aria-hidden="true">${n + 1}</span>
      <div class="strat-bet-main"><textarea class="strat-ed strat-bet-st" data-bet-st rows="1" maxlength="300" placeholder="A ${esc(view.one.toLowerCase())}" aria-label="New ${esc(view.one.toLowerCase())}"></textarea></div></li>`;
}
function strategyBetsHead(view, live) {
  const add = view.owner_id && !STRAT?.adding && live.length < 30
    ? `<button type="button" class="strat-add" data-bet-add aria-label="Add ${esc(view.one)}">+ ${esc(view.one)}</button>` : '';
  return `<div class="strat-card-head"><h2 class="strat-k" id="strat-bets-h">${esc(view.label)}</h2>${add}</div>`;
}
function strategyBetRowsHtml(data, view) {
  const live = view.bets.filter(c => c.status !== 'retired');
  return live.map((c, n) => strategyBetHtml(c, n, view, data)).join('') + (view.owner_id && STRAT?.adding ? strategyNewBetHtml(view, live.length) : '');
}
function strategyRetiredHtml(view) {
  const retired = view.bets.filter(c => c.status === 'retired');
  if (!view.owner_id || !retired.length) return '';
  return `<details class="strat-retired"${STRAT?.retiredOpen ? ' open' : ''}><summary>Retired <span class="cnt tnum">${retired.length}</span></summary>
      <ul class="strat-retired-list">${retired.map(c => `<li data-bet="${esc(c.id)}"><span>${esc(c.statement)}</span>
        <button type="button" class="ghost strat-mini" data-bet-restore>Restore</button></li>`).join('')}</ul></details>`;
}

// ---------------------------------------------------------------- Where we play
// The market's segments by spend (GET /v2/market/spend), a sized segment inside its sized parent, the rest as plain
// tiles. Us is filled, Next outlined; a click moves a segment Us, then Next, then neither. Several may be Us or Next, as
// Growth's Serving and Next segments are (the server keeps both in step).
function strategyAddRowHtml(kind, placeholder) {
  return `<form class="strat-addrow" data-add="${kind}" novalidate><input type="text" name="name" maxlength="${kind === 'rival' ? 300 : 120}" autocomplete="off" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}">
      <button type="submit" class="strat-add">Add</button></form>`;
}
const stratSk = () => skelRows(3, {dot: false});
function strategyWhereHtml(owner) {
  const spend = STRAT_MKT.spend;
  const segs = spend?.segments || [];
  const hint = owner && segs.length ? '<span class="strat-hint">Click to target</span>' : '';
  const head = `<div class="strat-card-head"><h2 class="strat-k">Where we play</h2>${hint}</div>`;
  if (spend === undefined) return head + stratSk();
  const add = owner ? strategyAddRowHtml('seg', 'Add a segment') : '';
  if (!segs.length) return `${head}<p class="strat-none-s">No market map yet.</p>${add}`;
  const {drawn} = mktTree(spend);
  const W = 200, H = 100, shown = new Set();
  const pos = r => `left:${(r.x / W * 100).toFixed(3)}%;top:${(r.y / H * 100).toFixed(3)}%;width:${(r.w / W * 100).toFixed(3)}%;height:${(r.h / H * 100).toFixed(3)}%`;
  const tile = (seg, r, value) => {
    shown.add(seg.id);
    const mark = seg.self ? 'Us' : seg.next ? 'Next' : '';
    const title = `${seg.name}${value ? `: ${mktMoney(value)} a year, an estimate` : ''}`;
    return `<div class="strat-tm${seg.self ? ' ours' : seg.next ? ' next' : ''}${owner ? ' pick' : ''}" data-seg="${esc(seg.id)}"${r ? ` style="${pos(r)}"` : ''}${owner
      ? ` role="button" tabindex="0" aria-pressed="${seg.self ? 'true' : seg.next ? 'mixed' : 'false'}"` : ''} title="${esc(title)}">${mark ? `<em>${mark}</em>` : ''}<b>${esc(seg.name)}</b>${value ? ` <span>${mktMoney(value)}</span>` : ''}${owner
      ? `<button type="button" class="strat-x" data-seg-x aria-label="Remove ${esc(seg.name)}">${STRAT_X}</button>` : ''}</div>`;
  };
  // One sized segment is not a map: then every segment is a plain tile.
  const mapped = drawn.length >= 2 ? drawn : [];
  const tiles = mktSquarify(mapped.map(g => ({g, value: g.value})), 0, 0, W, H).map(r => {
    const g = r.g;
    if (!g.kids.length) return tile(g.seg, r, g.value);
    // The parent's own share holds its name and whole size; its sized parts sit under it, inside it, as full-width bands.
    const sum = g.kids.reduce((a, k) => a + k.value, 0), own = g.value - sum;
    const bands = [...(own > 0 ? [{seg: g.seg, value: own, whole: g.value}] : []), ...g.kids.slice().sort((a, b) => b.value - a.value)];
    const total = bands.reduce((a, b) => a + b.value, 0);
    let y = r.y;
    const inner = bands.map(b => { const h = b.value / total * r.h, out = tile(b.seg, {x: r.x, y, w: r.w, h}, b.whole || b.value); y += h; return out; }).join('');
    return `<div class="strat-tm-group" style="${pos(r)}"></div>${inner}`;
  }).join('');
  const plain = segs.filter(s => !shown.has(s.id));
  const total = Number(spend.total?.spend_usd) || 0;
  return `${head}${tiles ? `<div class="strat-map">${tiles}</div>` : ''}${plain.length ? `<div class="strat-tm-plain">${plain.map(s => tile(s, null, mapped.length ? 0 : Number(s.rolled_usd?.spend_usd) || 0)).join('')}</div>` : ''}
    ${tiles && total ? `<p class="strat-cap">About ${mktMoney(total)} a year</p>` : ''}${add}`;
}

// ---------------------------------------------------------------- Competitors and Related news
function strategyRivalsHtml(owner) {
  const head = '<div class="strat-card-head"><h2 class="strat-k">Competitors</h2></div>';
  const list = STRAT_MKT.rivals;
  if (list === undefined) return head + stratSk();
  const chips = (list || []).map(c => `<li class="strat-chip" data-rival="${esc(c.id)}">${/^https?:\/\//i.test(c.url || '')
    ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.name)}</a>` : `<span>${esc(c.name)}</span>`}${owner
    ? `<button type="button" class="strat-x" data-rival-x aria-label="Remove ${esc(c.name)}">${STRAT_X}</button>` : ''}</li>`).join('');
  return `${head}${chips ? `<ul class="strat-chips">${chips}</ul>` : '<p class="strat-none-s">None yet.</p>'}${owner ? strategyAddRowHtml('rival', 'Add a competitor or its site') : ''}`;
}
// What Listening found: the posts routed to someone who reads them (GET /v2/listening/stats), and the posts setup filed
// as market insights ("Listening: <tag>", backend/company_import.py). Hot first, then the newest.
function strategyNews(stats, insights) {
  const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } };
  const out = [], seen = new Set();
  const push = item => { if (!/^https?:\/\//i.test(item.url || '') || seen.has(item.url)) return; seen.add(item.url); out.push(item); };
  for (const i of insights?.insights || []) {
    const m = /^Listening:\s*([a-z]+)/i.exec(i.about || '');
    if (m && i.status !== 'rejected') push({tag: m[1].toLowerCase(), title: i.claim || i.source_url, url: i.source_url, source: host(i.source_url), at: i.reported_at || ''});
  }
  for (const p of stats?.recent || []) push({tag: '', title: p.text || p.url, url: p.url,
    source: typeof rdmLabel === 'function' ? rdmLabel(p.source) : p.source, at: p.published_at || p.at || ''});
  const order = Object.keys(STRAT_TAGS), rank = t => (order.includes(t) ? order.indexOf(t) : order.length);
  return out.sort((a, b) => rank(a.tag) - rank(b.tag) || String(b.at).localeCompare(String(a.at))).slice(0, STRAT_NEWS_MAX);
}
function strategyNewsHtml() {
  const head = '<div class="strat-card-head"><h2 class="strat-k">Related news</h2></div>';
  const news = STRAT_MKT.news;
  if (news === undefined) return head + stratSk();
  if (!news?.length) return head + '<p class="strat-none-s">No news yet.</p>';
  return `${head}<ul class="strat-posts">${news.map(p => `<li>${STRAT_TAGS[p.tag] ? `<b class="strat-tag ${esc(p.tag)}">${STRAT_TAGS[p.tag]}</b>` : ''}<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>
      <small>${esc(p.source || '')}${p.at ? `${p.source ? ' · ' : ''}${esc(stratDate(String(p.at).slice(0, 10)))}` : ''}</small></li>`).join('')}</ul>`;
}

// Reads one market card. Each lands on its own and redraws only its card.
async function strategyMarketRead(key, load, fresh = false) {
  const opts = fresh ? {fresh: true} : undefined;
  let value = null;
  try {
    if (key === 'spend') value = await get('/v2/market/spend', opts);
    else if (key === 'rivals') value = (await get('/v2/market/competitors', opts))?.competitors || [];
    else if (key === 'growth') {
      // The Brand strip (ui/app/brand-page.js).
      value = {brand: await get('/v2/brand', opts).catch(() => null)};
    }
    else {
      const since = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 19);
      const [stats, insights] = await Promise.all([get(`/v2/listening/stats?since=${encodeURIComponent(since)}`, opts).catch(() => null),
        S.me?.role === 'owner' ? get('/v2/market/insights', opts).catch(() => null) : null]);
      value = strategyNews(stats, insights);
    }
  } catch { value = null; }
  if (load !== STRAT_LOAD) return;
  STRAT_MKT[key] = value;
  strategyPaintMarket(key);
}
function strategyPaintMarket(key) {
  if (!STRAT) return;
  const owner = strategyView(STRAT.data).owner_id;
  if (key === 'growth') {
    const g = STRAT_MKT.growth;
    if ($('#strat-setup')) patchHTML($('#strat-setup'), g ? brandSetupHtml(g.brand) : '');
    if ($('#strat-brand')) patchHTML($('#strat-brand'), g ? brandStripHtml(g.brand) : '');
    return;
  }
  const box = {spend: '#strat-where', rivals: '#strat-rivals', news: '#strat-news'}[key];
  const el = $(box);
  if (!el) return;
  patchHTML(el, key === 'spend' ? strategyWhereHtml(owner) : key === 'rivals' ? strategyRivalsHtml(owner) : strategyNewsHtml());
}

// ---------------------------------------------------------------- the sections
// The four fixed sections, then the extra ones in the order they were added. The owner writes each, one at a time;
// an extra one can be renamed or removed, and nothing else ever drops it.
function strategySectionList(version) {
  const s = version?.sections || {};
  return [...STRAT_SECTIONS.map(sec => ({...sec, text: s[sec.key] || '', extra: false})),
    ...Object.entries(stratExtra(version)).filter(([, x]) => x).map(([key, x]) => ({key, title: x.title || key, prompt: '', text: x.body || '', extra: true}))];
}
function strategySectionsHtml(view) {
  const owner = view.owner_id;
  const rows = strategySectionList(view.version).map(sec => {
    const editing = owner && STRAT?.editSection === sec.key;
    if (!owner && !stratText(sec.text)) return '';
    const id = 'x-' + sec.key;
    const body = editing
      ? `<form class="strat-sec-form" data-section-form="${esc(sec.key)}"${sec.extra ? ' data-extra' : ''} novalidate>
          ${sec.extra ? `<input type="text" name="title" maxlength="80" autocomplete="off" value="${esc(sec.title)}" aria-label="Title" placeholder="Title">` : ''}
          <textarea name="text" rows="8" aria-label="${esc(sec.title)}" placeholder="${esc(sec.prompt)}">${esc(sec.text)}</textarea>
          <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-section-cancel>Cancel</button>
            ${sec.extra ? '<span class="spacer"></span><button class="ghost danger" type="button" data-section-remove>Remove</button>' : ''}</div></form>`
      : stratText(sec.text) ? `<div class="md strat-md">${safeMd(sec.text)}</div>` : '<p class="muted strat-empty">Not written</p>';
    return `<section class="card strat-card strat-sec" id="strat-sec-${esc(sec.extra ? id : sec.key)}" data-section="${esc(sec.key)}"${sec.extra ? ' data-extra' : ''}>
        <div class="strat-sec-head"><h2 class="strat-k">${esc(sec.title)}</h2>${owner && !editing ? `<button class="ghost strat-mini" type="button" data-section-edit>${stratText(sec.text) ? 'Edit' : 'Write'}</button>` : ''}</div>
        ${body}</section>`;
  }).join('');
  const add = !owner ? '' : STRAT?.addSection
    ? `<section class="card strat-card strat-sec"><form class="strat-sec-form" data-section-new novalidate>
        <input type="text" name="title" maxlength="80" autocomplete="off" aria-label="Title" placeholder="Title" required>
        <textarea name="text" rows="6" aria-label="Section"></textarea>
        <div class="goal-actions"><button class="primary" type="submit">Add</button><button class="ghost" type="button" data-section-cancel>Cancel</button></div></form></section>`
    : '';
  return rows + add;
}

// ---------------------------------------------------------------- the page
// A frame of cards drawn once; each card is patched on its own, so a redraw leaves a field being typed in alone.
const STRAT_FRAME = `<div id="strat-pub"></div>
  <div class="strat-memo" id="strat-doc">
    <section class="card strat-card strat-top strat-summary" id="strat-top"></section>
    <div class="strat-row"><section class="card strat-card strat-where" id="strat-where"></section><section class="card strat-card strat-bets-card" id="strat-sec-bets" aria-labelledby="strat-bets-h"></section></div>
    <div class="strat-setup" id="strat-setup"></div>
    <div class="strat-brand" id="strat-brand"></div>
    <div class="strat-row"><section class="card strat-card" id="strat-rivals"></section><section class="card strat-card" id="strat-news"></section></div>
    <div class="strat-secs" id="strat-secs"></div>
    <div class="strat-add-sec" id="strat-add-sec"></div>
  </div>`;

function strategyRender() {
  const body = $('#strat-body');
  if (!body || !STRAT) return;
  const data = STRAT.data, view = strategyView(data);
  patchHTML($('#strat-actions'), strategyActionsHtml(data, view));
  if (!view.owner_id && !data.current) { body.innerHTML = '<p class="strat-none">No strategy yet.</p>'; strategyBind(); return; }
  if (!$('#strat-doc')) body.innerHTML = STRAT_FRAME;
  const v = view.version || {};
  // Words typed and not yet saved survive a redraw (their save is on its way).
  const key = f => f.dataset.stratField || f.closest('[data-bet]')?.dataset.bet || 'new';
  const typed = [...body.querySelectorAll('[data-strat-field], [data-bet-st]')].filter(f => f.value !== f.defaultValue).map(f => [key(f), f.value]);
  patchHTML($('#strat-pub'), `${STRAT.publishing ? strategyPublishHtml() : ''}${strategyMetaHtml(data, view)}`);
  patchHTML($('#strat-top'), view.owner_id
    ? `<textarea class="strat-ed strat-big" data-strat-field="summary" rows="1" maxlength="400" placeholder="Your strategy in one sentence" aria-label="Summary">${esc(v.summary || '')}</textarea>`
    : `<p class="strat-text strat-big">${esc(v.summary || '')}</p>`);
  $('#strat-top').hidden = !view.owner_id && !stratText(v.summary);
  const bets = $('#strat-sec-bets'), live = view.bets.filter(c => c.status !== 'retired');
  bets.hidden = !view.owner_id && !live.length;
  patchHTML(bets, `${strategyBetsHead(view, live)}<ol class="strat-bets" id="strat-bets-ol"></ol>${strategyRetiredHtml(view)}`);
  const ol = $('#strat-bets-ol');
  patchHTML(ol, strategyBetRowsHtml(data, view));
  if (!ol.children.length) patchHTML(ol, `<li class="strat-bet strat-none-row"><span class="muted strat-empty">None yet</span></li>`);
  for (const k of ['spend', 'rivals', 'news', 'growth']) strategyPaintMarket(k);
  patchHTML($('#strat-secs'), strategySectionsHtml(view));
  patchHTML($('#strat-add-sec'), view.owner_id && !STRAT.addSection ? '<button class="ghost strat-mini" type="button" data-section-add>Add a section</button>' : '');
  for (const [k, value] of typed) {
    const f = k === 'summary' ? body.querySelector('[data-strat-field="summary"]')
      : k === 'new' ? body.querySelector('[data-bet-new] [data-bet-st]') : body.querySelector(`[data-bet="${CSS.escape(k)}"] [data-bet-st]`);
    if (f) f.value = value;
  }
  strategyBind();
}

// Reads the page again and redraws what changed; the cards patch on their own, so a field in use stays as it is.
async function strategyReload(state) {
  try { state.data = await get('/v2/strategy', {fresh: true}); } catch { /* the saved words stay as drawn */ }
  if (STRAT !== state) return;
  strategyRender();
}
// Saves, then reads the page again. A draft changed elsewhere (409: its revision moved on) is read again whole, so
// the owner sees what is there now.
async function strategyWrite(run, {fail = 'Could not save'} = {}) {
  const state = STRAT;
  try { await run(); }
  catch (error) {
    if (error?.status === 409 && STRAT === state) {
      toast('Changed elsewhere, reloaded');
      state.editSection = ''; state.addSection = false; state.publishing = false; state.adding = false;
      // What was typed lost to the newer draft: the fields draw what is there now.
      for (const f of document.querySelectorAll('[data-strat-field], [data-bet-st]')) f.value = f.defaultValue;
      await strategyReload(state);
      return false;
    }
    toast(error.message || fail, true);
    return false;
  }
  if (STRAT !== state) return true;
  await strategyReload(state);
  return true;
}
// A draft write: only what changed, with the revision it was read at.
const strategyDraftPut = change => put('/v2/strategy/draft', {...change, revision: STRAT?.data?.draft?.revision ?? null});
const strategyBetPost = (id, change) => post(`/v2/strategy/bets/${encodeURIComponent(id)}`, {...change, revision: STRAT?.data?.draft?.revision ?? null});

const stratAutosize = el => { el.style.height = 'auto'; el.style.height = el.scrollHeight + 2 + 'px'; };

function strategyBindActions() {
  const state = STRAT;
  const actions = $('#strat-actions');
  const versions = actions.querySelector('[data-strat-versions]');
  if (versions) versions.onclick = () => void strategyVersionsOpen();
  const publish = actions.querySelector('[data-strat-publish]');
  if (publish) publish.onclick = () => { state.publishing = true; strategyRender(); $('[data-strat-publish-form] input')?.focus(); };
}

// One line of text that is its own field: Enter saves (by leaving it), Escape puts it back.
function strategyInPlace(field, saved, save) {
  stratAutosize(field);
  field.oninput = () => stratAutosize(field);
  field.onkeydown = ev => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); field.blur(); }
    if (ev.key === 'Escape') { field.value = saved(); stratAutosize(field); field.blur(); }
  };
  field.onblur = () => save(field.value.replace(/\s+/g, ' ').trim());
}

function strategyBind() {
  const state = STRAT, body = $('#strat-body');
  if (!state || !body) return;
  strategyBindActions();
  const data = state.data, view = strategyView(data);

  const form = body.querySelector('[data-strat-publish-form]');
  if (form) {
    const done = () => { state.publishing = false; strategyRender(); };
    form.querySelector('[data-strat-publish-cancel]').onclick = done;
    form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
    form.onsubmit = async ev => {
      ev.preventDefault();
      const note = form.elements.note.value.trim();
      if (!note) { form.elements.note.focus(); return; }
      form.querySelectorAll('button').forEach(b => { b.disabled = true; });
      let out = null;
      const ok = await strategyWrite(async () => { out = await post('/v2/strategy/publish', {note, revision: state.data?.draft?.revision ?? null}); }, {fail: 'Could not publish'});
      if (ok) { state.publishing = false; strategyRender(); toast(`Published v${out?.n ?? out?.version?.n ?? ''}`.trim()); }
      else form.querySelectorAll('button').forEach(b => { b.disabled = false; });
    };
  }
  if (!view.owner_id) return;

  const summary = body.querySelector('[data-strat-field="summary"]');
  if (summary) {
    const saved = () => strategyView(state.data).version?.summary || '';
    strategyInPlace(summary, saved, text => {
      if (STRAT !== state || text === stratText(saved())) return;
      void strategyWrite(() => strategyDraftPut({summary: text}));
    });
  }
  strategyBindBets(state, body, view);
  strategyBindMarket(state, body);

  // The sections, one at a time: Save, or Cmd/Ctrl+Enter; Escape leaves it as it was.
  for (const sec of body.querySelectorAll('[data-section]')) {
    const b = sec.querySelector('[data-section-edit]');
    if (b) b.onclick = () => { state.editSection = sec.dataset.section; state.addSection = false; strategyRender(); const t = $(`[data-section-form="${CSS.escape(sec.dataset.section)}"] textarea`); if (t) { stratAutosize(t); t.focus(); } };
  }
  const addSec = body.querySelector('[data-section-add]');
  if (addSec) addSec.onclick = () => { state.addSection = true; state.editSection = ''; strategyRender(); $('[data-section-new] input')?.focus(); };
  const sform = body.querySelector('[data-section-form], [data-section-new]');
  if (sform) {
    const area = sform.elements.text, extra = sform.hasAttribute('data-extra'), fresh = sform.hasAttribute('data-section-new');
    const key = sform.dataset.sectionForm;
    const done = () => { state.editSection = ''; state.addSection = false; strategyRender(); };
    stratAutosize(area);
    area.oninput = () => stratAutosize(area);
    sform.querySelector('[data-section-cancel]').onclick = done;
    sform.onkeydown = ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); done(); }
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); sform.requestSubmit(); }
    };
    const send = async sections => {
      sform.querySelectorAll('button').forEach(b => { b.disabled = true; });
      const was = [state.editSection, state.addSection];
      state.editSection = ''; state.addSection = false;
      const ok = await strategyWrite(() => strategyDraftPut({sections}));
      if (!ok && STRAT === state && sform.isConnected) { [state.editSection, state.addSection] = was; sform.querySelectorAll('button').forEach(b => { b.disabled = false; }); }
    };
    const remove = sform.querySelector('[data-section-remove]');
    if (remove) remove.onclick = () => {
      const title = stratExtra(view.version)[key]?.title || key;
      if (confirm(`Remove ${title}? Earlier versions keep it.`)) void send({extra: {[key]: ''}});
    };
    sform.onsubmit = async ev => {
      ev.preventDefault();
      const text = area.value.trim();
      if (fresh || extra) {
        const title = sform.elements.title.value.trim();
        if (!title) { sform.elements.title.focus(); return; }
        const now = stratExtra(view.version)[key];
        if (!fresh && title === stratText(now?.title) && text === stratText(now?.body)) { done(); return; }
        return send({extra: {[fresh ? strategyExtraKey(title, view.version) : key]: {title, body: text}}});
      }
      if (text === stratText(view.version?.sections?.[key])) { done(); return; }
      // The server merges sections, so only this one goes; an empty one clears it.
      return send({[key]: text});
    };
  }
}

// A key for a new extra section, from its title, not taken by another one.
function strategyExtraKey(title, version) {
  const taken = new Set([...STRAT_SECTIONS.map(s => s.key), 'extra', ...Object.keys(stratExtra(version))]);
  // The server takes lowercase letters, digits and dashes (backend/strategy.py EXTRA_KEY).
  const base = title.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 36) || 'section';
  let key = base, n = 2;
  while (taken.has(key)) key = `${base}-${n++}`;
  return key;
}

function strategyBindBets(state, body, view) {
  const data = state.data, one = view.one.toLowerCase();
  const add = body.querySelector('[data-bet-add]');
  if (add) add.onclick = () => { state.adding = true; strategyRender(); body.querySelector('[data-bet-new] [data-bet-st]')?.focus(); };
  const fresh = body.querySelector('[data-bet-new] [data-bet-st]');
  if (fresh) strategyInPlace(fresh, () => '', text => {
    if (STRAT !== state || !state.adding) return;
    state.adding = false;
    if (!text) { strategyRender(); return; }
    // New bets start active while there is room; past the limit, proposed.
    void strategyWrite(() => post('/v2/strategy/bets', {statement: text, status: stratRoom(data) ? 'active' : 'proposed',
      revision: data.draft?.revision ?? null}));
  });
  for (const li of body.querySelectorAll('#strat-bets-ol li[data-bet]')) {
    const id = li.dataset.bet, bet = () => (state.data.bets || []).find(c => c.id === id);
    const field = li.querySelector('[data-bet-st]');
    if (field) strategyInPlace(field, () => bet()?.statement || '', text => {
      if (STRAT !== state || !bet()) return;
      // Emptied, it keeps its words: × is how a bet goes.
      if (!text) { field.value = bet().statement; stratAutosize(field); return; }
      if (text !== stratText(bet().statement)) void strategyWrite(() => strategyBetPost(id, {statement: text}));
    });
    const x = li.querySelector('[data-bet-x]');
    if (x) x.onclick = async () => {
      const was = bet()?.status || 'active';
      x.disabled = true;
      if (await strategyWrite(() => strategyBetPost(id, {status: 'retired'}), {fail: 'Could not remove it'}))
        toast(`Removed ${one}`, false, {label: 'Undo', run: () => void strategyWrite(() => strategyBetPost(id, {status: was}))});
    };
    const up = li.querySelector('[data-bet-activate]');
    if (up) up.onclick = () => {
      const refusal = stratActiveRefusal(bet(), state.data.bets);
      if (refusal) { toast(refusal, true); return; }
      up.disabled = true;
      void strategyWrite(() => strategyBetPost(id, {status: 'active'}));
    };
    const handle = li.querySelector('[data-bet-drag]');
    if (handle) strategyBindDrag(state, li, handle);
  }
  const retired = body.querySelector('.strat-retired');
  if (retired) retired.ontoggle = () => { state.retiredOpen = retired.open; };
  for (const li of body.querySelectorAll('.strat-retired li[data-bet]')) {
    const b = li.querySelector('[data-bet-restore]');
    b.onclick = () => { b.disabled = true; void strategyWrite(() => strategyBetPost(li.dataset.bet, {status: stratRoom(state.data) ? 'active' : 'proposed'})); };
  }
}

// The number is the handle: drag it up or down, or press the arrow keys on it.
function strategyBindDrag(state, li, handle) {
  const id = li.dataset.bet;
  handle.onkeydown = ev => {
    if (ev.key !== 'ArrowUp' && ev.key !== 'ArrowDown') return;
    ev.preventDefault();
    const order = stratLive(state.data).map(c => c.id), at = order.indexOf(id), to = at + (ev.key === 'ArrowUp' ? -1 : 1);
    if (at < 0 || to < 0 || to >= order.length) return;
    [order[at], order[to]] = [order[to], order[at]];
    void strategyReorder(state, order).then(() => $(`#strat-bets-ol li[data-bet="${CSS.escape(id)}"] [data-bet-drag]`)?.focus());
  };
  handle.onpointerdown = ev => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const ol = li.parentElement, start = [...ol.querySelectorAll('li[data-bet]')].map(x => x.dataset.bet);
    li.classList.add('dragging');
    // On the document, not captured by the handle: moving the row in the list would drop a capture.
    const move = e => {
      if (e.pointerId !== ev.pointerId) return;
      const others = [...ol.querySelectorAll('li[data-bet]')].filter(x => x !== li);
      const before = others.find(x => { const r = x.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
      if (before) { if (li.nextElementSibling !== before) ol.insertBefore(li, before); }
      else if (others.length && others.at(-1).nextElementSibling !== li) others.at(-1).after(li);
    };
    const end = e => {
      if (e.pointerId !== ev.pointerId) return;
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', end);
      document.removeEventListener('pointercancel', end);
      li.classList.remove('dragging');
      const order = [...ol.querySelectorAll('li[data-bet]')].map(x => x.dataset.bet);
      if (STRAT === state && order.join() !== start.join()) void strategyReorder(state, order);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', end);
    document.addEventListener('pointercancel', end);
  };
}
// A new order renumbers the live bets 0, 1, 2… and saves only the ranks that changed. Each save carries the revision the
// last one answered, so a stale page gets a 409; one that fails midway is read again.
async function strategyReorder(state, order) {
  const live = stratLive(state.data), byId = new Map(live.map(c => [c.id, c]));
  const list = order.map(id => byId.get(id)).filter(Boolean);
  let revision = state.data.draft?.revision ?? null;
  const ok = await strategyWrite(async () => {
    for (const [rank, c] of list.entries()) {
      if (c.rank !== rank) revision = (await post(`/v2/strategy/bets/${encodeURIComponent(c.id)}`, {rank, revision}))?.revision ?? null;
    }
  }, {fail: 'Could not move it'});
  if (!ok && STRAT === state) await strategyReload(state);
}

// ---------------------------------------------------------------- the market cards' writes
// Each change draws at once and then saves; a refused one reads its card again.
function strategyBindMarket(state, body) {
  const spend = () => STRAT_MKT.spend;
  const again = key => strategyMarketRead(key, STRAT_LOAD, true);
  const failed = (key, error) => { toast(error?.message || 'Could not save', true); void again(key); };
  const where = body.querySelector('#strat-where');
  if (where) {
    const cycle = tile => {
      const seg = (spend()?.segments || []).find(s => s.id === tile.dataset.seg);
      if (!seg) return;
      const to = seg.self ? 'next' : seg.next ? 'none' : 'ours';
      seg.self = to === 'ours'; seg.next = to === 'next';
      strategyPaintMarket('spend');
      $(`#strat-where [data-seg="${CSS.escape(seg.id)}"]`)?.focus();
      post('/v2/market/segments/target', {id: seg.id, target: to}).catch(error => failed('spend', error));
    };
    where.onclick = ev => {
      if (!STRAT || STRAT !== state) return;
      const x = ev.target.closest('[data-seg-x]'), tile = ev.target.closest('[data-seg]');
      if (x && tile) {
        const id = tile.dataset.seg, all = spend()?.segments || [], seg = all.find(s => s.id === id);
        if (!seg) return;
        // Its sub-segments move up to its parent, as the server does.
        spend().segments = all.filter(s => s !== seg).map(s => (s.parent === id ? {...s, parent: seg.parent, depth: Math.max(1, (s.depth || 2) - 1)} : s));
        strategyPaintMarket('spend');
        post('/v2/market/segments/remove', {id}).then(() => again('spend')).catch(error => failed('spend', error));
        return;
      }
      if (tile) cycle(tile);
    };
    where.onkeydown = ev => {
      const tile = ev.target.closest?.('[data-seg]');
      if (!tile || ev.target !== tile || (ev.key !== 'Enter' && ev.key !== ' ')) return;
      ev.preventDefault();
      cycle(tile);
    };
  }
  const rivals = body.querySelector('#strat-rivals');
  if (rivals) rivals.onclick = ev => {
    const x = ev.target.closest('[data-rival-x]'), chip = x?.closest('[data-rival]');
    if (!chip) return;
    const id = chip.dataset.rival;
    STRAT_MKT.rivals = (STRAT_MKT.rivals || []).filter(c => c.id !== id);
    strategyPaintMarket('rivals');
    post('/v2/market/competitors/remove', {id}).catch(error => failed('rivals', error));
  };
  // The add rows sit in cards that are patched; a submit is caught on the card.
  for (const [box, kind] of [['#strat-where', 'seg'], ['#strat-rivals', 'rival']]) {
    const card = body.querySelector(box);
    if (card) card.onsubmit = async ev => {
      const f = ev.target.closest(`[data-add="${kind}"]`);
      if (!f) return;
      ev.preventDefault();
      const name = f.elements.name.value.replace(/\s+/g, ' ').trim();
      if (!name) { f.elements.name.focus(); return; }
      f.querySelectorAll('input, button').forEach(el => { el.disabled = true; });
      try {
        if (kind === 'seg') {
          if (!spend()?.definition) await put('/v2/market/definition', {root_name: `${companyName() || 'Our'} market`});
          await post('/v2/market/segments', {name});
        } else await post('/v2/market/competitors', {name});
      } catch (error) {
        toast(error?.status === 409 ? 'Already there' : error?.message || 'Could not add it', error?.status !== 409);
        f.querySelectorAll('input, button').forEach(el => { el.disabled = false; });
        f.elements.name.focus();
        return;
      }
      await again(kind === 'seg' ? 'spend' : 'rivals');
      // The row may be the same node as before (its markup did not change): emptied and on again, for the next one.
      const row = $(`${box} [data-add="${kind}"]`);
      if (row) { row.querySelectorAll('input, button').forEach(el => { el.disabled = false; }); row.elements.name.value = ''; row.elements.name.focus(); }
    };
  }
}

// ---------------------------------------------------------------- versions
// A drawer: the published versions, newest first; one opens read-only.
function strategyVersionsDialog() {
  let dlg = document.getElementById('strat-versions');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'strat-versions'; dlg.className = 'tmodal gpanel strat-versions';
    dlg.setAttribute('aria-label', 'Versions');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
  }
  return dlg;
}
function strategyDrawerHtml(title, inner, back = false) {
  return `<div class="tmodal-head">${back ? '<button class="ghost strat-back" type="button" data-sv-back>‹ Versions</button>' : `<h2>${esc(title)}</h2>`}<span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-sv-close aria-label="Close">✕</button></div>
    <div class="tmodal-body strat-sv-body">${inner}</div>`;
}
async function strategyVersionsOpen() {
  const state = STRAT, dlg = strategyVersionsDialog();
  dlg.innerHTML = strategyDrawerHtml('Versions', skelRows(4, {dot: false}));
  dlg.querySelector('[data-sv-close]').onclick = () => dlg.close();
  if (!dlg.open) dlg.showModal();
  let list;
  try { list = await get('/v2/strategy/versions', {fresh: true}); }
  catch (error) { dlg.querySelector('.strat-sv-body').innerHTML = `<p class="err">${esc(error.message)}</p>`; return; }
  if (STRAT !== state || !dlg.open) return;
  state.versions = (Array.isArray(list) ? list : []).slice().sort((a, b) => b.n - a.n);
  strategyVersionsList(dlg, state.versions);
}
function strategyVersionsList(dlg, rows) {
  dlg.innerHTML = strategyDrawerHtml('Versions', rows.length ? `<ul class="strat-sv-list">${rows.map(v =>
    `<li><button type="button" class="strat-sv-row" data-sv-open="${esc(v.n)}"><span class="strat-sv-n tnum">v${esc(v.n)}</span><span class="strat-sv-note">${esc(v.note || '')}</span>
      <span class="muted strat-sv-meta">${v.published_by ? esc(goalActorName(v.published_by)) + ' · ' : ''}<time datetime="${esc(v.published || '')}" title="${esc(fmt(v.published))}">${esc(ago(v.published))}</time></span></button></li>`).join('')}</ul>`
    : '<p class="muted">No versions yet.</p>');
  dlg.querySelector('[data-sv-close]').onclick = () => dlg.close();
  for (const b of dlg.querySelectorAll('[data-sv-open]')) b.onclick = () => void strategyVersionShow(dlg, Number(b.dataset.svOpen));
}
function strategyVersionShow(dlg, n) {
  const state = STRAT;
  dlg.innerHTML = strategyDrawerHtml('', skelRows(5, {dot: false}), true);
  dlg.querySelector('[data-sv-close]').onclick = () => dlg.close();
  dlg.querySelector('[data-sv-back]').onclick = () => strategyVersionsList(dlg, state?.versions || []);
  return (async () => {
    let html;
    try {
      const v = await get(`/v2/strategy/versions/${encodeURIComponent(n)}`), label = state.data.label || 'Bets';
      const bets = (v.bets || []).slice().sort(stratRank).filter(c => c.status !== 'retired');
      const block = (title, inner) => inner ? `<section class="strat-sv-sec"><h3 class="strat-k">${esc(title)}</h3>${inner}</section>` : '';
      html = `<div class="strat-sv-title"><b>v${esc(v.n)}</b>${v.note ? ` · ${esc(v.note)}` : ''}<div class="muted">${v.published_by ? esc(goalActorName(v.published_by)) : ''}${v.published_by && v.published ? ' · ' : ''}${v.published ? esc(fmt(v.published)) : ''}</div></div>
        ${block('Summary', stratText(v.summary) ? `<p>${esc(v.summary)}</p>` : '')}
        ${block(label, bets.length ? `<ol class="strat-sv-bets">${bets.map(c => `<li><b>${esc(c.statement)}</b>${stratText(c.why) ? `<div class="muted">${esc(c.why)}</div>` : ''}</li>`).join('')}</ol>` : '')}
        ${strategySectionList(v).map(sec => block(sec.title, stratText(sec.text) ? `<div class="md strat-md">${safeMd(sec.text)}</div>` : '')).join('')}`;
    } catch (error) { html = `<p class="err">${esc(error.message)}</p>`; }
    if (STRAT !== state || !dlg.open) return;
    dlg.querySelector('.strat-sv-body').innerHTML = html;
  })();
}
