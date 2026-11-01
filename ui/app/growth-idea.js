/* ui/app/growth-idea.js — The Idea panel at the top of an Idea task (growth spec §4.4): hypothesis, prediction, bet,
   metric, channels and buyers; ICE (1–10 each, POST /api/v2/growth/ideas/{id}/score); the test (Start test, POST .../start)
   and the verdict (POST .../verdict); the idea it follows up and the ones it waits on. The subtasks stay the task's
   own tree. Drawn only when GET /api/v2/growth/ideas/{id} answers; the server decides who may change it, and the
   controls show to the owner and to whoever holds the task.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The panel element lives across redraws of the same task; it reads again when the task changes (its version) and
// on every opening (the modal forgets d.giEl when it closes).
function growthIdeaAdopt(d, task) {
  const slot = $('[data-growth-idea]', d);
  if (!slot) return;
  const id = String(task.id), ver = String(task.version ?? task.updated ?? '');
  if (pipelineTypeId(task) !== 'idea') { slot.hidden = true; return; }
  if (d.giEl && d.giEl.dataset.task === id) {
    d.giEl.giTask = task; slot.replaceWith(d.giEl);
    growthIdeaOneBody(d.giEl);
    if (d.giEl.dataset.ver !== ver) { d.giEl.dataset.ver = ver; void growthIdeaLoad(d.giEl, id); }
    return;
  }
  d.giEl = slot; slot.dataset.task = id; slot.dataset.ver = ver; slot.giTask = task;
  void growthIdeaLoad(slot, id);
}
async function growthIdeaLoad(slot, id) {
  let idea, refs;
  try { [idea, refs] = await Promise.all([get(`/v2/growth/ideas/${encodeURIComponent(id)}`, {fresh: true}).then(r => r.idea || r), grRefs(true)]); }
  catch (e) {
    if (slot.dataset.task !== id) return;
    if (e?.status === 404 || e?.status === 403) { slot.hidden = true; return; }
    slot.innerHTML = `<div class="gi-head"><span class="gr-k">Idea</span><span class="muted">${esc(e?.message || 'Did not load')}</span><span class="spacer"></span>
      <button class="ghost small" type="button" data-gi-retry>Retry</button></div>`;
    slot.hidden = false;
    slot.querySelector('[data-gi-retry]').onclick = () => void growthIdeaLoad(slot, id);
    return;
  }
  if (slot.dataset.task !== id) return;
  const links = [...new Set([idea.follow_up_of, ...(idea.depends_on || [])].filter(Boolean))];
  const titles = new Map(await Promise.all(links.map(t => get(`/v2/tasks/${encodeURIComponent(t)}`).then(r => [t, r.title || r.task?.title || t]).catch(() => [t, t]))));
  if (slot.dataset.task !== id) return;
  slot.giIdea = idea; slot.giRefs = refs; slot.giTitles = titles;
  growthIdeaDraw(slot);
}
// An idea's task body starts as its hypothesis (bots read it there); the panel already shows it, so the page shows it once.
function growthIdeaOneBody(slot) {
  const desc = slot.parentElement?.querySelector(':scope > .tdesc.md'), hyp = String(slot.giIdea?.hypothesis || '').trim();
  if (desc) desc.hidden = !slot.hidden && !!hyp && String(taskBody(slot.giTask || {})).trim() === hyp;
}
function growthIdeaDraw(slot) {
  const i = slot.giIdea, refs = slot.giRefs, titles = slot.giTitles, task = slot.giTask || {};
  // As the server allows: the assignee or an admin runs the test and gives the verdict; the creator also edits and scores.
  const run = grAdmin() || grSame(i.assignee ?? task.owner, grMe()), may = run || grSame(i.created_by, grMe());
  const bet = refs.bets.find(b => b.id === i.bet_id), kpi = refs.kpis.find(k => k.id === i.kpi_id), judged = refs.kpis.find(k => k.id === i.judged_kpi_id);
  const kpiName = kpi?.name || i.kpi_name, judgedName = judged?.name || i.judged_kpi_name;
  const dl = rows => rows.filter(r => r[1]).map(([k, v, raw]) => `<dt>${esc(k)}</dt><dd>${raw ? v : esc(v)}</dd>`).join('');
  const link = t => `<a href="${grTaskHref(t)}">${esc(titles.get(t) || t)}</a>`;
  const ice = may ? `<form class="gi-ice" data-gi-ice novalidate aria-label="ICE">${[['i', 'Impact', i.ice_i], ['c', 'Confidence', i.ice_c], ['e', 'Ease', i.ice_e]].map(([k, label, v]) =>
      `<label class="gi-icef"><span>${esc(label[0])}</span><input type="number" name="${k}" min="1" max="10" step="1" value="${esc(v ?? '')}" aria-label="${esc(label)}"></label>`).join('')}
      ${i.ice != null ? `<span class="gr-ice">= ${esc(grX(i.ice))}</span>` : ''}<button class="ghost small" type="submit">Score</button></form>`
    : i.ice != null ? `<span class="gr-ice">ICE ${esc([i.ice_i, i.ice_c, i.ice_e].map(x => x ?? '—').join(' · '))}</span>` : '';
  const testing = i.status === 'testing', done = GR_RESULTS.includes(i.status);
  const test = i.test_to || i.change ? `<section class="gi-sec"><h4 class="gr-k">Test</h4><dl class="br-dl">${dl([['Window', [grDay(i.test_from), grDay(i.test_to)].filter(Boolean).join(' – ')],
      ['Change', i.change], ['Stop if', i.stop_if], ['Min events', i.min_events != null ? String(i.min_events) : ''], ['Judged on', judgedName], ['Audience', i.audience]])}</dl></section>` : '';
  const verdict = done || i.result || i.learning ? `<section class="gi-sec"><h4 class="gr-k">Verdict</h4><dl class="br-dl">${dl([['Result', i.result], ['Learning', i.learning],
      ['Bet', i.bet_effect && i.bet_effect !== 'none' ? (i.bet_effect === 'supports' ? 'Supports' : 'Weakens') : ''], ['Confidence', i.confidence],
      ['By', [grWho(i.verdict_by), grDay(i.verdict_at)].filter(Boolean).join(' · ')]])}</dl></section>` : '';
  const actions = `${run && !testing && !done ? '<button class="primary small" type="button" data-gi-start>Start test</button>' : ''}
      ${run && (testing || i.status === 'paused') ? '<button class="primary small" type="button" data-gi-verdict>Verdict</button>' : ''}
      ${run && !done ? '<button class="ghost small" type="button" data-gi-drop>Drop</button>' : ''}${may ? '<button class="ghost small" type="button" data-gi-edit>Edit</button>' : ''}`;
  slot.innerHTML = `<div class="gi-head"><span class="gr-k">Idea</span>${grStatus(i.status)}${ice}<span class="spacer"></span>${actions}</div>
    <dl class="br-dl gi-dl">${dl([['Hypothesis', i.hypothesis], ['Prediction', i.prediction], ['Bet', bet ? `${bet.n} · ${bet.statement}` : i.bet_title],
      ['Metric', kpi ? `${kpi.name} ${grKpiVal(kpi)}` : kpiName || ''], ['Channels', (i.channels || []).map(s => `<span class="gr-tag">${esc(grChanName(refs.channels, s))}</span>`).join(''), true],
      ['Buyers', gsTags(refs.segments, i.segments), true],
      ['Follow-up of', i.follow_up_of ? link(i.follow_up_of) : '', true], ['Waits on', (i.depends_on || []).map(link).join(', '), true]])}</dl>${test}${verdict}`;
  slot.hidden = false;
  growthIdeaOneBody(slot);
  growthIdeaBind(slot);
}
function growthIdeaBind(slot) {
  const i = slot.giIdea, refs = slot.giRefs, base = `/v2/growth/ideas/${encodeURIComponent(i.task_id)}`;
  const after = () => void growthIdeaLoad(slot, slot.dataset.task);
  const ice = slot.querySelector('[data-gi-ice]');
  if (ice) ice.onsubmit = async ev => {
    ev.preventDefault();
    const v = k => Number(ice.elements[k].value);
    if (![v('i'), v('c'), v('e')].every(n => Number.isInteger(n) && n >= 1 && n <= 10)) { toast('Each score 1 to 10', true); return; }
    try { after(await post(base + '/score', {i: v('i'), c: v('c'), e: v('e')})); toast('Scored'); } catch (e) { toast(e.message || 'Not saved', true); }
  };
  const edit = slot.querySelector('[data-gi-edit]');
  if (edit) edit.onclick = () => grDialog('Edit idea', grIdeaFields(refs, i), 'Save', async form => {
    const body = grIdeaBody(form, i);
    if (!Object.keys(body).length) return;
    after(await patch(base, body)); toast('Saved');
  });
  const start = slot.querySelector('[data-gi-start]');
  if (start) start.onclick = () => {
    const today = new Date().toISOString().slice(0, 10), two = new Date(Date.now() + 14 * 864e5).toISOString().slice(0, 10);
    grDialog('Start test', `<div class="gr-pair">${grField('From', `<input type="date" name="test_from" value="${esc(i.test_from || today)}">`)}
      ${grField('To', `<input type="date" name="test_to" required value="${esc(i.test_to || two)}">`)}</div>
      ${grField('Change', `<textarea name="change" rows="2" required>${esc(i.change || '')}</textarea>`)}
      ${grField('Stop if', `<input name="stop_if" required value="${esc(i.stop_if || '')}">`)}
      <div class="gr-pair">${grField('Judged on', `<select name="judged_kpi_id">${grOpts(grKpiOpts(refs, i.judged_kpi_id || i.kpi_id, i.judged_kpi_id ? i.judged_kpi_name : i.kpi_name), i.judged_kpi_id || i.kpi_id, '—')}</select>`)}
      ${grField('Min events', `<input type="number" name="min_events" min="0" step="1" value="${esc(i.min_events ?? '')}">`)}</div>
      ${grField('Audience', `<input name="audience" maxlength="500" value="${esc(i.audience || '')}">`)}`, 'Start', async form => {
      const v = n => form.elements[n].value.trim();
      if (!v('test_to') || !v('change') || !v('stop_if')) throw new Error('An end date, the change and when to stop');
      after(await post(base + '/start', {test_from: v('test_from') || null, test_to: v('test_to'), change: v('change'), stop_if: v('stop_if'),
        judged_kpi_id: v('judged_kpi_id') || null, min_events: v('min_events') === '' ? null : Number(v('min_events')), audience: v('audience') || null}));
      toast('Test started');
    });
  };
  const verdict = (status = '') => grDialog(status === 'dropped' ? 'Drop idea' : 'Verdict', `${grField('Verdict', `<select name="status">${grOpts([['won', 'Won'], ['lost', 'Lost'],
      ['inconclusive', 'Inconclusive'], ['paused', 'Paused'], ['dropped', 'Dropped']], status || 'won', null)}</select>`)}
    ${grField('Result', `<textarea name="result" rows="2" maxlength="2000">${esc(i.result || '')}</textarea>`)}
    ${grField('Learning', `<textarea name="learning" rows="2" maxlength="2000">${esc(i.learning || '')}</textarea>`)}
    <div class="gr-pair">${grField('Bet', `<select name="bet_effect">${grOpts([['supports', 'Supports'], ['weakens', 'Weakens'], ['none', 'No effect']], i.bet_effect, '—')}</select>`)}
    ${grField('Confidence', `<select name="confidence">${grOpts([['low', 'Low'], ['medium', 'Medium'], ['high', 'High']], i.confidence, '—')}</select>`)}</div>`, 'Save', async form => {
    const v = n => form.elements[n].value.trim();
    after(await post(base + '/verdict', {status: v('status'), result: v('result') || null, learning: v('learning') || null, bet_effect: v('bet_effect') || null, confidence: v('confidence') || null}));
    toast('Saved');
  });
  const vb = slot.querySelector('[data-gi-verdict]');
  if (vb) vb.onclick = () => verdict();
  const drop = slot.querySelector('[data-gi-drop]');
  if (drop) drop.onclick = () => verdict('dropped');
}
