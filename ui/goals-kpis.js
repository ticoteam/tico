/* Goals page: the tree (a line per owner), the owner's panel a tap opens, a goal's KPI lines, the KPI panel,
   adding a KPI and Needs you.
   Everything shown is what the server computed (GET /api/v2/goals/tree, /kpis/{id}, /goals/needs-you,
   /bots/{bot}/kpis): a colour is never derived here, and a KPI with no fresh data says so instead of a number.
   pageGoals and the goal editor (goalFormHtml, bindGoalForm) are in ui/app/goals.js. */
'use strict';

const KPI_LABEL = {green: 'On track', yellow: 'At risk', red: 'Off track', gray: 'No fresh data', none: 'No target'};
const KPI_QUALITY = {estimate: 'estimate', partial: 'partial'};
const GOAL_MANAGER_ACTOR = 'bot:goal-manager';

function goalActorName(actor) {
  if (!actor) return '';
  if (actor === 'keeper') return S.config?.app_name || 'Tico';
  const info = goalOwnerInfo(actor);
  return actor === GOAL_MANAGER_ACTOR ? strategyBotName() : info.name;
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
// On a goal an archived link no longer counts, so it shows only that; the owner's archive list keeps the last reading.
function kpiLine(k, {owner = false, goal = ''} = {}) {
  if (k.archived_at && goal) return `<li class="kpi-line kpi-archived-line muted" data-kpi="${esc(k.id)}" data-kpi-goal="${esc(goal)}" tabindex="0" role="button">
    <span class="kpi-name">${esc(k.name)}</span><span class="kpi-off">Archived ${esc(kpiDay(k.archived_at))}, not counting</span></li>`;
  const value = kpiFresh(k)
    ? `<span class="kpi-val tnum">${esc(kpiNum(k.latest.value, k.unit))}${KPI_QUALITY[k.latest.quality] ? ` <span class="kpi-q">${KPI_QUALITY[k.latest.quality]}</span>` : ''}<span class="muted"> · ${esc(kpiDay(k.latest.period_end))}</span></span>`
    : `<span class="kpi-val kpi-off">${k.freshness === 'stale' ? 'stale' : 'no data'}</span>`;
  return `<li class="kpi-line" data-kpi="${esc(k.id)}"${goal ? ` data-kpi-goal="${esc(goal)}"` : ''} tabindex="0" role="button" title="${esc(k.reason || '')}">
    ${gdot(k.status, k.reason)}<span class="kpi-name">${esc(k.name)}</span>${owner ? `<span class="kpi-owner">${esc(goalActorName(k.owner_id))}</span>` : ''}
    ${value}${k.target_label ? `<span class="kpi-target">${esc(k.target_label)}</span>` : ''}${kpiSpark(k.spark, k.status)}</li>`;
}
function kpiLinesHtml(kpis, opts) {
  const active = (kpis || []).filter(k => !k.archived_at), archived = (kpis || []).filter(k => k.archived_at);
  const limit = opts?.goal ? 3 : archived.length;
  const lines = [...active, ...archived.slice(0, limit)].map(k => kpiLine(k, opts)).join('');
  return lines ? `<ul class="kpi-lines">${lines}${archived.length > limit ? `<li class="muted kpi-archived-more">+${archived.length - limit} archived</li>` : ''}</ul>` : '';
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

// ---------------------------------------------------------------- suggestion cards
// The yes / no row every suggestion card ends with, shared with the strategy cards: yes first, saying what it does, then
// "No, leave it" (`no: null` for a card with one button). `line` is a plain sentence above the buttons. `decide` is what
// the yes button sends: a one-button card that changes nothing ("Got it" on a strategy change the draft holds) rejects.
// `reason`: once no has rejected it, the card offers a line on why (propLeftHtml), which the Strategy Bot reads.
function propDoHtml(p, {yes, no = 'No, leave it', line = '', decide = 'confirm', reason = false}) {
  if (!p.may_decide) return '';
  return `<div class="kpi-prop-do">${line ? `<p class="muted kpi-prop-line">${esc(line)}</p>` : ''}${yes ? `<button class="primary" type="button" data-decide="${decide}" data-prop-yes>${esc(yes)}</button>` : ''}${no ? `<button class="ghost" type="button" data-decide="reject"${reason ? ' data-prop-reason' : ''}>${esc(no)}</button>` : ''}</div>`;
}
// What a card asking a reason becomes after no (already rejected, in one click): one line, a one-line box and Send.
// Sent, the reason is the suggestion's decision note (POST /v2/proposals/{id}/note, once, by whoever said no), read by
// the Strategy Bot before it suggests again; left, nothing more happens. The server's cap (backend/goals.py REASON_MAX).
const PROP_REASON_MAX = 280;
function propLeftHtml() {
  return `<form class="kpi-prop-left" data-prop-left novalidate><span class="kpi-prop-left-k">Left as it is. Tell it why? (optional)</span>
    <input type="text" name="note" maxlength="${PROP_REASON_MAX}" autocomplete="off" aria-label="Why you left it (optional)" placeholder="One line, e.g. no billing yet">
    <button class="primary" type="submit">Send</button><span class="kpi-prop-left-err" role="alert" hidden></span></form>`;
}
function bindPropLeft(li, after) {
  const form = li.querySelector('[data-prop-left]'), err = form.querySelector('.kpi-prop-left-err');
  form.elements.note.focus();
  form.onsubmit = async ev => {
    ev.preventDefault();
    const note = form.elements.note.value.trim(), send = form.querySelector('button[type=submit]');
    if (!note) return;                  // an empty box is a reason left out: the rejection stands as it is
    send.disabled = true;
    try {
      await post(`/v2/proposals/${encodeURIComponent(li.dataset.proposal)}/note`, {note});
      toast('Sent');
      await after();
    } catch (e) {
      // The server's refusal, in its words, beside the box (a reason already added, too long, not theirs to add).
      err.textContent = e.message || 'Could not send the reason'; err.hidden = false;
      toast(err.textContent, true); send.disabled = false;
    }
  };
}
// What a suggestion would change, as Now and Proposed: rows [{label, now, proposed}], a label only when there are
// several. Long text is cut to four lines, with one "Show all" opening both.
const PROP_NP_LONG = 280;
function propNowProposedHtml(rows) {
  if (!rows?.length) return '';
  const text = v => String(v ?? '').trim();
  const long = rows.some(r => [r.now, r.proposed].some(v => text(v).length > PROP_NP_LONG || text(v).split('\n').length > 4));
  const cell = (k, v) => `<div class="prop-np-row"><span class="prop-np-k">${k}</span><span class="prop-np-v${text(v) ? '' : ' muted'}">${esc(text(v) || 'Empty')}</span></div>`;
  return `<div class="prop-np${long ? ' prop-np-clamp' : ''}">${rows.map(r => `${rows.length > 1 && r.label ? `<div class="prop-np-label">${esc(r.label)}</div>` : ''}${cell('Now', r.now)}${cell('Proposed', r.proposed)}`).join('')}
    ${long ? '<button class="linkish" type="button" data-np-all>Show all</button>' : ''}</div>`;
}
// What the Strategy Bot noticed, by the flag's issue (backend/goals.py FLAGS).
const PROP_FLAG = {vague: 'is worded too vaguely to tell when it is done', duplicate: 'looks like a duplicate of another goal', unmeasured: 'has nothing measuring it'};
// A goal suggestion card (the payload's words, and `card`: what the server read to say it plainly, backend/goals.py
// card): one line on what yes changes, what it would change, why, then the buttons named for what they do.
function goalPropHtml(p, extra) {
  const pl = p.payload || {}, c = p.card || {}, by = goalActorName(p.proposed_by);
  const goal = `<b>${esc(c.goal_title || extra || 'this goal')}</b>`, kpiName = `<b>${esc(c.kpi_name || extra || 'this measure')}</b>`;
  const why = `<span class="muted kpi-prop-why">${p.reason ? esc(p.reason) + ' · ' : ''}${esc(by)}</span>`;
  let head, body = '', yes = 'Use this', no = 'No, leave it', more = '', line = '';
  if (p.kind === 'goal_kpi') {
    const m = c.measure || pl.kpi || {}, proposed = c.target_label || '';
    head = `Add the measure <b>${esc(m.name || 'with no name')}</b> to ${goal}`;
    // A measure the reader may not read (card `hidden`) says nothing about itself, not even that it lacks a source.
    body = `${m.hidden ? '' : `<span class="kpi-prop-detail">${m.definition ? esc(m.definition) : '<span class="muted">What it counts is not written down yet</span>'}</span>`}
      <span class="kpi-prop-target">${proposed ? `Target ${esc(proposed)}` : 'No target yet'}</span>
      ${m.hidden || String(m.source_note || '').trim() ? '' : '<span class="kpi-prop-warn">No data source yet: it will show no numbers until one is set</span>'}`;
    // The target fields of the goal editor, folded away: the card stays short, and blank leaves the measure with none.
    const t = pl.target && pl.target.kind && pl.target.kind !== 'none' ? pl.target : {kind: m.direction === 'range' ? 'maintain' : 'improve'};
    if (p.may_decide && !m.hidden) more = `<details class="kpi-prop-set"${proposed ? ' data-proposed' : ''}><summary>${proposed ? 'Change the target' : 'Set a target (optional)'}</summary>
      <form class="goal-form kpi-prop-tf" data-prop-target novalidate>${kpiTargetFields(t)}</form></details>`;
    // Nobody adds a measure they may not read (the server refuses it, as it refuses linking one by hand).
    yes = m.hidden ? null : 'Add measure';
    if (m.hidden) line = 'You may not read this measure, so someone who may read it adds it';
  } else if (p.kind === 'kpi_target') {
    head = `New target for ${kpiName} on ${goal}`;
    body = propNowProposedHtml([{now: c.now_label || 'No target', proposed: c.target_label || 'No target'}]);
    yes = 'Use this target';
  } else if (p.kind === 'kpi_definition') {
    head = `New definition for ${kpiName}`;
    body = propNowProposedHtml(c.changes || Object.entries(pl).map(([k, v]) => ({label: k, now: '', proposed: v})));
    yes = 'Use new definition';
  } else if (p.kind === 'goal_wording') {
    head = `New wording for ${goal}`;
    body = propNowProposedHtml(c.changes || [{label: 'Title', now: '', proposed: pl.title}, {label: 'Description', now: '', proposed: pl.body}].filter(r => r.proposed != null));
    yes = 'Use new wording';
  } else if (p.kind === 'flag') {
    head = `<b>${esc(by)} noticed:</b> ${goal} ${esc(PROP_FLAG[pl.issue] || pl.issue || 'needs a look')}${pl.note ? `: ${esc(pl.note)}` : ''}`;
    yes = 'Got it'; no = null;
  } else if (p.kind === 'recovery') {
    // Out of this card's scope: a recovery keeps its own words (recoveryHtml), here its pick on one line.
    head = `<b>A recovery</b> for ${goal}: ${esc(pl.options?.[pl.pick || 0]?.label || '')}`;
    yes = 'Approve'; no = 'Reject';
  } else if (p.kind === 'goal_why') {
    head = `<b>A Why</b> for ${goal}: ${esc(pl.why || '')}`;
    yes = 'Accept'; no = 'Reject';
  } else head = `<b>${esc(p.kind)}</b>${extra ? ` <span class="muted">· ${esc(extra)}</span>` : ''}`;
  return `<li class="kpi-prop" data-proposal="${esc(p.id)}" data-prop-kind="${esc(p.kind)}"><div class="kpi-prop-main"><span class="kpi-prop-head">${head}</span>${body}
    ${p.kind === 'flag' ? (p.reason ? `<span class="muted kpi-prop-why">${esc(p.reason)}</span>` : '') : why}${more}</div>
    ${propDoHtml(p, {yes, no, line, reason: no === 'No, leave it'})}</li>`;
}
// ---------------------------------------------------------------- strategy proposals
// The strategy's live bets, for the names a proposal leaves out (a retire is just {id, status}).
const stratPropBets = () => GOALS_ST?.strategy?.bets || [];
const stratPropOne = () => GOALS_ST?.strategy?.label_one || 'Bet';
// A bet by the number the Strategy page shows it under: its place among the live bets, "Bet 2".
function stratPropBetName(id) {
  const n = stratPropBets().filter(b => b.status !== 'retired').findIndex(b => b.id === id);
  return n < 0 ? `a ${stratPropOne().toLowerCase()}` : `${stratPropOne()} ${n + 1}`;
}
// A wrong-if in plain text, as the Strategy page words it.
function stratPropWrongText(w) {
  if (!w) return '';
  const el = document.createElement('span');
  el.innerHTML = strategyWrongIfHtml(w);
  return el.textContent.trim();
}
// One changed thing on a strategy card: its name, then Now / Proposed (or only what is proposed without a draft to read).
const stratPropRow = (k, block) => `<li><span class="strat-prop-k">${k}</span><div class="strat-prop-v">${block}</div></li>`;
// A change to the strategy draft (backend/strategy.py proposal_error): who suggests it and why, each change as Now /
// Proposed against the draft as it is now, then "Add to draft" / "No, leave it". Nothing it does is published.
function strategyDraftPropHtml(p) {
  const pl = p.payload || {}, st = GOALS_ST?.strategy || null, d = st?.draft || null, one = stratPropOne();
  const rows = [], by = goalActorName(p.proposed_by);
  const same = (a, b) => stratText(a) === stratText(b);
  // A text field: left out when the proposal says what the draft already says.
  const text = (k, now, proposed) => {
    if (d && same(now, proposed)) return;
    rows.push(stratPropRow(esc(k), propNowProposedHtml([{now: d ? now : '', proposed}])));
  };
  if (pl.summary != null) text('Summary', d?.summary, pl.summary);
  if (pl.vision != null) text('Vision', d?.vision, pl.vision);
  const secs = pl.sections || {};
  for (const sec of STRAT_SECTIONS) if (sec.key in secs) text(sec.title, d?.sections?.[sec.key], secs[sec.key]);
  // An extra section can be renamed with its text unchanged: the title is a change of its own, as the server writes
  // it (backend/strategy.py, a missing title keeps the draft's). An empty body removes the section, so no rename then.
  for (const [key, x] of Object.entries(secs.extra || {})) {
    const was = d?.sections?.extra?.[key], title = x?.title || was?.title || key;
    const changed = [];
    if (d && was && stratText(x?.body) && !same(was.title, title)) changed.push({label: 'Title', now: was.title, proposed: title});
    if (!(d && same(was?.body, x?.body))) changed.push({label: 'Text', now: d ? was?.body : '', proposed: x?.body});
    // A rename alone names itself, since the block shows a name only for two or more changes.
    const only = changed.length === 1 && changed[0].label === 'Title' ? ' <span class="muted">· Title</span>' : '';
    if (changed.length) rows.push(stratPropRow(esc(title) + only, propNowProposedHtml(changed)));
  }
  const fields = [['statement', 'Statement'], ['why', 'Why'], ['rules_out', 'Rules out'], ['wrong_if', 'Wrong if'], ['cites', 'Cites']];
  const value = (k, v) => k === 'wrong_if' ? stratPropWrongText(v) : k === 'cites' ? (v || []).join(', ') : v;
  for (const b of pl.bets || []) {
    const was = b.id ? stratPropBets().find(x => x.id === b.id) : null;
    if (!b.id) {
      const lines = fields.filter(([k]) => k !== 'cites' && stratText(value(k, b[k])))
        .map(([k, label]) => `<div class="prop-np-row"><span class="prop-np-k">${label}</span><span class="prop-np-v">${esc(value(k, b[k]))}</span></div>`).join('');
      rows.push(stratPropRow(`New ${esc(one.toLowerCase())}`, `<div class="prop-np">${lines}</div>${b.status === 'active' ? ''
        : `<p class="muted strat-prop-note">It starts as proposed; you make it active on the Strategy page.</p>`}`));
    } else if (b.status === 'retired') {
      // Every live goal, not goals_by_bet: that list stops at 20 and may hold only closed goals.
      const serve = (st?.live_by_bet || {})[b.id] || 0;
      rows.push(stratPropRow(`Retire ${esc(stratPropBetName(b.id))}`, `<span class="strat-prop-st">${esc(was?.statement || b.statement || '')}</span>${serve
        ? `<p class="muted strat-prop-note">${serve} goal${serve === 1 ? ' serves' : 's serve'} it. When you publish, you'll be asked what happens to ${serve === 1 ? 'it' : 'them'}.</p>` : ''}`));
    } else {
      const changed = fields.filter(([k]) => k in b && !(was && same(value(k, was[k]), value(k, b[k]))))
        .map(([k, label]) => ({label, now: was ? value(k, was[k]) : '', proposed: value(k, b[k])}));
      if (b.status && b.status !== was?.status) changed.push({label: 'Status', now: was?.status || '', proposed: b.status});
      // One field changed: its name goes with the bet's, since the block shows a name only for two or more.
      if (changed.length) rows.push(stratPropRow(`Change ${esc(stratPropBetName(b.id))}${changed.length === 1 ? ` <span class="muted">· ${esc(changed[0].label)}</span>` : ''}`, propNowProposedHtml(changed)));
    }
    // Ideas the proposal links to this bet (backend/strategy.py apply_proposal); a link alone is a change too.
    const ideas = (b.ideas || []).length;
    if (ideas) rows.push(stratPropRow(`${b.id ? esc(stratPropBetName(b.id)) : `New ${esc(one.toLowerCase())}`} <span class="muted">· Ideas</span>`,
      `Links ${ideas} idea${ideas === 1 ? '' : 's'}`));
  }
  const lens = pl.source === 'lens' ? p.lens_name : '', tripped = pl.source === 'bet_tripped';
  const from = lens ? ` from the <b>${esc(lens)}</b> lens` : pl.source === 'briefing' ? " from this week's market briefing"
    : tripped ? ` because <b>${esc(stratPropBetName((pl.bets || []).find(b => b.id)?.id))}</b> may be wrong${p.reason ? `: ${esc(p.reason)}` : ''}` : '';
  const empty = !rows.length;
  const line = st?.current?.n ? `Yes adds this to your strategy draft. Bots keep reading v${st.current.n} until you publish it on the Strategy page.`
    : 'Nothing is published until you publish it on the Strategy page.';
  return `<li class="kpi-prop strat-prop" data-proposal="${esc(p.id)}" data-strategy-draft><div class="kpi-prop-main">
      <span class="kpi-prop-head"><b>${esc(by)}</b> suggests a change to your strategy${from} <a class="strat-prop-open" href="${STRATEGY}">Open Strategy</a></span>
      ${empty ? '<span class="kpi-prop-detail">Your draft already says this.</span>' : `<ul class="strat-prop-list">${rows.join('')}</ul>`}
      ${p.reason && !tripped ? `<span class="muted kpi-prop-why">${esc(p.reason)}</span>` : ''}</div>
    ${empty ? propDoHtml(p, {yes: 'Got it', no: null, decide: 'reject'}) : propDoHtml(p, {yes: 'Add to draft', line})}</li>`;
}
// What the realign card's one button says: what the choices change, or that every goal stays.
function stratRealignYes(open, retire, move) {
  if (!open) return 'Got it';
  if (!retire && !move) return open === 1 ? 'Keep this goal as it is' : `Keep all ${open} goals as they are`;
  const parts = [];
  if (retire) parts.push(`retire ${retire} goal${retire === 1 ? '' : 's'}`);
  if (move) parts.push(`move ${move}${retire ? '' : ` goal${move === 1 ? '' : 's'}`}`);
  const out = parts.join(', ');
  return out[0].toUpperCase() + out.slice(1);
}
// The goals of bets a publish retired or reworded (backend/strategy.py realign): per goal Keep as it is, Retire goal or
// Move to one of the active bets, sent together as `moves` by one button that says what they change. No Reject: every
// goal on Keep is the no. A goal finished or dropped since (`closed_goals`) reads Already closed, with no choice.
function strategyRealignPropHtml(p) {
  const pl = p.payload || {}, one = stratPropOne(), closed = new Set(p.closed_goals || []);
  const moveTo = (pl.bets || []).map(b => `<option value="${esc(b.id)}">Move to ${esc(one)} ${esc(b.number)}: ${esc(b.statement)}</option>`).join('');
  const bet = g => g.change === 'retired' ? `<span class="muted">Its ${esc(one.toLowerCase())} was retired: ${esc(g.statement || '')}</span>`
    : `<div class="prop-np strat-realign-bet">${g.was ? `<div class="prop-np-row"><span class="prop-np-k">Before v${esc(pl.n)}</span><span class="prop-np-v">${esc(g.was)}</span></div>` : ''}<div class="prop-np-row"><span class="prop-np-k">Now</span><span class="prop-np-v">${esc(g.statement || '')}</span></div></div>`;
  const goals = (pl.goals || []).map(g => `<li class="strat-realign-goal${closed.has(g.goal_id) ? ' closed' : ''}"><span class="strat-realign-t"><b>${esc(g.title)}</b>
      <span class="muted">${esc(goalActorName(g.owner_id))}</span>${bet(g)}</span>
      ${closed.has(g.goal_id) ? '<span class="muted strat-realign-closed">Already closed</span>' : `<label class="goal-f strat-realign-f"><span class="sr-only">${esc(g.title)}</span><select data-realign-goal="${esc(g.goal_id)}" aria-label="${esc(g.title)}"${p.may_decide ? '' : ' disabled'}>
        <option value="keep" selected>Keep as it is</option><option value="retire">Retire goal</option>${moveTo}</select></label>`}</li>`).join('');
  const count = (pl.goals || []).length, open = (pl.goals || []).filter(g => !closed.has(g.goal_id)).length;
  const head = `Strategy v${esc(pl.n)} changed ${count === 1 ? 'a bet that 1 goal serves' : `bets that ${count} goals serve`}`;
  const line = open ? 'Choose what happens to each goal. Nothing changes until you press the button.' : 'These goals were closed since; nothing to decide.';
  return `<li class="kpi-prop strat-prop" data-proposal="${esc(p.id)}" data-realign><div class="kpi-prop-main">
      <span class="kpi-prop-head"><b>${head}</b> <a class="strat-prop-open" href="${STRATEGY}">Open Strategy</a></span>
      <span class="kpi-prop-detail">${line}</span>
      <ul class="strat-realign">${goals}</ul></div>
    ${propDoHtml(p, {yes: stratRealignYes(open, 0, 0), no: null})}</li>`;
}
function proposalHtml(p, extra = '') {
  if (p.kind === 'strategy_draft') return strategyDraftPropHtml(p);
  if (p.kind === 'strategy_realign') return strategyRealignPropHtml(p);
  return goalPropHtml(p, extra);
}
// What the target fields on an Add a KPI card send: nothing when folded away or left blank (the suggestion's target
// stands: none, or the one it proposed), else the fields as the goal editor sends them.
function propTarget(li) {
  const set = li.querySelector('.kpi-prop-set'), form = set?.querySelector('[data-prop-target]');
  if (!set?.open || !form) return null;
  const t = kpiTargetBody(form);
  if (t.kind === 'none') return set.hasAttribute('data-proposed') ? t : null;
  const filled = ['baseline', 'target', 'deadline', 'min', 'max'].some(k => t[k] != null && t[k] !== '');
  return filled ? t : null;
}
function bindProposals(root, after) {
  for (const li of root.querySelectorAll('[data-proposal]')) {
    for (const b of li.querySelectorAll('[data-np-all]')) b.onclick = () => { b.closest('.prop-np').classList.remove('prop-np-clamp'); b.remove(); };
    const form = li.querySelector('[data-prop-target]');
    if (form) { kpiTargetKinds(form); form.onsubmit = ev => { ev.preventDefault(); li.querySelector('[data-prop-yes]')?.click(); }; }
    // The realign card's button follows the choices: "Retire 1 goal, move 2", or every goal kept.
    const sels = li.hasAttribute('data-realign') ? [...li.querySelectorAll('[data-realign-goal]')] : [];
    for (const sel of sels) sel.onchange = () => {
      const yes = li.querySelector('[data-prop-yes]');
      if (yes) yes.textContent = stratRealignYes(sels.length, sels.filter(x => x.value === 'retire').length, sels.filter(x => !['keep', 'retire'].includes(x.value)).length);
    };
  }
  for (const li of root.querySelectorAll('[data-proposal]')) for (const b of li.querySelectorAll('[data-decide]')) b.onclick = async () => {
    b.disabled = true;
    // A recovery's Approve buttons carry their option: approving applies exactly that one.
    const body = {decision: b.dataset.decide, ...(b.dataset.option ? {option: Number(b.dataset.option)} : {})};
    // A realignment confirms every goal's choice at once: keep, retire, or the bet it moves to.
    if (li.hasAttribute('data-realign') && body.decision === 'confirm')
      body.moves = Object.fromEntries([...li.querySelectorAll('[data-realign-goal]')].map(sel => [sel.dataset.realignGoal, sel.value]));
    if (li.dataset.propKind === 'goal_kpi' && body.decision === 'confirm') {
      const target = propTarget(li);
      if (target) body.target = target;
    }
    let out;
    try { out = await post(`/v2/proposals/${encodeURIComponent(li.dataset.proposal)}/decide`, body); }
    catch (e) { toast(e.message || 'Could not decide', true); b.disabled = false; return; }
    // Rejected already; the card stays as one line asking why, until something draws the list again.
    if (b.hasAttribute('data-prop-reason') && out?.proposal?.may_reason) {
      li.innerHTML = propLeftHtml(); bindPropLeft(li, after);
      return;
    }
    await after();
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
        <dt>Owner</dt><dd>${esc(goalActorName(k.owner_id))}</dd>
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
      <label class="goal-f"><span>Owner</span><select name="owner">${ownerOptions(kpiOwnerChoices(k.owner_id), k.owner_id)}</select></label></div>
    <label class="goal-f"><span>Source</span><input name="source_note" maxlength="2000" value="${esc(k.source_note || '')}"></label>
    <div class="goal-actions"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-cancel>Cancel</button></div></form>`;
}

function kpiPanelBind(dlg, d) {
  const k = d.kpi, body = dlg.querySelector('.kpi-body');
  // A reply can land after the person has left this KPI (back, closed, another goal): redraw it only while it shows.
  const again = async () => { if (dlg.open && dlg.dataset.kpi === k.id) await kpiOpen(k.id); if (typeof goalsRefresh === 'function') { await goalsRefresh(); if (GOALS_ST && $('#goal-body')) goalsRender(GOALS_ST); } };
  dlg.querySelector('[data-kpi-close]').onclick = () => dlg.close();
  bindProposals(body, again);
  const inPanel = dlg.id === 'goal-panel' && GOALS_ST;
  for (const b of body.querySelectorAll('[data-kpi-goal-open]')) b.onclick = () => {
    const g = inPanel && GOALS_ST.goals.find(x => x.id === b.dataset.kpiGoalOpen);
    if (g) goalPanelOpen(GOALS_ST, g.owner_id, {goal: g.id});
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
    const linked = d.links || [];
    const impact = linked.length ? `\n\nIt stops counting toward ${linked.length} ${linked.length === 1 ? 'goal' : 'goals'}:\n${linked.map(l => '• ' + l.goal_title).join('\n')}` : '';
    if (!restoring && !window.confirm(`Archive “${k.name}”? Its readings and definition history will stay available.${impact}`)) return;
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
  if (inPage && !GOALS_ST.panel.owner_id) GOALS_ST.panel.owner_id = data.kpi.owner_id;
  dlg.innerHTML = kpiPanelHtml(data, {back: inPage});
  kpiPanelBind(dlg, data);
}

// ---------------------------------------------------------------- adding a KPI
// On a goal: pick a KPI that exists (or a bot's automatic ones) or make one, with the target on the link.
// On its own: a KPI no goal uses yet, owned by whoever's panel it is.
function kpiAddHtml(goal, forOwner = '') {
  const owner = goal ? goal.owner_id : forOwner || (mePerson() ? 'human:' + mePerson().id : '');
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
    const [all, auto] = await Promise.all([v2Get('/v2/kpis'), String(goal.owner_id).startsWith('bot:') ? v2Get(`/v2/kpis?auto_for=${encodeURIComponent(goal.owner_id.slice(4))}`) : null]);
    if (!form.isConnected) return;
    const linked = new Set((goal.kpis || []).map(k => k.id));
    const opts = list => list.filter(k => !linked.has(k.id)).map(k => `<option value="${esc(k.id)}">${esc(k.name)}${k.auto ? '' : ' · ' + esc(goalActorName(k.owner_id))}</option>`).join('');
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
        if (form.elements.owner.value !== goal.owner_id) body.owner = form.elements.owner.value;
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
// tooltip) and its KPIs as small chips; more goals follow on lines of their own. Helpers stay off it, as on the Org
// tab. Tapping a goal opens it (goalSheetOpen); tapping anything else on a line opens that owner's panel.
function goalTreeShape() {
  const people = (S.people || []).filter(p => !p.hidden);
  const bots = (S.emps || []).filter(e => !['archived', 'retired'].includes(e.status));
  // The sidebar's own rule: built-in and message bots stay outside the chart. The Strategy Bot is placed and then lifted
  // out (orgTreeWithHelpers), so the Heads and groups the server hangs under it show under the owner, as on the sidebar.
  const placed = bots.filter(e => !isHelperBot(e) || e.id === 'goal-manager');
  const keys = new Set([...(S.orgGroups || []).map(g => 'g:' + g.id), ...people.map(p => 'p:' + p.id), ...placed.map(e => 'b:' + e.id)]);
  const byParent = {}, groupOf = orgGroupOf(people, placed);
  const put = (parent, node) => (byParent[keys.has(parent) ? parent : ''] ||= []).push(node);
  for (const g of (S.orgGroups || [])) put(orgGroupParent(g, keys), {kind: 'group', id: g.id, name: g.name, order: g.order || 0});
  for (const p of people) put(orgHang('p:' + p.id, orgBossKey({kind: 'person', person: p}), groupOf), {kind: 'person', id: p.id, person: p});
  for (const e of placed) {
    // A bot under a built-in or message bot (or a bot that is gone) hangs from the next one up, else its owner.
    let parent = orgBossKey({kind: 'bot', ...e});
    for (let hops = 0; parent.startsWith('b:') && !keys.has(parent) && hops < 5; hops++)
      parent = orgBossKey({kind: 'bot', ...(botRow(parent.slice(2)) || {})});
    if (parent.startsWith('b:') && !keys.has(parent)) parent = e.owner_id || e.users?.[0]?.id ? 'p:' + (e.owner_id || e.users[0].id) : '';
    put(orgHang('b:' + e.id, parent, groupOf), {kind: 'bot', ...e});
  }
  return {byParent: orgTreeWithHelpers(byParent)};
}
// The org chart's order: a group's humans and bots first, then the groups in it; a manager's reports in the order
// someone put them; then the chart's own order (chartFallback).
function goalTreeOrder(parent) {
  const placed = byReportOrder(parent);
  return (a, b) => {
    if ((a.kind === 'group') !== (b.kind === 'group')) return a.kind === 'group' ? 1 : -1;
    if (a.kind === 'group') return (a.order || 0) - (b.order || 0);
    if (placed(a, b)) return placed(a, b);
    return chartFallback(a, b);
  };
}
const GOAL_WORST = ['red', 'yellow', 'green', 'gray', 'none'];
// A goal's KPIs as chips: the dot and the latest value. More than three: two and a count. A phone shows the count.
function goalChips(kpis) {
  kpis = (kpis || []).filter(k => !k.archived_at);
  if (!kpis.length) return '';
  const shown = kpis.length > 3 ? kpis.slice(0, 2) : kpis;
  const chip = k => `<span class="gt-chip" data-kpi-chip="${esc(k.id)}" title="${esc(k.name + (k.reason ? ': ' + k.reason : ''))}">${gdot(k.status)}<span class="tnum">${kpiFresh(k) ? esc(kpiNum(k.latest.value, k.unit)) : '–'}</span></span>`;
  const worst = GOAL_WORST.find(s => kpis.some(k => (k.status || 'gray') === s)) || 'gray';
  return shown.map(chip).join('') + (kpis.length > shown.length ? `<span class="gt-kmore">+${kpis.length - shown.length}</span>` : '')
    + `<span class="gt-kn" title="${kpis.length} KPI${kpis.length === 1 ? '' : 's'}">${gdot(worst)}${kpis.length}</span>`;
}
function goalStatusNote(g) {
  const byHand = g.status_source === 'person' && ['red', 'yellow', 'green'].includes(g.status);
  return byHand ? `Set by ${goalActorName(g.status_by_id)}${g.status_note ? ': ' + g.status_note : ''}` : g.status_note || '';
}
function goalTreeRows(actor, depth, goals, loose, standing = '') {
  const info = goalOwnerInfo(actor);
  const av = actor === GOAL_COMPANY ? `<span class="av gt-co-av" aria-hidden="true">${esc(String(info.name || 'C').trim()[0] || 'C').toUpperCase()}</span>` : info.avatar;
  const bet = g => { const st = GOALS_ST?.strategy, c = g.bet_id && (st?.bets || []).find(x => x.id === g.bet_id); return c ? ` · ${st.label_one || 'Bet'}: ${c.statement}` : ''; };
  const goalCell = g => `<span class="gt-goal" title="${esc(g.title + (g.why || !bet(g) ? ' · Why: ' + (g.why || GOAL_NOT_TIED) : '') + bet(g))}">${gdot(g.status, goalStatusNote(g))}<span class="gt-title">${esc(g.title)}</span></span>`;
  // "+" adds a goal for this owner (a pointer's hover; a long press on a phone; the header's "+ Goal" for a keyboard).
  const add = goalMayEdit({owner: actor}, GOALS_ST?.goals || []) ? `<span class="gt-add" data-goal-add="${esc(actor)}" aria-hidden="true" title="Add goal">+</span>` : '';
  const row = (cls, goal, inner) => `<li class="gt-row${cls}" data-owner="${esc(actor)}"${goal ? ` data-goal="${esc(goal.id)}"` : ''} style="--d:${depth}" tabindex="0" role="button">`
    + `<span class="gt-ind" aria-hidden="true"></span>${inner}${cls.includes('gt-cont') ? '' : add}</li>`;
  const first = goals[0], words = String(standing || '').trim();
  const lead = first ? goalCell(first) + `<span class="gt-kpis">${goalChips(first.kpis)}</span>`
    : (words ? `<span class="gt-goal gt-standing" title="${esc(words)}"><span class="gt-title">${esc(words)}</span></span>` : '')
      + (loose.length ? `<span class="gt-kpis">${goalChips(loose)}</span>` : '');
  return row(actor === GOAL_COMPANY ? ' gt-co' : '', first, `<span class="gt-av">${av}</span><span class="gt-name"><span>${esc(info.name)}</span></span>${lead}`)
    + goals.slice(1).map(g => row(' gt-cont', g, goalCell(g) + `<span class="gt-kpis">${goalChips(g.kpis)}</span>`)).join('');
}
// ---------------------------------------------------------------- the strategy
// The bet a goal serves, as a short chip ("Bet 2") linked to it on Strategy, with its statement beside it.
function goalBetLineHtml(state, g) {
  const st = state.strategy, bets = st?.bets || [], c = g.bet_id && bets.find(x => x.id === g.bet_id);
  if (!c) return '';
  const live = bets.filter(x => x.status !== 'retired').sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
  const n = live.findIndex(x => x.id === c.id), one = st.label_one || 'Bet';
  return `<div class="goal-bet-line" data-goal-bet="${esc(c.id)}"><a class="goal-bet-chip" href="${STRATEGY}?bet=${encodeURIComponent(c.id)}" title="${esc(c.statement)}">${esc(one)}${n >= 0 ? ' ' + (n + 1) : ''}</a>
    <span class="goal-bet-st">${esc(c.statement)}</span>${c.status === 'retired' ? ' <span class="muted">· retired</span>' : ''}</div>`;
}

// ---------------------------------------------------------------- a goal's Why
// One line tying a goal to the vision (backend/company.py). The Strategy Bot proposes it; a person who may edit the
// goal
// accepts it, edits it or writes their own. A goal with none is "Not tied to the vision": a note, not an error.
const GOAL_NOT_TIED = 'Not tied to the vision';
const goalWhyProposal = (state, g) => state.proposals.find(p => p.kind === 'goal_why' && p.goal_id === g.id);
function goalWhyHtml(state, g, may) {
  const P = state.panel, p = goalWhyProposal(state, g), why = String(g.why || '');
  const editing = P?.why === g.id ? (P.whyFrom || 'own') : '';
  if (editing) {
    const start = P.whyDraft ?? (editing === 'proposal' && p ? p.payload?.why || '' : why);
    return `<form class="goal-why-ed" data-why-form="${esc(g.id)}" data-why-from="${esc(editing)}"${p ? ` data-why-prop="${esc(p.id)}"` : ''} novalidate>
        <input type="text" name="why" maxlength="200" autocomplete="off" value="${esc(start)}" aria-label="Why: how this goal serves the vision" placeholder="One line: how this goal serves the vision">
        <button class="primary" type="submit">Save</button><button class="ghost" type="button" data-why-cancel>Cancel</button></form>`;
  }
  // A goal that serves a bet needs no Why of its own: the bet is the why (its line follows).
  if (!why && !p && g.bet_id && (state.strategy?.bets || []).some(c => c.id === g.bet_id)) return '';
  const line = why
    ? `<div class="goal-why" data-why="${esc(g.id)}"><span class="goal-why-k">Why</span> <span data-why-text>${esc(why)}</span>${may ? ` <button class="linkish" type="button" data-why-edit="${esc(g.id)}">Edit</button>` : ''}</div>`
    : `<div class="goal-why goal-why-none" data-why="${esc(g.id)}"><span data-why-none>${GOAL_NOT_TIED}</span>${may && !p ? ` <button class="linkish" type="button" data-why-edit="${esc(g.id)}">Write a Why</button>` : ''}</div>`;
  const proposed = p ? `<div class="goal-why-prop" data-why-prop="${esc(p.id)}"><span class="kpi-prop-main"><span><b>${esc(goalActorName(p.proposed_by))} proposes</b> <span data-why-proposed>${esc(p.payload?.why || '')}</span></span>${p.reason ? `<span class="muted kpi-prop-why">${esc(p.reason)}</span>` : ''}</span>
      ${p.may_decide ? `<span class="kpi-prop-do"><button class="primary" type="button" data-why-accept>Accept</button><button class="ghost" type="button" data-why-edit="${esc(g.id)}" data-why-from="proposal">Edit</button><button class="ghost" type="button" data-why-reject>Reject</button></span>` : ''}</div>` : '';
  return line + proposed;
}
function bindGoalWhy(state, root, reload) {
  const P = state.panel, redraw = () => goalPanelRender(state);
  for (const b of root.querySelectorAll('[data-why-edit]')) b.onclick = () => { P.why = b.dataset.whyEdit; P.whyFrom = b.dataset.whyFrom || 'own'; P.whyDraft = null; redraw(); };
  const decide = async (b, body) => {
    const id = b.closest('[data-why-prop]').dataset.whyProp;
    b.disabled = true;
    // Refused (the Strategy Bot replaced the line meanwhile): draw the panel again, so they read the line now waiting.
    try { await post(`/v2/proposals/${encodeURIComponent(id)}/decide`, body); await reload(); }
    catch (e) { toast(e.message || 'Could not decide', true); b.disabled = false; await reload(); }
  };
  for (const b of root.querySelectorAll('[data-why-accept]')) b.onclick = () => decide(b, {decision: 'confirm'});
  for (const b of root.querySelectorAll('[data-why-reject]')) b.onclick = () => decide(b, {decision: 'reject'});
  const form = root.querySelector('[data-why-form]');
  if (!form) return;
  const input = form.elements.why, done = () => { P.why = ''; P.whyFrom = ''; P.whyDraft = null; redraw(); };
  form.querySelector('[data-why-cancel]').onclick = done;
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const text = input.value.trim(), gid = form.dataset.whyForm;
    if (P.whySaving) return;
    P.whySaving = true;
    try {
      if (form.dataset.whyFrom === 'proposal' && form.dataset.whyProp) {
        if (!text) { toast('Write the Why, or reject the proposal', true); P.whySaving = false; return; }
        await post(`/v2/proposals/${encodeURIComponent(form.dataset.whyProp)}/decide`, {decision: 'confirm', why: text});
      } else await post(`/v2/goals/${encodeURIComponent(gid)}/why`, {why: text});
      P.why = ''; P.whyFrom = ''; P.whyDraft = null;
      await reload();
    } catch (e) {
      // Refused: what they typed stays in the form, which is drawn again against whatever is waiting now.
      toast(e.message || 'Could not save the Why', true);
      P.whyDraft = text;
      await reload();
    } finally { P.whySaving = false; }
  };
  input.focus();
}

// What the tree is drawn from in the roster, so a refresh redraws it only when that changed (goalsFollowRoster).
const goalTreeKey = () => JSON.stringify([
  (S.emps || []).map(e => [e.id, e.slug, e.display_name, e.org_parent, e.team, e.status]),
  (S.people || []).map(p => [p.id, p.org_parent, p.team, p.hidden]), (S.orgGroups || []).map(g => [g.id, g.parent, g.org_parent])]);
// The tree's lines (goals.css, tree-lines.css). The rows are one flat list, each with its depth, so whether a line
// runs on past a row depends on the rows after it: read bottom up, open[l] says a row at depth l is still to come
// before the tree climbs above it. A row at depth d hangs from the last row above it at depth d - 1 (its manager, or a
// group), turns its elbow into its own avatar, and drops a stub from its avatar when its reports follow; a further
// goal of the same owner (gt-cont) only carries the lines past it, its owner's reports' among them.
function goalTreeLines(tree) {
  const rows = [...tree.children], depth = li => Number(li.style.getPropertyValue('--d')) || 0;
  const cont = li => li.classList.contains('gt-cont');
  const group = [], under = rows.map(li => {   // top down: is each level's manager a group?
    const was = group.slice();
    if (!cont(li)) group[depth(li) + 1] = li.classList.contains('gt-group');
    return was;
  });
  const open = [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const li = rows[i], d = depth(li), more = cont(li), ind = li.querySelector(':scope>.gt-ind');
    ind.classList.toggle('gt-elbow', !more && d > 0);
    ind.classList.toggle('gt-gp', !more && d > 0 && !!under[i][d]);
    ind.classList.toggle('gt-kids', !more && !!open[d + 1]);
    let rails = '';
    for (let l = 1; l <= (more ? d + 1 : d); l++) {
      if (open[l]) rails += `<i class="gt-rail${under[i][l] ? ' gt-gp' : ''}" style="--l:${l}" aria-hidden="true"></i>`;
    }
    ind.insertAdjacentHTML('afterend', rails);
    if (!more) { open.length = d + 1; open[d] = true; }
  }
}
function goalsRender(state) {
  const body = $('#goal-body');
  if (!body || !state.loaded) return;
  state.shape = goalTreeKey();
  const owned = {}, loose = {};
  for (const g of state.goals.filter(goalLive)) (owned[g.owner_id] ||= []).push(g);
  for (const list of Object.values(owned)) list.sort(goalRank);
  for (const k of state.other) (loose[k.owner_id] ||= []).push(k);
  const {byParent} = goalTreeShape();
  const seen = new Set([GOAL_COMPANY]);
  const rows = (actor, depth, standing) => { seen.add(actor); return goalTreeRows(actor, depth, owned[actor] || [], loose[actor] || [], standing); };
  const walk = (parent, depth, path) => (byParent[parent] || []).slice().sort(goalTreeOrder(parent)).map(n => {
    const key = n.kind === 'group' ? 'g:' + n.id : n.kind === 'person' ? 'p:' + n.id : 'b:' + n.id;
    if (path.has(key)) return '';
    const below = walk(key, depth + 1, new Set([...path, key]));
    if (n.kind === 'group') {
      const add = goalGroupOwner(n.id) ? `<span class="gt-add" data-goal-add-group="${esc(n.id)}" aria-hidden="true" title="Add goal">+</span>` : '';
      return below ? `<li class="gt-group" data-group="${esc(n.id)}" style="--d:${depth}"><span class="gt-ind" aria-hidden="true"></span><span class="gt-name"><span>${esc(n.name)}</span></span>${add}</li>${below}` : '';
    }
    return rows(n.kind === 'person' ? 'human:' + n.id : 'bot:' + n.id, depth, n.kind === 'person' ? n.person.goals : '') + below;
  }).join('');
  const tree = goalTreeRows(GOAL_COMPANY, 0, owned[GOAL_COMPANY] || [], loose[GOAL_COMPANY] || []) + walk('', 0, new Set());
  // Helpers are off the chart here (as on the Org tab); one with goals still shows below, with anyone else who has
  // goals (someone gone from the roster), at the end.
  const rest = Object.keys(owned).filter(a => !seen.has(a)).map(a => rows(a, 0)).join('');
  body.innerHTML = goalNeedsHtml(state.needs) + `<ul class="gt" id="goal-tree" aria-label="Goals">${tree}${rest}</ul>`;
  goalTreeLines(body.querySelector('#goal-tree'));
  // Tapping a goal opens it on its own (goalSheetOpen); the name, a line with no goal, or Enter opens the owner's panel.
  for (const li of body.querySelectorAll('.gt-row')) {
    const open = () => goalPanelOpen(state, li.dataset.owner, {tapped: li.dataset.goal || ''});
    li.onclick = ev => {
      if (goalPressed(li)) return;
      if (ev.target.closest('[data-goal-add]')) goalSheetOpen(state, {owner: li.dataset.owner});
      else if (li.dataset.goal && ev.target.closest('.gt-goal')) goalSheetOpen(state, {goal: li.dataset.goal});
      else open();
    };
    li.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } };
    if (li.querySelector('[data-goal-add]')) goalLongPress(li, () => goalSheetOpen(state, {owner: li.dataset.owner}));
  }
  for (const li of body.querySelectorAll('.gt-group')) {
    const plus = li.querySelector('[data-goal-add-group]');
    if (!plus) continue;
    const add = () => goalSheetOpen(state, {owner: goalGroupOwner(li.dataset.group)});
    plus.onclick = add;
    goalLongPress(li, add);
  }
  const needs = body.querySelector('#goal-needs');
  if (needs) {
    bindKpiLines(needs); bindProposals(needs, () => goalsReload(state));
    const vision = () => { location.hash = STRATEGY; };
    const why = id => {
      const goal = (state.goals || []).find(g => g.id === id);
      if (goal) goalPanelOpen(state, goal.owner_id, {tapped: goal.id});
    };
    for (const li of needs.querySelectorAll('[data-need-vision], [data-need-why]')) {
      const open = () => li.dataset.needWhy !== undefined ? why(li.dataset.needWhy) : vision();
      li.onclick = open;
      li.onkeydown = ev => { if (ev.target === li && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); open(); } };
    }
    for (const b of needs.querySelectorAll('[data-need-why-goal]')) b.onclick = ev => { ev.stopPropagation(); why(b.dataset.needWhyGoal); };
    // The task link opens the task, not the KPI its row opens.
    for (const a of needs.querySelectorAll('[data-need-task]')) { a.onclick = ev => ev.stopPropagation(); a.onkeydown = ev => ev.stopPropagation(); }
  }
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
      const goal = st.panel?.goal;
      st.panel = null;
      // Only the address this panel set: a link followed before the event arrives names another goal and stays.
      if (st === GOALS_ST && goal && location.hash === goalsBase() + '/' + encodeURIComponent(goal)) history.replaceState(null, '', goalsBase());
    });
    document.body.appendChild(dlg);
  }
  dlg._state = state;
  return dlg;
}
window.addEventListener('hashchange', () => {
  const dlg = document.getElementById('goal-panel');
  if (dlg?.open && !location.hash.startsWith(goalsBase())) dlg.close();
});
// goal: the goal to open for editing ('new' to add one). tapped: the goal on the line that was tapped. Someone with no
// goal opens on a new one; the team line opens its goal when it has just one.
function goalPanelOpen(state, owner, {goal = '', tapped = ''} = {}) {
  const live = state.goals.filter(g => goalLive(g) && g.owner_id === owner);
  if (!goal && !live.length && goalMayEdit({owner}, state.goals)) goal = 'new';
  if (!goal && owner === GOAL_COMPANY && live.length === 1) goal = live[0].id;
  state.panel = {owner, goal, kpiAdd: '', kpi: '', standing: false, tapped, history: false, historyKpis: null,
    historyLoading: false, historyError: false, why: '', whyFrom: '', whyDraft: null, update: '', updateDraft: null, updateError: '', updateFocus: false, updatePending: null};
  history.replaceState(null, '', goal && goal !== 'new' ? goalsBase() + '/' + encodeURIComponent(goal) : goalsBase());
  const dlg = goalPanelDialog(state);
  delete dlg.dataset.kpi;
  if (!dlg.open) dlg.showModal();
  goalPanelRender(state);
}
function goalCheckinHtml(c) {
  return `<blockquote class="kpi-check">${esc(c.body)}<footer class="muted">${esc(goalActorName(c.source_actor))}${c.signal ? ' · ' + esc(c.signal.replace('_', ' ')) : ''} · ${esc(ago(c.ts))}</footer></blockquote>`;
}
// "Post update": the owner's own words on how a goal is going (POST /v2/goals/{id}/checkins), for whoever may colour it.
// The answer colours the goal only when nothing outranks it (backend/goals.py `apply_auto`, `score`): a colour a person
// set sticks, and a KPI with a target and a colour of its own decides; either way the answer is kept only as words.
const GOAL_SIGNALS = [['on_track', 'On track'], ['at_risk', 'At risk'], ['off_track', 'Off track'], ['', 'No answer']];
const GOAL_JUDGED = ['red', 'yellow', 'green'];
function goalUpdateNote(g) {
  if (g.status_source === 'person' && GOAL_JUDGED.includes(g.status)) return 'A person set its colour; this update is kept as your words';
  if ((g.kpis || []).some(k => ['improve', 'maintain'].includes(k.link?.kind) && GOAL_JUDGED.includes(k.status))) return 'Its measures set its colour; this update is kept as your words';
  return '';
}
// While a post is out the form is frozen (words, answer, Cancel): what comes back is about exactly what was sent, and
// the panel may be redrawn meanwhile, so the outcome lands in the panel's state, never in the form that sent it.
function goalUpdateHtml(g, P) {
  const d = P.updateDraft || {body: '', signal: ''}, note = d.signal ? goalUpdateNote(g) : '', busy = P.updatePending ? ' disabled' : '';
  return `<form class="goal-form gp-update" data-gp-update-form="${esc(g.id)}" novalidate${P.updatePending ? ' aria-busy="true"' : ''}>
      <label class="goal-f"><span>What is going on?</span><textarea name="body" rows="3" maxlength="4000" placeholder="In a sentence or two: what happened, and what comes next"${P.updatePending ? ' readonly' : ''}>${esc(d.body)}</textarea></label>
      <fieldset class="gp-signal"${busy}><legend>How is it going?</legend><div class="gp-signal-row">${GOAL_SIGNALS.map(([v, label]) =>
        `<label><input type="radio" name="signal" value="${v}"${v === d.signal ? ' checked' : ''}>${label}</label>`).join('')}</div></fieldset>
      <p class="muted gp-update-note" data-gp-update-note${note ? '' : ' hidden'}>${esc(note)}</p>
      <p class="err" data-gp-update-err role="alert"${P.updateError && !P.updatePending ? '' : ' hidden'}>${esc(P.updateError)}</p>
      <div class="goal-actions"><button class="primary" type="submit"${busy}>${P.updatePending ? 'Posting…' : 'Post'}</button><button class="ghost" type="button" data-gp-update-cancel${busy}>Cancel</button></div>
    </form>`;
}
function goalPanelHtml(state) {
  const P = state.panel, actor = P.owner, info = goalOwnerInfo(actor);
  const goals = state.goals.filter(g => goalLive(g) && g.owner_id === actor).sort(goalRank);
  const may = goalMayEdit({owner: actor}, state.goals);
  const other = state.other.filter(k => k.owner_id === actor);
  const finished = goalFinishedOf(state.goals, actor).length;
  const person = actor.startsWith('human:') ? (S.people || []).find(p => p.id === actor.slice(6)) : null;
  const standing = String(person?.goals || '').trim();
  const av = actor === GOAL_COMPANY ? `<span class="av gt-co-av" aria-hidden="true">${esc((String(info.name || 'C').trim()[0] || 'C').toUpperCase())}</span>` : info.avatar;
  const item = g => {
    const mayG = goalMayEdit(g, state.goals);
    // The Why stays in view while the goal is being edited: beside its form, not in it, as it saves on its own.
    const head = (P.goal === g.id ? goalFormHtml(g, state.goals)
      : `<button class="gp-gline" type="button" data-gp-edit="${esc(g.id)}" title="${esc(g.title)}">${gdot(g.status, goalStatusNote(g))}<span>${esc(g.title)}</span></button>${goalNoteHtml(g, mayG)}`)
      + `<div class="goal-why-slot" data-why-slot="${esc(g.id)}">${goalWhyHtml(state, g, mayG)}</div>` + (P.goal === g.id ? '' : goalBetLineHtml(state, g));
    const props = state.proposals.filter(p => p.goal_id === g.id && p.kind !== 'goal_why');
    return `<li class="gp-goal" data-gp-goal="${esc(g.id)}">${head}${kpiLinesHtml(g.kpis, {goal: g.id})}
      ${props.length ? `<ul class="kpi-props">${props.map(p => proposalHtml(p)).join('')}</ul>` : ''}
      ${g.checkin ? `<div data-gp-checkins="${esc(g.id)}">${goalCheckinHtml(g.checkin)}</div><button class="linkish" type="button" data-gp-more="${esc(g.id)}">Check-ins</button>` : ''}
      ${mayG ? (P.update === g.id ? goalUpdateHtml(g, P) : `<button class="linkish" type="button" data-gp-update="${esc(g.id)}"${P.updatePending ? ' disabled' : ''}>Post update</button>`) : ''}
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
      ${finished ? '<div data-gp-finished></div>' : ''}${newGoal}${other.length ? `<h3 class="kpi-h">Other KPIs</h3>${kpiLinesHtml(other)}` : ''}${newKpi}${actions}${history}
      ${!goals.length && !profile && !finished && !other.length && !newGoal && !newKpi && !actions ? '<p class="muted">No goals.</p>' : ''}
    </div>`;
}
function goalPanelRender(state) {
  const dlg = document.getElementById('goal-panel'), P = state.panel;
  if (!dlg?.open || !P || P.kpi || dlg._state !== state) return;
  dlg.setAttribute('aria-label', goalOwnerInfo(P.owner).name);
  dlg.dataset.owner = P.owner;
  const scroll = dlg.querySelector('.gp-body')?.scrollTop || 0;
  const toasts = [...dlg.querySelectorAll(':scope > .toast')];   // a toast shown in the panel (Undo) outlives a redraw
  dlg.innerHTML = goalPanelHtml(state);
  dlg.append(...toasts);
  const body = dlg.querySelector('.gp-body');
  body.scrollTop = scroll;
  const redraw = () => goalPanelRender(state), reload = () => goalsReload(state);
  const setGoal = id => { P.goal = id; P.kpiAdd = ''; history.replaceState(null, '', id && id !== 'new' ? goalsBase() + '/' + encodeURIComponent(id) : goalsBase()); redraw(); };
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
    if (g) goalPanelOpen(state, g.owner_id, {goal: g.id});
  };
  for (const b of body.querySelectorAll('button[data-goal-auto]')) if (b.dataset.goalAuto) b.onclick = async () => {
    b.disabled = true;
    try { await post('/v2/goals/' + encodeURIComponent(b.dataset.goalAuto) + '/status/auto', {}); await reload(); }
    catch (e) { toast(e.message || 'Could not hand the colour back', true); b.disabled = false; }
  };
  goalFinishedMount(body.querySelector('[data-gp-finished]'), P.owner, state.goals, reload);
  bindKpiLines(body);
  bindProposals(body, reload);
  bindGoalWhy(state, body, reload);
  for (const b of body.querySelectorAll('[data-gp-kpi-add]')) b.onclick = () => { P.kpiAdd = b.dataset.gpKpiAdd; P.goal = ''; redraw(); };
  const newGoal = body.querySelector('[data-gp-goal-new]'), newKpi = body.querySelector('[data-gp-kpi-new]');
  if (newGoal) newGoal.onclick = () => setGoal('new');
  if (newKpi) newKpi.onclick = () => { P.kpiAdd = '@owner'; P.goal = ''; redraw(); };
  const historyButton = body.querySelector('[data-gp-history]');
  if (historyButton) historyButton.onclick = async () => {
    P.history = !P.history;
    if (!P.history || P.historyKpis !== null) { redraw(); return; }
    P.historyLoading = true;
    P.historyError = false;
    redraw();
    const result = await v2Get(`/v2/kpis?owner=${encodeURIComponent(P.owner)}&include_archived=true`);
    if (state.panel !== P) return;
    P.historyLoading = false;
    P.historyError = !result;
    P.historyKpis = result ? (result.kpis || []).filter(k => k.archived_at && !goalBuiltInOwner(k.owner_id)) : null;
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
  for (const b of body.querySelectorAll('[data-gp-update]')) b.onclick = () => {
    if (P.updatePending) return;
    P.update = b.dataset.gpUpdate; P.updateDraft = {body: '', signal: ''}; P.updateError = ''; P.updateFocus = true; redraw();
  };
  const uform = body.querySelector('[data-gp-update-form]');
  if (uform) {
    const id = uform.dataset.gpUpdateForm, text = uform.elements.body;
    const note = uform.querySelector('[data-gp-update-note]'), err = uform.querySelector('[data-gp-update-err]');
    const signal = () => uform.querySelector('input[name=signal]:checked')?.value || '';
    const keep = () => { if (!P.updatePending) P.updateDraft = {body: text.value, signal: signal()}; };
    const close = () => { if (P.updatePending) return; P.update = ''; P.updateDraft = null; P.updateError = ''; redraw(); };
    text.oninput = keep;
    uform.onchange = () => { keep(); const words = signal() ? goalUpdateNote(state.goals.find(g => g.id === id) || {}) : ''; note.textContent = words; note.hidden = !words; };
    uform.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); close(); } };
    uform.querySelector('[data-gp-update-cancel]').onclick = close;
    if (P.updateFocus) { P.updateFocus = false; text.focus(); uform.scrollIntoView({block: 'nearest'}); }
    uform.onsubmit = async ev => {
      ev.preventDefault();
      if (P.updatePending) return;
      keep();
      const words = text.value.trim(), said = signal();
      if (!words) { P.updateError = err.textContent = 'Say in a sentence what is going on'; err.hidden = false; text.focus(); return; }
      const sent = {goal: id};
      P.updatePending = sent; P.updateError = '';
      redraw();
      const ours = () => state.panel === P && P.updatePending === sent;
      let r;
      try { r = await post(`/v2/goals/${encodeURIComponent(id)}/checkins`, said ? {body: words, signal: said} : {body: words}); }
      catch (e) {
        const msg = e.message || 'Could not post the update';
        if (ours()) { P.updatePending = null; P.updateError = msg; P.updateFocus = true; redraw(); }
        else toast(msg, true);   // the panel was closed meanwhile: its words are gone, so say it where it is still seen
        return;
      }
      // At once the goal's latest, then the tree again for the colour it may have set.
      const g = state.goals.find(x => x.id === id);
      if (g && r?.checkin) g.checkin = r.checkin;
      if (ours()) { P.updatePending = null; P.update = ''; P.updateDraft = null; P.updateError = ''; }
      redraw();
      await reload();
    };
  }
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
// Each item is a Strategy Bot task for the caller (backend/goal_asks.py), the same one Tasks and Decide show: linked.
const needTaskLink = it => it.task ? `<a class="need-task muted" href="#/task/${esc(it.task.id)}" data-need-task>Task</a>` : '';
// The Strategy Bot's recovery for a red or stalled goal (level 2): the evidence, each option in plain words with its
// pick
// marked, one Approve per option and one Reject for them all. A part that can't happen now says why.
function recoveryHtml(it) {
  const p = it.proposal || {}, pl = p.payload || {};
  const options = (it.options || []).map((o, n) => `<li class="rec-opt${o.pick ? ' rec-pick' : ''}"><div class="rec-opt-h"><b>${esc(o.label)}</b>${o.pick ? '<span class="muted"> · recommended</span>' : ''}</div>
    <ul class="rec-acts">${o.actions.map(a => `<li>${esc(a.text)}${a.note ? `<span class="muted"> · ${esc(a.note)}; this part would come back to you</span>` : ''}</li>`).join('')}</ul>
    <button class="${o.pick ? 'primary' : 'ghost'}" type="button" data-decide="confirm" data-option="${n}">Approve${(it.options || []).length > 1 ? ` option ${n + 1}` : ''}</button></li>`).join('');
  return `<li class="kpi-prop rec" data-proposal="${esc(p.id)}"><div class="kpi-prop-main"><span><b>A recovery</b> <span class="muted">· ${esc(it.goal_title || '')}</span></span>
    <span class="kpi-prop-detail">${esc(pl.evidence || '')}</span>${p.reason ? `<span class="muted kpi-prop-why">${esc(p.reason)}</span>` : ''}
    <ol class="rec-opts">${options}</ol></div>
    <div class="kpi-prop-do"><button class="ghost" type="button" data-decide="reject">Reject</button></div>${needTaskLink(it)}</li>`;
}
function goalNeedsHtml(items) {
  if (!items?.length) return '';
  const rows = items.map(it => {
    if (it.kind === 'recovery') return recoveryHtml(it);
    if (it.kind === 'proposal') return proposalHtml({...it.proposal, may_decide: true}, it.kpi_name || it.goal_title || '').replace(/<\/li>$/, `${needTaskLink(it)}</li>`);
    // Where the Strategy Bot is on: the vision, written on Strategy, and the Why lines waiting on this
    // person, one row for all of them, each goal opening its panel where the Why is accepted or edited.
    if (it.kind === 'vision') return `<li class="need-row" data-need-vision tabindex="0" role="button">${gdot('gray')}<span class="need-text"><b>Say what this company is for</b><span class="muted">: write the summary on Strategy</span></span>${needTaskLink(it)}</li>`;
    if (it.kind === 'why') {
      const goals = it.goals || [];
      return `<li class="need-row" data-need-why="${esc(goals[0]?.goal_id || '')}" tabindex="0" role="button">${gdot('gray')}<span class="need-text"><b>These goals need a Why line</b><span class="muted">: </span>${goals.map(g =>
        `<button class="linkish" type="button" data-need-why-goal="${esc(g.goal_id)}">${esc(g.goal_title)}</button>`).join('<span class="muted">, </span>')}</span>${needTaskLink(it)}</li>`;
    }
    const red = it.kind === 'kpi_red';
    return `<li class="need-row" data-kpi="${esc(it.kpi_id)}" tabindex="0" role="button">${gdot(red ? 'red' : 'gray')}<span class="need-text"><b>${esc(it.kpi_name)}</b>${red ? ` on ${esc(it.goal_title)}` : ''}<span class="muted">: ${esc(it.reason)}</span></span>${needTaskLink(it)}</li>`;
  }).join('');
  return `<section class="gt-needs" id="goal-needs" aria-label="Needs you"><div class="gt-needs-h">Needs you<span class="cnt tnum">${items.length}</span></div><ul>${rows}</ul></section>`;
}
