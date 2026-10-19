/* ui/app/overview-hours.js — the Overview's "Bot hours vs human hours" card
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Totals from GET /api/v2/overview/bot-hours (backend/bot_hours.py): the last 7 days and one row a week, Monday to
// Sunday in the company's time zone. The server keeps the answer a minute; the card asks again at most every 5.
const HOURS_EVERY = 5 * 60e3;
const hoursTimes = r => `${r < 10 ? r.toFixed(1) : Math.round(r)}×`;
const hoursNum = h => h < 10 ? String(Math.round(h * 10) / 10) : Math.round(h).toLocaleString();
const hoursWeek = day => new Date(day + 'T12:00:00').toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
const hoursPoint = w => `Week of ${hoursWeek(w.week)}${w.so_far ? ' (so far)' : ''}: ${hoursNum(w.bot_hours)} bot hours · `
  + `${hoursNum(w.human_hours)} human hours${w.ratio == null ? '' : ' · ' + hoursTimes(w.ratio)}`;

async function overviewHoursLoad() {
  const state = OVERVIEW_PAGE;
  if (!state || state.hoursLoading) return;
  state.hoursLoading = true; state.hoursAt = Date.now();
  try {
    const data = await get('/v2/overview/bot-hours');
    if (OVERVIEW_PAGE === state) { state.hours = data; overviewHoursDraw(); }
  } catch {} // The card stays as it was; the next refresh asks again.
  finally { state.hoursLoading = false; }
}
function overviewHoursMaybe() {
  const state = OVERVIEW_PAGE;
  if (state && Date.now() - (state.hoursAt || 0) > HOURS_EVERY) void overviewHoursLoad();
}

// One line, bot hours per human hour a week; a dashed Parity line at 1×; the week still going is dotted and hollow.
function overviewHoursChart(weeks) {
  const W = 300, H = 72, pad = {l: 6, r: 6, t: 8, b: 8};
  const top = Math.max(1, ...weeks.map(w => w.ratio)) * 1.15;
  const x = i => weeks.length < 2 ? W / 2 : pad.l + i * (W - pad.l - pad.r) / (weeks.length - 1);
  const y = r => pad.t + (1 - r / top) * (H - pad.t - pad.b);
  const pts = weeks.map((w, i) => [x(i), y(w.ratio)]);
  const done = weeks.filter(w => !w.so_far).length;
  const path = list => list.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`).join('');
  const solid = pts.slice(0, done), dotted = pts.slice(Math.max(0, done - 1));
  const step = weeks.length < 2 ? W : (W - pad.l - pad.r) / (weeks.length - 1);
  return `<svg class="ovh-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
      aria-label="Bot hours per human hour, one point a week, with a parity line at 1×">
    <line class="ovh-parity" x1="0" x2="${W}" y1="${y(1).toFixed(1)}" y2="${y(1).toFixed(1)}" vector-effect="non-scaling-stroke"/>
    ${solid.length > 1 ? `<path class="ovh-line" d="${path(solid)}" vector-effect="non-scaling-stroke"/>` : ''}
    ${done < weeks.length && dotted.length > 1 ? `<path class="ovh-line ovh-so-far" d="${path(dotted)}" vector-effect="non-scaling-stroke"/>` : ''}
    <line class="ovh-cross" y1="0" y2="${H}" x1="-9" x2="-9" vector-effect="non-scaling-stroke"/>
    ${weeks.map((w, i) => `<rect class="ovh-hit" data-i="${i}" x="${(x(i) - step / 2).toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${H}"/>`).join('')}
  </svg>
  ${pts.map(([px, py], i) => `<i class="ovh-dot${weeks[i].so_far ? ' ovh-dot-so-far' : ''}" data-i="${i}"
    style="left:${(px / W * 100).toFixed(2)}%;top:${(py / H * 100).toFixed(2)}%"></i>`).join('')}
  <span class="ovh-parity-label" style="top:${(y(1) / H * 100).toFixed(2)}%">Parity 1×</span>
  <div class="ovh-tip" role="status" hidden></div>`;
}

function overviewHoursDraw() {
  const el = OVERVIEW_PAGE && $('#overview-hours'), d = OVERVIEW_PAGE?.hours;
  if (!el || !d) return;
  const last = d.last_7_days || {}, weeks = (d.weeks || []).filter(w => w.ratio != null);
  let body, plot = false;
  if (!d.first_run) {
    body = `<p class="ovh-head">No bot work yet</p><p class="ovh-note"><a href="#/settings" data-ovh-bots>Bots</a></p>`;
  } else if (!d.people) {
    body = `<p class="ovh-head"><strong class="tnum">${hoursNum(last.bot_hours)}</strong> bot hours · last 7 days</p>
      <p class="ovh-note">Add people to the org chart to compare</p>`;
  } else {
    const chart = plot = !!(last.full && weeks.length);
    body = `<p class="ovh-head"><strong class="tnum">${hoursTimes(last.ratio)}</strong> bot hours per human hour · last 7 days</p>
      ${chart ? `<div class="ovh-plot">${overviewHoursChart(weeks)}</div>` : '<p class="ovh-note">Chart fills in as weeks pass</p>'}
      <p class="ovh-note">Bot hours = time bots spent working (pauses over ${d.gap_minutes || 30} min left out). Human hours =
        people in your org chart × ${d.hours_per_person || 40} h a week.</p>`;
  }
  el.hidden = false;
  el.classList.toggle('has-plot', !!plot);
  el.innerHTML = `<span class="ovh-title">Bot hours vs human hours</span>${body}`;
  el.querySelector('[data-ovh-bots]')?.addEventListener('click', ev => { ev.stopPropagation(); SETTINGS_TAB = 'bots'; });
  if (plot) overviewHoursHover(el.querySelector('.ovh-plot'), weeks);
}

// Hover (or tap) anywhere in a week's column shows its numbers; the table holds the same numbers without hovering.
function overviewHoursHover(plot, weeks) {
  const tip = plot.querySelector('.ovh-tip'), cross = plot.querySelector('.ovh-cross'), dots = plot.querySelectorAll('.ovh-dot');
  const show = i => {
    const w = weeks[i], dot = dots[i]; if (!w || !dot) return;
    tip.textContent = hoursPoint(w); tip.hidden = false;
    // Centred on the point, kept inside the chart's width so a phone never scrolls sideways.
    const width = plot.clientWidth, centre = parseFloat(dot.style.left) / 100 * width;
    tip.style.left = Math.max(0, Math.min(width - tip.offsetWidth, centre - tip.offsetWidth / 2)) + 'px';
    const hit = plot.querySelector(`.ovh-hit[data-i="${i}"]`), cx = Number(hit.getAttribute('x')) + Number(hit.getAttribute('width')) / 2;
    cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
    dots.forEach((d, j) => d.classList.toggle('on', j === i));
  };
  const hide = () => { tip.hidden = true; cross.setAttribute('x1', -9); cross.setAttribute('x2', -9); dots.forEach(d => d.classList.remove('on')); };
  const at = ev => {
    const box = plot.getBoundingClientRect(), f = (ev.clientX - box.left) / box.width;
    return Math.max(0, Math.min(weeks.length - 1, Math.round(f * (weeks.length - 1))));
  };
  plot.addEventListener('pointermove', ev => show(weeks.length < 2 ? 0 : at(ev)));
  plot.addEventListener('pointerleave', ev => { if (ev.pointerType === 'mouse') hide(); });
  // A tap on the chart shows the week; it doesn't open the table.
  plot.addEventListener('click', ev => { ev.stopPropagation(); show(weeks.length < 2 ? 0 : at(ev)); });
}

function overviewHoursOpen() {
  const d = OVERVIEW_PAGE?.hours; if (!d?.first_run) return;
  let dlg = $('#overview-hours-dialog');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'overview-hours-dialog'; dlg.className = 'tmodal ovh-dialog'; dlg.setAttribute('aria-label', 'Bot hours vs human hours by week');
    document.body.appendChild(dlg);
  }
  const rows = [...(d.weeks || [])].reverse().map(w => `<tr${w.so_far ? ' class="ovh-row-so-far"' : ''}>
    <th scope="row">${esc(hoursWeek(w.week))}${w.so_far ? ' <span class="muted">so far</span>' : ''}</th>
    <td class="tnum">${hoursNum(w.bot_hours)}</td><td class="tnum">${w.people}</td>
    <td class="tnum">${hoursNum(w.human_hours)}</td><td class="tnum">${w.ratio == null ? '–' : hoursTimes(w.ratio)}</td></tr>`).join('');
  dlg.innerHTML = `<div class="tmodal-head"><strong class="who">Bot hours vs human hours</strong><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></div>
    <div class="tmodal-body"><div class="scroll"><table class="ovh-table"><thead><tr><th scope="col">Week</th><th scope="col">Bot hours</th>
      <th scope="col">People</th><th scope="col">Human hours</th><th scope="col">Ratio</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="muted ovh-note">Weeks run Monday to Sunday (${esc(d.zone || '')}). An estimate: pauses over ${d.gap_minutes || 30} min
        inside a run are left out, and every person counts ${d.hours_per_person || 40} h a week.</p></div>`;
  dlg.querySelector('[data-close]').onclick = () => dlg.close();
  dlg.showModal();
}
