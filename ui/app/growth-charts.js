/* ui/app/growth-charts.js — The Growth cards' small trend lines (ui/app/growth-page.js), hand-written SVG in the app's
   tokens. A null value is unknown: the line breaks there and is never drawn as 0.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A compact number: in full under 10,000.
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

// A trend: `values` oldest first with nulls for unknown, the dot on index `pick` (none when it is unknown or -1), drawn
// `w` by `h`. Under two known values, none.
function grSpark(values, pick = values.length - 1, {w = 120, h = 32} = {}) {
  const known = values.filter(v => v != null).map(Number);
  if (known.length < 2) return '';
  const P = h < 30 ? 3 : 4, lo = Math.min(...known), span = Math.max(...known) - lo;
  const at = (i, v) => [P + i * (w - 2 * P) / Math.max(1, values.length - 1), span ? h - P - (Number(v) - lo) / span * (h - 2 * P) : h / 2];
  const runs = grRuns(values, at);
  const dot = values[pick] == null ? null : at(pick, values[pick]);
  return `<svg class="gr-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${runs.map(r => r.length > 1
    ? `<path d="${grPath(r)}"/>` : `<circle class="gr-spark-one" cx="${r[0][0].toFixed(1)}" cy="${r[0][1].toFixed(1)}" r="1.5"/>`).join('')}
    ${dot ? `<circle class="gr-spark-dot" cx="${dot[0].toFixed(1)}" cy="${dot[1].toFixed(1)}" r="${h < 30 ? 2.5 : 3.5}"/>` : ''}</svg>`;
}
