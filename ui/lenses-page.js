/* Lenses are the Strategy section's second tab (ui/strategy-page.js): thought exercises run against the strategy, such as
   Blue Ocean or Scenarios (backend/lenses.py). The list shows each lens and the past runs; Run asks the lens's steps one at a
   time in a panel and goes to the run page, which draws the result: a summary, a chart in hand-written SVG, sections, how the
   result bears on the bets, and changes the owner may add to the draft. Nothing here publishes. */
'use strict';
const LENSES_ROUTE = '#/strategy/lenses';
const isLensesRoute = r => r === LENSES_ROUTE || r.startsWith(LENSES_ROUTE + '?') || r.startsWith(LENSES_ROUTE + '/');
let LENS = null;           // the open page: {load, timer, bets, label, watch}
let LENS_LOAD = 0;
let LENS_FLOW = null;      // the open run panel: {lens, i, answers, on, published, notice}
const LENS_GROUPS = [['strategy', 'Strategy'], ['market', 'Market']];
const LENS_STATUS = {running: ['Running', 'in-progress'], done: ['Done', 'ok'], failed: ['Failed', 'fail'], no_model: ['No model', 'needs']};
const LENS_FINDING = {supports: ['Supports', 'ok'], challenges: ['Challenges', 'fail'], gap: ['Gap', 'needs']};
const LENS_POWER = {scale: 'Scale', network: 'Network', 'counter-positioning': 'Counter-positioning', switching: 'Switching costs',
  brand: 'Brand', cornered: 'Cornered resource', process: 'Process', none: 'None'};
const LENS_MAX_COLS = 6, LENS_MAX_ROWS = 10, LENS_MAX_LIST = 12;
const LENS_POLL_MS = 2000;

const lensMayRun = () => S.me?.role === 'owner';
const lensRunId = () => {
  const rest = S.route.slice(LENSES_ROUTE.length).split('?')[0];
  return rest.startsWith('/') ? decodeURIComponent(rest.slice(1)) : '';
};
const lensRunHref = id => LENSES_ROUTE + '/' + encodeURIComponent(id);
const lensErrorCode = error => error?.body?.error?.code || '';
const lensFirstLine = text => String(text || '').split('\n').find(line => line.trim()) || '';

window.lensesStop = function lensesStop() {
  LENS_LOAD++;
  if (LENS) { clearTimeout(LENS.timer); LENS.watch?.disconnect(); }
  LENS = null;
  LENS_FLOW = null;
  const dlg = document.getElementById('lens-flow');
  if (dlg?.open) dlg.close();
};

window.pageLenses = async function pageLenses() {
  window.lensesStop();
  const load = LENS_LOAD;
  const main = $('#main');
  main.classList.add('strategy-layout');
  main.innerHTML = strategyHeadHtml('lenses') + `<div id="lens-body" class="lens-body">${skelRows(5, {dot: false})}</div>`;
  LENS = {load, timer: 0, bets: [], label: 'Bet', watch: null};
  const id = lensRunId();
  if (id) await lensRunPage(id, load); else await lensListPage(load);
};

const lensWhen = iso => iso ? `<time datetime="${esc(iso)}" title="${esc(fmt(iso))}">${esc(stratDate(iso))}</time>` : '';
const lensStatusPill = status => { const [label, cls] = LENS_STATUS[status] || [status, '']; return `<span class="pill ${cls}" data-lens-status="${esc(status)}">${esc(label)}</span>`; };

// ---------------------------------------------------------------- the list
async function lensListPage(load) {
  let lenses, runs;
  try {
    [lenses, runs] = await Promise.all([get('/v2/lenses', {fresh: true}), get('/v2/lenses/runs', {fresh: true})]);
  } catch (error) {
    if (load === LENS_LOAD && $('#lens-body')) $('#lens-body').innerHTML = `<p class="err">${esc(error.message || 'Could not load the lenses')}</p>`;
    return;
  }
  if (load !== LENS_LOAD || !$('#lens-body')) return;
  const list = lenses?.lenses || [], past = runs?.runs || [], mayRun = lensMayRun();
  const row = l => `<li class="lens-row" data-lens="${esc(l.id)}"><div class="lens-main"><b class="lens-name">${esc(l.name)}</b><span class="lens-desc">${esc(l.description || '')}</span></div>
      <span class="lens-min tnum muted">${esc(l.minutes)} min</span>${mayRun ? `<button class="ghost" type="button" data-lens-run="${esc(l.id)}">Run</button>` : ''}</li>`;
  const groups = LENS_GROUPS.map(([key, title]) => {
    const rows = list.filter(l => l.applies_to === key);
    return rows.length ? `<section class="lens-group" data-lens-group="${key}"><h2 class="strat-k">${title}</h2><ul class="lens-list">${rows.map(row).join('')}</ul></section>` : '';
  }).join('');
  const names = Object.fromEntries(list.map(l => [l.id, l.name]));
  const prior = r => `<li><a class="lens-past" href="${lensRunHref(r.id)}" data-lens-run-row="${esc(r.id)}">
      <span class="lens-past-top"><b class="lens-name">${esc(r.name || names[r.lens] || r.lens)}</b>${r.on_draft ? '<span class="pill">draft</span>' : ''}${lensStatusPill(r.status)}
        <span class="muted lens-past-when">${lensWhen(r.created_at)}</span></span>
      ${lensFirstLine(r.result?.summary) ? `<span class="lens-past-sum">${esc(lensFirstLine(r.result.summary))}</span>` : ''}</a></li>`;
  $('#lens-body').innerHTML = `<div class="lens-wrap">${groups || '<p class="muted strat-empty">No lenses</p>'}
    <section class="lens-group" data-lens-group="runs"><h2 class="strat-k">Past runs</h2>
      ${past.length ? `<ul class="lens-list lens-pastlist">${past.map(prior).join('')}</ul>` : '<p class="muted strat-empty">None yet</p>'}</section></div>`;
  for (const b of document.querySelectorAll('[data-lens-run]')) b.onclick = () => void lensFlowOpen(list.find(l => l.id === b.dataset.lensRun), b);
}

// ---------------------------------------------------------------- the run panel
function lensDialog() {
  let dlg = document.getElementById('lens-flow');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'lens-flow'; dlg.className = 'tmodal gpanel lens-flow';
    dlg.setAttribute('aria-label', 'Run a lens');
    dlg.addEventListener('close', () => { LENS_FLOW = null; });
    document.body.appendChild(dlg);
  }
  return dlg;
}

async function lensFlowOpen(lens, button) {
  if (!lens) return;
  if (button) button.disabled = true;
  let published = true;
  // Whether a version is published decides what a strategy lens may run on; a market lens ignores it.
  try { published = !!(await get('/v2/strategy', {fresh: true}))?.current; }
  catch (error) { toast(error.message || 'Could not open it', true); if (button) button.disabled = false; return; }
  if (button) button.disabled = false;
  LENS_FLOW = {lens, i: 0, answers: {}, on: published ? 'published' : 'draft', published, notice: ''};
  const dlg = lensDialog();
  if (!dlg.open) dlg.showModal();
  lensFlowRender();
}

const lensList = value => (Array.isArray(value) ? value : []).map(x => String(x ?? '').trim()).filter(Boolean);

// The grid a scores step asks: its rows are the answers of one list step, its columns "Us" and the answers of another.
function lensGrid(flow, step) {
  const rows = lensList(flow.answers[step.rows_from]).slice(0, LENS_MAX_ROWS);
  const rivals = lensList(flow.answers[step.columns_from]).slice(0, LENS_MAX_COLS - 1);
  return {rows, columns: ['Us', ...rivals], cut: lensList(flow.answers[step.rows_from]).length > rows.length || lensList(flow.answers[step.columns_from]).length > rivals.length};
}

function lensInputHtml(flow, step) {
  const value = flow.answers[step.key];
  if (step.input === 'list') {
    const rows = lensList(value);
    return `<ul class="lens-rows" data-lens-rows>${(rows.length ? rows : ['']).map(lensRowHtml).join('')}</ul>
      <button class="ghost strat-mini" type="button" data-lens-add>Add</button>`;
  }
  if (step.input === 'scores') {
    const grid = lensGrid(flow, step);
    if (!grid.rows.length || grid.columns.length < 2) return '<p class="lens-note" data-lens-blocked>Needs the answers from the earlier steps. Go back, or skip.</p>';
    const saved = value && typeof value === 'object' ? value : {};
    const at = (factor, column) => {
      const r = (saved.rows || []).find(x => x.factor === factor), c = (saved.columns || []).indexOf(column);
      return r && c >= 0 && Number.isInteger(r.scores?.[c]) ? r.scores[c] : '';
    };
    return `<div class="lens-scroll"><table class="lens-scores"><thead><tr><th><span class="sr-only">Factor</span></th>${grid.columns.map(c => `<th scope="col" title="${esc(c)}">${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${grid.rows.map((r, ri) => `<tr><th scope="row" title="${esc(r)}">${esc(r)}</th>${grid.columns.map((c, ci) =>
        `<td><input type="number" inputmode="numeric" min="0" max="5" step="1" data-r="${ri}" data-c="${ci}" value="${esc(at(r, c))}" aria-label="${esc(r)}, ${esc(c)}"></td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="lens-scale muted">0 is none, 5 is the most${grid.cut ? '. Only the first rivals and factors are scored' : ''}</p>`;
  }
  if (step.input === 'choice') {
    return `<div class="lens-choices" role="radiogroup" aria-label="${esc(step.ask)}">${(step.options || []).map(o =>
      `<label class="lens-pill"><input type="radio" name="choice" value="${esc(o)}"${value === o ? ' checked' : ''}><span>${esc(o)}</span></label>`).join('')}</div>`;
  }
  return `<textarea name="text" rows="6" maxlength="2000" aria-label="${esc(step.ask)}">${esc(typeof value === 'string' ? value : '')}</textarea>`;
}
const lensRowHtml = (text = '') => `<li><input type="text" maxlength="200" autocomplete="off" value="${esc(text)}" aria-label="Item"><button class="ghost lens-x" type="button" data-lens-del aria-label="Remove">✕</button></li>`;

// What the step's inputs hold now: the answer, or undefined for none. A scores grid with a half-filled row is {error}.
function lensRead(dlg, flow, step) {
  const form = dlg.querySelector('[data-lens-step]');
  if (!form || !step) return undefined;
  if (step.input === 'list') {
    const items = [...form.querySelectorAll('[data-lens-rows] input')].map(i => i.value.trim()).filter(Boolean).slice(0, LENS_MAX_LIST);
    return items.length ? items : undefined;
  }
  if (step.input === 'scores') {
    const cells = [...form.querySelectorAll('.lens-scores input')];
    if (!cells.length) return undefined;
    const grid = lensGrid(flow, step), out = [];
    for (let r = 0; r < grid.rows.length; r++) {
      const mine = cells.filter(c => Number(c.dataset.r) === r), have = mine.filter(c => c.value.trim() !== '');
      for (const c of mine) c.classList.toggle('bad', have.length > 0 && have.length < mine.length && c.value.trim() === '');
      if (!have.length) continue;
      if (have.length < mine.length) return {error: 'Score every column in a row, or leave the row empty'};
      out.push({factor: grid.rows[r], scores: mine.map(c => Math.min(5, Math.max(0, Math.round(Number(c.value)) || 0)))});
    }
    return out.length ? {columns: grid.columns, rows: out} : undefined;
  }
  if (step.input === 'choice') return form.querySelector('input[name="choice"]:checked')?.value;
  const text = form.elements.text?.value.trim();
  return text ? text : undefined;
}

function lensFlowRender() {
  const flow = LENS_FLOW, dlg = document.getElementById('lens-flow');
  if (!flow || !dlg) return;
  const steps = flow.lens.steps || [], step = steps[flow.i] || null, last = flow.i >= steps.length - 1;
  const toggle = last && flow.lens.applies_to === 'strategy' && lensMayRun()
    ? `<div class="lens-on"><span class="muted">Run on</span><div class="lens-seg" role="group" aria-label="Run on">${
        (flow.published ? [['published', 'Published'], ['draft', 'Draft']] : [['draft', 'Draft']]).map(([v, label]) =>
          `<button type="button" data-lens-on="${v}" aria-pressed="${flow.on === v}">${label}</button>`).join('')}</div></div>` : '';
  dlg.innerHTML = `<div class="tmodal-head"><h2>${esc(flow.lens.name)}</h2><span class="muted tnum lens-n">${steps.length ? `${flow.i + 1} of ${steps.length}` : ''}</span><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-lens-close aria-label="Close">✕</button></div>
    <form class="tmodal-body lens-step" data-lens-step novalidate>
      ${step ? `<p class="lens-ask" id="lens-ask">${esc(step.ask)}</p>${step.hint ? `<p class="lens-hint muted">${esc(step.hint)}</p>` : ''}${lensInputHtml(flow, step)}` : ''}
      ${flow.notice ? `<p class="lens-note" data-lens-notice>${esc(flow.notice)}</p>` : ''}
      ${toggle}
      <p class="lens-note" data-lens-error hidden></p>
      <div class="goal-actions lens-actions"><button class="ghost" type="button" data-lens-back${flow.i === 0 ? ' disabled' : ''}>Back</button><span class="spacer"></span>
        <button class="primary" type="submit" data-lens-next>${last ? 'Run' : 'Next'}</button></div>
      ${step ? `<button class="linkish lens-skip" type="button" data-lens-skip>Skip, let the Assistant assume</button>` : ''}
    </form>`;
  lensFlowBind(dlg, flow, step, last);
  (dlg.querySelector('.lens-rows input, textarea, .lens-scores input, .lens-pill input') || dlg.querySelector('[data-lens-next]'))?.focus();
}

function lensFlowBind(dlg, flow, step, last) {
  const form = dlg.querySelector('[data-lens-step]'), say = text => { const e = dlg.querySelector('[data-lens-error]'); e.textContent = text || ''; e.hidden = !text; };
  dlg.querySelector('[data-lens-close]').onclick = () => dlg.close();
  const save = () => {
    const value = lensRead(dlg, flow, step);
    if (value?.error) { say(value.error); return false; }
    if (step) { if (value === undefined) delete flow.answers[step.key]; else flow.answers[step.key] = value; }
    return true;
  };
  const go = async skip => {
    if (skip) { if (step) delete flow.answers[step.key]; } else if (!save()) return;
    if (last) return lensFlowRun(dlg, flow);
    flow.i++; flow.notice = ''; lensFlowRender();
  };
  form.onsubmit = ev => { ev.preventDefault(); void go(false); };
  dlg.querySelector('[data-lens-back]').onclick = () => { if (save()) { flow.i = Math.max(0, flow.i - 1); flow.notice = ''; lensFlowRender(); } };
  const skip = dlg.querySelector('[data-lens-skip]');
  if (skip) skip.onclick = () => void go(true);
  for (const b of dlg.querySelectorAll('[data-lens-on]')) b.onclick = () => { flow.on = b.dataset.lensOn; for (const x of dlg.querySelectorAll('[data-lens-on]')) x.setAttribute('aria-pressed', String(x === b)); };
  // A list: Enter adds a row, and Enter on an empty last row moves on.
  const rows = dlg.querySelector('[data-lens-rows]');
  if (rows) {
    const add = after => {
      if (rows.children.length >= LENS_MAX_LIST) return;
      const li = document.createElement('li'); li.innerHTML = lensRowHtml('').replace(/^<li>|<\/li>$/g, '');
      (after ? after.after(li) : rows.append(li)); li.querySelector('input').focus();
    };
    rows.onkeydown = ev => {
      const input = ev.target.closest('input');
      if (!input || ev.key !== 'Enter' || ev.isComposing) return;
      ev.preventDefault();
      const li = input.closest('li');
      if (input.value.trim()) add(li);
      else if (li === rows.lastElementChild && [...rows.querySelectorAll('input')].some(i => i.value.trim())) form.requestSubmit();
    };
    rows.onclick = ev => {
      const del = ev.target.closest('[data-lens-del]');
      if (!del) return;
      const li = del.closest('li');
      if (rows.children.length > 1) li.remove(); else li.querySelector('input').value = '';
    };
    dlg.querySelector('[data-lens-add]').onclick = () => add(rows.lastElementChild);
  }
  // A score is a whole number from 0 to 5.
  for (const cell of dlg.querySelectorAll('.lens-scores input')) cell.onchange = () => {
    if (cell.value.trim() === '') return;
    cell.value = String(Math.min(5, Math.max(0, Math.round(Number(cell.value)) || 0)));
    cell.classList.remove('bad');
  };
}

async function lensFlowRun(dlg, flow) {
  const buttons = [...dlg.querySelectorAll('button')];
  buttons.forEach(b => { b.disabled = true; });
  const body = {answers: flow.answers};
  if (flow.lens.applies_to === 'strategy') body.on = flow.on;
  let run;
  try { run = await post(`/v2/lenses/${encodeURIComponent(flow.lens.id)}/runs`, body); }
  catch (error) {
    if (LENS_FLOW !== flow) return;
    // Nothing published yet: the owner may run it on the draft instead.
    if (lensErrorCode(error) === 'no_published') { flow.published = false; flow.on = 'draft'; flow.notice = 'Nothing is published yet. Run on the draft?'; lensFlowRender(); return; }
    buttons.forEach(b => { b.disabled = false; });
    dlg.querySelector('[data-lens-back]').disabled = flow.i === 0;
    toast(error.message || 'Could not run it', true);
    return;
  }
  LENS_FLOW = null;
  dlg.close();
  location.hash = lensRunHref(run.id);
}

// ---------------------------------------------------------------- the run page
async function lensRunPage(id, load) {
  let run;
  try { run = await get('/v2/lenses/runs/' + encodeURIComponent(id), {fresh: true}); }
  catch (error) {
    if (load === LENS_LOAD && $('#lens-body')) $('#lens-body').innerHTML = `<p class="err">${esc(error.status === 404 ? 'That run is not here' : error.message || 'Could not load the run')}</p><p><a href="${LENSES_ROUTE}">Back to lenses</a></p>`;
    return;
  }
  if (load !== LENS_LOAD || !$('#lens-body')) return;
  // The bets a finding names, numbered the way the Strategy page numbers them; the page works without them.
  try {
    const data = await get('/v2/strategy');
    const owner = (data?.may_edit ?? S.me?.role === 'owner') && !!data?.draft;
    LENS.bets = ((owner ? data.bets : data?.current?.bets) || []).filter(b => b.status !== 'retired').sort(stratRank);
    LENS.label = data?.label_one || 'Bet';
  } catch { /* labels fall back to "Bet" */ }
  if (load !== LENS_LOAD || !$('#lens-body')) return;
  lensRunRender(run);
  if (run.status === 'running') lensPoll(id, load);
}

function lensPoll(id, load) {
  clearTimeout(LENS.timer);
  LENS.timer = setTimeout(async () => {
    let run = null;
    try { run = await get('/v2/lenses/runs/' + encodeURIComponent(id), {fresh: true}); } catch { /* try again */ }
    if (load !== LENS_LOAD || !LENS) return;
    if (run && run.status !== 'running') lensRunRender(run); else lensPoll(id, load);
  }, LENS_POLL_MS);
}

function lensBetLink(bets, betId, label) {
  const at = bets.findIndex(b => b.id === betId);
  return at < 0 ? '' : `<a class="lens-bet" href="#/strategy?bet=${encodeURIComponent(betId)}">${esc(label)} ${at + 1}</a>`;
}

function lensRunRender(run) {
  const body = $('#lens-body');
  if (!body || !LENS) return;
  LENS.watch?.disconnect(); LENS.watch = null;
  const owner = lensMayRun(), result = run.result || {};
  const meta = [lensWhen(run.created_at), run.created_by ? esc(goalActorName(run.created_by)) : '', run.strategy_n ? `v${esc(run.strategy_n)}` : ''].filter(Boolean).join(' · ');
  const head = `<div class="lens-head"><a class="ghost strat-back lens-back" href="${LENSES_ROUTE}">‹ Lenses</a>
      <h2 class="lens-title">${esc(run.name || run.lens)}</h2>
      <p class="lens-meta">${run.on_draft ? '<span class="pill">draft</span>' : ''}${run.status === 'done' ? '' : lensStatusPill(run.status)}<span>${meta}</span></p></div>`;
  let inner;
  if (run.status === 'running') inner = '<p class="lens-running muted" data-lens-running role="status">Running…</p>';
  else if (run.status === 'failed') inner = `<p class="err" data-lens-failed>${esc(run.error?.message || 'The run failed')}</p>${owner ? '<button class="ghost" type="button" data-lens-again>Run again</button>' : ''}`;
  else if (run.status === 'no_model') inner = `<p class="lens-note" data-lens-nomodel>No model provider is set up. Add one in <a href="#/settings/computers">Settings → Computers → Models</a>, then run it again.</p>${owner ? '<button class="ghost" type="button" data-lens-again>Run again</button>' : ''}`;
  else inner = lensResultHtml(run, result, owner);
  body.innerHTML = `<div class="lens-wrap lens-runpage" data-lens-run-page="${esc(run.id)}">${head}${inner}</div>`;
  lensDrawCharts(result.chart);
  const again = body.querySelector('[data-lens-again]');
  if (again) again.onclick = async () => {
    again.disabled = true;
    try {
      const next = await post(`/v2/lenses/${encodeURIComponent(run.lens)}/runs`, {answers: run.answers || {}, ...(run.applies_to === 'strategy' ? {on: run.on_draft ? 'draft' : 'published'} : {})});
      location.hash = lensRunHref(next.id);
    } catch (error) { toast(error.message || 'Could not run it', true); again.disabled = false; }
  };
  for (const b of body.querySelectorAll('[data-lens-apply]')) b.onclick = () => void lensApply(run, Number(b.dataset.lensApply), b);
  for (const b of body.querySelectorAll('[data-lens-sharpen]')) b.onclick = () => void lensSharpen(run, Number(b.dataset.lensSharpen), b);
}

function lensResultHtml(run, result, owner) {
  const sec = (key, title, inner) => inner ? `<section class="lens-sec" data-lens-sec="${key}"><h3 class="strat-k">${esc(title)}</h3>${inner}</section>` : '';
  const sections = (result.sections || []).filter(s => s && (s.title || s.body)).map(s =>
    `<section class="lens-sec" data-lens-sec="section"><h3 class="strat-k">${esc(s.title || '')}</h3><div class="md strat-md">${safeMd(s.body || '')}</div></section>`).join('');
  const findings = (result.findings || []).map(f => {
    const [label, cls] = LENS_FINDING[f.kind] || [f.kind, ''];
    return `<li class="lens-finding"><span class="pill ${cls}">${esc(label)}</span>${f.bet_id ? lensBetLink(LENS.bets, f.bet_id, LENS.label) : ''}<span class="lens-ftext">${esc(f.text)}</span></li>`;
  }).join('');
  const applied = new Set(run.applied || []);
  const changes = (result.changes || []).map((c, i) => `<li class="lens-change" data-lens-change="${i}"><span class="lens-ctext">${esc(c.label)}</span>${
    applied.has(i) ? '<span class="lens-done muted">Added</span>' : owner && !run.proposal_id ? `<button class="ghost strat-mini" type="button" data-lens-apply="${i}">Add to draft</button>` : ''}</li>`).join('');
  const tasks = run.sharpen_tasks || {};
  const sharpen = (result.sharpen || []).map((s, i) => {
    const text = typeof s === 'string' ? s : s?.text || '', task = tasks[String(i)];
    return `<li class="lens-change" data-lens-sharpen-row="${i}"><span class="lens-ctext">${esc(text)}</span>${
      task ? `<span class="lens-done muted">Asked · <a href="#/task/${encodeURIComponent(task)}">task</a></span>` : owner ? `<button class="ghost strat-mini" type="button" data-lens-sharpen="${i}">Ask</button>` : ''}</li>`;
  }).join('');
  const proposal = run.proposal_id ? `<p class="lens-proposal muted" data-lens-proposal>${owner ? `Sent to you as a <a href="${NEEDS_YOU}">proposal</a>` : 'Sent to the owner as a proposal'}</p>` : '';
  return `${stratText(result.summary) ? `<p class="lens-summary">${esc(result.summary)}</p>` : ''}
    ${lensChartHtml(result.chart, LENS.bets, LENS.label)}${sections}
    ${sec('findings', 'Findings', findings ? `<ul class="lens-items">${findings}</ul>` : '')}
    ${sec('assumptions', 'Assumptions', (result.assumptions || []).length ? `<ul class="lens-bullets">${result.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : '')}
    ${sec('changes', 'Changes', (changes ? `<ul class="lens-items">${changes}</ul>` : '') + proposal)}
    ${sec('sharpen', 'Sharpen', sharpen ? `<ul class="lens-items">${sharpen}</ul>` : '')}`;
}

// Adds a change to the draft as the owner. The revision is the draft's as it is now.
async function lensApply(run, index, button) {
  const state = LENS;
  button.disabled = true;
  try {
    const strategy = await get('/v2/strategy', {fresh: true});
    const out = await post(`/v2/lenses/runs/${encodeURIComponent(run.id)}/apply`, {index, revision: strategy?.draft?.revision ?? null});
    if (LENS !== state) return;
    toast('Added to the draft');
    lensRunRender(out.run);
  } catch (error) {
    if (LENS !== state) return;
    if (error?.status === 409) {
      toast(lensErrorCode(error) === 'applied' ? 'Already added' : 'The draft changed, reloaded', true);
      try { lensRunRender(await get('/v2/lenses/runs/' + encodeURIComponent(run.id), {fresh: true})); } catch { button.disabled = false; }
      return;
    }
    toast(error.message || 'Could not add it', true);
    button.disabled = false;
  }
}

async function lensSharpen(run, index, button) {
  const state = LENS;
  button.disabled = true;
  try {
    const out = await post(`/v2/lenses/runs/${encodeURIComponent(run.id)}/sharpen/${index}`, {});
    if (LENS !== state) return;
    lensRunRender(out.run);
  } catch (error) {
    if (LENS !== state) return;
    toast(error.message || 'Could not ask', true);
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- charts
// Each chart is drawn at the width it has, so its text keeps its size on a phone; a value curve is drawn again when the
// width changes. The colours are the theme's (styles/lenses.css), so light and dark follow.
function lensChartHtml(chart, bets, label) {
  if (!chart || typeof chart !== 'object') return '';
  const kind = chart.kind;
  if (kind === 'value_curve' && Array.isArray(chart.factors) && Array.isArray(chart.series))
    return `<figure class="lens-chart" data-lens-chart="value_curve"><div class="lens-plot" data-lens-plot></div>${lensLegendHtml(chart.series)}</figure>`;
  if (kind === 'matrix_2x2') return lensMatrixHtml(chart);
  if (kind === 'power_table' && Array.isArray(chart.rows)) {
    const cols = ['Bet', 'Power', 'How', 'When'];
    const rows = chart.rows.map(r => [
      `${r.bet_id ? lensBetLink(bets, r.bet_id, label) + ' ' : ''}${esc(r.bet || '')}`,
      r.power === 'none' ? '<span class="pill needs">None</span>' : esc(LENS_POWER[r.power] || r.power || ''), esc(r.how || ''), esc(r.when || '')]);
    return lensTableHtml(cols.map(esc), rows, 'power_table');
  }
  if (kind === 'table' && Array.isArray(chart.columns) && Array.isArray(chart.rows))
    return lensTableHtml(chart.columns.map(c => esc(c)), chart.rows.map(r => (Array.isArray(r) ? r : []).map(c => esc(c))), 'table');
  if (kind === 'cascade' && Array.isArray(chart.steps))
    return `<ol class="lens-cascade" data-lens-chart="cascade">${chart.steps.map((s, i) =>
      `<li class="lens-step-card${s.weak ? ' weak' : ''}"${s.weak ? ' data-lens-weak' : ''}><span class="lens-step-n tnum" aria-hidden="true">${i + 1}</span>
        <div><div class="lens-step-top"><b>${esc(s.title)}</b>${s.weak ? '<span class="pill needs">Weakest</span>' : ''}</div>${s.text ? `<p>${esc(s.text)}</p>` : ''}</div></li>`).join('')}</ol>`;
  return '';
}

function lensTableHtml(columns, rows, kind) {
  return `<div class="lens-tablewrap" data-lens-chart="${kind}"><table class="lens-table"><thead><tr>${columns.map(c => `<th scope="col">${c}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td data-k="${esc(String(columns[i] ?? '').replace(/<[^>]*>/g, ''))}"><span class="lens-cell">${c}</span></td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

// Four worlds. The second letter of a cell's q is the height on y, the first the height on x: y high is the top row.
function lensMatrixHtml(chart) {
  const x = chart.x || {}, y = chart.y || {};
  const cell = q => {
    const c = (chart.cells || []).find(k => k.q === q);
    return `<div class="lens-quad" data-q="${q}">${c ? `<b>${esc(c.title)}</b>${c.text ? `<p>${esc(c.text)}</p>` : ''}` : '<span class="muted">-</span>'}</div>`;
  };
  return `<figure class="lens-matrix" data-lens-chart="matrix_2x2" role="group" aria-label="${esc(x.name)} by ${esc(y.name)}">
    <div class="lens-yaxis"><span class="lens-end" data-end="y-high">${esc(y.high)}</span><b class="lens-axis" data-axis="y">${esc(y.name)}</b><span class="lens-end" data-end="y-low">${esc(y.low)}</span></div>
    <div class="lens-quads">${cell('lh')}${cell('hh')}${cell('ll')}${cell('hl')}</div>
    <div class="lens-xaxis"><span class="lens-end" data-end="x-low">${esc(x.low)}</span><b class="lens-axis" data-axis="x">${esc(x.name)}</b><span class="lens-end" data-end="x-high">${esc(x.high)}</span></div></figure>`;
}

const LENS_SHAPES = ['circle', 'square', 'diamond', 'triangle', 'cross'];
// A marker at x,y. Us is a bigger filled circle; the others take a shape each, so the lines differ by more than colour.
function lensMark(shape, x, y, r) {
  const p = n => n.toFixed(1);
  if (shape === 'square') return `<rect x="${p(x - r)}" y="${p(y - r)}" width="${p(2 * r)}" height="${p(2 * r)}"/>`;
  if (shape === 'diamond') return `<polygon points="${p(x)},${p(y - r * 1.3)} ${p(x + r * 1.3)},${p(y)} ${p(x)},${p(y + r * 1.3)} ${p(x - r * 1.3)},${p(y)}"/>`;
  if (shape === 'triangle') return `<polygon points="${p(x)},${p(y - r * 1.25)} ${p(x + r * 1.2)},${p(y + r)} ${p(x - r * 1.2)},${p(y + r)}"/>`;
  if (shape === 'cross') return `<path d="M${p(x - r)} ${p(y - r)}L${p(x + r)} ${p(y + r)}M${p(x + r)} ${p(y - r)}L${p(x - r)} ${p(y + r)}" class="lens-cross"/>`;
  return `<circle cx="${p(x)}" cy="${p(y)}" r="${p(r)}"/>`;
}
const lensSeriesClass = i => i === 0 ? 'us' : `s${(i - 1) % 5 + 1}`;
const lensShape = i => i === 0 ? 'circle' : LENS_SHAPES[(i - 1) % LENS_SHAPES.length];

function lensLegendHtml(series) {
  return `<ul class="lens-legend" aria-label="Legend">${series.map((s, i) =>
    `<li class="lens-lg ${lensSeriesClass(i)}"><svg width="22" height="12" viewBox="0 0 22 12" aria-hidden="true"><line x1="0" x2="22" y1="6" y2="6"/><g>${lensMark(lensShape(i), 11, 6, i === 0 ? 3.6 : 2.8)}</g></svg><span>${esc(s.name)}</span></li>`).join('')}</ul>`;
}

// Words wrapped into at most `lines` lines of `max` characters; what does not fit ends in an ellipsis.
function lensWrap(text, max, lines) {
  const out = [];
  let line = '';
  for (let word of String(text || '').split(/\s+/).filter(Boolean)) {
    if (word.length > max) word = word.slice(0, Math.max(1, max - 1)) + '…';
    if (line && (line + ' ' + word).length > max) { out.push(line); line = word; }
    else line = line ? line + ' ' + word : word;
  }
  if (line) out.push(line);
  if (out.length > lines) { out.length = lines; out[lines - 1] = out[lines - 1].replace(/…?$/, '…'); }
  return out;
}
const lensCut = (text, max) => String(text).length > max ? String(text).slice(0, Math.max(1, max - 1)) + '…' : String(text);

// The value curve: one line per series across the factors on a 0 to 5 axis. Us is thick and in the accent; the others are
// thinner and each its own muted colour and marker. A name is written at the end of its line only where it clears the
// others (Us first); the legend names every line.
function lensCurveSvg(chart, width) {
  const factors = chart.factors.map(String), series = chart.series.filter(s => s && Array.isArray(s.scores));
  const narrow = width < 460, R = narrow ? 58 : 96, T = 10, plot = narrow ? 190 : 240, SLANT = 15;
  let L = 22, step = (width - L - R) / Math.max(1, factors.length);
  const flat = step >= 64, per = Math.max(4, Math.floor((step - 6) / 5.4));
  // Slanted names reach left of the first point: room for them, so the first is not cut off.
  if (!flat) { L = Math.max(L, Math.ceil(SLANT * 4.1 - step / 2)); step = (width - L - R) / Math.max(1, factors.length); }
  const wrapped = flat ? factors.map(f => lensWrap(f, per, 3)) : [];
  const B = flat ? 10 + Math.max(1, ...wrapped.map(w => w.length)) * 12 : 78;
  const H = T + plot + B, X = i => L + step * (i + 0.5), Y = v => T + plot * (1 - Math.min(5, Math.max(0, v)) / 5);
  const grid = [0, 1, 2, 3, 4, 5].map(v => `<line class="lens-grid" x1="${L}" x2="${width - R + 4}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>
    <text class="lens-t" x="${L - 6}" y="${(Y(v) + 3.5).toFixed(1)}" text-anchor="end">${v}</text>`).join('');
  const labels = factors.map((f, i) => flat
    ? `<text class="lens-t" text-anchor="middle">${wrapped[i].map((ln, k) => `<tspan x="${X(i).toFixed(1)}" y="${(T + plot + 16 + k * 12).toFixed(1)}">${esc(ln)}</tspan>`).join('')}<title>${esc(f)}</title></text>`
    : `<text class="lens-t" text-anchor="end" transform="translate(${(X(i) + 3).toFixed(1)} ${T + plot + 14}) rotate(-40)">${esc(lensCut(f, SLANT))}<title>${esc(f)}</title></text>`).join('');
  // Others first, Us last so it sits on top.
  const order = series.map((s, i) => i).reverse();
  const lines = order.map(i => {
    const s = series[i], cls = lensSeriesClass(i), pts = s.scores.map((v, k) => (typeof v === 'number' && k < factors.length ? [X(k), Y(v), v, k] : null));
    let d = '', pen = false;
    for (const p of pts) { if (!p) { pen = false; continue; } d += `${pen ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`; pen = true; }
    const marks = pts.filter(Boolean).map(p => `<g><title>${esc(s.name)}, ${esc(factors[p[3]])}: ${p[2]}</title>${lensMark(lensShape(i), p[0], p[1], i === 0 ? 4 : 3)}</g>`).join('');
    return `<g class="lens-series ${cls}" data-series="${esc(s.name)}"><path class="lens-line" d="${d}"/><g class="lens-marks">${marks}</g></g>`;
  }).join('');
  // Names at the line ends, kept 13px apart; Us always wins its place.
  const ends = series.map((s, i) => {
    const k = s.scores.map((v, n) => typeof v === 'number' && n < factors.length ? n : -1).filter(n => n >= 0).pop();
    return k === undefined ? null : {i, name: String(s.name), y: Y(s.scores[k]), x: X(k)};
  }).filter(Boolean);
  const kept = [];
  for (const e of ends.slice().sort((a, b) => (a.i === 0 ? -1 : b.i === 0 ? 1 : a.i - b.i))) if (kept.every(k => Math.abs(k.y - e.y) >= 13)) kept.push(e);
  const names = kept.map(e => `<text class="lens-name-t ${lensSeriesClass(e.i)}" x="${(e.x + 9).toFixed(1)}" y="${(e.y + 3.5).toFixed(1)}" data-end-label="${esc(e.name)}">${esc(lensCut(e.name, Math.floor((width - e.x - 12) / 5.6)))}<title>${esc(e.name)}</title></text>`).join('');
  return `<svg class="lens-curve" width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="Value curve: ${esc(series.map(s => s.name).join(', '))}">${grid}${labels}${lines}${names}</svg>`;
}

function lensDrawCharts(chart) {
  const host = document.querySelector('[data-lens-plot]');
  if (!host || !chart || chart.kind !== 'value_curve') return;
  let drawn = 0;
  const draw = () => {
    const width = Math.floor(host.clientWidth);
    if (width < 120 || Math.abs(width - drawn) < 8) return;
    drawn = width;
    host.innerHTML = lensCurveSvg(chart, width);
  };
  draw();
  if (typeof ResizeObserver === 'function' && LENS) { LENS.watch = new ResizeObserver(draw); LENS.watch.observe(host); }
}
