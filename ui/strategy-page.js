/* Strategy: how the team means to win (backend/strategy.py). A summary and a vision, the bets the team is making (each
   with what it rules out, what would prove it wrong, its goals and their colours), then four fixed sections and any
   the owner adds. The owner edits the draft in place and publishes numbered versions with a note; everyone else reads
   the latest published version. Lenses (ui/lenses-page.js) and Market (ui/market-page.js) are the section's other tabs. */
'use strict';
let STRAT = null;          // the open page: {data, kpis, load, editBet, editSection, addSection, publishing}
let STRAT_LOAD = 0;
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

// The heading and the tabs, the same on each, so switching tabs never moves them.
window.strategyHeadHtml = function strategyHeadHtml(tab) {
  const link = (id, href, label) => `<a href="${href}" data-strat-tab="${id}"${tab === id ? ' class="cur" aria-current="page"' : ''}>${label}</a>`;
  return `<div class="strat-head"><h1>Strategy</h1>
      <nav class="tabs slim strat-tabs" aria-label="Strategy">${link('strategy', '#/strategy', 'Strategy')}${link('lenses', LENSES_ROUTE, 'Lenses')}${link('market', MARKET_ROUTE, 'Market')}</nav>
      <span class="spacer"></span><div class="strat-actions" id="strat-actions"></div></div>`;
};

window.strategyStop = function strategyStop() {
  STRAT_LOAD++;
  STRAT = null;
  const dlg = document.getElementById('strat-versions');
  if (dlg?.open) dlg.close();
};

window.pageStrategy = async function pageStrategy() {
  const load = ++STRAT_LOAD;
  const main = $('#main');
  main.classList.add('strategy-layout');
  main.innerHTML = strategyHeadHtml('strategy') + `<div id="strat-body" class="strat-body">${skelRows(6, {dot: false})}</div>`;
  let data, kpis;
  // The KPIs name a KPI-backed Wrong if and fill the bet form's KPI picker; the page never waits on them.
  try { [data, kpis] = await Promise.all([get('/v2/strategy', {fresh: true}), v2Get('/v2/kpis').catch(() => null)]); }
  catch (error) {
    if (load === STRAT_LOAD && $('#strat-body')) $('#strat-body').innerHTML = `<p class="err">${esc(error.message || 'Could not load the strategy')}</p>`;
    return;
  }
  if (load !== STRAT_LOAD) return;
  STRAT = {data, kpis: kpis?.kpis || [], load, editBet: '', editSection: '', addSection: false, publishing: false};
  strategyRender();
  // #/strategy?bet=<id> (a goal's bet chip) scrolls to that bet and flashes it.
  const bet = new URLSearchParams(S.route.split('?')[1] || '').get('bet');
  const row = bet && document.querySelector(`[data-bet="${CSS.escape(bet)}"]`);
  if (row) { row.scrollIntoView({block: 'center'}); row.classList.add('strat-flash'); }
};

// Only the owner writes; the server says so (may_edit) and is the one that enforces it.
const strategyMayEdit = data => data?.may_edit ?? S.me?.role === 'owner';
const stratText = s => String(s ?? '').trim();
const stratRank = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9);
const stratExtra = version => version?.sections?.extra || {};
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
// Why a bet cannot be active, or '' when it can.
function stratActiveRefusal(bet, bets, one) {
  if (!stratText(bet.rules_out) && !stratWrongSet(bet.wrong_if)) return `An active ${one.toLowerCase()} needs Rules out and Wrong if`;
  if (!stratText(bet.rules_out)) return `An active ${one.toLowerCase()} needs Rules out`;
  if (!stratWrongSet(bet.wrong_if)) return `An active ${one.toLowerCase()} needs Wrong if`;
  const others = (bets || []).filter(b => b.status === 'active' && b.id !== bet.id).length;
  if (others >= STRAT_MAX_ACTIVE) return `At most ${STRAT_MAX_ACTIVE} can be active`;
  return '';
}
// The server's rule for publishing, said before the click: one active bet with Rules out and Wrong if, and What we
// won't do written.
function strategyPublishRefusal(data, view) {
  const ready = (data.bets || []).some(b => b.status === 'active' && stratText(b.rules_out) && stratWrongSet(b.wrong_if));
  if (!ready) return `Needs an active ${view.one.toLowerCase()} with Rules out and Wrong if`;
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
  if (!view.owner) return '';
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
  if (!view.owner) return versions;
  const refusal = strategyPublishRefusal(data, view), fresh = strategyDiffers(data);
  const why = refusal || (fresh ? '' : 'Nothing new to publish');
  return `${versions}${refusal ? `<span class="strat-why-not" data-strat-why-not>${esc(refusal)}</span>` : ''}<button class="primary" type="button" data-strat-publish${why ? ` disabled title="${esc(why)}"` : ''}>Publish</button>`;
}

function strategyPublishHtml() {
  return `<form class="strat-publish" data-strat-publish-form novalidate>
      <input type="text" name="note" maxlength="200" autocomplete="off" placeholder="What changed" aria-label="What changed" required>
      <button class="primary" type="submit">Publish</button><button class="ghost" type="button" data-strat-publish-cancel>Cancel</button></form>`;
}

// Summary and vision: the owner's are fields that save when left, or on Enter.
function strategyFieldHtml(key, title, value, owner, placeholder) {
  const body = owner
    ? `<textarea class="strat-field" data-strat-field="${key}" rows="2" maxlength="400" placeholder="${esc(placeholder)}" aria-label="${esc(title)}">${esc(value || '')}</textarea>`
    : stratText(value) ? `<p class="strat-text">${esc(value)}</p>` : '';
  if (!body) return '';
  return `<section class="strat-sec strat-${key}" id="strat-sec-${key}"><h2 class="strat-k">${esc(title)}</h2>${body}</section>`;
}

// A goal's colour is its status (red, yellow, green); none yet is gray.
function strategyGoalsHtml(goals) {
  if (!goals?.length) return '';
  return `<ul class="strat-goals" aria-label="Goals">${goals.map(g =>
    `<li><a href="${GOALS}/${encodeURIComponent(g.id)}">${gdot(g.colour ?? g.status)}<span>${esc(g.title)}</span></a></li>`).join('')}</ul>`;
}

// ---------------------------------------------------------------- a bet's form
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
function strategyBetHtml(c, n, view, data, live) {
  if (view.owner && STRAT?.editBet === c.id) return `<li class="strat-bet" data-bet="${esc(c.id)}">${strategyBetFormHtml(c)}</li>`;
  const move = view.owner && c.status !== 'retired' ? `<button class="ghost strat-mini" type="button" data-bet-up${n === 0 ? ' disabled' : ''} aria-label="Move up" title="Move up">↑</button>
      <button class="ghost strat-mini" type="button" data-bet-down${n === live - 1 ? ' disabled' : ''} aria-label="Move down" title="Move down">↓</button>` : '';
  const tools = view.owner ? `<span class="strat-bet-tools">${move}<button class="ghost strat-mini" type="button" data-bet-edit>Edit</button></span>` : '';
  const status = c.status || 'proposed', health = (data.health_by_bet || {})[c.id];
  const line = (k, html) => `<p class="strat-bet-line"><span class="strat-bet-k">${k}</span><span>${html}</span></p>`;
  const goals = (data.goals_by_bet || {})[c.id];
  return `<li class="strat-bet strat-${esc(status)}" data-bet="${esc(c.id)}">
      <div class="strat-bet-top">${status === 'retired' ? '' : `<span class="strat-bet-n tnum" aria-hidden="true">${n + 1}</span>`}<span class="strat-bet-st">${esc(c.statement)}</span>
        ${health && status !== 'retired' ? `<span class="strat-health" data-bet-health="${esc(health)}">${gdot(health, 'Health, from its KPI goals')}</span>` : ''}
        ${status === 'active' ? '' : `<span class="pill strat-pill ${esc(status)}">${STRAT_STATUS[status] || status}</span>`}${tools}</div>
      <div class="strat-bet-lines">
        ${stratText(c.why) ? line('Why', esc(c.why)) : ''}
        ${stratText(c.rules_out) ? line('Rules out', esc(c.rules_out)) : ''}
        ${status === 'retired' ? '' : `<div data-bet-wrong>${line('Wrong if', strategyWrongIfHtml(c.wrong_if))}</div>`}
        ${goals?.length ? `<div class="strat-bet-line strat-bet-goals"><span class="strat-bet-k">Goals</span>${strategyGoalsHtml(goals)}</div>` : ''}
      </div></li>`;
}

function strategyBetsHtml(data, view) {
  const live = view.bets.filter(c => c.status !== 'retired'), retired = view.bets.filter(c => c.status === 'retired');
  if (!view.owner && !live.length) return '';
  const adding = view.owner && STRAT?.editBet === 'new';
  return `<section class="strat-sec strat-bets-sec" id="strat-sec-bets" aria-labelledby="strat-bets-h">
      <div class="strat-sec-head"><h2 class="strat-k" id="strat-bets-h">${esc(view.label)}</h2>
        ${view.owner && !adding ? `<button class="ghost strat-mini" type="button" data-bet-add aria-label="Add ${esc(view.one)}" title="Add ${esc(view.one)}">Add</button>` : ''}</div>
      ${live.length ? `<ol class="strat-bets">${live.map((c, n) => strategyBetHtml(c, n, view, data, live.length)).join('')}</ol>`
        : adding ? '' : `<p class="muted strat-empty">None yet</p>`}
      ${adding ? `<div class="strat-bet strat-bet-new">${strategyBetFormHtml()}</div>` : ''}
      ${view.owner && retired.length ? `<details class="strat-retired"><summary>Retired <span class="cnt tnum">${retired.length}</span></summary>
        <ol class="strat-bets">${retired.map((c, n) => strategyBetHtml(c, n, view, data, retired.length)).join('')}</ol></details>` : ''}
    </section>`;
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
  const owner = view.owner;
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
    return `<section class="strat-sec" id="strat-sec-${esc(sec.extra ? id : sec.key)}" data-section="${esc(sec.key)}"${sec.extra ? ' data-extra' : ''}>
        <div class="strat-sec-head"><h2 class="strat-k">${esc(sec.title)}</h2>${owner && !editing ? `<button class="ghost strat-mini" type="button" data-section-edit>${stratText(sec.text) ? 'Edit' : 'Write'}</button>` : ''}</div>
        ${body}</section>`;
  }).join('');
  const add = !owner ? '' : STRAT?.addSection
    ? `<section class="strat-sec"><form class="strat-sec-form" data-section-new novalidate>
        <input type="text" name="title" maxlength="80" autocomplete="off" aria-label="Title" placeholder="Title" required>
        <textarea name="text" rows="6" aria-label="Section"></textarea>
        <div class="goal-actions"><button class="primary" type="submit">Add</button><button class="ghost" type="button" data-section-cancel>Cancel</button></div></form></section>`
    : '<div class="strat-add-sec"><button class="ghost strat-mini" type="button" data-section-add>Add a section</button></div>';
  return rows + add;
}

function strategyTocHtml(view) {
  const has = key => view.owner || stratText(view.version?.[key]);
  const rows = [...(has('summary') ? [['summary', 'Summary']] : []), ...(has('vision') ? [['vision', 'Vision']] : []),
    ...(view.owner || view.bets.some(b => b.status !== 'retired') ? [['bets', view.label]] : []),
    ...strategySectionList(view.version).filter(sec => view.owner || stratText(sec.text)).map(sec => [sec.extra ? 'x-' + sec.key : sec.key, sec.title])];
  return `<nav class="strat-toc" aria-label="Contents">${rows.map(([key, title]) =>
      `<button type="button" class="strat-toc-link" data-strat-go="${esc(key)}">${esc(title)}</button>`).join('')}</nav>`;
}

function strategyRender() {
  const body = $('#strat-body');
  if (!body || !STRAT) return;
  const data = STRAT.data, view = strategyView(data);
  $('#strat-actions').innerHTML = strategyActionsHtml(data, view);
  if (!view.owner && !data.current) { body.innerHTML = '<p class="strat-none">No strategy yet.</p>'; strategyBind(); return; }
  const blank = !data.current && strategyBlank(data.draft, data.bets || []);
  const v = view.version || {};
  // Words typed in Summary or Vision and not yet saved survive a redraw (their save is on its way).
  const typed = [...body.querySelectorAll('[data-strat-field]')].filter(f => f.value !== f.defaultValue).map(f => [f.dataset.stratField, f.value]);
  body.innerHTML = `${STRAT.publishing ? strategyPublishHtml() : ''}${strategyMetaHtml(data, view)}
    <div class="strat-grid">${strategyTocHtml(view)}
      <article class="strat-doc" id="strat-doc">${blank ? '<p class="strat-none">No strategy yet.</p>' : ''}
        ${strategyFieldHtml('summary', 'Summary', v.summary, view.owner, 'What the team does, for whom, and how it wins')}
        ${strategyFieldHtml('vision', 'Vision', v.vision, view.owner, 'Where the team is going')}
        ${strategyBetsHtml(data, view)}
        ${strategySectionsHtml(view)}
      </article></div>`;
  for (const [key, value] of typed) { const f = body.querySelector(`[data-strat-field="${key}"]`); if (f) f.value = value; }
  strategyBind();
}

// Reads the page again. `quiet` keeps the fields as they are (one may have the focus) and redraws only what depends on
// the draft: the line above the page and the buttons.
async function strategyReload(state, quiet = false) {
  try { state.data = await get('/v2/strategy', {fresh: true}); } catch { /* the saved words stay as drawn */ }
  if (STRAT !== state) return;
  if (!quiet) { strategyRender(); return; }
  const view = strategyView(state.data);
  $('#strat-actions').innerHTML = strategyActionsHtml(state.data, view);
  const old = document.querySelector('#strat-body .strat-meta'), html = strategyMetaHtml(state.data, view);
  if (old) old.outerHTML = html || '';
  else if (html) $('#strat-body .strat-grid')?.insertAdjacentHTML('beforebegin', html);
  strategyBindActions();
}
// Saves, then reads the page again. A draft changed elsewhere (409: its revision moved on) is read again whole, so
// the owner sees what is there now.
async function strategyWrite(run, {quiet = false, fail = 'Could not save'} = {}) {
  const state = STRAT;
  try { await run(); }
  catch (error) {
    if (error?.status === 409 && STRAT === state) {
      toast('Changed elsewhere, reloaded');
      state.editSection = ''; state.addSection = false; state.publishing = false;
      // What was typed lost to the newer draft: the fields draw what is there now.
      for (const f of document.querySelectorAll('[data-strat-field]')) f.value = f.defaultValue;
      await strategyReload(state, false);
      return false;
    }
    toast(error.message || fail, true);
    return false;
  }
  if (STRAT !== state) return true;
  await strategyReload(state, quiet);
  return true;
}
// A draft write: only what changed, with the revision it was read at.
const strategyDraftPut = change => put('/v2/strategy/draft', {...change, revision: STRAT?.data?.draft?.revision ?? null});

const stratAutosize = el => { el.style.height = 'auto'; el.style.height = el.scrollHeight + 2 + 'px'; };

function strategyBindActions() {
  const state = STRAT;
  const actions = $('#strat-actions');
  const versions = actions.querySelector('[data-strat-versions]');
  if (versions) versions.onclick = () => void strategyVersionsOpen();
  const publish = actions.querySelector('[data-strat-publish]');
  if (publish) publish.onclick = () => { state.publishing = true; strategyRender(); $('[data-strat-publish-form] input')?.focus(); };
}

function strategyBind() {
  const state = STRAT, body = $('#strat-body');
  if (!state || !body) return;
  strategyBindActions();
  const data = state.data, view = strategyView(data);

  for (const button of body.querySelectorAll('[data-strat-go]')) button.onclick = () =>
    document.getElementById('strat-sec-' + button.dataset.stratGo)?.scrollIntoView({block: 'start', behavior: 'smooth'});

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

  for (const field of body.querySelectorAll('[data-strat-field]')) {
    const key = field.dataset.stratField;
    stratAutosize(field);
    field.oninput = () => stratAutosize(field);
    // What is saved now: a quiet save reads the draft again without drawing these fields.
    const saved = () => strategyView(state.data).version?.[key] || '';
    field.onkeydown = ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); field.blur(); }
      if (ev.key === 'Escape') { field.value = saved(); stratAutosize(field); field.blur(); }
    };
    field.onblur = () => {
      const text = field.value.trim();
      if (STRAT !== state || text === stratText(saved())) return;
      void strategyWrite(() => strategyDraftPut({[key]: text}), {quiet: true});
    };
  }

  // The bets: add, edit (status included, so retiring is an edit), and move up or down.
  const add = body.querySelector('[data-bet-add]');
  if (add) add.onclick = () => { state.editBet = 'new'; strategyRender(); $('[data-bet-form="new"] input')?.focus(); };
  for (const li of body.querySelectorAll('li[data-bet]')) {
    const id = li.dataset.bet;
    const edit = li.querySelector('[data-bet-edit]');
    if (edit) edit.onclick = () => { state.editBet = id; strategyRender(); $(`[data-bet-form="${CSS.escape(id)}"] input`)?.focus(); };
    for (const [sel, step] of [['[data-bet-up]', -1], ['[data-bet-down]', 1]]) {
      const b = li.querySelector(sel);
      if (b) b.onclick = () => void strategyMove(id, step);
    }
  }
  const bform = body.querySelector('[data-bet-form]');
  if (bform) strategyBindBetForm(bform, state, view);

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

function strategyBindBetForm(bform, state, view) {
  const data = state.data, id = bform.dataset.betForm, el = bform.elements;
  const done = () => { state.editBet = ''; strategyRender(); };
  const whyNot = bform.querySelector('[data-bet-why-not]');
  const say = text => { whyNot.textContent = text; whyNot.hidden = !text; };
  bform.querySelector('[data-bet-cancel]').onclick = done;
  bform.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  el.wrong_kind.onchange = () => {
    for (const box of bform.querySelectorAll('[data-wrong-kind]')) box.hidden = box.dataset.wrongKind !== el.wrong_kind.value;
  };
  const fields = () => ({statement: el.statement.value.trim(), why: el.why.value.trim(), rules_out: el.rules_out.value.trim(),
    wrong_if: strategyWrongFromForm(el), status: el.status.value});
  // Active needs Rules out and Wrong if: chosen without them, the bet stays Proposed and says why.
  const check = () => {
    if (el.status.value !== 'active') { say(''); return true; }
    const refusal = stratActiveRefusal({id, ...fields()}, data.bets, view.one);
    if (!refusal) { say(''); return true; }
    el.status.value = (data.bets || []).find(b => b.id === id)?.status === 'retired' ? 'retired' : 'proposed';
    say(refusal);
    return false;
  };
  el.status.onchange = check;
  bform.onsubmit = async ev => {
    ev.preventDefault();
    if (!el.statement.value.trim()) { el.statement.focus(); return; }
    if (!check()) return;
    const now = fields();
    const was = (data.bets || []).find(c => c.id === id);
    const same = (k, v) => k === 'wrong_if' ? JSON.stringify(v || null) === JSON.stringify(was.wrong_if || null) : v === (was[k] || '');
    const change = was ? Object.fromEntries(Object.entries(now).filter(([k, v]) => !same(k, v))) : now;
    // An emptied wrong-if is sent as {}: null means unchanged.
    if (was && 'wrong_if' in change && !change.wrong_if) change.wrong_if = {};
    if (was && !Object.keys(change).length) { done(); return; }
    bform.querySelectorAll('button').forEach(b => { b.disabled = true; });
    state.editBet = '';
    const ok = await strategyWrite(() => post(was ? `/v2/strategy/bets/${encodeURIComponent(id)}` : '/v2/strategy/bets',
      {...change, revision: state.data?.draft?.revision ?? null}));
    if (!ok && STRAT === state) { state.editBet = id; bform.querySelectorAll('button').forEach(b => { b.disabled = false; }); }
  };
}

// Moving a bet renumbers the live ones 0, 1, 2… and saves only the ranks that changed.
async function strategyMove(id, step) {
  const state = STRAT;
  const live = (state.data.bets || []).filter(c => c.status !== 'retired').sort(stratRank);
  const at = live.findIndex(c => c.id === id), to = at + step;
  if (at < 0 || to < 0 || to >= live.length) return;
  [live[at], live[to]] = [live[to], live[at]];
  // Each save carries the revision the last one answered, so a stale page gets a 409; one that fails midway is read again.
  let revision = state.data.draft?.revision ?? null;
  const ok = await strategyWrite(async () => {
    for (const [rank, c] of live.entries()) {
      if (c.rank !== rank) revision = (await post(`/v2/strategy/bets/${encodeURIComponent(c.id)}`, {rank, revision}))?.revision ?? null;
    }
  }, {fail: 'Could not move it'});
  if (!ok && STRAT === state) await strategyReload(state);
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
        ${block('Vision', stratText(v.vision) ? `<p>${esc(v.vision)}</p>` : '')}
        ${block(label, bets.length ? `<ol class="strat-sv-bets">${bets.map(c => `<li><b>${esc(c.statement)}</b>${stratText(c.why) ? `<div class="muted">${esc(c.why)}</div>` : ''}</li>`).join('')}</ol>` : '')}
        ${strategySectionList(v).map(sec => block(sec.title, stratText(sec.text) ? `<div class="md strat-md">${safeMd(sec.text)}</div>` : '')).join('')}`;
    } catch (error) { html = `<p class="err">${esc(error.message)}</p>`; }
    if (STRAT !== state || !dlg.open) return;
    dlg.querySelector('.strat-sv-body').innerHTML = html;
  })();
}
