/* Goals page: the tree (a line per owner), the owner's panel a tap opens, a goal's KPI lines, the KPI panel,
   adding a KPI and Needs you.
   Everything shown is what the server computed (GET /api/v2/goals/tree, /kpis/{id}, /goals/needs-you,
   /bots/{bot}/kpis): a colour is never derived here, and a KPI with no fresh data says so instead of a number.
   pageGoals and the goal editor (goalFormHtml, bindGoalForm) are in ui/app/goals.js. */

const KPI_LABEL = {green: 'On track', yellow: 'At risk', red: 'Off track', gray: 'No fresh data', none: 'No target'};
const KPI_QUALITY = {estimate: 'estimate', partial: 'partial'};
const GOAL_MANAGER_ACTOR = 'bot:goal-manager';

function goalActorName(actor) {
  if (!actor) return '';
  if (actor === 'keeper') return S.config?.app_name || 'Tico';
  const info = goalOwnerInfo(actor);
  return actor === GOAL_MANAGER_ACTOR && info.name === 'goal-manager' ? 'Goal Manager' : info.name;
}

// A status dot: green, yellow, red, gray (stale, missing or no data) or a ring (fresh, no target to judge by).
function gdot(status, title = '') {
  const s = ['green', 'yellow', 'red', 'none'].includes(status) ? status : 'gray';
  return `<span class="gdot ${s}" role="img" aria-label="${esc(KPI_LABEL[s])}"${title ? ` title="${esc(title)}"` : ''}></span>`;
}

function kpiNum(value, unit = '') {
  if (value == null || value === '') return '';
  const text = Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  unit = String(unit || '').trim();
  if (!unit) return text;
  if (unit === '%' || unit === 'pp') return text + unit;
  if (['$', '€', '£'].includes(unit)) return unit + text;
  // "1 accounts" reads wrong: a word unit drops its plural s for exactly one.
  if (Number(value) === 1 && /^[A-Za-z]+s$/.test(unit) && !/ss$/.test(unit)) unit = unit.slice(0, -1);
  return `${text} ${unit}`;
}
// Periods are business days: shown in UTC so a week that ends on the 20th says the 20th everywhere.
function kpiDay(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  const other = date.getUTCFullYear() !== new Date().getUTCFullYear();
  return date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC', ...(other ? {year: 'numeric'} : {})});
}
const kpiFresh = k => k.latest && (k.freshness || 'fresh') === 'fresh';

// A tiny line of the last readings, oldest first; the last point takes the status colour.
function kpiSpark(values, status, w = 64, h = 18) {
  const v = (values || []).map(Number).filter(Number.isFinite);
  const s = ['green', 'yellow', 'red'].includes(status) ? status : 'gray';
  if (v.length < 2) return `<svg class="kspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${v.length ? `<circle class="kspark-end ${s}" cx="${w - 3}" cy="${h / 2}" r="2.2"/>` : ''}</svg>`;
  const lo = Math.min(...v), span = Math.max(...v) - lo || 1, pad = 3;
  const pts = v.map((y, i) => [pad + i * (w - 2 * pad) / (v.length - 1), h - pad - (y - lo) / span * (h - 2 * pad)]);
  const last = pts.at(-1);
  return `<svg class="kspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ')}"/><circle class="kspark-end ${s}" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.2"/></svg>`;
}

// One KPI on one line: dot, name, latest value and its period, the target on the goal, a sparkline.
function kpiLine(k, {owner = false, goal = ''} = {}) {
  const value = kpiFresh(k)
    ? `<span class="kpi-val tnum">${esc(kpiNum(k.latest.value, k.unit))}${KPI_QUALITY[k.latest.quality] ? ` <span class="kpi-q">${KPI_QUALITY[k.latest.quality]}</span>` : ''}<span class="muted"> · ${esc(kpiDay(k.latest.period_end))}</span></span>`
    : `<span class="kpi-val kpi-off">${k.freshness === 'stale' ? 'stale' : 'no data'}</span>`;
  return `<li class="kpi-line" data-kpi="${esc(k.id)}"${goal ? ` data-kpi-goal="${esc(goal)}"` : ''} tabindex="0" role="button" title="${esc(k.reason || '')}">
    ${gdot(k.status, k.reason)}<span class="kpi-name">${esc(k.name)}</span>${owner ? `<span class="kpi-owner">${esc(goalActorName(k.owner))}</span>` : ''}
    ${value}${k.target_label ? `<span class="kpi-target">${esc(k.target_label)}</span>` : ''}${kpiSpark(k.spark, k.status)}</li>`;
}
function kpiLinesHtml(kpis, opts) {
  return (kpis || []).length ? `<ul class="kpi-lines">${kpis.map(k => kpiLine(k, opts)).join('')}</ul>` : '';
}
function bindKpiLines(root) {
  for (const li of root.querySelectorAll('[data-kpi]')) {
    if (li.tagName !== 'LI') continue;
    li.onclick = () => kpiOpen(li.dataset.kpi);
    li.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); kpiOpen(li.dataset.kpi); } };
  }
}

// ---------------------------------------------------------------- the KPI panel
function kpiChart(rows, links, kpi) {
  const pts = rows.filter(r => !r.superseded_by && r.quality !== 'partial').map(r => ({t: Date.parse(r.period_end), v: Number(r.value), q: r.quality}));
  if (!pts.length) return '';
  const link = links.find(l => l.kind === 'improve' || l.kind === 'maintain');
  const W = 480, H = 150, L = 34, R = 8, T = 10, B = 20;
  let t0 = Math.min(...pts.map(p => p.t)), t1 = Math.max(...pts.map(p => p.t));
  let pace = null;
  if (link?.kind === 'improve' && link.target != null && link.deadline) {
    const start = link.baseline != null ? Date.parse(link.baseline_at || pts[0].t) : pts[0].t, base = link.baseline != null ? link.baseline : pts[0].v;
    const end = Date.parse(link.deadline + 'T23:59:59Z');
    if (Number.isFinite(start) && end > start) {
      t0 = Math.min(t0, start);
      t1 = Math.max(t1, Math.min(end, Math.max(Date.now(), t1)));
      const at = t => base + (link.target - base) * Math.min(1, Math.max(0, (t - start) / (end - start)));
      pace = [[t0, at(t0)], [t1, at(t1)]];
    }
  }
  if (t1 <= t0) t1 = t0 + 86400000;
  const ys = [...pts.map(p => p.v), ...(pace ? pace.map(p => p[1]) : []), ...(link?.kind === 'maintain' ? [link.min, link.max].filter(v => v != null) : [])];
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const room = (hi - lo || Math.abs(hi) || 1) * 0.08; lo -= room; hi += room;
  const x = t => L + (t - t0) / (t1 - t0) * (W - L - R), y = v => H - B - (v - lo) / (hi - lo) * (H - B - T);
  const top = link?.kind === 'maintain' ? y(link.max != null ? link.max : hi) : 0, floor = link?.kind === 'maintain' ? y(link.min != null ? link.min : lo) : 0;
  const band = link?.kind === 'maintain' ? `<rect class="kc-band" x="${L}" width="${W - L - R}" y="${top.toFixed(1)}" height="${Math.max(1, floor - top).toFixed(1)}"/>` : '';
  const line = pts.map(p => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const last = pts.at(-1);
  return `<svg class="kchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(kpi.name)} readings">
    <line class="kc-axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${band}
    ${pace ? `<line class="kc-pace" x1="${x(pace[0][0]).toFixed(1)}" y1="${y(pace[0][1]).toFixed(1)}" x2="${x(pace[1][0]).toFixed(1)}" y2="${y(pace[1][1]).toFixed(1)}"/>` : ''}
    <polyline class="kc-line" points="${line}"/>${pts.map(p => `<circle class="kc-dot" cx="${x(p.t).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="2.4"/>`).join('')}
    <circle class="kc-last ${esc(kpi.status || 'gray')}" cx="${x(last.t).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="4"/>
    <text class="kc-t" x="${L - 4}" y="${T + 3}" text-anchor="end">${esc(kpiNum(hi, ''))}</text><text class="kc-t" x="${L - 4}" y="${H - B}" text-anchor="end">${esc(kpiNum(lo, ''))}</text>
    <text class="kc-t" x="${L}" y="${H - 5}">${esc(kpiDay(new Date(t0).toISOString()))}</text><text class="kc-t" x="${W - R}" y="${H - 5}" text-anchor="end">${esc(kpiDay(new Date(t1).toISOString()))}</text></svg>`;
}

function kpiEvidence(text) {
  const t = String(text || '').trim();
  if (!t) return '';
  return /^https?:\/\/\S+$/.test(t) ? `<a href="${esc(t)}" target="_blank" rel="noopener noreferrer">evidence</a>` : `<span class="kpi-note" title="${esc(t)}">${esc(t)}</span>`;
}
// The target fields of a goal's link to a KPI: which of them show follows the kind.
function kpiTargetFields(link) {
  const n = v => v == null ? '' : v;
  return `<label class="goal-f"><span>Target</span><select name="kind"><option value="improve"${link.kind === 'improve' ? ' selected' : ''}>Improve</option><option value="maintain"${link.kind === 'maintain' ? ' selected' : ''}>Stay in range</option><option value="none"${link.kind === 'none' ? ' selected' : ''}>None</option></select></label>
    <div class="goal-fs" data-k="improve"><label class="goal-f"><span>Baseline</span><input name="baseline" type="number" step="any" value="${esc(n(link.baseline))}"></label>
      <label class="goal-f"><span>Target</span><input name="target" type="number" step="any" value="${esc(n(link.target))}"></label></div>
    <div class="goal-fs" data-k="improve"><label class="goal-f"><span>Deadline</span><input name="deadline" type="date" value="${esc(link.deadline || '')}"></label></div>
    <div class="goal-fs" data-k="maintain"><label class="goal-f"><span>Min</span><input name="min" type="number" step="any" value="${esc(n(link.min))}"></label>
      <label class="goal-f"><span>Max</span><input name="max" type="number" step="any" value="${esc(n(link.max))}"></label></div>`;
}
function kpiTargetForm(link) {
  return `<form class="goal-form kpi-target-form" data-kpi-target="${esc(link.goal_id)}" novalidate>${kpiTargetFields(link)}
    <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-cancel>Cancel</button></div></form>`;
}
function kpiTargetBody(form) {
  const kind = form.elements.kind.value, num = name => form.elements[name].value === '' ? null : Number(form.elements[name].value);
  if (kind === 'improve') return {kind, baseline: num('baseline'), target: num('target'), deadline: form.elements.deadline.value || null};
  if (kind === 'maintain') return {kind, min: num('min'), max: num('max')};
  return {kind: 'none'};
}
function kpiTargetKinds(form) {
  const sync = () => { for (const el of form.querySelectorAll('[data-k]')) el.hidden = el.dataset.k !== form.elements.kind.value; };
  form.elements.kind.onchange = sync; sync();
}

function kpiProposalLine(p) {
  const what = {goal_wording: 'New wording', goal_kpi: 'Add a KPI', kpi_definition: 'New definition', kpi_target: 'New target', flag: 'Flag'}[p.kind] || p.kind;
  const pl = p.payload || {};
  const detail = p.kind === 'goal_wording' ? (pl.title || pl.body || '')
    : p.kind === 'kpi_definition' ? (pl.definition || pl.name || Object.keys(pl).join(', '))
    : p.kind === 'kpi_target' ? (pl.kind === 'maintain' ? `range ${pl.min ?? ''}–${pl.max ?? ''}` : pl.kind === 'improve' ? `→ ${pl.target} by ${kpiDay(pl.deadline + 'T00:00:00Z')}` : 'no target')
    : p.kind === 'goal_kpi' ? (pl.kpi?.name || 'existing KPI')
    : `${pl.issue || ''}${pl.note ? ': ' + pl.note : ''}`;
  return {what, detail, reason: p.reason || ''};
}
function proposalHtml(p, extra = '') {
  const x = kpiProposalLine(p);
  return `<li class="kpi-prop" data-proposal="${esc(p.id)}"><div class="kpi-prop-main"><span><b>${esc(x.what)}</b>${extra ? ` <span class="muted">· ${esc(extra)}</span>` : ''}</span><span class="kpi-prop-detail">${esc(x.detail)}</span>
    ${x.reason ? `<span class="muted kpi-prop-why">${esc(x.reason)} · ${esc(goalActorName(p.proposed_by))}</span>` : `<span class="muted kpi-prop-why">${esc(goalActorName(p.proposed_by))}</span>`}</div>
    ${p.may_decide ? `<div class="kpi-prop-do"><button class="primary" type="button" data-decide="confirm">Confirm</button><button class="ghost" type="button" data-decide="reject">Reject</button></div>` : ''}</li>`;
}
function bindProposals(root, after) {
  for (const li of root.querySelectorAll('[data-proposal]')) for (const b of li.querySelectorAll('[data-decide]')) b.onclick = async () => {
    b.disabled = true;
    try { await post(`/v2/proposals/${encodeURIComponent(li.dataset.proposal)}/decide`, {decision: b.dataset.decide}); await after(); }
    catch (e) { toast(e.message || 'Could not decide', true); b.disabled = false; }
  };
}

function kpiOwnerChoices(current) {
  const me = mePerson(), mine = me ? 'human:' + me.id : '';
  const all = S.me?.role === 'owner' ? [[GOAL_COMPANY, goalOwnerInfo(GOAL_COMPANY).name], ...goalOwnerChoices(mine)] : [[mine, 'Me']];
  const out = current && !all.some(([v]) => v === current) ? [[current, goalActorName(current)], ...all] : all;
  return out.filter(([v]) => v);
}
const ownerOptions = (choices, selected) => choices.map(([v, name]) => `<option value="${esc(v)}"${v === selected ? ' selected' : ''}>${esc(name)}</option>`).join('');

function kpiReadingRow(r, d) {
  const k = d.kpi;
  return `<li class="kpi-r${r.superseded_by ? ' old' : ''}" data-reading="${esc(r.id)}"><span class="tnum kpi-rv">${esc(kpiNum(r.value, k.unit))}</span><span class="muted">${esc(kpiDay(r.period_end))}</span>
    ${KPI_QUALITY[r.quality] ? `<span class="pill">${KPI_QUALITY[r.quality]}</span>` : ''}${kpiEvidence(r.evidence)}${r.note ? `<span class="kpi-note muted" title="${esc(r.note)}">${esc(r.note)}</span>` : ''}
    <span class="muted kpi-by">${esc(goalActorName(r.actor))}</span>${d.may_log && !r.superseded_by && !k.auto ? `<button class="linkish" type="button" data-correct="${esc(r.id)}" data-correct-text="${esc(kpiNum(r.value, k.unit))} ${esc(kpiDay(r.period_end))}">Correct</button>` : ''}</li>`;
}

function kpiPanelHtml(d, {back = false} = {}) {
  const k = d.kpi, links = d.links || [], rows = d.readings || [];
  const state = links.find(l => l.kind !== 'none') || links[0] || {};
  const status = state.status || k.status;
  const now = kpiFresh(k)
    ? `<span class="kpi-big tnum">${esc(kpiNum(k.latest.value, k.unit))}</span><span class="muted"> ${esc(kpiDay(k.latest.period_end))}</span>`
    : `<span class="kpi-big kpi-off">${k.freshness === 'stale' ? 'stale' : 'no data'}</span>`;
  const check = (d.checkins || [])[0];
  const list = rows.slice().reverse();
  const shown = list.slice(0, 30);
  const editableGoals = links.filter(l => GOALS_ST && goalMayEdit({id: l.goal_id, owner: l.goal_owner, parent_id: GOALS_ST.goals.find(g => g.id === l.goal_id)?.parent_id}, GOALS_ST.goals));
  return `<div class="tmodal-head">${back ? '<button class="ghost tmodal-x gp-back" type="button" data-kpi-back aria-label="Back">‹</button>' : ''}<span class="kpi-ptitle">${gdot(status, state.reason || k.reason)}<span class="who">${esc(k.name)}</span>${k.auto ? '' : `<span class="pill" title="Definition version">v${esc(k.definition_version)}</span>`}</span><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-kpi-close aria-label="Close">✕</button></div>
    <div class="tmodal-body kpi-body" data-kpi-body="${esc(k.id)}">
      ${k.archived_at ? `<p class="kpi-archived" role="status">Archived ${esc(kpiDay(k.archived_at))}. Readings and definition history are retained.</p>` : ''}
      <div class="kpi-now">${now}${k.reason && !kpiFresh(k) ? `<div class="muted kpi-why">${esc(k.reason)}</div>` : state.reason ? `<div class="muted kpi-why">${esc(state.reason)}</div>` : ''}</div>
      ${kpiChart(rows, links, k)}
      <dl class="kpi-meta">
        ${k.definition ? `<dt>Definition</dt><dd>${esc(k.definition)}</dd>` : ''}
        <dt>Owner</dt><dd>${esc(goalActorName(k.owner))}</dd>
        <dt>Measured</dt><dd>${esc(k.cadence)}, ${esc(k.direction === 'range' ? 'in a range' : k.direction === 'down' ? 'lower is better' : 'higher is better')}${k.unit ? ', ' + esc(k.unit) : ''}</dd>
        ${k.source_note ? `<dt>Source</dt><dd>${esc(k.source_note)}</dd>` : ''}
      </dl>
      ${links.length ? `<h3 class="kpi-h">Used by</h3><ul class="kpi-used">${links.map(l => `<li data-used="${esc(l.goal_id)}">${gdot(l.status, l.reason)}<button class="linkish kpi-goal" type="button" data-kpi-goal-open="${esc(l.goal_id)}">${esc(l.goal_title)}</button><span class="kpi-target">${esc(l.target_label || '')}</span>
        ${editableGoals.some(g => g.goal_id === l.goal_id) ? `<button class="linkish" type="button" data-kpi-target-edit="${esc(l.goal_id)}">Target</button><button class="linkish danger" type="button" data-kpi-unlink="${esc(l.goal_id)}">Unlink</button>` : ''}
        <div class="kpi-target-host" data-target-host="${esc(l.goal_id)}"></div></li>`).join('')}</ul>` : ''}
      ${check ? `<h3 class="kpi-h">Check-in</h3><blockquote class="kpi-check">${esc(check.body)}<footer class="muted">${esc(goalActorName(check.source_actor))}${check.signal ? ' · ' + esc(check.signal.replace('_', ' ')) : ''} · ${esc(ago(check.ts))}</footer></blockquote>` : ''}
      ${(d.proposals || []).length ? `<h3 class="kpi-h">Proposed</h3><ul class="kpi-props">${d.proposals.map(p => proposalHtml(p)).join('')}</ul>` : ''}
      <h3 class="kpi-h">Readings</h3>
      ${shown.length ? `<ul class="kpi-readings">${shown.map(r => kpiReadingRow(r, d)).join('')}</ul>
        ${list.length > shown.length ? `<button class="linkish" type="button" data-kpi-all>All ${list.length}</button>` : ''}` : '<p class="muted">No readings.</p>'}
      ${d.may_log ? `<form class="goal-form kpi-log" data-kpi-log novalidate>
        <div class="kpi-correcting muted" hidden></div>
        <div class="goal-fs"><label class="goal-f"><span>Value</span><input name="value" type="number" step="any" inputmode="decimal"></label>
          <label class="goal-f"><span>Period end</span><input name="period_end" type="date"></label></div>
        <div class="goal-fs"><label class="goal-f"><span>Evidence</span><input name="evidence" maxlength="2000" placeholder="Link or note"></label>
          <label class="goal-f"><span>Quality</span><select name="quality"><option value="measured">Measured</option><option value="estimate">Estimate</option><option value="partial">Partial</option></select></label></div>
        <label class="goal-f kpi-note-f" hidden><span>What changed</span><input name="note" maxlength="2000"></label>
        <div class="goal-actions"><button class="primary" type="submit">Log reading</button><button class="ghost" type="button" data-correct-cancel hidden>Cancel</button></div></form>` : ''}
      ${d.may_edit && !k.auto ? `<div class="kpi-edit-host"><button class="linkish" type="button" data-kpi-edit>Edit</button>
        <button class="linkish${k.archived_at ? '' : ' danger'}" type="button" data-kpi-archive>${k.archived_at ? 'Restore KPI' : 'Archive KPI'}</button></div>` : ''}
    </div>`;
}

function kpiEditForm(k) {
  const opt = (list, v) => list.map(([val, label]) => `<option value="${val}"${val === v ? ' selected' : ''}>${label}</option>`).join('');
  return `<form class="goal-form kpi-edit" novalidate>
    <label class="goal-f"><span>Name</span><input name="name" maxlength="300" value="${esc(k.name)}"></label>
    <label class="goal-f"><span>Definition</span><textarea name="definition" rows="2" maxlength="2000">${esc(k.definition || '')}</textarea></label>
    <div class="goal-fs"><label class="goal-f"><span>Unit</span><input name="unit" maxlength="40" value="${esc(k.unit || '')}"></label>
      <label class="goal-f"><span>Direction</span><select name="direction">${opt([['up', 'Up'], ['down', 'Down'], ['range', 'Range']], k.direction)}</select></label></div>
    <div class="goal-fs"><label class="goal-f"><span>Cadence</span><select name="cadence">${opt([['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly']], k.cadence)}</select></label>
      <label class="goal-f"><span>Owner</span><select name="owner">${ownerOptions(kpiOwnerChoices(k.owner), k.owner)}</select></label></div>
    <label class="goal-f"><span>Source</span><input name="source_note" maxlength="2000" value="${esc(k.source_note || '')}"></label>
    <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-cancel>Cancel</button></div></form>`;
}

function kpiPanelBind(dlg, d) {
  const k = d.kpi, body = dlg.querySelector('.kpi-body');
  const again = async () => { await kpiOpen(k.id); if (typeof goalsRefresh === 'function') { await goalsRefresh(); if (GOALS_ST && $('#goal-body')) goalsRender(GOALS_ST); } };
  dlg.querySelector('[data-kpi-close]').onclick = () => dlg.close();
  bindProposals(body, again);
  const inPanel = dlg.id === 'goal-panel' && GOALS_ST;
  for (const b of body.querySelectorAll('[data-kpi-goal-open]')) b.onclick = () => {
    const g = inPanel && GOALS_ST.goals.find(x => x.id === b.dataset.kpiGoalOpen);
    if (g) goalPanelOpen(GOALS_ST, g.owner, {goal: g.id});
    else { dlg.close(); openGoal(b.dataset.kpiGoalOpen); }
  };
  const back = dlg.querySelector('[data-kpi-back]');
  if (back) back.onclick = () => { if (GOALS_ST?.panel) { GOALS_ST.panel.kpi = ''; delete dlg.dataset.kpi; goalPanelRender(GOALS_ST); } };
  const all = body.querySelector('[data-kpi-all]');
  const correctable = () => { for (const b of body.querySelectorAll('[data-correct]')) b.onclick = () => body._mode?.(b.dataset.correct, b.dataset.correctText); };
  if (all) all.onclick = () => { body.querySelector('.kpi-readings').innerHTML = d.readings.slice().reverse().map(r => kpiReadingRow(r, d)).join(''); all.remove(); correctable(); };
  for (const b of body.querySelectorAll('[data-kpi-unlink]')) b.onclick = async () => {
    b.disabled = true;
    try { await post(`/v2/goals/${encodeURIComponent(b.dataset.kpiUnlink)}/kpis/${encodeURIComponent(k.id)}/unlink`, {}); await again(); }
    catch (e) { toast(e.message || 'Could not unlink', true); b.disabled = false; }
  };
  for (const b of body.querySelectorAll('[data-kpi-target-edit]')) b.onclick = () => {
    const host = body.querySelector(`[data-target-host="${CSS.escape(b.dataset.kpiTargetEdit)}"]`);
    if (host.firstChild) { host.innerHTML = ''; return; }
    const link = d.links.find(l => l.goal_id === b.dataset.kpiTargetEdit);
    host.innerHTML = kpiTargetForm(link);
    const form = host.querySelector('form');
    kpiTargetKinds(form);
    form.querySelector('[data-cancel]').onclick = () => { host.innerHTML = ''; };
    form.onsubmit = async ev => {
      ev.preventDefault();
      try { await post(`/v2/goals/${encodeURIComponent(link.goal_id)}/kpis/${encodeURIComponent(k.id)}`, kpiTargetBody(form)); await again(); }
      catch (e) { toast(e.message || 'Could not save the target', true); }
    };
  };
  const archive = body.querySelector('[data-kpi-archive]');
  if (archive) archive.onclick = async () => {
    const restoring = Boolean(k.archived_at);
    if (!restoring && !window.confirm(`Archive “${k.name}”? Its readings and definition history will stay available.`)) return;
    archive.disabled = true;
    try {
      await post(`/v2/kpis/${encodeURIComponent(k.id)}/${restoring ? 'restore' : 'archive'}`, {});
      if (GOALS_ST?.panel) {
        GOALS_ST.panel.history = false;
        GOALS_ST.panel.historyKpis = null;
      }
      await again();
    } catch (e) { toast(e.message || `Could not ${restoring ? 'restore' : 'archive'} the KPI`, true); archive.disabled = false; }
  };
  const log = body.querySelector('[data-kpi-log]');
  if (log) {
    let correcting = '';
    const mode = (id, text) => {
      correcting = id;
      log.querySelector('.kpi-correcting').hidden = !id; log.querySelector('.kpi-correcting').textContent = id ? `Correcting ${text}` : '';
      log.querySelector('.kpi-note-f').hidden = !id; log.querySelector('[data-correct-cancel]').hidden = !id;
      log.querySelector('[type=submit]').textContent = id ? 'Log correction' : 'Log reading';
      if (id) log.elements.value.focus();
    };
    body._mode = mode; correctable();
    log.querySelector('[data-correct-cancel]').onclick = () => mode('', '');
    log.onsubmit = async ev => {
      ev.preventDefault();
      if (log.elements.value.value === '') { log.elements.value.focus(); return; }
      const payload = {value: Number(log.elements.value.value), quality: log.elements.quality.value};
      if (log.elements.period_end.value) payload.period_end = log.elements.period_end.value;
      if (log.elements.evidence.value.trim()) payload.evidence = log.elements.evidence.value.trim();
      if (correcting) { payload.supersedes = correcting; payload.note = log.elements.note.value.trim(); }
      const submit = log.querySelector('[type=submit]'); submit.disabled = true;
      try { await post(`/v2/kpis/${encodeURIComponent(k.id)}/readings`, payload); await again(); }
      catch (e) { toast(e.message || 'Could not log the reading', true); submit.disabled = false; }
    };
  }
  const edit = body.querySelector('[data-kpi-edit]');
  if (edit) edit.onclick = () => {
    const host = edit.parentElement;
    host.innerHTML = kpiEditForm(k);
    const form = host.querySelector('form');
    form.querySelector('[data-cancel]').onclick = () => again();
    form.onsubmit = async ev => {
      ev.preventDefault();
      const payload = {};
      for (const name of ['name', 'definition', 'unit', 'direction', 'cadence', 'source_note', 'owner']) {
        const value = form.elements[name].value.trim();
        if (value !== String(k[name] ?? '') && !(name === 'name' && !value)) payload[name] = value;
      }
      if (!Object.keys(payload).length) { await again(); return; }
      try { await post(`/v2/kpis/${encodeURIComponent(k.id)}`, payload); await again(); }
      catch (e) { toast(e.message || 'Could not save the KPI', true); }
    };
    form.elements.name.focus();
  };
}

// On the Goals page a KPI opens inside the owner's panel, with a way back; anywhere else in a dialog of its own.
async function kpiOpen(id) {
  const inPage = !!(GOALS_ST && document.getElementById('goal-body'));
  let dlg = inPage ? goalPanelDialog(GOALS_ST) : document.getElementById('kpi-panel');
  if (inPage) (GOALS_ST.panel ||= {owner: '', goal: '', kpiAdd: ''}).kpi = id;
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'kpi-panel'; dlg.className = 'tmodal kpi-panel'; dlg.setAttribute('aria-label', 'KPI');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
  }
  dlg.dataset.kpi = id;
  if (!dlg.open) { dlg.innerHTML = '<div class="tmodal-body"><p class="muted">Loading…</p></div>'; dlg.showModal(); }
  const data = await v2Get('/v2/kpis/' + encodeURIComponent(id));
  if (dlg.dataset.kpi !== id || !dlg.open) return;
  if (!data) { dlg.innerHTML = '<div class="tmodal-body"><p class="err">Could not load the KPI.</p><div class="goal-actions"><button class="ghost" type="button" data-kpi-close>Close</button></div></div>'; dlg.querySelector('[data-kpi-close]').onclick = () => dlg.close(); return; }
  if (inPage && !GOALS_ST.panel.owner) GOALS_ST.panel.owner = data.kpi.owner;
  dlg.innerHTML = kpiPanelHtml(data, {back: inPage});
  kpiPanelBind(dlg, data);
}

// ---------------------------------------------------------------- adding a KPI
// On a goal: pick a KPI that exists (or a bot's automatic ones) or make one, with the target on the link.
// On its own: a KPI no goal uses yet, owned by whoever's panel it is.
function kpiAddHtml(goal, forOwner = '') {
  const owner = goal ? goal.owner : forOwner || (mePerson() ? 'human:' + mePerson().id : '');
  const choices = kpiOwnerChoices(owner);
  const target = goal ? `<div data-target>${kpiTargetFields({goal_id: goal.id, kind: 'improve'})}</div>` : '';
  return `<form class="goal-form kpi-add" data-kpi-add="${esc(goal ? goal.id : '@page')}" novalidate>
    ${goal ? `<label class="goal-f"><span>KPI</span><select name="pick"><option value="">New KPI</option></select></label>` : ''}
    <div data-new>
      <label class="goal-f"><span>Name</span><input name="name" maxlength="300" autocomplete="off" placeholder="What is counted, per what"></label>
      <label class="goal-f"><span>Definition</span><input name="definition" maxlength="2000" autocomplete="off"></label>
      <div class="goal-fs"><label class="goal-f"><span>Unit</span><input name="unit" maxlength="40" autocomplete="off"></label>
        <label class="goal-f"><span>Direction</span><select name="direction"><option value="up">Up</option><option value="down">Down</option><option value="range">Range</option></select></label></div>
      <div class="goal-fs"><label class="goal-f"><span>Cadence</span><select name="cadence"><option value="daily">Daily</option><option value="weekly" selected>Weekly</option><option value="monthly">Monthly</option></select></label>
        <label class="goal-f"><span>Owner</span><select name="owner">${ownerOptions(choices, owner)}</select></label></div>
      <label class="goal-f"><span>Source</span><input name="source_note" maxlength="2000" autocomplete="off"></label>
    </div>${target}
    <div class="goal-actions"><button class="primary" type="submit">Add KPI</button><button class="ghost" type="button" data-cancel>Cancel</button></div></form>`;
}
async function kpiAddBind(form, goal, done) {
  const pick = form.elements.pick, fresh = form.querySelector('[data-new]');
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(false); } };
  form.querySelector('[data-cancel]').onclick = () => done(false);
  if (form.elements.kind) kpiTargetKinds(form);
  if (pick) {
    pick.onchange = () => { fresh.hidden = !!pick.value; };
    const [all, auto] = await Promise.all([v2Get('/v2/kpis'), String(goal.owner).startsWith('bot:') ? v2Get(`/v2/kpis?auto_for=${encodeURIComponent(goal.owner.slice(4))}`) : null]);
    if (!form.isConnected) return;
    const linked = new Set((goal.kpis || []).map(k => k.id));
    const opts = list => list.filter(k => !linked.has(k.id)).map(k => `<option value="${esc(k.id)}">${esc(k.name)}${k.auto ? '' : ' · ' + esc(goalActorName(k.owner))}</option>`).join('');
    const a = opts(auto?.kpis || []), b = opts(all?.kpis || []);
    pick.insertAdjacentHTML('beforeend', (a ? `<optgroup label="Bot">${a}</optgroup>` : '') + (b ? `<optgroup label="KPIs">${b}</optgroup>` : ''));
  }
  form.onsubmit = async ev => {
    ev.preventDefault();
    const submit = form.querySelector('[type=submit]');
    const text = name => form.elements[name].value.trim();
    let body, path;
    if (goal) {
      body = form.elements.kind ? kpiTargetBody(form) : {kind: 'none'};
      path = `/v2/goals/${encodeURIComponent(goal.id)}/kpis`;
      if (pick.value) body.kpi_id = pick.value;
      else {
        if (!text('name')) { form.elements.name.focus(); return; }
        Object.assign(body, {name: text('name'), definition: text('definition'), unit: text('unit'), direction: form.elements.direction.value,
          cadence: form.elements.cadence.value, source_note: text('source_note')});
        if (form.elements.owner.value !== goal.owner) body.owner = form.elements.owner.value;
      }
    } else {
      if (!text('name')) { form.elements.name.focus(); return; }
      path = '/v2/kpis';
      body = {name: text('name'), definition: text('definition'), unit: text('unit'), direction: form.elements.direction.value,
        cadence: form.elements.cadence.value, source_note: text('source_note'), owner: form.elements.owner.value};
    }
    submit.disabled = true;
    try { await post(path, body); done(true); }
    catch (e) { toast(e.message || 'Could not add the KPI', true); submit.disabled = false; }
  };
  (pick || form.elements.name).focus();
}

// ---------------------------------------------------------------- the page: one tree
// The team, then every human and every bot that is not archived, nested the way the team chart nests them,
// whether or not they have a goal. One line each: the owner, their first goal (cut short, the whole of it in the
// tooltip) and its KPIs as small chips; more goals follow on lines of their own. Message bots
// sit in their own group below; built-ins have their own entry points. Tapping any line opens that owner's panel.
function goalTreeShape() {
  const people = (S.people || []).filter(p => !p.hidden);
  const bots = (S.emps || []).filter(e => !['archived', 'retired'].includes(e.status));
  // The sidebar's own rule: built-in and message bots stay outside the chart.
  const helper = isHelperBot;
  const placed = bots.filter(e => !helper(e));
  const helpers = bots.filter(e => helper(e) && !isBuiltInBot(e.name)).sort((a, b) => helperRank(a) - helperRank(b) || String(a.display_name || a.name).localeCompare(String(b.display_name || b.name)));
  const keys = new Set([...(S.orgGroups || []).map(g => 'g:' + g.id), ...people.map(p => 'p:' + p.id), ...placed.map(e => 'b:' + e.name)]);
  const byParent = {}, groupOf = orgGroupOf(people, placed);
  const put = (parent, node) => (byParent[keys.has(parent) ? parent : ''] ||= []).push(node);
  for (const g of (S.orgGroups || [])) put(g.parent ? 'g:' + g.parent : '', {kind: 'group', id: g.id, name: g.name, order: g.order || 0});
  for (const p of people) put(orgHang('p:' + p.id, orgBossKey({kind: 'person', person: p}), groupOf), {kind: 'person', id: p.id, person: p});
  for (const e of placed) {
    // A bot under a built-in or message bot (or a bot that is gone) hangs from the next one up, else its owner.
    let parent = orgBossKey({kind: 'bot', ...e});
    for (let hops = 0; parent.startsWith('b:') && !keys.has(parent) && hops < 5; hops++)
      parent = orgBossKey({kind: 'bot', ...(S.emps.find(x => x.name === parent.slice(2)) || {})});
    if (parent.startsWith('b:') && !keys.has(parent)) parent = e.operator || e.users?.[0]?.id ? 'p:' + (e.operator || e.users[0].id) : '';
    put(orgHang('b:' + e.name, parent, groupOf), {kind: 'bot', ...e});
  }
  return {byParent, helpers};
}
// The org chart's order: a group's humans and bots first, then the groups in it; people before bots; a bot's
// `order`, then names.
function goalTreeOrder(parent) {
  const nameOf = n => n.kind === 'person' ? (n.person.name || n.id) : n.kind === 'group' ? n.name : (n.display_name || n.name || '').replace(TEMP_RE, '');
  return (a, b) => {
    if ((a.kind === 'group') !== (b.kind === 'group')) return a.kind === 'group' ? 1 : -1;
    if (a.kind === 'group') return (a.order || 0) - (b.order || 0);
    if ((a.kind === 'person') !== (b.kind === 'person')) return a.kind === 'person' ? -1 : 1;
    if (a.kind === 'bot' && isTempBot(a) !== isTempBot(b)) return isTempBot(a) - isTempBot(b);
    if (a.kind === 'bot' && byBotOrder(a, b)) return byBotOrder(a, b);
    return nameOf(a).localeCompare(nameOf(b));
  };
}
const GOAL_WORST = ['red', 'yellow', 'green', 'gray', 'none'];
// A goal's KPIs as chips: the dot and the latest value. More than three: two and a count. A phone shows the count.
function goalChips(kpis) {
  if (!kpis?.length) return '';
  const shown = kpis.length > 3 ? kpis.slice(0, 2) : kpis;
  const chip = k => `<span class="gt-chip" data-kpi-chip="${esc(k.id)}" title="${esc(k.name + (k.reason ? ': ' + k.reason : ''))}">${gdot(k.status)}<span class="tnum">${kpiFresh(k) ? esc(kpiNum(k.latest.value, k.unit)) : '–'}</span></span>`;
  const worst = GOAL_WORST.find(s => kpis.some(k => (k.status || 'gray') === s)) || 'gray';
  return shown.map(chip).join('') + (kpis.length > shown.length ? `<span class="gt-kmore">+${kpis.length - shown.length}</span>` : '')
    + `<span class="gt-kn" title="${kpis.length} KPI${kpis.length === 1 ? '' : 's'}">${gdot(worst)}${kpis.length}</span>`;
}
function goalStatusNote(g) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  return byHand ? `Set by ${goalActorName(g.status_by)}${g.status_note ? ': ' + g.status_note : ''}` : g.status_note || '';
}
function goalTreeRows(actor, depth, goals, loose, standing = '') {
  const info = goalOwnerInfo(actor);
  const av = actor === GOAL_COMPANY ? `<span class="av gt-co-av" aria-hidden="true">${esc(String(info.name || 'C').trim()[0] || 'C').toUpperCase()}</span>` : info.avatar;
  const goalCell = g => `<span class="gt-goal" title="${esc(g.title)}">${gdot(g.status, goalStatusNote(g))}<span class="gt-title">${esc(g.title)}</span></span>`;
  const row = (cls, goal, inner) => `<li class="gt-row${cls}" data-owner="${esc(actor)}"${goal ? ` data-goal="${esc(goal.id)}"` : ''} style="--d:${depth}" tabindex="0" role="button">`
    + `<span class="gt-ind" aria-hidden="true"></span>${inner}</li>`;
  const first = goals[0], words = String(standing || '').trim();
  const lead = first ? goalCell(first) + `<span class="gt-kpis">${goalChips(first.kpis)}</span>`
    : (words ? `<span class="gt-goal gt-standing" title="${esc(words)}"><span class="gt-title">${esc(words)}</span></span>` : '')
      + (loose.length ? `<span class="gt-kpis">${goalChips(loose)}</span>` : '');
  return row(actor === GOAL_COMPANY ? ' gt-co' : '', first, `<span class="gt-av">${av}</span><span class="gt-name"><span>${esc(info.name)}</span></span>${lead}`)
    + goals.slice(1).map(g => row(' gt-cont', g, goalCell(g) + `<span class="gt-kpis">${goalChips(g.kpis)}</span>`)).join('');
}
function goalsRender(state) {
  const body = $('#goal-body');
  if (!body || !state.loaded) return;
  const owned = {}, loose = {};
  for (const g of state.goals.filter(goalLive)) (owned[g.owner] ||= []).push(g);
  for (const list of Object.values(owned)) list.sort(goalRank);
  for (const k of state.other) (loose[k.owner] ||= []).push(k);
  const {byParent, helpers} = goalTreeShape();
  const seen = new Set([GOAL_COMPANY]);
  const rows = (actor, depth, standing) => { seen.add(actor); return goalTreeRows(actor, depth, owned[actor] || [], loose[actor] || [], standing); };
  const walk = (parent, depth, path) => (byParent[parent] || []).slice().sort(goalTreeOrder(parent)).map(n => {
    const key = n.kind === 'group' ? 'g:' + n.id : n.kind === 'person' ? 'p:' + n.id : 'b:' + n.name;
    if (path.has(key)) return '';
    const below = walk(key, depth + 1, new Set([...path, key]));
    if (n.kind === 'group') return below ? `<li class="gt-group" style="--d:${depth}"><span class="gt-ind" aria-hidden="true"></span><span class="gt-name"><span>${esc(n.name)}</span></span></li>${below}` : '';
    return rows(n.kind === 'person' ? 'human:' + n.id : 'bot:' + n.name, depth, n.kind === 'person' ? n.person.goals : '') + below;
  }).join('');
  const tree = goalTreeRows(GOAL_COMPANY, 0, owned[GOAL_COMPANY] || [], loose[GOAL_COMPANY] || []) + walk('', 0, new Set());
  const help = [
    ['Message bots', helpers.filter(e => !isBuiltInBot(e.name))],
  ].map(([name, members]) => members.length ? `<li class="gt-sep">${name}</li>` + members.map(e => rows('bot:' + e.name, 0)).join('') : '').join('');
  // Anyone else with goals (someone gone from the roster) still shows, at the end.
  const rest = Object.keys(owned).filter(a => !seen.has(a)).map(a => rows(a, 0)).join('');
  body.innerHTML = goalNeedsHtml(state.needs) + `<ul class="gt" id="goal-tree" aria-label="Goals">${tree}${rest}`
    + help + '</ul>';
  for (const li of body.querySelectorAll('.gt-row')) {
    const open = () => goalPanelOpen(state, li.dataset.owner, {tapped: li.dataset.goal || ''});
    li.onclick = open;
    li.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } };
  }
  const needs = body.querySelector('#goal-needs');
  if (needs) { bindKpiLines(needs); bindProposals(needs, () => goalsReload(state)); }
}
async function goalsReload(state) {
  await goalsRefresh();
  if (GOALS_ST !== state) return;
  goalsRender(state);
  goalPanelRender(state);
}

// ---------------------------------------------------------------- the owner's panel
// Everything the owner has: each goal (tap to edit it, its colour and what it supports), its KPIs (tap one for its
// history), adding a KPI to it, its check-ins; the KPIs no goal uses; and adding a goal or a KPI. The owner is fixed.
function goalPanelDialog(state) {
  let dlg = document.getElementById('goal-panel');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'goal-panel'; dlg.className = 'tmodal kpi-panel gpanel';
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    dlg.addEventListener('close', () => {
      const st = dlg._state;
      if (!st || dlg.open) return;   // the event comes late: the panel may be open again already
      st.panel = null;
      if (st === GOALS_ST && location.hash.startsWith(GOALS + '/')) history.replaceState(null, '', GOALS);
    });
    document.body.appendChild(dlg);
  }
  dlg._state = state;
  return dlg;
}
window.addEventListener('hashchange', () => {
  const dlg = document.getElementById('goal-panel');
  if (dlg?.open && !location.hash.startsWith('#/goals')) dlg.close();
});
// goal: the goal to open for editing ('new' to add one). tapped: the goal on the line that was tapped. Someone with no
// goal opens on a new one; the team line opens its goal when it has just one.
function goalPanelOpen(state, owner, {goal = '', tapped = ''} = {}) {
  const live = state.goals.filter(g => goalLive(g) && g.owner === owner);
  if (!goal && !live.length && goalMayEdit({owner}, state.goals)) goal = 'new';
  if (!goal && owner === GOAL_COMPANY && live.length === 1) goal = live[0].id;
  state.panel = {owner, goal, kpiAdd: '', kpi: '', standing: false, tapped, history: false, historyKpis: null,
    historyLoading: false, historyError: false};
  history.replaceState(null, '', goal && goal !== 'new' ? GOALS + '/' + encodeURIComponent(goal) : GOALS);
  const dlg = goalPanelDialog(state);
  delete dlg.dataset.kpi;
  if (!dlg.open) dlg.showModal();
  goalPanelRender(state);
}
function goalCheckinHtml(c) {
  return `<blockquote class="kpi-check">${esc(c.body)}<footer class="muted">${esc(goalActorName(c.source_actor))}${c.signal ? ' · ' + esc(c.signal.replace('_', ' ')) : ''} · ${esc(ago(c.ts))}</footer></blockquote>`;
}
function goalPanelHtml(state) {
  const P = state.panel, actor = P.owner, info = goalOwnerInfo(actor);
  const goals = state.goals.filter(g => goalLive(g) && g.owner === actor).sort(goalRank);
  const may = goalMayEdit({owner: actor}, state.goals);
  const other = state.other.filter(k => k.owner === actor);
  const person = actor.startsWith('human:') ? (S.people || []).find(p => p.id === actor.slice(6)) : null;
  const standing = String(person?.goals || '').trim();
  const av = actor === GOAL_COMPANY ? `<span class="av gt-co-av" aria-hidden="true">${esc((String(info.name || 'C').trim()[0] || 'C').toUpperCase())}</span>` : info.avatar;
  const item = g => {
    const mayG = goalMayEdit(g, state.goals);
    const head = P.goal === g.id ? goalFormHtml(g, state.goals)
      : `<button class="gp-gline" type="button" data-gp-edit="${esc(g.id)}" title="${esc(g.title)}">${gdot(g.status, goalStatusNote(g))}<span>${esc(g.title)}</span></button>${goalNoteHtml(g, mayG)}`;
    const props = state.proposals.filter(p => p.goal_id === g.id);
    return `<li class="gp-goal" data-gp-goal="${esc(g.id)}">${head}${kpiLinesHtml(g.kpis, {goal: g.id})}
      ${props.length ? `<ul class="kpi-props">${props.map(p => proposalHtml(p)).join('')}</ul>` : ''}
      ${g.checkin ? `<div data-gp-checkins="${esc(g.id)}">${goalCheckinHtml(g.checkin)}</div><button class="linkish" type="button" data-gp-more="${esc(g.id)}">Check-ins</button>` : ''}
      ${mayG ? (P.kpiAdd === g.id ? `<div class="kpi-add-host">${kpiAddHtml(g)}</div>` : `<button class="linkish" type="button" data-gp-kpi-add="${esc(g.id)}">Add KPI</button>`) : ''}</li>`;
  };
  const profile = standing && !goals.some(g => g.title.trim() === standing)
    ? `<li class="gp-goal">${P.standing ? `<form class="goal-form" data-standing-ed="${esc(person.id)}" novalidate>
        <label class="goal-f"><span>Profile goal</span><input type="text" name="title" maxlength="2000" autocomplete="off" value="${esc(standing)}"${personCanEdit(person) ? '' : ' readonly'}></label>
        <div class="goal-actions">${personCanEdit(person) ? '<button class="primary" type="submit">Save</button>' : ''}<button class="ghost" type="button" data-goal-cancel>Cancel</button></div></form>`
      : `<button class="gp-gline gt-standing" type="button" data-gp-standing title="${esc(standing)}"><span>${esc(standing)}</span></button>`}</li>` : '';
  const newGoal = P.goal === 'new' ? `<div class="gp-add">${goalFormHtml({}, state.goals, actor === GOAL_COMPANY ? {company: true} : {owner: actor, fixed: true})}</div>` : '';
  const newKpi = P.kpiAdd === '@owner' ? `<div class="gp-add">${kpiAddHtml(null, actor)}</div>` : '';
  const actions = may && !newGoal && !newKpi && !P.kpiAdd ? `<div class="gp-actions"><button class="ghost" type="button" data-gp-goal-new>Add goal</button><button class="ghost" type="button" data-gp-kpi-new>Add KPI</button></div>` : '';
  const history = may ? `<section class="gp-history" aria-label="Archived KPI history">
      <button class="linkish" type="button" data-gp-history aria-expanded="${P.history ? 'true' : 'false'}">${P.history ? 'Hide archived KPIs' : 'Archived KPIs'}</button>
      ${P.history ? `<div class="gp-history-list"><h3 class="kpi-h">Archived KPIs</h3>
        ${P.historyLoading ? '<p class="muted">Loading history…</p>' : P.historyError ? '<p class="err">Could not load archived KPIs.</p>'
          : P.historyKpis?.length ? kpiLinesHtml(P.historyKpis, {owner: true}) : '<p class="muted">No archived KPIs for this owner.</p>'}</div>` : ''}
    </section>` : '';
  return `<div class="tmodal-head"><span class="gp-who">${av}<span>${esc(info.name)}</span></span><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-gp-close aria-label="Close">✕</button></div>
    <div class="tmodal-body gp-body">
      ${goals.length || profile ? `<ul class="gp-goals">${goals.map(item).join('')}${profile}</ul>` : ''}
      ${newGoal}${other.length ? `<h3 class="kpi-h">Other KPIs</h3>${kpiLinesHtml(other)}` : ''}${newKpi}${actions}${history}
      ${!goals.length && !profile && !other.length && !newGoal && !newKpi && !actions ? '<p class="muted">No goals.</p>' : ''}
    </div>`;
}
function goalPanelRender(state) {
  const dlg = document.getElementById('goal-panel'), P = state.panel;
  if (!dlg?.open || !P || P.kpi || dlg._state !== state) return;
  dlg.setAttribute('aria-label', goalOwnerInfo(P.owner).name);
  dlg.dataset.owner = P.owner;
  const scroll = dlg.querySelector('.gp-body')?.scrollTop || 0;
  dlg.innerHTML = goalPanelHtml(state);
  const body = dlg.querySelector('.gp-body');
  body.scrollTop = scroll;
  const redraw = () => goalPanelRender(state), reload = () => goalsReload(state);
  const setGoal = id => { P.goal = id; P.kpiAdd = ''; history.replaceState(null, '', id && id !== 'new' ? GOALS + '/' + encodeURIComponent(id) : GOALS); redraw(); };
  dlg.querySelector('[data-gp-close]').onclick = () => dlg.close();
  for (const b of body.querySelectorAll('[data-gp-edit]')) b.onclick = () => setGoal(b.dataset.gpEdit);
  const form = body.querySelector('[data-goal-form]');
  if (form) {
    const id = form.dataset.goalForm, g = state.goals.find(x => x.id === id) || {};
    bindGoalForm(form, g, state.goals, {company: id === 'company', owner: P.owner}, async ok => {
      setGoal('');
      if (ok && GOALS_ST === state) goalsRender(state);
    });
  }
  for (const a of body.querySelectorAll('[data-goal-go]')) a.onclick = ev => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    ev.preventDefault();
    const g = state.goals.find(x => x.id === a.dataset.goalGo);
    if (g) goalPanelOpen(state, g.owner, {goal: g.id});
  };
  for (const b of body.querySelectorAll('button[data-goal-auto]')) if (b.dataset.goalAuto) b.onclick = async () => {
    b.disabled = true;
    try { await post('/v2/goals/' + encodeURIComponent(b.dataset.goalAuto) + '/status/auto', {}); await reload(); }
    catch (e) { toast(e.message || 'Could not hand the colour back', true); b.disabled = false; }
  };
  bindKpiLines(body);
  bindProposals(body, reload);
  for (const b of body.querySelectorAll('[data-gp-kpi-add]')) b.onclick = () => { P.kpiAdd = b.dataset.gpKpiAdd; P.goal = ''; redraw(); };
  const newGoal = body.querySelector('[data-gp-goal-new]'), newKpi = body.querySelector('[data-gp-kpi-new]');
  if (newGoal) newGoal.onclick = () => setGoal('new');
  if (newKpi) newKpi.onclick = () => { P.kpiAdd = '@owner'; P.goal = ''; redraw(); };
  const history = body.querySelector('[data-gp-history]');
  if (history) history.onclick = async () => {
    P.history = !P.history;
    if (!P.history || P.historyKpis !== null) { redraw(); return; }
    P.historyLoading = true;
    P.historyError = false;
    redraw();
    const result = await v2Get(`/v2/kpis?owner=${encodeURIComponent(P.owner)}&include_archived=true`);
    if (state.panel !== P) return;
    P.historyLoading = false;
    P.historyError = !result;
    P.historyKpis = result ? (result.kpis || []).filter(k => k.archived_at && !goalBuiltInOwner(k.owner)) : null;
    redraw();
  };
  const kform = body.querySelector('[data-kpi-add]');
  if (kform) {
    kpiAddBind(kform, state.goals.find(g => g.id === kform.dataset.kpiAdd) || null, async ok => { P.kpiAdd = ''; if (ok) await reload(); else redraw(); });
    kform.scrollIntoView({block: 'nearest'});
  }
  for (const b of body.querySelectorAll('[data-gp-more]')) b.onclick = async () => {
    b.disabled = true;
    const r = await v2Get(`/v2/goals/${encodeURIComponent(b.dataset.gpMore)}/checkins`);
    const host = body.querySelector(`[data-gp-checkins="${CSS.escape(b.dataset.gpMore)}"]`);
    if (r && host) { host.innerHTML = (r.checkins || []).slice().sort((x, y) => String(y.ts).localeCompare(String(x.ts))).map(goalCheckinHtml).join(''); b.remove(); }
    else b.disabled = false;
  };
  const standingBtn = body.querySelector('[data-gp-standing]');
  if (standingBtn) standingBtn.onclick = () => { P.standing = true; redraw(); };
  const standing = body.querySelector('[data-standing-ed]');
  if (standing) {
    const person = (S.people || []).find(x => x.id === standing.dataset.standingEd), input = standing.elements.title;
    const done = () => { P.standing = false; redraw(); };
    standing.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
    standing.querySelector('[data-goal-cancel]').onclick = done;
    standing.onsubmit = async ev => {
      ev.preventDefault();
      const text = input.value.trim();
      if (input.readOnly || !person) return;
      if (text === String(person.goals || '').trim()) { done(); return; }
      try { const row = await post(`/v2/humans/${encodeURIComponent(person.id)}`, {goals: text}); person.goals = row.goals; done(); goalsRender(state); }
      catch (e) { toast(e.message || 'Could not save the goal', true); }
    };
    input.focus();
  }
  const tapped = P.tapped && body.querySelector(`[data-gp-goal="${CSS.escape(P.tapped)}"]`);
  if (tapped && !form && !kform) { tapped.scrollIntoView({block: 'nearest'}); P.tapped = ''; }
}

// ---------------------------------------------------------------- Needs you
function goalNeedsHtml(items) {
  if (!items?.length) return '';
  const rows = items.map(it => {
    if (it.kind === 'proposal') return proposalHtml({...it.proposal, may_decide: true}, it.kpi_name || it.goal_title || '');
    const red = it.kind === 'kpi_red';
    return `<li class="need-row" data-kpi="${esc(it.kpi_id)}" tabindex="0" role="button">${gdot(red ? 'red' : 'gray')}<span class="need-text"><b>${esc(it.kpi_name)}</b>${red ? ` on ${esc(it.goal_title)}` : ''}<span class="muted">: ${esc(it.reason)}</span></span></li>`;
  }).join('');
  return `<section class="gt-needs" id="goal-needs" aria-label="Needs you"><div class="gt-needs-h">Needs you<span class="cnt tnum">${items.length}</span></div><ul>${rows}</ul></section>`;
}
