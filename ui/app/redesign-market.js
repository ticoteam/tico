/* ui/app/redesign-market.js — Strategy › Market's Briefing and Listening views with the redesign on (PLAN §6 wave 6).
   Briefing: the latest weekly briefing as a front page (GET /api/v2/market/briefings/latest): masthead,
   headline, its first lines, then the items by kind; Read opens the whole page (ui/market-briefing.js).
   Listening: posts per day by source over two weeks and the newest posts sent somewhere, one line each
   (GET /api/v2/listening/stats `daily` and `recent`, backend/listening.py timeline), with the destinations folded below.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RD_BRIEFING = '#/strategy/market/briefing';
const RDM_DAYS = 14;
let RDM_SEQ = 0;

// ----------------------------------------------------------------- Briefing
const RDB_KINDS = [['change', 'Changes'], ['signal', 'Signals'], ['theme', 'Themes'], ['size', 'Size']];
// The body's first paragraph as plain words: what sits under the headline.
function rdbDeck(body) {
  const para = String(body || '').split(/\n\s*\n/).map(s => s.trim()).find(s => s && !/^(#|[-*] |\d+\. |>|\|)/.test(s)) || '';
  const plain = para.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`]+/g, '').replace(/\s+/g, ' ').trim();
  return plain.length > 180 ? plain.slice(0, 179).replace(/\s+\S*$/, '') + '…' : plain;
}
// What the briefing is: a daily one is Today or its date; a weekly one is the week it covers (its ISO week's Monday).
const rdbDay = at => at.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(at.getFullYear() !== new Date().getFullYear() ? {year: 'numeric'} : {})});
const rdbDate = b => {
  const at = new Date(b.updated_at || b.created_at || Date.now());
  if (isNaN(at)) return '';
  if (b.cadence === 'daily') return at.toDateString() === new Date().toDateString() ? 'Today' : rdbDay(at);
  const [, y, w] = String(b.week || '').match(/^(\d{4})-W(\d{1,2})$/) || [];
  let monday = new Date(at.getFullYear(), at.getMonth(), at.getDate() - (at.getDay() + 6) % 7);
  if (y) { const jan4 = new Date(Number(y), 0, 4); monday = new Date(Number(y), 0, 4 - (jan4.getDay() + 6) % 7 + (Number(w) - 1) * 7); }
  return 'Week of ' + rdbDay(monday);
};
function rdbHtml(b) {
  const deck = rdbDeck(b.body);
  const cols = RDB_KINDS.map(([kind, label]) => [label, (b.items || []).filter(i => (i.kind || 'change') === kind)])
    .filter(([, items]) => items.length)
    .map(([label, items]) => `<section><h3>${esc(label)}</h3>${items.map(i => `<p>${esc(i.text)}</p>`).join('')}</section>`).join('');
  const bets = (b.touched || []).map(t => `<a href="#/strategy" title="${esc(t.statement)}">Bet ${esc(t.n)}</a>`).join(' · ');
  return `<article class="rdb" aria-label="Briefing">
    <div class="rdb-mast">The ${esc(companyName())} Briefing</div>
    <div class="rdb-date"><span>${esc(rdbDate(b))}</span></div>
    <h2 class="rdb-hl">${esc(b.title)}</h2>
    ${deck ? `<p class="rdb-deck">${esc(deck)}</p>` : ''}
    ${cols ? `<div class="rdb-cols">${cols}</div>` : ''}
    <div class="rdb-foot"><span>${bets}</span><button class="ghost" type="button" data-rdb-read>Read</button></div>
  </article>`;
}
async function pageRdBriefing() {
  window.marketStop?.();
  const main = $('#main'), seq = ++RDM_SEQ;
  main.classList.add('strategy-layout', 'rd-brief-layout');
  main.innerHTML = strategyHeadHtml('market') + `<div id="rdb-host" class="rdb-host">${skelRows(3, {card: true, dot: false})}</div>`;
  marketViewSwitch('briefing');
  let b = null, failed = '';
  try { b = (await get('/v2/market/briefings/latest'))?.briefing || null; } catch (e) { failed = e.message; }
  const host = $('#rdb-host');
  if (seq !== RDM_SEQ || !host) return;
  if (!b) { host.innerHTML = `<p class="muted rd-empty">${esc(failed || 'No briefing yet.')}</p>`; return; }
  host.innerHTML = rdbHtml(b);
  host.querySelector('[data-rdb-read]').onclick = () => briefingOpen(b);
}

// ----------------------------------------------------------------- Listening
// Icons from the shipped subset (ui/vendor/fonts/icons.txt): a name missing from it shows as text.
const RDM_SOURCES = {x: ['X', 'tag'], twitter: ['X', 'tag'], reddit: ['Reddit', 'forum'],
  hackernews: ['Hacker News', 'code'], hn: ['Hacker News', 'code'], news: ['News', 'newspaper'],
  linkedin: ['LinkedIn', 'groups'], youtube: ['YouTube', 'campaign'], bluesky: ['Bluesky', 'chat_bubble']};
// The mock's four first (Reddit, X, Hacker News, News), then the status colours for the rest.
const RDM_COLORS = {reddit: 'var(--fail)', x: 'var(--ink)', twitter: 'var(--ink)', hackernews: 'var(--wait)', hn: 'var(--wait)', news: 'var(--run)'};
const RDM_MORE = ['var(--ok)', 'var(--accent)', 'var(--idle)', 'var(--g5)'];
const rdmLabel = s => RDM_SOURCES[s]?.[0] || String(s).replace(/[-_.]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const rdmIcon = s => RDM_SOURCES[s]?.[1] || 'link';
const rdmAge = iso => { const a = ago(iso); return a === 'just now' ? 'now' : a.replace(' ago', ''); };
const rdmDay = d => d.toISOString().slice(0, 10);
// The current view in sight inside the view switch's strip (it scrolls sideways on a phone).
function rdmViewInSight() {
  const cur = $('.mkt-views [aria-current="page"]'), strip = cur?.parentElement;
  if (!strip || strip.scrollWidth <= strip.clientWidth) return;
  const a = cur.getBoundingClientRect(), b = strip.getBoundingClientRect();
  if (a.right > b.right || a.left < b.left) strip.scrollLeft += a.left - b.left - 8;
}

// The last two weeks, today last, each day's counts by source (days with nothing are zero).
function rdmSeries(daily) {
  const by = new Map((daily || []).map(d => [d.day, d.sources || {}]));
  const today = new Date(), out = [];
  for (let i = RDM_DAYS - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push({day: rdmDay(d), date: d, sources: by.get(rdmDay(d)) || {}});
  }
  return out;
}
function rdmChartHtml(series) {
  const totals = {};
  for (const d of series) for (const [s, n] of Object.entries(d.sources)) totals[s] = (totals[s] || 0) + n;
  const sources = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
  const color = {};
  let more = 0;
  for (const s of sources) color[s] = RDM_COLORS[s] || RDM_MORE[more++ % RDM_MORE.length];
  const sums = series.map(d => Object.values(d.sources).reduce((t, n) => t + n, 0));
  const max = Math.max(1, ...sums);
  const sorted = [...sums].sort((a, b) => a - b), median = sorted[Math.floor(sorted.length / 2)];
  const peak = sums.indexOf(max);
  const spike = max >= 4 && max >= 2 * Math.max(1, median) ? peak : -1;
  const short = d => d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'});
  const cols = series.map((d, i) => {
    const segs = sources.filter(s => d.sources[s]).map(s =>
      `<span style="height:${(d.sources[s] / max * 100).toFixed(2)}%;background:${color[s]}" title="${esc(rdmLabel(s))} ${d.sources[s]}"></span>`).join('');
    const mark = i === spike ? `<button type="button" class="rdm-spk" data-rdm-day="${d.day}" style="bottom:calc(${(sums[i] / max * 100).toFixed(2)}% + 4px)" aria-label="${sums[i]} on ${esc(short(d.date))}">${sums[i]}</button>` : '';
    return `<div class="rdm-col" title="${esc(short(d.date))} · ${sums[i]}"><div class="rdm-stk">${segs}</div>${mark}</div>`;
  }).join('');
  const legend = sources.map(s => `<span><i style="background:${color[s]}"></i>${esc(rdmLabel(s))}</span>`).join('');
  return {legend, chart: `<div class="rdm-bars" role="img" aria-label="Posts per day, last ${RDM_DAYS} days">${cols}</div>
    <div class="rdm-ax"><span>${esc(short(series[0].date))}</span><span>${esc(short(series[7].date))}</span><span>Today</span></div>`};
}
function rdmRowsHtml(recent) {
  if (!recent.length) return '<p class="muted rdm-none">Nothing sent on yet.</p>';
  return `<div class="rdm-list" role="list">${recent.map(r => {
    const tag = r.url ? 'a' : 'div', link = r.url ? ` href="${esc(r.url)}" target="_blank" rel="noopener noreferrer"` : '';
    return `<${tag} class="rdm-row" role="listitem" data-rdm-item="${esc(r.item_id)}" data-day="${esc(String(r.at || '').slice(0, 10))}"${link}>
      <span class="nav-icon" aria-hidden="true" style="color:${RDM_COLORS[r.source] || 'var(--muted)'}">${rdmIcon(r.source)}</span>
      <span class="rdm-t" title="${esc(r.text)}">${esc(r.text)}</span><span class="rdm-d">${esc((r.destinations || []).join(', '))}</span>
      <span class="rdm-w">${esc(rdmAge(r.at))}</span></${tag}>`;
  }).join('')}</div>`;
}
async function pageRdListening() {
  window.marketStop?.();
  const main = $('#main'), seq = ++RDM_SEQ;
  main.classList.add('strategy-layout', 'rd-listen-layout');
  main.innerHTML = strategyHeadHtml('market') + `<section class="card rdm" aria-label="Listening">
      <div class="rdm-hd"><span class="rdm-cap">Listening</span><span class="rdm-leg" id="rdm-leg"></span>
        <button class="rdm-lnk" type="button" data-rdm-settings aria-expanded="false" aria-controls="rdm-set">Settings</button></div>
      <div id="rdm-body">${skelRows(3, {dot: false})}</div>
      <div class="rdm-set" id="rdm-set" hidden><div id="set-listening">${skelRows(2, {card: true, dot: false})}</div></div></section>`;
  marketViewSwitch('listening');
  rdmViewInSight();
  void renderSettingsListening();
  const since = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate() - (RDM_DAYS - 1)));
  let stats = null, failed = '';
  try { stats = await get('/v2/listening/stats?' + new URLSearchParams({since: since.toISOString().slice(0, 19)})); }
  catch (e) { failed = e.status === 403 ? 'Not shared with you.' : e.message; }
  const body = $('#rdm-body');
  if (seq !== RDM_SEQ || !body) return;
  if (!stats) { body.innerHTML = `<p class="muted rdm-none">${esc(failed)}</p>`; return; }
  const series = rdmSeries(stats.daily), {legend, chart} = rdmChartHtml(series);
  $('#rdm-leg').innerHTML = legend;
  body.innerHTML = chart + rdmRowsHtml(stats.recent || []);
}

document.addEventListener('click', ev => {
  const set = ev.target.closest?.('[data-rdm-settings]');
  if (set) {
    const box = $('#rdm-set'), open = box.hidden;
    box.hidden = !open; set.setAttribute('aria-expanded', String(open));
    return;
  }
  // A spike's marker lights the posts sent that day.
  const spike = ev.target.closest?.('.rdm-spk');
  if (spike) {
    const rows = [...document.querySelectorAll('.rdm-row')];
    rows.forEach(r => r.classList.toggle('hl', r.dataset.day === spike.dataset.rdmDay));
    rows.find(r => r.classList.contains('hl'))?.scrollIntoView({block: 'nearest'});
  }
});
