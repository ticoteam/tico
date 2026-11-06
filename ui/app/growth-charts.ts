/* ui/app/growth-charts.ts — The Growth cards' small trend lines (ui/app/growth-page.js), hand-written SVG in the app's
   tokens. A null value is unknown: the line breaks there and is never drawn as 0.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/growth-charts.js, which the page runs in this file's place. */
'use strict';

// One reading as the API sends it: a number, a numeric string, or null/absent for unknown.
type GrValue = number | string | null | undefined;
// A point on the chart, in SVG pixels.
type GrPoint = [x: number, y: number];

// A compact number: in full under 10,000.
function grShort(v: number | string): string {
  const n = Number(v), a = Math.abs(n);
  if (a < 1e4) return n.toLocaleString('en-US', {maximumFractionDigits: a < 10 ? 1 : 0});
  return a < 1e6 ? `${+(n / 1e3).toFixed(a < 1e5 ? 1 : 0)}K` : `${+(n / 1e6).toFixed(1)}M`;
}
// A run of consecutive known points: never empty, so its first point is always there.
type GrRun = [GrPoint, ...GrPoint[]];
// The line through known values as runs of consecutive points, each [x, y] from `at(i, v)`; a null ends a run.
function grRuns(values: readonly GrValue[], at: (i: number, v: number | string) => GrPoint): GrRun[] {
  const runs: GrRun[] = [];
  let run: GrRun | null = null;
  values.forEach((v, i) => {
    if (v == null) { run = null; return; }
    const point = at(i, v);
    if (run) run.push(point);
    else { run = [point]; runs.push(run); }
  });
  return runs;
}
const grPath = (run: readonly GrPoint[]): string => run.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('');

// A trend: `values` oldest first with nulls for unknown, the dot on index `pick` (none when it is unknown or -1), drawn
// `w` by `h`. Under two known values, none.
function grSpark(values: readonly GrValue[], pick: number = values.length - 1,
                 {w = 120, h = 32}: {w?: number, h?: number} = {}): string {
  const known = values.filter(v => v != null).map(Number);
  if (known.length < 2) return '';
  const P = h < 30 ? 3 : 4, lo = Math.min(...known), span = Math.max(...known) - lo;
  const at = (i: number, v: number | string): GrPoint =>
    [P + i * (w - 2 * P) / Math.max(1, values.length - 1), span ? h - P - (Number(v) - lo) / span * (h - 2 * P) : h / 2];
  const runs = grRuns(values, at);
  const picked = values[pick];
  const dot = picked == null ? null : at(pick, picked);
  return `<svg class="gr-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${runs.map(r => r.length > 1
    ? `<path d="${grPath(r)}"/>` : `<circle class="gr-spark-one" cx="${r[0][0].toFixed(1)}" cy="${r[0][1].toFixed(1)}" r="1.5"/>`).join('')}
    ${dot ? `<circle class="gr-spark-dot" cx="${dot[0].toFixed(1)}" cy="${dot[1].toFixed(1)}" r="${h < 30 ? 2.5 : 3.5}"/>` : ''}</svg>`;
}
