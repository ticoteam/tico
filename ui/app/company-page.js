/* ui/app/company-page.js — The Company page section for owners and admins (docs/company-page.md): one screen with
   Transparency, Editions, Audiences, Disclosure, Stats, Readers and Preview, in that order. Each part loads and fails on
   its own. The server decides every right; this file only hides what the viewer can't use. The reader's page itself is
   not drawn here: Preview opens what the server renders.
   Reads and writes: /api/v2/company-page, /api/v2/audiences, /api/v2/editions, /api/v2/readers (backend/company_page).
   An approval decision goes through /api/v2/approvals like every other one.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const CP_ROUTE = '#/company-page';
let CP = null;    // the open page: {settings, audiences, editions, disclosure, stats, projection, readers, invites, err, pick}

const CP_PARTS = [['transparency', 'Transparency'], ['editions', 'Editions'], ['audiences', 'Audiences'], ['disclosure', 'Disclosure'],
  ['stats', 'Stats'], ['readers', 'Readers'], ['preview', 'Preview']];
const CP_LEVELS = [['closed', 'Closed'], ['private', 'Private'], ['open', 'Open']];
const CP_STATUS = {drafting: ['Drafting', ''], held: ['Held', 'blocked'], ready: ['Ready', 'ready'], awaiting_approval: ['Awaiting approval', 'waiting'],
  published: ['Published', 'ok'], superseded: ['Superseded', ''], retracted: ['Retracted', '']};
// The queue: what needs someone first.
const CP_QUEUE = ['held', 'awaiting_approval', 'ready', 'drafting', 'published', 'retracted'];
const CP_TEXT = [['highlights', 'Highlights'], ['lowlights', 'Lowlights'], ['numbers', 'Numbers'], ['asks', 'Asks'], ['next', 'Next']];
const CP_MODES = [['show', 'Show'], ['rounded', 'Rounded'], ['range', 'Range'], ['delayed', 'Delayed'], ['hidden', 'Hidden']];
const CP_PRESETS = [['radical', 'Radical'], ['standard', 'Standard'], ['quiet', 'Quiet']];
const CP_CHARTS = [['number', 'Number'], ['line', 'Line'], ['bars', 'Bars']];
const CP_CADENCE = [['monthly', 'Monthly'], ['quarterly', 'Quarterly']];
const CP_DUE = [['business_day', 'Business day of the month'], ['days_after_period_end', 'Days after period end']];
const CP_CHECKS = {leak: 'Leak check', rules: 'Rules', draft: 'Draft'};
// A check's reason from the server, in words. Anything not listed reads as held.
const CP_HELD = {leak: 'May say something this audience can\'t see', broken: 'Breaks a rule', empty: 'Nothing in it yet', facts: 'Couldn\'t read the numbers',
  checks_off: 'Rule checks are off', too_many_rules: 'Too many rules to check', too_large: 'Too long to check', budget: 'Over the checks budget',
  no_engine: 'No model to run this', day_cap: 'Checks hit today\'s limit', shape: 'The checks gave no clear answer', error: 'The checks failed', unchecked: 'Not checked',
  malformed: 'The draft came back unreadable', no_answer: 'The draft got no answer'};
const cpHeldWord = reason => CP_HELD[reason] || 'Held by the checks';
// The classes the server knows (disclosure.DATA_CLASSES), in the order they read best. A class that isn't here still gets a row.
const CP_CLASSES = [['revenue', 'Revenue'], ['growth', 'Growth'], ['gross_margin', 'Gross margin'], ['burn', 'Burn'], ['cash_runway', 'Cash and runway'], ['fundraising', 'Fundraising'],
  ['ai_spend', 'AI spend'], ['pnl_line', 'All P&L lines'], ['headcount', 'Headcount'], ['customers', 'Customers'], ['customer_names', 'Customer names'], ['pipeline', 'Pipeline'],
  ['kpi', 'All KPIs'], ['product', 'Product and roadmap'], ['people', 'People'], ['legal', 'Legal'], ['security', 'Security incidents'], ['mission', 'Mission']];
const CP_GROUPS = {kpi: 'KPIs', pnl_line: 'P&L lines'};
// What a stat can come from without a list from the server: Finance's own numbers (stats.METRIC_LABEL).
const CP_METRICS = [['revenue', 'Revenue'], ['spend', 'Spend'], ['burn', 'Burn'], ['cash', 'Cash'], ['runway_months', 'Runway'], ['gross_margin', 'Gross margin']];

// ----------------------------------------------------------------- who sees it
const cpAllowed = () => S.me?.role === 'owner' || S.me?.bot_admin === true;
// The link shows to owners and admins only, under Finance.
function cpNavSync() {
  const nav = $('#side .side-scroll'); if (!nav) return;
  const link = nav.querySelector('a[data-nav="company-page"]');
  if (!cpAllowed()) { if (link) { link.remove(); renderTree(); } return; }
  if (link) return;
  const a = Object.assign(document.createElement('a'), {className: 'nav-link', href: CP_ROUTE,
    innerHTML: '<span class="nav-icon" aria-hidden="true">newspaper</span>Company page'});
  a.dataset.nav = 'company-page';
  const after = nav.querySelector('a[data-nav="finance"]') || nav.querySelector('a[data-nav="growth"]') || nav.querySelector('a[data-nav="strategy"]');
  if (after) after.after(a); else nav.prepend(a);
  renderTree();
}

// ----------------------------------------------------------------- small pieces
const cpList = (r, key) => Array.isArray(r) ? r : Array.isArray(r?.[key]) ? r[key] : [];
const cpId = id => encodeURIComponent(id);
const cpAud = id => (CP?.audiences || []).find(a => a.id === id);
const cpAudName = id => cpAud(id)?.name || '';
const cpPrivateOn = () => CP?.signIn?.available !== false;
const cpPrivateOff = () => `<p class="cp-note" data-cp-private-off>${esc(CP?.signIn?.reason || 'Private audiences are off')}. <a href="#/settings/people">Settings</a></p>`;
const cpPrivateAudiences = () => (CP?.audiences || []).filter(a => a.kind !== 'public');
function cpWho(ref) {
  const s = String(ref || '');
  if (!s) return '';
  if (s === 'company') return 'Company';
  if (s.startsWith('human:')) return personDisplay(s.slice(6));
  return botDisplayName(s.startsWith('bot:') ? s.slice(4) : s);
}
const cpPill = status => { const [label, cls] = CP_STATUS[status] || [status, '']; return `<span class="pill ${cls}">${esc(label)}</span>`; };
// A whole month reads "Oct 2026"; anything else as its two dates.
function cpPeriod(start, end) {
  if (!start && !end) return '';
  const a = finDate(start), b = finDate(end), last = new Date(b.getFullYear(), b.getMonth() + 1, 0).getDate();
  if (a.getDate() === 1 && a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear() && b.getDate() === last) return `${FIN_MON[a.getMonth()]} ${a.getFullYear()}`;
  return `${finDayLabel(start)} – ${finDayLabel(end)}`;
}
const cpOptions = (pairs, value) => pairs.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('');
const cpAudOptions = (auds, value) => cpOptions(auds.map(a => [a.id, a.name]), value);
const cpEmpty = (line, action = '') => `<div class="empty-state cp-empty"><span>${esc(line)}</span>${action}</div>`;
const cpBtn = (attr, label, cls = 'ghost small') => `<button type="button" class="${cls}" ${attr}>${esc(label)}</button>`;
const cpSection = (id, actions, body) => `<header><h2>${esc(CP_PARTS.find(p => p[0] === id)[1])}</h2>${actions ? `<span class="spacer"></span><span class="cp-acts">${actions}</span>` : ''}</header>${body}`;
function cpFail(id, error) {
  const denied = error?.status === 403;
  return `<div class="cp-fail" role="alert"><span class="muted">${esc(denied ? 'Owners and admins only' : error?.message || 'Did not load')}</span>${denied ? '' : cpBtn(`data-cp-retry="${esc(id)}"`, 'Retry')}</div>`;
}

// A small dialog with a form: `onSave(form)` throws to show an error and returns false to keep it open. A button runs
// once; nothing here uses the browser's own dialogs, so it looks and works the same in the desktop app.
function cpDialog(title, body, save, onSave, {danger = false, wide = false} = {}) {
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal cp-dlg' + (wide ? ' cp-wide' : '');
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body cp-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="cp-foot"><button type="button" class="ghost" data-close>${save ? 'Cancel' : 'Close'}</button>${save ? `<button type="submit" class="primary${danger ? ' people-danger' : ''}">${esc(save)}</button>` : ''}</div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector('[data-error]'), go = dlg.querySelector('[type=submit]');
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!go) return;
    err.hidden = true; go.disabled = true;
    try { if (await onSave(form) === false) go.disabled = false; else dlg.close(); }
    catch (e) { err.textContent = e.message; err.hidden = false; go.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('input:not([type=hidden]):not([type=checkbox]),textarea')?.focus();
  return dlg;
}
const cpConfirm = (title, line, label, work, opts = {danger: true}) => cpDialog(title, `<p class="cp-line">${esc(line)}</p>`, label, async () => { await work(); }, opts);

// ----------------------------------------------------------------- the page
function pageCompanyPage() {
  cpTeardown();
  const main = $('#main');
  main.classList.add('strategy-layout', 'company-layout');
  const head = '<div class="strat-head rd-strat-head cp-head"><h1>Company page</h1></div>';
  if (!cpAllowed()) { main.innerHTML = head + cpEmpty('Owners and admins only'); return; }
  const state = CP = {err: {}, pick: {}, groups: {}, audiences: null, disclosure: {}, editions: null, editionsNext: null, readers: null, readersNext: null,
    stats: null, available: [], projection: {}, invites: {}, settings: null, timer: null, mq: matchMedia(PHONE), onMq: null};
  main.innerHTML = head + `<div id="cp-page" class="cp-page">${CP_PARTS.map(([id]) => `<section class="card cp-sec" id="cp-${id}">${cpSection(id, '', skelRows(2, {dot: false}))}</section>`).join('')}</div>`;
  state.onMq = () => { if (CP === state) cpDraw('disclosure'); };
  state.mq.addEventListener('change', state.onMq);
  const root = $('#cp-page');
  root.addEventListener('click', cpClick);
  root.addEventListener('change', cpChange);
  const want = new URLSearchParams((S.route || '').split('?')[1] || '').get('edition');
  void cpLoadAll(state).then(() => { if (want && CP === state) cpOpenEdition(want); });
}
function cpTeardown() {
  document.querySelectorAll('dialog.cp-dlg').forEach(d => d.close());
  if (!CP) return;
  clearTimeout(CP.timer);
  CP.mq.removeEventListener('change', CP.onMq);
  CP = null;
}
function companyPageStop() { if (CP && !rdUnder(S.route || '', CP_ROUTE)) cpTeardown(); }

// One part's read: its failure stays in that part, with Retry. A 403 on the settings means the whole page is not theirs.
const CP_LOAD = {
  transparency: async s => { s.settings = await get('/v2/company-page', {fresh: true}); },
  audiences: async s => {
    const list = cpList(await get('/v2/audiences', {fresh: true}), 'audiences');
    const withSources = await Promise.all(list.map(async a => ({...a, sources: a.sources || cpList(await get(`/v2/audiences/${cpId(a.id)}/sources`, {fresh: true}), 'sources')})));
    s.audiences = withSources;
    s.pick.disclosure = cpAud(s.pick.disclosure)?.id || list[0]?.id || '';
    s.pick.stats = cpAud(s.pick.stats)?.id || list[0]?.id || '';
    s.pick.invites = cpAud(s.pick.invites)?.id || cpPrivateAudiences()[0]?.id || '';
  },
  editions: async s => {
    const r = await get('/v2/editions', {fresh: true});
    s.editions = cpList(r, 'editions'); s.editionsNext = cpNextQuery(r);
    cpWatchDrafts(s);
  },
  disclosure: async s => {
    const reads = await Promise.all((s.audiences || []).map(async a => [a.id, await get(`/v2/audiences/${cpId(a.id)}/disclosure`, {fresh: true})]));
    s.disclosure = Object.fromEntries(reads);
  },
  stats: async s => {
    const r = await get('/v2/company-page/stats', {fresh: true});
    s.stats = cpList(r, 'stats').sort((a, b) => (a.position ?? 0) - (b.position ?? 0)); s.available = cpList(r?.available, 'available');
    if (!s.pick.stats) return;
    const [board, ask] = await Promise.all([get(`/v2/audiences/${cpId(s.pick.stats)}/stats`, {fresh: true}), get(`/v2/audiences/${cpId(s.pick.stats)}/stats/approval`, {fresh: true})]);
    s.projection[s.pick.stats] = {...board, approval: ask?.approval || null};
  },
  readers: async s => {
    const r = await get('/v2/readers', {fresh: true});
    s.readers = cpList(r, 'readers'); s.readersNext = cpNextQuery(r); s.signIn = r?.sign_in || null;
    if (s.pick.invites) s.invites[s.pick.invites] = cpList(await get(`/v2/audiences/${cpId(s.pick.invites)}/invites`, {fresh: true}), 'invites');
  },
};
async function cpLoad(state, id) {
  try { await CP_LOAD[id](state); delete state.err[id]; }
  catch (e) { state.err[id] = e; }
  if (CP !== state) return;
  cpDraw(id);
  // Whether Private can work is learned with the readers; the grid has a row for each KPI and line on the board.
  if (id === 'readers') cpDraw('transparency');
  if (id === 'stats') {
    const board = (state.stats || []).map(x => x.id).join();
    if (board !== state.boardDrawn) { state.boardDrawn = board; cpDraw('disclosure'); }
  }
}
// The parts that need the audiences follow them; a redraw of one audience's data doesn't reload the others.
async function cpLoadAudienceParts(state) {
  await cpLoad(state, 'audiences');
  if (CP !== state) return;
  cpDraw('preview'); cpDraw('editions');
  await Promise.all(['disclosure', 'stats', 'readers'].map(id => cpLoad(state, id)));
}
async function cpLoadAll(state) {
  await cpLoad(state, 'transparency');
  if (CP !== state) return;
  if (state.err.transparency?.status === 403) {
    $('#cp-page').innerHTML = cpEmpty('Owners and admins only');
    return;
  }
  await Promise.all([cpLoad(state, 'editions'), cpLoadAudienceParts(state)]);
}
const CP_DRAW = {transparency: cpTransparency, editions: cpEditions, audiences: cpAudiences, disclosure: cpDisclosure, stats: cpStats, readers: cpReaders, preview: cpPreview};
function cpDraw(id) {
  const el = $('#cp-' + id); if (!el || !CP) return;
  el.innerHTML = CP.err[id] ? cpSection(id, '', cpFail(id, CP.err[id])) : CP_DRAW[id]();
}
// A write's result: read the part again and draw it, so the screen shows what the server holds.
const cpReload = (...ids) => Promise.all(ids.map(id => cpLoad(CP, id)));

function cpClick(ev) {
  const b = ev.target.closest('button,[data-cp-edition]'); if (!b) return;
  const d = b.dataset;
  if (d.cpRetry) return void (d.cpRetry === 'audiences' ? cpLoadAudienceParts(CP) : cpLoad(CP, d.cpRetry));
  if (d.v && b.closest('[data-cp-level]')) return cpChooseLevel(d.v);
  if ('cpAddress' in d) return cpEditAddress();
  if (d.cpEdition) return void cpOpenEdition(d.cpEdition);
  if ('cpDraft' in d) return cpDraftNow();
  if ('cpEditionsMore' in d) return void cpMore('editions');
  if (d.cpAudience !== undefined) return cpAudienceEdit(cpAud(d.cpAudience));
  if (d.cpGroup) { CP.groups[d.cpGroup] = !CP.groups[d.cpGroup]; return cpDraw('disclosure'); }
  if ('cpAddStat' in d) return void cpAddStat();
  if (d.cpStatRemove) return cpStatRemove(d.cpStatRemove);
  if (d.cpStatMove) return void cpStatMove(d.cpStatMove, Number(d.dir));
  if ('cpStatsRequest' in d) return void cpStatsRequest();
  if (d.cpStatsApprove) return void cpStatsDecide(d.cpStatsApprove);
  if ('cpAddReader' in d) return cpAddReader();
  if ('cpImport' in d) return cpImportReaders();
  if ('cpReadersMore' in d) return void cpMore('readers');
  if (d.cpReaderRemove) return cpRemoveReader(d.cpReaderRemove);
  if ('cpNewInvite' in d) return void cpNewInvite();
  if (d.cpInviteRevoke) return cpRevokeInvite(d.cpInviteRevoke);
}
function cpChange(ev) {
  const el = ev.target, d = el.dataset;
  if (d.cpPick) return void cpPick(d.cpPick, el.value);
  if (d.cpPreset !== undefined) { const name = el.value; el.value = ''; return name && cpPreset(d.cpPreset, name); }
  if (d.cpMode !== undefined) return void cpSetMode(el);
  if (d.cpLag !== undefined) return void cpSetLag(el);
  if (d.cpStatChart) return void cpStatChart(d.cpStatChart, el.value);
}
// A part's "next page": the list grows in place. Editions page by date, readers by offset.
const cpNextQuery = r => r?.next_before ? `before=${encodeURIComponent(r.next_before)}` : r?.next != null ? `offset=${encodeURIComponent(r.next)}` : '';
async function cpMore(id) {
  const state = CP, editions = id === 'editions', next = editions ? state.editionsNext : state.readersNext;
  if (!next) return;
  try {
    const r = await get(`/v2/${id}?${next}`, {fresh: true});
    if (CP !== state) return;
    if (editions) { state.editions.push(...cpList(r, 'editions')); state.editionsNext = cpNextQuery(r); }
    else { state.readers.push(...cpList(r, 'readers')); state.readersNext = cpNextQuery(r); }
    cpDraw(id);
  } catch (e) { toast(e.message, true); }
}
// A picker for "which audience" in Disclosure, Stats or Readers: reads what that audience needs.
async function cpPick(what, id) {
  const state = CP; state.pick[what] = id;
  if (what === 'disclosure') return cpDraw('disclosure');
  await cpLoad(state, what === 'stats' ? 'stats' : 'readers');
}

// ----------------------------------------------------------------- 1. Transparency
function cpTransparency() {
  const s = CP.settings || {}, level = s.transparency || 'closed', privateOn = cpPrivateOn();
  const seg = `<div class="theme-seg cp-seg" role="radiogroup" aria-label="Transparency" data-cp-level>${CP_LEVELS.map(([v, l]) =>
    `<button type="button" role="radio" data-v="${v}" aria-checked="${v === level}"${v === 'private' && !privateOn ? ' disabled' : ''}>${l}</button>`).join('')}</div>`;
  const url = cpAddress(s);
  const address = level !== 'closed' && url ? `<a class="cp-address" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url.replace(/^https?:\/\//, ''))}<span class="nav-icon" aria-hidden="true">open_in_new</span></a>` : '';
  // An install with its own reader address has nothing to set here. Without one, the address is what Open needs first.
  const set = s.reader_origin ? '' : cpBtn('data-cp-address', s.slug ? 'Change address' : 'Set address');
  return cpSection('transparency', '', `<div class="cp-level">${seg}${address}${set}</div>${privateOn ? '' : cpPrivateOff()}`);
}
const cpAddress = s => s.reader_origin || (s.slug ? `https://${s.slug}.tico.page` : '');
function cpEditAddress() {
  const s = CP.settings || {}, live = s.slug && s.transparency !== 'closed';
  cpDialog('Address', `${live ? '<p class="cp-line">Links to the old address stop working.</p>' : ''}<label>Address<span class="cp-slug"><input name="slug" value="${esc(s.slug || '')}" maxlength="63" autocomplete="off" spellcheck="false"><span class="muted">.tico.page</span></span></label>`, 'Save', async form => {
    const slug = form.slug.value.trim();
    if (!slug) throw new Error('Enter an address');
    const r = await patch('/v2/company-page', {slug});
    CP.settings = {...CP.settings, ...r};
    cpDraw('transparency');
  });
}
// What a change takes away, in one line, before it runs. Going to Open warns that it is the public internet. Closed to
// Private takes nothing away and shows nobody anything new until they are added, so it runs at once.
function cpChooseLevel(to) {
  const from = CP.settings?.transparency || 'closed';
  if (to === from) return;
  const run = () => cpSetLevel(to);
  const kept = 'Feed readers and search engines may keep what they fetched.';
  if (to === 'open') return void cpConfirm('Go public?', 'Anyone on the public internet can read this page, and search engines can index it.', 'Go public', run, {danger: false});
  if (from === 'open' && to === 'private') return void cpConfirm('Make private?', `The public page comes down now. ${kept}`, 'Make private', run);
  if (from === 'open') return void cpConfirm('Close the page?', `The public and private pages come down now. ${kept}`, 'Close', run);
  if (to === 'closed') return void cpConfirm('Close the page?', 'Readers lose access now.', 'Close', run);
  void run().catch(e => toast(e.message, true));
}
async function cpSetLevel(to) {
  const state = CP;
  const r = await patch('/v2/company-page', {transparency: to});
  if (CP !== state) return;
  if (r?.transparency) state.settings = {...state.settings, ...r};
  else await cpLoad(state, 'transparency');
  cpDraw('transparency');
}

// ----------------------------------------------------------------- 2. Editions: the queue
const cpMine = approval => !!approval?.to && approval.to === myActor();
const cpApprover = approval => approval?.to_name || cpWho(approval?.to);
// Why an edition is held, from its last check. A failed draft has no check yet: its dialog says why.
const cpHeldWhy = e => e.status === 'held' && e.last_check?.outcome === 'held' ? (e.last_check.reasons || []).map(cpHeldWord).join(' · ') : '';
function cpQueue(editions) {
  const rank = e => { const i = CP_QUEUE.indexOf(e.status); return i < 0 ? 99 : i; };
  // Stable: within a status the server's order stands, and an approval waiting on the viewer comes first.
  return editions.filter(e => e.status !== 'superseded')
    .map((e, i) => [e, i]).sort(([a, i], [b, j]) => rank(a) - rank(b) || Number(cpMine(b.approval)) - Number(cpMine(a.approval)) || i - j).map(([e]) => e);
}
function cpEditionRow(e) {
  const state = e.status === 'awaiting_approval' ? e.approval?.state : '';
  const where = {pending: cpMine(e.approval) ? 'Waiting on you' : `Waiting on ${cpApprover(e.approval)}`, approved: 'Approved', declined: 'Declined', lapsed: 'Approval lapsed', stale: 'Approval out of date'}[state] || '';
  const meta = [cpAudName(e.audience_id), cpPeriod(e.period_start, e.period_end), e.author?.kind === 'company' ? '' : e.author?.name, where].filter(Boolean).join(' · ');
  const reason = cpHeldWhy(e), why = reason ? `<span class="cp-why">${esc(reason)}</span>` : '';
  return `<button type="button" class="cp-ed-row" data-cp-edition="${esc(e.id)}">${cpPill(e.status)}<span class="cp-ed-t"><b>${esc(e.title || 'Untitled')}</b><span class="muted">${esc(meta)}</span>${why}</span><span class="nav-icon cp-chev" aria-hidden="true">chevron_right</span></button>`;
}
function cpEditions() {
  const list = cpQueue(CP.editions || []), draft = cpBtn('data-cp-draft', 'Draft now', 'primary small');
  if (CP.editions === null) return cpSection('editions', '', skelRows(3, {dot: false}));
  if (!list.length) return cpSection('editions', '', cpEmpty('No editions yet', draft));
  return cpSection('editions', draft, `<div class="cp-queue">${list.map(cpEditionRow).join('')}</div>${CP.editionsNext ? cpBtn('data-cp-editions-more', 'Show more', 'ghost small cp-more') : ''}`);
}
// A draft is one server call and takes a moment: the queue reads again until none is left drafting.
function cpWatchDrafts(state) {
  clearTimeout(state.timer);
  if (!(state.editions || []).some(e => e.status === 'drafting')) return;
  state.timer = setTimeout(() => { if (CP === state) void cpLoad(state, 'editions'); }, 5000);
}
// Draft now: an out-of-cycle edition for last month by default.
function cpDraftNow() {
  const auds = (CP.audiences || []).filter(a => a.enabled !== 0 && a.enabled !== false);
  const all = auds.length ? auds : CP.audiences || [];
  if (!all.length) return void toast('Add an audience first', true);
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth() - 1, 1), last = new Date(now.getFullYear(), now.getMonth(), 0);
  const sourceOptions = id => {
    const sources = cpAud(id)?.sources || [];
    return cpOptions(sources.length ? sources.map(x => [x.author, x.name || cpWho(x.author)]) : [['company', 'Company']]);
  };
  const dlg = cpDialog('Draft now', `<label>Audience<select name="audience">${cpAudOptions(all, all[0].id)}</select></label>
    <label>Source<select name="author">${sourceOptions(all[0].id)}</select></label>
    <div class="cp-two"><label>From<input type="date" name="start" value="${finIso(first)}"></label><label>To<input type="date" name="end" value="${finIso(last)}"></label></div>`, 'Draft', async form => {
    if (!form.start.value || !form.end.value || form.start.value > form.end.value) throw new Error('Choose a period');
    const r = await post('/v2/editions', {audience_id: form.audience.value, author: form.author.value, period_start: form.start.value, period_end: form.end.value});
    await cpReload('editions');
    if (r?.id) void cpOpenEdition(r.id);
  });
  dlg.querySelector('[name=audience]').onchange = ev => { dlg.querySelector('[name=author]').innerHTML = sourceOptions(ev.target.value); };
}

// ----------------------------------------------------------------- 2. Editions: one edition
const cpTextOf = e => Array.isArray(e.sections) ? e.sections : [];
// The full text in the order and with the labels of the reader's page.
function cpReadHtml(e) {
  const sections = cpTextOf(e);
  const parts = CP_TEXT.map(([key, label]) => {
    const items = sections.find(x => x.key === key)?.items || [];
    return items.length ? `<section class="cp-read-sec"><h4>${label}</h4><ul>${items.map(i => `<li>${safeMd(i.body)}</li>`).join('')}</ul></section>` : '';
  }).join('');
  return `<article class="cp-read" aria-label="Full text"><h3>${esc(e.title || 'Untitled')}</h3>${e.correction_note ? `<p class="cp-correction">Corrected: ${esc(e.correction_note)}</p>` : ''}${parts || '<p class="muted">No text yet</p>'}</article>`;
}
const cpCheckOk = c => c.outcome === 'pass';
// What a check found, in words: the leak check lists what it can't place, the rules check quotes the rule that broke.
function cpCheckReason(c) {
  if (cpCheckOk(c)) return '';
  const d = c.detail || {}, found = [['Names', d.names], ['Numbers', d.numbers], ['Links', d.links]].filter(([, v]) => v?.length).map(([k, v]) => `${k}: ${v.join(', ')}`);
  if (found.length) return found.join('. ');
  const broken = (d.broken || []).map(id => (d.rules || []).find(r => r.id === id)?.text).filter(Boolean);
  return broken.length ? broken.join('. ') : cpHeldWord(d.reason);
}
// The last run of each check on this version: the server keeps every run, newest first.
function cpChecksHtml(e) {
  const seen = new Set();
  const latest = cpList(e.checks, 'checks').filter(c => c.version === e.version && !seen.has(c.kind) && seen.add(c.kind));
  const checks = latest.filter(c => c.kind === 'leak' || c.kind === 'rules' || (c.kind === 'draft' && !cpCheckOk(c)));
  if (!checks.length) return '<p class="muted cp-small">Not checked yet</p>';
  return `<ul class="cp-checks">${checks.sort((a, b) => Number(cpCheckOk(a)) - Number(cpCheckOk(b))).map(c => {
    const ok = cpCheckOk(c), reason = cpCheckReason(c);
    return `<li class="cp-check"><span class="pill ${ok ? 'ok' : 'blocked'}">${esc(ok ? 'Passed' : c.outcome === 'flagged' ? 'Flagged' : 'Failed')}</span><span class="cp-check-t"><b>${esc(CP_CHECKS[c.kind] || c.kind)}</b>${reason ? `<span>${esc(reason)}</span>` : ''}</span></li>`;
  }).join('')}</ul>`;
}
function cpSourcesHtml(e) {
  const sources = cpList(e.sources, 'sources');
  if (!sources.length) return '<p class="muted cp-small">None</p>';
  return sources.map((x, i) => `<details class="cp-source"><summary>${esc(`Update ${i + 1}`)}</summary><div class="cp-source-t">${safeMd(x.body)}</div></details>`).join('');
}
// Page views of a private edition; a public page has no reader to name.
function cpSeenHtml(e) {
  if (cpAud(e.audience_id)?.kind === 'public' || e.status !== 'published' && e.status !== 'retracted') return '';
  const seen = cpList(e.seen_by, 'seen_by');
  const count = e.seen_count ?? seen.length;
  return `<h4 class="label cp-h">Seen by${count ? ` <span class="tnum">${count}</span>` : ''}</h4>${seen.length
    ? `<ul class="cp-seen">${seen.map(x => `<li><span>${esc(x.name || x.email || '')}</span><span class="muted">${esc(finDayLabel(x.viewed_at))}</span></li>`).join('')}</ul>` : '<p class="muted cp-small">Nobody yet</p>'}`;
}
// What can be done from here. Approve and Decline only for the person it is addressed to; the server checks that too.
function cpEdActions(e) {
  const a = e.approval, btn = (act, label, cls = 'ghost') => `<button type="button" class="${cls}" data-act="${act}">${esc(label)}</button>`;
  if (e.status === 'published') return btn('correct', 'Correct') + btn('retract', 'Retract', 'ghost danger');
  if (e.status === 'retracted' || e.status === 'superseded' || e.status === 'drafting') return '';
  const out = [btn('edit', 'Edit'), btn('check', 'Check'), btn('discard', 'Discard', 'ghost danger')];
  if (e.status === 'held') out.push(btn('override', 'Override'));
  if (e.status === 'ready') out.push(btn('request', 'Request approval', 'primary'));
  if (e.status !== 'awaiting_approval') return out.join('');
  if (a?.state === 'approved') out.push(btn('publish', 'Publish', 'primary'));
  else if (a?.state === 'pending') { if (cpMine(a)) out.push(btn('decline', 'Decline'), btn('approve', 'Approve', 'primary')); }
  else out.push(btn('request', 'Request approval', 'primary'));
  return out.join('');
}
function cpEdNotes(e) {
  const a = e.status === 'awaiting_approval' ? e.approval : null, notes = [];
  if (a?.state === 'lapsed') notes.push('Approval lapsed. Ask again.');
  else if (a?.state === 'stale') notes.push('The text, disclosure or rules changed after this was asked. Ask again.');
  else if (a?.state === 'declined') notes.push(`Declined by ${cpApprover(a)}`);
  else if (a?.state === 'pending' && !cpMine(a)) notes.push(`Waiting on ${cpApprover(a)}`);
  else if (a?.state === 'approved') notes.push(`Approved by ${cpApprover(a)}`);
  if (e.checks_current === false && (e.status === 'ready' || e.status === 'held') && e.last_check) notes.push('Something changed since the last check.');
  if (e.override_note) notes.push(`Override: ${e.override_note}`);
  if (e.retraction_note) notes.push(`Retracted: ${e.retraction_note}`);
  return notes.map(n => `<p class="cp-note">${esc(n)}</p>`).join('');
}
function cpEdDraw(dlg, e) {
  dlg.dataset.edition = e.id;
  dlg.querySelector('[data-ed-pill]').innerHTML = cpPill(e.status);
  const meta = [cpAudName(e.audience_id), cpPeriod(e.period_start, e.period_end), e.version > 1 ? `v${e.version}` : '', e.author?.name].filter(Boolean).join(' · ');
  const actions = cpEdActions(e);
  dlg.querySelector('[data-ed-body]').innerHTML = `<p class="muted cp-small">${esc(meta)}</p>${cpEdNotes(e)}
    ${actions ? `<div class="cp-ed-acts">${actions}</div>` : ''}
    ${cpReadHtml(e)}
    <h4 class="label cp-h">Checks</h4>${cpChecksHtml(e)}
    <h4 class="label cp-h">Sources</h4>${cpSourcesHtml(e)}
    ${cpSeenHtml(e)}`;
  dlg.querySelectorAll('[data-act]').forEach(b => { b.onclick = () => cpEdAct(dlg, e, b.dataset.act); });
}
async function cpOpenEdition(id) {
  const state = CP;
  document.querySelectorAll('dialog.cp-ed').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal cp-dlg cp-ed cp-wide';
  dlg.setAttribute('aria-label', 'Edition');
  dlg.innerHTML = `<div class="tmodal-head"><span data-ed-pill></span><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <div class="tmodal-body" data-ed-body>${skelRows(5, {dot: false})}</div>`;
  document.body.appendChild(dlg);
  dlg.querySelector('[data-close]').onclick = () => dlg.close();
  dlg.onclose = () => dlg.remove();
  dlg.showModal();
  try {
    const e = await get(`/v2/editions/${cpId(id)}`, {fresh: true});
    if (CP === state && dlg.isConnected) cpEdDraw(dlg, e);
  } catch (e) {
    if (dlg.isConnected) dlg.querySelector('[data-ed-body]').innerHTML = `<p class="muted">${esc(e.status === 403 ? 'Owners and admins only' : e.status === 404 ? 'Edition not found' : e.message)}</p>`;
  }
}
// After any write: read the edition and the queue again.
async function cpEdRefresh(dlg, id) {
  const state = CP;
  await cpReload('editions');
  if (CP !== state || !dlg.isConnected) return;
  cpEdDraw(dlg, await get(`/v2/editions/${cpId(id)}`, {fresh: true}));
}
// One button: runs, says so, and shows what the server now holds. Failure stays on this dialog.
async function cpEdRun(dlg, e, work, done) {
  dlg.querySelectorAll('[data-act]').forEach(b => { b.disabled = true; });
  try { await work(); toast(done, false, null, {host: dlg}); await cpEdRefresh(dlg, e.id); }
  catch (err) {
    toast(err.message, true, null, {host: dlg});
    dlg.querySelectorAll('[data-act]').forEach(b => { b.disabled = false; });
  }
}
function cpEdAct(dlg, e, act) {
  const api = `/v2/editions/${cpId(e.id)}`, name = cpAudName(e.audience_id), isPublic = cpAud(e.audience_id)?.kind === 'public';
  if (act === 'edit') return cpEdEdit(dlg, e);
  if (act === 'correct') return cpEdCorrect(dlg, e);
  if (act === 'check') return void cpEdRun(dlg, e, () => post(api + '/check', {}), 'Checked');
  if (act === 'request') return void cpEdRun(dlg, e, () => post(api + '/approval', {}), 'Approval requested');
  // The person's own click on the text above, through the approvals API. No token or Assistant decides this one.
  if (act === 'approve') return void cpEdRun(dlg, e, () => post(`/v2/approvals/${cpId(e.approval.id)}`, {decision: 'approved'}), 'Approved');
  if (act === 'decline') return void cpEdRun(dlg, e, () => post(`/v2/approvals/${cpId(e.approval.id)}`, {decision: 'declined'}), 'Declined');
  if (act === 'publish') return void cpConfirm(`Publish to ${name}?`, isPublic ? 'It goes on the public page now.' : 'Readers can see it now, and get an email with a link.', 'Publish', async () => {
    await post(api + '/publish', {approval_id: e.approval?.id});
    toast('Published', false, null, {host: dlg});
    await cpEdRefresh(dlg, e.id);
  }, {danger: false});
  if (act === 'retract') return void cpDialog('Retract this edition?', `<p class="cp-line">${esc(isPublic ? 'It comes off the public page now. Feed readers and search engines may keep what they fetched.' : 'Readers can\'t open it after this. Emails already sent can\'t be recalled.')}</p>
    <label>Note<input name="note" maxlength="2000" required></label>`, 'Retract', async form => {
    const note = form.note.value.trim();
    if (!note) throw new Error('A note is required');
    await post(api + '/retract', {note});
    toast('Retracted', false, null, {host: dlg});
    await cpEdRefresh(dlg, e.id);
  }, {danger: true});
  // The same way out, for one nobody has seen: it leaves the queue and any open approval is withdrawn.
  if (act === 'discard') return void cpDialog('Discard this edition?', `<p class="cp-line">It leaves the queue and any approval request is withdrawn. Nobody has seen it.</p>
    <label>Note<input name="note" maxlength="2000" required></label>`, 'Discard', async form => {
    const note = form.note.value.trim();
    if (!note) throw new Error('A note is required');
    await post(api + '/retract', {note});
    toast('Discarded', false, null, {host: dlg});
    await cpEdRefresh(dlg, e.id);
  }, {danger: true});
  if (act === 'override') return void cpDialog('Override the checks?', `<p class="cp-line">Lets this edition go on to approval. Your note is kept with it.</p>
    <label>Note<textarea name="note" rows="3" maxlength="2000" required></textarea></label>`, 'Override', async form => {
    const note = form.note.value.trim();
    if (!note) throw new Error('A note is required');
    await post(api + '/override', {note});
    toast('Overridden', false, null, {host: dlg});
    await cpEdRefresh(dlg, e.id);
  }, {danger: true});
}
// The text as one box per section, one item a line (an item is one short plain line on the server). An item whose words
// stay keeps its ask, so a reader's reply still points at it.
function cpEditorFields(e) {
  const sections = cpTextOf(e);
  return `<label>Title<input name="title" value="${esc(e.title || '')}" maxlength="200"></label>${CP_TEXT.map(([key, label]) => {
    const text = (sections.find(x => x.key === key)?.items || []).map(i => i.body).join('\n');
    return `<label>${label}<textarea name="s-${key}" rows="${Math.min(10, Math.max(3, text.split('\n').length + 1))}">${esc(text)}</textarea></label>`;
  }).join('')}`;
}
function cpEditorSections(form, e) {
  const sections = cpTextOf(e);
  return CP_TEXT.map(([key]) => {
    const before = sections.find(x => x.key === key)?.items || [];
    const items = form['s-' + key].value.split('\n').map(t => t.trim()).filter(Boolean)
      .map(body => ({body, ask_id: before.find(i => i.body === body)?.ask_id ?? null}));
    return {key, items};
  }).filter(x => x.items.length);
}
function cpEdEdit(dlg, e) {
  const waiting = e.status === 'awaiting_approval' ? '<p class="cp-line">Saving cancels the approval request.</p>' : '';
  cpDialog('Edit', waiting + cpEditorFields(e), 'Save', async form => {
    await patch(`/v2/editions/${cpId(e.id)}`, {title: form.title.value.trim(), sections: cpEditorSections(form, e)});
    await cpEdRefresh(dlg, e.id);
  }, {wide: true});
}
function cpEdCorrect(dlg, e) {
  cpDialog('Correct', `<p class="cp-line">Replaces the published text after approval, and says what changed.</p>${cpEditorFields(e)}
    <label>What changed<input name="note" maxlength="300" required></label>`, 'Save correction', async form => {
    const note = form.note.value.trim();
    if (!note) throw new Error('Say what changed');
    const r = await post(`/v2/editions/${cpId(e.id)}/correct`, {title: form.title.value.trim(), sections: cpEditorSections(form, e), note});
    await cpReload('editions');
    if (r?.id && r.id !== e.id) { dlg.close(); void cpOpenEdition(r.id); }
    else await cpEdRefresh(dlg, e.id);
  }, {wide: true});
}

// ----------------------------------------------------------------- 3. Audiences
function cpDueText(a) {
  let rule = a.due_rule;
  if (typeof rule === 'string') { try { rule = JSON.parse(rule); } catch { rule = {}; } }
  const n = Number(rule?.n);
  if (!rule?.kind || !Number.isInteger(n)) return '';
  return rule.kind === 'days_after_period_end' ? `${n} days after period end` : `Business day ${n}`;
}
function cpAudienceRow(a) {
  const on = a.enabled !== 0 && a.enabled !== false;
  const sources = (a.sources || []).map(x => x.name || cpWho(x.author)).join(', ');
  const cadence = CP_CADENCE.find(c => c[0] === a.cadence)?.[1] || a.cadence || '';
  const facts = [['Approver', a.approver ? cpWho(a.approver) : 'Owner'], ['Sources', sources || 'None'], ['Due', [cadence, cpDueText(a)].filter(Boolean).join(', ')]];
  if (a.next_due && on) facts.push(['Next', finDayLabel(a.next_due)]);
  return `<div class="cp-aud" data-cp-aud="${esc(a.id)}"><div class="cp-aud-t"><b>${esc(a.name)}</b>${a.kind === 'custom' ? '' : ' <span class="pill">Built in</span>'}${on ? '' : ' <span class="pill">Off</span>'}<span class="spacer"></span>${cpBtn(`data-cp-audience="${esc(a.id)}"`, 'Edit')}</div>
    <dl class="cp-facts">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl></div>`;
}
function cpAudiences() {
  if (CP.audiences === null) return cpSection('audiences', '', skelRows(3, {dot: false}));
  const add = cpBtn('data-cp-audience=""', '+ Audience');
  if (!CP.audiences.length) return cpSection('audiences', '', cpEmpty('No audiences', add));
  return cpSection('audiences', add, `<div class="cp-auds">${CP.audiences.map(cpAudienceRow).join('')}</div>`);
}
// Everyone who can feed an audience: the company update, then people, then bots (never the built-in helpers).
function cpSourceChoices() {
  const people = (S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id]);
  const bots = (S.emps || []).filter(e => !isHelperBot(e)).map(e => [e.name, e.display_name || e.name]);
  return [['company', 'Company'], ...people, ...bots];
}
function cpAudienceEdit(a) {
  const isNew = !a, custom = isNew || a.kind === 'custom', on = isNew ? true : a.enabled !== 0 && a.enabled !== false;
  let rule = a?.due_rule;
  if (typeof rule === 'string') { try { rule = JSON.parse(rule); } catch { rule = {}; } }
  const had = new Map((a?.sources || []).map(x => [x.author, x.id]));
  const people = [['', 'Owner'], ...(S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id])];
  const choices = cpSourceChoices();
  for (const author of had.keys()) if (!choices.some(c => c[0] === author)) choices.push([author, cpWho(author)]);
  const dlg = cpDialog(isNew ? 'New audience' : a.name, `<label>Name<input name="name" value="${esc(a?.name || '')}" maxlength="80"${custom ? '' : ' disabled'}></label>
    <label class="cp-check-l"><input type="checkbox" name="enabled"${on ? ' checked' : ''}>On</label>
    <p class="cp-line" data-off-note hidden>This audience's readers lose access when you save.</p>
    <label>Approver<select name="approver">${cpOptions(people, a?.approver || '')}</select></label>
    <div class="cp-two"><label>Cadence<select name="cadence">${cpOptions(CP_CADENCE, a?.cadence || 'monthly')}</select></label>
      <label>Due<select name="due_kind">${cpOptions(CP_DUE, rule?.kind || 'business_day')}</select></label>
      <label>Day<input type="number" name="due_n" min="0" max="365" value="${esc(rule?.n || 5)}"></label></div>
    <fieldset class="cp-picks"><legend>Sources</legend>${choices.map(([v, l]) => `<label class="cp-check-l"><input type="checkbox" name="source" value="${esc(v)}"${had.has(v) ? ' checked' : ''}>${esc(l)}</label>`).join('')}</fieldset>
    ${!isNew && custom ? cpBtn('data-delete', 'Delete audience', 'ghost small danger cp-del') : ''}`, 'Save', async form => {
    const name = form.name.value.trim();
    if (custom && !name) throw new Error('Name it');
    const fields = {enabled: form.enabled.checked, approver: form.approver.value || null, cadence: form.cadence.value,
      due_rule: {kind: form.due_kind.value, n: Math.round(Number(form.due_n.value)) || 0}};
    const saved = isNew ? await post('/v2/audiences', {name, ...fields}) : await patch(`/v2/audiences/${cpId(a.id)}`, custom ? {name, ...fields} : fields);
    const id = a?.id || saved?.id;
    if (id) {
      const want = new Set([...form.querySelectorAll('[name=source]:checked')].map(i => i.value));
      await Promise.all([...[...want].filter(v => !had.has(v)).map(author => post(`/v2/audiences/${cpId(id)}/sources`, {author})),
        ...[...had].filter(([v]) => !want.has(v)).map(([, sid]) => writeRequest('DELETE', `/v2/audiences/${cpId(id)}/sources/${cpId(sid)}`))]);
    }
    await cpLoadAudienceParts(CP);
  }, {wide: true});
  const box = dlg.querySelector('[name=enabled]'), note = dlg.querySelector('[data-off-note]');
  box.onchange = () => { note.hidden = box.checked || !on; };
  const del = dlg.querySelector('[data-delete]');
  if (del) del.onclick = () => cpConfirm(`Delete ${a.name}?`, 'Its readers lose this access now.', 'Delete', async () => {
    await writeRequest('DELETE', `/v2/audiences/${cpId(a.id)}`);
    dlg.close();
    await cpLoadAudienceParts(CP);
  });
}

// ----------------------------------------------------------------- 4. Disclosure
// The rows: every class, then each KPI and P&L line on the stats board in a fold of its own. A line's rule is filed under
// pnl_line and a KPI's under kpi, whatever class the stat itself is in; `parent` is that class.
function cpClasses() {
  const known = Object.values(CP.disclosure || {}).find(d => Array.isArray(d?.data_classes))?.data_classes;
  const listed = known ? CP_CLASSES.filter(([k]) => known.includes(k)) : CP_CLASSES;
  const extra = (known || []).filter(k => !CP_CLASSES.some(c => c[0] === k)).map(k => [k, k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, ' ')]);
  const rows = [...listed, ...extra].map(([data_class, label]) => ({data_class, ref_id: '', label, group: ''}));
  for (const x of CP.stats || []) {
    const own = {kpi: 'kpi', finance_line: 'pnl_line'}[x.source_kind];
    if (own) rows.push({data_class: own, ref_id: x.ref_id, label: x.label || x.ref_id, group: own, parent: x.data_class || own});
  }
  return rows;
}
// As the server reads it: the cell's own rule, then its class's; no rule is Hidden.
function cpRuleOf(audId, c) {
  const rules = cpList(CP.disclosure?.[audId], 'rules');
  const find = (cls, ref) => rules.find(r => r.data_class === cls && (r.ref_id || '') === ref);
  return find(c.data_class, c.ref_id) || (c.ref_id ? find(c.parent || c.data_class, '') : undefined);
}
function cpRuleCell(a, c) {
  const rule = cpRuleOf(a.id, c), mode = rule?.mode || 'hidden', key = `data-aud="${esc(a.id)}" data-class="${esc(c.data_class)}" data-ref="${esc(c.ref_id)}" data-parent="${esc(c.parent || '')}"`;
  return `<td><span class="cp-rule${mode === 'hidden' ? ' off' : ''}"><select data-cp-mode ${key} aria-label="${esc(c.label)}, ${esc(a.name)}">${cpOptions(CP_MODES, mode)}</select>
    <span class="cp-lag"${mode === 'delayed' ? '' : ' hidden'}><input type="number" min="1" max="3650" data-cp-lag ${key} value="${esc(rule?.lag_days || 30)}" aria-label="Days">d</span></span></td>`;
}
function cpDisclosure() {
  const auds = CP.audiences;
  if (auds === null) return cpSection('disclosure', '', skelRows(4, {dot: false}));
  if (!auds.length) return cpSection('disclosure', '', cpEmpty('Add an audience first'));
  // A phone shows one audience at a time, so nothing scrolls sideways.
  const phone = CP.mq.matches, one = cpAud(CP.pick.disclosure) || auds[0], shown = phone ? [one] : auds;
  const pick = phone ? `<label class="cp-pick">Audience<select data-cp-pick="disclosure">${cpAudOptions(auds, one.id)}</select></label>` : '';
  const row = c => `<tr><th scope="row">${esc(c.label)}</th>${shown.map(a => cpRuleCell(a, c)).join('')}</tr>`;
  const classes = cpClasses(), base = classes.filter(c => !c.group);
  const folds = Object.entries(CP_GROUPS).map(([group, label]) => {
    const rows = classes.filter(c => c.group === group);
    if (!rows.length) return '';
    const open = !!CP.groups[group];
    return `<tbody><tr class="cp-fold"><th colspan="${shown.length + 1}"><button type="button" class="linkish" data-cp-group="${group}" aria-expanded="${open}"><span class="nav-icon cp-chev${open ? ' open' : ''}" aria-hidden="true">chevron_right</span>${esc(label)} <span class="muted tnum">${rows.length}</span></button></th></tr>${open ? rows.map(row).join('') : ''}</tbody>`;
  }).join('');
  const presets = `<tr class="cp-presets"><th scope="row">Preset</th>${shown.map(a => `<td><select data-cp-preset="${esc(a.id)}" aria-label="Preset, ${esc(a.name)}"><option value="">Choose</option>${cpOptions(CP_PRESETS)}</select></td>`).join('')}</tr>`;
  const headRow = phone ? '' : `<thead><tr><th></th>${shown.map(a => `<th scope="col">${esc(a.name)}</th>`).join('')}</tr></thead>`;
  return cpSection('disclosure', '', `${pick}<div class="${phone ? 'cp-grid-one' : 'cp-grid scroll'}"><table class="cp-table">${headRow}<tbody>${presets}${base.map(row).join('')}</tbody>${folds}</table></div>`);
}
// One cell: send it and keep the choice; if the server refuses, the cell goes back to what it holds.
async function cpSaveRule(el, mode, lag, rounding) {
  const {aud, class: dataClass, ref} = el.dataset, state = CP;
  const rule = {data_class: dataClass, ref_id: ref, mode, lag_days: lag, ...(rounding ? {rounding} : {})};
  const r = await put(`/v2/audiences/${cpId(aud)}/disclosure`, {rules: [rule]});
  if (CP !== state) return;
  state.disclosure[aud] = r;
  if (aud === state.pick.stats) void cpLoad(state, 'stats');   // the projection follows the rule
}
const cpCellOf = el => ({data_class: el.dataset.class, ref_id: el.dataset.ref, parent: el.dataset.parent});
// "Whole quarters only" outlives a mode change: dropping it would show more without anyone choosing that.
const cpQuarterly = rule => /\bquarterly\b/.test(rule?.rounding || '') ? 'quarterly' : undefined;
async function cpSetMode(el) {
  const cell = el.closest('.cp-rule'), lag = cell.querySelector('[data-cp-lag]'), mode = el.value;
  const rule = cpRuleOf(el.dataset.aud, cpCellOf(el)), was = rule?.mode || 'hidden';
  const paint = m => { cell.classList.toggle('off', m === 'hidden'); cell.querySelector('.cp-lag').hidden = m !== 'delayed'; };
  paint(mode);
  try { await cpSaveRule(el, mode, mode === 'delayed' ? Number(lag.value) || 30 : 0, mode === 'hidden' ? undefined : cpQuarterly(rule)); }
  catch (e) { el.value = was; paint(was); toast(e.message, true); }
}
async function cpSetLag(el) {
  const days = Math.max(1, Math.round(Number(el.value)) || 30);
  el.value = days;
  try { await cpSaveRule(el, 'delayed', days, cpQuarterly(cpRuleOf(el.dataset.aud, cpCellOf(el)))); } catch (e) { toast(e.message, true); }
}
function cpPreset(audId, name) {
  const label = CP_PRESETS.find(p => p[0] === name)?.[1] || name;
  cpConfirm(`Apply ${label} to ${cpAudName(audId)}?`, 'Replaces every rule for this audience. Anything it loosens needs a new stats approval.', 'Apply', async () => {
    await post(`/v2/audiences/${cpId(audId)}/disclosure/preset`, {name});
    await cpReload('disclosure', 'stats');
  });
}

// ----------------------------------------------------------------- 5. Stats
function cpBoardRow(x, i, n) {
  const up = i > 0 ? cpBtn(`data-cp-stat-move="${esc(x.id)}" data-dir="-1" aria-label="Move ${esc(x.label)} up"`, '↑', 'ghost small cp-icon') : '<span class="cp-icon-gap"></span>';
  const down = i < n - 1 ? cpBtn(`data-cp-stat-move="${esc(x.id)}" data-dir="1" aria-label="Move ${esc(x.label)} down"`, '↓', 'ghost small cp-icon') : '<span class="cp-icon-gap"></span>';
  return `<li class="cp-board-row" data-cp-stat="${esc(x.id)}"><span class="cp-board-l">${esc(x.label || x.ref_id)}</span><select data-cp-stat-chart="${esc(x.id)}" aria-label="Chart, ${esc(x.label || '')}">${cpOptions(CP_CHARTS, x.chart || 'number')}</select>${up}${down}${cpBtn(`data-cp-stat-remove="${esc(x.id)}" aria-label="Remove ${esc(x.label || '')}"`, '✕', 'ghost small cp-icon')}</li>`;
}
function cpTileChart(stat) {
  const values = (stat.series || []).map(p => p.value).filter(v => v != null).map(Number);
  if (stat.chart === 'line') return kpiSpark(values, '', 180, 36);
  if (stat.chart === 'bars' && values.length) {
    const top = Math.max(1, ...values.map(Math.abs)), w = 180, h = 36, gap = 2, bw = Math.max(2, (w - gap * (values.length - 1)) / values.length);
    return `<svg class="cp-bars" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${values.map((v, i) => { const bh = Math.max(1, Math.abs(v) / top * h); return `<rect x="${(i * (bw + gap)).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}"/>`; }).join('')}</svg>`;
  }
  return values.length > 1 ? kpiSpark(values, '', 80, 20) : '';
}
function cpTile(x) {
  return `<div class="cp-tile" data-cp-tile="${esc(x.id)}"><span class="cp-tile-l">${esc(x.label)}</span><span class="cp-tile-v">${esc(x.display)}</span>${cpTileChart(x)}
    <span class="muted cp-small">${esc([x.period, x.as_of ? `as of ${finDayLabel(x.as_of)}` : ''].filter(Boolean).join(' · '))}${x.stale ? ' · <span class="cp-stale">Out of date</span>' : ''}</span></div>`;
}
// The stats board's approval for one audience: approved, waiting, or ask. It's a separate approval from any edition's.
function cpBoardApproval(audId, p) {
  const a = p?.approval;
  if (p?.approved === true) return '<span class="pill ok">Approved</span>';
  if (a?.state === 'pending') return cpMine(a)
    ? cpBtn(`data-cp-stats-approve="${esc(a.id)}"`, 'Approve stats', 'primary small')
    : `<span class="pill waiting">Waiting on ${esc(cpApprover(a))}</span>`;
  return cpBtn('data-cp-stats-request', 'Request stats approval');
}
function cpStats() {
  if (CP.stats === null) return cpSection('stats', '', skelRows(3, {dot: false}));
  const auds = CP.audiences || [], add = cpBtn('data-cp-add-stat', '+ Stat');
  if (!CP.stats.length) return cpSection('stats', '', cpEmpty('No stats chosen', add));
  const n = CP.stats.length, id = CP.pick.stats, p = CP.projection[id];
  const held = [...cpList(p?.held, 'held'), ...cpList(p?.absent, 'absent')], tiles = cpList(p?.stats, 'stats');
  const projection = !id ? '<p class="muted cp-small">Add an audience first</p>' : `<div class="cp-proj-bar"><label class="cp-pick">Audience<select data-cp-pick="stats">${cpAudOptions(auds, id)}</select></label><span class="spacer"></span>${cpBoardApproval(id, p)}</div>
    ${tiles.length ? `<div class="cp-tiles">${tiles.map(cpTile).join('')}</div>` : `<p class="muted cp-small">Nothing shows to ${esc(cpAudName(id))}</p>`}
    ${held.length ? `<h4 class="label cp-h">Held</h4><ul class="cp-held">${held.map(x => `<li data-cp-held="${esc(x.id)}"><b>${esc(x.label)}</b><span>${esc(x.reason)}</span></li>`).join('')}</ul>` : ''}`;
  return cpSection('stats', add, `<div class="cp-stats"><ul class="cp-board">${CP.stats.map((x, i) => cpBoardRow(x, i, n)).join('')}</ul><div class="cp-proj">${projection}</div></div>`);
}
// What can go on the board: the server's list if it sends one, else the KPIs this person can see and Finance's own numbers.
async function cpStatChoices() {
  if (CP.available?.length) return CP.available;
  const kpis = cpList(await get('/v2/kpis', {fresh: true}).catch(() => null), 'kpis');
  return [...kpis.map(k => ({source_kind: 'kpi', ref_id: k.id, label: k.name})), ...CP_METRICS.map(([ref_id, label]) => ({source_kind: 'finance_metric', ref_id, label}))];
}
async function cpAddStat() {
  const state = CP, choices = await cpStatChoices();
  if (CP !== state) return;
  const taken = new Set((CP.stats || []).map(x => `${x.source_kind}|${x.ref_id}`));
  const free = choices.filter(x => !taken.has(`${x.source_kind}|${x.ref_id}`));
  if (!free.length) return void toast('Nothing left to add');
  const group = (kinds, label) => {
    const rows = free.filter(x => kinds.includes(x.source_kind));
    return rows.length ? `<optgroup label="${label}">${rows.map(x => `<option value="${esc(`${x.source_kind}|${x.ref_id}`)}">${esc(x.label)}</option>`).join('')}</optgroup>` : '';
  };
  cpDialog('Add a stat', `<label>Stat<select name="stat">${group(['kpi'], 'KPIs')}${group(['finance_metric', 'finance_line'], 'Finance')}</select></label>
    <label>Chart<select name="chart">${cpOptions(CP_CHARTS, 'number')}</select></label>`, 'Add', async form => {
    const pickd = free.find(x => `${x.source_kind}|${x.ref_id}` === form.stat.value);
    await post('/v2/company-page/stats', {source_kind: pickd.source_kind, ref_id: pickd.ref_id, chart: form.chart.value, position: CP.stats.length});
    await cpReload('stats');
  });
}
function cpStatRemove(id) {
  const x = (CP.stats || []).find(y => y.id === id);
  cpConfirm(`Remove ${x?.label || 'this stat'}?`, 'It comes off every audience\'s page at the next rebuild.', 'Remove', async () => {
    await writeRequest('DELETE', `/v2/company-page/stats/${cpId(id)}`);
    await cpReload('stats');
  });
}
async function cpStatChart(id, chart) {
  try { await patch(`/v2/company-page/stats/${cpId(id)}`, {chart}); await cpReload('stats'); }
  catch (e) { toast(e.message, true); await cpReload('stats'); }
}
// The order is the positions of the two rows swapped.
async function cpStatMove(id, dir) {
  const list = CP.stats, i = list.findIndex(x => x.id === id), j = i + dir;
  if (i < 0 || !list[j]) return;
  try {
    await Promise.all([patch(`/v2/company-page/stats/${cpId(list[i].id)}`, {position: j}), patch(`/v2/company-page/stats/${cpId(list[j].id)}`, {position: i})]);
  } catch (e) { toast(e.message, true); }
  await cpReload('stats');
}
async function cpStatsRequest() {
  try { await post(`/v2/audiences/${cpId(CP.pick.stats)}/stats/approval`, {}); toast('Approval requested'); }
  catch (e) { toast(e.message, true); }
  await cpReload('stats');
}
async function cpStatsDecide(approvalId) {
  try { await post(`/v2/approvals/${cpId(approvalId)}`, {decision: 'approved'}); toast('Approved'); }
  catch (e) { toast(e.message, true); }
  await cpReload('stats');
}

// ----------------------------------------------------------------- 6. Readers
function cpReaderRow(r) {
  const auds = (r.audiences || []).map(x => x.name).filter(Boolean).join(', ');
  return `<li class="cp-reader" data-cp-reader="${esc(r.id)}"><span class="cp-reader-t"><b>${esc(r.name || r.email)}</b><span class="muted">${esc([r.name ? r.email : '', auds].filter(Boolean).join(' · '))}</span></span>${cpBtn(`data-cp-reader-remove="${esc(r.id)}"`, 'Remove')}</li>`;
}
function cpInviteRow(x) {
  const state = {used: 'Used', expired: 'Expired', open: 'Open'}[x.state] || 'Expired';
  return `<li class="cp-reader" data-cp-invite="${esc(x.id)}"><span class="cp-reader-t"><span>${esc(`${state} · made ${finDayLabel(x.created_at)}`)}</span>${state === 'Open' && x.expires_at ? `<span class="muted">Expires ${esc(finDayLabel(x.expires_at))}</span>` : ''}</span>${state === 'Open' ? cpBtn(`data-cp-invite-revoke="${esc(x.id)}"`, 'Revoke') : ''}</li>`;
}
function cpReaders() {
  if (CP.readers === null) return cpSection('readers', '', skelRows(2, {dot: false}));
  if (!cpPrivateOn()) return cpSection('readers', '', cpPrivateOff());
  const adds = cpBtn('data-cp-add-reader', '+ Reader') + cpBtn('data-cp-import', 'CSV');
  const people = CP.readers.length ? `<ul class="cp-readers">${CP.readers.map(cpReaderRow).join('')}</ul>${CP.readersNext ? cpBtn('data-cp-readers-more', 'Show more', 'ghost small cp-more') : ''}`
    : cpEmpty('No readers yet', `<span class="cp-acts">${adds}</span>`);
  const auds = cpPrivateAudiences(), id = CP.pick.invites;
  const invites = (CP.invites[id] || []).filter(x => x.state !== 'revoked');
  const links = !auds.length ? '' : `<h4 class="label cp-h">Invite links</h4>
    <div class="cp-proj-bar"><label class="cp-pick">Audience<select data-cp-pick="invites">${cpAudOptions(auds, id)}</select></label><span class="spacer"></span>${cpBtn('data-cp-new-invite', 'New link')}</div>
    ${invites.length ? `<ul class="cp-readers">${invites.map(cpInviteRow).join('')}</ul>` : '<p class="muted cp-small">No links</p>'}`;
  return cpSection('readers', CP.readers.length ? adds : '', people + links);
}
const cpAudienceChecks = (checked = '') => `<fieldset class="cp-picks"><legend>Audiences</legend>${cpPrivateAudiences().map(a => `<label class="cp-check-l"><input type="checkbox" name="audience" value="${esc(a.id)}"${a.id === checked ? ' checked' : ''}>${esc(a.name)}</label>`).join('')}</fieldset>`;
function cpAddReader() {
  cpDialog('Add a reader', `<label>Email<input type="email" name="email" required autocomplete="off"></label><label>Name<input name="name" autocomplete="off"></label>${cpAudienceChecks(CP.pick.invites)}`, 'Add', async form => {
    const email = form.email.value.trim(), audience_ids = [...form.querySelectorAll('[name=audience]:checked')].map(i => i.value);
    if (!/^[^@\s]+@[^@\s]+$/.test(email)) throw new Error('Enter an email');
    if (!audience_ids.length) throw new Error('Choose an audience');
    await post('/v2/readers', {email, name: form.name.value.trim(), audience_ids});
    await cpReload('readers');
  });
}
function cpImportReaders() {
  const dlg = cpDialog('Add readers from CSV', `<label>Audience<select name="audience">${cpAudOptions(cpPrivateAudiences(), CP.pick.invites)}</select></label>
    <label>File<input type="file" name="file" accept=".csv,text/csv"></label>
    <label>Or paste<textarea name="csv" rows="5" placeholder="email,name"></textarea></label>
    <p class="cp-line" data-result hidden></p>`, 'Add', async form => {
    const csv = form.csv.value.trim();
    if (!csv) throw new Error('Choose a file or paste the rows');
    const r = await post('/v2/readers/import', {csv, audience_id: form.audience.value});
    await cpReload('readers');
    const errors = cpList(r?.invalid, 'invalid');
    if (!r?.invalid_count) { toast(`${r?.added ?? 0} added`); return true; }
    // Some rows failed: stay here, say which, so they can be fixed and sent again.
    const out = form.querySelector('[data-result]');
    out.textContent = `${r?.added ?? 0} added. Not added (${r.invalid_count}): ${errors.map(x => `line ${x.line}: ${x.reason}`).join('; ')}`;
    out.hidden = false;
    form.csv.value = '';
    return false;
  });
  dlg.querySelector('[name=file]').onchange = async ev => {
    const file = ev.target.files?.[0];
    if (file) dlg.querySelector('[name=csv]').value = await file.text();
  };
}
function cpRemoveReader(id) {
  const r = (CP.readers || []).find(x => x.id === id);
  cpConfirm(`Remove ${r?.email || 'this reader'}?`, 'They lose access and are signed out now. Emails already sent can\'t be recalled.', 'Remove', async () => {
    await writeRequest('DELETE', `/v2/readers/${cpId(id)}`);
    await cpReload('readers');
  });
}
// A link's address exists only in this answer: the server keeps a hash of it.
async function cpNewInvite() {
  const id = CP.pick.invites;
  try {
    const r = await post(`/v2/audiences/${cpId(id)}/invites`, {});
    await cpReload('readers');
    const dlg = cpDialog(`Invite to ${cpAudName(id)}`, `<label>Link<input name="url" readonly value="${esc(r?.url || '')}"></label><p class="cp-line">Works once. Shown only now.</p>${cpBtn('data-copy', 'Copy')}`, '', null);
    dlg.querySelector('[data-copy]').onclick = async () => {
      const input = dlg.querySelector('[name=url]');
      try { await navigator.clipboard.writeText(input.value); toast('Copied', false, null, {host: dlg}); }
      catch { input.select(); }
    };
  } catch (e) { toast(e.message, true); }
}
function cpRevokeInvite(inviteId) {
  const id = CP.pick.invites;
  cpConfirm('Revoke this link?', 'It stops working now. Anyone who already joined stays.', 'Revoke', async () => {
    await writeRequest('DELETE', `/v2/audiences/${cpId(id)}/invites/${cpId(inviteId)}`);
    await cpReload('readers');
  });
}

// ----------------------------------------------------------------- 7. Preview
function cpPreview() {
  if (CP.audiences === null) return cpSection('preview', '', skelRows(1, {dot: false}));
  if (!CP.audiences.length) return cpSection('preview', '', cpEmpty('Add an audience first'));
  return cpSection('preview', '', `<div class="cp-previews">${CP.audiences.map(a =>
    `<a class="ghost small cp-preview" href="${API}/v2/company-page/preview?audience=${cpId(a.id)}" target="_blank" rel="noopener noreferrer" data-cp-preview="${esc(a.id)}">${esc(a.name)}<span class="nav-icon" aria-hidden="true">open_in_new</span></a>`).join('')}</div>`);
}
