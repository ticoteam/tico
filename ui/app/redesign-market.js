/* ui/app/redesign-market.js — Strategy › Market: one page of five questions, from the top (ui/market-page.js mounts
   it). The header names the market and opens the map; three tiles give its yearly spend, its fastest-growing segment and
   our share. Then What changed? (the latest briefing's items, GET /api/v2/market/briefings/latest; Read opens the whole
   page, ui/market-briefing.js), Where is the money going? (the treemap and Size vs growth, ui/market-spend.js), Who are
   we up against? (the players in GET /api/v2/market/spend, a row opening its note), How does it reach buyers? (the value
   chain) and What are buyers saying? (Listening's posts per day by source over two weeks and the newest posts sent
   somewhere, GET /api/v2/listening/stats `daily` and `recent`, backend/listening.py timeline). Each question carries
   the briefing's one-line answer when it has one; a question with nothing to show is left out, except that the owner
   gets Where is the money going? before anything is sized, to start the sizing.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RDM_DAYS = 14;
const RDQ_KINDS = {change: 'Change', signal: 'Signal', theme: 'Theme', size: 'Size'};
const RDQ_PLAYERS = 12;

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

// ----------------------------------------------------------------- the page
const rdqPct = n => (n >= 10 || n === 0 ? mktTrim(n, 0) : n >= 1 ? mktTrim(n, 1) : mktTrim(n, 2)) + '%';
const rdqGrowth = g => `<span class="${g < 0 ? 'mkt-down' : 'mkt-up'}">${g > 0 ? '+' : g < 0 ? '−' : ''}${mktTrim(Math.abs(g), 1)}%</span>`;

// A player's yearly revenue in the market: its newest revenue or ARR figure in each segment, summed over the segments
// where no segment above holds a figure for it too, so a segment and its part are not counted twice.
function rdqRevenue(data) {
  const segs = data?.segments || [], parent = new Map(segs.map(s => [s.id, s.parent]));
  const by = new Map();
  for (const s of segs) for (const p of s.players || []) {
    const f = (p.figures || []).find(f => (f.metric === 'revenue' || f.metric === 'arr') && f.value != null && Number.isFinite(Number(f.value)));
    if (f) (by.get(p.entity_id) || by.set(p.entity_id, new Map()).get(p.entity_id)).set(s.id, Number(f.value));
  }
  const out = new Map();
  for (const [id, figs] of by) {
    let sum = 0;
    for (const [segId, value] of figs) {
      let up = parent.get(segId);
      while (up && !figs.has(up)) up = parent.get(up);
      if (!up) sum += value;
    }
    out.set(id, sum);
  }
  return out;
}

const rdqTile = (label, big, small) => `<div class="mkt-tile-k"><div class="mkt-lab">${label}</div><div class="mkt-big">${big}</div><div class="mkt-small">${small}</div></div>`;
// The whole market's tiles (rdqPageHtml lays them out): yearly spend, the fastest-growing segment, our share.
function rdqTilesHtml(data, revenue) {
  if (!mktSized(data)) return [];
  const total = Number(data.total?.spend_usd) || 0, out = [];
  const tops = mktTree(data).drawn.map(g => g.seg).filter(s => s.size?.spend_usd != null && Number.isFinite(s.size.growth_pct));
  const weight = tops.reduce((a, s) => a + s.size.spend_usd, 0);
  const growth = weight ? tops.reduce((a, s) => a + s.size.spend_usd * s.size.growth_pct, 0) / weight : null;
  if (total) out.push(rdqTile('Yearly spend', mktMoney(total), `${growth == null ? '' : rdqGrowth(growth) + ' a year '}${mktSureHtml(data)}`));
  const fast = (data.segments || []).filter(s => s.size?.spend_usd != null && Number.isFinite(s.size.growth_pct)).sort((a, b) => b.size.growth_pct - a.size.growth_pct)[0];
  if (fast) out.push(rdqTile('Fastest growing', esc(fast.name), `${rdqGrowth(fast.size.growth_pct)} · ${mktMoney(fast.size.spend_usd)}`));
  const ours = revenue.get(marketSelfId());
  if (ours && total) out.push(rdqTile(`${esc(companyName())} share`, rdqPct(100 * ours / total), `${mktMoney(ours)} of ${mktMoney(total)}`));
  return out;
}

function rdqSection(key, title, answer, more, body) {
  return `<section class="mkt-q" id="mkt-q-${key}" aria-labelledby="mkt-qh-${key}">
    <div class="mkt-qh"><h3 id="mkt-qh-${key}">${title}</h3>${answer ? `<p class="mkt-ans">${esc(answer)}</p>` : ''}${more ? `<span class="mkt-more">${more}</span>` : ''}</div>${body}</section>`;
}

// The briefing's items, each with its kind and the bets it touches; with none, its headline.
function rdqChangedHtml(b) {
  const touched = new Map((b.touched || []).map(t => [t.id, t]));
  const items = (b.items || []).length ? b.items : [{text: b.title, kind: '', bets: (b.touched || []).map(t => t.id)}];
  return `<ul class="mkt-card mkt-items">${items.map(i => `<li>${RDQ_KINDS[i.kind] ? `<span class="mkt-kind k-${i.kind}">${RDQ_KINDS[i.kind]}</span>` : ''}
      <span class="mkt-it">${esc(i.text)}</span>${(i.bets || []).map(id => touched.get(id)).filter(Boolean).map(t =>
        `<a class="mkt-bet" href="#/strategy" title="${esc(t.statement)}">Bet ${esc(t.n)}</a>`).join('')}</li>`).join('')}</ul>`;
}

// One row per player in the segments (or, before any is placed in one, per tracked company), biggest first, ours
// marked. Revenue and share show when a figure is known; the last move is the latest briefing's item about it.
function rdqPlayersHtml(data, companies, briefing, revenue) {
  const rows = new Map(), total = Number(data?.total?.spend_usd) || 0, self = marketSelfId();
  for (const s of data?.segments || []) for (const p of s.players || []) {
    const row = rows.get(p.entity_id) || rows.set(p.entity_id, {id: p.entity_id, name: p.name, sells: []}).get(p.entity_id);
    if (!row.sells.includes(s.name)) row.sells.push(s.name);
  }
  if (!rows.size) for (const e of companies?.entities || []) rows.set(e.id, {id: e.id, name: e.name, sells: []});
  if (!rows.size) return '';
  const moves = new Map();
  for (const i of briefing?.items || []) for (const id of i.entity_ids || []) if (!moves.has(id)) moves.set(id, i.text);
  const list = [...rows.values()].map(r => ({...r, revenue: revenue.get(r.id) || 0, move: moves.get(r.id) || ''}))
    .sort((a, b) => b.revenue - a.revenue || a.name.localeCompare(b.name));
  const shown = list.slice(0, RDQ_PLAYERS);
  const mine = list.find(r => r.id === self);
  if (mine && !shown.includes(mine)) shown.push(mine);
  const top = Math.max(0, ...shown.map(r => r.revenue)), sells = shown.some(r => r.sells.length), money = top > 0, moved = shown.some(r => r.move);
  return `<div class="mkt-card mkt-tbl"><table class="mkt-pl"><thead><tr><th>Name</th>${sells ? '<th>Sells</th>' : ''}${money ? '<th>Revenue</th><th class="mkt-sh">Share</th>' : ''}${moved ? '<th>Last move</th>' : ''}</tr></thead>
    <tbody>${shown.map(r => `<tr${r.id === self ? ' class="us"' : ''}><td><button class="mkt-pn" type="button" data-player="${esc(r.id)}">${esc(r.name)}</button></td>
      ${sells ? `<td class="mkt-sells">${esc(r.sells.slice(0, 3).join(', '))}</td>` : ''}
      ${money ? `<td class="mkt-num">${r.revenue ? mktMoney(r.revenue) : ''}</td><td class="mkt-sh">${r.revenue ? `<span class="mkt-sbar"><i style="width:${(100 * r.revenue / top).toFixed(1)}%"></i></span>${total ? `<span class="mkt-num">${rdqPct(100 * r.revenue / total)}</span>` : ''}` : ''}</td>` : ''}
      ${moved ? `<td class="mkt-move">${esc(r.move)}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}

// The money on each value-chain node: a segment's spend (either dimension), a player's revenue.
function rdqChainMoney(offering, buyer, revenue) {
  const out = new Map();
  for (const data of [offering, buyer]) for (const s of data?.segments || []) {
    const usd = Number(s.size?.spend_usd ?? s.rolled_usd?.spend_usd);
    if (usd > 0) out.set(s.id, usd);
  }
  for (const [id, usd] of revenue) if (usd > 0) out.set(id, usd);
  return out;
}

// A player's note in a side panel: the entry the map shows (ui/market-page.js renderEntity). A link in it opens the map.
async function rdqPlayerOpen(id) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal gpanel mkt-note';
  dialog.setAttribute('aria-label', 'Note');
  dialog.innerHTML = `<div class="tmodal-head"><span class="spacer"></span><button class="ghost tmodal-x" type="button" aria-label="Close">✕</button></div>
    <div class="tmodal-body market-read">${skelRows(3, {dot: false})}</div>`;
  document.body.append(dialog);
  const body = /** @type {HTMLElement} */ (dialog.querySelector('.tmodal-body'));
  /** @type {HTMLElement} */ (dialog.querySelector('.tmodal-x')).onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  body.onclick = ev => {
    const research = ev.target.closest('[data-market-research]');
    if (research) DocsEditor.researchDialog({target: research.dataset.marketResearch, body: ''});
    else if (ev.target.closest('a[href^="#"]')) dialog.close();
  };
  dialog.showModal();
  try {
    const shown = await get('/v2/market/entities/' + id.split('/').map(encodeURIComponent).join('/') + '?names=true');
    body.innerHTML = renderEntity(shown, new Map([[shown.entity.id, shown.entity], ...(shown.neighbors || []).map(row => [row.id, row])]));
  } catch (error) { body.innerHTML = `<p class="market-empty">${esc(error.message || 'That note is not in the market.')}</p>`; }
}

// With no posts yet, an admin still gets the question for its Settings: it is where Listening is set up.
function rdqSayingHtml(stats, heard) {
  const series = rdmSeries(stats.daily), {legend, chart} = rdmChartHtml(series);
  return `${heard ? `<div class="mkt-say"><div class="mkt-card"><div class="rdm-hd"><h4 class="mkt-lab">Posts a day</h4><span class="rdm-leg" id="rdm-leg">${legend}</span></div>${chart}</div>
      <div class="mkt-card">${rdmRowsHtml(stats.recent || [])}</div></div>` : ''}
    ${settingsIsAdmin() ? `<div class="mkt-card rdm-set" id="rdm-set" hidden><div id="set-listening">${skelRows(2, {card: true, dot: false})}</div></div>` : ''}`;
}

// The one-page Market: its header, its tiles and its questions. The whole market and a segment's market both draw it,
// each with its own data (rdqQuestionsHtml); the blank state is the caller's.
function rdqPageHtml({cls = '', top, tiles = [], questions}) {
  const nothing = !tiles.length && !questions;
  return `<div class="mkt-pg${cls ? ' ' + cls : ''}">${top}${tiles.length ? `<div class="mkt-tiles">${tiles.join('')}</div>` : ''}${questions || (nothing ? '<p class="market-none">Nothing here yet.</p>' : '')}</div>`;
}

// The five questions, in order. A question draws when it has a body (HTML its caller built from its data). With `keep`
// naming it, a question with no body shows its one-line answer alone; one with neither is left out.
function rdqQuestionsHtml(q) {
  const answers = q.answers || {}, keep = q.keep || [];
  const part = (key, title, body, more) => (body || (keep.includes(key) && answers[key]) ? rdqSection(key, title, answers[key], more || '', body || '') : '');
  return part('changed', 'What changed?', q.changed, q.changedMore) + part('money', 'Where is the money going?', q.money, q.moneyMore)
    + part('players', 'Who are we up against?', q.players, q.playersMore) + part('reach', 'How does it reach buyers?', q.reach, q.reachMore)
    + part('saying', 'What are buyers saying?', q.saying, q.sayingMore);
}

// The clicks a drawn page takes: a briefing's Read, and a player's note (from its name or its row). `more` takes the
// clicks its own page owns first, and returns true for each it took.
function rdqPageBind(shell, briefing, more) {
  shell.onclick = ev => {
    if (more && more(ev)) return;
    if (ev.target.closest('[data-rdb-read]')) { briefingOpen(briefing); return; }
    const player = ev.target.closest('[data-player]') || ev.target.closest('.mkt-pl tbody tr')?.querySelector('[data-player]');
    if (player && !ev.target.closest('a')) void rdqPlayerOpen(player.dataset.player);
  };
}

async function mountMarketPage(shell) {
  const day = new Date(), since = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate() - (RDM_DAYS - 1)));
  const soft = path => get(path).catch(() => null);   // a part that does not load leaves its question out
  const [count, library, latest, offering, buyer, chain, companies, stats] = await Promise.all([
    get('/v2/market/entities?status=active&limit=1'), get('/company-docs?collection=market'),
    soft('/v2/market/briefings/latest'), soft('/v2/market/spend?dimension=offering'), soft('/v2/market/spend?dimension=buyer'),
    soft('/v2/market/value-chain'), soft('/v2/market/entities?type=company&status=active&limit=50'),
    soft('/v2/listening/stats?' + new URLSearchParams({since: since.toISOString().slice(0, 19)})),
  ]);
  if (marketBlank(count.total ?? (count.entities || []).length, library.documents).blank) return await mountBlank(shell);
  const briefing = latest?.briefing || null, answers = briefing?.answers || {};
  const revenue = rdqRevenue(offering);
  const name = [offering?.name, offering?.definition?.geography].filter(Boolean).join(', ');
  const sized = mktSized(offering) || mktSized(buyer), sizing = !sized && !!offering && mktOwner();
  const players = rdqPlayersHtml(offering, companies, briefing, revenue);
  const reach = mktChainHtml(chain, rdqChainMoney(offering, buyer, revenue));
  const heard = !!stats && ((stats.recent || []).length > 0 || (stats.daily || []).some(d => Object.values(d.sources || {}).some(Boolean)));
  const saying = heard || (!!stats && settingsIsAdmin());
  const tracked = companies?.total ?? (companies?.entities || []).length;
  shell.innerHTML = rdqPageHtml({
    top: `<header class="mkt-top"><h2>Market</h2>${name ? `<span class="mkt-name">${esc(name)}</span>` : ''}
      <span class="spacer"></span><a class="ghost" href="${MARKET_ROUTE}">All segments</a><a class="ghost" href="${MARKET_MAP}">Open map</a></header>`,
    tiles: rdqTilesHtml(offering, revenue),
    questions: rdqQuestionsHtml({
      answers,
      changed: briefing ? rdqChangedHtml(briefing) : '',
      changedMore: briefing ? `${esc(rdbDate(briefing))} · <button class="linkish" type="button" data-rdb-read>Read</button>` : '',
      money: sized || sizing ? '<div id="mkt-money"></div>' : '',
      players, playersMore: tracked ? `${tracked} tracked` : '',
      reach,
      saying: saying ? rdqSayingHtml(stats, heard) : '',
      sayingMore: settingsIsAdmin() ? '<button class="linkish" type="button" data-rdm-settings aria-expanded="false" aria-controls="rdm-set">Settings</button>' : '',
    }),
  });
  const money = sized ? mktSpend(shell.querySelector('#mkt-money'), {offering, buyer}) : null;
  if (sizing) mktSizeForm(/** @type {HTMLElement} */ (shell.querySelector('#mkt-money')), offering.definition?.description);
  const chainEl = shell.querySelector('.mkt-chain');
  if (chainEl) mktChainBind(chainEl, chain.edges);
  rdqPageBind(shell, briefing);
  return {
    shell,
    // An old view's address lands on its question (and Buyers on the buyer side of the money).
    async open(at) {
      if (at.dimension) money?.show(at.dimension);
      const target = at.section && shell.querySelector('#mkt-q-' + at.section);
      if (target) target.scrollIntoView({block: 'start'});
      else shell.scrollTop = 0;
    },
    stop() { money?.stop(); },
  };
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
document.addEventListener('click', ev => {
  const target = /** @type {HTMLElement} */ (ev.target);
  const set = target.closest?.('[data-rdm-settings]');
  if (set) {
    const box = $('#rdm-set'), open = box.hidden;
    box.hidden = !open; set.setAttribute('aria-expanded', String(open));
    if (open) void renderSettingsListening();
    return;
  }
  // A spike's marker lights the posts sent that day.
  const spike = /** @type {HTMLElement | null} */ (target.closest?.('.rdm-spk'));
  if (spike) {
    const rows = [...document.querySelectorAll('.rdm-row')];
    rows.forEach(r => r.classList.toggle('hl', r.dataset.day === spike.dataset.rdmDay));
    rows.find(r => r.classList.contains('hl'))?.scrollIntoView({block: 'nearest'});
  }
});
