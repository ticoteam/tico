/* ui/app/growth-page.js — The Growth tab of Strategy (#/strategy/growth, docs: the growth spec §4): a framework picker
   (North Star · Loops · Journey · Bets × Channels · Experiments), each a view over the same ideas, KPIs, channels and bets;
   Working now; + Idea; This week (#/strategy/growth/week). Channels and Learning are their own files
   (growth-channels.js, growth-learning.js); the Idea panel on a task is growth-idea.js; Brand and Go-to-market are
   brand-page.js and gtm-page.js. Also what those files share: formats, chips, idea cards, a small dialog, the lists
   the forms pick from. Reads GET /api/v2/growth/{ideas,kpis,channels,settings,working,week} and GET /api/v2/strategy (bets).
   Every write is the server's to allow; edit controls show to the owner.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GR_ROUTE = '#/strategy/growth', GR_WEEK = GR_ROUTE + '/week', GR_CHANNELS = GR_ROUTE + '/channels',
  GR_LEARNING = GR_ROUTE + '/learning', BRAND_ROUTE = '#/strategy/brand', GTM_ROUTE = '#/strategy/gtm';
let GR = null;            // the open Growth page: {load, sub, data, fw}
let GR_LOAD = 0;
const GR_FW_KEY = 'tico.growth.framework';
const GR_FWS = [['north_star', 'North Star'], ['loops', 'Loops'], ['journey', 'Journey'], ['bets_channels', 'Bets × Channels'],
  ['experiments', 'Experiments']];
const GR_STAGES = [['reach', 'Reach'], ['acquire', 'Acquire'], ['activate', 'Activate'], ['retain', 'Retain'], ['refer', 'Refer']];
const GR_STATUS = {idea: ['Idea', ''], testing: ['Testing', 'in-progress'], paused: ['Paused', 'waiting'], won: ['Won', 'ok'],
  lost: ['Lost', 'fail'], inconclusive: ['Inconclusive', 'waiting'], dropped: ['Dropped', '']};
const GR_CH_STAGE = {idea: ['Idea', ''], testing: ['Testing', 'in-progress'], scaling: ['Scaling', 'ok'], paused: ['Paused', 'waiting']};
const GR_RESULTS = ['won', 'lost', 'inconclusive', 'dropped'];

// ----------------------------------------------------------------- shared formats and pieces
// Who may change company-level growth records: the owner or an admin (the server checks and is the one that enforces it).
const grAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
const grMe = () => myActor();
// An owner ref as Tico writes them: bot:<slug>, human:<id>, or a bare bot slug.
const grWho = ref => {
  const s = String(ref || '');
  if (!s) return '';
  if (s.includes(':')) return actorLabel(s);
  return (S.emps || []).some(e => e.name === s) ? botDisplayName(s) : personDisplay(s);
};
const grSame = (a, b) => !!a && !!b && (a === b || a.replace(/^bot:/, '') === b.replace(/^bot:/, ''));
const grUsd = v => v == null || v === '' ? '—' : (v < 0 ? '−$' : '$') + Math.abs(Number(v)).toLocaleString('en-US', {maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0});
const grPct = v => v == null || v === '' ? '—' : `${Math.round(Number(v))}%`;
const grX = (v, d = 1) => v == null || v === '' ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: d});
const GR_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const grMonth = m => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${y}` : String(m || ''); };
const grDay = iso => { if (!iso) return ''; const d = new Date(String(iso).length <= 10 ? iso + 'T12:00:00' : iso); return isNaN(d) ? String(iso) : `${GR_MON[d.getMonth()]} ${d.getDate()}`; };
const grStatus = s => { const [w, c] = GR_STATUS[s] || [s || '', '']; return w ? `<span class="pill ${c}">${esc(w)}</span>` : ''; };
const grStage = s => { const [w, c] = GR_CH_STAGE[s] || [s || '', '']; return w ? `<span class="pill ${c}">${esc(w)}</span>` : ''; };
const grKpiVal = k => k?.latest == null ? '—' : kpiNum(k.latest, k.unit);
const grChanName = (chans, slug) => (chans || []).find(c => c.slug === slug)?.name || slug;
const grTaskHref = id => '#/task/' + encodeURIComponent(id);
const grFail = e => `<p class="muted gr-note">${esc(e?.status === 403 ? 'Not in your view.' : e?.message || 'Did not load.')}</p>`;
const grOpts = (pairs, value, blank = '') => (blank !== null ? `<option value="">${esc(blank)}</option>` : '')
  + pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('');
// A segmented switch: [[value, label]], the chosen one aria-checked.
const grSeg = (name, label, options, value) => `<div class="theme-seg gr-seg" role="radiogroup" aria-label="${esc(label)}" data-gr-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
const grCrumbs = (base, baseLabel, title, tools = '') => `<div class="gr-crumbs"><a href="${base}">${esc(baseLabel)}</a><span class="muted" aria-hidden="true">/</span><h2>${esc(title)}</h2><span class="spacer"></span>${tools}</div>`;
// A labelled field: the caption over the control.
const grField = (label, control, cls = '') => `<label class="gr-f${cls ? ' ' + cls : ''}"><span>${esc(label)}</span>${control}</label>`;
const grNum = (v, d = null) => { const t = String(v ?? '').trim().replace(/[$,%\s]/g, ''); if (t === '') return d; const n = Number(t); return Number.isFinite(n) ? n : d; };

function grIdeaCard(i, chans, {chan = true, result = false} = {}) {
  const tags = chan ? (i.channels || []).map(s => `<span class="gr-tag">${esc(grChanName(chans, s))}</span>`).join('') : '';
  return `<a class="gr-ic gr-is-${esc(i.status || 'idea')}" href="${grTaskHref(i.task_id)}" data-idea="${esc(i.task_id)}"><span class="gr-ic-t">${esc(i.title)}</span>
    <span class="gr-ic-f">${grStatus(i.status)}${i.ice != null ? `<span class="gr-ice">ICE ${esc(grX(i.ice))}</span>` : ''}${tags}${i.assignee ? `<span class="gr-who">${esc(grWho(i.assignee))}</span>` : ''}</span>
    ${result && i.result ? `<span class="gr-ic-r">${esc(i.result)}</span>` : ''}</a>`;
}

// The bets a reader sees: the owner's live bets, everyone else the published version's.
function grBetsOf(s) {
  if (!s) return [];
  const owner = (s.may_edit ?? S.me?.role === 'owner') && !!s.draft;
  return ((owner ? s.bets : s.current?.bets) || []).filter(b => b.status !== 'retired')
    .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).map((b, n) => ({...b, n: n + 1}));
}
// What the forms pick from: KPIs, channels and bets, read together.
async function grRefs(fresh = false) {
  const opts = fresh ? {fresh: true} : undefined;
  const [k, c, s] = await Promise.all([get('/v2/growth/kpis', opts).catch(() => null), get('/v2/growth/channels', opts).catch(() => null),
    get('/v2/strategy', opts).catch(() => null)]);
  return {kpis: k?.kpis || [], channels: c?.channels || [], bets: grBetsOf(s), failed: !k || !c || !s};
}

// A small dialog: a title, a form, Cancel and the primary button. `onSave(form)` throws to show an error.
function grDialog(title, body, saveLabel, onSave) {
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal gr-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body gr-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="gr-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(saveLabel)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector('[data-error]'), save = dlg.querySelector('[type=submit]');
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true; save.disabled = true;
    try { if (await onSave(form) === false) save.disabled = false; else dlg.close(); }
    catch (e) { err.textContent = e.message || 'Not saved'; err.hidden = false; save.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('input:not([type=hidden]):not([type=checkbox]),select,textarea')?.focus();
  return dlg;
}
const grChecks = (name, pairs, on = []) => `<div class="gr-checks" role="group">${pairs.map(([v, l]) =>
  `<label class="gr-check"><input type="checkbox" name="${esc(name)}" value="${esc(v)}"${on.includes(v) ? ' checked' : ''}>${esc(l)}</label>`).join('')}</div>`;
const grChecked = (form, name) => [...form.querySelectorAll(`input[name="${CSS.escape(name)}"]:checked`)].map(i => i.value);

// The KPI choices: the loaded list, plus the idea's own KPI when that list does not hold it (hidden or retired).
const grKpiOpts = (refs, id, name) => {
  const pairs = refs.kpis.map(k => [k.id, k.name]);
  if (id && !pairs.some(([x]) => x === id)) pairs.push([id, name || id]);
  return pairs;
};
// The idea form's fields (+ Idea, and editing an idea on its task). The idea's own bet, KPI and channels stay
// choosable when a list did not load or no longer holds them (retired), so saving never drops them.
function grIdeaFields(refs, i = {}) {
  const bets = refs.bets.map(b => [b.id, `${b.n} · ${b.statement}`]), kpis = grKpiOpts(refs, i.kpi_id, i.kpi_name);
  if (i.bet_id && !bets.some(([id]) => id === i.bet_id)) bets.push([i.bet_id, i.bet_title || i.bet_id]);
  const chans = (refs.channels || []).filter(c => !c.retired_at).map(c => [c.slug, c.name]);
  for (const slug of i.channels || []) if (!chans.some(([s]) => s === slug)) chans.push([slug, grChanName(refs.channels, slug)]);
  return `${refs.failed ? '<p class="err">Some lists did not load</p>' : ''}${grField('Title', `<input name="title" maxlength="300" required value="${esc(i.title || '')}">`)}
    ${grField('Hypothesis', `<textarea name="hypothesis" rows="3">${esc(i.hypothesis || '')}</textarea>`)}
    ${grField('Prediction', `<input name="prediction" maxlength="500" value="${esc(i.prediction || '')}">`)}
    ${grField('Bet', `<select name="bet_id">${grOpts(bets, i.bet_id)}</select>`)}
    ${grField('KPI', `<select name="kpi_id">${grOpts(kpis, i.kpi_id)}</select>`)}
    ${chans.length ? `<fieldset class="gr-fs"><legend>Channels</legend>${grChecks('channels', chans, i.channels || [])}</fieldset>` : ''}`;
}
// The form as a body; given the idea it edits, only the fields that changed.
function grIdeaBody(form, was = null) {
  const v = n => form.elements[n]?.value.trim() ?? undefined;
  const body = {title: v('title'), hypothesis: v('hypothesis'), bet_id: v('bet_id') || null, kpi_id: v('kpi_id') || null,
    channels: grChecked(form, 'channels')};
  if (form.elements.prediction) body.prediction = v('prediction');
  if (!body.title) throw new Error('A title');
  if (!was) return body;
  const same = (a, b) => Array.isArray(a) ? [...a].sort().join('\n') === [...(b || [])].sort().join('\n') : (a ?? '') === (b ?? '');
  return Object.fromEntries(Object.entries(body).filter(([k, x]) => !same(x, was[k])));
}
function grNewIdea(after) {
  void grRefs().then(refs => grDialog('New idea', grIdeaFields(refs), 'Add', async form => {
    const out = await post('/v2/growth/ideas', grIdeaBody(form));
    toast('Idea added');
    after?.(out);
  }));
}

// The ask itself carries the steps, for a lead whose bot predates the propose-ideas playbook.
const GR_PROPOSE_BODY = `File 3–8 growth ideas. Start no tests.

1. Read: \`hub gtm show\`, \`hub brand summary\`, \`hub growth channel list\`, \`hub growth kpis\`, \`hub growth idea list\`, \`hub strategy show\`.
2. Skip duplicates: \`hub growth idea list --q "<two words>"\`.
3. File each with \`hub growth idea create --title ... --hypothesis "If we X for Y, Z moves because W" --prediction "<KPI> from <now> to <number> by <date>" --kpi <id> --channel <slug> --i N --c N --e N --source-kind strategy|competitor|listening|peec|lost_test|person|other --source-ref <item>\`, adding \`--bet <id>\` when a strategy bet fits. With no bets yet, file without one.
4. Follow the brand and the go-to-market wishes.
5. Close this task with the count and the top three by ICE; say so if the strategy has no bets.`;

// The marketing lead: who "Propose ideas" asks.
const grLead = () => (S.emps || []).find(e => e.status !== 'archived' && (e.template === 'marketing-lead' || e.name === 'marketing-lead'
  || /^(head of marketing|growth lead|marketing lead)$/i.test(String(e.display_name || '').trim())));
async function grPropose(button) {
  const lead = grLead();
  if (!lead) return;
  if (button) button.disabled = true;
  try {
    await post('/v2/tasks', {title: 'Propose growth ideas', body: GR_PROPOSE_BODY, owner: lead.name});
    toast(`Sent to ${lead.display_name || lead.name}`);
  } catch (e) { toast(e.message || 'Not sent', true); if (button) button.disabled = false; }
}

// ----------------------------------------------------------------- the page
// After every route: forget each of the Growth, Brand and Go-to-market pages once off it, so a late answer for one
// never draws over another (each page's reads check that its own state is still the open one).
window.growthStop = function growthStop() {
  const r = S.route || '';
  const off = base => !rdUnder(r, base);
  if (!(GR && off(GR_ROUTE)) && !(BR && off(BRAND_ROUTE)) && !(GT && off(GTM_ROUTE))) return;
  GR_LOAD++;
  if (off(GR_ROUTE)) GR = null;
  if (off(BRAND_ROUTE)) BR = null;
  if (off(GTM_ROUTE)) GT = null;
  if ([GR_ROUTE, BRAND_ROUTE, GTM_ROUTE].every(off)) document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
};
function pageGrowth() {
  const path = (S.route || '').split('?')[0];
  const sub = path.slice(GR_ROUTE.length + 1).split('/')[0] || '';
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const load = ++GR_LOAD;
  GR = {load, sub, data: null, fw: ''};
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = redesignStrategyHead('growth') + `<div id="gr-body" class="gr-body">${skelRows(6, {dot: false})}</div>`;
  if (sub === 'channels') return pageGrowthChannels(GR);
  if (sub === 'learning') return pageGrowthLearning(GR);
  if (sub === 'week') return void growthWeek(GR);
  if (sub) { location.replace(GR_ROUTE); return; }
  void growthMain(GR);
}

async function growthMain(state) {
  let data;
  try {
    const [ideas, refs, settings, working] = await Promise.all([get('/v2/growth/ideas', {fresh: true}), grRefs(true),
      get('/v2/growth/settings').catch(() => ({})), get('/v2/growth/working', {fresh: true}).catch(() => ({items: []}))]);
    data = {ideas: ideas.ideas || [], ...refs, settings: settings || {}, working: working.items || []};
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = data;
  let saved = '';
  try { saved = localStorage.getItem(GR_FW_KEY) || ''; } catch { /* not kept */ }
  const known = f => GR_FWS.some(([v]) => v === f);
  state.fw = (!(data.settings.may_edit ?? grAdmin()) && known(saved) && saved) || (known(data.settings.framework) && data.settings.framework) || 'north_star';
  growthMainDraw(state);
}
function growthMainDraw(state) {
  const d = state.data, body = $('#gr-body');
  if (!body || !d) return;
  patchHTML($('#strat-actions'), `<a class="ghost gr-btn" href="${GR_WEEK}">This week</a><a class="ghost gr-btn" href="${GR_CHANNELS}">Channels</a>`
    + `<a class="ghost gr-btn" href="${GR_LEARNING}">Learning</a>${grLead() ? '<button class="ghost" type="button" data-gr-propose>Propose ideas</button>' : ''}`
    + '<button class="primary" type="button" data-gr-new>+ Idea</button>');
  const view = grView(state.fw, d);
  const now = d.working.length ? `<aside class="card gr-now" aria-label="Working now"><h2 class="gr-k">Working now</h2>${d.working.map(w =>
    `<a class="gr-now-row" href="${grTaskHref(w.task_id)}"><span class="gr-now-top"><b>${esc(grWho(w.assignee) || '—')}</b>${w.channel ? `<span class="gr-tag">${esc(grChanName(d.channels, w.channel))}</span>` : ''}</span>
      <span class="gr-now-t">${w.status ? `<span class="gr-dot gr-ts-${esc(w.status)}" aria-hidden="true"></span>` : ''}${esc(w.title)}</span>${w.idea_title ? `<span class="muted gr-now-i">${esc(w.idea_title)}</span>` : ''}</a>`).join('')}</aside>` : '';
  body.innerHTML = `<div class="gr-bar">${grSeg('fw', 'Framework', GR_FWS, state.fw)}</div>
    <div class="gr-split${now ? '' : ' gr-solo'}"><div class="gr-view" id="gr-view">${view}</div>${now}</div>`;
  $('#strat-actions [data-gr-new]').onclick = () => grNewIdea(() => void growthMain(state));
  const propose = $('#strat-actions [data-gr-propose]');
  if (propose) propose.onclick = () => void grPropose(propose);
  body.querySelector('[data-gr-seg="fw"]').onclick = ev => {
    const b = ev.target.closest('button[data-v]'); if (!b || b.dataset.v === state.fw) return;
    state.fw = b.dataset.v;
    if (d.settings.may_edit ?? grAdmin()) void put('/v2/growth/settings', {framework: state.fw}).catch(e => toast(e.message || 'Not saved', true));
    try { localStorage.setItem(GR_FW_KEY, state.fw); } catch { /* not kept */ }
    growthMainDraw(state);
  };
}

// ----------------------------------------------------------------- the five views
function grView(fw, d) {
  if (fw === 'loops') return grLoops(d);
  if (fw === 'journey') return grJourney(d);
  if (fw === 'bets_channels') return grGrid(d);
  if (fw === 'experiments') return grExperiments(d);
  return grNorthStar(d);
}
const grByKpi = ideas => { const m = new Map(); for (const i of ideas) { const k = i.kpi_id || ''; if (!m.has(k)) m.set(k, []); m.get(k).push(i); } return m; };
const grCards = (list, d, opts) => list.map(i => grIdeaCard(i, d.channels, opts)).join('');
const grCol = (kind, name, value, list, d, cls = '') => `<section class="gr-col${cls ? ' ' + cls : ''}"><header class="gr-col-h">${kind ? `<span class="gr-k">${esc(kind)}</span>` : ''}
    <span class="gr-col-n">${esc(name)}</span>${value ? `<span class="gr-mv">${esc(value)}</span>` : ''}<span class="muted gr-cnt">${list.length}</span></header>${grCards(list, d)}</section>`;
// The KPIs below the North Star in tree order: each input, then its own inputs.
function grTree(kpis, rootId) {
  const out = [], seen = new Set([rootId]);
  const walk = (pid, depth) => { for (const k of kpis.filter(k => k.parent_kpi_id === pid && !seen.has(k.id))) { seen.add(k.id); out.push({...k, depth}); walk(k.id, depth + 1); } };
  walk(rootId, 0);
  return out;
}
function grNorthStar(d) {
  const ns = d.kpis.find(k => k.growth_role === 'north_star'), by = grByKpi(d.ideas);
  const tree = ns ? grTree(d.kpis, ns.id).filter(k => k.growth_role !== 'guardrail') : [];
  const inputs = [...tree, ...d.kpis.filter(k => k.growth_role === 'input' && !tree.some(t => t.id === k.id))];
  const guards = d.kpis.filter(k => k.growth_role === 'guardrail');
  const shown = new Set([ns?.id, ...inputs.map(k => k.id), ...guards.map(k => k.id)].filter(Boolean));
  const loose = d.ideas.filter(i => !shown.has(i.kpi_id));
  const head = ns ? `<section class="card gr-ns"><span class="gr-k">North Star</span><span class="gr-ns-n">${esc(ns.name)}</span>
      <span class="gr-mv gr-big">${esc(grKpiVal(ns))}</span>${ns.target != null ? `<span class="muted">/ ${esc(kpiNum(ns.target, ns.unit))}</span>` : ''}
      ${(by.get(ns.id) || []).length ? `<div class="gr-ns-ideas">${grCards(by.get(ns.id), d)}</div>` : ''}</section>` : '';
  const cols = [...inputs.map(k => grCol('Input', k.name, grKpiVal(k), by.get(k.id) || [], d)),
    ...guards.map(k => grCol('Guardrail', k.name, grKpiVal(k), by.get(k.id) || [], d, 'gr-guard')),
    ...(loose.length ? [grCol('', 'No metric', '', loose, d)] : [])];
  return head + `<div class="gr-cols">${cols.join('')}</div>`;
}
function grLoops(d) {
  const by = grByKpi(d.ideas), loops = new Map();
  for (const k of d.kpis.filter(k => k.loop_name)) { if (!loops.has(k.loop_name)) loops.set(k.loop_name, []); loops.get(k.loop_name).push(k); }
  const on = new Set();
  const html = [...loops].map(([name, steps]) => {
    steps.sort((a, b) => (a.loop_step ?? 1e9) - (b.loop_step ?? 1e9));
    return `<section class="card gr-loop"><h3 class="gr-loop-h">${esc(name)}</h3><div class="gr-steps">${steps.map((k, n) => {
      on.add(k.id);
      return `<div class="gr-step"><header class="gr-col-h"><span class="gr-col-n">${esc(k.name)}</span><span class="gr-mv">${esc(grKpiVal(k))}</span>${n < steps.length - 1 ? '<span class="gr-arrow" aria-hidden="true">→</span>' : '<span class="gr-arrow" aria-hidden="true">↺</span>'}</header>${grCards(by.get(k.id) || [], d)}</div>`;
    }).join('')}</div></section>`;
  }).join('');
  const off = d.ideas.filter(i => !on.has(i.kpi_id));
  return html + (off.length ? `<section class="gr-off"><h3 class="gr-k">Not on a loop</h3><div class="gr-flow">${grCards(off, d)}</div></section>` : '');
}
function grJourney(d) {
  const stageOf = new Map(d.kpis.map(k => [k.id, k.stage]));
  const cols = GR_STAGES.map(([id, label]) => {
    const kp = d.kpis.filter(k => k.stage === id);
    const list = d.ideas.filter(i => stageOf.get(i.kpi_id) === id);
    return `<section class="gr-col"><header class="gr-col-h"><span class="gr-col-n">${esc(label)}</span><span class="muted gr-cnt">${list.length}</span>
      ${kp.length ? `<span class="muted gr-col-m">${esc(kp.map(k => `${k.name} ${grKpiVal(k)}`).join(' · '))}</span>` : ''}</header>${grCards(list, d)}</section>`;
  });
  const none = d.ideas.filter(i => !GR_STAGES.some(([id]) => stageOf.get(i.kpi_id) === id));
  if (none.length) cols.push(grCol('', 'No stage', '', none, d));
  return `<div class="gr-cols gr-journey">${cols.join('')}</div>`;
}
function grGrid(d) {
  const chans = d.channels.filter(c => !c.retired_at);
  const has = (i, slug) => slug ? (i.channels || []).includes(slug) : !(i.channels || []).some(s => chans.some(c => c.slug === s));
  const bets = [...d.bets.map(b => ({id: b.id, label: `${b.n} · ${b.statement}`}))];
  if (!bets.length || d.ideas.some(i => !i.bet_id || !d.bets.some(b => b.id === i.bet_id))) bets.push({id: '', label: 'No bet'});
  const cols = [...chans.map(c => ({slug: c.slug, name: c.name, track: c.track}))];
  if (!cols.length || d.ideas.some(i => has(i, ''))) cols.push({slug: '', name: 'No channel', track: ''});
  const inBet = (i, id) => id ? i.bet_id === id : !i.bet_id || !d.bets.some(b => b.id === i.bet_id);
  return `<div class="card gr-scroll"><table class="gr-grid"><thead><tr><th scope="col"><span class="sr-only">Bet</span></th>${cols.map(c =>
    `<th scope="col">${c.track ? `<span class="gr-track gr-track-${esc(c.track)}">${c.track === 'brand' ? 'Brand' : 'Performance'}</span>` : ''}<span>${esc(c.name)}</span></th>`).join('')}</tr></thead>
    <tbody>${bets.map(b => `<tr><th scope="row" title="${esc(b.label)}"><span>${esc(b.label)}</span></th>${cols.map(c =>
      `<td>${grCards(d.ideas.filter(i => inBet(i, b.id) && has(i, c.slug)), d, {chan: false})}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function grExperiments(d) {
  const ice = (a, b) => (b.ice ?? -1) - (a.ice ?? -1);
  const cols = [['Ideas', d.ideas.filter(i => i.status === 'idea' || !i.status).sort(ice)],
    ['Testing', d.ideas.filter(i => i.status === 'testing' || i.status === 'paused').sort((a, b) => String(a.test_to || '').localeCompare(String(b.test_to || '')))],
    ['Results', d.ideas.filter(i => GR_RESULTS.includes(i.status)).sort((a, b) => String(b.verdict_at || '').localeCompare(String(a.verdict_at || '')))]];
  return `<div class="gr-cols gr-exp">${cols.map(([name, list]) => `<section class="gr-col"><header class="gr-col-h"><span class="gr-col-n">${esc(name)}</span><span class="muted gr-cnt">${list.length}</span></header>
    ${list.map(i => grIdeaCard(i, d.channels, {result: true})).join('')}</section>`).join('')}</div>`;
}

// ----------------------------------------------------------------- This week
async function growthWeek(state) {
  let w, chans = [];
  try { [w, chans] = await Promise.all([get('/v2/growth/week', {fresh: true}), get('/v2/growth/channels').then(r => r.channels || []).catch(() => [])]); }
  catch (e) { if (GR === state) $('#gr-body').innerHTML = grCrumbs(GR_ROUTE, 'Growth', 'This week') + grFail(e); return; }
  if (GR !== state) return;
  const row = i => `<a class="gr-row" href="${grTaskHref(i.task_id)}"><span class="gr-row-t">${esc(i.title)}</span>${grStatus(i.status)}`;
  const sec = (title, inner, cls = '') => inner ? `<section class="card gr-sec${cls ? ' ' + cls : ''}"><h3 class="gr-k">${esc(title)}</h3>${inner}</section>` : '';
  const guards = (w.guardrails || []).map(g => `<div class="gr-row gr-breach"><span class="gr-row-t">${esc(grChanName(chans, g.channel))} · ${esc(g.kpi_name || '')}</span>
    <span class="gr-num">${esc(grX(g.value))} / ${esc(grX(g.max))}</span></div>`).join('');
  const readings = (w.readings || []).map(r => ({...r, latest: r.value})).map(k => `<div class="gr-row"><span class="gr-row-t">${esc(k.name)}</span><span class="muted">${esc({north_star: 'North Star', input: 'Input', guardrail: 'Guardrail'}[k.growth_role] || '')}</span>
    <span class="gr-num">${esc(grKpiVal(k))}${k.target != null ? `<span class="muted"> / ${esc(kpiNum(k.target, k.unit))}</span>` : ''}</span></div>`).join('');
  const due = (w.tests_due || []).map(i => `${row(i)}<span class="muted gr-num">${esc(grDay(i.test_to))}</span></a>`).join('');
  const top = (w.top_ideas || []).map(i => `${row(i)}<span class="gr-ice">ICE ${esc(grX(i.ice))}</span></a>`).join('');
  const verdicts = (w.recent_verdicts || []).map(i => `<a class="gr-row gr-row-v" href="${grTaskHref(i.task_id)}"><span class="gr-row-t">${esc(i.title)}</span>${grStatus(i.status)}
    <span class="muted gr-num">${esc(grDay(i.verdict_at))}</span>${i.result || i.learning ? `<span class="gr-row-l">${esc([i.result, i.learning].filter(Boolean).join(' · '))}</span>` : ''}</a>`).join('');
  $('#gr-body').innerHTML = grCrumbs(GR_ROUTE, 'Growth', 'This week') + `<div class="gr-week">
    ${sec('Guardrails', guards, 'gr-amber')}${sec('Readings', readings)}${sec('Tests due', due)}${sec('Top ideas', top)}${sec('Verdicts', verdicts)}</div>`;
}
