/* ui/app/computer-performance.js — a computer's performance (#/computers/{id}/performance): CPU and memory over the
   last day, week or 14 days, the overloads the server recorded, the turns that were active, and the busiest processes
   and bots. GET /api/v2/computers/{id}/performance; its operator or an admin turns it off or deletes the history.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PERF_WINDOWS = [['24', '1 day'], ['168', '7 days'], ['336', '14 days']];
const PERF_SERIES = [['cpu', 1, 'CPU'], ['mem', 3, 'Memory']];   // [class, column in a point, label], in legend order
const PERF_REASONS = {cpu: 'CPU over 90%', memory: 'memory over 90%', swap: 'swapping', disk: 'disk nearly full'};
let PERF = null;

const performanceHref = id => `#/computers/${encodeURIComponent(id)}/performance`;
const perfPct = v => v == null ? '–' : `${Math.round(v)}%`;
const perfBot = slug => !slug ? 'another bot' : typeof botDisplayName === 'function' ? botDisplayName(slug) : slug;
const perfWhen = ts => fmt(new Date(ts * 1000).toISOString());
const perfMinutes = n => n < 60 ? `${n} min` : `${Math.floor(n / 60)} h ${n % 60 ? `${n % 60} min` : ''}`.trim();

function perfStop() {
  PERF?.resize?.disconnect();
  PERF = null;
}
async function pageComputerPerformance(id) {
  perfStop();
  const saved = localStorage.getItem('tico.performance.hours');
  const state = PERF = {id, hours: PERF_WINDOWS.some(w => w[0] === saved) ? saved : '24', data: null, gen: 0};
  $('#main').innerHTML = `<div class="perf-page">
    <div class="perf-head"><a class="ghost perf-back" href="#/settings" aria-label="Back to Settings" title="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></a>
      <h1 id="perf-title">${esc(screenMachine(id)?.label || 'Computer')} · Performance${betaPill('replay_performance')}</h1><span class="spacer"></span>
      ${repoSegHTML('perf-hours', 'Window', PERF_WINDOWS, state.hours, false, ' repo-seg-sm')}</div>
    <div id="perf-body" aria-live="polite"><p class="empty">Loading…</p></div></div>`;
  $('#main').querySelector('.repo-seg').onchange = event => {
    state.hours = event.target.value; localStorage.setItem('tico.performance.hours', state.hours); void perfLoad(state);
  };
  await perfLoad(state);
}

async function perfLoad(state) {
  const gen = ++state.gen;
  let data;
  try { data = await get(`/v2/computers/${encodeURIComponent(state.id)}/performance?hours=${state.hours}`); }
  catch (error) { if (PERF === state && gen === state.gen) $('#perf-body').innerHTML = `<p class="err">${esc(error.message)}</p>`; return; }
  if (PERF !== state || gen !== state.gen) return;
  state.data = data;
  $('#perf-title').innerHTML = `${esc(data.computer.label)} · Performance${betaPill('replay_performance')}`;
  perfPaint(state);
}

function perfPaint(state) {
  const d = state.data, body = $('#perf-body');
  const off = d.performance === 'off'
    ? `<p class="perf-off" role="status">Performance diagnostics are off for this computer. What was recorded stays until it is 14 days old.</p>` : '';
  const scope = d.scope === 'container' ? 'Measured inside its container, against the container\'s limits.'
    : d.scope === 'container_cpu' ? 'CPU is its container\'s own; memory is the whole computer\'s (the container has no memory limit).'
    : d.scope ? 'Measured across the whole computer.' : '';
  const controls = d.may_switch ? `<div class="perf-controls">
      <button type="button" class="ghost" data-perf-mode="${d.performance === 'off' ? 'on' : 'off'}">${d.performance === 'off' ? 'Turn on' : 'Turn off'}</button>
      <button type="button" class="ghost danger-text" data-perf-erase>Delete history</button></div>` : '';
  body.innerHTML = `${off}<p class="perf-summary">${esc(d.summary)}</p>
    <section class="card"><header><h2>CPU and memory</h2><span class="sub">${esc(scope)}</span></header>
      ${d.points.length ? perfLegendHTML(d) + '<div class="perf-chart" id="perf-chart"></div>' : '<p class="empty">Nothing recorded in this window yet. A computer reports every 30 seconds while performance is on.</p>'}
      ${d.points.length ? `<details class="perf-table" id="perf-table"><summary>Show as a table</summary><div class="scroll"></div></details>` : ''}</section>
    ${perfOverloadsHTML(d)}${perfBusiestHTML(d)}${controls}`;
  if (d.points.length) perfChart(state);
  body.querySelector('#perf-table')?.addEventListener('toggle', event => {
    const box = event.target.querySelector('.scroll');
    if (event.target.open && !box.innerHTML) box.innerHTML = perfPointsTableHTML(d);
  });
  body.onclick = event => {
    const mode = event.target.closest('[data-perf-mode]');
    if (mode) { void perfSwitch(state, mode); return; }
    const erase = event.target.closest('[data-perf-erase]');
    if (erase) void perfErase(state, erase);
  };
}

const perfLegendHTML = d => `<div class="perf-legend">${PERF_SERIES.map(([key, , label]) =>
  `<span><i class="perf-key perf-${key}" aria-hidden="true"></i>${label}</span>`).join('')}
  ${d.overloads.length ? '<span><i class="perf-key perf-band" aria-hidden="true"></i>Overloaded</span>' : ''}
  <span><i class="perf-key perf-active" aria-hidden="true"></i>Active turns</span></div>`;

// ----------------------------------------------------------------- the chart
// One axis (percent), CPU and memory lines, overloads shaded behind them, and active turns as a strip beneath.
function perfChart(state) {
  const d = state.data, box = $('#perf-chart');
  const W = Math.max(320, box.clientWidth), PH = 200, STRIP = 34, L = 36, R = 70, T = 16, GAP = 18, B = 22;
  const H = T + PH + GAP + STRIP + B, pw = W - L - R;
  const x = ts => L + (ts - d.since) / (d.until - d.since) * pw, y = v => T + PH - v / 100 * PH;
  const brk = d.span * 2.5;
  const line = col => {
    let path = '', prev = null;
    for (const p of d.points) {
      if (p[col] == null) { prev = null; continue; }
      path += `${prev && p[0] - prev <= brk ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(Math.min(100, p[col])).toFixed(1)}`;
      prev = p[0];
    }
    return path;
  };
  const most = Math.max(1, ...d.points.map(p => p[8] || 0));
  const sy = n => T + PH + GAP + STRIP - n / most * STRIP;
  let strip = '';
  d.points.forEach((p, i) => {
    const x0 = x(p[0]), x1 = x(Math.min(d.until, p[0] + d.span)), before = d.points[i - 1], next = d.points[i + 1];
    strip += `${before && p[0] - before[0] <= brk ? 'L' : `M${x0.toFixed(1)},${sy(0)}L`}${x0.toFixed(1)},${sy(p[8] || 0).toFixed(1)}H${x1.toFixed(1)}`;
    if (!next || next[0] - p[0] > brk) strip += `V${sy(0)}Z`;
  });
  const bands = d.overloads.map(o => {
    const a = Math.max(L, x(o.started_at)), b = Math.min(L + pw, x(o.ended_at || o.last_seen_at + 60));
    return `<rect class="perf-band" x="${a.toFixed(1)}" y="${T}" width="${Math.max(4, b - a).toFixed(1)}" height="${PH}" rx="2"/>`;
  }).join('');
  const grid = [0, 50, 100].map(v => `<line class="perf-grid" x1="${L}" x2="${L + pw}" y1="${y(v)}" y2="${y(v)}"/><text class="perf-axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`).join('')
    + `<line class="perf-limit" x1="${L}" x2="${L + pw}" y1="${y(90)}" y2="${y(90)}"/>`;
  const ticks = perfTicks(d).map(t => `<line class="perf-tick" x1="${x(t.ts)}" x2="${x(t.ts)}" y1="${T + PH}" y2="${T + PH + 4}"/><text class="perf-axis" x="${x(t.ts)}" y="${H - 6}" text-anchor="middle">${esc(t.label)}</text>`).join('');
  // Direct labels at each line's last value; nudged apart when they would overlap.
  const last = PERF_SERIES.map(([key, col, label]) => {
    const p = [...d.points].reverse().find(q => q[col] != null);
    return p && {key, label, value: p[col], at: y(Math.min(100, p[col]))};
  }).filter(Boolean).sort((a, b) => a.at - b.at);
  for (let i = 1; i < last.length; i++) if (last[i].at - last[i - 1].at < 13) last[i].at = last[i - 1].at + 13;
  const labels = last.map(l => `<text class="perf-direct" x="${L + pw + 6}" y="${(l.at + 4).toFixed(1)}">${l.label} ${perfPct(l.value)}</text>`).join('');
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(`CPU and memory, ${d.summary}`)}">
    ${bands}${grid}
    ${PERF_SERIES.map(([key, col]) => `<path class="perf-line perf-${key}" d="${line(col)}"/>`).join('')}
    <path class="perf-strip" d="${strip}"/><text class="perf-axis" x="${L - 6}" y="${sy(most) + 8}" text-anchor="end">${most}</text>
    <line class="perf-grid" x1="${L}" x2="${L + pw}" y1="${sy(0)}" y2="${sy(0)}"/>
    ${ticks}${labels}
    <g class="perf-hover" hidden><line class="perf-cross" y1="${T}" y2="${sy(0)}"/>${PERF_SERIES.map(([key]) => `<circle class="perf-dot perf-${key}" r="4"/>`).join('')}</g>
    <rect class="perf-hit" x="${L}" y="0" width="${pw}" height="${H}" tabindex="0" aria-label="Move through the chart with the arrow keys"/></svg>
    <div class="perf-tip" role="status" hidden></div>`;
  const svg = box.querySelector('svg'), hover = svg.querySelector('.perf-hover'), tip = box.querySelector('.perf-tip'), hit = svg.querySelector('.perf-hit');
  let at = -1;
  const show = i => {
    at = Math.max(0, Math.min(d.points.length - 1, i));
    const p = d.points[at], px = x(p[0]);
    hover.hidden = false;
    hover.querySelector('.perf-cross').setAttribute('x1', px); hover.querySelector('.perf-cross').setAttribute('x2', px);
    PERF_SERIES.forEach(([key, col]) => {
      const dot = hover.querySelector(`.perf-dot.perf-${key}`);
      dot.style.display = p[col] == null ? 'none' : '';
      dot.setAttribute('cx', px); dot.setAttribute('cy', y(Math.min(100, p[col] ?? 0)));
    });
    const turns = d.runs.filter(r => Date.parse(r.started_at) / 1000 < p[0] + d.span && (!r.finished_at || Date.parse(r.finished_at) / 1000 >= p[0]));
    const names = [...new Set(turns.map(r => perfBot(r.bot)))];
    tip.innerHTML = `<strong>${esc(perfWhen(p[0]))}</strong>
      <span><i class="perf-key perf-cpu" aria-hidden="true"></i>CPU ${perfPct(p[1])} <span class="muted">(peak ${perfPct(p[2])})</span></span>
      <span><i class="perf-key perf-mem" aria-hidden="true"></i>Memory ${perfPct(p[3])} <span class="muted">(peak ${perfPct(p[4])})</span></span>
      <span>${p[8] || 0} active turn${p[8] === 1 ? '' : 's'}${names.length ? `: ${esc(names.slice(0, 4).join(', '))}${names.length > 4 ? ` +${names.length - 4}` : ''}` : ''}</span>`;
    tip.hidden = false;
    const scale = box.clientWidth / W, left = px * scale;
    tip.style.left = `${Math.min(left + 12, box.clientWidth - tip.offsetWidth - 4)}px`;
    if (left + 12 + tip.offsetWidth > box.clientWidth) tip.style.left = `${Math.max(0, left - tip.offsetWidth - 12)}px`;
  };
  const nearest = ts => {
    let lo = 0, hi = d.points.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (d.points[mid][0] < ts) lo = mid + 1; else hi = mid; }
    return lo > 0 && ts - d.points[lo - 1][0] < d.points[lo][0] - ts ? lo - 1 : lo;
  };
  const hide = () => { hover.hidden = true; tip.hidden = true; };
  hit.onpointermove = event => {
    const r = svg.getBoundingClientRect(), vx = (event.clientX - r.left) * W / r.width;
    show(nearest(d.since + (vx - L) / pw * (d.until - d.since)));
  };
  hit.onpointerleave = hide; hit.onblur = hide;
  hit.onkeydown = event => {
    const step = {ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity}[event.key];
    if (step === undefined) return;
    event.preventDefault();
    show(at < 0 ? d.points.length - 1 : at + (Number.isFinite(step) ? step : step > 0 ? d.points.length : -d.points.length));
  };
  // The chart is drawn to the box's width; redraw it when that changes.
  if (state.resize) state.resize.disconnect();
  let width = box.clientWidth;
  state.resize = new ResizeObserver(() => {
    if (PERF !== state || !box.isConnected) { state.resize.disconnect(); return; }
    if (Math.abs(box.clientWidth - width) > 8) { width = box.clientWidth; perfChart(state); }
  });
  state.resize.observe(box);
}

// Time labels: every 6 hours over a day, every day over a week, every other day over 14.
function perfTicks(d) {
  const step = d.hours <= 24 ? 6 * 3600 : d.hours <= 168 ? 86400 : 2 * 86400;
  const first = new Date(d.since * 1000);
  if (step >= 86400) first.setHours(24, 0, 0, 0); else first.setHours(Math.ceil((first.getHours() + 1) / 6) * 6, 0, 0, 0);
  const out = [];
  for (let t = first.getTime() / 1000; t < d.until; t += step) {
    const at = new Date(t * 1000);
    out.push({ts: t, label: step >= 86400 ? at.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) : at.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'})});
  }
  return out;
}

// ----------------------------------------------------------------- tables
function perfOverloadsHTML(d) {
  if (!d.overloads.length) return '';
  const rows = [...d.overloads].reverse().map(o => {
    const end = o.ended_at || o.last_seen_at + 60;
    const replay = '#/replay?' + new URLSearchParams({from: new Date(o.started_at * 1000).toISOString(), to: new Date(end * 1000).toISOString()});
    return `<tr><td class="tnum">${esc(perfWhen(o.started_at))}</td><td class="tnum">${esc(perfMinutes(o.minutes))}${o.ended ? '' : ' <span class="pill fail">now</span>'}</td>
      <td>${esc(o.reasons.map(r => PERF_REASONS[r] || r).join(', '))}<span class="settings-cell-note">peak CPU ${perfPct(o.cpu_max)} · memory ${perfPct(o.mem_max)} · ${o.runs_max} turn${o.runs_max === 1 ? '' : 's'}</span></td>
      <td>${esc(o.bots.map(b => perfBot(b.bot) + (b.runs > 1 ? ` ×${b.runs}` : '')).join(', ') || '–')}</td>
      <td>${esc(o.procs.slice(0, 3).map(p => p.name).join(', ') || '–')}</td><td><a href="${esc(replay)}">Replay</a></td></tr>`;
  }).join('');
  return `<section class="card"><header><h2>Overloads</h2><span class="sub">10 minutes or more of CPU or memory over 90%, heavy swapping or a nearly full disk</span></header>
    <div class="scroll"><table><thead><tr><th>Started</th><th>For</th><th>Why</th><th>Running</th><th>Busiest processes</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function perfBusiestHTML(d) {
  const procs = d.busiest.processes, bots = d.busiest.bots;
  if (!procs.length && !bots.length) return '';
  const procRows = procs.map(p => `<tr><td><code>${esc(p.name)}</code></td><td class="tnum">${p.cpu_minutes}</td><td class="tnum">${perfPct(p.cpu_max)}</td><td class="tnum">${p.mem_max ? bytes(p.mem_max) : '–'}</td></tr>`).join('');
  const botRows = bots.map(b => `<tr><td>${esc(perfBot(b.bot))}</td><td>${esc(b.model || '–')}</td><td class="tnum">${b.runs}</td></tr>`).join('');
  return `<div class="perf-split">
    ${procs.length ? `<section class="card"><header><h2>Busiest processes</h2><span class="sub">100% is one core fully busy; a CPU minute is a minute of that</span></header>
      <div class="scroll"><table><thead><tr><th>Process</th><th>CPU minutes</th><th>Peak CPU</th><th>Peak memory</th></tr></thead><tbody>${procRows}</tbody></table></div></section>` : ''}
    ${bots.length ? `<section class="card"><header><h2>Turns on this computer</h2></header>
      <div class="scroll"><table><thead><tr><th>Bot</th><th>Model</th><th>Turns</th></tr></thead><tbody>${botRows}</tbody></table></div></section>` : ''}</div>`;
}

function perfPointsTableHTML(d) {
  const rows = [...d.points].reverse().map(p => `<tr><td class="tnum">${esc(perfWhen(p[0]))}</td><td class="tnum">${perfPct(p[1])}</td><td class="tnum">${perfPct(p[2])}</td>
    <td class="tnum">${perfPct(p[3])}</td><td class="tnum">${perfPct(p[4])}</td><td class="tnum">${p[5] ?? '–'}</td><td class="tnum">${perfPct(p[6])}</td>
    <td class="tnum">${p[7] == null ? '–' : bytes(p[7])}</td><td class="tnum">${p[8]}</td></tr>`).join('');
  return `<table><thead><tr><th>Time</th><th>CPU</th><th>Peak CPU</th><th>Memory</th><th>Peak memory</th><th>Load</th><th>Swap</th><th>Disk free</th><th>Active turns</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ----------------------------------------------------------------- the switch
async function perfSwitch(state, button) {
  const mode = button.dataset.perfMode;
  button.disabled = true;
  try {
    await post(`/v2/computers/${encodeURIComponent(state.id)}/performance`, {mode});
    toast(mode === 'off' ? 'Performance diagnostics off' : 'Performance diagnostics on');
    await perfLoad(state);
  } catch (error) { toast(error.message, true); button.disabled = false; }
}

async function perfErase(state, button) {
  if (!confirm('Delete this computer\'s recorded performance history? This cannot be undone.')) return;
  button.disabled = true;
  try {
    await writeRequest('DELETE', `/v2/computers/${encodeURIComponent(state.id)}/performance`);
    toast('Performance history deleted');
    await perfLoad(state);
  } catch (error) { toast(error.message, true); button.disabled = false; }
}
