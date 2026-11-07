/* ui/app/overview-hours.ts — the Overview's "Bot hours vs human hours" card
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/overview-hours.js, which the page runs in this file's place. */
'use strict';

/** One week of GET /api/v2/overview/bot-hours (backend/bot_hours.py), Monday to Sunday in the company's time zone. */
interface OverviewHoursWeek {
  week: string; bot_hours: number; people: number; human_hours: number; ratio: number | null; so_far: boolean;
}
/** A week with a ratio: the only kind the chart plots. The server sends a null ratio when there are no human hours. */
type OverviewHoursPoint = OverviewHoursWeek & { ratio: number };
/** The last seven days of the answer. */
interface OverviewHoursLast { bot_hours: number; human_hours: number; ratio: number | null; full: boolean; }
/** What GET /api/v2/overview/bot-hours answers, with the fields the card reads. */
interface OverviewHours {
  zone: string; first_run: string | null; people: number; hours_per_person: number; gap_minutes: number;
  last_7_days: OverviewHoursLast; weeks: OverviewHoursWeek[];
}

// Totals from GET /api/v2/overview/bot-hours (backend/bot_hours.py): the last 7 days and one row a week, Monday to
// Sunday in the company's time zone. The server keeps the answer a minute; the card asks again at most every 5.
const HOURS_EVERY = 5 * 60e3;
const hoursTimes = (r: number): string => `${r < 10 ? r.toFixed(1) : Math.round(r)}×`;
const hoursNum = (h: number): string => h < 10 ? String(Math.round(h * 10) / 10) : Math.round(h).toLocaleString();
const hoursWeek = (day: string): string => new Date(day + 'T12:00:00').toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
const hoursPoint = (w: OverviewHoursWeek): string => `Week of ${hoursWeek(w.week)}${w.so_far ? ' (so far)' : ''}: ${hoursNum(w.bot_hours)} bot hours · `
  + `${hoursNum(w.human_hours)} human hours${w.ratio == null ? '' : ' · ' + hoursTimes(w.ratio)}`;
function overviewHoursHasRatio(w: OverviewHoursWeek): w is OverviewHoursPoint { return w.ratio != null; }
// Read for an answer with no last-7-days row (the server always sends one). NaN prints the same text the missing
// fields printed before (hoursNum and hoursTimes both give NaN for undefined), so the card is unchanged.
const HOURS_LAST_NONE: OverviewHoursLast = {bot_hours: NaN, human_hours: NaN, ratio: NaN, full: false};

async function overviewHoursLoad(): Promise<void> {
  const state = OVERVIEW_PAGE;
  if (!state || state.hoursLoading) return;
  state.hoursLoading = true; state.hoursAt = Date.now();
  try {
    const data = await get<OverviewHours>('/v2/overview/bot-hours');
    S.lastRead.hours = data;
    if (OVERVIEW_PAGE === state && !sameData(data, state.hours)) { state.hours = data; overviewHoursDraw(); }
  } catch { // The card stays as it was (a first visit's empty card goes away); the next refresh asks again.
    if (OVERVIEW_PAGE === state && !state.hours) document.querySelector<HTMLElement>('#overview-hours')!.hidden = true;
  }
  finally { state.hoursLoading = false; }
}
function overviewHoursMaybe(): void {
  const state = OVERVIEW_PAGE;
  if (state && Date.now() - (state.hoursAt || 0) > HOURS_EVERY) void overviewHoursLoad();
}

// One line, bot hours per human hour a week; a dashed Parity line at 1×; the week still going is dotted and hollow.
function overviewHoursChart(weeks: OverviewHoursPoint[]): string {
  const W = 300, H = 72, pad = {l: 6, r: 6, t: 8, b: 8};
  const top = Math.max(1, ...weeks.map(w => w.ratio)) * 1.15;
  const x = (i: number): number => weeks.length < 2 ? W / 2 : pad.l + i * (W - pad.l - pad.r) / (weeks.length - 1);
  const y = (r: number): number => pad.t + (1 - r / top) * (H - pad.t - pad.b);
  const pts: Array<[number, number, boolean]> = weeks.map((w, i) => [x(i), y(w.ratio), w.so_far]);
  const done = weeks.filter(w => !w.so_far).length;
  const path = (list: Array<[number, number, boolean]>): string =>
    list.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`).join('');
  const solid = pts.slice(0, done), dotted = pts.slice(Math.max(0, done - 1));
  const step = weeks.length < 2 ? W : (W - pad.l - pad.r) / (weeks.length - 1);
  return `<svg class="ovh-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
      aria-label="Bot hours per human hour, one point a week, with a parity line at 1×">
    <line class="ovh-parity" x1="0" x2="${W}" y1="${y(1).toFixed(1)}" y2="${y(1).toFixed(1)}" vector-effect="non-scaling-stroke"/>
    ${solid.length > 1 ? `<path class="ovh-line" d="${path(solid)}" vector-effect="non-scaling-stroke"/>` : ''}
    ${done < weeks.length && dotted.length > 1 ? `<path class="ovh-line ovh-so-far" d="${path(dotted)}" vector-effect="non-scaling-stroke"/>` : ''}
    <line class="ovh-cross" y1="0" y2="${H}" x1="-9" x2="-9" vector-effect="non-scaling-stroke"/>
    ${weeks.map((_, i) => `<rect class="ovh-hit" data-i="${i}" x="${(x(i) - step / 2).toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${H}"/>`).join('')}
  </svg>
  ${pts.map(([px, py, soFar], i) => `<i class="ovh-dot${soFar ? ' ovh-dot-so-far' : ''}" data-i="${i}"
    style="left:${(px / W * 100).toFixed(2)}%;top:${(py / H * 100).toFixed(2)}%"></i>`).join('')}
  <span class="ovh-parity-label" style="top:${(y(1) / H * 100).toFixed(2)}%">Parity 1×</span>
  <div class="ovh-tip" role="status" hidden></div>`;
}

// Before its first answer the card holds a line's place, so the campus below does not jump when it lands; a return
// visit draws the last answer at once (S.lastRead.hours) and the read that follows redraws it only if it changed.
function overviewHoursDraw(): void {
  const page = OVERVIEW_PAGE, el = page && document.querySelector<HTMLElement>('#overview-hours');
  if (!page || !el) return;
  if (!page.hours) page.hours = S.lastRead.hours;
  const d = page.hours;
  if (!d) {
    el.innerHTML = '<span class="ovh-title">Bot hours vs human hours</span><p class="ovh-head" aria-hidden="true">&nbsp;</p>';
    return;
  }
  const last = d.last_7_days || HOURS_LAST_NONE, weeks = (d.weeks || []).filter(overviewHoursHasRatio);
  let body: string, plot = false;
  if (!d.first_run) {
    body = `<p class="ovh-head">No bot work yet</p><p class="ovh-note"><a href="#/settings" data-ovh-bots>Bots</a></p>`;
  } else if (!d.people) {
    body = `<p class="ovh-head"><strong class="tnum">${hoursNum(last.bot_hours)}</strong> bot hours · last 7 days</p>
      <p class="ovh-note">Add people to the org chart to compare</p>`;
  } else {
    // People above zero means human hours above zero, so the server sends a ratio here (backend/bot_hours.py ratio()).
    plot = !!(last.full && weeks.length);
    body = `<p class="ovh-head"><strong class="tnum">${hoursTimes(last.ratio!)}</strong> bot hours per human hour · last 7 days</p>
      ${plot ? `<div class="ovh-plot">${overviewHoursChart(weeks)}</div>` : '<p class="ovh-note">Chart fills in as weeks pass</p>'}
      <p class="ovh-note">Bot hours = time bots spent working (pauses over ${d.gap_minutes || 30} min left out). Human hours =
        people in your org chart × ${d.hours_per_person || 40} h a week.</p>`;
  }
  el.hidden = false;
  el.classList.toggle('has-plot', plot);
  el.innerHTML = `<span class="ovh-title">Bot hours vs human hours</span>${body}`;
  el.querySelector<HTMLElement>('[data-ovh-bots]')?.addEventListener('click', ev => { ev.stopPropagation(); SETTINGS_TAB = 'bots'; });
  // Drawn just above whenever plot is true.
  if (plot) overviewHoursHover(el.querySelector<HTMLElement>('.ovh-plot')!, weeks);
}

// Hover (or tap) anywhere in a week's column shows its numbers; the table holds the same numbers without hovering.
function overviewHoursHover(plot: HTMLElement, weeks: OverviewHoursPoint[]): void {
  // The chart markup always carries these, so they are read once here.
  const tip = plot.querySelector<HTMLElement>('.ovh-tip')!, cross = plot.querySelector<SVGLineElement>('.ovh-cross')!;
  const dots = plot.querySelectorAll<HTMLElement>('.ovh-dot');
  const show = (i: number): void => {
    const w = weeks[i], dot = dots[i]; if (!w || !dot) return;
    tip.textContent = hoursPoint(w); tip.hidden = false;
    // Centred on the point, kept inside the chart's width so a phone never scrolls sideways.
    const width = plot.clientWidth, centre = parseFloat(dot.style.left) / 100 * width;
    tip.style.left = Math.max(0, Math.min(width - tip.offsetWidth, centre - tip.offsetWidth / 2)) + 'px';
    const hit = plot.querySelector<SVGRectElement>(`.ovh-hit[data-i="${i}"]`)!;
    const cx = Number(hit.getAttribute('x')) + Number(hit.getAttribute('width')) / 2;
    cross.setAttribute('x1', String(cx)); cross.setAttribute('x2', String(cx));
    dots.forEach((d, j) => { d.classList.toggle('on', j === i); });
  };
  const hide = (): void => {
    tip.hidden = true; cross.setAttribute('x1', '-9'); cross.setAttribute('x2', '-9');
    dots.forEach(d => { d.classList.remove('on'); });
  };
  const at = (ev: MouseEvent): number => {
    const box = plot.getBoundingClientRect(), f = (ev.clientX - box.left) / box.width;
    return Math.max(0, Math.min(weeks.length - 1, Math.round(f * (weeks.length - 1))));
  };
  plot.addEventListener('pointermove', ev => show(weeks.length < 2 ? 0 : at(ev)));
  plot.addEventListener('pointerleave', ev => { if (ev.pointerType === 'mouse') hide(); });
  // A tap on the chart shows the week; it doesn't open the table.
  plot.addEventListener('click', ev => { ev.stopPropagation(); show(weeks.length < 2 ? 0 : at(ev)); });
}

function overviewHoursOpen(): void {
  const d = OVERVIEW_PAGE?.hours; if (!d?.first_run) return;
  let dlg = document.querySelector<HTMLDialogElement>('#overview-hours-dialog');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'overview-hours-dialog'; dlg.className = 'tmodal ovh-dialog'; dlg.setAttribute('aria-label', 'Bot hours vs human hours by week');
    document.body.appendChild(dlg);
  }
  const dialog: HTMLDialogElement = dlg;
  const rows = [...(d.weeks || [])].reverse().map(w => `<tr${w.so_far ? ' class="ovh-row-so-far"' : ''}>
    <th scope="row">${esc(hoursWeek(w.week))}${w.so_far ? ' <span class="muted">so far</span>' : ''}</th>
    <td class="tnum">${hoursNum(w.bot_hours)}</td><td class="tnum">${w.people}</td>
    <td class="tnum">${hoursNum(w.human_hours)}</td><td class="tnum">${w.ratio == null ? '–' : hoursTimes(w.ratio)}</td></tr>`).join('');
  dialog.innerHTML = `<div class="tmodal-head"><strong class="who">Bot hours vs human hours</strong><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></div>
    <div class="tmodal-body"><div class="scroll"><table class="ovh-table"><thead><tr><th scope="col">Week</th><th scope="col">Bot hours</th>
      <th scope="col">People</th><th scope="col">Human hours</th><th scope="col">Ratio</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="muted ovh-note">Weeks run Monday to Sunday (${esc(d.zone || '')}). An estimate: pauses over ${d.gap_minutes || 30} min
        inside a run are left out, and every person counts ${d.hours_per_person || 40} h a week.</p></div>`;
  // The close button was just drawn above.
  dialog.querySelector<HTMLButtonElement>('[data-close]')!.onclick = () => dialog.close();
  dialog.showModal();
}
