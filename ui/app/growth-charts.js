/* ui/app/growth-charts.js — The Growth page's charts, hand-written SVG in the app's tokens. grSpark is a stat's small
   trend line; grChartMount draws the larger line into its host (the host's CSS sets the height) with a crosshair and
   ticks on the time axis for marketing tasks, whose titles show on hover, tap or focus, and redraws when the host's width
   changes. A null value is unknown: the line breaks there and is never drawn as 0.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GR_TICK_MERGE = 10;    // px: task ticks closer than this are one tick that lists them all
const GR_TICK_TITLES = 3;    // titles a merged tick lists before "+N"

// A compact axis number: in full under 10,000.
function grShort(v) {
  const n = Number(v), a = Math.abs(n);
  if (a < 1e4) return n.toLocaleString('en-US', {maximumFractionDigits: a < 10 ? 1 : 0});
  return a < 1e6 ? `${+(n / 1e3).toFixed(a < 1e5 ? 1 : 0)}K` : `${+(n / 1e6).toFixed(1)}M`;
}
// The line through known values as runs of consecutive points, each [x, y] from `at(i, v)`; a null ends a run.
function grRuns(values, at) {
  const runs = [];
  let run = null;
  values.forEach((v, i) => {
    if (v == null) { run = null; return; }
    if (!run) runs.push(run = []);
    run.push(at(i, v));
  });
  return runs;
}
const grPath = run => run.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('');

// A stat's trend: `values` oldest first with nulls for unknown, the dot on index `pick`. Under two known values, none.
function grSpark(values, pick = values.length - 1) {
  const known = values.filter(v => v != null).map(Number);
  if (known.length < 2) return '';
  const W = 120, H = 32, P = 4, lo = Math.min(...known), span = Math.max(...known) - lo;
  const at = (i, v) => [P + i * (W - 2 * P) / Math.max(1, values.length - 1), span ? H - P - (Number(v) - lo) / span * (H - 2 * P) : H / 2];
  const runs = grRuns(values, at);
  const dot = values[pick] == null ? null : at(pick, values[pick]);
  return `<svg class="gr-spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${runs.map(r => r.length > 1
    ? `<path d="${grPath(r)}"/>` : `<circle class="gr-spark-one" cx="${r[0][0].toFixed(1)}" cy="${r[0][1].toFixed(1)}" r="1.5"/>`).join('')}
    ${dot ? `<circle class="gr-spark-dot" cx="${dot[0].toFixed(1)}" cy="${dot[1].toFixed(1)}" r="3.5"/>` : ''}</svg>`;
}

// Clean gridline values over lo..hi. Near zero, the scale starts at zero.
function grScale(lo, hi) {
  if (lo === hi) { const pad = Math.abs(lo) * .1 || 1; lo -= pad; hi += pad; }
  if (lo >= 0 && lo < hi * .4) lo = 0;
  const raw = (hi - lo) / 3, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const from = Math.floor(lo / step + 1e-9) * step, to = Math.ceil(hi / step - 1e-9) * step, lines = [];
  for (let v = from; v <= to + step / 2; v += step) lines.push(+v.toPrecision(12));
  return lines;
}

/* spec: {points: [{x, value, label}], domain: [x0, x1], axis: [{x, label}], ticks: [{x, title, when}], pick, fmt}.
   `value` is null where unknown; `pick` is the index of the point the dot sits on. */
function grChartMount(host, spec) {
  host.grSpec = spec;
  grChartDraw(host);
  if (host.grBound) return;
  host.grBound = true;
  let width = host.clientWidth;
  new ResizeObserver(() => { if (host.clientWidth !== width) { width = host.clientWidth; grChartDraw(host); } }).observe(host);
  const tickOf = t => t.closest?.('[data-gr-tick]');
  // A tapped tick's title stays until the next tap: on that tick, elsewhere on the chart, or off it (grChartOutside).
  host.addEventListener('pointermove', ev => { if (host.grPin == null && !tickOf(ev.target)) grChartAim(host, ev.clientX); });
  host.addEventListener('pointerdown', ev => {
    if (tickOf(ev.target)) return;
    if (host.grPin != null) grChartClear(host); else grChartAim(host, ev.clientX);
  });
  host.addEventListener('pointerleave', ev => { if (ev.pointerType !== 'touch' && host.grPin == null) grChartClear(host); });
  host.addEventListener('keydown', ev => {
    if (ev.target !== host || !['ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
    ev.preventDefault();
    host.grPin = null;
    const known = host.grGeo.pts, step = ev.key === 'ArrowLeft' ? -1 : 1;
    const now = host.grAim == null ? known.length - 1 : known.indexOf(host.grAim) + step;
    grChartShow(host, known[Math.max(0, Math.min(known.length - 1, now))]);
  });
  host.addEventListener('focusout', ev => { if (!host.contains(ev.relatedTarget)) grChartClear(host); });
  for (const name of ['pointerover', 'focusin']) host.addEventListener(name, ev => {
    const b = tickOf(ev.target);
    if (b && host.grPin == null) grChartTick(host, b);
  });
  host.addEventListener('click', ev => {
    const b = tickOf(ev.target);
    if (!b) return;
    const at = Number(b.dataset.grTick);
    if (host.grPin === at) return grChartClear(host);
    grChartTick(host, b);
    host.grPin = at;
  });
}
function grChartOutside(ev) {
  document.querySelectorAll('.gr-chart').forEach(host => { if (host.grBound && !host.contains(ev.target)) grChartClear(host); });
}
document.addEventListener('pointerdown', grChartOutside, true);
function grChartDraw(host) {
  const s = host.grSpec, W = host.clientWidth, H = host.clientHeight;
  if (!W || !H) return;
  const T = 18, B = 40, L = 2, R = 8, base = H - B;
  const known = s.points.map(p => p.value).filter(v => v != null).map(Number);
  const lines = grScale(Math.min(...known), Math.max(...known));
  const lo = lines[0], hi = lines.at(-1), [x0, x1] = s.domain;
  const fx = x => L + (x - x0) / (x1 - x0 || 1) * (W - L - R), fy = v => base - (Number(v) - lo) / (hi - lo) * (base - T);
  const runs = grRuns(s.points.map(p => p.value), (i, v) => [fx(s.points[i].x), fy(v), i]);
  const pick = s.points[s.pick]?.value == null ? null : [fx(s.points[s.pick].x), fy(s.points[s.pick].value)];
  const grid = lines.map(v => `<line class="gr-grid-l" x1="0" x2="${W}" y1="${fy(v).toFixed(1)}" y2="${fy(v).toFixed(1)}"/>
    <text class="gr-ax" x="0" y="${(fy(v) - 5).toFixed(1)}">${esc(grShort(v))}</text>`).join('');
  const area = run => `${grPath(run)}L${run.at(-1)[0].toFixed(1)} ${base}L${run[0][0].toFixed(1)} ${base}Z`;
  const axis = s.axis.map((a, i) => {
    const anchor = i === 0 ? 'start' : i === s.axis.length - 1 ? 'end' : 'middle';
    const x = i === 0 ? 0 : i === s.axis.length - 1 ? W : fx(a.x);
    return `<text class="gr-ax" x="${x.toFixed(1)}" y="${H - 4}" text-anchor="${anchor}">${esc(a.label)}</text>`;
  }).join('');
  // Task ticks on the time axis, the near ones merged so each keeps a target a finger can hit.
  const groups = [];
  for (const t of (s.ticks || []).filter(t => t.x >= x0 && t.x <= x1).sort((a, b) => a.x - b.x)) {
    const x = fx(t.x), last = groups.at(-1);
    if (last && x - last.x < GR_TICK_MERGE) last.list.push(t); else groups.push({x, list: [t]});
  }
  host.grTicks = groups;
  const ticks = groups.map((g, i) => `<button type="button" class="gr-tick" data-gr-tick="${i}" style="left:${g.x.toFixed(1)}px;top:${base}px"
    aria-label="${esc(g.list.map(t => `${t.when}: ${t.title}`).join('; '))}"><i></i></button>`).join('');
  host.innerHTML = `<svg class="gr-chart-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${grid}
      ${runs.map(r => r.length > 1 ? `<path class="gr-area" d="${area(r)}"/><path class="gr-line" d="${grPath(r)}"/>`
    : `<circle class="gr-one" cx="${r[0][0].toFixed(1)}" cy="${r[0][1].toFixed(1)}" r="3"/>`).join('')}
      ${pick ? `<circle class="gr-end" cx="${pick[0].toFixed(1)}" cy="${pick[1].toFixed(1)}" r="4.5"/>` : ''}
      <line class="gr-cross" y1="${T}" y2="${base}" hidden/><circle class="gr-cross-dot" r="4.5" hidden/>${axis}</svg>
    ${ticks}<div class="gr-chart-tip" hidden></div>`;
  host.grGeo = {pts: runs.flat().map(([x, y, i]) => ({x, y, i})), W};
  host.grAim = null;
  host.grPin = null;
}
function grChartAim(host, clientX) {
  const geo = host.grGeo;
  if (!geo?.pts.length) return;
  const x = clientX - host.getBoundingClientRect().left;
  grChartShow(host, geo.pts.reduce((a, b) => Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a));
}
function grChartShow(host, pt) {
  host.grAim = pt;
  const p = host.grSpec.points[pt.i], line = host.querySelector('.gr-cross'), dot = host.querySelector('.gr-cross-dot');
  for (const a of ['x1', 'x2']) line.setAttribute(a, pt.x.toFixed(1));
  dot.setAttribute('cx', pt.x.toFixed(1)); dot.setAttribute('cy', pt.y.toFixed(1));
  line.removeAttribute('hidden'); dot.removeAttribute('hidden');
  grChartTip(host, pt.x, `<span class="muted">${esc(p.label)}</span> <b>${esc(host.grSpec.fmt(p.value))}</b>`);
}
function grChartTick(host, button) {
  const g = host.grTicks[Number(button.dataset.grTick)];
  if (!g) return;
  grChartClearCross(host);
  const more = g.list.length - GR_TICK_TITLES;
  grChartTip(host, g.x, g.list.slice(0, GR_TICK_TITLES).map(t => `<div><span class="muted">${esc(t.when)}</span> ${esc(t.title)}</div>`).join('')
    + (more > 0 ? `<div class="muted">+${more}</div>` : ''));
}
function grChartTip(host, x, html) {
  const tip = host.querySelector('.gr-chart-tip');
  tip.innerHTML = html;
  tip.hidden = false;
  tip.style.left = Math.max(0, Math.min(host.clientWidth - tip.offsetWidth, x - tip.offsetWidth / 2)) + 'px';
}
function grChartClearCross(host) {
  host.grAim = null;
  host.querySelectorAll('.gr-cross,.gr-cross-dot').forEach(el => el.setAttribute('hidden', ''));
}
function grChartClear(host) {
  host.grPin = null;
  grChartClearCross(host);
  const tip = host.querySelector('.gr-chart-tip');
  if (tip) tip.hidden = true;
}
