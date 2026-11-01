/* ui/app/growth-page.js — The Growth section (#/growth, docs/growth.md): its head (tabs Ideas · Buyers · Channels ·
   Sources, the page's one action and the Learning icon) and Ideas, the Bets × Channels grid (a list grouped by bet on a
   phone) with + Idea, which can also ask the marketing lead to propose ideas. Buyers, Channels, Sources and Learning
   are their own files (growth-buyers.js, growth-channels.js, growth-sources.js, growth-learning.js); the Idea panel on
   a task is growth-idea.js; Brand is brand-page.js. Also what those files share: formats, idea cards, a small dialog,
   the owner picker, the lists the forms pick from. Old addresses (This week, Segments, Go-to-market) land on what
   replaced them. Reads GET /api/v2/growth/{ideas,kpis,channels,segments} and GET /api/v2/strategy (bets).
   Every write is the server's to allow; edit controls show to the owner and admins.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GR_ROUTE = '#/growth', GR_BUYERS = GR_ROUTE + '/buyers', GR_CHANNELS = GR_ROUTE + '/channels', GR_SOURCES = GR_ROUTE + '/sources',
  GR_LEARNING = GR_ROUTE + '/learning', BRAND_ROUTE = '#/strategy/brand';
// Where Growth and Go-to-market lived, under Strategy: redesignRoute sends them here, and pageGrowth on from there.
const GR_OLD_ROUTE = '#/strategy/growth', GTM_OLD_ROUTE = '#/strategy/gtm';
const GR_TABS = [['', GR_ROUTE, 'Ideas'], ['buyers', GR_BUYERS, 'Buyers'], ['channels', GR_CHANNELS, 'Channels'], ['sources', GR_SOURCES, 'Sources']];
let GR = null;            // the open Growth page: {load, sub, data}
let GR_LOAD = 0;
const GR_STAGES = [['reach', 'Reach'], ['acquire', 'Acquire'], ['activate', 'Activate'], ['retain', 'Retain'], ['refer', 'Refer']];
const GR_STATUS = {idea: ['Idea', ''], testing: ['Testing', 'in-progress'], paused: ['Paused', 'waiting'], won: ['Won', 'ok'],
  lost: ['Lost', 'fail'], inconclusive: ['Inconclusive', 'waiting'], dropped: ['Dropped', '']};
const GR_CH_STAGE = {idea: ['Idea', ''], testing: ['Testing', 'in-progress'], scaling: ['Scaling', 'ok'], paused: ['Paused', 'waiting']};
const GR_RESULTS = ['won', 'lost', 'inconclusive', 'dropped'];

// ----------------------------------------------------------------- shared formats and pieces
// Who may change company-level growth records: the owner or an admin (the server checks and is the one that enforces it).
const grAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
const grMe = () => myActor();
// An owner ref as Tico writes them: bot:<id>, human:<id>, or a bare bot id (older rows: a slug).
const grWho = ref => {
  const s = String(ref || '');
  if (!s) return '';
  if (s.includes(':')) return actorLabel(s);
  const bot = botByRef(s);
  return bot ? botDisplayName(bot.id) : personDisplay(s);
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
// An idea's status as a small dot and word, for a card or a row.
const grMark = s => { const st = GR_STATUS[s] ? s : 'idea'; return `<span class="gr-mk gr-mk-${st}"><i aria-hidden="true"></i>${esc(GR_STATUS[st][0])}</span>`; };
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
// The people and bots a row can belong to, as human:<id> and bot:<id>.
function grOwnerOpts(selected) {
  const sel = String(selected || ''), picked = sel.startsWith('human:') ? '' : botByRef(sel)?.id;
  const people = (S.people || []).filter(p => !p.hidden).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived').slice().sort((a, b) => String(a.display_name || a.slug).localeCompare(String(b.display_name || b.slug)));
  return `<option value="">—</option><optgroup label="People">${people.map(p => `<option value="human:${esc(p.id)}"${sel === 'human:' + p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e => `<option value="bot:${esc(e.id)}"${picked === e.id ? ' selected' : ''}>${esc(e.display_name || e.slug)}</option>`).join('')}</optgroup>`;
}
// Retire asks once more on the same button (no window.confirm); `after` runs once it is retired.
function grRetireButton(btn, dlg, path, after) {
  btn.onclick = async () => {
    if (!btn.dataset.armed) { btn.dataset.armed = '1'; btn.textContent = 'Retire for good'; return; }
    btn.disabled = true;
    try { await post(path, {}); toast('Retired'); dlg.close(); after?.(); }
    catch (e) { toast(e.message || 'Not retired', true); btn.disabled = false; }
  };
}

// Testing first, then ideas, then results; within each, the highest ICE first.
const GR_ORDER = ['testing', 'paused', 'idea', ...GR_RESULTS];
const grRank = i => { const n = GR_ORDER.indexOf(i.status || 'idea'); return n < 0 ? 2 : n; };
const grSort = list => list.slice().sort((a, b) => grRank(a) - grRank(b) || (b.ice ?? -1) - (a.ice ?? -1));
function grIdeaCard(i, chans, {chan = true, result = false} = {}) {
  const tags = chan ? (i.channels || []).map(s => `<span class="gr-tag">${esc(grChanName(chans, s))}</span>`).join('') : '';
  return `<a class="gr-ic gr-is-${esc(i.status || 'idea')}" href="${grTaskHref(i.task_id)}" data-idea="${esc(i.task_id)}"><span class="gr-ic-t">${esc(i.title)}</span>
    <span class="gr-ic-f">${grMark(i.status)}${tags}</span>${result && i.result ? `<span class="gr-ic-r">${esc(i.result)}</span>` : ''}</a>`;
}

// The bets a reader sees: the owner's live bets, everyone else the published version's.
function grBetsOf(s) {
  if (!s) return [];
  const owner = (s.may_edit ?? S.me?.role === 'owner') && !!s.draft;
  return ((owner ? s.bets : s.current?.bets) || []).filter(b => b.status !== 'retired')
    .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).map((b, n) => ({...b, n: n + 1}));
}
// What the forms pick from: KPIs, channels, buyers (segments) and bets, read together.
async function grRefs(fresh = false) {
  const opts = fresh ? {fresh: true} : undefined;
  const [k, c, s, g] = await Promise.all([get('/v2/growth/kpis', opts).catch(() => null), get('/v2/growth/channels', opts).catch(() => null),
    get('/v2/strategy', opts).catch(() => null), gsRead(fresh)]);
  return {kpis: k?.kpis || [], channels: c?.channels || [], segments: g, bets: grBetsOf(s), failed: !k || !c || !s};
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
    ${chans.length ? `<fieldset class="gr-fs"><legend>Channels</legend>${grChecks('channels', chans, i.channels || [])}</fieldset>` : ''}
    ${gsChecks(refs.segments, i.segments || [])}`;
}
// The form as a body; given the idea it edits, only the fields that changed.
function grIdeaBody(form, was = null) {
  const v = n => form.elements[n]?.value.trim() ?? undefined;
  const body = {title: v('title'), hypothesis: v('hypothesis'), bet_id: v('bet_id') || null, kpi_id: v('kpi_id') || null,
    channels: grChecked(form, 'channels')};
  if (form.elements.prediction) body.prediction = v('prediction');
  const segs = gsPicked(form);
  if (segs) body.segments = segs;
  if (!body.title) throw new Error('A title');
  if (!was) return body;
  const same = (a, b) => Array.isArray(a) ? [...a].sort().join('\n') === [...(b || [])].sort().join('\n') : (a ?? '') === (b ?? '');
  return Object.fromEntries(Object.entries(body).filter(([k, x]) => !same(x, was[k])));
}
// + Idea: the form, and with a marketing lead on the team a second way out, asking them to propose ideas instead.
function grNewIdea(after) {
  void grRefs().then(refs => {
    const dlg = grDialog('New idea', grIdeaFields(refs), 'Add', async form => {
      const out = await post('/v2/growth/ideas', grIdeaBody(form));
      toast('Idea added');
      after?.(out);
    });
    const lead = grLead();
    if (!lead) return;
    const ask = Object.assign(document.createElement('button'), {type: 'button', className: 'ghost gr-ask',
      textContent: `Ask ${lead.display_name || lead.slug} to propose`});
    ask.dataset.grPropose = '';
    ask.onclick = async () => { if (await grPropose(lead, ask)) dlg.close(); };
    dlg.querySelector('.gr-foot').prepend(ask);
  });
}

// The ask itself carries the steps, for a lead whose bot predates the propose-ideas playbook.
const GR_PROPOSE_BODY = `File 3–8 growth ideas. Start no tests.

1. Read: \`hub gtm show\`, \`hub brand summary\`, \`hub growth channel list\`, \`hub growth kpis\`, \`hub growth idea list\`, \`hub strategy show\`.
2. Skip duplicates: \`hub growth idea list --q "<two words>"\`.
3. File each with \`hub growth idea create --title ... --hypothesis "If we X for Y, Z moves because W" --prediction "<KPI> from <now> to <number> by <date>" --kpi <id> --channel <slug> --i N --c N --e N --source-kind strategy|competitor|listening|peec|lost_test|person|other --source-ref <item>\`, adding \`--bet <id>\` when a strategy bet fits. With no bets yet, file without one.
4. Close this task with the count and the top three by ICE; say so if the strategy has no bets.`;

// The marketing lead: who "Ask … to propose" asks.
const grLead = () => (S.emps || []).find(e => e.status !== 'archived' && (e.template === 'marketing-lead' || e.slug === 'marketing-lead'
  || /^(head of marketing|growth lead|marketing lead)$/i.test(String(e.display_name || '').trim())));
async function grPropose(lead, button) {
  if (button) button.disabled = true;
  try {
    await post('/v2/tasks', {title: 'Propose growth ideas', body: GR_PROPOSE_BODY, owner: lead.id});
    toast(`Sent to ${lead.display_name || lead.slug}`);
    return true;
  } catch (e) { toast(e.message || 'Not sent', true); if (button) button.disabled = false; return false; }
}

// ----------------------------------------------------------------- the section
// The Growth section's heading: its tabs, the current page's one action (#strat-actions) and the Learning icon.
function growthHead(tab) {
  return `<div class="strat-head rd-strat-head gr-head"><h1>Growth</h1>
      <nav class="tabs slim strat-tabs" aria-label="Growth">${GR_TABS.map(([id, href, label]) =>
        `<a href="${href}" data-gr-tab="${id || 'ideas'}"${tab === id ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>
      <span class="spacer"></span><div class="strat-actions" id="strat-actions"></div>
      <a class="ghost gr-brain${tab === 'learning' ? ' cur' : ''}" href="${GR_LEARNING}" aria-label="Learning" title="Learning"${tab === 'learning' ? ' aria-current="page"' : ''}><span class="nav-icon" aria-hidden="true">psychology</span></a></div>`;
}
// A page's tools above its body, right-aligned, if any.
const grTools = (tools = '') => tools ? `<div class="gr-crumbs"><span class="spacer"></span>${tools}</div>` : '';

// After every route: forget the Growth and Brand pages once off them, so a late answer for one never draws over
// another (each page's reads check that its own state is still the open one).
window.growthStop = function growthStop() {
  const r = S.route || '';
  gkvStop(r);
  const offGrowth = !rdUnder(r, GR_ROUTE), offBrand = !rdUnder(r, BRAND_ROUTE);
  if (!(GR && offGrowth) && !(BR && offBrand)) return;
  GR_LOAD++;
  if (offGrowth) GR = null;
  if (offBrand) BR = null;
  if (offGrowth && offBrand) document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
};
// Old addresses: This week is gone, Segments are Buyers, and Go-to-market's tabs live on Buyers, Channels and Sources.
function grMoved(parts) {
  if (parts[0] === 'week') return GR_ROUTE;
  if (parts[0] === 'segments') return GR_BUYERS;
  if (parts[0] === 'gtm') return parts[1] === 'sources' ? GR_SOURCES : parts[1] === 'channels' ? GR_CHANNELS : GR_BUYERS;
  return parts[0] && !['buyers', 'channels', 'sources', 'learning'].includes(parts[0]) ? GR_ROUTE : '';
}
function pageGrowth() {
  const path = (S.route || '').split('?')[0];
  const parts = path.slice(GR_ROUTE.length + 1).split('/'), sub = parts[0] || '';
  const moved = grMoved(parts);
  if (moved) { location.replace(moved); return; }
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const load = ++GR_LOAD;
  GR = {load, sub, data: null};
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = growthHead(sub) + `<div id="gr-body" class="gr-body">${skelRows(6, {dot: false})}</div>`;
  if (sub === 'buyers') return pageGrowthBuyers(GR);
  if (sub === 'channels') return pageGrowthChannels(GR);
  if (sub === 'sources') return pageGrowthSources(GR);
  if (sub === 'learning') return pageGrowthLearning(GR);
  void growthIdeas(GR);
}

// ----------------------------------------------------------------- Ideas
async function growthIdeas(state) {
  let data;
  try {
    const [ideas, refs] = await Promise.all([get('/v2/growth/ideas', {fresh: true}), grRefs(true)]);
    data = {ideas: ideas.ideas || [], ...refs};
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = data;
  patchHTML($('#strat-actions'), '<button class="primary" type="button" data-gr-new>+ Idea</button>');
  $('#strat-actions [data-gr-new]').onclick = () => grNewIdea(() => void growthIdeas(state));
  $('#gr-body').innerHTML = grGrid(data) + grPhoneList(data);
}
// The rows and columns both layouts share: live bets (then No bet when an idea has none, or there are no bets) and live
// channels (then No channel the same way). An idea on two channels shows in both.
function grAxes(d) {
  const chans = d.channels.filter(c => !c.retired_at);
  const inChan = (i, slug) => slug ? (i.channels || []).includes(slug) : !(i.channels || []).some(s => chans.some(c => c.slug === s));
  const inBet = (i, id) => id ? i.bet_id === id : !i.bet_id || !d.bets.some(b => b.id === i.bet_id);
  const bets = d.bets.map(b => ({id: b.id, label: `${b.n} · ${b.statement}`}));
  if (!bets.length || d.ideas.some(i => inBet(i, ''))) bets.push({id: '', label: 'No bet'});
  const cols = chans.map(c => ({slug: c.slug, name: c.name}));
  if (!cols.length || d.ideas.some(i => inChan(i, ''))) cols.push({slug: '', name: 'No channel'});
  return {bets, cols, inBet, inChan};
}
function grGrid(d) {
  const {bets, cols, inBet, inChan} = grAxes(d);
  return `<div class="card gr-scroll gr-grid-wrap"><table class="gr-grid"><thead><tr><th scope="col"><span class="sr-only">Bet</span></th>${cols.map(c =>
    `<th scope="col">${esc(c.name)}</th>`).join('')}</tr></thead>
    <tbody>${bets.map(b => `<tr><th scope="row">${esc(b.label)}</th>${cols.map(c =>
      `<td>${grSort(d.ideas.filter(i => inBet(i, b.id) && inChan(i, c.slug))).map(i => grIdeaCard(i, d.channels, {chan: false})).join('')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
// A phone: no grid. Each bet with its ideas as rows: title, channels, status.
function grPhoneList(d) {
  const {bets, inBet} = grAxes(d);
  return `<div class="gr-plist">${bets.map(b => {
    const list = grSort(d.ideas.filter(i => inBet(i, b.id)));
    return list.length ? `<section class="card gr-pbet"><h3 class="gr-pbet-h">${esc(b.label)}</h3>${list.map(i =>
      `<a class="gr-prow gr-is-${esc(i.status || 'idea')}" href="${grTaskHref(i.task_id)}" data-idea="${esc(i.task_id)}"><span class="gr-prow-t">${esc(i.title)}</span>
        <span class="gr-prow-f">${(i.channels || []).length ? `<span class="muted">${esc(i.channels.map(s => grChanName(d.channels, s)).join(', '))}</span>` : ''}${grMark(i.status)}</span></a>`).join('')}</section>` : '';
  }).join('')}</div>`;
}
