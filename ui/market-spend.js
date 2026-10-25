/* The Market tab's other views (ui/market-page.js): Spend and Buyers, one treemap of what the market spends (by offering,
   by buyer), with how sure the team is of each box and a panel with the arithmetic behind it; Size vs growth, one dot per
   segment; and Value chain, who supplies, sells and buys around the team. Drawn from GET /v2/market/spend and
   /v2/market/value-chain. The layout and the charts are hand-written, with no library. */
'use strict';
const MKT_METHODS = {top_down: 'Top down', bottom_up: 'Bottom up', player_sum: 'Player sum', owner: 'Owner'};
const MKT_KINDS = {reported: 'Reported', estimated: 'Estimated', claimed: 'Claimed'};
const MKT_METRICS = {revenue: 'Revenue', arr: 'ARR', customers: 'Customers', seats: 'Seats'};
const MKT_BASES = {product: 'Product', platform: 'Platform'};
const MKT_COLOURS = 8;
let MKT = null;          // the open view: {stop}
let MKT_PEEK = null;     // the default view's first read, kept for the Spend view that follows it

window.marketViewsStop = function marketViewsStop() {
  if (MKT) MKT.stop();
  MKT = null;
};

const mktSized = data => (data?.segments || []).some(s => s.size);
const mktOwner = () => S.me?.role === 'owner';
const mktTrim = (x, d) => String(Number(x.toFixed(d)));
function mktMoney(n) {
  n = Number(n) || 0;
  const a = Math.abs(n);
  if (a >= 1e12) return '$' + mktTrim(n / 1e12, 2) + 'T';
  if (a >= 1e9) return '$' + mktTrim(n / 1e9, a >= 1e10 ? 1 : 2) + 'B';
  if (a >= 1e6) return '$' + mktTrim(n / 1e6, a >= 1e8 ? 0 : 1) + 'M';
  if (a >= 1e3) return '$' + mktTrim(n / 1e3, a >= 1e5 ? 0 : 1) + 'K';
  return '$' + mktTrim(n, 0);
}
const mktRange = size => `${mktMoney(size.low_usd)}–${mktMoney(size.high_usd)}`;
const mktLink = u => /^https?:\/\//i.test(u || '')
  ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(String(u).replace(/^https?:\/\/(www\.)?/i, '').slice(0, 60))}</a>` : esc(u || '');
const mktObject = v => {
  if (typeof v === 'string') { try { v = JSON.parse(v); } catch { return {}; } }
  return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
};
const mktNice = raw => {            // a round step near raw: 1, 2 or 5 times a power of ten
  const p = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
};

// The default for a bare Market tab: Spend once anything is sized (ui/market-page.js asks).
window.marketSpendSized = async function marketSpendSized() {
  try {
    const data = await get('/v2/market/spend');
    MKT_PEEK = {at: Date.now(), data};
    return mktSized(data);
  } catch { return false; }
};

window.marketViewsMount = function marketViewsMount(host, view) {
  window.marketViewsStop();
  MKT = view === 'value-chain' ? mktChain(host) : mktSpend(host, view);
};

// Squarified treemap (Bruls, Huizing, van Wijk): items {value}, laid into the rectangle in rows that stay near square.
function mktSquarify(items, x, y, w, h) {
  const out = [], total = items.reduce((a, b) => a + b.value, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map(i => ({...i, area: i.value * scale})).sort((a, b) => b.area - a.area);
  const worst = (row, short) => {
    const s = row.reduce((a, b) => a + b.area, 0), mx = row[0].area, mn = row[row.length - 1].area;
    return Math.max(short * short * mx / (s * s), (s * s) / (short * short * mn));
  };
  while (rest.length) {
    const short = Math.min(w, h), row = [rest[0]];
    let i = 1;
    while (i < rest.length && worst([...row, rest[i]], short) <= worst(row, short)) row.push(rest[i++]);
    const s = row.reduce((a, b) => a + b.area, 0);
    if (w >= h) {
      const cw = s / h; let cy = y;
      row.forEach(r => { const ch = r.area / cw; out.push({...r, x, y: cy, w: cw, h: ch}); cy += ch; });
      x += cw; w -= cw;
    } else {
      const ch = s / w; let cx = x;
      row.forEach(r => { const rw = r.area / ch; out.push({...r, x: cx, y, w: rw, h: ch}); cx += rw; });
      y += ch; h -= ch;
    }
    rest = rest.slice(i);
  }
  return out;
}

// The segments as the map draws them: each top segment with its sized sub-segments. A segment with no size of its own
// takes the API's rolled-up spend (its parts, at any depth), so the map and the total agree; a segment with nothing
// sized is not drawn, and is listed instead.
function mktTree(data) {
  const segs = data.segments || [], ids = new Set(segs.map(s => s.id));
  const size = s => Number(s.rolled_usd?.spend_usd) || 0;
  const tops = segs.filter(s => !s.parent || !ids.has(s.parent));
  const tree = tops.map((seg, i) => {
    const kids = segs.filter(c => c.parent === seg.id).map(c => ({seg: c, value: size(c)}));
    const sized = kids.filter(k => k.value > 0);
    return {seg, colour: i % MKT_COLOURS, value: size(seg), kids: sized,
      unsized: kids.filter(k => !k.value).map(k => k.seg)};
  });
  return {drawn: tree.filter(g => g.value > 0), unsized: [...tree.filter(g => !(g.value > 0)).map(g => g.seg), ...tree.flatMap(g => g.unsized)]};
}

const mktChips = (seg, room) => room ? [
  seg.disagree ? '<span class="pill waiting">Disagree</span>' : '',
  seg.self ? '<span class="pill us">Us</span>' : '',
  seg.size && !seg.size.accepted ? '<span class="pill">Proposed</span>' : '',
].join('') : '';

function mktTileHtml(seg, value, r, colour, nested) {
  const mid = r.w > 58 && r.h > 34, big = r.w > 96 && r.h > 70;
  const shown = Number(seg.size?.spend_usd) || value;
  const label = `${seg.name}, ${mktMoney(shown)}${seg.size?.confidence === 'low' ? ', low confidence' : ''}${seg.disagree ? ', estimates disagree' : ''}`;
  const cls = ['mkt-box', 'mkt-tile', seg.size?.confidence === 'low' ? 'low' : '', seg.self ? 'self' : '', nested ? 'nested' : ''].filter(Boolean).join(' ');
  return `<div class="${cls}" role="button" tabindex="0" data-seg="${esc(seg.id)}" data-value="${value}" aria-label="${esc(label)}" title="${esc(label)}"
    style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;--c:var(--mkt-c${colour + 1})">${mid
      ? `<span class="mkt-n">${esc(seg.name)}</span><span class="mkt-v">${mktMoney(shown)}</span>${big && seg.size?.spend_usd != null ? `<span class="mkt-r">${mktRange(seg.size)}</span>` : ''}${mktChips(seg, big)}` : ''}</div>`;
}

function mktBoxHtml(r) {
  const g = r.g, seg = g.seg, head = 26;
  if (!(g.kids.length && r.w > 120 && r.h > 80)) return mktTileHtml(seg, g.value, r, g.colour, false);
  const sum = g.kids.reduce((a, k) => a + k.value, 0), rest = g.value - sum;
  // Parts that add up to less than the box keep their own dollars and a plain tile takes the rest, so each area matches
  // its label; parts that overlap (more than the box) are scaled down to fit it.
  const parts = g.kids.map(k => ({seg: k.seg, value: rest < 0 ? k.value * g.value / sum : k.value}));
  if (rest > g.value * 0.01) parts.push({rest: true, value: rest});
  const inner = mktSquarify(parts, 0, head, r.w, r.h - head);
  const shown = Number(seg.size?.spend_usd) || g.value;
  return `<div class="mkt-box mkt-group${seg.self ? ' self' : ''}" data-value="${g.value}" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;--c:var(--mkt-c${g.colour + 1})">
    <button class="mkt-gh" type="button" data-seg="${esc(seg.id)}" aria-label="${esc(seg.name + ', ' + mktMoney(shown))}"><span class="mkt-n">${esc(seg.name)}</span><b>${mktMoney(shown)}</b>${mktChips(seg, r.w > 240)}</button>
    ${inner.map(k => k.rest ? mktRestHtml(seg, k) : mktTileHtml(k.seg, k.value, k, g.colour, true)).join('')}</div>`;
}

function mktRestHtml(seg, r) {
  const label = `Rest of ${seg.name}`;
  return `<div class="mkt-box mkt-rest" data-value="${r.value}" aria-label="${esc(label + ', ' + mktMoney(r.value))}" title="${esc(label)}"
    style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px">${r.w > 58 && r.h > 34
      ? `<span class="mkt-n">${esc(label)}</span><span class="mkt-v">${mktMoney(r.value)}</span>` : ''}</div>`;
}

// ------------------------------------------------------------------ Spend, Buyers and Size vs growth

function mktSpend(host, view) {
  const dimension = view === 'buyers' ? 'buyer' : 'offering';
  const ctx = {dead: false, data: null, basis: '', sel: '', cite: null, load: 0, observer: null, dialogs: {}};

  const seg = id => (ctx.data?.segments || []).find(s => s.id === id);
  const fail = error => { if (!ctx.dead) host.innerHTML = `<p class="market-none">${esc(error.message || 'The market did not load.')}</p>`; };

  async function load() {
    const mine = ++ctx.load;
    const peek = MKT_PEEK; MKT_PEEK = null;
    let data;
    try {
      data = peek && dimension === 'offering' && !ctx.basis && Date.now() - peek.at < 5000 ? peek.data
        : await get('/v2/market/spend?' + new URLSearchParams({dimension, ...(ctx.basis ? {basis: ctx.basis} : {})}));
    } catch (error) { return fail(error); }
    if (ctx.dead || mine !== ctx.load) return;
    ctx.data = data;
    ctx.basis = data.basis || ctx.basis;
    render();
    if (ctx.dialogs.seg?.open) fillPanel();
  }

  // ---- the page
  function render() {
    const top = host.scrollTop, data = ctx.data;
    ctx.observer?.disconnect();
    if (!mktSized(data)) { host.className = 'mkt-host blank'; host.innerHTML = emptyHtml(); bindEmpty(); return; }
    host.className = 'mkt-host';
    const tree = ctx.tree = mktTree(data);
    host.innerHTML = headHtml() + (view === 'growth' ? '<div id="mkt-growth" class="mkt-growth"></div>' : '<div id="mkt-map" class="mkt-map"></div>'
      + '<p class="mkt-legend"><span class="mkt-key"><i class="mkt-sw low"></i>Low confidence</span><span class="mkt-key"><i class="mkt-sw us"></i>Us</span></p>')
      + (tree.unsized.length && view !== 'growth' ? unsizedHtml(tree.unsized, 'Not sized yet') : '')
      + (view === 'growth' ? '' : sureHtml(tree.drawn));
    const area = $('#mkt-map') || $('#mkt-growth');
    let frame = 0, width = 0;
    const draw = () => { frame = 0; width = area.clientWidth; if (width) (view === 'growth' ? drawGrowth : drawMap)(area, width); };
    ctx.observer = new ResizeObserver(() => { if (area.clientWidth !== width && !frame) frame = requestAnimationFrame(draw); });
    ctx.observer.observe(area);
    draw();
    host.scrollTop = top;
  }

  function headHtml() {
    const d = ctx.data, def = d.definition || {}, total = d.total || {};
    const bases = [...new Set((d.segments || []).flatMap(s => s.bases || []))];
    const facts = [d.year, def.geography].filter(Boolean).join(' · ');
    return `<header class="mkt-head"><div class="mkt-total"><strong>${mktMoney(total.spend_usd)}</strong>
        <span>${mktRange(total)}${facts ? ' · ' + esc(facts) : ''}</span></div>
      <div class="mkt-ctl">${bases.length > 1 ? `<div class="mkt-seg" role="group" aria-label="Basis">${bases.map(b =>
        `<button type="button" data-basis="${esc(b)}" aria-pressed="${b === ctx.basis}">${esc(MKT_BASES[b] || b)}</button>`).join('')}</div>` : ''}
        ${d.definition ? '<button class="ghost" type="button" data-mkt-def>Definition</button>' : ''}</div>
      ${def.description ? `<p class="mkt-desc">${esc(def.description)}</p>` : ''}</header>`;
  }

  const unsizedHtml = (list, title) => `<section class="mkt-sec"><h2 class="strat-k">${title}</h2><div class="mkt-chips">${list.map(s =>
    `<button class="mkt-pick${s.self ? ' self' : ''}" type="button" data-seg="${esc(s.id)}">${esc(s.name)}</button>`).join('')}</div></section>`;

  // How sure are we: one bar per top segment from low to high, the point estimate ticked.
  function sureHtml(drawn) {
    const rows = drawn.filter(g => g.seg.size?.spend_usd != null).sort((a, b) => b.value - a.value);
    if (!rows.length) return '';
    const max = Math.max(...rows.map(g => Math.max(g.seg.size.high_usd, g.seg.size.spend_usd)));
    const step = mktNice(max / 4), top = Math.ceil(max / step) * step, ticks = [];
    for (let t = 0; t <= top + step / 2; t += step) ticks.push(t);
    const pct = v => (100 * Math.min(v, top) / top).toFixed(2) + '%';
    const grid = ticks.map(t => `<i style="left:${pct(t)}"></i>`).join('');
    return `<section class="mkt-sec mkt-sure"><h2 class="strat-k">How sure are we</h2>
      <div class="mkt-axis" aria-hidden="true"><span></span><div>${ticks.map(t => `<em style="left:${pct(t)}">${t ? mktMoney(t) : '0'}</em>`).join('')}</div><span></span></div>
      ${rows.map(g => { const z = g.seg.size; return `<div class="mkt-row" role="button" tabindex="0" data-seg="${esc(g.seg.id)}" style="--c:var(--mkt-c${g.colour + 1})">
        <span class="mkt-lab">${esc(g.seg.name)}</span>
        <div class="mkt-bar">${grid}<div class="span${z.confidence === 'low' ? ' low' : ''}" style="left:${pct(z.low_usd)};width:calc(${pct(z.high_usd)} - ${pct(z.low_usd)})"></div><div class="mid" style="left:${pct(z.spend_usd)}"></div></div>
        <span class="mkt-num">${mktRange(z)}</span></div>`; }).join('')}</section>`;
  }

  function drawMap(map, W) {
    const H = Math.round(W < 560 ? W * 1.2 : Math.min(600, W * 0.58));
    map.style.height = H + 'px';
    const laid = mktSquarify(ctx.tree.drawn.map(g => ({g, value: g.value})), 0, 0, W, H);
    map.innerHTML = laid.map(mktBoxHtml).join('');
  }

  // One dot per top segment: spend across (log when the range is wide), growth up, ours filled.
  function drawGrowth(area, W) {
    const all = ctx.tree.drawn.filter(g => g.seg.size?.spend_usd != null);
    const pts = all.filter(g => Number.isFinite(g.seg.size.growth_pct));
    const none = all.filter(g => !pts.includes(g)).map(g => g.seg);
    if (!pts.length) { area.innerHTML = '<p class="market-none">No growth figures yet.</p>' + (none.length ? unsizedHtml(none, 'No growth figure') : ''); return; }
    const H = W < 560 ? 340 : 420, m = {l: 46, r: 14, t: 14, b: 34};
    const xs = pts.map(g => g.seg.size.spend_usd), lo = Math.min(...xs), hi = Math.max(...xs);
    const log = lo > 0 && hi / lo > 20;
    const x0 = log ? lo / 1.7 : 0, x1 = log ? hi * 1.7 : hi * 1.1;
    const fx = v => m.l + (W - m.l - m.r) * (log ? Math.log(v / x0) / Math.log(x1 / x0) : (v - x0) / (x1 - x0));
    const gs = pts.map(g => g.seg.size.growth_pct);
    const y0 = Math.min(0, Math.min(...gs)), y1 = Math.max(10, Math.max(...gs) * 1.12);
    const ystep = mktNice((y1 - y0) / 5), fy = v => H - m.b - (H - m.t - m.b) * (v - y0) / (y1 - y0);
    const yt = []; for (let t = Math.ceil(y0 / ystep) * ystep; t <= y1; t += ystep) yt.push(t);
    let xt = [];
    if (log) { for (let p = 10 ** Math.floor(Math.log10(x0)); p <= x1; p *= 10) for (const f of [1, 2, 5]) if (p * f >= x0 && p * f <= x1) xt.push(p * f); }
    else { const s = mktNice(x1 / 5); for (let t = 0; t <= x1; t += s) xt.push(t); }
    if (xt.length > 6) xt = xt.filter((_, i) => i % Math.ceil(xt.length / 6) === 0);
    const dots = pts.map(g => ({g, x: fx(g.seg.size.spend_usd), y: fy(g.seg.size.growth_pct), r: 7,
      text: g.seg.name.length > 24 ? g.seg.name.slice(0, 23) + '…' : g.seg.name}));
    const spots = [];
    const free = (box, mine) => box.x0 >= 2 && box.x1 <= W - 2 && box.y0 >= 2 && box.y1 <= H - m.b + 6
      && ![...spots, ...dots.filter(d => d !== mine).map(d => ({x0: d.x - d.r - 1, x1: d.x + d.r + 1, y0: d.y - d.r - 1, y1: d.y + d.r + 1}))]
        .some(o => box.x0 < o.x1 && box.x1 > o.x0 && box.y0 < o.y1 && box.y1 > o.y0);
    [...dots].sort((a, b) => a.y - b.y).forEach(d => {
      const w = d.text.length * 6.3 + 2, spot = [
        [d.x + d.r + 5, d.y + 4, 'start', d.x + d.r + 5, d.x + d.r + 5 + w, d.y - 8, d.y + 5],
        [d.x - d.r - 5, d.y + 4, 'end', d.x - d.r - 5 - w, d.x - d.r - 5, d.y - 8, d.y + 5],
        [d.x, d.y - d.r - 6, 'middle', d.x - w / 2, d.x + w / 2, d.y - d.r - 18, d.y - d.r - 4],
        [d.x, d.y + d.r + 14, 'middle', d.x - w / 2, d.x + w / 2, d.y + d.r + 3, d.y + d.r + 16]]
        .map(([tx, ty, anchor, x0, x1, y0, y1]) => ({tx, ty, anchor, x0, x1, y0, y1}));
      d.at = spot.find(s => free(s, d)) || spot[0];
      spots.push(d.at);
    });
    area.innerHTML = `<svg class="mkt-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Segment size against growth">
      ${yt.map(t => `<line class="g" x1="${m.l}" x2="${W - m.r}" y1="${fy(t)}" y2="${fy(t)}"${t === 0 ? ' data-zero' : ''}/><text class="t" x="${m.l - 6}" y="${fy(t) + 4}" text-anchor="end">${mktTrim(t, 1)}%</text>`).join('')}
      ${xt.map(t => `<line class="g v" x1="${fx(t)}" x2="${fx(t)}" y1="${m.t}" y2="${H - m.b}"/><text class="t" x="${fx(t)}" y="${H - m.b + 16}" text-anchor="middle">${t ? mktMoney(t) : '0'}</text>`).join('')}
      <text class="t cap" x="${W - m.r}" y="${H - 2}" text-anchor="end">Spend${log ? ' (log scale)' : ''}</text>
      <text class="t cap" x="${m.l}" y="10">Growth</text>
      ${dots.map(d => `<g class="mkt-dot${d.g.seg.self ? ' self' : ''}" role="button" tabindex="0" data-seg="${esc(d.g.seg.id)}" aria-label="${esc(`${d.g.seg.name}, ${mktMoney(d.g.seg.size.spend_usd)}, growth ${mktTrim(d.g.seg.size.growth_pct, 1)}%`)}">
        <circle cx="${d.x}" cy="${d.y}" r="${d.r}"/><text x="${d.at.tx}" y="${d.at.ty}" text-anchor="${d.at.anchor}">${esc(d.text)}</text></g>`).join('')}</svg>
      <p class="mkt-legend"><span class="mkt-key"><i class="mkt-sw us"></i>Us</span></p>${none.length ? unsizedHtml(none, 'No growth figure') : ''}`;
  }

  // ---- empty: nothing sized yet
  function emptyHtml() {
    const description = ctx.data?.definition?.description || '';
    if (!mktOwner()) return '<p class="market-none">Not sized yet.</p>';
    return `<form class="market-start mkt-start" data-mkt-size novalidate>
      <h1><label for="mkt-describe">Describe the market in a sentence</label></h1>
      <input id="mkt-describe" name="description" type="text" maxlength="400" autocomplete="off" value="${esc(description)}" placeholder="What small offices spend on cleaning services">
      <p class="err" data-mkt-error role="alert" hidden></p>
      <div class="market-start-actions"><button class="primary" type="submit">Size the market</button></div></form>`;
  }
  function bindEmpty() {
    const form = host.querySelector('[data-mkt-size]');
    if (!form) return;
    form.onsubmit = async ev => {
      ev.preventDefault();
      const line = form.querySelector('[data-mkt-error]'), description = form.elements.description.value.trim();
      if (!description) { line.textContent = 'Say what the market is.'; line.hidden = false; return; }
      line.hidden = true;
      form.querySelector('[type=submit]').disabled = true;
      try {
        const sent = await post('/v2/market/spend/research', {description});
        if (ctx.dead) return;
        host.innerHTML = `<div class="market-researching" data-mkt-researching role="status"><span class="market-spin" aria-hidden="true"></span>
          <div><strong>The Librarian is on it.</strong>${sent.task_id ? `<p><a href="#/task/${esc(sent.task_id)}">View task</a></p>` : ''}</div></div>`;
      } catch (error) {
        form.querySelector('[type=submit]').disabled = false;
        line.textContent = error.message || 'That did not go through.'; line.hidden = false;
      }
    };
  }

  // ---- panels
  function dialog(name, label) {
    let dlg = ctx.dialogs[name];
    if (dlg) return dlg;
    dlg = ctx.dialogs[name] = document.createElement('dialog');
    dlg.className = 'tmodal mkt-panel'; dlg.setAttribute('aria-label', label);
    dlg.addEventListener('click', ev => {
      if (ev.target === dlg || ev.target.closest('[data-mkt-close]')) dlg.close();
    });
    bindPanel(dlg);
    document.body.appendChild(dlg);
    return dlg;
  }

  const head = (title, crumb) => `<div class="tmodal-head"><div class="mkt-ph">${crumb ? `<small>${esc(crumb)}</small>` : ''}<h2>${esc(title)}</h2></div><span class="spacer"></span>
    <button class="ghost tmodal-x" type="button" data-mkt-close aria-label="Close">✕</button></div>`;
  const kv = rows => `<dl class="mkt-kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
  const block = (title, body) => body ? `<section class="mkt-blk"><h3 class="strat-k">${title}</h3>${body}</section>` : '';
  const list = items => items?.length ? `<ul class="mkt-list">${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` : '';

  function openDefinition() {
    const def = ctx.data?.definition;
    if (!def) return;
    const dlg = dialog('def', 'Definition');
    const params = Object.entries(mktObject(def.params));
    dlg.innerHTML = head('Definition', '') + `<div class="tmodal-body mkt-body">
      ${def.description ? `<p>${esc(def.description)}</p>` : ''}
      ${kv([def.year && ['Year', esc(def.year)], def.geography && ['Where', esc(def.geography)], def.currency && ['Currency', esc(def.currency)]])}
      ${block('Includes', list(def.includes))}${block('Excludes', list(def.excludes))}${block('Counting rules', list(def.counting_rules))}
      ${block('Shared params', params.length ? `<ul class="mkt-list">${params.map(([name, p]) => { p = typeof p === 'object' && p ? p : {value: p};
        return `<li><code>${esc(name)}</code> ${esc(p.value)}${p.judgement ? ' <span class="pill">Judgement</span>' : ''}${p.source ? '<br>' + mktLink(p.source) : ''}</li>`; }).join('')}</ul>` : '')}</div>`;
    if (!dlg.open) dlg.showModal();
  }

  function openPanel(id) {
    if (!seg(id)) return;
    ctx.sel = id; ctx.cite = null;
    const dlg = dialog('seg', 'Segment');
    fillPanel();
    if (!dlg.open) dlg.showModal();
  }

  function estimateHtml(e, s) {
    const params = Object.entries(mktObject(e.params)), owner = mktOwner();
    const accepted = e.status === 'accepted' || (s.size?.id === e.id && s.size.accepted);
    const evidence = (Array.isArray(e.evidence) ? e.evidence : []).filter(x => x && (x.source_url || x.quote));
    return `<li class="mkt-est" data-size="${esc(e.id)}"><div class="mkt-est-h"><b>${esc(MKT_METHODS[e.method] || e.method)}</b>
        ${e.spend_usd == null ? `<span>${esc(e.error || '')}</span>` : `<span>${mktMoney(e.spend_usd)} <small>${mktRange(e)}</small></span>`}
        ${accepted ? '<span class="pill active">Accepted</span>' : '<span class="pill">Proposed</span>'}
        ${owner && !accepted ? `<button class="ghost mkt-mini" type="button" data-mkt-accept="${esc(e.id)}">Accept</button>` : ''}</div>
      ${e.arithmetic ? `<p class="mkt-arith">${esc(e.arithmetic)}</p>` : ''}${e.formula ? `<p class="mkt-arith"><code>${esc(e.formula)}</code></p>` : ''}
      ${params.length ? `<ul class="mkt-params">${params.map(([name, p]) => { p = typeof p === 'object' && p ? p : {value: p};
        return `<li>${owner ? `<form data-mkt-param data-size="${esc(e.id)}" data-name="${esc(name)}"><label><code>${esc(name)}</code>
            <input name="value" type="number" step="any" value="${esc(p.value)}" aria-label="${esc(name)}"></label><button class="ghost mkt-mini" type="submit">Save</button></form>`
          : `<code>${esc(name)}</code> ${esc(p.value)}`}${p.judgement ? ' <span class="pill">Judgement</span>' : ''}${p.source ? ` <span class="mkt-src">${mktLink(p.source)}</span>` : ''}</li>`; }).join('')}</ul>` : ''}
      ${evidence.length ? `<ul class="mkt-list mkt-ev">${evidence.map(x => `<li>${x.source_url ? mktLink(x.source_url) : ''}${x.source_kind ? ` <span class="pill">${esc(x.source_kind)}</span>` : ''}${x.quote ? `<br><q>${esc(x.quote)}</q>` : ''}</li>`).join('')}</ul>` : ''}</li>`;
  }

  function figureHtml(f) {
    const n = f.metric === 'revenue' || f.metric === 'arr' ? mktMoney(f.value) : Number(f.value).toLocaleString('en-US');
    const what = f.value === null || f.value === undefined ? esc(f.text || '') : `${esc(MKT_METRICS[f.metric] || f.metric)} ${n}`;
    return `<span class="mkt-fig">${what}${f.kind ? ` · ${esc(MKT_KINDS[f.kind] || f.kind)}` : ''}${f.as_of ? ` · ${esc(f.as_of)}` : ''}${f.source_url ? ' · ' + mktLink(f.source_url) : ''}</span>`;
  }

  function fillPanel() {
    const dlg = ctx.dialogs.seg, s = seg(ctx.sel);
    if (!dlg) return;
    if (!s) { dlg.close(); return; }
    const z = s.size, parent = s.parent ? seg(s.parent) : null, owner = mktOwner();
    const estimates = s.estimates || [], over = estimates.map(e => e.overlaps).find(Boolean) || z?.overlaps;
    const spread = estimates.map(e => e.spread).find(Boolean) || z?.spread;
    const span = z ? Math.max(z.high_usd, z.spend_usd) * 1.08 || 1 : 1, pc = v => (100 * v / span).toFixed(2) + '%';
    dlg.innerHTML = head(s.name, parent?.name) + `<div class="tmodal-body mkt-body">
      <div class="mkt-chips">${z ? `<span class="pill${z.confidence === 'low' ? '' : ' active'}">${esc(z.confidence)} confidence</span>` : '<span class="pill">Not sized yet</span>'}
        ${s.disagree ? '<span class="pill waiting">Disagree</span>' : ''}${s.self ? '<span class="pill us">Us</span>' : ''}${z && !z.accepted ? '<span class="pill">Proposed</span>' : ''}</div>
      ${s.summary ? `<p>${esc(s.summary)}</p>` : ''}
      ${z?.error ? `<p>${esc(z.error)}</p>` : ''}
      ${z && z.spend_usd != null ? kv([['Spend', mktMoney(z.spend_usd)], ['Range', mktRange(z)], Number.isFinite(z.growth_pct) ? ['Growth', mktTrim(z.growth_pct, 1) + '%'] : null, ctx.data.year && ['Year', esc(ctx.data.year)]])
        + `<div class="mkt-bar solo" aria-hidden="true"><div class="span" style="left:${pc(z.low_usd)};width:calc(${pc(z.high_usd)} - ${pc(z.low_usd)})"></div><div class="mid" style="left:${pc(z.spend_usd)}"></div></div>` : ''}
      ${block('Estimates', estimates.length ? `<ul class="mkt-ests">${estimates.map(e => estimateHtml(e, s)).join('')}</ul>` : '')}
      ${block('Overlaps', over ? `<p>${esc(over)}</p>` : '')}${block('Spread', spread ? `<p>${esc(spread)}</p>` : '')}
      ${block('Players', (s.players || []).length ? `<ul class="mkt-list">${s.players.map(p => `<li><b>${esc(p.name)}</b>${(p.figures || []).length ? '<br>' + p.figures.map(figureHtml).join('<br>') : ''}</li>`).join('')}</ul>` : '')}
      ${owner ? `<div class="mkt-acts"><button class="ghost" type="button" data-mkt-research>Research this segment</button>
        <button class="ghost" type="button" data-mkt-cite-open>Cite in a bet</button></div>${citeHtml()}` : ''}</div>`;
  }

  function citeHtml() {
    const c = ctx.cite;
    if (!c) return '';
    if (c.loading) return '<p class="muted mkt-cite">Loading…</p>';
    if (!c.bets.length) return '<p class="muted mkt-cite">No active bets.</p>';
    return `<form class="mkt-cite" data-mkt-cite><select name="bet" aria-label="Bet">${c.bets.map(b =>
      `<option value="${esc(b.id)}">${esc(b.statement)}</option>`).join('')}</select><button class="primary" type="submit">Cite</button></form>`;
  }

  async function write(job, ok) {
    try { await job(); if (ok) toast(ok); await load(); } catch (error) { toast(error.message || 'That did not save', true); }
  }

  // ---- events
  host.addEventListener('click', ev => {
    const basis = ev.target.closest('[data-basis]');
    if (basis) { if (basis.dataset.basis !== ctx.basis) { ctx.basis = basis.dataset.basis; void load(); } return; }
    if (ev.target.closest('[data-mkt-def]')) { openDefinition(); return; }
    const pick = ev.target.closest('[data-seg]');
    if (pick) openPanel(pick.dataset.seg);
  });
  host.addEventListener('keydown', ev => {
    const pick = ev.key === 'Enter' || ev.key === ' ' ? ev.target.closest('[role=button][data-seg]') : null;
    if (pick) { ev.preventDefault(); openPanel(pick.dataset.seg); }
  });
  // The panel is a dialog outside the page, so it listens for itself.
  function bindPanel(dlg) {
    dlg.addEventListener('click', ev => {
      const accept = ev.target.closest('[data-mkt-accept]');
      if (accept) { void write(() => post(`/v2/market/sizes/${encodeURIComponent(accept.dataset.mktAccept)}/accept`, {}), 'Accepted'); return; }
      if (ev.target.closest('[data-mkt-research]')) { window.DocsEditor?.researchDialog({target: ctx.sel}); return; }
      if (ev.target.closest('[data-mkt-cite-open]')) { void openCite(); }
    });
    dlg.addEventListener('submit', ev => {
      ev.preventDefault();
      const param = ev.target.closest('[data-mkt-param]'), cite = ev.target.closest('[data-mkt-cite]');
      if (param) {
        const value = Number(param.elements.value.value);
        if (param.elements.value.value.trim() === '' || !Number.isFinite(value)) { toast('Enter a number', true); return; }
        void write(() => post(`/v2/market/sizes/${encodeURIComponent(param.dataset.size)}/params`, {params: {[param.dataset.name]: value}}));
      } else if (cite) void citeBet(cite.elements.bet.value);
    });
  }

  async function openCite() {
    ctx.cite = {loading: true}; fillPanel();
    try {
      const strategy = await get('/v2/strategy', {fresh: true});
      if (ctx.dead) return;
      ctx.cite = {bets: (strategy.bets || []).filter(b => b.status === 'active' && !(b.cites || []).includes(ctx.sel)), all: strategy.bets || [], revision: strategy.draft?.revision ?? null};
    } catch (error) { ctx.cite = null; toast(error.message || 'Could not load the bets', true); }
    fillPanel();
  }
  async function citeBet(id) {
    const bet = (ctx.cite?.all || []).find(b => b.id === id);
    if (!bet) return;
    try {
      await post(`/v2/strategy/bets/${encodeURIComponent(id)}`, {cites: [...(bet.cites || []), ctx.sel], revision: ctx.cite.revision});
      toast('Cited');
      ctx.cite = null; fillPanel();
    } catch (error) { toast(error.message || 'Could not cite it', true); }
  }

  host.className = 'mkt-host';
  host.innerHTML = '<p class="market-loading">Opening the market…</p>';
  void load();
  return {stop() {
    ctx.dead = true; ctx.observer?.disconnect();
    Object.values(ctx.dialogs).forEach(d => d.remove());
  }};
}

// ------------------------------------------------------------------ Value chain

function mktChain(host) {
  let dead = false;
  host.className = 'mkt-host';
  host.innerHTML = '<p class="market-loading">Opening the market…</p>';
  get('/v2/market/value-chain').then(data => {
    if (dead) return;
    const layers = data.layers || [], edges = data.edges || [];
    if (!layers.some(l => (l.nodes || []).length)) { host.className = 'mkt-host blank'; host.innerHTML = '<p class="market-none">Nothing here yet.</p>'; return; }
    host.innerHTML = `<div class="mkt-chain">${layers.map(l => `<section class="mkt-layer" data-layer="${esc(l.key)}"><h2 class="strat-k">${esc(l.title)}</h2>
        <div class="mkt-nodes">${(l.nodes || []).map(n => `<a class="mkt-node${n.self ? ' self' : ''}" href="${marketHref(n.id)}" data-node="${esc(n.id)}"
          title="${esc(n.name)}">${esc(n.name)}</a>`).join('') || '<span class="muted">None</span>'}</div></section>`).join('')}</div>`;
    // A node lit shows what it connects to; the rest step back.
    const near = new Map();
    edges.forEach(e => { (near.get(e.src) || near.set(e.src, new Set()).get(e.src)).add(e.dst); (near.get(e.dst) || near.set(e.dst, new Set()).get(e.dst)).add(e.src); });
    const chain = host.querySelector('.mkt-chain');
    const light = id => {
      chain.classList.toggle('lit', !!id);
      chain.querySelectorAll('.mkt-node').forEach(a => a.classList.toggle('near', !!id && (a.dataset.node === id || !!near.get(id)?.has(a.dataset.node))));
    };
    chain.addEventListener('pointerover', ev => { const a = ev.target.closest('.mkt-node'); if (a) light(a.dataset.node); });
    chain.addEventListener('pointerout', () => light(''));
    chain.addEventListener('focusin', ev => { const a = ev.target.closest('.mkt-node'); if (a) light(a.dataset.node); });
    chain.addEventListener('focusout', () => light(''));
  }).catch(error => { if (!dead) host.innerHTML = `<p class="market-none">${esc(error.message || 'The market did not load.')}</p>`; });
  return {stop() { dead = true; }};
}
